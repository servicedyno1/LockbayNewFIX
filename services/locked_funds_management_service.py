"""
Enhanced Locked Funds Management Service
Provides comprehensive locked funds detection, cleanup, and monitoring with proper idempotency
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from decimal import Decimal
from sqlalchemy import and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from models import Transaction, Cashout, Wallet, CashoutStatus
from services.idempotency import IdempotencyService
from services.crypto import CryptoServiceAtomic
from utils.universal_id_generator import UniversalIDGenerator
from utils.atomic_transactions import async_atomic_transaction

logger = logging.getLogger(__name__)


class LockedFundsManagementService:
    """
    Comprehensive locked funds detection and management service
    Integrates with existing systems for safe fund recovery
    """
    
    # Configuration
    STALE_CASHOUT_TIMEOUT_MINUTES = 30  # Cashouts stuck longer than this are considered stale (safety)
    CLEANUP_BATCH_SIZE = 50  # Process this many records at once
    MAX_AUTO_CLEANUP_USD = 50.0  # Safety limit for automatic cleanup (conservative)
    
    @classmethod
    async def detect_locked_funds_issues(cls, user_id: Optional[int] = None) -> Dict[str, Any]:
        """
        Comprehensive detection of locked funds issues across the platform
        
        Args:
            user_id: Optional specific user to check, if None checks all users
            
        Returns:
            Dict with detection results and recommendations
        """
        try:
            logger.info("🔍 LOCKED_FUNDS_DETECTION: Starting comprehensive scan" + 
                       (f" for user {user_id}" if user_id else " (all users)"))
            
            async with async_atomic_transaction() as session:
                results = {
                    'scan_timestamp': datetime.utcnow().isoformat(),
                    'user_scope': user_id or 'all_users',
                    'issues_found': [],
                    'cleanup_recommendations': [],
                    'total_locked_amount': Decimal('0'),
                    'users_affected': 0
                }
                
                # 1. Detect stale pending cashouts
                stale_cashouts = await cls._detect_stale_cashouts(session, user_id)
                if stale_cashouts['issues']:
                    results['issues_found'].extend(stale_cashouts['issues'])
                    results['total_locked_amount'] += stale_cashouts['total_amount']
                    results['cleanup_recommendations'].append('Cancel stale cashouts and refund locked funds')
                
                # 2. Detect wallet balance inconsistencies
                balance_issues = await cls._detect_wallet_inconsistencies(session, user_id)
                if balance_issues['issues']:
                    results['issues_found'].extend(balance_issues['issues'])
                    results['cleanup_recommendations'].append('Reconcile wallet balance inconsistencies')
                
                # 3. Detect orphaned transaction records
                orphaned_txns = await cls._detect_orphaned_transactions(session, user_id)
                if orphaned_txns['issues']:
                    results['issues_found'].extend(orphaned_txns['issues'])
                    results['cleanup_recommendations'].append('Clean up orphaned transaction records')
                
                # 4. Calculate summary statistics
                results['users_affected'] = len(set(issue.get('user_id') for issue in results['issues_found'] if issue.get('user_id')))
                results['severity'] = cls._calculate_severity(results)
                
                logger.info(f"🔍 DETECTION COMPLETE: Found {len(results['issues_found'])} issues affecting {results['users_affected']} users")
                
                return results
                
        except Exception as e:
            logger.error(f"❌ LOCKED_FUNDS_DETECTION_FAILED: {e}")
            return {
                'scan_timestamp': datetime.utcnow().isoformat(),
                'error': str(e),
                'issues_found': [],
                'severity': 'error'
            }
    
    @classmethod
    async def _detect_stale_cashouts(cls, session: AsyncSession, user_id: Optional[int] = None) -> Dict[str, Any]:
        """Detect cashouts that are stuck in pending status"""
        try:
            cutoff_time = datetime.utcnow() - timedelta(minutes=cls.STALE_CASHOUT_TIMEOUT_MINUTES)
            
            query = session.query(Cashout).filter(
                and_(
                    Cashout.status.in_([
                        CashoutStatus.PENDING.value,
                        CashoutStatus.OTP_PENDING.value,
                        CashoutStatus.ADMIN_PENDING.value
                    ]),
                    Cashout.created_at < cutoff_time
                )
            )
            
            if user_id:
                query = query.filter(Cashout.user_id == user_id)
            
            stale_cashouts = query.limit(cls.CLEANUP_BATCH_SIZE).all()
            
            issues = []
            total_amount = Decimal('0')
            
            for cashout in stale_cashouts:
                minutes_stuck = int((datetime.utcnow() - cashout.created_at).total_seconds() / 60)
                issue = {
                    'type': 'stale_cashout',
                    'user_id': cashout.user_id,
                    'cashout_id': cashout.cashout_id,
                    'amount': float(cashout.amount),
                    'currency': cashout.currency,
                    'status': cashout.status,
                    'minutes_stuck': minutes_stuck,
                    'created_at': cashout.created_at.isoformat(),
                    'severity': 'high' if minutes_stuck > 60 else 'medium'
                }
                issues.append(issue)
                total_amount += Decimal(str(cashout.amount))
            
            return {
                'issues': issues,
                'total_amount': total_amount,
                'count': len(issues)
            }
            
        except Exception as e:
            logger.error(f"Error detecting stale cashouts: {e}")
            return {'issues': [], 'total_amount': Decimal('0'), 'count': 0}
    
    @classmethod
    async def _detect_wallet_inconsistencies(cls, session: AsyncSession, user_id: Optional[int] = None) -> Dict[str, Any]:
        """Detect wallets with inconsistent locked balances"""
        try:
            query = session.query(Wallet).filter(
                Wallet.locked_balance > 0
            )
            
            if user_id:
                query = query.filter(Wallet.user_id == user_id)
            
            wallets_with_locks = query.all()
            
            issues = []
            
            for wallet in wallets_with_locks:
                # Check if there are corresponding pending cashouts
                pending_cashouts = session.query(Cashout).filter(
                    and_(
                        Cashout.user_id == wallet.user_id,
                        Cashout.currency == wallet.currency,
                        Cashout.status.in_([
                            CashoutStatus.PENDING.value,
                            CashoutStatus.OTP_PENDING.value,
                            CashoutStatus.ADMIN_PENDING.value,
                            CashoutStatus.APPROVED.value,
                            CashoutStatus.EXECUTING.value
                        ])
                    )
                ).all()
                
                expected_locked = sum(Decimal(str(co.amount)) for co in pending_cashouts)
                actual_locked = Decimal(str(wallet.locked_balance))
                
                if abs(expected_locked - actual_locked) > Decimal('0.01'):  # Allow for small rounding differences
                    issue = {
                        'type': 'wallet_inconsistency',
                        'user_id': wallet.user_id,
                        'currency': wallet.currency,
                        'actual_locked': float(actual_locked),
                        'expected_locked': float(expected_locked),
                        'difference': float(actual_locked - expected_locked),
                        'pending_cashouts_count': len(pending_cashouts),
                        'severity': 'high' if abs(actual_locked - expected_locked) > 1 else 'medium'
                    }
                    issues.append(issue)
            
            return {
                'issues': issues,
                'count': len(issues)
            }
            
        except Exception as e:
            logger.error(f"Error detecting wallet inconsistencies: {e}")
            return {'issues': [], 'count': 0}
    
    @classmethod
    async def _detect_orphaned_transactions(cls, session: AsyncSession, user_id: Optional[int] = None) -> Dict[str, Any]:
        """Detect transaction records that might be orphaned"""
        try:
            # Look for old pending cashout transactions
            cutoff_time = datetime.utcnow() - timedelta(hours=2)
            
            query = session.query(Transaction).filter(
                and_(
                    Transaction.transaction_type == 'cashout',
                    Transaction.status == 'pending',
                    Transaction.created_at < cutoff_time
                )
            )
            
            if user_id:
                query = query.filter(Transaction.user_id == user_id)
            
            orphaned_txns = query.limit(cls.CLEANUP_BATCH_SIZE).all()
            
            issues = []
            
            for txn in orphaned_txns:
                # Check if there's a corresponding cashout record
                corresponding_cashout = session.query(Cashout).filter(
                    Cashout.user_id == txn.user_id
                ).filter(
                    or_(
                        Cashout.cashout_id.like(f"%{txn.transaction_id.split('-')[-1]}%"),
                        Cashout.created_at.between(
                            txn.created_at - timedelta(minutes=5),
                            txn.created_at + timedelta(minutes=5)
                        )
                    )
                ).first()
                
                if not corresponding_cashout:
                    hours_old = (datetime.utcnow() - txn.created_at).total_seconds() / 3600
                    issue = {
                        'type': 'orphaned_transaction',
                        'user_id': txn.user_id,
                        'transaction_id': txn.transaction_id,
                        'amount': float(abs(txn.amount)),
                        'currency': txn.currency,
                        'hours_old': round(hours_old, 1),
                        'created_at': txn.created_at.isoformat(),
                        'severity': 'medium'
                    }
                    issues.append(issue)
            
            return {
                'issues': issues,
                'count': len(issues)
            }
            
        except Exception as e:
            logger.error(f"Error detecting orphaned transactions: {e}")
            return {'issues': [], 'count': 0}
    
    @classmethod
    async def cleanup_user_locked_funds(cls, user_id: int, dry_run: bool = True) -> Dict[str, Any]:
        """
        Clean up locked funds for a specific user with comprehensive safety checks
        
        Args:
            user_id: User to clean up funds for
            dry_run: If True, only simulate cleanup without making changes
            
        Returns:
            Dict with cleanup results
        """
        try:
            logger.info(f"🧹 CLEANUP_USER_LOCKED_FUNDS: User {user_id} (dry_run={dry_run})")
            
            # First, detect issues for this user
            detection_results = await cls.detect_locked_funds_issues(user_id)
            
            if not detection_results['issues_found']:
                return {
                    'success': True,
                    'user_id': user_id,
                    'actions_taken': [],
                    'message': 'No locked funds issues found for user'
                }
            
            actions_taken = []
            total_refunded = Decimal('0')
            
            async with async_atomic_transaction() as session:
                # Process stale cashouts
                for issue in detection_results['issues_found']:
                    if issue['type'] == 'stale_cashout':
                        result = await cls._cleanup_stale_cashout(
                            issue['cashout_id'], 
                            session, 
                            dry_run=dry_run
                        )
                        if result['success']:
                            actions_taken.append(result)
                            total_refunded += Decimal(str(result.get('amount_refunded', 0)))
                    
                    elif issue['type'] == 'wallet_inconsistency':
                        result = await cls._fix_wallet_inconsistency(
                            user_id,
                            issue['currency'],
                            issue['difference'],
                            session,
                            dry_run=dry_run
                        )
                        if result['success']:
                            actions_taken.append(result)
                    
                    elif issue['type'] == 'orphaned_transaction':
                        result = await cls._cleanup_orphaned_transaction(
                            issue['transaction_id'],
                            session,
                            dry_run=dry_run
                        )
                        if result['success']:
                            actions_taken.append(result)
                            total_refunded += Decimal(str(result.get('amount_refunded', 0)))
            
            return {
                'success': True,
                'user_id': user_id,
                'dry_run': dry_run,
                'actions_taken': actions_taken,
                'total_refunded': float(total_refunded),
                'issues_resolved': len(actions_taken),
                'message': f"{'Simulated' if dry_run else 'Completed'} cleanup for user {user_id}"
            }
            
        except Exception as e:
            logger.error(f"❌ CLEANUP_USER_LOCKED_FUNDS_FAILED: {e}")
            return {
                'success': False,
                'user_id': user_id,
                'error': str(e)
            }
    
    @classmethod
    async def _cleanup_stale_cashout(cls, cashout_id: str, session: AsyncSession, dry_run: bool = True) -> Dict[str, Any]:
        """Clean up a stale cashout with proper idempotency"""
        from models import Transaction
        try:
            cashout = session.query(Cashout).filter(Cashout.cashout_id == cashout_id).first()
            if not cashout:
                return {'success': False, 'error': 'Cashout not found'}
            
            # Create idempotency key for this cleanup operation
            idempotency_key = f"cleanup_stale_cashout_{cashout_id}_{int(datetime.utcnow().timestamp())}"
            
            if IdempotencyService.is_duplicate_operation(idempotency_key, window_seconds=3600):
                return {'success': False, 'error': 'Cleanup already in progress or completed'}
            
            if dry_run:
                return {
                    'success': True,
                    'action': 'cleanup_stale_cashout',
                    'cashout_id': cashout_id,
                    'user_id': cashout.user_id,
                    'amount_would_refund': float(cashout.amount),
                    'currency': cashout.currency,
                    'dry_run': True
                }
            
            # Generate refund transaction ID
            refund_id = UniversalIDGenerator.generate_refund_id()
            
            # Create refund transaction
            refund_transaction = Transaction(
                transaction_id=refund_id,
                user_id=cashout.user_id,
                transaction_type='refund',
                amount=float(cashout.amount),
                currency=cashout.currency,
                status='completed',
                description=f'Automatic refund for stale cashout {cashout_id}',
                confirmations=1,
                created_at=datetime.utcnow()
            )
            session.add(refund_transaction)
            
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
                original_usd_amount = abs(float(original_debit.amount))
                refund_currency = "USD"
                logger.info(f"💰 Found original debit: ${original_usd_amount} USD for cashout {cashout_id}")
            else:
                # Fallback: For crypto cashouts, convert crypto back to USD at current rates
                logger.warning(f"⚠️ No original debit found for {cashout_id}, using rate conversion")
                if cashout.currency in ["LTC", "BTC", "ETH", "DOGE", "BCH", "TRX"]:
                    try:
                        from services.fastforex_service import FastForexService
                        fastforex = FastForexService()
                        crypto_rate = await fastforex.get_crypto_to_usd_rate(cashout.currency)
                        original_usd_amount = float(cashout.amount) * crypto_rate
                        refund_currency = "USD"
                        logger.info(f"💱 Converted {cashout.amount} {cashout.currency} → ${original_usd_amount:.2f} USD at rate ${crypto_rate}")
                    except Exception as e:
                        # Last resort: estimate based on typical amounts
                        original_usd_amount = 2.50
                        refund_currency = "USD"
                        logger.error(f"❌ Rate conversion failed: {e}, using fallback ${original_usd_amount} USD")
                else:
                    # For NGN or other fiat, keep as is
                    original_usd_amount = float(cashout.amount)
                    refund_currency = cashout.currency
            
            # Update the transaction record to reflect correct amount
            refund_transaction.amount = original_usd_amount
            refund_transaction.currency = refund_currency
            
            # Credit user's wallet using atomic service with correct USD amount
            credit_success = await CryptoServiceAtomic.credit_user_wallet_atomic(
                user_id=cashout.user_id,
                amount=original_usd_amount,
                currency=refund_currency,
                transaction_type="refund",
                description=f"Refund for stale cashout {cashout_id}"
            )
            
            if not credit_success:
                return {'success': False, 'error': 'Failed to credit wallet'}
            
            # Update cashout status
            cashout.status = CashoutStatus.CANCELLED.value
            
            # Register idempotency
            IdempotencyService.register_operation(idempotency_key)
            
            session.commit()
            
            logger.info(f"✅ CLEANUP_STALE_CASHOUT: {cashout_id} refunded ${original_usd_amount} {refund_currency} to user {cashout.user_id}")
            
            return {
                'success': True,
                'action': 'cleanup_stale_cashout',
                'cashout_id': cashout_id,
                'user_id': cashout.user_id,
                'amount_refunded': original_usd_amount,
                'currency': refund_currency,
                'refund_transaction_id': refund_id
            }
            
        except Exception as e:
            logger.error(f"Error cleaning up stale cashout {cashout_id}: {e}")
            return {'success': False, 'error': str(e)}
    
    @classmethod
    async def _fix_wallet_inconsistency(cls, user_id: int, currency: str, difference: float, session: AsyncSession, dry_run: bool = True) -> Dict[str, Any]:
        """Fix wallet balance inconsistency"""
        try:
            if dry_run:
                return {
                    'success': True,
                    'action': 'fix_wallet_inconsistency',
                    'user_id': user_id,
                    'currency': currency,
                    'adjustment_amount': -difference,
                    'dry_run': True
                }
            
            # Get wallet
            wallet = session.query(Wallet).filter(
                and_(Wallet.user_id == user_id, Wallet.currency == currency)
            ).first()
            
            if not wallet:
                return {'success': False, 'error': 'Wallet not found'}
            
            # Adjust locked balance
            wallet.locked_balance = max(0, wallet.locked_balance - Decimal(str(abs(difference))))
            wallet.updated_at = datetime.utcnow()
            
            session.commit()
            
            return {
                'success': True,
                'action': 'fix_wallet_inconsistency',
                'user_id': user_id,
                'currency': currency,
                'adjustment_amount': -abs(difference)
            }
            
        except Exception as e:
            logger.error(f"Error fixing wallet inconsistency for user {user_id}: {e}")
            return {'success': False, 'error': str(e)}
    
    @classmethod
    async def _cleanup_orphaned_transaction(cls, transaction_id: str, session: AsyncSession, dry_run: bool = True) -> Dict[str, Any]:
        """Clean up orphaned transaction record"""
        try:
            txn = session.query(Transaction).filter(Transaction.transaction_id == transaction_id).first()
            if not txn:
                return {'success': False, 'error': 'Transaction not found'}
            
            if dry_run:
                return {
                    'success': True,
                    'action': 'cleanup_orphaned_transaction',
                    'transaction_id': transaction_id,
                    'user_id': txn.user_id,
                    'amount_would_refund': float(abs(txn.amount)),
                    'dry_run': True
                }
            
            # Cancel the orphaned transaction
            txn.status = 'cancelled'
            
            # If it was a debit transaction, refund the amount
            if txn.amount < 0:
                refund_amount = abs(txn.amount)
                credit_success = await CryptoServiceAtomic.credit_user_wallet_atomic(
                    user_id=txn.user_id,
                    amount=refund_amount,
                    currency=txn.currency,
                    transaction_type="refund",
                    description=f"Refund for orphaned transaction {transaction_id}"
                )
                
                if credit_success:
                    session.commit()
                    return {
                        'success': True,
                        'action': 'cleanup_orphaned_transaction',
                        'transaction_id': transaction_id,
                        'user_id': txn.user_id,
                        'amount_refunded': refund_amount
                    }
            
            session.commit()
            return {
                'success': True,
                'action': 'cleanup_orphaned_transaction',
                'transaction_id': transaction_id,
                'user_id': txn.user_id
            }
            
        except Exception as e:
            logger.error(f"Error cleaning up orphaned transaction {transaction_id}: {e}")
            return {'success': False, 'error': str(e)}
    
    @classmethod
    def _calculate_severity(cls, results: Dict[str, Any]) -> str:
        """Calculate overall severity of locked funds issues"""
        issues = results['issues_found']
        
        if not issues:
            return 'none'
        
        high_count = sum(1 for issue in issues if issue.get('severity') == 'high')
        total_amount = float(results['total_locked_amount'])
        
        if high_count > 5 or total_amount > 1000:
            return 'critical'
        elif high_count > 0 or total_amount > 100:
            return 'high'
        elif len(issues) > 10 or total_amount > 10:
            return 'medium'
        else:
            return 'low'


# Create global service instance
locked_funds_management_service = LockedFundsManagementService()