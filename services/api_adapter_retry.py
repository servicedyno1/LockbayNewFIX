"""
Standardized API Adapter with Unified Retry System
Base class for all external API integrations to provide consistent error handling and retry logic
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, Optional, Tuple, TypeVar
from datetime import datetime
import aiohttp
from functools import wraps

from services.cashout_error_classifier import UnifiedErrorClassifier
from services.circuit_breaker import circuit_breakers
from models import OperationFailureType, CashoutErrorCode
from utils.error_handler import handle_error

logger = logging.getLogger(__name__)
T = TypeVar('T')


class APIRetryException(Exception):
    """
    Wrapper exception that includes retry classification information
    """
    def __init__(self, original_exception: Exception, error_code: CashoutErrorCode, retryable: bool):
        self.original_exception = original_exception
        self.error_code = error_code
        self.retryable = retryable
        super().__init__(str(original_exception))


class APIAdapterRetry(ABC):
    """
    Base class for external API integrations with unified retry logic
    
    Provides:
    - Standardized error classification
    - Intelligent retry with exponential backoff
    - Circuit breaker integration
    - Consistent logging and monitoring
    - Unified error mapping from provider-specific to generic codes
    """
    
    def __init__(self, service_name: str, timeout: int = 30):
        self.service_name = service_name
        self.timeout = timeout
        self.error_classifier = UnifiedErrorClassifier()
        
        # Ensure circuit breaker exists for this service
        if service_name not in circuit_breakers:
            from services.circuit_breaker import CircuitBreaker
            circuit_breakers[service_name] = CircuitBreaker(
                name=f"{service_name} API",
                failure_threshold=5,
                recovery_timeout=60
            )
        
        logger.info(f"🔧 APIAdapterRetry initialized for {service_name}")
    
    @abstractmethod
    def _map_provider_error_to_unified(self, exception: Exception, context: Optional[Dict] = None) -> CashoutErrorCode:
        """
        Map provider-specific errors to unified error codes
        Must be implemented by each API service
        
        Args:
            exception: The original provider exception
            context: Additional context about the API call
            
        Returns:
            CashoutErrorCode: Unified error code for classification
        """
        pass
    
    @abstractmethod
    def _get_circuit_breaker_name(self) -> str:
        """Return the circuit breaker name for this API service"""
        pass
    
    async def _classify_api_error(self, exception: Exception, context: Optional[Dict] = None) -> Tuple[OperationFailureType, CashoutErrorCode, bool, int]:
        """
        Classify an API error using the unified classification system
        
        Args:
            exception: The exception that occurred
            context: Additional context (operation, endpoint, etc.)
            
        Returns:
            Tuple of (failure_type, error_code, retryable, recommended_delay_seconds)
        """
        start_time = datetime.utcnow()
        logger.info(f"🔍 API_ERROR_CLASSIFICATION: {self.service_name} - {type(exception).__name__}: {str(exception)[:100]}...")
        
        try:
            # First try provider-specific error mapping
            mapped_error_code = self._map_provider_error_to_unified(exception, context)
            
            if mapped_error_code != CashoutErrorCode.UNKNOWN_ERROR:
                # Use the mapped error code for classification
                config = self.error_classifier.get_retry_config(mapped_error_code)
                failure_type = OperationFailureType.TECHNICAL if config["retryable"] else OperationFailureType.USER
                retryable = config["retryable"]
                delay = config.get("backoff_delays", [600])[0]
                
                logger.info(f"✅ PROVIDER_MAPPED: {self.service_name} - {mapped_error_code.value} (retryable={retryable})")
                return failure_type, mapped_error_code, retryable, delay
            
            # Fall back to generic error classification
            enhanced_context = {
                "service": self.service_name,
                "provider_error_type": type(exception).__name__,
                **(context or {})
            }
            
            failure_type, error_code, retryable, delay = self.error_classifier.classify_error(exception, enhanced_context)
            
            processing_time = (datetime.utcnow() - start_time).total_seconds()
            logger.info(f"✅ API_CLASSIFIED: {self.service_name} - {error_code.value} (retryable={retryable}) in {processing_time:.3f}s")
            
            # Send comprehensive admin notifications for API authentication and funding failures
            if error_code == CashoutErrorCode.API_AUTHENTICATION_FAILED:
                try:
                    from services.comprehensive_admin_notifications import comprehensive_admin_notifications
                    from utils.graceful_shutdown import create_managed_task
                    
                    # Create managed task for admin notification
                    create_managed_task(
                        comprehensive_admin_notifications.send_api_authentication_alert(
                            service_name=self.service_name,
                            error_message=str(exception),
                            affected_transactions=[],  # Could be enhanced to track affected transactions
                            error_details={
                                'error_code': error_code.value,
                                'failure_type': failure_type.value,
                                'retryable': retryable,
                                'context': context or {},
                                'processing_time_seconds': processing_time
                            }
                        )
                    )
                    logger.info(f"✅ API authentication failure admin alert queued for {self.service_name}")
                    
                except Exception as alert_error:
                    logger.error(f"❌ Failed to send API authentication admin alert for {self.service_name}: {alert_error}")
            
            elif error_code == CashoutErrorCode.API_INSUFFICIENT_FUNDS:
                try:
                    from services.comprehensive_admin_notifications import comprehensive_admin_notifications
                    from utils.graceful_shutdown import create_managed_task
                    
                    # Create managed task for generic provider funding notification
                    create_managed_task(
                        comprehensive_admin_notifications.send_generic_provider_funding_alert(
                            provider_name=self.service_name,
                            cashout_id=context.get('cashout_id', 'Unknown'),
                            amount=context.get('amount', 0.0),
                            currency=context.get('currency', 'Unknown'),
                            user_data=context.get('user_data', {}),
                            error_message=str(exception),
                            provider_config={
                                'error_code': error_code.value,
                                'failure_type': failure_type.value,
                                'retryable': retryable,
                                'processing_time_seconds': processing_time
                            }
                        )
                    )
                    logger.info(f"✅ Generic provider funding alert queued for {self.service_name}")
                    
                except Exception as alert_error:
                    logger.error(f"❌ Failed to send generic provider funding admin alert for {self.service_name}: {alert_error}")
            
            return failure_type, error_code, retryable, delay
            
        except Exception as e:
            logger.error(f"❌ API_CLASSIFICATION_ERROR: {self.service_name} - {e}")
            handle_error(e, {"service": self.service_name, "original_exception": str(exception)})
            return OperationFailureType.TECHNICAL, CashoutErrorCode.UNKNOWN_ERROR, True, 600
    
    def api_retry(
        self,
        max_attempts: int = None,
        timeout: Optional[int] = None,
        context: Optional[Dict] = None
    ):
        """
        Decorator for API methods to add unified retry logic
        
        Usage:
            @api_retry(max_attempts=3, context={"operation": "create_payment"})
            async def create_payment(self, amount, currency):
                return await self._make_api_request(...)
        """
        def decorator(func: Callable) -> Callable:
            @wraps(func)
            async def wrapper(*args, **kwargs) -> Any:
                # Determine max attempts from error classification if not specified
                attempts_remaining = max_attempts or 3
                last_exception = None
                attempt = 0
                
                operation_start = datetime.utcnow()
                operation_context = {
                    "function": func.__name__,
                    "service": self.service_name,
                    "operation_start": operation_start.isoformat(),
                    **(context or {})
                }
                
                logger.info(f"🚀 API_OPERATION_START: {self.service_name}.{func.__name__} (max_attempts={attempts_remaining})")
                
                while attempts_remaining > 0:
                    attempt += 1
                    attempt_start = datetime.utcnow()
                    
                    try:
                        # Apply circuit breaker protection
                        circuit_breaker_name = self._get_circuit_breaker_name()
                        breaker = circuit_breakers.get(circuit_breaker_name)
                        
                        if breaker:
                            result = await breaker.async_call(func, *args, **kwargs)
                        else:
                            result = await func(*args, **kwargs)
                        
                        # Success
                        total_time = (datetime.utcnow() - operation_start).total_seconds()
                        logger.info(f"✅ API_SUCCESS: {self.service_name}.{func.__name__} completed in {total_time:.3f}s after {attempt} attempt(s)")
                        return result
                        
                    except Exception as e:
                        last_exception = e
                        attempts_remaining -= 1
                        attempt_time = (datetime.utcnow() - attempt_start).total_seconds()
                        
                        # Classify the error
                        failure_type, error_code, retryable, delay = await self._classify_api_error(e, operation_context)
                        
                        # Enhanced error logging
                        error_data = {
                            "attempt": attempt,
                            "attempts_remaining": attempts_remaining,
                            "error_code": error_code.value,
                            "retryable": retryable,
                            "delay_seconds": delay,
                            "attempt_duration": attempt_time,
                            "failure_type": failure_type.value
                        }
                        
                        if attempts_remaining > 0 and retryable:
                            logger.warning(f"🔄 API_RETRY: {self.service_name}.{func.__name__} attempt {attempt} failed - retrying in {delay}s", extra=error_data)
                            await asyncio.sleep(delay)
                        else:
                            # Final failure
                            total_time = (datetime.utcnow() - operation_start).total_seconds()
                            error_data.update({
                                "total_operation_time": total_time,
                                "final_failure": True
                            })
                            
                            if not retryable:
                                logger.error(f"❌ API_USER_ERROR: {self.service_name}.{func.__name__} failed with non-retryable error", extra=error_data)
                            else:
                                logger.error(f"❌ API_MAX_RETRIES: {self.service_name}.{func.__name__} failed after {attempt} attempts", extra=error_data)
                            
                            # Wrap exception with retry information
                            raise APIRetryException(e, error_code, retryable) from e
                
                # Should never reach here, but just in case
                raise APIRetryException(last_exception, CashoutErrorCode.UNKNOWN_ERROR, False) from last_exception
            
            return wrapper
        return decorator
    
    async def _make_http_request(
        self,
        method: str,
        url: str,
        headers: Optional[Dict] = None,
        params: Optional[Dict] = None,
        json: Optional[Dict] = None,
        data: Optional[Any] = None,
        timeout: Optional[int] = None
    ) -> Dict:
        """
        Make HTTP request with standardized error handling
        
        Args:
            method: HTTP method (GET, POST, etc.)
            url: Request URL
            headers: Request headers
            params: URL parameters
            json: JSON data
            data: Form data
            timeout: Request timeout
            
        Returns:
            Dict: Response JSON data
            
        Raises:
            APIRetryException: Classified API error
        """
        timeout = timeout or self.timeout
        
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
                async with session.request(
                    method=method,
                    url=url,
                    headers=headers,
                    params=params,
                    json=json,
                    data=data
                ) as response:
                    # Check for HTTP error status codes
                    if response.status >= 400:
                        error_text = await response.text()
                        error_msg = f"HTTP {response.status}: {error_text}"
                        
                        # Map HTTP status codes to unified error codes
                        if response.status == 400:
                            raise Exception(f"API_INVALID_REQUEST: {error_msg}")
                        elif response.status in [401, 403]:
                            raise Exception(f"API_AUTHENTICATION_FAILED: {error_msg}")
                        elif response.status == 429:
                            raise Exception(f"RATE_LIMIT_EXCEEDED: {error_msg}")
                        elif response.status in [500, 502, 503, 504]:
                            raise Exception(f"SERVICE_UNAVAILABLE: {error_msg}")
                        else:
                            raise Exception(error_msg)
                    
                    # Parse response
                    response_data = await response.json()
                    return response_data
                    
        except aiohttp.ClientError as e:
            # Network/connection errors
            raise Exception(f"API_NETWORK_ERROR: {str(e)}") from e
        except asyncio.TimeoutError as e:
            # Timeout errors
            raise Exception(f"API_TIMEOUT: Request timed out after {timeout}s") from e
        except Exception as e:
            # Re-raise already formatted exceptions
            if str(e).startswith(("API_", "RATE_LIMIT_", "SERVICE_UNAVAILABLE")):
                raise
            else:
                raise Exception(f"API_UNKNOWN_ERROR: {str(e)}") from e
    
    def get_retry_stats(self) -> Dict[str, Any]:
        """Get retry statistics for this API service"""
        circuit_breaker_name = self._get_circuit_breaker_name()
        breaker = circuit_breakers.get(circuit_breaker_name)
        
        return {
            "service_name": self.service_name,
            "circuit_breaker_state": breaker.get_state() if breaker else None,
            "timeout_seconds": self.timeout,
            "classification_enabled": True
        }


class GenericAPIAdapter(APIAdapterRetry):
    """
    Generic API adapter for services that don't need custom error mapping
    Uses default error classification based on HTTP status codes and exception types
    """
    
    def __init__(self, service_name: str, timeout: int = 30):
        super().__init__(service_name, timeout)
    
    def _map_provider_error_to_unified(self, exception: Exception, context: Optional[Dict] = None) -> CashoutErrorCode:
        """
        Generic error mapping based on exception message patterns
        """
        error_message = str(exception).lower()
        
        # Check for specific patterns in the exception message
        if "api_timeout" in error_message or "timeout" in error_message:
            return CashoutErrorCode.API_TIMEOUT
        elif "api_authentication_failed" in error_message or "authentication" in error_message:
            return CashoutErrorCode.API_AUTHENTICATION_FAILED
        elif "api_invalid_request" in error_message or "invalid" in error_message:
            return CashoutErrorCode.API_INVALID_REQUEST
        elif "api_insufficient_funds" in error_message or "insufficient" in error_message:
            return CashoutErrorCode.API_INSUFFICIENT_FUNDS
        elif "rate_limit" in error_message:
            return CashoutErrorCode.RATE_LIMIT_EXCEEDED
        elif "service_unavailable" in error_message:
            return CashoutErrorCode.SERVICE_UNAVAILABLE
        elif "api_network_error" in error_message or "network" in error_message:
            return CashoutErrorCode.NETWORK_ERROR
        
        # Default to unknown error for generic classification
        return CashoutErrorCode.UNKNOWN_ERROR
    
    def _get_circuit_breaker_name(self) -> str:
        """Return the circuit breaker name for this service"""
        return self.service_name.lower()