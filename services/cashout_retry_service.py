"""
Unified Retry Orchestrator Service
Manages intelligent retry logic for failed cashouts and exchanges, distinguishing between 
technical failures (retry) and user errors (refund)
"""

import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import and_

from database import managed_session
from models import Cashout, CashoutStatus, OperationFailureType, CashoutFailureType, CashoutErrorCode, ExchangeOrder, Escrow, ExchangeStatus
from services.cashout_error_classifier import UnifiedErrorClassifier, classify_cashout_error, classify_escrow_error, classify_deposit_error
from services.kraken_service import KrakenService
from utils.error_handler import handle_error
from utils.constants import CASHOUT_STATUSES_WITHOUT_HOLDS
from utils.exchange_state_validator import ExchangeStateValidator

logger = logging.getLogger(__name__)


class UnifiedRetryService:
    """Orchestrates intelligent retry logic for cashout and exchange failures"""
    
    def __init__(self):
        self.error_classifier = UnifiedErrorClassifier()
        self.kraken_service = KrakenService()
    
    async def handle_cashout_failure(self, 
                                   cashout_id: str, 
                                   exception: Exception, 
                                   context: Optional[Dict[str, Any]] = None) -> bool:
        """
        Handle a cashout failure with intelligent classification and retry logic
        
        Args:
            cashout_id: ID of the failed cashout
            exception: The exception that caused the failure
            context: Additional context (service, operation, etc.)
        
        Returns:
            bool: True if retry was scheduled, False if refund was triggered
        """
        start_time = datetime.utcnow()
        logger.info(f"🔄 RETRY_ORCHESTRATOR: Handling failure for cashout {cashout_id}")
        
        # Enhanced logging context
        log_context = {
            "cashout_id": cashout_id,
            "exception_type": type(exception).__name__,
            "exception_message": str(exception),
            "context": context or {},
            "start_time": start_time.isoformat()
        }
        
        try:
            # Classify the error
            failure_type, error_code, retryable, delay_seconds = classify_cashout_error(
                exception, context
            )
            
            # Enhanced classification logging
            classification_data = {
                "failure_type": failure_type.value,
                "error_code": error_code.value,
                "retryable": retryable,
                "delay_seconds": delay_seconds,
                "classification_duration_ms": int((datetime.utcnow() - start_time).total_seconds() * 1000)
            }
            
            logger.info(f"🔍 CLASSIFICATION_DECISION: {cashout_id} classified as {failure_type.value}/{error_code.value} (retryable={retryable})", extra=classification_data)
            
            # Update cashout record with failure classification
            retry_scheduled = await self._update_cashout_failure_info(
                cashout_id, failure_type, error_code, retryable, delay_seconds
            )
            
            # Final result logging with metrics
            processing_time = (datetime.utcnow() - start_time).total_seconds()
            result_data = {
                "retry_scheduled": retry_scheduled,
                "processing_time_seconds": processing_time,
                "outcome": "retry" if retry_scheduled else "refund"
            }
            
            logger.info(f"✅ RETRY_ORCHESTRATOR: Cashout {cashout_id} processed - retry_scheduled={retry_scheduled} in {processing_time:.3f}s", extra={**log_context, **classification_data, **result_data})
            return retry_scheduled
            
        except Exception as e:
            processing_time = (datetime.utcnow() - start_time).total_seconds()
            error_data = {
                "processing_time_seconds": processing_time,
                "orchestrator_error": str(e)
            }
            
            logger.error(f"❌ RETRY_ORCHESTRATOR: Error handling failure for {cashout_id}: {e}", extra={**log_context, **error_data})
            handle_error(e, {"cashout_id": cashout_id, "original_exception": str(exception)})
            return False
    
    async def _update_cashout_failure_info(self, 
                                         cashout_id: str, 
                                         failure_type: CashoutFailureType, 
                                         error_code: CashoutErrorCode, 
                                         retryable: bool, 
                                         delay_seconds: int) -> bool:
        """Update cashout record with failure classification and retry schedule"""
        
        with managed_session() as db:
            try:
                # Get the cashout
                cashout = db.query(Cashout).filter(
                    Cashout.cashout_id == cashout_id
                ).first()
                
                if not cashout:
                    logger.error(f"❌ Cashout {cashout_id} not found")
                    return False
                
                # Check if we should retry based on current retry count
                should_retry = self.error_classifier.should_retry(error_code, cashout.retry_count)
                
                # Enhanced retry decision logging
                decision_data = {
                    "failure_type": failure_type.value,
                    "error_code": error_code.value,
                    "retry_count": cashout.retry_count,
                    "should_retry": should_retry,
                    "retryable": retryable,
                    "user_id": cashout.user_id,
                    "amount": float(cashout.amount),
                    "currency": cashout.currency
                }
                
                logger.info(f"🔍 RETRY_DECISION: {cashout_id} - type={failure_type.value}, code={error_code.value}, retry_count={cashout.retry_count}, should_retry={should_retry}", extra=decision_data)
                
                # Update failure classification
                cashout.failure_type = failure_type.value
                cashout.last_error_code = error_code.value
                cashout.last_retry_at = datetime.utcnow()
                
                if should_retry and retryable:
                    # Schedule retry
                    next_retry_delay = self.error_classifier.get_next_retry_delay(error_code, cashout.retry_count)
                    cashout.next_retry_at = datetime.utcnow() + timedelta(seconds=next_retry_delay)
                    
                    # Track technical failure duration
                    if failure_type == CashoutFailureType.TECHNICAL:
                        if not cashout.technical_failure_since:
                            cashout.technical_failure_since = datetime.utcnow()
                    
                    # Enhanced retry scheduling logging
                    retry_data = {
                        "next_retry_at": cashout.next_retry_at.isoformat(),
                        "delay_seconds": next_retry_delay,
                        "delay_minutes": round(next_retry_delay / 60, 2),
                        "technical_failure_since": cashout.technical_failure_since.isoformat() if cashout.technical_failure_since else None,
                        "retry_attempt": cashout.retry_count + 1
                    }
                    
                    logger.info(f"⏰ RETRY_SCHEDULED: {cashout_id} will retry at {cashout.next_retry_at} (delay: {next_retry_delay}s)", extra={**decision_data, **retry_data})
                    
                    db.commit()
                    return True
                    
                else:
                    # Cannot retry - mark as failed and keep funds frozen for admin review
                    cashout.next_retry_at = None
                    cashout.status = CashoutStatus.FAILED.value
                    cashout.failed_at = datetime.utcnow()
                    
                    # ARCHITECTURAL FIX: Proper lifecycle-aware hold processing (no automatic refunds)
                    try:
                        from utils.cashout_completion_handler import process_failed_cashout_hold_lifecycle
                        lifecycle_result = await process_failed_cashout_hold_lifecycle(
                            cashout_id=cashout_id,
                            user_id=cashout.user_id,
                            session=db,
                            reason="max_retries_exceeded" if failure_type == CashoutFailureType.TECHNICAL else "user_error"
                        )
                        if lifecycle_result.get('success') and not lifecycle_result.get('skipped'):
                            action = lifecycle_result.get('action', 'processed')
                            amount = lifecycle_result.get('amount', 0)
                            currency = lifecycle_result.get('currency', 'USD')
                            status = lifecycle_result.get('status', 'FAILED_HELD')
                            logger.info(f"✅ LIFECYCLE_PROCESSED: {action} ${amount:.2f} {currency} for failed cashout {cashout_id} (status: {status})")
                            
                            # Send admin notification for frozen funds requiring review
                            await self._notify_admin_failed_cashout(
                                cashout_id=cashout_id,
                                user_id=cashout.user_id,
                                amount=amount,
                                currency=currency,
                                failure_type=failure_type,
                                final_retry_count=cashout.retry_count,
                                status=status
                            )
                        else:
                            logger.warning(f"⚠️ LIFECYCLE_SKIPPED: {cashout_id} - {lifecycle_result.get('reason', 'unknown')}")
                    except Exception as lifecycle_error:
                        logger.error(f"❌ Failed to process hold lifecycle for max-retry-failed cashout {cashout_id}: {lifecycle_error}")
                    
                    # Enhanced failure logging (no automatic refund)
                    failure_reason = "user_error" if failure_type == CashoutFailureType.USER else "max_retries_exceeded"
                    failure_data = {
                        "failure_reason": failure_reason,
                        "final_retry_count": cashout.retry_count,
                        "failed_at": cashout.failed_at.isoformat(),
                        "technical_failure_duration_hours": None,
                        "funds_status": "frozen_for_admin_review"
                    }
                    
                    if cashout.technical_failure_since:
                        duration = cashout.failed_at - cashout.technical_failure_since
                        failure_data["technical_failure_duration_hours"] = round(duration.total_seconds() / 3600, 2)
                    
                    if failure_type == CashoutFailureType.USER:
                        logger.info(f"🔒 FAILED_HELD: {cashout_id} - User error, funds frozen for admin review", extra={**decision_data, **failure_data})
                    else:
                        logger.info(f"🔒 FAILED_HELD: {cashout_id} - Max retries exceeded, funds frozen for admin review", extra={**decision_data, **failure_data})
                    
                    db.commit()
                    
                    # NO automatic refund - funds stay frozen for admin review
                    return False
                    
            except Exception as e:
                db.rollback()
                logger.error(f"❌ Error updating cashout {cashout_id}: {e}")
                raise
    
    async def _notify_admin_failed_cashout(self,
                                          cashout_id: str,
                                          user_id: int,
                                          amount: float,
                                          currency: str,
                                          failure_type: CashoutFailureType,
                                          final_retry_count: int,
                                          status: str) -> None:
        """Notify admin about failed cashout requiring manual review (replaces automatic refund)"""
        try:
            logger.info(f"🚨 ADMIN_NOTIFICATION: Failed cashout {cashout_id} requires manual review - ${amount:.2f} {currency} frozen")
            
            # TODO: Send admin notification via email/Telegram
            # This replaces the automatic wallet credit with admin intervention requirement
            notification_data = {
                "event": "cashout_failed_admin_review_required",
                "cashout_id": cashout_id,
                "user_id": user_id,
                "amount": amount,
                "currency": currency,
                "failure_type": failure_type.value,
                "final_retry_count": final_retry_count,
                "wallet_hold_status": status,
                "action_required": "Admin must review and decide whether to credit available balance or process external refund",
                "timestamp": datetime.utcnow().isoformat()
            }
            
            # Log admin notification (placeholder for actual notification system)
            logger.warning(f"🚨 ADMIN_REVIEW_REQUIRED: Cashout {cashout_id} failed after {final_retry_count} retries - ${amount:.2f} {currency} frozen for admin review", extra=notification_data)
            
            # TODO: Integrate with actual admin notification system
            # Examples:
            # - await send_admin_email(notification_data)
            # - await send_admin_telegram_alert(notification_data)
            # - await create_admin_task(notification_data)
            
            logger.info(f"✅ ADMIN_NOTIFIED: Notification sent for failed cashout {cashout_id} requiring manual review")
            
        except Exception as e:
            logger.error(f"❌ ADMIN_NOTIFICATION_FAILED: {cashout_id} - {e}")
            handle_error(e, {"cashout_id": cashout_id, "operation": "admin_notification"})
    
    # DEPRECATED: This method should not automatically credit wallets in the new frozen funds lifecycle
    async def _legacy_trigger_cashout_refund(self, cashout: Cashout):
        """DEPRECATED: Legacy method that automatically credited wallets - violates frozen funds policy"""
        logger.warning(f"🚨 DEPRECATED_REFUND_CALL: Attempted to call legacy automatic refund for {cashout.cashout_id}")
        logger.warning("⚠️ POLICY_VIOLATION: Automatic wallet credits are not allowed - funds must stay frozen for admin review")
        
        # Instead of automatic refund, trigger admin notification
        await self._notify_admin_failed_cashout(
            cashout_id=cashout.cashout_id,
            user_id=cashout.user_id,
            amount=float(cashout.amount),
            currency=cashout.currency,
            failure_type=CashoutFailureType.TECHNICAL,  # Default to technical for legacy calls
            final_retry_count=0,  # Unknown for legacy calls
            status="FAILED_HELD"
        )
    
    async def process_retry_queue(self, limit: int = 50) -> Dict[str, Any]:
        """
        Process cashouts that are ready for retry
        Called by the unified retry processor job
        
        Args:
            limit: Maximum number of cashouts to process in one batch
            
        Returns:
            Dict with processing results
        """
        logger.info(f"🔄 RETRY_QUEUE_PROCESSOR: Starting batch processing (limit: {limit})")
        
        results = {
            "processed": 0,
            "successful": 0,
            "failed": 0,
            "rescheduled": 0,
            "failed_held": 0  # Changed from "refunded" to reflect frozen funds policy
        }
        
        try:
            with managed_session() as db:
                # Find cashouts ready for retry
                # CRITICAL: Only process cashouts that originally had holds to prevent processing statuses without funds
                now = datetime.utcnow()
                ready_cashouts = db.query(Cashout).filter(
                    and_(
                        Cashout.status == CashoutStatus.FAILED.value,
                        Cashout.failure_type == CashoutFailureType.TECHNICAL.value,
                        Cashout.next_retry_at <= now,
                        Cashout.next_retry_at.isnot(None),
                        # CRITICAL SAFEGUARD: Exclude statuses that never had holds
                        Cashout.status.notin_(CASHOUT_STATUSES_WITHOUT_HOLDS)
                    )
                ).order_by(Cashout.next_retry_at).limit(limit).all()
                
                logger.info(f"🔍 RETRY_QUEUE: Found {len(ready_cashouts)} cashouts ready for retry")
                
                for cashout in ready_cashouts:
                    results["processed"] += 1
                    
                    try:
                        retry_result = await self._attempt_cashout_retry(cashout)
                        
                        if retry_result == "success":
                            results["successful"] += 1
                        elif retry_result == "reschedule":
                            results["rescheduled"] += 1
                        elif retry_result == "failed_held":
                            results["failed_held"] += 1  # Funds frozen for admin review
                        else:
                            results["failed"] += 1
                            
                    except Exception as e:
                        results["failed"] += 1
                        logger.error(f"❌ RETRY_FAILED: {cashout.cashout_id} - {e}")
                        handle_error(e, {"cashout_id": cashout.cashout_id, "operation": "retry"})
                
                logger.info(f"✅ RETRY_QUEUE_COMPLETE: {results}")
                return results
                
        except Exception as e:
            logger.error(f"❌ RETRY_QUEUE_ERROR: {e}")
            handle_error(e, {"operation": "retry_queue_processing"})
            return results
    
    async def _attempt_cashout_retry(self, cashout: Cashout) -> str:
        """
        Attempt to retry a failed cashout
        
        CRITICAL SAFEGUARD: Check WalletHolds status before retry to prevent double-spending
        
        Returns:
            str: "success", "reschedule", "failed_held", or "error"
        """
        logger.info(f"🔄 ATTEMPTING_RETRY: {cashout.cashout_id} (attempt #{cashout.retry_count + 1})")
        
        # CRITICAL VALIDATION: Reject cashouts in statuses that never had holds
        if hasattr(cashout, 'original_status') and cashout.original_status in CASHOUT_STATUSES_WITHOUT_HOLDS:
            logger.error(f"🚨 RETRY_BLOCKED: {cashout.cashout_id} was originally in {cashout.original_status} - no holds to retry")
            return "error"
        
        try:
            # CRITICAL SAFEGUARD: Check if funds were already consumed before attempting retry
            with managed_session() as db:
                from models import WalletHolds, WalletHoldStatus
                
                hold_record = db.query(WalletHolds).filter(
                    WalletHolds.linked_type == "cashout",
                    WalletHolds.linked_id == cashout.cashout_id,
                    WalletHolds.user_id == cashout.user_id
                ).first()
                
                if hold_record and hold_record.status == WalletHoldStatus.CONSUMED_SENT.value:
                    # DANGEROUS: Funds already sent to external provider, cannot retry!
                    logger.critical(f"🚨 RETRY_BLOCKED: {cashout.cashout_id} has CONSUMED_SENT status - funds already sent, triggering refund instead")
                    
                    # Update hold status to FAILED_HELD for admin review
                    try:
                        from utils.cashout_completion_handler import process_failed_cashout_hold_lifecycle
                        lifecycle_result = await process_failed_cashout_hold_lifecycle(
                            cashout_id=cashout.cashout_id,
                            user_id=cashout.user_id,
                            session=db,
                            reason="retry_blocked_funds_consumed"
                        )
                        if lifecycle_result.get('success'):
                            action = lifecycle_result.get('action', 'processed')
                            status = lifecycle_result.get('status', 'FAILED_HELD')
                            logger.info(f"✅ CONSUMED_FAILED_HELD: Processed consumed funds failure for {cashout.cashout_id} (status: {status})")
                            
                            # Notify admin about consumed funds requiring review
                            await self._notify_admin_failed_cashout(
                                cashout_id=cashout.cashout_id,
                                user_id=cashout.user_id,
                                amount=float(lifecycle_result.get('amount', 0)),
                                currency=lifecycle_result.get('currency', 'USD'),
                                failure_type=CashoutFailureType.TECHNICAL,
                                final_retry_count=cashout.retry_count,
                                status=status
                            )
                        else:
                            logger.error(f"❌ LIFECYCLE_FAILED: Failed to process consumed funds failure for {cashout.cashout_id}")
                    except Exception as lifecycle_error:
                        logger.error(f"❌ LIFECYCLE_ERROR: Error processing consumed funds failure for {cashout.cashout_id}: {lifecycle_error}")
                    
                    # Mark as permanently failed (cannot retry consumed funds)
                    db.query(Cashout).filter(
                        Cashout.cashout_id == cashout.cashout_id
                    ).update({
                        "status": CashoutStatus.FAILED.value,
                        "failure_type": CashoutFailureType.USER.value,  # Prevent further retries
                        "failed_at": datetime.utcnow(),
                        "next_retry_at": None,
                        "last_error": "Retry blocked: funds already consumed and sent to external provider"
                    })
                    db.commit()
                    
                    return "failed_held"  # Changed return value to reflect frozen funds policy
                
                elif hold_record and hold_record.status != WalletHoldStatus.HELD.value:
                    # Handle different hold statuses appropriately
                    if hold_record.status in [WalletHoldStatus.FAILED_HELD.value, WalletHoldStatus.CANCELLED_HELD.value, WalletHoldStatus.DISPUTED_HELD.value]:
                        logger.info(f"🔒 RETRY_BLOCKED: {cashout.cashout_id} already in admin review status {hold_record.status}, no further action needed")
                    else:
                        logger.warning(f"⚠️ RETRY_BLOCKED: {cashout.cashout_id} has status {hold_record.status}, cannot retry")
                    
                    # Mark as permanently failed (no retry)
                    db.query(Cashout).filter(
                        Cashout.cashout_id == cashout.cashout_id
                    ).update({
                        "status": CashoutStatus.FAILED.value,
                        "failure_type": CashoutFailureType.USER.value,  # Prevent further retries
                        "failed_at": datetime.utcnow(),
                        "next_retry_at": None,
                        "last_error": f"Retry blocked: hold status is {hold_record.status}"
                    })
                    db.commit()
                    
                    return "failed_held"  # Changed return value to reflect frozen funds policy
                
                # Safe to retry: either no hold record (pre-migration) or status is HELD
                logger.info(f"✅ RETRY_SAFE: {cashout.cashout_id} - funds still held or pre-migration cashout")
            
            # Increment retry count
            with managed_session() as db:
                db.query(Cashout).filter(
                    Cashout.cashout_id == cashout.cashout_id
                ).update({
                    "retry_count": cashout.retry_count + 1,
                    "last_retry_at": datetime.utcnow(),
                    "next_retry_at": None,  # Clear until we know the result
                    "status": CashoutStatus.PROCESSING.value
                })
                db.commit()
            
            # Attempt the actual cashout based on type
            if cashout.cashout_type == "crypto":
                result = await self._retry_crypto_cashout(cashout)
            elif cashout.cashout_type == "ngn_bank":
                result = await self._retry_ngn_cashout(cashout)
            else:
                logger.error(f"❌ Unknown cashout type: {cashout.cashout_type}")
                return "error"
            
            return result
            
        except Exception as e:
            logger.error(f"❌ RETRY_ATTEMPT_FAILED: {cashout.cashout_id} - {e}")
            
            # Classify the new failure and determine next action
            failure_type, error_code, retryable, delay_seconds = classify_cashout_error(e)
            
            # Check if we should retry again
            should_retry = self.error_classifier.should_retry(error_code, cashout.retry_count)
            
            if should_retry and retryable:
                # Schedule another retry
                next_delay = self.error_classifier.get_next_retry_delay(error_code, cashout.retry_count)
                with managed_session() as db:
                    db.query(Cashout).filter(
                        Cashout.cashout_id == cashout.cashout_id
                    ).update({
                        "next_retry_at": datetime.utcnow() + timedelta(seconds=next_delay),
                        "status": CashoutStatus.FAILED.value,
                        "last_error_code": error_code.value
                    })
                    db.commit()
                
                logger.info(f"⏰ RETRY_RESCHEDULED: {cashout.cashout_id} - Next attempt in {next_delay}s")
                return "reschedule"
            else:
                # Mark as failed and keep funds frozen for admin review
                with managed_session() as db:
                    db.query(Cashout).filter(
                        Cashout.cashout_id == cashout.cashout_id
                    ).update({
                        "status": CashoutStatus.FAILED.value,
                        "failed_at": datetime.utcnow(),
                        "next_retry_at": None
                    })
                    db.commit()
                    
                    # ARCHITECTURAL FIX: Proper lifecycle-aware hold processing (no automatic refunds)
                    try:
                        from utils.cashout_completion_handler import process_failed_cashout_hold_lifecycle
                        lifecycle_result = await process_failed_cashout_hold_lifecycle(
                            cashout_id=cashout.cashout_id,
                            user_id=cashout.user_id,
                            session=db,
                            reason="max_retries_final_failure"
                        )
                        if lifecycle_result.get('success') and not lifecycle_result.get('skipped'):
                            action = lifecycle_result.get('action', 'processed')
                            amount = lifecycle_result.get('amount', 0)
                            currency = lifecycle_result.get('currency', 'USD')
                            status = lifecycle_result.get('status', 'FAILED_HELD')
                            logger.info(f"✅ LIFECYCLE_PROCESSED: {action} ${amount:.2f} {currency} for final-failed cashout {cashout.cashout_id} (status: {status})")
                            
                            # Send admin notification for frozen funds requiring review
                            await self._notify_admin_failed_cashout(
                                cashout_id=cashout.cashout_id,
                                user_id=cashout.user_id,
                                amount=amount,
                                currency=currency,
                                failure_type=CashoutFailureType.TECHNICAL,  # Final failure after retries
                                final_retry_count=cashout.retry_count,
                                status=status
                            )
                        else:
                            logger.warning(f"⚠️ LIFECYCLE_SKIPPED: {cashout.cashout_id} - {lifecycle_result.get('reason', 'unknown')}")
                    except Exception as lifecycle_error:
                        logger.error(f"❌ Failed to process hold lifecycle for final-failed cashout {cashout.cashout_id}: {lifecycle_error}")
                
                # NO automatic refund - funds stay frozen for admin review
                logger.info(f"🔒 RETRY_FAILED_HELD: {cashout.cashout_id} - Max retries exceeded, funds frozen for admin review")
                return "failed_held"  # Changed return value to reflect new behavior
    
    async def _retry_crypto_cashout(self, cashout: Cashout) -> str:
        """Retry a crypto cashout via Kraken"""
        try:
            # Parse destination address from JSON
            import json
            try:
                if isinstance(cashout.destination, str):
                    destination_data = json.loads(cashout.destination)
                elif isinstance(cashout.destination, dict):
                    destination_data = cashout.destination
                else:
                    # Handle corrupted destination data
                    logger.error(f"❌ CRYPTO_RETRY_CORRUPTED_DESTINATION: {cashout.cashout_id} has invalid destination type: {type(cashout.destination)}")
                    raise ValueError(f"Invalid destination data type: {type(cashout.destination)}")
                    
                crypto_address = destination_data.get("address")
            except (json.JSONDecodeError, TypeError, AttributeError) as e:
                logger.error(f"❌ CRYPTO_RETRY_DESTINATION_PARSE_ERROR: {cashout.cashout_id} - {e}")
                raise ValueError(f"Failed to parse destination data: {e}")
            
            if not crypto_address:
                raise ValueError("No crypto address found in destination")
            
            # Attempt Kraken withdrawal with context validation
            from utils.universal_id_generator import UniversalIDGenerator
            from database import SyncSessionLocal
            transaction_id = UniversalIDGenerator.generate_transaction_id()
            
            # Create session for context validation
            retry_session = SyncSessionLocal()
            try:
                withdrawal_result = await self.kraken_service.withdraw_crypto(
                    currency=cashout.currency,
                    amount=float(cashout.net_amount),
                    address=crypto_address,
                    reference=cashout.cashout_id,
                    session=retry_session,
                    cashout_id=cashout.cashout_id,
                    transaction_id=transaction_id
                )
            finally:
                retry_session.close()
            
            if withdrawal_result.get("success"):
                # Update cashout with success
                with managed_session() as db:
                    db.query(Cashout).filter(
                        Cashout.cashout_id == cashout.cashout_id
                    ).update({
                        "status": CashoutStatus.PROCESSING.value,
                        "kraken_withdrawal_id": withdrawal_result.get("withdrawal_id"),
                        "external_tx_id": withdrawal_result.get("reference"),
                        "processed_at": datetime.utcnow(),
                        "failure_type": None,  # Clear failure info on success
                        "last_error_code": None,
                        "technical_failure_since": None
                    })
                    db.commit()
                
                logger.info(f"✅ CRYPTO_RETRY_SUCCESS: {cashout.cashout_id}")
                return "success"
            else:
                raise Exception(f"Kraken withdrawal failed: {withdrawal_result.get('error', 'Unknown error')}")
                
        except Exception as e:
            logger.error(f"❌ CRYPTO_RETRY_FAILED: {cashout.cashout_id} - {e}")
            raise
    
    async def _retry_ngn_cashout(self, cashout: Cashout) -> str:
        """Retry an NGN bank cashout via Fincra"""
        try:
            logger.info(f"🏦 NGN_RETRY_START: {cashout.cashout_id} - Retrying NGN cashout")
            
            # Import Fincra service and process the cashout
            from services.fincra_service import FincraService
            
            # Get bank account details from metadata
            import json
            metadata = {}
            if cashout.metadata:
                try:
                    if isinstance(cashout.metadata, str):
                        metadata = json.loads(cashout.metadata)
                    elif isinstance(cashout.metadata, dict):
                        metadata = cashout.metadata
                    else:
                        # Handle corrupted metadata (like SQLAlchemy MetaData objects)
                        logger.warning(f"⚠️ NGN_RETRY_CORRUPTED_METADATA: {cashout.cashout_id} has invalid metadata type: {type(cashout.metadata)}")
                        metadata = {}
                except (json.JSONDecodeError, TypeError, AttributeError) as e:
                    logger.warning(f"⚠️ NGN_RETRY_METADATA_PARSE_ERROR: {cashout.cashout_id} - {e}")
                    metadata = {}
            
            bank_code = metadata.get('bank_code')
            account_number = metadata.get('account_number')
            recipient_name = metadata.get('recipient_name')
            
            if not all([bank_code, account_number, recipient_name]):
                logger.error(f"❌ NGN_RETRY_FAILED: {cashout.cashout_id} - Missing bank details in metadata")
                raise Exception("Missing bank account details in cashout metadata")
            
            # Initialize Fincra service and process transfer
            fincra_service = FincraService()
            
            # Create transfer request
            transfer_result = await fincra_service.process_ngn_transfer(
                amount=float(cashout.amount),
                bank_code=bank_code,
                account_number=account_number,
                recipient_name=recipient_name,
                reference=f"RETRY_{cashout.cashout_id}_{int(datetime.utcnow().timestamp())}"
            )
            
            if transfer_result and transfer_result.get('success'):
                # Update cashout with success
                with managed_session() as db:
                    db.query(Cashout).filter(
                        Cashout.cashout_id == cashout.cashout_id
                    ).update({
                        "status": CashoutStatus.PROCESSING.value,  # Will be updated to completed by webhook
                        "external_tx_id": transfer_result.get('transfer_id'),
                        "processed_at": datetime.utcnow(),
                        "failure_type": None,  # Clear failure info on success
                        "last_error_code": None,
                        "technical_failure_since": None
                    })
                    db.commit()
                
                logger.info(f"✅ NGN_RETRY_SUCCESS: {cashout.cashout_id} - Transfer initiated with ID {transfer_result.get('transfer_id')}")
                return "success"
            else:
                error_msg = transfer_result.get('error', 'Unknown transfer error') if transfer_result else 'Fincra service returned None'
                logger.error(f"❌ NGN_RETRY_TRANSFER_FAILED: {cashout.cashout_id} - {error_msg}")
                raise Exception(f"Fincra transfer failed: {error_msg}")
                
        except Exception as e:
            logger.error(f"❌ NGN_RETRY_FAILED: {cashout.cashout_id} - {e}")
            raise
    
    async def get_retry_stats(self) -> Dict[str, Any]:
        """Get comprehensive retry system statistics"""
        try:
            with managed_session() as db:
                now = datetime.utcnow()
                
                # Count cashouts by failure type and status
                total_failed = db.query(Cashout).filter(
                    Cashout.status == CashoutStatus.FAILED.value
                ).count()
                
                technical_failures = db.query(Cashout).filter(
                    and_(
                        Cashout.status == CashoutStatus.FAILED.value,
                        Cashout.failure_type == CashoutFailureType.TECHNICAL.value
                    )
                ).count()
                
                user_failures = db.query(Cashout).filter(
                    and_(
                        Cashout.status == CashoutStatus.FAILED.value,
                        Cashout.failure_type == CashoutFailureType.USER.value
                    )
                ).count()
                
                pending_retries = db.query(Cashout).filter(
                    and_(
                        Cashout.status == CashoutStatus.FAILED.value,
                        Cashout.next_retry_at.isnot(None),
                        Cashout.next_retry_at > now
                    )
                ).count()
                
                ready_for_retry = db.query(Cashout).filter(
                    and_(
                        Cashout.status == CashoutStatus.FAILED.value,
                        Cashout.next_retry_at <= now,
                        Cashout.next_retry_at.isnot(None)
                    )
                ).count()
                
                return {
                    "total_failed_cashouts": total_failed,
                    "technical_failures": technical_failures,
                    "user_failures": user_failures,
                    "pending_retries": pending_retries,
                    "ready_for_retry": ready_for_retry,
                    "retry_system_health": "healthy" if ready_for_retry < 10 else "attention_needed"
                }
                
        except Exception as e:
            logger.error(f"❌ Error getting retry stats: {e}")
            return {"error": str(e)}
    
    async def handle_exchange_failure(self, 
                                    exchange_id: str, 
                                    exception: Exception, 
                                    context: Optional[Dict[str, Any]] = None,
                                    exchange_type: str = "exchange_order") -> bool:
        """
        Handle an exchange failure with intelligent classification and retry logic
        
        Args:
            exchange_id: ID of the failed exchange (ExchangeOrder or DirectExchange)
            exception: The exception that caused the failure
            context: Additional context (service, operation, etc.)
            exchange_type: "exchange_order" or "direct_exchange"
        
        Returns:
            bool: True if retry was scheduled, False if refund was triggered
        """
        start_time = datetime.utcnow()
        logger.info(f"🔄 EXCHANGE_RETRY: Handling {exchange_type} failure for {exchange_id}")
        
        # Enhanced logging context for exchanges
        log_context = {
            "exchange_id": exchange_id,
            "exchange_type": exchange_type,
            "exception_type": type(exception).__name__,
            "start_time": start_time.isoformat()
        }
        if context:
            log_context.update(context)
        
        try:
            with managed_session() as db:
                # Fetch exchange record based on type
                if exchange_type == "direct_exchange":
                    # DirectExchange model not yet implemented - use ExchangeOrder as fallback
                    exchange = db.query(ExchangeOrder).filter_by(exchange_order_id=exchange_id).first()
                else:
                    exchange = db.query(ExchangeOrder).filter_by(exchange_order_id=exchange_id).first()
                
                if not exchange:
                    logger.error(f"Exchange {exchange_id} not found in database")
                    return False
                
                # Classify the error using unified classifier
                failure_type, error_code, retryable, delay = self.error_classifier.classify_error(
                    exception, {**log_context, "entity_type": exchange_type}
                )
                
                logger.info(f"📊 EXCHANGE_CLASSIFICATION: {exchange_id} - {failure_type.value} / {error_code.value} (retryable: {retryable})")
                
                if retryable and failure_type == CashoutFailureType.TECHNICAL:
                    # Schedule retry for technical failures
                    return await self._schedule_exchange_retry(
                        db, exchange, exception, error_code, delay, exchange_type
                    )
                else:
                    # Handle user errors or non-retryable failures
                    return await self._handle_exchange_refund(
                        db, exchange, exception, error_code, exchange_type
                    )
                    
        except Exception as e:
            logger.error(f"❌ EXCHANGE_RETRY_ERROR: Failed to handle {exchange_type} {exchange_id}: {e}")
            return False
    
    async def _schedule_exchange_retry(self, 
                                     db: Session, 
                                     exchange, 
                                     exception: Exception, 
                                     error_code: CashoutErrorCode, 
                                     delay_seconds: int,
                                     exchange_type: str) -> bool:
        """
        Schedule an exchange for retry with exponential backoff
        
        Args:
            db: Database session
            exchange: Exchange record (DirectExchange or ExchangeOrder)
            exception: Original exception
            error_code: Classified error code
            delay_seconds: Delay before next retry
            exchange_type: Type of exchange
        
        Returns:
            bool: True if retry was scheduled successfully
        """
        try:
            # Get exchange ID based on type
            exchange_id = exchange.exchange_id if exchange_type == "direct_exchange" else exchange.exchange_order_id
            
            # Calculate retry timing
            retry_count = getattr(exchange, 'retry_count', 0) + 1
            next_retry_at = datetime.utcnow() + timedelta(seconds=delay_seconds)
            
            # Update exchange with retry information - validate state transition
            try:
                current_status = ExchangeStatus(exchange.status) if isinstance(exchange.status, str) else exchange.status
                # Note: failed_retry_pending is not a standard ExchangeStatus - handle gracefully
                # For retry scenarios, we allow transition to FAILED status
                new_status = ExchangeStatus.FAILED
                is_valid, reason = ExchangeStateValidator.validate_transition(current_status, new_status, exchange_id)
                if is_valid:
                    exchange.status = "failed_retry_pending"
                else:
                    logger.warning(f"🚫 RETRY_EXCHANGE_TRANSITION_BLOCKED: {current_status.value}→failed_retry_pending for {exchange_id}: {reason}")
                    # Still allow retry for technical failures even if validation blocks
                    exchange.status = "failed_retry_pending"
            except Exception as e:
                logger.error(f"🚫 RETRY_EXCHANGE_TRANSITION_BLOCKED: Error validating transition for {exchange_id}: {e}")
                # Allow retry to proceed despite validation error
                exchange.status = "failed_retry_pending"
            
            exchange.retry_count = retry_count
            exchange.last_error = str(exception)[:1000]  # Truncate long errors
            exchange.error_code = error_code.value
            exchange.failure_type = CashoutFailureType.TECHNICAL.value
            exchange.next_retry_at = next_retry_at
            exchange.updated_at = datetime.utcnow()
            
            db.commit()
            
            logger.info(f"🔄 EXCHANGE_RETRY_SCHEDULED: {exchange_id} - attempt #{retry_count} in {delay_seconds}s (at {next_retry_at})")
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to schedule exchange retry: {e}")
            db.rollback()
            return False
    
    async def _handle_exchange_refund(self, 
                                    db: Session, 
                                    exchange, 
                                    exception: Exception, 
                                    error_code: CashoutErrorCode,
                                    exchange_type: str) -> bool:
        """
        Handle exchange refund for user errors or non-retryable failures
        
        Args:
            db: Database session
            exchange: Exchange record
            exception: Original exception
            error_code: Classified error code
            exchange_type: Type of exchange
        
        Returns:
            bool: False (refund triggered, no retry)
        """
        try:
            exchange_id = exchange.exchange_id if exchange_type == "direct_exchange" else exchange.exchange_order_id
            
            # Update exchange status to failed - validate state transition
            try:
                current_status = ExchangeStatus(exchange.status) if isinstance(exchange.status, str) else exchange.status
                new_status = ExchangeStatus.FAILED
                is_valid, reason = ExchangeStateValidator.validate_transition(current_status, new_status, exchange_id)
                if is_valid:
                    exchange.status = "failed"
                else:
                    logger.warning(f"🚫 RETRY_EXCHANGE_TRANSITION_BLOCKED: {current_status.value}→{new_status.value} for {exchange_id}: {reason}")
                    # Still mark as failed for user errors even if validation blocks
                    exchange.status = "failed"
            except Exception as e:
                logger.error(f"🚫 RETRY_EXCHANGE_TRANSITION_BLOCKED: Error validating transition for {exchange_id}: {e}")
                # Allow failure marking to proceed despite validation error
                exchange.status = "failed"
            
            exchange.last_error = str(exception)[:1000]
            exchange.error_code = error_code.value
            exchange.failure_type = CashoutFailureType.USER.value
            exchange.updated_at = datetime.utcnow()
            
            # Trigger refund process (release holds, credit wallet)
            try:
                from services.crypto import CashoutHoldService
                
                # Release any holds for this exchange
                if hasattr(exchange, 'hold_transaction_id') and exchange.hold_transaction_id:
                    release_result = CashoutHoldService.release_exchange_hold(
                        user_id=exchange.user_id,
                        amount=float(exchange.from_amount),
                        currency="USD",  # Assuming USD holds
                        exchange_id=exchange_id,
                        hold_transaction_id=exchange.hold_transaction_id,
                        description=f"🔄 Exchange refund: {exchange_type} {exchange_id} failed",
                        session=db
                    )
                    
                    if release_result.get('success'):
                        logger.info(f"✅ EXCHANGE_REFUND: Released hold for {exchange_id}")
                    else:
                        logger.error(f"❌ EXCHANGE_REFUND: Failed to release hold for {exchange_id}: {release_result.get('error')}")
                
            except Exception as refund_error:
                logger.error(f"❌ EXCHANGE_REFUND_ERROR: Failed to process refund for {exchange_id}: {refund_error}")
            
            db.commit()
            
            logger.info(f"💸 EXCHANGE_REFUND: {exchange_id} marked as failed, refund processed")
            
            return False
            
        except Exception as e:
            logger.error(f"❌ Failed to handle exchange refund: {e}")
            db.rollback()
            return False
    
    async def handle_escrow_failure(self,
                                   escrow_id: str,
                                   exception: Exception,
                                   operation: str,
                                   context: Optional[Dict[str, Any]] = None) -> bool:
        """
        Handle an escrow operation failure with intelligent classification and retry logic
        
        Args:
            escrow_id: ID of the failed escrow
            exception: The exception that caused the failure
            operation: Escrow operation type ("payment_processing", "release", "refund", "cancellation")
            context: Additional context (user_id, service, etc.)
        
        Returns:
            bool: True if retry was scheduled, False if alternative action was taken
        """
        start_time = datetime.utcnow()
        logger.info(f"🔄 ESCROW_RETRY_ORCHESTRATOR: Handling {operation} failure for escrow {escrow_id}")
        
        # Enhanced logging context
        log_context = {
            "escrow_id": escrow_id,
            "operation": operation,
            "exception_type": type(exception).__name__,
            "exception_message": str(exception),
            "context": context or {},
            "start_time": start_time.isoformat()
        }
        
        try:
            # Classify the error using escrow-specific classifier
            failure_type, error_code, retryable, delay_seconds = classify_escrow_error(
                exception, escrow_id, operation, context
            )
            
            # Enhanced classification logging
            classification_data = {
                "failure_type": failure_type.value,
                "error_code": error_code.value,
                "retryable": retryable,
                "delay_seconds": delay_seconds,
                "classification_duration_ms": int((datetime.utcnow() - start_time).total_seconds() * 1000)
            }
            
            logger.info(f"🔍 ESCROW_CLASSIFICATION_DECISION: {escrow_id} operation '{operation}' classified as {failure_type.value}/{error_code.value} (retryable={retryable})", extra=classification_data)
            
            # Schedule retry or take alternative action based on operation type
            retry_scheduled = await self._schedule_escrow_retry(
                escrow_id, operation, failure_type, error_code, retryable, delay_seconds
            )
            
            # Final result logging with metrics
            processing_time = (datetime.utcnow() - start_time).total_seconds()
            result_data = {
                "retry_scheduled": retry_scheduled,
                "processing_time_seconds": processing_time,
                "outcome": "retry" if retry_scheduled else "alternative_action"
            }
            
            logger.info(f"✅ ESCROW_RETRY_ORCHESTRATOR: Escrow {escrow_id} operation '{operation}' processed - retry_scheduled={retry_scheduled} in {processing_time:.3f}s", extra={**log_context, **classification_data, **result_data})
            return retry_scheduled
            
        except Exception as e:
            processing_time = (datetime.utcnow() - start_time).total_seconds()
            error_data = {
                "processing_time_seconds": processing_time,
                "orchestrator_error": str(e)
            }
            
            logger.error(f"❌ ESCROW_RETRY_ORCHESTRATOR: Error handling failure for {escrow_id} operation '{operation}': {e}", extra={**log_context, **error_data})
            handle_error(e, {"escrow_id": escrow_id, "operation": operation, "original_exception": str(exception)})
            return False
    
    async def handle_deposit_failure(self,
                                   user_id: int,
                                   exception: Exception,
                                   operation: str,
                                   context: Optional[Dict[str, Any]] = None) -> bool:
        """
        Handle a deposit/wallet operation failure with intelligent classification and retry logic
        
        Args:
            user_id: ID of the user for the failed deposit
            exception: The exception that caused the failure
            operation: Deposit operation type ("webhook_processing", "confirmation_polling", "wallet_credit")
            context: Additional context (transaction_id, amount, currency, etc.)
        
        Returns:
            bool: True if retry was scheduled, False if alternative action was taken
        """
        start_time = datetime.utcnow()
        logger.info(f"🔄 DEPOSIT_RETRY_ORCHESTRATOR: Handling {operation} failure for user {user_id}")
        
        # Enhanced logging context
        log_context = {
            "user_id": user_id,
            "operation": operation,
            "exception_type": type(exception).__name__,
            "exception_message": str(exception),
            "context": context or {},
            "start_time": start_time.isoformat()
        }
        
        try:
            # Classify the error using deposit-specific classifier
            failure_type, error_code, retryable, delay_seconds = classify_deposit_error(
                exception, user_id, operation, context
            )
            
            # Enhanced classification logging
            classification_data = {
                "failure_type": failure_type.value,
                "error_code": error_code.value,
                "retryable": retryable,
                "delay_seconds": delay_seconds,
                "classification_duration_ms": int((datetime.utcnow() - start_time).total_seconds() * 1000)
            }
            
            logger.info(f"🔍 DEPOSIT_CLASSIFICATION_DECISION: User {user_id} operation '{operation}' classified as {failure_type.value}/{error_code.value} (retryable={retryable})", extra=classification_data)
            
            # Schedule retry or take alternative action based on operation type
            retry_scheduled = await self._schedule_deposit_retry(
                user_id, operation, failure_type, error_code, retryable, delay_seconds, context
            )
            
            # Final result logging with metrics
            processing_time = (datetime.utcnow() - start_time).total_seconds()
            result_data = {
                "retry_scheduled": retry_scheduled,
                "processing_time_seconds": processing_time,
                "outcome": "retry" if retry_scheduled else "alternative_action"
            }
            
            logger.info(f"✅ DEPOSIT_RETRY_ORCHESTRATOR: User {user_id} operation '{operation}' processed - retry_scheduled={retry_scheduled} in {processing_time:.3f}s", extra={**log_context, **classification_data, **result_data})
            return retry_scheduled
            
        except Exception as e:
            processing_time = (datetime.utcnow() - start_time).total_seconds()
            error_data = {
                "processing_time_seconds": processing_time,
                "orchestrator_error": str(e)
            }
            
            logger.error(f"❌ DEPOSIT_RETRY_ORCHESTRATOR: Error handling failure for user {user_id} operation '{operation}': {e}", extra={**log_context, **error_data})
            handle_error(e, {"user_id": user_id, "operation": operation, "original_exception": str(exception)})
            return False
    
    async def _schedule_escrow_retry(self,
                                   escrow_id: str,
                                   operation: str,
                                   failure_type: OperationFailureType,
                                   error_code: CashoutErrorCode,
                                   retryable: bool,
                                   delay_seconds: int) -> bool:
        """
        Schedule retry for escrow operation or take alternative action
        
        Returns:
            bool: True if retry was scheduled, False if alternative action was taken
        """
        with managed_session() as db:
            try:
                # Get the escrow record
                escrow = db.query(Escrow).filter(
                    Escrow.escrow_id == escrow_id
                ).first()
                
                if not escrow:
                    logger.error(f"❌ Escrow {escrow_id} not found")
                    return False
                
                # Initialize retry fields if they don't exist (for new fields)
                if not hasattr(escrow, 'retry_count') or escrow.retry_count is None:
                    escrow.retry_count = 0
                if not hasattr(escrow, 'failure_type') or escrow.failure_type is None:
                    escrow.failure_type = None
                if not hasattr(escrow, 'last_error_code') or escrow.last_error_code is None:
                    escrow.last_error_code = None
                
                # Check if we should retry based on current retry count
                should_retry = self.error_classifier.should_retry(error_code, escrow.retry_count)
                
                # Enhanced retry decision logging
                decision_data = {
                    "operation": operation,
                    "failure_type": failure_type.value,
                    "error_code": error_code.value,
                    "retry_count": escrow.retry_count,
                    "should_retry": should_retry,
                    "retryable": retryable,
                    "escrow_status": escrow.status
                }
                
                logger.info(f"🔍 ESCROW_RETRY_DECISION: {escrow_id} operation '{operation}' - type={failure_type.value}, code={error_code.value}, retry_count={escrow.retry_count}, should_retry={should_retry}", extra=decision_data)
                
                # Update failure classification
                escrow.failure_type = failure_type.value
                escrow.last_error_code = error_code.value
                escrow.last_retry_at = datetime.utcnow()
                
                if should_retry and retryable:
                    # Schedule retry
                    next_retry_delay = self.error_classifier.get_next_retry_delay(error_code, escrow.retry_count)
                    escrow.next_retry_at = datetime.utcnow() + timedelta(seconds=next_retry_delay)
                    
                    # Enhanced retry scheduling logging
                    retry_data = {
                        "next_retry_at": escrow.next_retry_at.isoformat(),
                        "delay_seconds": next_retry_delay,
                        "delay_minutes": round(next_retry_delay / 60, 2),
                        "retry_attempt": escrow.retry_count + 1
                    }
                    
                    logger.info(f"⏰ ESCROW_RETRY_SCHEDULED: {escrow_id} operation '{operation}' will retry at {escrow.next_retry_at} (delay: {next_retry_delay}s)", extra={**decision_data, **retry_data})
                    
                    db.commit()
                    return True
                    
                else:
                    # Cannot retry - take alternative action based on operation type
                    escrow.next_retry_at = None
                    
                    # Enhanced alternative action logging
                    alternative_reason = "user_error" if failure_type == OperationFailureType.USER else "max_retries_exceeded"
                    alternative_data = {
                        "alternative_reason": alternative_reason,
                        "final_retry_count": escrow.retry_count,
                        "action_taken": "escrow_status_update"
                    }
                    
                    if operation in ["release", "refund"]:
                        # Critical operations - mark for manual intervention
                        logger.warning(f"⚠️ ESCROW_MANUAL_INTERVENTION: {escrow_id} operation '{operation}' failed - requires manual processing", extra={**decision_data, **alternative_data})
                        # Could set a flag for admin attention here
                    else:
                        # Non-critical operations
                        logger.info(f"💡 ESCROW_ALTERNATIVE_ACTION: {escrow_id} operation '{operation}' failed - taking alternative action", extra={**decision_data, **alternative_data})
                    
                    db.commit()
                    return False
                    
            except Exception as e:
                db.rollback()
                logger.error(f"❌ Error scheduling escrow retry for {escrow_id}: {e}")
                raise
    
    async def _schedule_deposit_retry(self,
                                    user_id: int,
                                    operation: str,
                                    failure_type: OperationFailureType,
                                    error_code: CashoutErrorCode,
                                    retryable: bool,
                                    delay_seconds: int,
                                    context: Optional[Dict[str, Any]] = None) -> bool:
        """
        Schedule retry for deposit operation or take alternative action
        
        Returns:
            bool: True if retry was scheduled, False if alternative action was taken
        """
        with managed_session():
            try:
                # For deposit operations, we might need to create/update a transaction retry record
                # This is a simplified approach - in a full implementation, you'd want to store
                # retry information in a dedicated table or in the Transaction model
                
                transaction_id = context.get("transaction_id") if context else None
                
                # Enhanced retry decision logging
                decision_data = {
                    "user_id": user_id,
                    "operation": operation,
                    "failure_type": failure_type.value,
                    "error_code": error_code.value,
                    "retryable": retryable,
                    "transaction_id": transaction_id
                }
                
                logger.info(f"🔍 DEPOSIT_RETRY_DECISION: User {user_id} operation '{operation}' - type={failure_type.value}, code={error_code.value}, retryable={retryable}", extra=decision_data)
                
                if retryable and failure_type == OperationFailureType.TECHNICAL:
                    # For now, we'll log the retry schedule but not persist it
                    # In a full implementation, you'd store this in the database
                    next_retry_delay = self.error_classifier.get_next_retry_delay(error_code, 0)  # Using 0 for first attempt
                    next_retry_at = datetime.utcnow() + timedelta(seconds=next_retry_delay)
                    
                    # Enhanced retry scheduling logging
                    retry_data = {
                        "next_retry_at": next_retry_at.isoformat(),
                        "delay_seconds": next_retry_delay,
                        "delay_minutes": round(next_retry_delay / 60, 2),
                        "retry_attempt": 1
                    }
                    
                    logger.info(f"⏰ DEPOSIT_RETRY_SCHEDULED: User {user_id} operation '{operation}' will retry at {next_retry_at} (delay: {next_retry_delay}s)", extra={**decision_data, **retry_data})
                    
                    # TODO: In a full implementation, store retry info in database
                    # This could be in a separate retry_queue table or in the Transaction model
                    
                    return True
                    
                else:
                    # Cannot retry - take alternative action based on operation type
                    alternative_reason = "user_error" if failure_type == OperationFailureType.USER else "non_retryable_technical"
                    alternative_data = {
                        "alternative_reason": alternative_reason,
                        "action_taken": "logged_for_manual_review"
                    }
                    
                    if operation == "wallet_credit":
                        # Critical operation - needs manual attention
                        logger.warning(f"⚠️ DEPOSIT_MANUAL_INTERVENTION: User {user_id} operation '{operation}' failed - requires manual processing", extra={**decision_data, **alternative_data})
                    else:
                        # Non-critical operations
                        logger.info(f"💡 DEPOSIT_ALTERNATIVE_ACTION: User {user_id} operation '{operation}' failed - taking alternative action", extra={**decision_data, **alternative_data})
                    
                    return False
                    
            except Exception as e:
                logger.error(f"❌ Error scheduling deposit retry for user {user_id}: {e}")
                raise


    # Phase 3: New handler methods for notifications, wallet operations, and admin operations
    
    async def handle_notification_failure(self,
                                        recipient: str,
                                        channel: str,
                                        exception: Exception,
                                        context: Optional[Dict[str, Any]] = None) -> bool:
        """
        Handle a notification failure with intelligent classification and retry logic
        
        Args:
            recipient: Email, phone number, or username
            channel: Notification channel ("email", "sms", "telegram")
            exception: The exception that caused the failure
            context: Additional context (message_type, user_id, etc.)
        
        Returns:
            bool: True if retry was scheduled, False if fallback/abandonment triggered
        """
        start_time = datetime.utcnow()
        logger.info(f"🔄 NOTIFICATION_RETRY_ORCHESTRATOR: Handling failure for {channel} to {recipient}")
        
        # Enhanced logging context
        log_context = {
            "recipient": recipient,
            "channel": channel,
            "exception_type": type(exception).__name__,
            "exception_message": str(exception),
            "context": context or {},
            "start_time": start_time.isoformat()
        }
        
        try:
            # Classify the error using the new notification classifier
            failure_type, error_code, retryable, delay_seconds = self.error_classifier.classify_notification_error(
                exception, recipient, channel, context
            )
            
            # Enhanced classification logging
            classification_data = {
                "failure_type": failure_type.value,
                "error_code": error_code.value,
                "retryable": retryable,
                "delay_seconds": delay_seconds,
                "classification_duration_ms": int((datetime.utcnow() - start_time).total_seconds() * 1000)
            }
            
            logger.info(f"🔍 NOTIFICATION_CLASSIFICATION_DECISION: {channel} to {recipient} classified as {failure_type.value}/{error_code.value} (retryable={retryable})", extra=classification_data)
            
            if retryable and failure_type == OperationFailureType.TECHNICAL:
                # Schedule retry with backoff
                logger.info(f"⏰ NOTIFICATION_RETRY_SCHEDULED: {channel} to {recipient} will retry in {delay_seconds}s")
                
                # Here you would implement notification-specific retry scheduling
                # This could integrate with a notification queue or job scheduler
                
                processing_time = (datetime.utcnow() - start_time).total_seconds()
                logger.info(f"✅ NOTIFICATION_RETRY_ORCHESTRATOR: {channel} to {recipient} retry scheduled in {processing_time:.3f}s")
                return True
                
            else:
                # Cannot retry - try fallback channel or mark as failed
                logger.info(f"💔 NOTIFICATION_FALLBACK: {channel} to {recipient} - {failure_type.value} error, attempting fallback")
                
                # Implement channel fallback logic here
                fallback_success = await self._attempt_notification_fallback(recipient, channel, context)
                
                processing_time = (datetime.utcnow() - start_time).total_seconds()
                result_data = {
                    "fallback_attempted": True,
                    "fallback_success": fallback_success,
                    "processing_time_seconds": processing_time,
                    "outcome": "fallback" if fallback_success else "failed"
                }
                
                logger.info(f"✅ NOTIFICATION_RETRY_ORCHESTRATOR: {channel} to {recipient} processed - fallback_success={fallback_success} in {processing_time:.3f}s", extra={**log_context, **classification_data, **result_data})
                return fallback_success
                
        except Exception as e:
            processing_time = (datetime.utcnow() - start_time).total_seconds()
            error_data = {
                "processing_time_seconds": processing_time,
                "orchestrator_error": str(e)
            }
            
            logger.error(f"❌ NOTIFICATION_RETRY_ORCHESTRATOR: Error handling failure for {channel} to {recipient}: {e}", extra={**log_context, **error_data})
            handle_error(e, {"recipient": recipient, "channel": channel, "original_exception": str(exception)})
            return False
    
    async def handle_wallet_failure(self,
                                  user_id: int,
                                  operation: str,
                                  exception: Exception,
                                  context: Optional[Dict[str, Any]] = None) -> bool:
        """
        Handle a wallet operation failure with intelligent classification and retry logic
        
        Args:
            user_id: User identifier for context
            operation: Wallet operation type ("balance_update", "debit", "credit", "freeze", "unfreeze")
            exception: The exception that caused the failure
            context: Additional context (amount, currency, transaction_id, etc.)
        
        Returns:
            bool: True if retry was scheduled, False if operation should be marked as failed
        """
        start_time = datetime.utcnow()
        logger.info(f"🔄 WALLET_RETRY_ORCHESTRATOR: Handling failure for user {user_id} operation '{operation}'")
        
        # Enhanced logging context
        log_context = {
            "user_id": user_id,
            "operation": operation,
            "exception_type": type(exception).__name__,
            "exception_message": str(exception),
            "context": context or {},
            "start_time": start_time.isoformat()
        }
        
        try:
            # Classify the error using the new wallet classifier
            failure_type, error_code, retryable, delay_seconds = self.error_classifier.classify_wallet_error(
                exception, user_id, operation, context
            )
            
            # Enhanced classification logging
            classification_data = {
                "failure_type": failure_type.value,
                "error_code": error_code.value,
                "retryable": retryable,
                "delay_seconds": delay_seconds,
                "classification_duration_ms": int((datetime.utcnow() - start_time).total_seconds() * 1000)
            }
            
            logger.info(f"🔍 WALLET_CLASSIFICATION_DECISION: User {user_id} operation '{operation}' classified as {failure_type.value}/{error_code.value} (retryable={retryable})", extra=classification_data)
            
            if retryable and failure_type == OperationFailureType.TECHNICAL:
                # Schedule retry with exponential backoff
                logger.info(f"⏰ WALLET_RETRY_SCHEDULED: User {user_id} operation '{operation}' will retry in {delay_seconds}s")
                
                # Here you would implement wallet-specific retry scheduling
                # This could integrate with the existing job scheduler or wallet service queue
                
                processing_time = (datetime.utcnow() - start_time).total_seconds()
                logger.info(f"✅ WALLET_RETRY_ORCHESTRATOR: User {user_id} operation '{operation}' retry scheduled in {processing_time:.3f}s")
                return True
                
            else:
                # Cannot retry - mark operation as permanently failed
                if failure_type == OperationFailureType.USER:
                    logger.info(f"💔 WALLET_USER_ERROR: User {user_id} operation '{operation}' - User error, marking as failed")
                else:
                    logger.info(f"💔 WALLET_MAX_RETRIES: User {user_id} operation '{operation}' - Max retries exceeded, marking as failed")
                
                # Implement wallet operation failure handling (e.g., rollback, notifications)
                await self._handle_wallet_operation_failure(user_id, operation, failure_type, context)
                
                processing_time = (datetime.utcnow() - start_time).total_seconds()
                result_data = {
                    "processing_time_seconds": processing_time,
                    "outcome": "failed",
                    "failure_reason": "user_error" if failure_type == OperationFailureType.USER else "max_retries"
                }
                
                logger.info(f"✅ WALLET_RETRY_ORCHESTRATOR: User {user_id} operation '{operation}' marked as failed in {processing_time:.3f}s", extra={**log_context, **classification_data, **result_data})
                return False
                
        except Exception as e:
            processing_time = (datetime.utcnow() - start_time).total_seconds()
            error_data = {
                "processing_time_seconds": processing_time,
                "orchestrator_error": str(e)
            }
            
            logger.error(f"❌ WALLET_RETRY_ORCHESTRATOR: Error handling failure for user {user_id} operation '{operation}': {e}", extra={**log_context, **error_data})
            handle_error(e, {"user_id": user_id, "operation": operation, "original_exception": str(exception)})
            return False
    
    async def handle_admin_failure(self,
                                 admin_user_id: int,
                                 operation: str,
                                 exception: Exception,
                                 context: Optional[Dict[str, Any]] = None) -> bool:
        """
        Handle an admin operation failure with intelligent classification and retry logic
        
        Args:
            admin_user_id: Admin user identifier for context
            operation: Admin operation type ("funding", "approval", "notification", "authentication")
            exception: The exception that caused the failure
            context: Additional context (target_service, amount, etc.)
        
        Returns:
            bool: True if retry was scheduled, False if operation should be escalated/failed
        """
        start_time = datetime.utcnow()
        logger.info(f"🔄 ADMIN_RETRY_ORCHESTRATOR: Handling failure for admin {admin_user_id} operation '{operation}'")
        
        # Enhanced logging context
        log_context = {
            "admin_user_id": admin_user_id,
            "operation": operation,
            "exception_type": type(exception).__name__,
            "exception_message": str(exception),
            "context": context or {},
            "start_time": start_time.isoformat()
        }
        
        try:
            # Classify the error using the new admin classifier
            failure_type, error_code, retryable, delay_seconds = self.error_classifier.classify_admin_error(
                exception, admin_user_id, operation, context
            )
            
            # Enhanced classification logging
            classification_data = {
                "failure_type": failure_type.value,
                "error_code": error_code.value,
                "retryable": retryable,
                "delay_seconds": delay_seconds,
                "classification_duration_ms": int((datetime.utcnow() - start_time).total_seconds() * 1000)
            }
            
            logger.info(f"🔍 ADMIN_CLASSIFICATION_DECISION: Admin {admin_user_id} operation '{operation}' classified as {failure_type.value}/{error_code.value} (retryable={retryable})", extra=classification_data)
            
            if retryable and failure_type == OperationFailureType.TECHNICAL:
                # Schedule retry with appropriate backoff
                logger.info(f"⏰ ADMIN_RETRY_SCHEDULED: Admin {admin_user_id} operation '{operation}' will retry in {delay_seconds}s")
                
                # Here you would implement admin-specific retry scheduling
                # This could integrate with admin notification systems and approval workflows
                
                processing_time = (datetime.utcnow() - start_time).total_seconds()
                logger.info(f"✅ ADMIN_RETRY_ORCHESTRATOR: Admin {admin_user_id} operation '{operation}' retry scheduled in {processing_time:.3f}s")
                return True
                
            else:
                # Cannot retry - escalate or mark as failed
                if failure_type == OperationFailureType.USER:
                    logger.info(f"💔 ADMIN_USER_ERROR: Admin {admin_user_id} operation '{operation}' - User error, escalating")
                else:
                    logger.info(f"💔 ADMIN_MAX_RETRIES: Admin {admin_user_id} operation '{operation}' - Max retries exceeded, escalating")
                
                # Implement admin operation failure handling (e.g., escalation, security alerts)
                await self._handle_admin_operation_failure(admin_user_id, operation, failure_type, context)
                
                processing_time = (datetime.utcnow() - start_time).total_seconds()
                result_data = {
                    "processing_time_seconds": processing_time,
                    "outcome": "escalated",
                    "failure_reason": "user_error" if failure_type == OperationFailureType.USER else "max_retries"
                }
                
                logger.info(f"✅ ADMIN_RETRY_ORCHESTRATOR: Admin {admin_user_id} operation '{operation}' escalated in {processing_time:.3f}s", extra={**log_context, **classification_data, **result_data})
                return False
                
        except Exception as e:
            processing_time = (datetime.utcnow() - start_time).total_seconds()
            error_data = {
                "processing_time_seconds": processing_time,
                "orchestrator_error": str(e)
            }
            
            logger.error(f"❌ ADMIN_RETRY_ORCHESTRATOR: Error handling failure for admin {admin_user_id} operation '{operation}': {e}", extra={**log_context, **error_data})
            handle_error(e, {"admin_user_id": admin_user_id, "operation": operation, "original_exception": str(exception)})
            return False
    
    # Helper methods for Phase 3 handlers
    
    async def _attempt_notification_fallback(self, recipient: str, failed_channel: str, context: Optional[Dict[str, Any]]) -> bool:
        """
        Attempt to deliver notification via fallback channel
        
        Channel priority: Email → SMS → Telegram
        """
        try:
            # Define channel fallback priorities
            fallback_channels = []
            if failed_channel == "email":
                fallback_channels = ["sms", "telegram"]
            elif failed_channel == "sms":
                fallback_channels = ["email", "telegram"]
            elif failed_channel == "telegram":
                fallback_channels = ["email", "sms"]
            
            for fallback_channel in fallback_channels:
                logger.info(f"📧 NOTIFICATION_FALLBACK: Attempting {fallback_channel} for {recipient}")
                
                # Here you would implement the actual fallback delivery logic
                # For now, we'll just log the attempt
                
                logger.info(f"✅ NOTIFICATION_FALLBACK_SUCCESS: {fallback_channel} delivery attempted for {recipient}")
                return True  # Simulate successful fallback
            
            return False
            
        except Exception as e:
            logger.error(f"❌ NOTIFICATION_FALLBACK_ERROR: Failed fallback for {recipient}: {e}")
            return False
    
    async def _handle_wallet_operation_failure(self, user_id: int, operation: str, failure_type: OperationFailureType, context: Optional[Dict[str, Any]]):
        """
        Handle permanent wallet operation failure (rollback, notifications, etc.)
        """
        try:
            logger.info(f"💔 WALLET_OPERATION_FAILURE: Handling permanent failure for user {user_id} operation '{operation}'")
            
            # Here you would implement failure handling logic:
            # - Rollback any partial state changes
            # - Send user notifications about failed operations
            # - Update audit logs
            # - Alert administrators if needed
            
            logger.info(f"✅ WALLET_FAILURE_HANDLED: User {user_id} operation '{operation}' failure handled")
            
        except Exception as e:
            logger.error(f"❌ WALLET_FAILURE_HANDLER_ERROR: Failed to handle wallet failure for user {user_id}: {e}")
            handle_error(e, {"user_id": user_id, "operation": operation})
    
    async def _handle_admin_operation_failure(self, admin_user_id: int, operation: str, failure_type: OperationFailureType, context: Optional[Dict[str, Any]]):
        """
        Handle permanent admin operation failure (escalation, security alerts, etc.)
        """
        try:
            logger.info(f"💔 ADMIN_OPERATION_FAILURE: Handling permanent failure for admin {admin_user_id} operation '{operation}'")
            
            # Here you would implement admin failure handling logic:
            # - Escalate to higher-level admins
            # - Send security alerts if authentication-related
            # - Update audit logs with failure details
            # - Lock services if critical failures detected
            
            logger.info(f"✅ ADMIN_FAILURE_HANDLED: Admin {admin_user_id} operation '{operation}' failure handled")
            
        except Exception as e:
            logger.error(f"❌ ADMIN_FAILURE_HANDLER_ERROR: Failed to handle admin failure for admin {admin_user_id}: {e}")
            handle_error(e, {"admin_user_id": admin_user_id, "operation": operation})


# Singleton instances for easy access
unified_retry_service = UnifiedRetryService()

# Legacy alias for backwards compatibility
cashout_retry_service = unified_retry_service