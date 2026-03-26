"""
Orphaned Cashout Cleanup Service
Automatically detects and cancels cashouts stuck in pending status due to session failures
Returns locked funds to users when cashouts are abandoned
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List
from sqlalchemy import and_
from models import Cashout, CashoutStatus
from database import SessionLocal
from utils.constants import ORPHANABLE_CASHOUT_STATUSES, CANCELLABLE_CASHOUT_STATUSES
# Removed import of non-existent AtomicCashoutService - implement cancellation directly

logger = logging.getLogger(__name__)

class OrphanedCashoutCleanupService:
    """Service to clean up orphaned cashouts and return locked funds"""
    
    # Cashouts stuck in pending for more than 10 minutes are considered orphaned
    ORPHAN_TIMEOUT_MINUTES = 10
    
    @classmethod
    async def detect_orphaned_cashouts(cls) -> List[Dict[str, Any]]:
        """
        Detect cashouts that are stuck in pending status for too long
        Returns list of orphaned cashout details
        """
        try:
            session = SessionLocal()
            
            # Calculate cutoff time (10 minutes ago) - FIXED: Use UTC for consistent timezone handling
            cutoff_time = datetime.utcnow() - timedelta(minutes=cls.ORPHAN_TIMEOUT_MINUTES)
            
            # CRITICAL FIX: Find cashouts that are stuck in any orphanable status:
            # 1. PENDING (standard cashouts)
            # 2. OTP_PENDING (abandoned LTC records) 
            # 3. ADMIN_PENDING (long-pending admin approvals)
            # Created more than 10 minutes ago
            orphaned_cashouts = session.query(Cashout).filter(
                and_(
                    Cashout.status.in_(ORPHANABLE_CASHOUT_STATUSES),
                    Cashout.created_at < cutoff_time
                )
            ).all()
            
            orphaned_data = []
            for cashout in orphaned_cashouts:
                orphaned_data.append({
                    'cashout_id': cashout.cashout_id,
                    'user_id': cashout.user_id,
                    'amount': cashout.amount,
                    'currency': cashout.currency,
                    'total_fee': cashout.total_fee,
                    'created_at': cashout.created_at,
                    'minutes_stuck': int((datetime.utcnow() - cashout.created_at).total_seconds() / 60)
                })
            
            session.close()
            
            if orphaned_data:
                logger.warning(f"🔍 ORPHAN DETECTION: Found {len(orphaned_data)} stuck cashouts")
                for orphan in orphaned_data:
                    logger.warning(f"   💸 {orphan['cashout_id']}: ${orphan['amount']} {orphan['currency']} stuck for {orphan['minutes_stuck']} minutes")
            else:
                logger.info("✅ ORPHAN DETECTION: No orphaned cashouts found")
            
            return orphaned_data
            
        except Exception as e:
            logger.error(f"❌ Failed to detect orphaned cashouts: {e}")
            return []
    
    @classmethod
    async def cleanup_orphaned_cashout(cls, cashout_id: str, reason: str = "Session timeout - automatic cleanup") -> Dict[str, Any]:
        """
        Cancel a specific orphaned cashout and return locked funds
        Uses the existing atomic cancellation service
        """
        try:
            logger.critical(f"🧹 ORPHAN CLEANUP: Cancelling stuck cashout {cashout_id}")
            
            # Cancel cashout and return locked funds directly (no external service needed)
            result = await cls._cancel_cashout_and_return_funds(cashout_id, reason)
            
            if result.get('success'):
                logger.critical(f"✅ ORPHAN CLEANUP SUCCESS: {cashout_id} cancelled and funds returned")
                return {
                    'success': True,
                    'cashout_id': cashout_id,
                    'action': 'cancelled_and_refunded',
                    'reason': reason
                }
            else:
                error = result.get('error', 'Unknown error')
                logger.error(f"❌ ORPHAN CLEANUP FAILED: {cashout_id} - {error}")
                return {
                    'success': False,
                    'cashout_id': cashout_id,
                    'error': error
                }
                
        except Exception as e:
            logger.error(f"❌ Exception during orphan cleanup for {cashout_id}: {e}")
            return {
                'success': False,
                'cashout_id': cashout_id,
                'error': str(e)
            }
    
    @classmethod
    async def _cancel_cashout_and_return_funds(cls, cashout_id: str, reason: str) -> Dict[str, Any]:
        """
        SECURITY-ENHANCED: Cancel a cashout and return locked funds to user's wallet
        CRITICAL: Now validates wallet was actually debited before allowing refund
        """
        try:
            session = SessionLocal()
            
            # Find the cashout
            cashout = session.query(Cashout).filter(Cashout.cashout_id == cashout_id).first()
            if not cashout:
                session.close()
                return {'success': False, 'error': 'Cashout not found'}
            
            # ENHANCED: Check if cashout is in a cancellable state using centralized constants
            # This prevents drift between services and handlers
            
            if cashout.status not in CANCELLABLE_CASHOUT_STATUSES:
                session.close() 
                return {'success': False, 'error': f'Cashout status is {cashout.status}, cannot cancel'}
            
            # CRITICAL SECURITY FIX: Validate wallet was actually debited for this cashout
            from utils.wallet_validation import WalletValidator
            from decimal import Decimal
            
            is_valid_debit, error_msg = WalletValidator.validate_cashout_debit_exists(
                user_id=cashout.user_id,
                cashout_id=cashout_id,
                expected_amount=Decimal(str(cashout.amount)),
                session=session
            )
            
            if not is_valid_debit:
                logger.error(
                    f"🚨 SECURITY BLOCK: Attempted refund for cashout {cashout_id} "
                    f"without corresponding debit: {error_msg}"
                )
                session.close()
                return {
                    'success': False, 
                    'error': f'Security validation failed: {error_msg}',
                    'security_block': True
                }
            
            # IDEMPOTENCY CHECK: Ensure this refund hasn't already been processed
            from services.idempotent_refund_service import IdempotentRefundService
            
            refund_key = f"orphan_cleanup_{cashout_id}"
            if IdempotentRefundService.is_refund_already_processed(refund_key):
                logger.warning(f"⚠️ DUPLICATE REFUND BLOCKED: {cashout_id} already refunded")
                session.close()
                return {
                    'success': False,
                    'error': 'Refund already processed',
                    'duplicate_block': True
                }
            
            # Import wallet service for crediting user
            from services.crypto import CryptoServiceAtomic
            
            # CRITICAL FIX: Find original USD debit amount instead of using crypto amount
            from models import Transaction
            
            # Find the original USD debit amount from wallet transactions
            original_debit = session.query(Transaction).filter(
                Transaction.user_id == cashout.user_id,
                Transaction.transaction_type == "cashout",
                Transaction.currency == "USD",
                Transaction.description.like(f"%{cashout_id}%")
            ).first()
            
            if original_debit:
                # Refund the original USD amount that was debited (make positive)
                original_usd_amount = abs(Decimal(str(original_debit.amount)))
                refund_currency = "USD"
                logger.info(f"💰 Found original debit: ${original_usd_amount} USD for cashout {cashout_id}")
            else:
                # Fallback: Determine correct USD amount based on cashout format
                logger.warning(f"⚠️ No original debit found for {cashout_id}, determining refund amount")
                
                # CRITICAL FIX: Check cashout ID format to determine how to interpret amount
                # WD format cashouts store USD amount in the amount field with crypto as currency
                # Direct crypto cashouts (LTC_, ETH_, etc.) store actual crypto amounts
                if cashout_id.startswith("WD"):
                    # WD format: amount field contains USD value, not crypto amount
                    # This is a legacy format where amount=2.00, currency=LTC means $2.00 worth of LTC
                    if cashout.currency in ["LTC", "BTC", "ETH", "DOGE", "BCH", "TRX", "USDT"]:
                        original_usd_amount = Decimal(str(cashout.amount))
                        refund_currency = "USD"
                        logger.info(f"💰 WD format crypto cashout: using stored USD value ${original_usd_amount:.2f}")
                    else:
                        # For NGN or other fiat in WD format, keep as is
                        original_usd_amount = Decimal(str(cashout.amount))
                        refund_currency = cashout.currency
                elif cashout.currency in ["LTC", "BTC", "ETH", "DOGE", "BCH", "TRX", "USDT"]:
                    # Direct crypto format: amount field contains actual crypto amount
                    # Need to convert crypto amount to USD using current rates
                    try:
                        from services.fastforex_service import FastForexService
                        fastforex = FastForexService()
                        crypto_rate = await fastforex.get_crypto_to_usd_rate(cashout.currency)
                        original_usd_amount = Decimal(str(cashout.amount)) * Decimal(str(crypto_rate))
                        refund_currency = "USD"
                        logger.info(f"💱 Direct crypto cashout: {cashout.amount} {cashout.currency} → ${original_usd_amount:.2f} USD at rate ${crypto_rate}")
                    except Exception as e:
                        # Conservative fallback if rate fetch fails
                        original_usd_amount = Decimal("2.50")
                        refund_currency = "USD"
                        logger.error(f"❌ Rate conversion failed for {cashout.currency}: {e}, using conservative fallback ${original_usd_amount} USD")
                else:
                    # NGN or other currency: amount is already in correct currency
                    original_usd_amount = Decimal(str(cashout.amount))
                    refund_currency = cashout.currency
                    logger.info(f"💰 Fiat cashout: {original_usd_amount} {refund_currency}")
            
            # FIXED: Use atomic transaction with correct USD amount
            credit_success = CryptoServiceAtomic.credit_user_wallet_atomic(
                user_id=cashout.user_id,
                amount=original_usd_amount,
                currency=refund_currency,
                transaction_type="refund",
                description=f"Orphan cleanup: {reason} (Cashout: {cashout_id})",
                session=session  # Use same session for atomicity
            )
            
            if not credit_success:
                session.close()
                return {'success': False, 'error': 'Failed to credit user wallet'}
            
            # Mark refund as processed to prevent duplicates
            IdempotentRefundService.mark_refund_processed(refund_key)
            
            # Update cashout status to cancelled
            cashout.status = CashoutStatus.CANCELLED.value
            
            session.commit()
            session.close()
            
            logger.info(
                f"✅ SECURE_ORPHAN_CANCEL: {cashout_id} cancelled after debit validation, "
                f"${original_usd_amount} {refund_currency} returned to user {cashout.user_id}"
            )
            
            return {
                'success': True,
                'cashout_id': cashout_id,
                'amount_refunded': original_usd_amount,
                'currency': refund_currency,
                'security_validated': True
            }
            
        except Exception as e:
            logger.error(f"❌ Error in secure orphan cleanup for {cashout_id}: {e}")
            return {'success': False, 'error': str(e)}
    
    @classmethod
    async def run_cleanup_cycle(cls) -> Dict[str, Any]:
        """
        Run a complete cleanup cycle:
        1. Detect orphaned cashouts
        2. Cancel them and return funds
        3. Report results
        """
        try:
            logger.info("🔄 ORPHAN CLEANUP CYCLE: Starting...")
            
            # Step 1: Detect orphaned cashouts
            orphaned_cashouts = await cls.detect_orphaned_cashouts()
            
            if not orphaned_cashouts:
                return {
                    'success': True,
                    'orphans_found': 0,
                    'orphans_cleaned': 0,
                    'message': 'No orphaned cashouts found'
                }
            
            # Step 2: Clean up each orphaned cashout
            cleanup_results = []
            successful_cleanups = 0
            
            for orphan in orphaned_cashouts:
                cashout_id = orphan['cashout_id']
                minutes_stuck = orphan['minutes_stuck']
                
                cleanup_result = await cls.cleanup_orphaned_cashout(
                    cashout_id=cashout_id,
                    reason=f"Automatic cleanup - stuck in pending for {minutes_stuck} minutes"
                )
                
                cleanup_results.append(cleanup_result)
                
                if cleanup_result.get('success'):
                    successful_cleanups += 1
            
            # Step 3: Report results
            logger.critical(f"🧹 ORPHAN CLEANUP COMPLETE: {successful_cleanups}/{len(orphaned_cashouts)} cashouts cleaned successfully")
            
            return {
                'success': True,
                'orphans_found': len(orphaned_cashouts),
                'orphans_cleaned': successful_cleanups,
                'cleanup_results': cleanup_results,
                'message': f'Cleaned {successful_cleanups} of {len(orphaned_cashouts)} orphaned cashouts'
            }
            
        except Exception as e:
            logger.error(f"❌ ORPHAN CLEANUP CYCLE FAILED: {e}")
            return {
                'success': False,
                'error': str(e),
                'message': 'Cleanup cycle failed'
            }

# Global service instance
orphaned_cashout_cleanup_service = OrphanedCashoutCleanupService()