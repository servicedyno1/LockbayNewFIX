"""
Base provider classes and utilities for the UTE provider system
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, Union
from enum import Enum
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


class ProviderStatus(Enum):
    """Provider operation status"""
    SUCCESS = "success"
    FAILED = "failed"
    PENDING = "pending"
    TIMEOUT = "timeout"
    RETRYABLE_ERROR = "retryable_error"
    PERMANENT_ERROR = "permanent_error"


@dataclass
class ProviderResult:
    """Standardized result from provider operations"""
    success: bool
    status: ProviderStatus
    message: Optional[str] = None
    data: Optional[Dict[str, Any]] = None
    error_code: Optional[str] = None
    external_reference: Optional[str] = None
    is_retryable: bool = False
    processing_time_ms: Optional[float] = None
    
    # Provider-specific metadata
    provider_name: Optional[str] = None
    provider_response: Optional[Dict[str, Any]] = None


class ProviderError(Exception):
    """Base provider error with standardized information"""
    
    def __init__(
        self,
        message: str,
        error_code: str = None,
        is_retryable: bool = False,
        provider_name: str = None,
        original_error: Exception = None,
        context: Dict[str, Any] = None
    ):
        super().__init__(message)
        self.error_code = error_code
        self.is_retryable = is_retryable
        self.provider_name = provider_name
        self.original_error = original_error
        self.context = context or {}


class BaseProvider(ABC):
    """
    Abstract base class for all UTE providers
    
    Standardizes the interface for external service integrations,
    providing consistent error handling, logging, and result formatting.
    """
    
    def __init__(self, provider_name: str):
        self.provider_name = provider_name
        self.logger = logging.getLogger(f"{__name__}.{provider_name}")
    
    @abstractmethod
    async def health_check(self) -> ProviderResult:
        """
        Check if the provider service is healthy and accessible
        
        Returns:
            ProviderResult indicating service health status
        """
        pass
    
    @abstractmethod
    async def validate_configuration(self) -> ProviderResult:
        """
        Validate that the provider is properly configured with required credentials
        
        Returns:
            ProviderResult indicating configuration validity
        """
        pass
    
    def _create_success_result(
        self,
        message: str = None,
        data: Dict[str, Any] = None,
        external_reference: str = None,
        processing_time_ms: float = None
    ) -> ProviderResult:
        """Create a success result"""
        return ProviderResult(
            success=True,
            status=ProviderStatus.SUCCESS,
            message=message,
            data=data,
            external_reference=external_reference,
            processing_time_ms=processing_time_ms,
            provider_name=self.provider_name
        )
    
    def _create_error_result(
        self,
        error: Union[Exception, str],
        error_code: str = None,
        is_retryable: bool = False,
        processing_time_ms: float = None,
        provider_response: Dict[str, Any] = None
    ) -> ProviderResult:
        """Create an error result"""
        if isinstance(error, Exception):
            message = str(error)
        else:
            message = error
            
        status = ProviderStatus.RETRYABLE_ERROR if is_retryable else ProviderStatus.PERMANENT_ERROR
        
        return ProviderResult(
            success=False,
            status=status,
            message=message,
            error_code=error_code,
            is_retryable=is_retryable,
            processing_time_ms=processing_time_ms,
            provider_name=self.provider_name,
            provider_response=provider_response
        )
    
    def _log_operation_start(self, operation: str, context: Dict[str, Any] = None):
        """Log the start of an operation"""
        self.logger.info(f"🚀 {self.provider_name.upper()}_START: {operation}", extra=context or {})
    
    def _log_operation_success(self, operation: str, result: ProviderResult):
        """Log successful operation completion"""
        self.logger.info(
            f"✅ {self.provider_name.upper()}_SUCCESS: {operation} - {result.message}",
            extra={
                "processing_time_ms": result.processing_time_ms,
                "external_reference": result.external_reference
            }
        )
    
    def _log_operation_error(self, operation: str, result: ProviderResult):
        """Log operation error"""
        log_level = logging.WARNING if result.is_retryable else logging.ERROR
        self.logger.log(
            log_level,
            f"❌ {self.provider_name.upper()}_ERROR: {operation} - {result.message}",
            extra={
                "error_code": result.error_code,
                "is_retryable": result.is_retryable,
                "processing_time_ms": result.processing_time_ms
            }
        )