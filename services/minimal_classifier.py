"""
Minimal Error Classification Service
Simple binary classification: Technical transient errors vs Everything else

Only 4 error types get automatic retry:
- NETWORK_ERROR: Network connectivity issues, SSL errors
- API_TIMEOUT: API call timeouts from external providers
- SERVICE_UNAVAILABLE: 502, 503, 504 errors from external services
- RATE_LIMIT_EXCEEDED: 429 errors from external APIs

Everything else goes to admin review:
- User errors (insufficient funds, invalid addresses)
- Authentication failures
- Business logic errors
- Unknown errors

This replaces the complex 50+ error code classification system.
"""

import logging
import re
from typing import Union, Dict, Any, Optional
from models import CashoutErrorCode

logger = logging.getLogger(__name__)


class MinimalClassifier:
    """
    Simple binary error classifier for streamlined failure handling.
    
    Philosophy: Most failures need human review. Only obvious transient 
    technical errors get automatic retry (exactly once).
    """
    
    # The only 4 error patterns that trigger automatic retry
    TECHNICAL_TRANSIENT_PATTERNS = {
        # Network connectivity issues
        r"network.*error|connection.*error|connection.*timeout": "NETWORK_ERROR",
        r"ssl.*error|ssl.*failed|certificate.*error": "NETWORK_ERROR",
        r"socket.*error|connection.*refused|connection.*reset": "NETWORK_ERROR",
        
        # API timeout errors
        r"timeout|timed.*out|request.*timeout": "API_TIMEOUT",
        r"kraken.*timeout|fincra.*timeout|blockbee.*timeout": "API_TIMEOUT",
        r"fastforex.*timeout|api.*timeout": "API_TIMEOUT",
        
        # Service unavailable errors
        r"service.*unavailable|502|503|504": "SERVICE_UNAVAILABLE",
        r"bad.*gateway|gateway.*timeout|server.*error": "SERVICE_UNAVAILABLE",
        r"maintenance.*mode|temporarily.*unavailable": "SERVICE_UNAVAILABLE",
        
        # Rate limiting
        r"rate.*limit|too.*many.*requests|429": "RATE_LIMIT_EXCEEDED",
        r"request.*limit.*exceeded|quota.*exceeded": "RATE_LIMIT_EXCEEDED",
    }
    
    # Crypto-specific error patterns that need immediate admin attention (non-retryable)
    CRYPTO_ADMIN_REVIEW_PATTERNS = {
        # Address validation errors - admin needs to verify addresses
        r"invalid.*address|address.*invalid|address.*not.*found": "INVALID_ADDRESS",
        r"address.*format.*invalid|malformed.*address": "INVALID_ADDRESS",
        r"destination.*address.*error|bad.*address.*format": "INVALID_ADDRESS",
        
        # Blockchain network issues - admin needs to investigate
        r"blockchain.*error|network.*not.*supported": "NETWORK_ERROR",
        r"chain.*error|fork.*detected|consensus.*error": "NETWORK_ERROR",
        
        # Crypto exchange specific errors - admin review needed
        r"withdrawal.*suspended|withdrawal.*disabled": "SERVICE_UNAVAILABLE",
        r"asset.*suspended|trading.*halted": "SERVICE_UNAVAILABLE",
        r"kraken.*maintenance|exchange.*maintenance": "SERVICE_UNAVAILABLE",
        
        # Fee related errors - admin needs to adjust fee settings
        r"insufficient.*fee|fee.*too.*low": "INSUFFICIENT_FUNDS",
        r"fee.*estimation.*failed|priority.*fee.*required": "INSUFFICIENT_FUNDS",
        
        # Account/permission issues - admin needs to check exchange configuration
        r"account.*suspended|permissions.*insufficient": "API_AUTHENTICATION_FAILED",
        r"withdrawal.*limit.*exceeded|daily.*limit": "MAX_AMOUNT_EXCEEDED",
        r"api.*key.*invalid|authentication.*failed": "API_AUTHENTICATION_FAILED",
        
        # Balance and funding issues - admin funding required
        r"insufficient.*balance|low.*balance": "INSUFFICIENT_FUNDS", 
        r"kraken.*insufficient|balance.*too.*low": "INSUFFICIENT_FUNDS",
        r"not.*enough.*funds|funds.*unavailable": "INSUFFICIENT_FUNDS",
    }
    
    # Error codes that are definitely retryable (technical transient)
    RETRYABLE_ERROR_CODES = {
        CashoutErrorCode.NETWORK_ERROR.value,
        CashoutErrorCode.API_TIMEOUT.value,
        CashoutErrorCode.SERVICE_UNAVAILABLE.value,
        CashoutErrorCode.RATE_LIMIT_EXCEEDED.value,
        
        # Legacy specific codes that map to the 4 core types
        CashoutErrorCode.SSL_ERROR.value,  # Maps to NETWORK_ERROR
        CashoutErrorCode.KRAKEN_API_TIMEOUT.value,  # Maps to API_TIMEOUT
        CashoutErrorCode.FINCRA_API_TIMEOUT.value,  # Maps to API_TIMEOUT
    }
    
    # All other error codes go to admin review (non-retryable)
    NON_RETRYABLE_ERROR_CODES = {
        # User errors
        CashoutErrorCode.INSUFFICIENT_FUNDS.value,
        CashoutErrorCode.INVALID_ADDRESS.value,
        CashoutErrorCode.INVALID_AMOUNT.value,
        CashoutErrorCode.MIN_AMOUNT_NOT_MET.value,
        CashoutErrorCode.MAX_AMOUNT_EXCEEDED.value,
        CashoutErrorCode.WALLET_INSUFFICIENT_BALANCE.value,
        
        # Authentication and authorization
        CashoutErrorCode.API_AUTHENTICATION_FAILED.value,
        CashoutErrorCode.ACCOUNT_FROZEN.value,
        CashoutErrorCode.SANCTIONS_BLOCKED.value,
        CashoutErrorCode.CURRENCY_NOT_SUPPORTED.value,
        
        # Business logic errors that need admin review
        CashoutErrorCode.API_INSUFFICIENT_FUNDS.value,  # Service funding issue - admin needs to top up
        CashoutErrorCode.FINCRA_INSUFFICIENT_FUNDS.value,  # Service funding issue - admin needs to top up
        CashoutErrorCode.KRAKEN_ADDR_NOT_FOUND.value,  # Admin needs to add address to Kraken
        CashoutErrorCode.API_INVALID_REQUEST.value,  # Bad request - needs investigation
        
        # Complex errors that need human review
        CashoutErrorCode.METADATA_PARSE_ERROR.value,
        CashoutErrorCode.DATABASE_ERROR.value,
        CashoutErrorCode.CIRCUIT_BREAKER_OPEN.value,
    }
    
    @classmethod
    def _extract_error_string(cls, error_input: Union[str, Exception, Dict[str, Any]]) -> str:
        """Extract error string from different input types."""
        if isinstance(error_input, str):
            return error_input
        elif isinstance(error_input, Exception):
            return str(error_input)
        elif isinstance(error_input, dict):
            return error_input.get('error_message', str(error_input))
        else:
            return str(error_input)
    
    @classmethod
    def classify_crypto_error(cls, error_input: Union[str, Exception, Dict[str, Any]], currency: str = None) -> str:
        """
        Classify crypto-specific errors for better admin handling.
        
        Args:
            error_input: Error message, exception, or error dict
            currency: Cryptocurrency being processed (BTC, ETH, etc.)
            
        Returns:
            Error classification string for crypto scenarios
        """
        error_str = cls._extract_error_string(error_input).lower()
        
        # Check crypto-specific patterns first
        for pattern, error_type in cls.CRYPTO_ADMIN_REVIEW_PATTERNS.items():
            if re.search(pattern, error_str, re.IGNORECASE):
                logger.info(f"🔍 CRYPTO_ERROR_CLASSIFIED: '{error_str[:100]}...' → {error_type}")
                return error_type
        
        # Fall back to checking if it's a technical transient error
        if cls.is_retryable_technical(error_input):
            return "TECHNICAL_TRANSIENT"
        
        # Default to requiring admin review
        return "ADMIN_REVIEW_REQUIRED"
    
    @classmethod
    def get_crypto_error_summary(cls, error_input: Union[str, Exception, Dict[str, Any]], currency: str = None) -> Dict[str, Any]:
        """
        Get detailed crypto error classification for enhanced admin handling.
        
        Args:
            error_input: Error to classify
            currency: Cryptocurrency being processed
            
        Returns:
            dict: Enhanced classification details for crypto scenarios
        """
        classification = cls.classify_crypto_error(error_input, currency)
        is_retryable = classification == "TECHNICAL_TRANSIENT"
        
        return {
            "is_retryable": is_retryable,
            "classification": classification,
            "currency": currency,
            "routing": "automatic_retry" if is_retryable else "admin_review",
            "requires_funding": "INSUFFICIENT_FUNDS" in classification,
            "requires_address_review": "INVALID_ADDRESS" in classification,
            "crypto_specific": True,
            "error_input": str(error_input),
            "system": "minimal_classifier_crypto_v1"
        }
    
    @classmethod
    def is_retryable_technical(cls, error_input: Union[str, Exception, Dict[str, Any]]) -> bool:
        """
        Determine if an error should trigger automatic retry.
        
        Returns True only for the 4 technical transient error types:
        - NETWORK_ERROR
        - API_TIMEOUT  
        - SERVICE_UNAVAILABLE
        - RATE_LIMIT_EXCEEDED
        
        Args:
            error_input: Error code string, exception object, or error details dict
            
        Returns:
            bool: True if error should be automatically retried, False otherwise
        """
        try:
            # Handle different input types
            if isinstance(error_input, str):
                error_message = error_input.lower()
                error_code = error_input
            elif isinstance(error_input, Exception):
                error_message = str(error_input).lower()
                error_code = type(error_input).__name__
            elif isinstance(error_input, dict):
                error_message = error_input.get('error_message', '').lower()
                error_code = error_input.get('error_code', '')
            else:
                logger.warning(f"⚠️ MINIMAL_CLASSIFIER: Unknown error input type: {type(error_input)}")
                return False  # Unknown format - send to admin review
            
            # First check: Direct error code match
            if error_code in cls.RETRYABLE_ERROR_CODES:
                logger.info(f"✅ MINIMAL_CLASSIFIER: {error_code} is retryable (direct code match)")
                return True
            
            # Second check: Explicit non-retryable code
            if error_code in cls.NON_RETRYABLE_ERROR_CODES:
                logger.info(f"❌ MINIMAL_CLASSIFIER: {error_code} is non-retryable (admin review required)")
                return False
            
            # Third check: Pattern matching in error message
            for pattern, error_type in cls.TECHNICAL_TRANSIENT_PATTERNS.items():
                if re.search(pattern, error_message, re.IGNORECASE):
                    logger.info(f"✅ MINIMAL_CLASSIFIER: '{error_message}' matches {error_type} pattern - retryable")
                    return True
            
            # Default: Send to admin review
            logger.info(f"❌ MINIMAL_CLASSIFIER: '{error_message}' (code: {error_code}) - admin review required")
            return False
            
        except Exception as e:
            logger.error(f"❌ MINIMAL_CLASSIFIER: Error during classification: {e}")
            return False  # On error, default to admin review
    
    @classmethod
    def get_classification_summary(cls, error_input: Union[str, Exception, Dict[str, Any]]) -> Dict[str, Any]:
        """
        Get detailed classification information for logging and debugging.
        
        Args:
            error_input: Error to classify
            
        Returns:
            dict: Classification details including decision, reason, and routing
        """
        is_retryable = cls.is_retryable_technical(error_input)
        
        return {
            "is_retryable": is_retryable,
            "routing": "automatic_retry" if is_retryable else "admin_review",
            "max_retry_attempts": 1 if is_retryable else 0,
            "retry_delay_minutes": 10 if is_retryable else None,
            "classification": "technical_transient" if is_retryable else "requires_human_review",
            "error_input": str(error_input),
            "system": "minimal_classifier_v1"
        }
    
    @classmethod
    def log_classification(cls, error_input: Union[str, Exception, Dict[str, Any]], 
                          context: Optional[Dict[str, Any]] = None) -> None:
        """
        Log error classification decision with context.
        
        Args:
            error_input: Error that was classified
            context: Additional context (transaction_id, user_id, etc.)
        """
        classification = cls.get_classification_summary(error_input)
        
        log_data = {
            "classification": classification,
            "context": context or {}
        }
        
        if classification["is_retryable"]:
            logger.info("🔄 MINIMAL_CLASSIFIER: TECHNICAL_TRANSIENT → 1 retry in 10min", extra=log_data)
        else:
            logger.info("👨‍💼 MINIMAL_CLASSIFIER: ADMIN_REVIEW_REQUIRED → No automatic retry", extra=log_data)


# Convenience functions for backward compatibility
def is_retryable_technical(error_input: Union[str, Exception, Dict[str, Any]]) -> bool:
    """Convenience function - determine if error should trigger automatic retry."""
    return MinimalClassifier.is_retryable_technical(error_input)


def classify_error(error_input: Union[str, Exception, Dict[str, Any]]) -> Dict[str, Any]:
    """Convenience function - get full classification details."""
    return MinimalClassifier.get_classification_summary(error_input)


# Module-level logger for integration testing
logger.info("🔧 MINIMAL_CLASSIFIER: Module loaded - streamlined failure handling active")