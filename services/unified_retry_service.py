"""
Streamlined Unified Retry Service for External API Failures
Simple "manual-first" approach with minimal automatic retry.

Features:
- SINGLE attempt maximum with fixed 10-minute delay
- Only applies to external API calls (wallet cashouts via Fincra, Kraken, DynoPay)
- Internal transfers (escrow, exchange) bypass retry logic entirely
- Only 4 error types get automatic retry (via MinimalClassifier)
- All other failures go to admin review queue
- Feature flag for gradual rollout
- Idempotency to prevent duplicate processing
"""

import logging
import time
import hashlib
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from decimal import Decimal
from dataclasses import dataclass
from enum import Enum

from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from sqlalchemy import and_, select

from database import sync_managed_session
from models import (
    UnifiedTransaction, UnifiedTransactionStatus, UnifiedTransactionType,
    UnifiedTransactionRetryLog
)
from config import Config
from utils.financial_audit_logger import (
    financial_audit_logger, FinancialEventType, FinancialContext, EntityType
)
from services.minimal_classifier import MinimalClassifier
from utils.unified_transaction_state_validator import (
    UnifiedTransactionStateValidator,
    StateTransitionError
)

logger = logging.getLogger(__name__)


class RetryStrategy(Enum):
    """Retry strategy types"""
    EXPONENTIAL = "exponential"
    LINEAR = "linear"
    IMMEDIATE = "immediate"
    DISABLED = "disabled"


class RetryDecision(Enum):
    """Retry decision outcomes"""
    RETRY = "retry"
    FAIL = "fail" 
    SKIP = "skip"


@dataclass
class RetryContext:
    """Context for retry processing"""
    transaction_id: str
    transaction_type: str
    user_id: int
    amount: Decimal
    currency: str
    external_provider: str
    attempt_number: int
    error_code: str
    error_message: str
    error_details: Optional[Dict[str, Any]] = None
    legacy_entity_id: Optional[str] = None  # cashout_id, etc.


@dataclass
class RetryResult:
    """Result of retry processing"""
    decision: RetryDecision
    next_retry_at: Optional[datetime] = None
    delay_seconds: Optional[int] = None
    final_failure: bool = False
    retry_log_id: Optional[int] = None
    message: Optional[str] = None
    idempotency_key: Optional[str] = None


class UnifiedRetryService:
    """
    Streamlined retry orchestrator for external API failures
    
    Key Features:
    - External API Only: Wallet cashouts (Fincra NGN, Kraken crypto, DynoPay)
    - Internal Transfers: Escrow releases, exchange completions bypass retry
    - Single Retry: 1 attempt maximum with fixed 10-minute delay
    - Binary Classification: Uses MinimalClassifier (only 4 error types are retryable)
    - Manual First: Most failures go to admin review queue
    - Idempotency: Prevents duplicate processing
    - Feature Flag: Gradual rollout control
    """
    
    def __init__(self):
        # Streamlined Configuration
        self.max_retry_attempts = getattr(Config, 'STREAMLINED_RETRY_MAX_ATTEMPTS', 1)  # Single retry only
        self.fixed_delay_seconds = getattr(Config, 'STREAMLINED_RETRY_DELAY_SECONDS', 600)  # 10 minutes
        self.feature_enabled = getattr(Config, 'STREAMLINED_FAILURE_HANDLING', True)
        self.technical_retry_enabled = getattr(Config, 'UNIFIED_TECHNICAL_RETRY_ENABLED', True)
        
        # External API providers that require retry logic
        self.external_providers = {
            'fincra': ['NGN'],  # Fincra for NGN payouts
            'kraken': ['BTC', 'ETH', 'LTC', 'XRP', 'ADA', 'DOT'],  # Kraken for crypto
            'dynopay': ['USD', 'EUR']  # DynoPay for international
        }
        
        # Transaction types that use external APIs (others are internal transfers)
        self.external_api_transaction_types = {
            UnifiedTransactionType.WALLET_CASHOUT.value
        }
        
        logger.info(f"🔄 StreamlinedRetryService initialized: enabled={self.feature_enabled}, "
                   f"technical_retry={self.technical_retry_enabled}, max_attempts={self.max_retry_attempts}, "
                   f"delay={self.fixed_delay_seconds}s")
    
    async def handle_transaction_failure(self, 
                                       context: RetryContext,
                                       exception: Exception) -> RetryResult:
        """
        Handle transaction failure with intelligent retry decision
        
        Args:
            context: Retry context with transaction details
            exception: The exception that caused the failure
            
        Returns:
            RetryResult with retry decision and scheduling information
        """
        start_time = datetime.utcnow()
        
        # Enhanced logging context
        log_context = {
            "transaction_id": context.transaction_id,
            "transaction_type": context.transaction_type,
            "user_id": context.user_id,
            "amount": float(context.amount),
            "currency": context.currency,
            "external_provider": context.external_provider,
            "attempt_number": context.attempt_number,
            "error_code": context.error_code,
            "exception_type": type(exception).__name__,
            "exception_message": str(exception)
        }
        
        logger.info(f"🔄 UNIFIED_RETRY: Processing failure for {context.transaction_id} (attempt {context.attempt_number})", extra=log_context)
        
        try:
            # Check if unified retry is enabled
            if not self.feature_enabled:
                logger.info(f"⚠️ UNIFIED_RETRY_DISABLED: Falling back to legacy retry for {context.transaction_id}")
                return RetryResult(
                    decision=RetryDecision.SKIP,
                    message="Unified retry disabled, using legacy system"
                )
            
            # Check if transaction type requires external API (has retry logic)
            if not self._requires_external_api(context.transaction_type):
                logger.info(f"🔄 INTERNAL_TRANSFER: No retry needed for {context.transaction_type} - {context.transaction_id}")
                return RetryResult(
                    decision=RetryDecision.SKIP,
                    message="Internal transfer - no retry logic needed"
                )
            
            # Generate idempotency key for this retry attempt
            idempotency_key = self._generate_idempotency_key(context)
            
            # Make retry decision based on error classification
            retry_decision = self._make_retry_decision(context, exception)
            
            if retry_decision.decision == RetryDecision.RETRY:
                # Schedule retry with fixed delay
                delay_seconds = self.fixed_delay_seconds
                next_retry_at = datetime.utcnow() + timedelta(seconds=delay_seconds)
                
                # Log retry attempt to database
                retry_log_id = await self._log_retry_attempt(
                    context=context,
                    exception=exception,
                    delay_seconds=delay_seconds,
                    next_retry_at=next_retry_at,
                    idempotency_key=idempotency_key,
                    final_retry=(context.attempt_number >= self.max_retry_attempts)
                )
                
                # Financial audit logging
                await self._audit_retry_event(context, retry_decision, delay_seconds)
                
                processing_time = (datetime.utcnow() - start_time).total_seconds()
                success_data = {
                    "retry_log_id": retry_log_id,
                    "next_retry_at": next_retry_at.isoformat(),
                    "delay_seconds": delay_seconds,
                    "processing_time_seconds": processing_time,
                    "idempotency_key": idempotency_key
                }
                
                logger.info(f"⏰ RETRY_SCHEDULED: {context.transaction_id} will retry at {next_retry_at} (delay: {delay_seconds}s)", 
                           extra={**log_context, **success_data})
                
                return RetryResult(
                    decision=RetryDecision.RETRY,
                    next_retry_at=next_retry_at,
                    delay_seconds=delay_seconds,
                    final_failure=(context.attempt_number >= self.max_retry_attempts),
                    retry_log_id=retry_log_id,
                    idempotency_key=idempotency_key,
                    message=f"Retry scheduled for attempt {context.attempt_number + 1}/{self.max_retry_attempts}"
                )
            
            elif retry_decision.decision == RetryDecision.FAIL:
                # Log final failure
                retry_log_id = await self._log_retry_attempt(
                    context=context,
                    exception=exception,
                    delay_seconds=0,
                    next_retry_at=None,
                    idempotency_key=idempotency_key,
                    final_retry=True
                )
                
                # Financial audit logging
                await self._audit_retry_event(context, retry_decision, 0)
                
                processing_time = (datetime.utcnow() - start_time).total_seconds()
                failure_data = {
                    "retry_log_id": retry_log_id,
                    "final_failure": True,
                    "processing_time_seconds": processing_time,
                    "failure_reason": retry_decision.message or "Max retries exceeded or non-retryable error"
                }
                
                logger.warning(f"❌ FINAL_FAILURE: {context.transaction_id} will not retry - {failure_data['failure_reason']}", 
                              extra={**log_context, **failure_data})
                
                return RetryResult(
                    decision=RetryDecision.FAIL,
                    final_failure=True,
                    retry_log_id=retry_log_id,
                    idempotency_key=idempotency_key,
                    message=retry_decision.message or f"Final failure after {context.attempt_number} attempts"
                )
            
            else:  # SKIP
                logger.info(f"⏭️ RETRY_SKIPPED: {context.transaction_id} - {retry_decision.message}")
                return RetryResult(
                    decision=RetryDecision.SKIP,
                    message=retry_decision.message or "Retry skipped"
                )
        
        except Exception as e:
            processing_time = (datetime.utcnow() - start_time).total_seconds()
            error_data = {
                "processing_time_seconds": processing_time,
                "retry_service_error": str(e)
            }
            
            logger.error(f"❌ UNIFIED_RETRY_ERROR: Failed to process retry for {context.transaction_id}: {e}", 
                        extra={**log_context, **error_data})
            
            # Return fail decision on internal error to prevent infinite loops
            return RetryResult(
                decision=RetryDecision.FAIL,
                final_failure=True,
                message=f"Retry service error: {str(e)}"
            )
    
    def _requires_external_api(self, transaction_type: str) -> bool:
        """Check if transaction type requires external API calls (and thus retry logic)"""
        return transaction_type in self.external_api_transaction_types
    
    def _make_retry_decision(self, context: RetryContext, exception: Exception) -> RetryResult:
        """
        Streamlined retry decision using MinimalClassifier.
        
        Only technical transient errors get 1 automatic retry.
        Everything else goes to admin review.
        """
        # Check if maximum retry attempts reached (should be 1)
        if context.attempt_number >= self.max_retry_attempts:
            return RetryResult(
                decision=RetryDecision.FAIL,
                message=f"Maximum retry attempts ({self.max_retry_attempts}) exceeded"
            )
        
        # Check if technical retry is enabled
        if not self.technical_retry_enabled:
            return RetryResult(
                decision=RetryDecision.FAIL,
                message="Technical retry disabled - routing to admin review"
            )
        
        # Use MinimalClassifier to determine if error is retryable
        error_input = {
            'error_code': context.error_code,
            'error_message': context.error_message,
            'exception_type': type(exception).__name__,
            'exception_message': str(exception)
        }
        
        is_retryable = MinimalClassifier.is_retryable_technical(error_input)
        
        # Log classification decision for monitoring
        MinimalClassifier.log_classification(
            error_input, 
            context={
                'transaction_id': context.transaction_id,
                'user_id': context.user_id,
                'external_provider': context.external_provider,
                'attempt_number': context.attempt_number
            }
        )
        
        if is_retryable:
            return RetryResult(
                decision=RetryDecision.RETRY,
                message=f"Technical transient error - automatic retry in {self.fixed_delay_seconds//60} minutes"
            )
        else:
            return RetryResult(
                decision=RetryDecision.FAIL,
                message="Non-technical error or requires human review - routing to admin"
            )
    
# Obsolete methods removed - no longer needed with streamlined approach
    
    def _generate_idempotency_key(self, context: RetryContext) -> str:
        """Generate unique idempotency key for retry attempt"""
        key_data = f"{context.transaction_id}:{context.attempt_number}:{context.error_code}:{int(time.time() // 60)}"
        return hashlib.sha256(key_data.encode()).hexdigest()[:32]
    
    async def _log_retry_attempt(self,
                               context: RetryContext,
                               exception: Exception,
                               delay_seconds: int,
                               next_retry_at: Optional[datetime],
                               idempotency_key: str,
                               final_retry: bool = False) -> Optional[int]:
        """
        Log retry attempt to database with comprehensive details
        
        Returns retry log ID if successful, None if failed
        """
        
        with sync_managed_session() as db:
            try:
                # Check for existing retry log with same idempotency key
                result = db.execute(select(UnifiedTransactionRetryLog).where(
                    UnifiedTransactionRetryLog.transaction_id == context.transaction_id,
                    UnifiedTransactionRetryLog.retry_attempt == context.attempt_number
                ))
                existing_log = result.scalar_one_or_none()
                
                if existing_log:
                    logger.warning(f"Retry log already exists for {context.transaction_id} attempt {context.attempt_number}")
                    return existing_log.id
                
                # Create new retry log entry
                retry_log = UnifiedTransactionRetryLog(
                    transaction_id=context.transaction_id,
                    retry_attempt=context.attempt_number,
                    retry_reason=f"External API failure: {context.error_code}",
                    error_code=context.error_code,
                    error_message=context.error_message,
                    error_details=context.error_details or {},
                    retry_strategy=RetryStrategy.EXPONENTIAL.value,
                    delay_seconds=delay_seconds,
                    next_retry_at=next_retry_at,
                    external_provider=context.external_provider,
                    external_response_code=getattr(exception, 'response_code', None),
                    external_response_body=str(exception) if len(str(exception)) < 1000 else str(exception)[:1000],
                    retry_successful=None,  # Will be updated when retry executes
                    final_retry=final_retry,
                    attempted_at=datetime.utcnow()
                )
                
                db.add(retry_log)
                db.commit()
                
                logger.debug(f"📝 RETRY_LOG_CREATED: ID={retry_log.id} for {context.transaction_id}")
                return retry_log.id
                
            except IntegrityError as e:
                db.rollback()
                logger.warning(f"Retry log already exists (integrity constraint): {e}")
                return None
            except Exception as e:
                db.rollback()
                logger.error(f"Failed to create retry log for {context.transaction_id}: {e}")
                return None
    
    async def _audit_retry_event(self, 
                                context: RetryContext, 
                                decision: RetryResult, 
                                delay_seconds: int):
        """Log retry event to financial audit system"""
        
        try:
            audit_context = FinancialContext(
                entity_type=EntityType.TRANSACTION,
                entity_id=context.transaction_id,
                user_id=context.user_id,
                amount=context.amount,
                currency=context.currency,
                metadata={
                    "external_provider": context.external_provider,
                    "attempt_number": context.attempt_number,
                    "error_code": context.error_code,
                    "retry_decision": decision.decision.value,
                    "delay_seconds": delay_seconds,
                    "transaction_type": context.transaction_type
                }
            )
            
            if decision.decision == RetryDecision.RETRY:
                event_type = FinancialEventType.RETRY_SCHEDULED
                event_details = f"Retry {context.attempt_number} scheduled for {context.external_provider} API failure"
            elif decision.decision == RetryDecision.FAIL:
                event_type = FinancialEventType.TRANSACTION_FAILED
                event_details = f"Final failure after {context.attempt_number} attempts on {context.external_provider}"
            else:
                event_type = FinancialEventType.SYSTEM_EVENT
                event_details = f"Retry skipped for {context.transaction_type}"
            
            await financial_audit_logger.log_event(
                event_type=event_type,
                context=audit_context,
                details=event_details
            )
            
        except Exception as e:
            logger.error(f"Failed to audit retry event for {context.transaction_id}: {e}")
    
    async def process_ready_retries(self, limit: int = 20) -> Dict[str, int]:
        """
        Process transactions ready for retry execution
        
        Args:
            limit: Maximum number of retries to process in this batch
            
        Returns:
            Dictionary with processing statistics
        """
        if not self.feature_enabled:
            return {"skipped": 1, "reason": "unified_retry_disabled"}
        
        start_time = datetime.utcnow()
        stats = {
            "processed": 0,
            "successful": 0,
            "failed": 0,
            "rescheduled": 0,
            "errors": 0
        }
        
        logger.info(f"🔄 UNIFIED_RETRY_PROCESSOR: Starting batch processing (limit: {limit})")
        
        with sync_managed_session() as db:
            try:
                # Find transactions ready for retry
                result = db.execute(select(UnifiedTransactionRetryLog).where(
                    and_(
                        UnifiedTransactionRetryLog.next_retry_at <= datetime.utcnow(),
                        UnifiedTransactionRetryLog.retry_successful.is_(None),
                        UnifiedTransactionRetryLog.final_retry == False
                    )
                ).order_by(UnifiedTransactionRetryLog.next_retry_at).limit(limit))
                
                ready_retries = list(result.scalars())
                logger.info(f"🔍 UNIFIED_RETRY_QUEUE: Found {len(ready_retries)} transactions ready for retry")
                
                for retry_log in ready_retries:
                    try:
                        stats["processed"] += 1
                        
                        # Process individual retry
                        result = await self._execute_retry(retry_log, db)
                        
                        if result["success"]:
                            stats["successful"] += 1
                        elif result["rescheduled"]:
                            stats["rescheduled"] += 1
                        else:
                            stats["failed"] += 1
                            
                    except Exception as e:
                        # Check if this is a non-retryable user error
                        error_message = str(e)
                        is_non_retryable = any(
                            code.value in error_message or code.name in error_message 
                            for code in self.non_retryable_error_codes
                        )
                        
                        if is_non_retryable:
                            # Handle non-retryable errors internally - don't let them bubble up
                            try:
                                # Mark retry as final failure
                                retry_log.retry_successful = False
                                retry_log.final_retry = True
                                retry_log.completed_at = datetime.utcnow()
                                retry_log.duration_ms = 0
                                
                                # Update transaction status to failed if we can find it
                                result = db.execute(select(UnifiedTransaction).where(
                                    UnifiedTransaction.transaction_id == retry_log.transaction_id
                                ))
                                transaction = result.scalar_one_or_none()
                                
                                if transaction:
                                    # Validate state transition to FAILED
                                    try:
                                        current_status = UnifiedTransactionStatus(transaction.status)
                                        is_valid, reason = UnifiedTransactionStateValidator.validate_transition(
                                            current_status,
                                            UnifiedTransactionStatus.FAILED,
                                            transaction_id=transaction.transaction_id
                                        )
                                        
                                        if is_valid:
                                            transaction.status = 'failed'
                                            transaction.last_error_code = error_message
                                            transaction.updated_at = datetime.utcnow()
                                        else:
                                            logger.warning(
                                                f"🚫 RETRY_TX_FAIL_BLOCKED: {current_status.value}→FAILED "
                                                f"for {transaction.transaction_id}: {reason} (transaction already in terminal state)"
                                            )
                                            # Don't fail the cleanup - transaction already in appropriate terminal state
                                    except StateTransitionError as e:
                                        logger.warning(
                                            f"🚫 RETRY_TX_FAIL_BLOCKED: Invalid transition for "
                                            f"{transaction.transaction_id}: {e} (transaction already in terminal state)"
                                        )
                                        # Transaction already in terminal state - this is OK for retry system
                                    except Exception as e:
                                        logger.error(
                                            f"🚫 RETRY_TX_VALIDATION_ERROR: Error validating transition for "
                                            f"{transaction.transaction_id}: {e}"
                                        )
                                        # Fallback for unknown status values
                                        transaction.status = 'failed'
                                        transaction.last_error_code = error_message
                                        transaction.updated_at = datetime.utcnow()
                                
                                logger.warning(f"❌ NON_RETRYABLE_USER_ERROR: {retry_log.transaction_id} failed with user error: {error_message} (handled internally)")
                                stats["failed"] += 1
                                
                            except Exception as cleanup_error:
                                logger.error(f"Failed to clean up non-retryable error for {retry_log.transaction_id}: {cleanup_error}")
                                stats["errors"] += 1
                        else:
                            # Re-raise technical errors that should be handled as retryable
                            stats["errors"] += 1
                            logger.error(f"Error processing retry {retry_log.id}: {e}")
                            # Don't re-raise to prevent job-level error propagation
                
                db.commit()
                
                processing_time = (datetime.utcnow() - start_time).total_seconds()
                logger.info(f"✅ UNIFIED_RETRY_BATCH_COMPLETE: {stats} in {processing_time:.3f}s")
                
                return stats
                
            except Exception as e:
                db.rollback()
                logger.error(f"Failed to process retry batch: {e}")
                stats["errors"] += 1
                return stats
    
    async def _execute_retry(self, retry_log: UnifiedTransactionRetryLog, db: Session) -> Dict[str, Any]:
        """
        Execute individual retry attempt
        
        Returns result dictionary with success/failure information
        """
        retry_start = datetime.utcnow()
        
        try:
            # Get the unified transaction
            result = db.execute(select(UnifiedTransaction).where(
                UnifiedTransaction.transaction_id == retry_log.transaction_id
            ))
            transaction = result.scalar_one_or_none()
            
            if not transaction:
                logger.error(f"Transaction {retry_log.transaction_id} not found for retry")
                retry_log.retry_successful = False
                retry_log.completed_at = datetime.utcnow()
                retry_log.duration_ms = 0
                return {"success": False, "rescheduled": False, "error": "transaction_not_found"}
            
            # Import the unified transaction service here to avoid circular imports
            from services.unified_transaction_service import UnifiedTransactionService
            
            # Create transaction service instance
            transaction_service = UnifiedTransactionService()
            
            # Attempt to continue processing from where it failed
            try:
                result = await transaction_service.continue_external_processing(
                    transaction_id=retry_log.transaction_id
                )
            except Exception as processing_error:
                # Check if this is a non-retryable user error
                error_message = str(processing_error)
                is_non_retryable = any(
                    code.value in error_message or code.name in error_message 
                    for code in self.non_retryable_error_codes
                )
                
                if is_non_retryable:
                    # Handle non-retryable errors internally - don't let them bubble up
                    retry_log.completed_at = datetime.utcnow()
                    retry_log.duration_ms = int((retry_log.completed_at - retry_start).total_seconds() * 1000)
                    retry_log.retry_successful = False
                    retry_log.final_retry = True
                    
                    # Update the transaction status to failed with validation
                    try:
                        current_status = UnifiedTransactionStatus(transaction.status)
                        is_valid, reason = UnifiedTransactionStateValidator.validate_transition(
                            current_status,
                            UnifiedTransactionStatus.FAILED,
                            transaction_id=transaction.transaction_id
                        )
                        
                        if is_valid:
                            transaction.status = 'failed'
                            transaction.last_error_code = error_message
                            transaction.updated_at = datetime.utcnow()
                        else:
                            logger.warning(
                                f"🚫 RETRY_TX_FAIL_BLOCKED: {current_status.value}→FAILED "
                                f"for {transaction.transaction_id}: {reason} (transaction already in terminal state)"
                            )
                            # Transaction already in terminal state - acceptable for retry
                    except StateTransitionError as e:
                        logger.warning(
                            f"🚫 RETRY_TX_FAIL_BLOCKED: Invalid transition for "
                            f"{transaction.transaction_id}: {e} (transaction already in terminal state)"
                        )
                        # Transaction already in terminal state - this is OK
                    except Exception as e:
                        logger.error(
                            f"🚫 RETRY_TX_VALIDATION_ERROR: Error validating transition for "
                            f"{transaction.transaction_id}: {e}"
                        )
                        # Fallback for unknown status values
                        transaction.status = 'failed'
                        transaction.last_error_code = error_message
                        transaction.updated_at = datetime.utcnow()
                    
                    logger.warning(f"❌ NON_RETRYABLE_ERROR: {retry_log.transaction_id} failed with user error: {error_message}")
                    return {"success": False, "rescheduled": False, "error": "non_retryable_user_error"}
                else:
                    # Re-raise technical errors that should be handled as retryable
                    raise processing_error
            
            # Update retry log with result
            retry_log.completed_at = datetime.utcnow()
            retry_log.duration_ms = int((retry_log.completed_at - retry_start).total_seconds() * 1000)
            
            if result.success:
                retry_log.retry_successful = True
                logger.info(f"✅ RETRY_SUCCESS: {retry_log.transaction_id} completed successfully")
                return {"success": True, "rescheduled": False}
            else:
                retry_log.retry_successful = False
                
                # Check if this result indicates a non-retryable error
                if result.error and any(
                    code.value in result.error or code.name in result.error 
                    for code in self.non_retryable_error_codes
                ):
                    # Non-retryable error - mark as final failure with validation
                    retry_log.final_retry = True
                    
                    try:
                        current_status = UnifiedTransactionStatus(transaction.status)
                        is_valid, reason = UnifiedTransactionStateValidator.validate_transition(
                            current_status,
                            UnifiedTransactionStatus.FAILED,
                            transaction_id=transaction.transaction_id
                        )
                        
                        if is_valid:
                            transaction.status = 'failed'
                            transaction.last_error_code = result.error
                            transaction.updated_at = datetime.utcnow()
                        else:
                            logger.warning(
                                f"🚫 RETRY_TX_FAIL_BLOCKED: {current_status.value}→FAILED "
                                f"for {transaction.transaction_id}: {reason} (transaction already in terminal state)"
                            )
                            # Transaction already in terminal state - acceptable
                    except StateTransitionError as e:
                        logger.warning(
                            f"🚫 RETRY_TX_FAIL_BLOCKED: Invalid transition for "
                            f"{transaction.transaction_id}: {e} (transaction already in terminal state)"
                        )
                        # Transaction already in terminal state - this is OK
                    except Exception as e:
                        logger.error(
                            f"🚫 RETRY_TX_VALIDATION_ERROR: Error validating transition for "
                            f"{transaction.transaction_id}: {e}"
                        )
                        # Fallback for unknown status values
                        transaction.status = 'failed'
                        transaction.last_error_code = result.error
                        transaction.updated_at = datetime.utcnow()
                    
                    logger.warning(f"❌ NON_RETRYABLE_RESULT: {retry_log.transaction_id} failed with user error: {result.error}")
                    return {"success": False, "rescheduled": False, "error": "non_retryable_user_error"}
                
                # Check if we should schedule another retry for retryable errors
                if retry_log.retry_attempt < self.max_retry_attempts:
                    # Schedule next retry
                    next_attempt = retry_log.retry_attempt + 1
                    delay = self._calculate_retry_delay(next_attempt)
                    next_retry_at = datetime.utcnow() + timedelta(seconds=delay)
                    
                    # Create new retry log for next attempt
                    context = RetryContext(
                        transaction_id=retry_log.transaction_id,
                        transaction_type=transaction.transaction_type,
                        user_id=transaction.user_id,
                        amount=transaction.amount,
                        currency=transaction.currency,
                        external_provider=retry_log.external_provider,
                        attempt_number=next_attempt,
                        error_code=result.error or "retry_failed",
                        error_message=result.message or "Retry attempt failed"
                    )
                    
                    await self._log_retry_attempt(
                        context=context,
                        exception=Exception(result.error or "Retry failed"),
                        delay_seconds=delay,
                        next_retry_at=next_retry_at,
                        idempotency_key=self._generate_idempotency_key(context),
                        final_retry=(next_attempt >= self.max_retry_attempts)
                    )
                    
                    logger.info(f"⏰ RETRY_RESCHEDULED: {retry_log.transaction_id} attempt {next_attempt} at {next_retry_at}")
                    return {"success": False, "rescheduled": True}
                else:
                    # Max retries reached
                    retry_log.final_retry = True
                    logger.warning(f"❌ RETRY_EXHAUSTED: {retry_log.transaction_id} failed after {retry_log.retry_attempt} attempts")
                    return {"success": False, "rescheduled": False, "error": "max_retries_exceeded"}
                    
        except Exception as e:
            # Check if this is a non-retryable user error
            error_message = str(e)
            is_non_retryable = any(
                code.value in error_message or code.name in error_message 
                for code in self.non_retryable_error_codes
            )
            
            # Update retry log with error
            retry_log.retry_successful = False
            retry_log.completed_at = datetime.utcnow()
            retry_log.duration_ms = int((retry_log.completed_at - retry_start).total_seconds() * 1000)
            
            if is_non_retryable:
                # Handle non-retryable errors - mark as final failure with validation
                retry_log.final_retry = True
                
                try:
                    current_status = UnifiedTransactionStatus(transaction.status)
                    is_valid, reason = UnifiedTransactionStateValidator.validate_transition(
                        current_status,
                        UnifiedTransactionStatus.FAILED,
                        transaction_id=transaction.transaction_id
                    )
                    
                    if is_valid:
                        transaction.status = 'failed'
                        transaction.last_error_code = error_message
                        transaction.updated_at = datetime.utcnow()
                    else:
                        logger.warning(
                            f"🚫 RETRY_TX_FAIL_BLOCKED: {current_status.value}→FAILED "
                            f"for {transaction.transaction_id}: {reason} (transaction already in terminal state)"
                        )
                        # Transaction already in terminal state - acceptable for retry system
                except StateTransitionError as e:
                    logger.warning(
                        f"🚫 RETRY_TX_FAIL_BLOCKED: Invalid transition for "
                        f"{transaction.transaction_id}: {e} (transaction already in terminal state)"
                    )
                    # Transaction already in terminal state - this is OK for retry system
                except Exception as e:
                    logger.error(
                        f"🚫 RETRY_TX_VALIDATION_ERROR: Error validating transition for "
                        f"{transaction.transaction_id}: {e}"
                    )
                    # Fallback for unknown status values
                    transaction.status = 'failed'
                    transaction.last_error_code = error_message
                    transaction.updated_at = datetime.utcnow()
                
                logger.warning(f"❌ NON_RETRYABLE_USER_ERROR: {retry_log.transaction_id} failed with user error: {error_message}")
                return {"success": False, "rescheduled": False, "error": "non_retryable_user_error"}
            else:
                # Technical error - can be retried
                logger.error(f"❌ RETRY_EXECUTION_ERROR: {retry_log.transaction_id} - {e}")
                return {"success": False, "rescheduled": False, "error": str(e)}


# Global instance for use across the application
unified_retry_service = UnifiedRetryService()