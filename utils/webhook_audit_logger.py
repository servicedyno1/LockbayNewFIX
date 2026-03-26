"""
Webhook Lifecycle Audit Logging
Provides comprehensive audit logging for all webhook endpoints including payment, Twilio, and other HTTP endpoints
"""

import logging
import time
import uuid
import inspect
from typing import Dict, Any, Optional
from fastapi import Request
from functools import wraps

from utils.comprehensive_audit_logger import (
    ComprehensiveAuditLogger, 
    AuditLevel,
    TraceContext,
    RelatedIDs,
    audit_user_interaction
)

logger = logging.getLogger(__name__)


class WebhookAuditLogger:
    """
    Comprehensive audit logger for webhook endpoints
    """
    
    def __init__(self):
        self.audit_logger = ComprehensiveAuditLogger()
    
    async def log_webhook_start(
        self, 
        webhook_type: str, 
        request: Request,
        related_ids: Optional[RelatedIDs] = None
    ) -> str:
        """
        Log webhook request start
        
        Args:
            webhook_type: Type of webhook (payment, twilio, etc.)
            request: FastAPI Request object
            related_ids: Related entity IDs
            
        Returns:
            Trace ID for correlation
        """
        
        # Generate trace ID for this webhook request
        trace_id = str(uuid.uuid4())
        TraceContext.set_trace_id(trace_id)
        
        # Extract safe request metadata
        request_metadata = await self._extract_request_metadata(request)
        
        # Extract webhook-specific context
        # CRITICAL FIX: Safely extract webhook context
        try:
            webhook_context = self._extract_webhook_context(webhook_type, request_metadata)
        except (TypeError, AttributeError) as e:
            logger.debug(f"Webhook context extraction failed: {e}")
            webhook_context = {'extraction_failed': str(e)}
        
        # Log webhook start - simple, clean format
        client_host = request.client.host if request.client else "unknown"
        logger.info(f"🔗 WEBHOOK START: {webhook_type} from {client_host} (trace: {trace_id[:8]})")
        
        # Only log detailed audit for significant events (not every webhook)
        if webhook_type in ['payment', 'important'] or webhook_context.get('user_id'):
            # Prepare audit kwargs safely
            audit_kwargs = {
                "webhook_type": webhook_type,
                "client_ip": request_metadata.get('client_ip'),
                "user_agent": request_metadata.get('user_agent'),
                "trace_id": trace_id
            }
            
            # Add related_ids safely
            if related_ids:
                related_dict = related_ids.to_dict() if hasattr(related_ids, 'to_dict') else related_ids
                if isinstance(related_dict, dict):
                    audit_kwargs.update(related_dict)  # type: ignore[arg-type]
            
            audit_user_interaction(
                action=f"webhook_{webhook_type}_start",
                update=None,
                result="webhook_received",
                level=AuditLevel.INFO,
                user_id=webhook_context.get('user_id'),
                is_admin=False,
                chat_id=None,
                message_id=None,
                latency_ms=0,
                **audit_kwargs
            )
        
        return trace_id
    
    async def log_webhook_end(
        self,
        webhook_type: str,
        trace_id: str,
        success: bool,
        response_code: int,
        processing_time_ms: float,
        result_data: Optional[Dict[str, Any]] = None,
        error_details: Optional[str] = None,
        related_ids: Optional[RelatedIDs] = None
    ) -> None:
        """
        Log webhook request completion
        
        Args:
            webhook_type: Type of webhook
            trace_id: Trace ID from start
            success: Whether processing succeeded
            response_code: HTTP response code
            processing_time_ms: Processing time in milliseconds
            result_data: Processing result data (PII-safe)
            error_details: Error details if failed
            related_ids: Related entity IDs
        """
        
        # Set trace context
        TraceContext.set_trace_id(trace_id)
        
        # Determine result
        result = "webhook_processed" if success else "webhook_failed"
        level = AuditLevel.INFO if success else AuditLevel.ERROR
        
        # Log webhook completion with proper parameter handling
        audit_kwargs = {
            "webhook_type": webhook_type,
            "response_code": response_code,
            "success": success,
            "trace_id": trace_id
        }
        
        if error_details:
            audit_kwargs["error_details"] = error_details
        
        # Add related_ids as separate parameters if they exist
        # CRITICAL FIX: Add type checking for related_ids processing
        if related_ids:
            try:
                related_dict = related_ids.to_dict() if hasattr(related_ids, 'to_dict') else related_ids
                # Ensure related_dict is actually a dictionary before updating
                if isinstance(related_dict, dict):
                    audit_kwargs.update(related_dict)
                else:
                    logger.debug(f"related_ids conversion resulted in non-dict: {type(related_dict)}")
                    audit_kwargs['related_ids_conversion'] = f"failed_{type(related_dict).__name__}"
            except (TypeError, AttributeError) as e:
                logger.debug(f"Related IDs processing failed in webhook audit: {e}")
                audit_kwargs['related_ids_processing'] = f"failed_{str(e)}"
        
        # Add result_data fields (excluding user_id to avoid conflicts)
        # CRITICAL FIX: Add comprehensive type checking to prevent float iteration errors
        if result_data:
            try:
                for key, value in result_data.items():
                    # CRITICAL FIX: Convert key to string IMMEDIATELY to prevent iteration errors
                    str_key = str(key) if not isinstance(key, str) else key
                    
                    # Safe comparison now that str_key is guaranteed to be string
                    if str_key != 'user_id':  # Avoid conflict with explicit user_id parameter
                        audit_kwargs[str_key] = value
            except (TypeError, AttributeError) as e:
                # Never fail webhook processing due to audit logging issues
                logger.debug(f"Result data processing failed in webhook audit: {e}")
                # Add a safe fallback representation
                audit_kwargs['result_data_processing'] = f"failed_{type(result_data).__name__}"
        
        audit_user_interaction(
            action=f"webhook_{webhook_type}_end",
            update=None,
            result=result,
            level=level,
            user_id=result_data.get('user_id') if result_data else None,
            is_admin=False,
            chat_id=None,
            message_id=None,
            latency_ms=processing_time_ms,
            **audit_kwargs
        )
        
        status_emoji = "✅" if success else "❌"
        logger.info(f"{status_emoji} WEBHOOK END: {webhook_type} "
                   f"({response_code}) in {processing_time_ms:.1f}ms (trace: {trace_id[:8]})")
    
    async def _extract_request_metadata(self, request: Request) -> Dict[str, Any]:
        """Extract safe metadata from request"""
        
        metadata = {
            'method': request.method,
            'path': str(request.url.path),
            'query_params': len(dict(request.query_params)),
            'headers_count': len(request.headers),
            'content_length': request.headers.get('content-length', 0),
            'client_ip': self._get_client_ip(request),
            'user_agent': request.headers.get('user-agent', '')[:200],  # Truncated
            'content_type': request.headers.get('content-type', ''),
            'timestamp': time.time()
        }
        
        # Add safe header information
        # CRITICAL FIX: Ensure header processing is safe
        safe_headers = ['authorization', 'x-api-key', 'x-webhook-signature']
        try:
            for header_name in safe_headers:
                # Ensure header_name is string (it should be, but safety first)
                if not isinstance(header_name, str):
                    logger.debug(f"Non-string header_name: {type(header_name)}")
                    continue
                    
                header_value = request.headers.get(header_name)
                if header_value:
                    # Only log that header exists, not its value
                    safe_header_key = header_name.replace("-", "_") if isinstance(header_name, str) else str(header_name).replace("-", "_")
                    metadata[f'has_{safe_header_key}'] = True
        except (TypeError, AttributeError) as e:
            logger.debug(f"Header processing failed in webhook audit: {e}")
        
        return metadata
    
    def _extract_webhook_context(self, webhook_type: str, request_metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Extract webhook-specific context"""
        
        context = {
            'webhook_type': webhook_type,
            'timestamp': time.time()
        }
        
        # Add webhook-type specific context
        if webhook_type == 'payment':
            context.update({
                'is_payment_webhook': True,
                'requires_verification': True
            })
        elif webhook_type == 'twilio':
            context.update({
                'is_sms_webhook': True,
                'requires_verification': True
            })
        elif webhook_type == 'telegram':
            context.update({
                'is_telegram_webhook': True,
                'requires_bot_token': True
            })
        
        return context
    
    def _get_client_ip(self, request: Request) -> str:
        """Get client IP address from request"""
        # Check common headers for real IP
        forwarded_for = request.headers.get('x-forwarded-for')
        if forwarded_for:
            return forwarded_for.split(',')[0].strip()
        
        real_ip = request.headers.get('x-real-ip')
        if real_ip:
            return real_ip
        
        # Fallback to client host
        if hasattr(request, 'client') and request.client:
            return request.client.host
        
        return 'unknown'


# Global instance
webhook_audit_logger = WebhookAuditLogger()


def audit_webhook(webhook_type: str):
    """
    Decorator for comprehensive webhook audit logging
    
    Args:
        webhook_type: Type of webhook (payment, twilio, etc.)
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(request: Request, *args, **kwargs):
            start_time = time.time()
            trace_id = None
            success = False
            response_code = 500
            error_details = None
            result_data = None
            
            try:
                # Log webhook start
                trace_id = await webhook_audit_logger.log_webhook_start(
                    webhook_type=webhook_type,
                    request=request
                )
                
                # Execute webhook handler
                response = await func(request, *args, **kwargs)
                
                # Extract response information
                if hasattr(response, 'status_code'):
                    response_code = response.status_code
                    success = 200 <= response_code < 300
                else:
                    response_code = 200
                    success = True
                
                # Extract result data if available
                if hasattr(response, 'body'):
                    # This is a response object, success determined by status code
                    pass
                elif isinstance(response, dict):
                    result_data = response
                
                return response
                
            except Exception as e:
                success = False
                response_code = 500
                error_details = str(e)
                logger.error(f"❌ Webhook {webhook_type} failed: {e}", exc_info=True)
                raise
                
            finally:
                if trace_id:
                    # Log webhook completion
                    processing_time = (time.time() - start_time) * 1000
                    await webhook_audit_logger.log_webhook_end(
                        webhook_type=webhook_type,
                        trace_id=trace_id,
                        success=success,
                        response_code=response_code,
                        processing_time_ms=processing_time,
                        result_data=result_data,
                        error_details=error_details
                    )
        
        wrapper.__signature__ = inspect.signature(func)  # type: ignore[attr-defined]
        return wrapper
    return decorator


# Convenience decorators for specific webhook types
def audit_payment_webhook(func):
    """Decorator for payment webhook audit logging"""
    return audit_webhook("payment")(func)


def audit_twilio_webhook(func):
    """Decorator for Twilio webhook audit logging"""
    return audit_webhook("twilio")(func)


def audit_telegram_webhook(func):
    """Decorator for Telegram webhook audit logging"""
    return audit_webhook("telegram")(func)


async def log_webhook_request(
    webhook_type: str,
    request: Request,
    success: bool,
    processing_time_ms: float,
    response_code: int = 200,
    result_data: Optional[Dict[str, Any]] = None,
    error_details: Optional[str] = None
) -> None:
    """
    Convenience function for manual webhook logging
    
    Args:
        webhook_type: Type of webhook
        request: FastAPI Request object
        success: Whether processing succeeded
        processing_time_ms: Processing time in milliseconds
        response_code: HTTP response code
        result_data: Processing result data
        error_details: Error details if failed
    """
    
    # Log start and end in sequence
    trace_id = await webhook_audit_logger.log_webhook_start(webhook_type, request)
    await webhook_audit_logger.log_webhook_end(
        webhook_type=webhook_type,
        trace_id=trace_id,
        success=success,
        response_code=response_code,
        processing_time_ms=processing_time_ms,
        result_data=result_data,
        error_details=error_details
    )