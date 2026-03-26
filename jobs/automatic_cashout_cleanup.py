"""
Automatic Cashout Cleanup Service
Prevents stuck cashouts from blocking users indefinitely
Now uses intelligent retry system to distinguish technical vs user failures
"""

import logging
import datetime as dt
from database import SessionLocal
from models import Cashout, CashoutStatus
from services.cashout_retry_service import cashout_retry_service
from utils.cashout_state_validator import CashoutStateValidator

logger = logging.getLogger(__name__)

class AutomaticCashoutCleanup:
    """Service to automatically clean up stuck cashouts"""
    
    @classmethod
    async def cleanup_stuck_cashouts(cls):
        """Clean up cashouts stuck in processing for >5 minutes"""
        session = None
        try:
            session = SessionLocal()
            
            # Find cashouts stuck for more than 10 minutes (extended grace period)
            # EXCLUDE PENDING_SERVICE_FUNDING - those are awaiting admin funding, not stuck
            # EXCLUDE recently processed cashouts - admin may have just taken action
            stuck_threshold = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=10)
            recent_process_threshold = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=5)
            
            # RETRY SYSTEM FIX: Only process truly stuck cashouts in PROCESSING states
            # EXCLUDE all PENDING states - those are awaiting user action, not retry-eligible
            stuck_cashouts = session.query(Cashout).filter(
                Cashout.status.in_([
                    # Only include states where system is stuck processing, not user waiting
                    CashoutStatus.PROCESSING.value,
                    CashoutStatus.EXECUTING.value,
                    CashoutStatus.AWAITING_RESPONSE.value
                    # EXCLUDED: PENDING* statuses are user action states, not retry-eligible
                    # EXCLUDED: PENDING_SERVICE_FUNDING - those need admin funding
                ]),
                Cashout.created_at <= stuck_threshold,
                # RACE CONDITION FIX: Don't clean up cashouts recently processed by admin
                # Use OR condition: either processed_at is NULL or it's older than threshold
                (Cashout.processed_at.is_(None)) | (Cashout.processed_at <= recent_process_threshold)  # type: ignore
            ).all()
            
            if stuck_cashouts:
                logger.warning(f"🔧 CLEANUP JOB: Found {len(stuck_cashouts)} stuck cashouts")
                
                cleanup_count = 0
                for cashout in stuck_cashouts:
                    age_minutes = (dt.datetime.now(dt.timezone.utc) - cashout.created_at).total_seconds() / 60
                    logger.warning(
                        f"   • Cleaning stuck cashout {cashout.cashout_id} "
                        f"(user: {cashout.user_id}, age: {age_minutes:.1f}min)"
                    )
                    
                    # CRITICAL FIX: Check if cashout was already refunded BEFORE attempting refund
                    from models import Transaction, TransactionType
                    existing_refund = session.query(Transaction).filter(
                        Transaction.user_id == cashout.user_id,
                        Transaction.transaction_type == TransactionType.REFUND.value,
                        Transaction.currency == "USD",
                        Transaction.description.like(f"%{cashout.cashout_id}%")
                    ).first()
                    
                    if existing_refund:
                        logger.warning(f"⚠️ Cashout {cashout.cashout_id} already has refund transaction {existing_refund.transaction_id} - skipping duplicate refund")
                        # Just mark as failed without refunding again
                        # SECURITY: Validate state transition to prevent overwriting terminal states
                        try:
                            current_status = CashoutStatus(cashout.status)
                            CashoutStateValidator.validate_transition(
                                current_status, 
                                CashoutStatus.FAILED, 
                                str(cashout.cashout_id)
                            )
                            cashout.status = CashoutStatus.FAILED.value  # type: ignore
                            cleanup_count += 1
                        except Exception as validation_error:
                            logger.error(
                                f"🚫 CLEANUP_FAIL_BLOCKED: {current_status}→FAILED for {cashout.cashout_id}: {validation_error}"
                            )
                            # Skip if already in terminal state (COMPLETED, SUCCESS, etc.)
                            logger.info(f"ℹ️ Cashout {cashout.cashout_id} already in terminal state {current_status.value}, skipping")
                        continue
                    
                    # NEW: Use intelligent retry system instead of direct refund
                    try:
                        # Create a timeout exception to classify the stuck cashout
                        timeout_exception = Exception(f"Cashout stuck in {cashout.status} for {age_minutes:.1f} minutes - automatic cleanup triggered")
                        
                        # Use retry orchestrator to classify and handle intelligently
                        retry_scheduled = await cashout_retry_service.handle_cashout_failure(
                            cashout_id=str(cashout.cashout_id),  # type: ignore
                            exception=timeout_exception,
                            context={
                                "source": "automatic_cleanup",
                                "age_minutes": age_minutes,
                                "original_status": str(cashout.status),  # type: ignore
                                "cashout_type": str(cashout.cashout_type) if cashout.cashout_type else None  # type: ignore
                            }
                        )
                        
                        if retry_scheduled:
                            logger.info(f"⏰ RETRY_SCHEDULED: {cashout.cashout_id} classified as technical failure - retry scheduled")
                            cleanup_count += 1
                            continue
                        else:
                            logger.info(f"💰 REFUND_TRIGGERED: {cashout.cashout_id} classified as user error or max retries exceeded")
                            cleanup_count += 1
                            continue
                            
                    except Exception as retry_error:
                        # Fallback to original refund logic if retry system fails
                        logger.error(f"❌ Retry system failed for {cashout.cashout_id}: {retry_error}, falling back to direct refund")
                        
                        # ORIGINAL LOGIC: Refund user's wallet before marking as failed
                        from services.wallet_service import CryptoServiceAtomic
                        
                        # CRITICAL FIX: Check cashout metadata for hold_transaction_id first
                        original_debit = None
                        if cashout.cashout_metadata:  # type: ignore
                            import json
                            try:
                                metadata = json.loads(cashout.cashout_metadata) if isinstance(cashout.cashout_metadata, str) else cashout.cashout_metadata
                                hold_transaction_id = metadata.get('hold_transaction_id')
                                if hold_transaction_id:
                                    # Direct lookup by transaction ID from metadata
                                    original_debit = session.query(Transaction).filter(
                                        Transaction.transaction_id == hold_transaction_id,
                                        Transaction.user_id == cashout.user_id
                                    ).first()
                                    if original_debit:
                                        logger.info(f"🔗 Found original transaction via metadata: {hold_transaction_id}")
                            except (json.JSONDecodeError, TypeError) as meta_error:
                                logger.warning(f"⚠️ Failed to parse cashout metadata: {meta_error}")
                        
                        # Fallback: Search by time window and improved transaction types
                        if not original_debit:
                            time_window_start = cashout.created_at - dt.timedelta(minutes=2)
                            time_window_end = cashout.created_at + dt.timedelta(minutes=2)
                            
                            original_debit = session.query(Transaction).filter(
                                Transaction.user_id == cashout.user_id,
                                Transaction.transaction_type.in_(["cashout", "cashout_hold", "debit"]),  # Multiple types
                                Transaction.currency == "USD",
                                # Remove amount sign requirement - cashout_hold can be positive
                                Transaction.created_at >= time_window_start,
                                Transaction.created_at <= time_window_end
                            ).order_by(Transaction.created_at.desc()).first()
                        
                        if original_debit:
                            # Refund the original USD amount that was debited (make positive)
                            # CRITICAL: Maintain Decimal precision for financial calculations
                            from decimal import Decimal
                            original_usd_amount = abs(Decimal(str(original_debit.amount)))  # type: ignore
                            logger.info(f"💰 Found original debit: ${original_usd_amount} USD for cashout {cashout.cashout_id}")
                        else:
                            # Fallback: Determine correct USD amount based on cashout format
                            logger.warning(f"⚠️ No original debit found for {cashout.cashout_id}, determining refund amount")
                            
                            # CRITICAL FIX: Check cashout ID format to determine how to interpret amount
                            # WD format cashouts store USD amount in the amount field with crypto as currency
                            # Direct crypto cashouts (LTC_, ETH_, etc.) store actual crypto amounts
                            from decimal import Decimal
                            if str(cashout.cashout_id).startswith("WD"):  # type: ignore
                                # WD format: amount field contains USD value, not crypto amount
                                # This is a legacy format where amount=2.00, currency=LTC means $2.00 worth of LTC
                                original_usd_amount = Decimal(str(cashout.amount))  # type: ignore  # Maintain Decimal precision
                                logger.info(f"💰 WD format cashout: using stored USD value ${original_usd_amount:.2f}")
                            elif str(cashout.currency) in ["LTC", "BTC", "ETH", "DOGE", "BCH", "TRX", "USDT"]:  # type: ignore
                                # Direct crypto format: amount field contains actual crypto amount
                                # Need to convert crypto amount to USD using current rates
                                from services.fastforex_service import FastForexService
                                fastforex = FastForexService()
                                try:
                                    crypto_rate = await fastforex.get_crypto_to_usd_rate(str(cashout.currency))  # type: ignore  # Returns Decimal
                                    # CRITICAL: Maintain Decimal precision - Decimal * Decimal = precise Decimal
                                    cashout_amount = Decimal(str(cashout.amount))  # type: ignore
                                    original_usd_amount = cashout_amount * crypto_rate  # Decimal * Decimal (no precision loss!)
                                    logger.info(f"💱 Direct crypto cashout: {cashout.amount} {cashout.currency} → ${original_usd_amount:.2f} USD at rate ${crypto_rate}")
                                except Exception as rate_error:
                                    # Conservative fallback if rate fetch fails
                                    original_usd_amount = Decimal("2.50")
                                    logger.error(f"❌ Rate conversion failed for {cashout.currency}: {rate_error}, using conservative fallback ${original_usd_amount} USD")
                            elif str(cashout.cashout_type) in ["NGN_BANK"] or str(cashout.currency) == "USD":  # type: ignore
                                # USD/NGN cashouts: amount field contains USD value
                                original_usd_amount = Decimal(str(cashout.amount))  # type: ignore  # Maintain Decimal precision
                                logger.info(f"💰 USD/NGN cashout: using stored USD value ${original_usd_amount:.2f} from amount field")
                            else:
                                # Unknown format: conservative fallback
                                original_usd_amount = Decimal("2.50")  # Conservative fallback as Decimal
                                logger.warning(f"⚠️ Unknown cashout format for {cashout.cashout_id}, using conservative fallback ${original_usd_amount} USD")
                        
                        refund_success = CryptoServiceAtomic.credit_user_wallet_atomic(
                            user_id=int(cashout.user_id),  # type: ignore
                            amount=original_usd_amount,
                            currency="USD",
                            transaction_type=TransactionType.REFUND.value,
                            description=f"Auto-refund for stuck cashout {cashout.cashout_id} (cleanup job)",
                            escrow_id=None
                        )
                        
                        if refund_success:
                            logger.info(f"✅ Wallet refunded: ${original_usd_amount:.2f} USD for stuck cashout {cashout.cashout_id}")
                        else:
                            logger.error(f"❌ Failed to refund wallet for stuck cashout {cashout.cashout_id} - manual intervention required")
                        
                        # Mark as failed to clear the block
                        # SECURITY: Validate state transition to prevent overwriting terminal states
                        try:
                            current_status = CashoutStatus(cashout.status)
                            CashoutStateValidator.validate_transition(
                                current_status, 
                                CashoutStatus.FAILED, 
                                str(cashout.cashout_id)
                            )
                            cashout.status = CashoutStatus.FAILED.value  # type: ignore
                            cleanup_count += 1
                        except Exception as validation_error:
                            logger.error(
                                f"🚫 CLEANUP_FAIL_BLOCKED: {current_status}→FAILED for {cashout.cashout_id}: {validation_error}"
                            )
                            # Skip if already in terminal state (COMPLETED, SUCCESS, etc.)
                            logger.info(f"ℹ️ Cashout {cashout.cashout_id} already in terminal state {current_status.value}, skipping cleanup")
                
                session.commit()
                logger.info(f"✅ CLEANUP JOB: Cleared {cleanup_count} stuck cashouts")
                
                return cleanup_count
            else:
                logger.info("✅ CLEANUP JOB: No stuck cashouts found")
                return 0
                
        except Exception as e:
            logger.error(f"❌ CLEANUP JOB ERROR: {e}")
            import traceback
            logger.error(f"❌ CLEANUP JOB TRACEBACK: {traceback.format_exc()}")
            return 0
        finally:
            if session is not None:
                session.close()
    
    @classmethod
    async def get_cleanup_stats(cls):
        """Get statistics about cashout statuses"""
        session = None
        try:
            session = SessionLocal()
            
            # Count cashouts by status
            stats = {}
            for status in [CashoutStatus.PENDING, CashoutStatus.OTP_PENDING, CashoutStatus.EXECUTING, CashoutStatus.AWAITING_RESPONSE, CashoutStatus.FAILED, CashoutStatus.COMPLETED]:
                count = session.query(Cashout).filter_by(status=status.value).count()
                stats[status.value] = count
            
            # Count stuck cashouts (>5 minutes old and still processing)  
            stuck_threshold = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=5)
            stuck_count = session.query(Cashout).filter(
                Cashout.status.in_([
                    CashoutStatus.PENDING.value,
                    CashoutStatus.OTP_PENDING.value,  # CRITICAL FIX: Include deferred records in statistics
                    CashoutStatus.PENDING_ADDRESS_CONFIG.value
                ]),
                Cashout.created_at <= stuck_threshold
            ).count()
            
            stats['stuck_count'] = stuck_count
            return stats
            
        except Exception as e:
            logger.error(f"❌ Stats error: {e}")
            return {}
        finally:
            if session is not None:
                session.close()

# Background job function for scheduler
async def run_cashout_cleanup():
    """Background job to clean up stuck cashouts every 3 minutes"""
    cleanup_service = AutomaticCashoutCleanup()
    return await cleanup_service.cleanup_stuck_cashouts()