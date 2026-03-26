#!/usr/bin/env python3
"""
Unified Error Handler Migration

Replaces complex error handling chains with the new unified error handling system.
This bridges the gap between existing error handling code and the new simplified
3-category error classification system.

Consolidates:
- Multiple competing error classifiers (MinimalClassifier, APIAdapterRetry, etc.)
- Complex error transformation chains that lose original error information
- Inconsistent retry logic across different services
- Overlapping error categorization systems

Into a single, unified error handling interface.
"""

import logging
from typing import Dict, Any, Optional
from decimal import Decimal
from datetime import datetime

# Import the new unified error handling
from services.core.unified_error_handler import (
    unified_error_handler
)

# Import existing error handling for backward compatibility
from services.minimal_classifier import MinimalClassifier

logger = logging.getLogger(__name__)


class UnifiedErrorMigrationBridge:
    """
    Migration bridge for error handling that gradually replaces complex
    error classification chains with the unified 3-category system.
    """
    
    def __init__(self):
        """Initialize the error migration bridge"""
        self.use_unified = True  # Flag to control migration
        logger.info("🔄 UnifiedErrorMigrationBridge initialized")
    
    def classify_error_unified(
        self,
        error: Exception,
        provider: str,
        operation: str,
        context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Classify error using unified system with fallback to legacy classifiers
        
        Args:
            error: The exception to classify
            provider: Provider that caused the error (fincra, kraken, blockbee)
            operation: Operation being performed (payout, payin, balance_check)
            context: Additional context for classification
            
        Returns:
            Dict with unified error classification
        """
        try:
            if self.use_unified:
                # Try unified error handler first
                logger.debug(f"🔄 UNIFIED_ERROR_CLASSIFICATION: Classifying {type(error).__name__} from {provider}")
                
                classification = unified_error_handler.classify_error(
                    error=error,
                    provider=provider,
                    operation=operation,
                    context=context or {}
                )
                
                return {
                    "success": True,
                    "category": classification.category.value,
                    "should_retry": classification.should_retry,
                    "retry_delay_seconds": classification.retry_delay_seconds,
                    "max_retries": classification.max_retries,
                    "user_message": classification.user_message,
                    "admin_message": classification.admin_message,
                    "original_error": classification.original_error,
                    "provider": classification.provider,
                    "error_code": classification.error_code,
                    "unified_classification": True
                }
            
            # Fallback to legacy classifier
            logger.debug(f"🔄 LEGACY_ERROR_CLASSIFICATION: Using MinimalClassifier for {provider}")
            return self._use_legacy_classifier(error, provider, operation, context)
            
        except Exception as e:
            logger.error(f"❌ Error classification failed: {e}")
            # Return safe default classification
            return {
                "success": False,
                "category": "technical",
                "should_retry": False,
                "retry_delay_seconds": 300,
                "max_retries": 3,
                "user_message": "An error occurred while processing your request",
                "admin_message": f"Error classification failed: {str(e)}",
                "original_error": str(error),
                "provider": provider,
                "error_code": None,
                "unified_classification": False
            }
    
    def _use_legacy_classifier(
        self,
        error: Exception,
        provider: str,
        operation: str,
        context: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Use legacy MinimalClassifier as fallback
        
        Converts legacy classification to unified format for consistency.
        """
        try:
            error_input = {
                'error_code': getattr(error, 'code', ''),
                'error_message': str(error),
                'external_provider': provider
            }
            
            is_retryable = MinimalClassifier.is_retryable_technical(error_input)
            
            # Map legacy classification to unified categories
            if is_retryable:
                category = "technical"
                should_retry = True
                retry_delay = 300  # 5 minutes
                max_retries = 3
                user_message = "Temporary issue - please try again later"
            else:
                category = "business"
                should_retry = False
                retry_delay = 0
                max_retries = 0
                user_message = "Please check your request and try again"
            
            return {
                "success": True,
                "category": category,
                "should_retry": should_retry,
                "retry_delay_seconds": retry_delay,
                "max_retries": max_retries,
                "user_message": user_message,
                "admin_message": f"Legacy classification: {str(error)}",
                "original_error": str(error),
                "provider": provider,
                "error_code": getattr(error, 'code', None),
                "unified_classification": False
            }
            
        except Exception as e:
            logger.error(f"❌ Legacy error classification failed: {e}")
            # Return safe default
            return {
                "success": False,
                "category": "technical",
                "should_retry": False,
                "retry_delay_seconds": 300,
                "max_retries": 1,
                "user_message": "An error occurred while processing your request",
                "admin_message": f"Legacy classification failed: {str(e)}",
                "original_error": str(error),
                "provider": provider,
                "error_code": None,
                "unified_classification": False
            }
    
    def should_retry_operation(
        self,
        error: Exception,
        provider: str,
        operation: str,
        attempt_count: int = 1,
        context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Determine if an operation should be retried based on unified classification
        
        Args:
            error: The exception that occurred
            provider: Provider that caused the error
            operation: Operation that failed
            attempt_count: Current attempt number
            context: Additional context
            
        Returns:
            Dict with retry decision and parameters
        """
        try:
            classification = self.classify_error_unified(error, provider, operation, context)
            
            should_retry = (
                classification.get("should_retry", False) and
                attempt_count < classification.get("max_retries", 0)
            )
            
            next_delay = classification.get("retry_delay_seconds", 300)
            if attempt_count > 1:
                # Exponential backoff for subsequent retries
                next_delay = min(next_delay * (2 ** (attempt_count - 1)), 1800)  # Max 30 minutes
            
            result = {
                "should_retry": should_retry,
                "retry_delay_seconds": next_delay,
                "attempts_remaining": max(0, classification.get("max_retries", 0) - attempt_count),
                "category": classification.get("category"),
                "user_message": classification.get("user_message"),
                "admin_message": classification.get("admin_message"),
                "classification": classification
            }
            
            logger.info(
                f"🔄 RETRY_DECISION: {provider} {operation} - "
                f"Retry: {should_retry}, Delay: {next_delay}s, "
                f"Attempts remaining: {result['attempts_remaining']}"
            )
            
            return result
            
        except Exception as e:
            logger.error(f"❌ Retry decision failed: {e}")
            return {
                "should_retry": False,
                "retry_delay_seconds": 0,
                "attempts_remaining": 0,
                "category": "technical",
                "user_message": "An error occurred while processing your request",
                "admin_message": f"Retry decision failed: {str(e)}"
            }
    
    def handle_payment_error(
        self,
        error: Exception,
        payment_type: str,
        provider: str,
        user_id: int,
        amount: Decimal,
        currency: str,
        context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Handle payment errors with unified classification and appropriate actions
        
        Consolidates payment error handling across all providers and payment types.
        """
        try:
            # Enhance context with payment details
            enhanced_context = {
                "payment_type": payment_type,
                "user_id": user_id,
                "amount": float(amount),
                "currency": currency,
                "timestamp": datetime.utcnow().isoformat()
            }
            if context:
                enhanced_context.update(context)
            
            classification = self.classify_error_unified(
                error=error,
                provider=provider,
                operation=payment_type,
                context=enhanced_context
            )
            
            # Determine appropriate action based on classification
            action = self._determine_payment_error_action(
                classification, payment_type, provider, amount, currency
            )
            
            logger.error(
                f"❌ PAYMENT_ERROR_HANDLED: {provider} {payment_type} for user {user_id} - "
                f"Category: {classification.get('category')}, Action: {action['action']}"
            )
            
            return {
                "success": False,
                "error_handled": True,
                "classification": classification,
                "recommended_action": action,
                "user_message": classification.get("user_message"),
                "admin_message": classification.get("admin_message"),
                "should_retry": classification.get("should_retry", False),
                "retry_delay": classification.get("retry_delay_seconds", 0)
            }
            
        except Exception as e:
            logger.error(f"❌ Payment error handling failed: {e}")
            return {
                "success": False,
                "error_handled": False,
                "user_message": "An error occurred while processing your payment",
                "admin_message": f"Payment error handling failed: {str(e)}",
                "should_retry": False,
                "retry_delay": 0
            }
    
    def _determine_payment_error_action(
        self,
        classification: Dict[str, Any],
        payment_type: str,
        provider: str,
        amount: Decimal,
        currency: str
    ) -> Dict[str, Any]:
        """
        Determine the appropriate action based on error classification
        """
        category = classification.get("category", "technical")
        
        if category == "technical":
            # Technical errors - retry automatically
            return {
                "action": "retry_automatic",
                "reason": "Temporary technical issue",
                "admin_notification": False,
                "user_notification": False
            }
        
        elif category == "business":
            # Business errors - user action required
            action = "user_action_required"
            admin_notification = False
            
            # For high-value transactions, notify admin
            if amount > 1000:  # $1000 or equivalent
                admin_notification = True
                
            return {
                "action": action,
                "reason": "User input or account issue",
                "admin_notification": admin_notification,
                "user_notification": True
            }
        
        else:  # permanent
            # Permanent errors - admin intervention needed
            return {
                "action": "admin_intervention",
                "reason": "Configuration or permanent issue",
                "admin_notification": True,
                "user_notification": True
            }
    
    def set_unified_mode(self, enabled: bool):
        """Enable or disable unified error handling mode"""
        self.use_unified = enabled
        logger.info(f"🔄 Unified error handling mode: {'ENABLED' if enabled else 'DISABLED'}")


# Global migration bridge instance
error_migration_bridge = UnifiedErrorMigrationBridge()


# Convenience functions for easy migration
def classify_payment_error(
    error: Exception,
    provider: str,
    operation: str,
    context: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Unified error classification for payment operations"""
    return error_migration_bridge.classify_error_unified(error, provider, operation, context)


def should_retry_payment(
    error: Exception,
    provider: str,
    operation: str,
    attempt_count: int = 1
) -> Dict[str, Any]:
    """Unified retry decision for payment operations"""
    return error_migration_bridge.should_retry_operation(
        error, provider, operation, attempt_count
    )


def handle_payment_error_unified(
    error: Exception,
    payment_type: str,
    provider: str,
    user_id: int,
    amount: Decimal,
    currency: str
) -> Dict[str, Any]:
    """Unified payment error handling"""
    return error_migration_bridge.handle_payment_error(
        error, payment_type, provider, user_id, amount, currency
    )


# Export main components
__all__ = [
    'UnifiedErrorMigrationBridge',
    'error_migration_bridge',
    'classify_payment_error',
    'should_retry_payment',
    'handle_payment_error_unified'
]