"""
Unified Error Handling System

Replaces multiple competing error classifiers with a simple 3-category system:
- TECHNICAL: Network, timeout, temporary API failures → Retry automatically
- BUSINESS: Insufficient funds, invalid details → Admin/user action needed  
- PERMANENT: Invalid config, unsupported operations → Don't retry, fix config

This eliminates error transformation bugs and provides consistent error handling
across all payment providers (Fincra, Kraken, BlockBee).
"""

import logging
import asyncio
from abc import ABC, abstractmethod
from enum import Enum
from typing import Dict, Any, Optional, Callable, TypeVar
from datetime import datetime
from dataclasses import dataclass
from functools import wraps

logger = logging.getLogger(__name__)
T = TypeVar('T')


class UnifiedErrorCategory(Enum):
    """
    Simple 3-category error classification system.
    Replaces complex 50+ error code systems with clear categories.
    """
    TECHNICAL = "technical"    # Retry automatically - network, timeout, temporary failures
    BUSINESS = "business"      # Admin/user action needed - insufficient funds, invalid details
    PERMANENT = "permanent"    # Don't retry - invalid config, unsupported operations


@dataclass
class ErrorClassification:
    """
    Result of unified error classification.
    Contains all information needed for error handling and retry decisions.
    """
    category: UnifiedErrorCategory
    should_retry: bool
    retry_delay_seconds: int
    max_retries: int
    user_message: str  # Clean message for end users
    admin_message: str  # Detailed message for admin/logs
    original_error: str  # Preserve original error for debugging
    provider: Optional[str] = None  # Which provider caused the error
    error_code: Optional[str] = None  # Provider-specific error code
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for logging and serialization"""
        return {
            'category': self.category.value,
            'should_retry': self.should_retry,
            'retry_delay_seconds': self.retry_delay_seconds,
            'max_retries': self.max_retries,
            'user_message': self.user_message,
            'admin_message': self.admin_message,
            'original_error': self.original_error,
            'provider': self.provider,
            'error_code': self.error_code
        }


class ProviderErrorMapper(ABC):
    """
    Abstract base class for provider-specific error mapping.
    Each payment provider implements this to map their specific errors
    to unified categories.
    """
    
    @abstractmethod
    def map_error(self, exception: Exception, context: Optional[Dict] = None) -> ErrorClassification:
        """
        Map provider-specific error to unified classification.
        
        Args:
            exception: Original exception from provider
            context: Additional context (operation, amount, etc.)
            
        Returns:
            ErrorClassification with category and retry information
        """
        pass
    
    @abstractmethod
    def get_provider_name(self) -> str:
        """Return the provider name for logging and identification"""
        pass


class FincraErrorMapper(ProviderErrorMapper):
    """Error mapper for Fincra NGN payment provider"""
    
    def get_provider_name(self) -> str:
        return "fincra"
    
    def map_error(self, exception: Exception, context: Optional[Dict] = None) -> ErrorClassification:
        """Map Fincra-specific errors to unified categories"""
        error_message = str(exception).lower()
        
        # TECHNICAL errors - retry automatically
        if any(pattern in error_message for pattern in [
            'timeout', 'timed out', 'connection error', 'network error',
            'ssl error', 'certificate error', 'service unavailable',
            '502', '503', '504', 'rate limit', 'too many requests'
        ]):
            return ErrorClassification(
                category=UnifiedErrorCategory.TECHNICAL,
                should_retry=True,
                retry_delay_seconds=300,  # 5 minutes
                max_retries=3,
                user_message="Payment processing temporarily unavailable. Please try again in a few minutes.",
                admin_message=f"Fincra technical error: {str(exception)}",
                original_error=str(exception),
                provider="fincra"
            )
        
        # BUSINESS errors - admin/user action needed
        if any(pattern in error_message for pattern in [
            'no_enough_money_in_wallet', 'insufficient funds in wallet',
            'insufficient balance', 'balance too low'
        ]):
            return ErrorClassification(
                category=UnifiedErrorCategory.BUSINESS,
                should_retry=False,
                retry_delay_seconds=0,
                max_retries=0,
                user_message="Your withdrawal is being processed. You'll be notified when it's complete.",
                admin_message=f"Fincra insufficient funds - admin funding needed: {str(exception)}",
                original_error=str(exception),
                provider="fincra",
                error_code="INSUFFICIENT_FUNDS"
            )
        
        if any(pattern in error_message for pattern in [
            'invalid account number', 'invalid bank code', 'bad request',
            'invalid request', 'malformed request'
        ]):
            return ErrorClassification(
                category=UnifiedErrorCategory.BUSINESS,
                should_retry=False,
                retry_delay_seconds=0,
                max_retries=0,
                user_message="Invalid payment details. Please check your account information.",
                admin_message=f"Fincra invalid request - user data issue: {str(exception)}",
                original_error=str(exception),
                provider="fincra",
                error_code="INVALID_REQUEST"
            )
        
        # PERMANENT errors - don't retry, fix config
        if any(pattern in error_message for pattern in [
            'authentication failed', 'invalid api key', 'unauthorized',
            'forbidden', 'access denied'
        ]):
            return ErrorClassification(
                category=UnifiedErrorCategory.PERMANENT,
                should_retry=False,
                retry_delay_seconds=0,
                max_retries=0,
                user_message="Payment system configuration error. Please contact support.",
                admin_message=f"Fincra authentication failed - check API keys: {str(exception)}",
                original_error=str(exception),
                provider="fincra",
                error_code="AUTH_FAILED"
            )
        
        # Default to TECHNICAL for unknown Fincra errors
        return ErrorClassification(
            category=UnifiedErrorCategory.TECHNICAL,
            should_retry=True,
            retry_delay_seconds=600,  # 10 minutes for unknown errors
            max_retries=2,
            user_message="Payment processing error. Please try again later.",
            admin_message=f"Fincra unknown error: {str(exception)}",
            original_error=str(exception),
            provider="fincra",
            error_code="UNKNOWN"
        )


class KrakenErrorMapper(ProviderErrorMapper):
    """Error mapper for Kraken crypto exchange provider"""
    
    def get_provider_name(self) -> str:
        return "kraken"
    
    def map_error(self, exception: Exception, context: Optional[Dict] = None) -> ErrorClassification:
        """Map Kraken-specific errors to unified categories"""
        error_message = str(exception).lower()
        
        # TECHNICAL errors - retry automatically
        if any(pattern in error_message for pattern in [
            'timeout', 'timed out', 'connection error', 'network error',
            'ssl error', 'service unavailable', '502', '503', '504',
            'rate limit', 'too many requests', 'nonce'
        ]):
            return ErrorClassification(
                category=UnifiedErrorCategory.TECHNICAL,
                should_retry=True,
                retry_delay_seconds=180,  # 3 minutes for faster crypto recovery
                max_retries=4,
                user_message="Crypto exchange temporarily unavailable. Please try again shortly.",
                admin_message=f"Kraken technical error: {str(exception)}",
                original_error=str(exception),
                provider="kraken"
            )
        
        # BUSINESS errors - admin action needed
        if any(pattern in error_message for pattern in [
            'eapi:invalid key', 'unknown withdraw key', 'address not found',
            'address not configured', 'address not verified'
        ]):
            return ErrorClassification(
                category=UnifiedErrorCategory.BUSINESS,
                should_retry=False,
                retry_delay_seconds=0,
                max_retries=0,
                user_message="Your withdrawal is being processed. You'll be notified when it's complete.",
                admin_message=f"Kraken address not found - admin needs to add address: {str(exception)}",
                original_error=str(exception),
                provider="kraken",
                error_code="ADDRESS_NOT_CONFIGURED"
            )
        
        if any(pattern in error_message for pattern in [
            'insufficient funds', 'balance too low', 'not enough funds'
        ]):
            return ErrorClassification(
                category=UnifiedErrorCategory.BUSINESS,
                should_retry=False,
                retry_delay_seconds=0,
                max_retries=0,
                user_message="Your withdrawal is being processed. You'll be notified when it's complete.",
                admin_message=f"Kraken insufficient funds - admin funding needed: {str(exception)}",
                original_error=str(exception),
                provider="kraken",
                error_code="INSUFFICIENT_FUNDS"
            )
        
        # PERMANENT errors - don't retry, fix config
        if any(pattern in error_message for pattern in [
            'invalid address format', 'malformed address', 'invalid currency',
            'unsupported currency', 'permissions insufficient'
        ]):
            return ErrorClassification(
                category=UnifiedErrorCategory.PERMANENT,
                should_retry=False,
                retry_delay_seconds=0,
                max_retries=0,
                user_message="Invalid withdrawal configuration. Please contact support.",
                admin_message=f"Kraken permanent error - fix configuration: {str(exception)}",
                original_error=str(exception),
                provider="kraken",
                error_code="CONFIG_ERROR"
            )
        
        # Default to TECHNICAL for unknown Kraken errors
        return ErrorClassification(
            category=UnifiedErrorCategory.TECHNICAL,
            should_retry=True,
            retry_delay_seconds=300,  # 5 minutes
            max_retries=3,
            user_message="Crypto exchange error. Please try again later.",
            admin_message=f"Kraken unknown error: {str(exception)}",
            original_error=str(exception),
            provider="kraken",
            error_code="UNKNOWN"
        )


class BlockBeeErrorMapper(ProviderErrorMapper):
    """Error mapper for BlockBee crypto payment provider"""
    
    def get_provider_name(self) -> str:
        return "blockbee"
    
    def map_error(self, exception: Exception, context: Optional[Dict] = None) -> ErrorClassification:
        """Map BlockBee-specific errors to unified categories"""
        error_message = str(exception).lower()
        
        # TECHNICAL errors - retry automatically
        if any(pattern in error_message for pattern in [
            'timeout', 'timed out', 'connection error', 'network error',
            'ssl error', 'service unavailable', '502', '503', '504',
            'rate limit', 'too many requests'
        ]):
            return ErrorClassification(
                category=UnifiedErrorCategory.TECHNICAL,
                should_retry=True,
                retry_delay_seconds=240,  # 4 minutes
                max_retries=3,
                user_message="Crypto payment service temporarily unavailable. Please try again.",
                admin_message=f"BlockBee technical error: {str(exception)}",
                original_error=str(exception),
                provider="blockbee"
            )
        
        # PERMANENT errors - don't retry, fix config
        if any(pattern in error_message for pattern in [
            'invalid api key', 'authentication failed', 'unauthorized',
            'invalid currency', 'unsupported currency', 'forbidden'
        ]):
            return ErrorClassification(
                category=UnifiedErrorCategory.PERMANENT,
                should_retry=False,
                retry_delay_seconds=0,
                max_retries=0,
                user_message="Crypto payment service configuration error. Please contact support.",
                admin_message=f"BlockBee configuration error: {str(exception)}",
                original_error=str(exception),
                provider="blockbee",
                error_code="CONFIG_ERROR"
            )
        
        # Default to TECHNICAL for unknown BlockBee errors
        return ErrorClassification(
            category=UnifiedErrorCategory.TECHNICAL,
            should_retry=True,
            retry_delay_seconds=300,  # 5 minutes
            max_retries=2,
            user_message="Crypto payment error. Please try again later.",
            admin_message=f"BlockBee unknown error: {str(exception)}",
            original_error=str(exception),
            provider="blockbee",
            error_code="UNKNOWN"
        )


class UnifiedErrorHandler:
    """
    Central error handling system that replaces multiple competing classifiers.
    
    Provides consistent error classification and retry logic across all providers.
    Eliminates error transformation bugs by preserving original error information.
    """
    
    def __init__(self):
        """Initialize with all provider error mappers"""
        self.provider_mappers: Dict[str, ProviderErrorMapper] = {
            'fincra': FincraErrorMapper(),
            'kraken': KrakenErrorMapper(),
            'blockbee': BlockBeeErrorMapper()
        }
        
        logger.info("🔧 UnifiedErrorHandler initialized with providers: fincra, kraken, blockbee")
    
    def classify_error(
        self, 
        exception: Exception, 
        provider: str,
        context: Optional[Dict] = None
    ) -> ErrorClassification:
        """
        Classify error using provider-specific mapper.
        
        Args:
            exception: Original exception from provider
            provider: Provider name (fincra, kraken, blockbee)
            context: Additional context for classification
            
        Returns:
            ErrorClassification with unified category and retry information
        """
        start_time = datetime.utcnow()
        
        logger.info(f"🔍 UNIFIED_ERROR_CLASSIFICATION: {provider} - {type(exception).__name__}: {str(exception)[:100]}...")
        
        try:
            # Get provider-specific mapper
            if provider not in self.provider_mappers:
                logger.warning(f"⚠️ Unknown provider '{provider}', using generic classification")
                return self._classify_generic_error(exception, provider, context)
            
            mapper = self.provider_mappers[provider]
            classification = mapper.map_error(exception, context)
            
            # Log classification result
            processing_time = (datetime.utcnow() - start_time).total_seconds()
            logger.info(
                f"✅ UNIFIED_CLASSIFIED: {provider} → {classification.category.value} "
                f"(retry={classification.should_retry}, delay={classification.retry_delay_seconds}s) "
                f"in {processing_time:.3f}s"
            )
            
            return classification
            
        except Exception as e:
            logger.error(f"❌ CLASSIFICATION_ERROR: {provider} - {e}")
            return self._classify_generic_error(exception, provider, context)
    
    def _classify_generic_error(
        self, 
        exception: Exception, 
        provider: str,
        context: Optional[Dict] = None
    ) -> ErrorClassification:
        """
        Generic error classification for unknown providers or classification failures.
        Defaults to TECHNICAL category with conservative retry policy.
        """
        return ErrorClassification(
            category=UnifiedErrorCategory.TECHNICAL,
            should_retry=True,
            retry_delay_seconds=600,  # 10 minutes for unknown errors
            max_retries=2,
            user_message="Payment system error. Please try again later.",
            admin_message=f"Generic error from {provider}: {str(exception)}",
            original_error=str(exception),
            provider=provider,
            error_code="UNKNOWN"
        )
    
    def with_unified_retry(
        self,
        provider: str,
        operation_name: str = "operation",
        context: Optional[Dict] = None
    ):
        """
        Decorator for unified retry logic across all providers.
        
        Usage:
            @error_handler.with_unified_retry("fincra", "process_payment")
            async def process_payment(self, amount, account):
                return await self._make_api_call(...)
        """
        def decorator(func: Callable) -> Callable:
            @wraps(func)
            async def wrapper(*args, **kwargs) -> Any:
                last_classification = None
                
                operation_start = datetime.utcnow()
                logger.info(f"🚀 UNIFIED_OPERATION_START: {provider}.{operation_name}")
                
                try:
                    # First attempt - no retry yet
                    result = await func(*args, **kwargs)
                    
                    total_time = (datetime.utcnow() - operation_start).total_seconds()
                    logger.info(f"✅ UNIFIED_SUCCESS: {provider}.{operation_name} completed in {total_time:.3f}s")
                    return result
                    
                except Exception as e:
                    # Classify the error
                    classification = self.classify_error(e, provider, context)
                    last_classification = classification
                    
                    # Only retry TECHNICAL errors
                    if not classification.should_retry or classification.category != UnifiedErrorCategory.TECHNICAL:
                        total_time = (datetime.utcnow() - operation_start).total_seconds()
                        logger.error(
                            f"❌ UNIFIED_NON_RETRYABLE: {provider}.{operation_name} failed "
                            f"({classification.category.value}) in {total_time:.3f}s"
                        )
                        raise UnifiedPaymentError(classification) from e
                    
                    # Retry TECHNICAL errors
                    for attempt in range(1, classification.max_retries + 1):
                        logger.warning(
                            f"🔄 UNIFIED_RETRY: {provider}.{operation_name} attempt {attempt} "
                            f"failed, retrying in {classification.retry_delay_seconds}s"
                        )
                        
                        await asyncio.sleep(classification.retry_delay_seconds)
                        
                        try:
                            result = await func(*args, **kwargs)
                            
                            total_time = (datetime.utcnow() - operation_start).total_seconds()
                            logger.info(
                                f"✅ UNIFIED_RETRY_SUCCESS: {provider}.{operation_name} "
                                f"succeeded on attempt {attempt + 1} in {total_time:.3f}s"
                            )
                            return result
                            
                        except Exception as retry_e:
                            classification = self.classify_error(retry_e, provider, context)
                            last_classification = classification
                            
                            # Stop retrying if error category changed to non-retryable
                            if classification.category != UnifiedErrorCategory.TECHNICAL:
                                logger.error(
                                    f"❌ UNIFIED_RETRY_CATEGORY_CHANGED: {provider}.{operation_name} "
                                    f"changed to {classification.category.value} - stopping retries"
                                )
                                break
                    
                    # All retries exhausted
                    total_time = (datetime.utcnow() - operation_start).total_seconds()
                    logger.error(
                        f"❌ UNIFIED_MAX_RETRIES: {provider}.{operation_name} "
                        f"failed after {classification.max_retries} retries in {total_time:.3f}s"
                    )
                    raise UnifiedPaymentError(last_classification) from e
            
            return wrapper
        return decorator


class UnifiedPaymentError(Exception):
    """
    Unified exception that carries error classification information.
    Replaces provider-specific exceptions with consistent error handling.
    """
    
    def __init__(self, classification: ErrorClassification):
        self.classification = classification
        super().__init__(classification.admin_message)
    
    def get_user_message(self) -> str:
        """Get user-friendly error message"""
        return self.classification.user_message
    
    def get_admin_message(self) -> str:
        """Get detailed admin error message"""
        return self.classification.admin_message
    
    def should_retry(self) -> bool:
        """Check if error should be retried"""
        return self.classification.should_retry
    
    def get_category(self) -> UnifiedErrorCategory:
        """Get error category"""
        return self.classification.category
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for logging/serialization"""
        return self.classification.to_dict()


# Global unified error handler instance
unified_error_handler = UnifiedErrorHandler()