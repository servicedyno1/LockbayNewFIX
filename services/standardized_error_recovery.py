"""
Standardized Error Recovery Service
Provides consistent error handling and recovery mechanisms across NGN and crypto flows
"""

import logging
from typing import Optional, Callable
from enum import Enum
from datetime import datetime, timedelta
from dataclasses import dataclass
from decimal import Decimal

logger = logging.getLogger(__name__)


class ErrorSeverity(Enum):
    """Error severity levels for recovery decisions"""
    LOW = "low"              # Retry automatically
    MEDIUM = "medium"        # Limited retries, then escalate
    HIGH = "high"           # Immediate escalation
    CRITICAL = "critical"   # Emergency protocols


class RecoveryAction(Enum):
    """Available recovery actions"""
    RETRY = "retry"
    REFUND = "refund"
    MANUAL_REVIEW = "manual_review"
    EMERGENCY_CREDIT = "emergency_credit"
    ESCALATE_ADMIN = "escalate_admin"
    WAIT_AND_RETRY = "wait_and_retry"


@dataclass
class ErrorContext:
    """Context information for error recovery decisions"""
    error_type: str
    order_id: str
    user_id: int
    payment_type: str  # 'ngn', 'crypto'
    amount: Decimal
    currency: str
    attempt_count: int
    last_error: str
    order_status: str
    time_since_creation: timedelta
    time_since_last_attempt: timedelta


@dataclass 
class RecoveryResult:
    """Result of error recovery attempt"""
    success: bool
    action_taken: RecoveryAction
    next_retry_at: Optional[datetime]
    escalated: bool
    refund_processed: bool
    message: str
    should_continue: bool


class StandardizedErrorRecovery:
    """Unified error recovery service for all payment flows"""
    
    # Recovery configuration
    MAX_RETRIES = {
        ErrorSeverity.LOW: 5,
        ErrorSeverity.MEDIUM: 3,
        ErrorSeverity.HIGH: 1,
        ErrorSeverity.CRITICAL: 0
    }
    
    RETRY_DELAYS = {
        ErrorSeverity.LOW: [60, 300, 900, 1800, 3600],  # 1min, 5min, 15min, 30min, 1hr
        ErrorSeverity.MEDIUM: [300, 1800, 7200],        # 5min, 30min, 2hr
        ErrorSeverity.HIGH: [1800],                      # 30min
        ErrorSeverity.CRITICAL: []                       # No retries
    }
    
    @classmethod
    async def handle_payment_error(
        cls,
        error_context: ErrorContext,
        error_classification_func: Optional[Callable] = None
    ) -> RecoveryResult:
        """
        Main entry point for standardized error recovery
        
        Args:
            error_context: Context about the error and order
            error_classification_func: Optional custom error classifier
            
        Returns:
            RecoveryResult with recommended actions
        """
        try:
            # 1. Classify error severity
            severity = cls._classify_error(error_context, error_classification_func)
            
            # 2. Determine recovery action based on context and severity
            recovery_action = cls._determine_recovery_action(error_context, severity)
            
            # 3. Execute recovery action
            result = await cls._execute_recovery_action(error_context, recovery_action, severity)
            
            # 4. Log recovery attempt
            cls._log_recovery_attempt(error_context, severity, result)
            
            return result
            
        except Exception as e:
            logger.error(f"Error in standardized error recovery: {e}")
            # Fallback to safe recovery action
            return RecoveryResult(
                success=False,
                action_taken=RecoveryAction.ESCALATE_ADMIN,
                next_retry_at=None,
                escalated=True,
                refund_processed=False,
                message=f"Recovery service failed: {str(e)}",
                should_continue=False
            )
    
    @classmethod
    def _classify_error(
        cls,
        error_context: ErrorContext,
        custom_classifier: Optional[Callable] = None
    ) -> ErrorSeverity:
        """Classify error severity based on context and error type"""
        
        # Use custom classifier if provided
        if custom_classifier:
            try:
                custom_severity = custom_classifier(error_context)
                if custom_severity:
                    return custom_severity
            except Exception as e:
                logger.warning(f"Custom error classifier failed: {e}")
        
        # Standard classification rules
        error_type = error_context.error_type.lower()
        
        # Critical errors - immediate escalation
        if any(keyword in error_type for keyword in [
            'fund_loss', 'double_credit', 'security_breach', 'database_corruption'
        ]):
            return ErrorSeverity.CRITICAL
        
        # High severity - limited recovery options
        if any(keyword in error_type for keyword in [
            'payment_failed_final', 'wallet_access_denied', 'api_authentication_failed',
            'insufficient_balance', 'fraud_detected'
        ]):
            return ErrorSeverity.HIGH
        
        # Medium severity - network and temporary issues
        if any(keyword in error_type for keyword in [
            'network_timeout', 'api_rate_limit', 'service_unavailable',
            'temporary_error', 'rate_limit_exceeded'
        ]):
            return ErrorSeverity.MEDIUM
        
        # Low severity - transient issues
        if any(keyword in error_type for keyword in [
            'connection_error', 'timeout', 'retry_needed', 'temporary_failure'
        ]):
            return ErrorSeverity.LOW
        
        # Default to medium if unknown
        return ErrorSeverity.MEDIUM
    
    @classmethod
    def _determine_recovery_action(
        cls,
        error_context: ErrorContext,
        severity: ErrorSeverity
    ) -> RecoveryAction:
        """Determine the best recovery action based on context and severity"""
        
        # Check if we've exceeded retry limits
        max_retries = cls.MAX_RETRIES[severity]
        if error_context.attempt_count >= max_retries:
            if error_context.time_since_creation > timedelta(hours=24):
                return RecoveryAction.REFUND  # Old orders get refunded
            else:
                return RecoveryAction.MANUAL_REVIEW  # Recent orders need review
        
        # Critical errors need immediate escalation
        if severity == ErrorSeverity.CRITICAL:
            return RecoveryAction.ESCALATE_ADMIN
        
        # High severity errors
        if severity == ErrorSeverity.HIGH:
            if error_context.attempt_count == 0:
                return RecoveryAction.WAIT_AND_RETRY  # First attempt with delay
            else:
                return RecoveryAction.MANUAL_REVIEW  # Escalate after first failure
        
        # Medium and low severity - retry with appropriate delays
        if severity in [ErrorSeverity.MEDIUM, ErrorSeverity.LOW]:
            return RecoveryAction.RETRY
        
        return RecoveryAction.MANUAL_REVIEW  # Default fallback
    
    @classmethod
    async def _execute_recovery_action(
        cls,
        error_context: ErrorContext,
        action: RecoveryAction,
        severity: ErrorSeverity
    ) -> RecoveryResult:
        """Execute the determined recovery action"""
        
        if action == RecoveryAction.RETRY:
            return await cls._handle_retry_action(error_context, severity)
        
        elif action == RecoveryAction.WAIT_AND_RETRY:
            return await cls._handle_wait_and_retry_action(error_context, severity)
        
        elif action == RecoveryAction.REFUND:
            return await cls._handle_refund_action(error_context)
        
        elif action == RecoveryAction.EMERGENCY_CREDIT:
            return await cls._handle_emergency_credit_action(error_context)
        
        elif action == RecoveryAction.MANUAL_REVIEW:
            return await cls._handle_manual_review_action(error_context)
        
        elif action == RecoveryAction.ESCALATE_ADMIN:
            return await cls._handle_admin_escalation_action(error_context, severity)
        
        else:
            return RecoveryResult(
                success=False,
                action_taken=action,
                next_retry_at=None,
                escalated=False,
                refund_processed=False,
                message=f"Unknown recovery action: {action}",
                should_continue=False
            )
    
    @classmethod
    async def _handle_retry_action(
        cls,
        error_context: ErrorContext,
        severity: ErrorSeverity
    ) -> RecoveryResult:
        """Handle immediate retry action"""
        
        # Calculate next retry time based on attempt count and severity
        delays = cls.RETRY_DELAYS[severity]
        if error_context.attempt_count < len(delays):
            delay_seconds = delays[error_context.attempt_count]
            next_retry = datetime.utcnow() + timedelta(seconds=delay_seconds)
        else:
            next_retry = None  # No more retries
        
        return RecoveryResult(
            success=True,
            action_taken=RecoveryAction.RETRY,
            next_retry_at=next_retry,
            escalated=False,
            refund_processed=False,
            message=f"Scheduled retry #{error_context.attempt_count + 1} in {delay_seconds}s",
            should_continue=True
        )
    
    @classmethod
    async def _handle_wait_and_retry_action(
        cls,
        error_context: ErrorContext,
        severity: ErrorSeverity
    ) -> RecoveryResult:
        """Handle wait and retry action with longer delays"""
        
        # Use longer delay for wait_and_retry
        delay_seconds = 1800  # 30 minutes default
        next_retry = datetime.utcnow() + timedelta(seconds=delay_seconds)
        
        return RecoveryResult(
            success=True,
            action_taken=RecoveryAction.WAIT_AND_RETRY,
            next_retry_at=next_retry,
            escalated=False,
            refund_processed=False,
            message=f"Scheduled delayed retry in {delay_seconds}s",
            should_continue=True
        )
    
    @classmethod
    async def _handle_refund_action(cls, error_context: ErrorContext) -> RecoveryResult:
        """Handle automatic refund action"""
        try:
            from services.automatic_refund_service import AutomaticRefundService
            from database import SessionLocal
            
            session = SessionLocal()
            
            # Create a mock order object for the refund service
            class MockOrder:
                def __init__(self, context: ErrorContext):
                    self.id = context.order_id
                    self.user_id = context.user_id
                    self.source_amount = context.amount
                    self.utid = context.order_id
            
            mock_order = MockOrder(error_context)
            
            refund_result = await AutomaticRefundService._process_order_refund(
                order=mock_order,
                order_type="error_recovery",
                refund_reason=f"Automatic refund due to {error_context.error_type} after {error_context.attempt_count} attempts",
                session=session
            )
            
            session.close()
            
            return RecoveryResult(
                success=refund_result["success"],
                action_taken=RecoveryAction.REFUND,
                next_retry_at=None,
                escalated=False,
                refund_processed=refund_result["success"],
                message=f"Refund {'processed' if refund_result['success'] else 'failed'}: {refund_result.get('refund_id', 'N/A')}",
                should_continue=False
            )
            
        except Exception as e:
            logger.error(f"Error processing automatic refund: {e}")
            return RecoveryResult(
                success=False,
                action_taken=RecoveryAction.REFUND,
                next_retry_at=None,
                escalated=True,
                refund_processed=False,
                message=f"Refund failed: {str(e)}",
                should_continue=False
            )
    
    @classmethod
    async def _handle_emergency_credit_action(
        cls,
        error_context: ErrorContext
    ) -> RecoveryResult:
        """Handle emergency wallet credit action"""
        try:
            from services.crypto import CryptoServiceAtomic
            
            # Emergency credit to user wallet
            credit_success = CryptoServiceAtomic.credit_user_wallet_atomic(
                user_id=error_context.user_id,
                amount=float(error_context.amount),
                currency=error_context.currency,
                transaction_type="emergency_recovery",
                description=f"Emergency credit due to {error_context.error_type}"
            )
            
            return RecoveryResult(
                success=credit_success,
                action_taken=RecoveryAction.EMERGENCY_CREDIT,
                next_retry_at=None,
                escalated=True,
                refund_processed=credit_success,
                message=f"Emergency credit {'processed' if credit_success else 'failed'}",
                should_continue=False
            )
            
        except Exception as e:
            logger.error(f"Error processing emergency credit: {e}")
            return RecoveryResult(
                success=False,
                action_taken=RecoveryAction.EMERGENCY_CREDIT,
                next_retry_at=None,
                escalated=True,
                refund_processed=False,
                message=f"Emergency credit failed: {str(e)}",
                should_continue=False
            )
    
    @classmethod
    async def _handle_manual_review_action(
        cls,
        error_context: ErrorContext
    ) -> RecoveryResult:
        """Handle manual review escalation"""
        try:
            from services.consolidated_notification_service import consolidated_notification_service
            
            # Send admin alert for manual review
            await consolidated_notification_service.send_admin_alert(
                f"🔍 MANUAL_REVIEW_REQUIRED\n"
                f"Order: {error_context.order_id}\n"
                f"User: {error_context.user_id}\n"
                f"Amount: {error_context.amount} {error_context.currency}\n"
                f"Error: {error_context.error_type}\n"
                f"Attempts: {error_context.attempt_count}\n"
                f"Status: {error_context.order_status}\n"
                f"Payment Type: {error_context.payment_type}\n\n"
                f"Manual intervention required!"
            )
            
            return RecoveryResult(
                success=True,
                action_taken=RecoveryAction.MANUAL_REVIEW,
                next_retry_at=None,
                escalated=True,
                refund_processed=False,
                message="Order escalated for manual review",
                should_continue=False
            )
            
        except Exception as e:
            logger.error(f"Error escalating for manual review: {e}")
            return RecoveryResult(
                success=False,
                action_taken=RecoveryAction.MANUAL_REVIEW,
                next_retry_at=None,
                escalated=True,
                refund_processed=False,
                message=f"Manual review escalation failed: {str(e)}",
                should_continue=False
            )
    
    @classmethod
    async def _handle_admin_escalation_action(
        cls,
        error_context: ErrorContext,
        severity: ErrorSeverity
    ) -> RecoveryResult:
        """Handle critical admin escalation"""
        try:
            from services.consolidated_notification_service import consolidated_notification_service
            
            # Send urgent admin alert
            await consolidated_notification_service.send_admin_alert(
                f"🚨 CRITICAL_ERROR_ESCALATION\n"
                f"Severity: {severity.value.upper()}\n"
                f"Order: {error_context.order_id}\n"
                f"User: {error_context.user_id}\n"
                f"Amount: {error_context.amount} {error_context.currency}\n"
                f"Error: {error_context.error_type}\n"
                f"Last Error: {error_context.last_error}\n"
                f"Payment Type: {error_context.payment_type}\n\n"
                f"IMMEDIATE ATTENTION REQUIRED!"
            )
            
            return RecoveryResult(
                success=True,
                action_taken=RecoveryAction.ESCALATE_ADMIN,
                next_retry_at=None,
                escalated=True,
                refund_processed=False,
                message="Critical error escalated to admin",
                should_continue=False
            )
            
        except Exception as e:
            logger.error(f"Error escalating to admin: {e}")
            return RecoveryResult(
                success=False,
                action_taken=RecoveryAction.ESCALATE_ADMIN,
                next_retry_at=None,
                escalated=True,
                refund_processed=False,
                message=f"Admin escalation failed: {str(e)}",
                should_continue=False
            )
    
    @classmethod
    def _log_recovery_attempt(
        cls,
        error_context: ErrorContext,
        severity: ErrorSeverity,
        result: RecoveryResult
    ):
        """Log recovery attempt for audit trail"""
        log_message = (
            f"STANDARDIZED_ERROR_RECOVERY: Order {error_context.order_id} "
            f"| Severity: {severity.value} "
            f"| Action: {result.action_taken.value} "
            f"| Success: {result.success} "
            f"| Escalated: {result.escalated} "
            f"| Should Continue: {result.should_continue}"
        )
        
        if result.success:
            logger.info(f"✅ {log_message}")
        else:
            logger.warning(f"⚠️ {log_message}")


# Convenience function for easy integration
async def recover_from_payment_error(
    order_id: str,
    user_id: int,
    error_type: str,
    last_error: str,
    payment_type: str,
    amount: Decimal,
    currency: str,
    attempt_count: int,
    order_status: str,
    time_since_creation: timedelta,
    time_since_last_attempt: timedelta
) -> RecoveryResult:
    """
    Convenience function for standardized error recovery
    """
    error_context = ErrorContext(
        error_type=error_type,
        order_id=order_id,
        user_id=user_id,
        payment_type=payment_type,
        amount=amount,
        currency=currency,
        attempt_count=attempt_count,
        last_error=last_error,
        order_status=order_status,
        time_since_creation=time_since_creation,
        time_since_last_attempt=time_since_last_attempt
    )
    
    return await StandardizedErrorRecovery.handle_payment_error(error_context)