"""
Service Integration Trace Correlation
Provides comprehensive trace correlation for external service integrations
including email, SMS, webhooks, and other third-party services
"""

import asyncio
import json
from functools import wraps
from typing import Dict, Any, Optional, Callable, List
from datetime import datetime
import traceback

from utils.trace_correlation import (
    trace_manager, OperationType, TraceStatus, TraceContext,
    correlate_with_external_id
)
from utils.trace_logging_integration import (
    get_trace_logger, MonitoringIntegration
)

logger = get_trace_logger(__name__)

class ServiceType:
    """Types of external services for detailed categorization"""
    EMAIL_SERVICE = "email_service"
    SMS_SERVICE = "sms_service"
    WEBHOOK_INCOMING = "webhook_incoming"
    WEBHOOK_OUTGOING = "webhook_outgoing"
    PAYMENT_API = "payment_api"
    CRYPTO_API = "crypto_api"
    NOTIFICATION_SERVICE = "notification_service"
    EXTERNAL_API = "external_api"
    THIRD_PARTY_INTEGRATION = "third_party_integration"

def service_traced(
    service_type: str,
    service_name: str,
    operation_name: Optional[str] = None,
    capture_request: bool = True,
    capture_response: bool = True,
    sensitive_fields: Optional[List[str]] = None,
    expected_duration_ms: Optional[int] = None,
    retry_enabled: bool = False
):
    """
    Decorator for external service operations to add automatic trace correlation
    
    Args:
        service_type: Type of service from ServiceType
        service_name: Name of the external service (e.g., 'brevo', 'twilio', 'fincra')
        operation_name: Custom operation name (defaults to function name)
        capture_request: Whether to capture request data in trace
        capture_response: Whether to capture response data in trace
        sensitive_fields: List of field names to exclude from trace logs
        expected_duration_ms: Expected operation duration for performance monitoring
        retry_enabled: Whether this operation supports retries
    """
    
    def decorator(func: Callable) -> Callable:
        actual_operation_name = operation_name or f"{service_name}_{func.__name__}"
        sensitive_fields_set = set(sensitive_fields or ['password', 'secret', 'key', 'token', 'api_key'])
        
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            # Extract service context from parameters
            service_context = ServiceTraceExtractor.extract_service_context(
                args, kwargs, service_type, service_name, sensitive_fields_set
            )
            
            # Create or get parent trace context
            current_context = trace_manager.get_current_trace_context()
            
            if current_context:
                # Create child trace for service operation
                trace_context = trace_manager.create_child_trace(
                    OperationType.EXTERNAL_API_CALL,
                    actual_operation_name,
                    {
                        'service_type': service_type,
                        'service_name': service_name,
                        'external_service': True,
                        **service_context
                    }
                )
            else:
                # Create new root trace for service operation
                trace_context = trace_manager.create_trace_context(
                    operation_type=OperationType.EXTERNAL_API_CALL,
                    operation_name=actual_operation_name,
                    correlation_data={
                        'service_type': service_type,
                        'service_name': service_name,
                        'external_service': True,
                        **service_context
                    }
                )
                
            if not trace_context:
                logger.warning(f"Failed to create trace context for service operation: {actual_operation_name}")
                return await func(*args, **kwargs)
            
            # Set trace context
            trace_manager.set_trace_context(trace_context)
            
            # Start service operation span
            span = trace_manager.start_span(f"service_{actual_operation_name}", "external_service")
            
            try:
                # Add service operation tags
                if span:
                    span.add_tag('service_type', service_type)
                    span.add_tag('service_name', service_name)
                    span.add_tag('external_service', True)
                    span.add_tag('function_name', func.__name__)
                    span.add_tag('retry_enabled', retry_enabled)
                    
                    if expected_duration_ms:
                        span.add_tag('expected_duration_ms', expected_duration_ms)
                    
                    # Capture request data if requested and safe
                    if capture_request and service_context.get('request_data'):
                        sanitized_request = ServiceTraceExtractor.sanitize_data(
                            service_context['request_data'], sensitive_fields_set
                        )
                        span.add_tag('request_data', json.dumps(sanitized_request, default=str)[:1000])
                        
                # Log operation start
                logger.info(
                    f"🔌 Service Operation Started: {actual_operation_name}",
                    service_details={
                        'service_type': service_type,
                        'service_name': service_name,
                        'retry_enabled': retry_enabled,
                        'has_request_data': bool(service_context.get('request_data'))
                    }
                )
                
                # Execute the service operation
                start_time = datetime.utcnow()
                result = await func(*args, **kwargs)
                execution_time = (datetime.utcnow() - start_time).total_seconds() * 1000  # ms
                
                # Process service result
                result_context = ServiceTraceExtractor.extract_result_context(result, service_name)
                
                # Add external ID correlation if available
                if result_context.get('external_id'):
                    correlate_with_external_id(result_context['external_id'], service_name)
                
                # Check performance against expected duration
                performance_status = "normal"
                if expected_duration_ms:
                    if execution_time > expected_duration_ms * 1.5:
                        performance_status = "slow"
                    elif execution_time > expected_duration_ms * 2:
                        performance_status = "very_slow"
                
                # Log successful completion
                logger.info(
                    f"✅ Service Operation Completed: {actual_operation_name}",
                    performance_metrics={
                        'execution_time_ms': execution_time,
                        'performance_status': performance_status,
                        'service_response_status': result_context.get('status', 'success'),
                        'external_id': result_context.get('external_id')
                    }
                )
                
                # Complete span and trace
                if span:
                    span.add_tag('execution_time_ms', execution_time)
                    span.add_tag('performance_status', performance_status)
                    span.add_tag('service_response_status', result_context.get('status', 'success'))
                    
                    if result_context.get('external_id'):
                        span.add_tag('external_id', result_context['external_id'])
                    
                    # Capture response data if requested and safe
                    if capture_response and result:
                        sanitized_response = ServiceTraceExtractor.sanitize_data(result, sensitive_fields_set)
                        span.add_tag('response_data', json.dumps(sanitized_response, default=str)[:1000])
                        
                    trace_manager.finish_span(span, TraceStatus.COMPLETED)
                    
                trace_manager.complete_trace(
                    trace_context.trace_id, 
                    TraceStatus.COMPLETED,
                    performance_metrics={
                        'execution_time_ms': execution_time,
                        'performance_status': performance_status,
                        'external_service': True,
                        'service_name': service_name
                    }
                )
                
                # Integrate with monitoring systems
                MonitoringIntegration.correlate_performance_metrics({
                    'operation': actual_operation_name,
                    'service_name': service_name,
                    'execution_time_ms': execution_time,
                    'performance_status': performance_status,
                    'external_service': True
                })
                
                # Alert if service took too long
                if expected_duration_ms and execution_time > expected_duration_ms * 2:
                    logger.warning(
                        f"⚠️ SLOW_SERVICE_OPERATION: {actual_operation_name} took {execution_time:.1f}ms "
                        f"(expected: {expected_duration_ms}ms)"
                    )
                
                return result
                
            except Exception as e:
                # Handle service operation errors with full context
                execution_time = (datetime.utcnow() - trace_context.start_time).total_seconds() * 1000
                
                error_info = {
                    'error_type': type(e).__name__,
                    'error_message': str(e),
                    'error_traceback': traceback.format_exc(),
                    'service_type': service_type,
                    'service_name': service_name,
                    'function_name': func.__name__,
                    'retry_enabled': retry_enabled
                }
                
                # Log error with service context
                logger.error(
                    f"❌ Service Operation Failed: {actual_operation_name}",
                    error_details=error_info,
                    performance_metrics={'execution_time_ms': execution_time}
                )
                
                # Update span and trace with error
                if span:
                    span.set_error(e, error_info)
                    trace_manager.finish_span(span, TraceStatus.FAILED)
                    
                trace_manager.complete_trace(
                    trace_context.trace_id, 
                    TraceStatus.FAILED, 
                    error_info,
                    performance_metrics={'execution_time_ms': execution_time}
                )
                
                # Re-raise the exception to maintain original behavior
                raise
                
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            # Similar implementation for sync service operations
            service_context = ServiceTraceExtractor.extract_service_context(
                args, kwargs, service_type, service_name, sensitive_fields_set
            )
            
            current_context = trace_manager.get_current_trace_context()
            
            if current_context:
                trace_context = trace_manager.create_child_trace(
                    OperationType.EXTERNAL_API_CALL,
                    actual_operation_name,
                    {
                        'service_type': service_type,
                        'service_name': service_name,
                        **service_context
                    }
                )
            else:
                trace_context = trace_manager.create_trace_context(
                    operation_type=OperationType.EXTERNAL_API_CALL,
                    operation_name=actual_operation_name,
                    correlation_data={
                        'service_type': service_type,
                        'service_name': service_name,
                        **service_context
                    }
                )
                
            if not trace_context:
                return func(*args, **kwargs)
                
            trace_manager.set_trace_context(trace_context)
            span = trace_manager.start_span(f"service_{actual_operation_name}", "external_service")
            
            try:
                if span:
                    span.add_tag('service_type', service_type)
                    span.add_tag('service_name', service_name)
                    
                start_time = datetime.utcnow()
                result = func(*args, **kwargs)
                execution_time = (datetime.utcnow() - start_time).total_seconds() * 1000
                
                if span:
                    span.add_tag('execution_time_ms', execution_time)
                    trace_manager.finish_span(span, TraceStatus.COMPLETED)
                    
                trace_manager.complete_trace(
                    trace_context.trace_id, 
                    TraceStatus.COMPLETED,
                    performance_metrics={'execution_time_ms': execution_time}
                )
                
                return result
                
            except Exception as e:
                error_info = {
                    'error_type': type(e).__name__,
                    'error_message': str(e),
                    'service_type': service_type,
                    'service_name': service_name
                }
                
                if span:
                    span.set_error(e, error_info)
                    trace_manager.finish_span(span, TraceStatus.FAILED)
                    
                trace_manager.complete_trace(trace_context.trace_id, TraceStatus.FAILED, error_info)
                raise
                
        # Return appropriate wrapper
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
            
    return decorator

class ServiceTraceExtractor:
    """Extract service operation context for tracing"""
    
    @staticmethod
    def extract_service_context(args: tuple, kwargs: dict, service_type: str, service_name: str, sensitive_fields: set) -> Dict[str, Any]:
        """Extract service context from function parameters"""
        context = {
            'service_type': service_type,
            'service_name': service_name,
            'operation_timestamp': datetime.utcnow().isoformat()
        }
        
        # Extract request data from common parameter names
        request_data = {}
        
        for key, value in kwargs.items():
            if key.lower() not in sensitive_fields:
                if key in ['data', 'payload', 'body', 'request_data', 'params']:
                    request_data[key] = value
                elif key in ['to', 'from', 'subject', 'message', 'recipient']:
                    request_data[key] = value
                elif key in ['amount', 'currency', 'reference']:
                    request_data[key] = value
                    
        if request_data:
            context['request_data'] = request_data
            
        # Extract endpoint/URL information
        if 'url' in kwargs:
            context['endpoint'] = kwargs['url']
        elif 'endpoint' in kwargs:
            context['endpoint'] = kwargs['endpoint']
            
        # Extract method information
        if 'method' in kwargs:
            context['http_method'] = kwargs['method']
            
        return context
    
    @staticmethod
    def extract_result_context(result: Any, service_name: str) -> Dict[str, Any]:
        """Extract context from service operation result"""
        context = {'result_type': type(result).__name__}
        
        if result is None:
            return context
            
        if isinstance(result, dict):
            # Look for common result fields
            for field in ['id', 'status', 'message_id', 'reference', 'external_id', 'transaction_id']:
                if field in result:
                    context[field] = result[field]
                    
            # Service-specific extractions
            if service_name.lower() == 'brevo' and 'message_id' in result:
                context['external_id'] = result['message_id']
            elif service_name.lower() == 'twilio' and 'sid' in result:
                context['external_id'] = result['sid']
            elif service_name.lower() in ['fincra', 'kraken'] and 'id' in result:
                context['external_id'] = result['id']
                
        elif isinstance(result, bool):
            context['success'] = result
            context['status'] = 'success' if result else 'failed'
            
        elif hasattr(result, '__dict__'):
            # Extract from result object
            for attr in ['id', 'status', 'message_id', 'reference', 'external_id']:
                if hasattr(result, attr):
                    context[attr] = getattr(result, attr)
                    
        return context
    
    @staticmethod
    def sanitize_data(data: Any, sensitive_fields: set, max_length: int = 500) -> Any:
        """Sanitize data for logging by removing sensitive information"""
        if data is None:
            return None
            
        if isinstance(data, dict):
            sanitized = {}
            for key, value in data.items():
                key_lower = str(key).lower()
                if any(sensitive in key_lower for sensitive in sensitive_fields):
                    sanitized[key] = '[REDACTED]'
                elif isinstance(value, dict):
                    sanitized[key] = ServiceTraceExtractor.sanitize_data(value, sensitive_fields, max_length)
                elif isinstance(value, str) and len(value) > max_length:
                    sanitized[key] = value[:max_length] + '...[TRUNCATED]'
                else:
                    sanitized[key] = value
            return sanitized
            
        elif isinstance(data, (list, tuple)):
            return [ServiceTraceExtractor.sanitize_data(item, sensitive_fields, max_length) for item in data]
            
        elif isinstance(data, str) and len(data) > max_length:
            return data[:max_length] + '...[TRUNCATED]'
            
        else:
            return data

# Webhook-specific trace utilities
class WebhookTraceManager:
    """Manage trace correlation for webhook operations"""
    
    @staticmethod
    def create_incoming_webhook_context(webhook_source: str, webhook_data: Dict[str, Any]) -> Optional[TraceContext]:
        """Create trace context for incoming webhooks"""
        return trace_manager.create_trace_context(
            operation_type=OperationType.WEBHOOK_REQUEST,
            operation_name=f"webhook_incoming_{webhook_source}",
            correlation_data={
                'webhook_direction': 'incoming',
                'webhook_source': webhook_source,
                'webhook_data_size': len(json.dumps(webhook_data, default=str)),
                'webhook_timestamp': datetime.utcnow().isoformat()
            }
        )
    
    @staticmethod
    def correlate_webhook_processing(webhook_id: str, processing_result: Dict[str, Any]):
        """Correlate webhook processing results"""
        current_context = trace_manager.get_current_trace_context()
        if current_context:
            current_context.correlation_data.update({
                'webhook_processing': True,
                'webhook_id': webhook_id,
                'processing_result': processing_result,
                'processing_timestamp': datetime.utcnow().isoformat()
            })

# Email service integration
class EmailServiceIntegration:
    """Trace integration for email services"""
    
    @staticmethod
    def trace_email_send(recipient: str, subject: str, email_service: str = 'brevo'):
        """Create trace context for email sending"""
        current_context = trace_manager.get_current_trace_context()
        if current_context:
            email_context = trace_manager.create_child_trace(
                OperationType.EMAIL_OPERATION,
                f"email_send_{email_service}",
                {
                    'email_service': email_service,
                    'recipient_domain': recipient.split('@')[-1] if '@' in recipient else 'unknown',
                    'subject_length': len(subject),
                    'email_operation': True
                }
            )
            return email_context
        return None

# SMS service integration
class SMSServiceIntegration:
    """Trace integration for SMS services"""
    
    @staticmethod
    def trace_sms_send(recipient: str, sms_service: str = 'twilio'):
        """Create trace context for SMS sending"""
        current_context = trace_manager.get_current_trace_context()
        if current_context:
            sms_context = trace_manager.create_child_trace(
                OperationType.SMS_OPERATION,
                f"sms_send_{sms_service}",
                {
                    'sms_service': sms_service,
                    'recipient_country': recipient[:3] if recipient.startswith('+') else 'unknown',
                    'sms_operation': True
                }
            )
            return sms_context
        return None

# Utility functions for common service operations
def correlate_service_health_check(service_name: str, health_status: str, response_time_ms: float):
    """Add service health check correlation to current trace"""
    current_context = trace_manager.get_current_trace_context()
    if current_context:
        current_context.correlation_data.update({
            'service_health_check': True,
            'service_name': service_name,
            'health_status': health_status,
            'response_time_ms': response_time_ms,
            'health_check_timestamp': datetime.utcnow().isoformat()
        })

def correlate_rate_limiting(service_name: str, rate_limit_status: Dict[str, Any]):
    """Add rate limiting correlation to current trace"""
    current_context = trace_manager.get_current_trace_context()
    if current_context:
        current_context.correlation_data.update({
            'rate_limiting': True,
            'service_name': service_name,
            'rate_limit_status': rate_limit_status
        })

def correlate_service_authentication(service_name: str, auth_method: str, auth_status: str):
    """Add service authentication correlation to current trace"""
    current_context = trace_manager.get_current_trace_context()
    if current_context:
        current_context.correlation_data.update({
            'service_authentication': True,
            'service_name': service_name,
            'auth_method': auth_method,
            'auth_status': auth_status,
            'auth_timestamp': datetime.utcnow().isoformat()
        })

def setup_service_trace_integration():
    """Setup service trace integration with existing systems"""
    logger.info("🔌 Setting up service trace integration...")
    
    # This function can be called during application initialization
    # to ensure service trace correlation is properly configured
    
    logger.info("✅ Service trace integration configured")

logger.info("🔌 Service trace integration module initialized")