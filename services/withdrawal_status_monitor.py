"""Withdrawal Status Monitor Service for tracking Kraken withdrawals and updating blockchain transaction hashes"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Any

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from database import managed_session
from models import Cashout, CashoutStatus, User
from services.kraken_service import get_kraken_service
from services.withdrawal_notification_service import WithdrawalNotificationService
from services.persistent_job_service import PersistentJobService
from services.withdrawal_error_handler import error_handler, RetryConfig

logger = logging.getLogger(__name__)


class WithdrawalStatusMonitor:
    """Monitor Kraken withdrawals and notify customers when blockchain hashes are available"""
    
    def __init__(self):
        self.kraken_service = get_kraken_service()  # Use singleton to avoid duplicate initialization
        self.notification_service = WithdrawalNotificationService()
        self.max_monitoring_days = 7  # Stop monitoring after 7 days
        self.check_interval_minutes = 2  # Check every 2 minutes
        
    async def initialize_monitoring(self):
        """Initialize the monitoring job with PersistentJobService"""
        job_service = PersistentJobService()
        
        # Register the handler function
        job_service.job_handlers["monitor_kraken_withdrawals"] = self.check_pending_withdrawals
        
        # Schedule recurring job every 2 minutes
        job_service.schedule_recurring_job(
            job_type="monitor_kraken_withdrawals",
            schedule_expression=f"*/{self.check_interval_minutes} * * * *"  # Every 2 minutes
        )
        
        logger.info("Withdrawal status monitoring initialized - checking every 2 minutes")
    
    async def check_pending_withdrawals(self) -> Dict[str, Any]:
        """Check status of all pending Kraken withdrawals and update with blockchain hashes"""
        try:
            with managed_session() as session:
                # Find cashouts that need monitoring:
                # 1. Have Kraken withdrawal ID
                # 2. Don't have blockchain tx hash yet
                # 3. Are in EXECUTING or COMPLETED status
                # 4. Haven't been notified yet
                # 5. Were created within the last 7 days
                cutoff_date = datetime.utcnow() - timedelta(days=self.max_monitoring_days)
                
                pending_cashouts = session.query(Cashout).filter(
                    and_(
                        Cashout.kraken_withdrawal_id.isnot(None),  # Has Kraken ID
                        Cashout.blockchain_tx_hash.is_(None),      # No blockchain hash yet
                        Cashout.tx_hash_notification_sent == False,  # Not notified yet
                        or_(
                            Cashout.status == CashoutStatus.EXECUTING.value,
                            Cashout.status == CashoutStatus.AWAITING_RESPONSE.value,
                            Cashout.status == CashoutStatus.SUCCESS.value,
                            Cashout.status == CashoutStatus.COMPLETED.value  # Legacy compatibility
                        ),
                        Cashout.created_at >= cutoff_date  # Within monitoring window
                    )
                ).all()
                
                logger.info(f"Found {len(pending_cashouts)} cashouts to monitor for blockchain hashes")
                
                results = {
                    'checked': len(pending_cashouts),
                    'updated': 0,
                    'notified': 0,
                    'errors': 0,
                    'details': []
                }
                
                for cashout in pending_cashouts:
                    try:
                        result = await self._check_individual_withdrawal(session, cashout)
                        results['details'].append(result)
                        
                        if result['status'] == 'updated':
                            results['updated'] += 1
                        elif result['status'] == 'notified':
                            results['notified'] += 1
                        elif result['status'] == 'error':
                            results['errors'] += 1
                            
                    except Exception as e:
                        logger.error(f"Error checking cashout {cashout.cashout_id}: {str(e)}")
                        results['errors'] += 1
                        results['details'].append({
                            'cashout_id': cashout.cashout_id,
                            'status': 'error',
                            'message': str(e)
                        })
                
                session.commit()
                
                if results['updated'] > 0 or results['notified'] > 0:
                    logger.info(f"Monitoring cycle complete: {results['updated']} updated, {results['notified']} notified")
                
                return results
                
        except Exception as e:
            logger.error(f"Error in withdrawal monitoring cycle: {str(e)}")
            return {'error': str(e)}
    
    async def _check_individual_withdrawal(self, session: Session, cashout: Cashout) -> Dict[str, Any]:
        """Check status of individual withdrawal and update if completed"""
        try:
            # Check withdrawal status with Kraken using error handling
            async def kraken_check_operation():
                return await self.kraken_service.get_withdrawal_status(
                    asset=cashout.currency
                )
            
            result = await error_handler.with_retry_and_circuit_breaker(
                operation=kraken_check_operation,
                service_name='kraken',
                retry_config=RetryConfig(max_retries=2, base_delay=2.0),
                error_context={'cashout_id': cashout.cashout_id, 'kraken_id': cashout.kraken_withdrawal_id}
            )
            
            if not result.get('success'):
                return {
                    'cashout_id': cashout.cashout_id,
                    'status': 'api_error',
                    'message': f"Kraken API error: {result.get('error')}"
                }
            
            withdrawal_data = result.get('result')
            
            if not withdrawal_data:
                return {
                    'cashout_id': cashout.cashout_id,
                    'status': 'no_status',
                    'message': 'No status returned from Kraken'
                }
            
            # Parse Kraken withdrawal status response to find our specific withdrawal
            withdrawal_status = None
            
            # Handle both list and dict responses from Kraken API
            if isinstance(withdrawal_data, dict):
                # Dictionary format: {withdrawal_id: {status: ..., txid: ...}}
                for ref_id, details in withdrawal_data.items():
                    if ref_id == cashout.kraken_withdrawal_id:
                        withdrawal_status = details
                        break
            elif isinstance(withdrawal_data, list):
                # List format: [{refid: withdrawal_id, status: ..., txid: ...}]
                for withdrawal in withdrawal_data:
                    if withdrawal.get('refid') == cashout.kraken_withdrawal_id:
                        withdrawal_status = withdrawal
                        break
            else:
                logger.error(f"Unexpected Kraken API response format: {type(withdrawal_data)}")
                return {
                    'cashout_id': cashout.cashout_id,
                    'status': 'format_error',
                    'message': f'Unexpected API response format: {type(withdrawal_data)}'
                }
            
            if not withdrawal_status:
                return {
                    'cashout_id': cashout.cashout_id,
                    'status': 'not_found',
                    'message': f'Withdrawal {cashout.kraken_withdrawal_id} not found in Kraken response'
                }
            
            kraken_status = withdrawal_status.get('status', '').lower()
            onchain_id = withdrawal_status.get('txid')  # Kraken's actual field for blockchain hash
            
            logger.debug(f"Cashout {cashout.cashout_id}: Kraken status={kraken_status}, onchain={onchain_id[:8] if onchain_id else 'None'}...")
            
            # If withdrawal is complete and has onchain ID
            # Kraken status values: 'success' (completed), 'pending', 'cancel', 'failure' 
            if kraken_status in ['complete', 'completed', 'success'] and onchain_id:
                # Update database with blockchain transaction hash
                cashout.blockchain_tx_hash = onchain_id
                cashout.status = CashoutStatus.SUCCESS.value
                cashout.completed_at = datetime.utcnow()
                
                # Update legacy field for compatibility
                if not cashout.blockchain_tx_id:
                    cashout.blockchain_tx_id = onchain_id
                
                # VERIFICATION FIX: Mark crypto address as verified after blockchain confirmation
                from services.kraken_withdrawal_service import mark_crypto_address_verified
                await mark_crypto_address_verified(cashout.user_id, cashout.destination, session)
                
                # CRITICAL FIX: Consume frozen hold for successful crypto cashouts when blockchain confirms
                try:
                    from services.crypto import CashoutHoldService
                    
                    # Extract hold metadata from cashout
                    metadata = cashout.cashout_metadata or {}
                    hold_transaction_id = metadata.get('hold_transaction_id')
                    hold_amount = metadata.get('hold_amount')
                    currency = metadata.get('currency', 'USD')
                    
                    if hold_transaction_id and hold_amount:
                        logger.critical(f"🔓 CONSUMING_CRYPTO_HOLD: Blockchain-confirmed cashout {cashout.cashout_id} - Consuming ${hold_amount} {currency} hold (txn: {hold_transaction_id})")
                        
                        # Consume (not release) the frozen hold
                        consume_result = CashoutHoldService.consume_cashout_hold(
                            user_id=cashout.user_id,
                            amount=float(hold_amount),
                            currency=currency,
                            cashout_id=cashout.cashout_id,
                            hold_transaction_id=hold_transaction_id,
                            session=session
                        )
                        
                        if consume_result.get("success"):
                            logger.critical(f"✅ BLOCKCHAIN_HOLD_CONSUMED: Successfully consumed ${hold_amount} {currency} frozen hold for blockchain-confirmed cashout {cashout.cashout_id}")
                        else:
                            logger.error(f"❌ BLOCKCHAIN_HOLD_CONSUME_FAILED: Failed to consume frozen hold for blockchain-confirmed cashout {cashout.cashout_id}")
                    else:
                        logger.warning(f"⚠️ NO_HOLD_METADATA: Blockchain-confirmed cashout {cashout.cashout_id} completed but no hold metadata found")
                        
                except Exception as hold_error:
                    logger.error(f"❌ BLOCKCHAIN_HOLD_CONSUMPTION_ERROR: Error consuming frozen hold for blockchain-confirmed cashout {cashout.cashout_id}: {hold_error}", exc_info=True)
                
                logger.info(f"Updated cashout {cashout.cashout_id} with blockchain hash: {onchain_id[:8]}...")
                
                # Send notification to customer with error handling
                await self._send_completion_notification(session, cashout)
                
                return {
                    'cashout_id': cashout.cashout_id,
                    'status': 'updated',
                    'blockchain_hash': onchain_id[:8] + '...',
                    'kraken_status': kraken_status
                }
                
            # If withdrawal failed
            elif kraken_status in ['failed', 'cancelled', 'rejected']:
                cashout.status = CashoutStatus.FAILED.value
                cashout.failed_at = datetime.utcnow()
                cashout.error_message = f"Kraken withdrawal failed: {kraken_status}"
                
                logger.warning(f"Cashout {cashout.cashout_id} failed in Kraken: {kraken_status}")
                
                return {
                    'cashout_id': cashout.cashout_id,
                    'status': 'failed',
                    'kraken_status': kraken_status
                }
            
            # Still pending
            else:
                return {
                    'cashout_id': cashout.cashout_id,
                    'status': 'pending',
                    'kraken_status': kraken_status,
                    'has_onchain': bool(onchain_id)
                }
                
        except Exception as e:
            logger.error(f"Error checking individual withdrawal {cashout.cashout_id}: {str(e)}")
            raise
    
    async def _send_completion_notification(self, session: Session, cashout: Cashout):
        """Send completion notification to customer with error handling"""
        try:
            # Get user details
            user = session.query(User).filter(User.id == cashout.user_id).first()
            if not user:
                logger.error(f"User not found for cashout {cashout.cashout_id}")
                return
            
            # Send notification with error handling and retries
            async def notification_operation():
                return await self.notification_service.send_withdrawal_completion_notification(
                    user_id=user.telegram_id,
                    cashout_id=cashout.cashout_id,
                    amount=float(cashout.amount),
                    currency=cashout.currency,
                    blockchain_hash=cashout.blockchain_tx_hash,
                    user_email=user.email if user.email else None
                )
            
            result = await error_handler.with_retry_and_circuit_breaker(
                operation=notification_operation,
                service_name='telegram',
                retry_config=RetryConfig(max_retries=3, base_delay=1.0),
                error_context={'cashout_id': cashout.cashout_id, 'user_id': user.telegram_id}
            )
            
            if result.get('success') and result.get('result'):
                # Mark as notified to prevent duplicates
                cashout.tx_hash_notification_sent = True
                logger.info(f"Sent completion notification for cashout {cashout.cashout_id}")
            else:
                logger.error(f"Failed to send completion notification for cashout {cashout.cashout_id}: {result.get('error')}")
                
        except Exception as e:
            logger.error(f"Error sending completion notification for {cashout.cashout_id}: {str(e)}")
    
    async def check_specific_withdrawal(self, cashout_id: str) -> Dict[str, Any]:
        """Manually check status of a specific withdrawal (for admin use)"""
        try:
            with managed_session() as session:
                cashout = session.query(Cashout).filter(
                    Cashout.cashout_id == cashout_id
                ).first()
                
                if not cashout:
                    return {'error': f'Cashout {cashout_id} not found'}
                
                if not cashout.kraken_withdrawal_id:
                    return {'error': f'Cashout {cashout_id} has no Kraken withdrawal ID'}
                
                result = await self._check_individual_withdrawal(session, cashout)
                session.commit()
                
                return {
                    'cashout_id': cashout_id,
                    'result': result,
                    'current_status': cashout.status,
                    'blockchain_hash': cashout.blockchain_tx_hash
                }
                
        except Exception as e:
            logger.error(f"Error checking specific withdrawal {cashout_id}: {str(e)}")
            return {'error': str(e)}
    
    async def get_monitoring_stats(self) -> Dict[str, Any]:
        """Get statistics about withdrawal monitoring"""
        try:
            with managed_session() as session:
                # Count cashouts in various states
                total_with_kraken_id = session.query(Cashout).filter(
                    Cashout.kraken_withdrawal_id.isnot(None)
                ).count()
                
                pending_monitoring = session.query(Cashout).filter(
                    and_(
                        Cashout.kraken_withdrawal_id.isnot(None),
                        Cashout.blockchain_tx_hash.is_(None),
                        Cashout.tx_hash_notification_sent == False,
                        or_(
                            Cashout.status == CashoutStatus.EXECUTING.value,
                            Cashout.status == CashoutStatus.AWAITING_RESPONSE.value,
                            Cashout.status == CashoutStatus.SUCCESS.value,
                            Cashout.status == CashoutStatus.COMPLETED.value  # Legacy compatibility
                        )
                    )
                ).count()
                
                completed_with_hash = session.query(Cashout).filter(
                    and_(
                        Cashout.kraken_withdrawal_id.isnot(None),
                        Cashout.blockchain_tx_hash.isnot(None)
                    )
                ).count()
                
                notified = session.query(Cashout).filter(
                    Cashout.tx_hash_notification_sent == True
                ).count()
                
                return {
                    'total_with_kraken_id': total_with_kraken_id,
                    'pending_monitoring': pending_monitoring,
                    'completed_with_hash': completed_with_hash,
                    'notified': notified,
                    'monitoring_interval_minutes': self.check_interval_minutes,
                    'max_monitoring_days': self.max_monitoring_days
                }
                
        except Exception as e:
            logger.error(f"Error getting monitoring stats: {str(e)}")
            return {'error': str(e)}


# Global instance for use across the application
withdrawal_monitor = WithdrawalStatusMonitor()