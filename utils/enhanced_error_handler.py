"""
Enhanced Error Handler with Correlation Support
Provides consistent error handling and logging correlation between user messages and backend errors
"""

import logging
from typing import Optional
from telegram.ext import ContextTypes

from utils.unified_activity_monitor import track_correlated_error, unified_monitor
from utils.update_interceptor import get_current_trace_id

logger = logging.getLogger(__name__)

class CorrelatedErrorHandler:
    """Enhanced error handler that correlates user-facing errors with backend logs"""
    
    @staticmethod
    async def handle_error_with_correlation(
        query_or_update,
        user_message: str,
        backend_error: str, 
        handler_name: str,
        user_id: Optional[int] = None,
        callback_data: Optional[str] = None,
        context: Optional[ContextTypes.DEFAULT_TYPE] = None
    ) -> str:
        """
        Handle error with correlation between user message and backend error
        
        Args:
            query_or_update: Telegram query or update object
            user_message: Error message shown to user
            backend_error: Technical error for logs
            handler_name: Name of the handler where error occurred
            user_id: User ID (optional, will be extracted if not provided)
            callback_data: Callback data if applicable
            context: Bot context
            
        Returns:
            Correlation ID for tracking
        """
        try:
            # Extract user ID if not provided
            if not user_id:
                if hasattr(query_or_update, 'from_user'):
                    user_id = query_or_update.from_user.id
                elif hasattr(query_or_update, 'effective_user') and query_or_update.effective_user:
                    user_id = query_or_update.effective_user.id
            
            # Extract callback data if not provided
            if not callback_data and hasattr(query_or_update, 'data'):
                callback_data = query_or_update.data
            
            # Get trace ID for correlation
            trace_id = get_current_trace_id()
            
            # Track the correlated error
            correlation_id = track_correlated_error(
                user_id=user_id,
                user_message=user_message,
                backend_error=backend_error,
                handler_name=handler_name,
                callback_data=callback_data,
                trace_id=trace_id
            )
            
            # Log both user and backend errors with correlation
            logger.error(
                f"🔗 CORRELATED ERROR [{correlation_id[:8] if correlation_id else 'NONE'}] "
                f"Handler: {handler_name} | "
                f"User sees: '{user_message}' | "
                f"Backend error: '{backend_error}' | "
                f"User: {user_id} | "
                f"Callback: {callback_data} | "
                f"Trace: {trace_id[:8] if trace_id else 'NONE'}"
            )
            
            # Send user message
            if hasattr(query_or_update, 'edit_message_text'):
                await query_or_update.edit_message_text(user_message)
            elif hasattr(query_or_update, 'message') and hasattr(query_or_update.message, 'reply_text'):
                await query_or_update.message.reply_text(user_message)
            
            return correlation_id or ""
            
        except Exception as e:
            logger.error(f"Error in correlated error handler: {e}")
            # Fallback - still try to show user message
            try:
                if hasattr(query_or_update, 'edit_message_text'):
                    await query_or_update.edit_message_text(user_message)
            except Exception as err:
                logger.debug(f"Could not send fallback error message: {err}")
                pass
            return ""
    
    @staticmethod
    async def safe_edit_with_correlation(
        query,
        user_message: str,
        handler_name: str,
        backend_error: str = None,
        **kwargs
    ):
        """
        Safe message edit with error correlation
        
        Args:
            query: Telegram callback query
            user_message: Message to show user
            handler_name: Handler name for tracking
            backend_error: Backend error details (optional)
            **kwargs: Additional arguments for edit_message_text
        """
        try:
            # Track the interaction
            user_id = query.from_user.id if query.from_user else None
            if user_id:
                unified_monitor.track_user_interaction(
                    user_id=user_id,
                    username=query.from_user.username if query.from_user else None,
                    action=f"message_edit_{handler_name}",
                    details={'message': user_message[:100]},
                    trace_id=get_current_trace_id()
                )
            
            # If this is an error message, correlate it
            if user_message.startswith('❌') or 'error' in user_message.lower():
                backend_msg = backend_error or f"Error in {handler_name}"
                await CorrelatedErrorHandler.handle_error_with_correlation(
                    query_or_update=query,
                    user_message=user_message,
                    backend_error=backend_msg,
                    handler_name=handler_name,
                    user_id=user_id,
                    callback_data=query.data
                )
            else:
                # Normal message edit
                await query.edit_message_text(user_message, **kwargs)
                
        except Exception as e:
            # Log the failure with correlation
            correlation_id = track_correlated_error(
                user_id=user_id if 'user_id' in locals() else None,
                user_message=user_message,
                backend_error=f"edit_message_text failed: {str(e)}",
                handler_name=handler_name,
                callback_data=getattr(query, 'data', None),
                trace_id=get_current_trace_id()
            )
            
            logger.error(
                f"🔗 MESSAGE_EDIT_FAILED [{correlation_id[:8] if correlation_id else 'NONE'}] "
                f"Handler: {handler_name} | "
                f"Error: {str(e)} | "
                f"User message: '{user_message[:100]}'"
            )
            
            # Try simpler fallback
            try:
                await query.edit_message_text("❌ An error occurred. Please try again.")
            except Exception as err:
                logger.debug(f"Could not send simple fallback message: {err}")
                pass

# Enhanced error handling decorators
def with_error_correlation(handler_name: str):
    """Decorator to add error correlation to handlers"""
    def decorator(func):
        async def wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                # Extract update/query from args
                update_or_query = None
                for arg in args:
                    if hasattr(arg, 'effective_user') or hasattr(arg, 'from_user'):
                        update_or_query = arg
                        break
                
                # Handle the error with correlation
                if update_or_query:
                    await CorrelatedErrorHandler.handle_error_with_correlation(
                        query_or_update=update_or_query,
                        user_message="❌ An error occurred. Please try again.",
                        backend_error=f"{type(e).__name__}: {str(e)}",
                        handler_name=handler_name
                    )
                else:
                    logger.error(f"Unhandled error in {handler_name}: {e}")
                
                raise
        return wrapper
    return decorator

# Convenience functions
async def handle_validation_error(query, field_name: str, handler_name: str):
    """Handle validation errors with correlation"""
    user_message = f"❌ Invalid {field_name} format."
    backend_error = f"Validation failed for {field_name} in {handler_name}"
    
    return await CorrelatedErrorHandler.handle_error_with_correlation(
        query_or_update=query,
        user_message=user_message,
        backend_error=backend_error,
        handler_name=handler_name
    )

async def handle_not_found_error(query, entity_name: str, handler_name: str):
    """Handle 'not found' errors with correlation"""
    user_message = f"❌ {entity_name} not found."
    backend_error = f"{entity_name} lookup failed in {handler_name}"
    
    return await CorrelatedErrorHandler.handle_error_with_correlation(
        query_or_update=query,
        user_message=user_message,
        backend_error=backend_error,
        handler_name=handler_name
    )

async def handle_permission_error(query, action: str, handler_name: str):
    """Handle permission errors with correlation"""
    user_message = f"❌ Access denied for {action}."
    backend_error = f"Permission check failed for {action} in {handler_name}"
    
    return await CorrelatedErrorHandler.handle_error_with_correlation(
        query_or_update=query,
        user_message=user_message,
        backend_error=backend_error,
        handler_name=handler_name
    )

# Global instance for convenience
error_handler = CorrelatedErrorHandler()