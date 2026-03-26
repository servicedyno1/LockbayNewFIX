"""
Handler Decorators for Automatic Audit Logging
Provides decorators for comprehensive handler entry/exit logging with timing and trace correlation
"""

import logging
import time
import inspect
from functools import wraps
from typing import Any, Dict, Optional, Callable
from datetime import datetime
import traceback

from telegram import Update
from telegram.ext import ContextTypes

from utils.comprehensive_audit_logger import (
    ComprehensiveAuditLogger,
    AuditEventType,
    AuditLevel,
    TraceContext,
    RelatedIDs,
    PIISafeDataExtractor
)
from utils.universal_session_manager import UniversalSessionManager, SessionType, OperationStatus
from utils.admin_security import is_admin_secure

logger = logging.getLogger(__name__)

# Initialize global instances
audit_logger = ComprehensiveAuditLogger()
pii_extractor = PIISafeDataExtractor()
session_manager = UniversalSessionManager()


class HandlerContext:
    """Context information for handler execution"""
    
    def __init__(self, handler_name: str, event_type: AuditEventType, action: Optional[str] = None):
        self.handler_name = handler_name
        self.event_type = event_type
        self.action = action or handler_name
        self.start_time: Optional[float] = None
        self.end_time: Optional[float] = None
        self.trace_id: Optional[str] = None
        self.user_id: Optional[int] = None
        self.chat_id: Optional[int] = None
        self.message_id: Optional[int] = None
        self.session_id: Optional[str] = None
        self.conversation_id: Optional[str] = None
        self.is_admin = False
        self.related_ids = RelatedIDs()
        self.success = True
        self.error_details: Optional[Exception] = None
        self.payload_metadata: Dict[str, Any] = {}
    
    def start_timing(self):
        """Start timing the handler execution"""
        self.start_time = time.time()
    
    def end_timing(self):
        """End timing and calculate latency"""
        self.end_time = time.time()
    
    @property
    def latency_ms(self) -> Optional[float]:
        """Calculate latency in milliseconds"""
        if self.start_time and self.end_time:
            return (self.end_time - self.start_time) * 1000
        return None


def extract_telegram_context(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Dict[str, Any]:
    """
    Safely extract context information from Telegram Update and Context
    
    Args:
        update: Telegram Update object
        context: Telegram Context object
        
    Returns:
        Dictionary with extracted context information
    """
    extracted = {
        'user_id': None,
        'chat_id': None,
        'message_id': None,
        'is_admin': False,
        'callback_data': None,
        'command': None,
        'message_text_length': None,
        'has_attachments': False,
        'button_count': None
    }
    
    try:
        # Extract user information
        if update.effective_user:
            extracted['user_id'] = update.effective_user.id
            extracted['is_admin'] = is_admin_secure(update.effective_user.id)
        
        # Extract chat information
        if update.effective_chat:
            extracted['chat_id'] = update.effective_chat.id
        
        # Extract message information
        if update.effective_message:
            extracted['message_id'] = update.effective_message.message_id
            
            # Check for text and attachments
            if update.effective_message.text:
                extracted['message_text_length'] = len(update.effective_message.text)
                if update.effective_message.text.startswith('/'):
                    extracted['command'] = update.effective_message.text.split()[0]
            
            # Check for attachments
            message = update.effective_message
            extracted['has_attachments'] = any([
                message.document, message.photo, message.video,
                message.audio, message.voice, message.sticker
            ])
        
        # Extract callback query information
        if update.callback_query:
            if update.callback_query.data:
                extracted['callback_data'] = update.callback_query.data[:50]  # First 50 chars only
            
            # Count buttons in original message
            message = update.callback_query.message
            if message and hasattr(message, 'reply_markup'):
                reply_markup = getattr(message, 'reply_markup', None)
                if reply_markup and hasattr(reply_markup, 'inline_keyboard'):
                    extracted['button_count'] = sum(len(row) for row in reply_markup.inline_keyboard)
    
    except Exception as e:
        logger.warning(f"Error extracting Telegram context: {e}")
    
    return extracted


def setup_trace_context(handler_ctx: HandlerContext, telegram_ctx: Dict[str, Any]) -> None:
    """
    Setup trace context for the handler execution
    
    Args:
        handler_ctx: Handler context object
        telegram_ctx: Extracted Telegram context
    """
    try:
        # Generate or get existing trace ID
        existing_trace = TraceContext.get_trace_id()
        if not existing_trace:
            handler_ctx.trace_id = TraceContext.generate_trace_id()
            TraceContext.set_trace_id(handler_ctx.trace_id)
        else:
            handler_ctx.trace_id = existing_trace
        
        # Set user context
        if telegram_ctx['user_id']:
            handler_ctx.user_id = telegram_ctx['user_id']
            handler_ctx.chat_id = telegram_ctx['chat_id']
            handler_ctx.message_id = telegram_ctx['message_id']
            handler_ctx.is_admin = telegram_ctx['is_admin']
            
            TraceContext.set_user_context(
                user_id=handler_ctx.user_id,
                chat_id=handler_ctx.chat_id
            )
        
        # Try to get session context from session manager
        existing_session = TraceContext.get_session_id()
        if existing_session:
            handler_ctx.session_id = existing_session
        
        existing_conversation = TraceContext.get_conversation_id()
        if existing_conversation:
            handler_ctx.conversation_id = existing_conversation
            
    except Exception as e:
        logger.warning(f"Error setting up trace context: {e}")


def extract_related_ids_from_context(context: ContextTypes.DEFAULT_TYPE) -> RelatedIDs:
    """
    Extract related entity IDs from context.user_data
    
    Args:
        context: Telegram context object
        
    Returns:
        RelatedIDs object with extracted IDs
    """
    related = RelatedIDs()
    
    try:
        if context.user_data:
            # Common keys to check for in user_data
            id_mappings = {
                'escrow_id': ['escrow_id', 'current_escrow_id', 'active_escrow'],
                'exchange_order_id': ['exchange_id', 'order_id', 'exchange_order_id'],
                'dispute_id': ['dispute_id', 'current_dispute_id'],
                'cashout_id': ['cashout_id', 'withdrawal_id'],
                'transaction_id': ['transaction_id', 'txn_id'],
                'referral_id': ['referral_id'],
                'user_rating_id': ['rating_id', 'user_rating_id']
            }
            
            for related_field, possible_keys in id_mappings.items():
                for key in possible_keys:
                    if key in context.user_data:
                        value = context.user_data[key]
                        if value:
                            setattr(related, related_field, str(value))
                            break
    
    except Exception as e:
        logger.warning(f"Error extracting related IDs: {e}")
    
    return related


def log_handler_entry(handler_ctx: HandlerContext) -> None:
    """Log handler entry"""
    try:
        audit_logger.log(
            level=AuditLevel.INFO,
            event_type=handler_ctx.event_type,
            action=f"{handler_ctx.action}_start",
            user_id=handler_ctx.user_id,
            chat_id=handler_ctx.chat_id,
            message_id=handler_ctx.message_id,
            is_admin=handler_ctx.is_admin,
            session_id=handler_ctx.session_id,
            conversation_id=handler_ctx.conversation_id,
            trace_id=handler_ctx.trace_id,
            related_ids=handler_ctx.related_ids,
            result="handler_entry",
            payload_metadata=handler_ctx.payload_metadata
        )
    except Exception as e:
        logger.error(f"Failed to log handler entry: {e}")


def log_handler_exit(handler_ctx: HandlerContext) -> None:
    """Log handler exit with timing and result"""
    try:
        result = "success" if handler_ctx.success else "error"
        level = AuditLevel.INFO if handler_ctx.success else AuditLevel.ERROR
        
        # Include error details in payload metadata if present
        payload_metadata = handler_ctx.payload_metadata.copy()
        if handler_ctx.error_details:
            payload_metadata['error_type'] = type(handler_ctx.error_details).__name__
            payload_metadata['error_message'] = str(handler_ctx.error_details)[:200]  # Truncate
        
        audit_logger.log(
            level=level,
            event_type=handler_ctx.event_type,
            action=f"{handler_ctx.action}_end",
            user_id=handler_ctx.user_id,
            chat_id=handler_ctx.chat_id,
            message_id=handler_ctx.message_id,
            is_admin=handler_ctx.is_admin,
            session_id=handler_ctx.session_id,
            conversation_id=handler_ctx.conversation_id,
            trace_id=handler_ctx.trace_id,
            related_ids=handler_ctx.related_ids,
            result=result,
            latency_ms=handler_ctx.latency_ms,
            payload_metadata=payload_metadata
        )
    except Exception as e:
        logger.error(f"Failed to log handler exit: {e}")


def audit_handler(
    event_type: AuditEventType = AuditEventType.USER_INTERACTION,
    action: Optional[str] = None,
    session_type: Optional[SessionType] = None
):
    """
    Main decorator for handler audit logging
    
    Args:
        event_type: Type of audit event
        action: Custom action name (defaults to function name)
        session_type: Session type for session tracking
    """
    def decorator(func: Callable) -> Callable:
        handler_name = func.__name__
        action_name = action or handler_name
        is_async = inspect.iscoroutinefunction(func)
        
        if is_async:
            @wraps(func)
            async def async_wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
                # Setup handler context
                handler_ctx = HandlerContext(handler_name, event_type, action_name)
                
                try:
                    # Start timing
                    handler_ctx.start_timing()
                    
                    # Extract Telegram context
                    telegram_ctx = extract_telegram_context(update, context)
                    
                    # Setup trace context
                    setup_trace_context(handler_ctx, telegram_ctx)
                    
                    # Extract related IDs
                    handler_ctx.related_ids = extract_related_ids_from_context(context)
                    
                    # Extract safe payload metadata
                    handler_ctx.payload_metadata = pii_extractor.extract_safe_payload_metadata(update).to_dict()
                    handler_ctx.payload_metadata.update({
                        'handler_name': handler_name,
                        'is_async': True,
                        **{k: v for k, v in telegram_ctx.items() if v is not None and k not in ['user_id', 'chat_id', 'message_id', 'is_admin']}
                    })
                    
                    # Log entry
                    log_handler_entry(handler_ctx)
                    
                    # Execute handler
                    result = await func(update, context, *args, **kwargs)
                    
                    # Mark success
                    handler_ctx.success = True
                    
                    return result
                    
                except Exception as e:
                    # Mark failure and capture error
                    handler_ctx.success = False
                    handler_ctx.error_details = e
                    
                    # Log the error (but don't fail the logging)
                    logger.error(f"Handler {handler_name} failed: {e}")
                    logger.error(f"Traceback: {traceback.format_exc()}")
                    
                    # Re-raise the original exception
                    raise
                
                finally:
                    # End timing and log exit
                    handler_ctx.end_timing()
                    log_handler_exit(handler_ctx)
            
            return async_wrapper
        
        else:
            @wraps(func)
            def sync_wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
                # Setup handler context
                handler_ctx = HandlerContext(handler_name, event_type, action_name)
                
                try:
                    # Start timing
                    handler_ctx.start_timing()
                    
                    # Extract Telegram context
                    telegram_ctx = extract_telegram_context(update, context)
                    
                    # Setup trace context
                    setup_trace_context(handler_ctx, telegram_ctx)
                    
                    # Extract related IDs
                    handler_ctx.related_ids = extract_related_ids_from_context(context)
                    
                    # Extract safe payload metadata
                    handler_ctx.payload_metadata = pii_extractor.extract_safe_payload_metadata(update).to_dict()
                    handler_ctx.payload_metadata.update({
                        'handler_name': handler_name,
                        'is_async': False,
                        **{k: v for k, v in telegram_ctx.items() if v is not None and k not in ['user_id', 'chat_id', 'message_id', 'is_admin']}
                    })
                    
                    # Log entry
                    log_handler_entry(handler_ctx)
                    
                    # Execute handler
                    result = func(update, context, *args, **kwargs)
                    
                    # Mark success
                    handler_ctx.success = True
                    
                    return result
                    
                except Exception as e:
                    # Mark failure and capture error
                    handler_ctx.success = False
                    handler_ctx.error_details = e
                    
                    # Log the error (but don't fail the logging)
                    logger.error(f"Handler {handler_name} failed: {e}")
                    logger.error(f"Traceback: {traceback.format_exc()}")
                    
                    # Re-raise the original exception
                    raise
                
                finally:
                    # End timing and log exit
                    handler_ctx.end_timing()
                    log_handler_exit(handler_ctx)
            
            return sync_wrapper
    
    return decorator


# Convenience decorators for specific handler types

def audit_admin_handler(action: Optional[str] = None):
    """Decorator for admin handlers"""
    return audit_handler(
        event_type=AuditEventType.ADMIN,
        action=action,
        session_type=None
    )


def audit_escrow_handler(action: Optional[str] = None):
    """Decorator for escrow/transaction handlers"""
    return audit_handler(
        event_type=AuditEventType.TRANSACTION,
        action=action,
        session_type=SessionType.ESCROW_CREATE
    )


def audit_exchange_handler(action: Optional[str] = None):
    """Decorator for exchange handlers"""
    return audit_handler(
        event_type=AuditEventType.TRANSACTION,
        action=action,
        session_type=SessionType.DIRECT_EXCHANGE
    )


def audit_conversation_handler(action: Optional[str] = None):
    """Decorator for conversation handlers"""
    return audit_handler(
        event_type=AuditEventType.CONVERSATION,
        action=action,
        session_type=SessionType.TRADE_CHAT
    )


def audit_wallet_handler(action: Optional[str] = None):
    """Decorator for wallet handlers"""
    return audit_handler(
        event_type=AuditEventType.TRANSACTION,
        action=action,
        session_type=SessionType.WALLET_OPERATION
    )


def audit_dispute_handler(action: Optional[str] = None):
    """Decorator for dispute handlers"""
    return audit_handler(
        event_type=AuditEventType.CONVERSATION,
        action=action,
        session_type=SessionType.DISPUTE_CHAT
    )


def audit_system_handler(action: Optional[str] = None):
    """Decorator for system handlers"""
    return audit_handler(
        event_type=AuditEventType.SYSTEM,
        action=action,
        session_type=None
    )


# Decorator for callback query handlers specifically
def audit_callback_handler(action: Optional[str] = None):
    """
    Specialized decorator for callback query handlers
    Extracts button/callback specific metadata
    """
    def decorator(func: Callable) -> Callable:
        @audit_handler(
            event_type=AuditEventType.USER_INTERACTION,
            action=action
        )
        @wraps(func)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
            # Additional callback-specific logging
            if update.callback_query and update.callback_query.data:
                callback_data = update.callback_query.data
                
                # Log the specific callback action
                try:
                    audit_logger.log(
                        level=AuditLevel.DEBUG,
                        event_type=AuditEventType.USER_INTERACTION,
                        action="callback_button_pressed",
                        user_id=update.effective_user.id if update.effective_user else None,
                        chat_id=update.effective_chat.id if update.effective_chat else None,
                        payload_metadata={
                            'callback_data': callback_data[:100],  # First 100 chars
                            'handler_name': func.__name__,
                            'is_callback': True
                        }
                    )
                except Exception as e:
                    logger.warning(f"Failed to log callback details: {e}")
            
            # Execute the original handler
            return await func(update, context, *args, **kwargs)
        
        return wrapper
    return decorator


# Session management integration
def with_session_tracking(session_type: SessionType, priority: int = 0):
    """
    Decorator to add session tracking to handlers
    
    Args:
        session_type: Type of session to create/track
        priority: Session priority level
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
            try:
                # Get user ID
                user_id = update.effective_user.id if update.effective_user else None
                if not user_id:
                    return await func(update, context, *args, **kwargs)
                
                # Generate session ID
                session_type_str = session_type.value if hasattr(session_type, 'value') else str(session_type)
                session_id = f"{session_type_str}_{user_id}_{int(time.time())}"
                
                # Create session
                session_data = session_manager.create_session(
                    user_id=user_id,
                    session_type=session_type,
                    session_id=session_id,
                    metadata={
                        'handler_name': func.__name__,
                        'chat_id': update.effective_chat.id if update.effective_chat else None,
                        'started_at': datetime.utcnow().isoformat()
                    },
                    priority=priority
                )
                
                if session_data:
                    # Set session context for audit logging
                    TraceContext.set_session_id(session_id)
                    
                    # Store session in context for handler use
                    if not context.user_data:
                        context.user_data = {}
                    context.user_data['current_session_id'] = session_id
                    context.user_data['session_type'] = session_type.value if hasattr(session_type, 'value') else str(session_type)
                
                try:
                    # Execute handler
                    result = await func(update, context, *args, **kwargs)
                    
                    # Mark session as completed on success
                    if session_data:
                        session_manager.update_session(
                            session_id,
                            status=OperationStatus.COMPLETED
                        )
                    
                    return result
                
                except Exception as e:
                    # Mark session as failed on error
                    if session_data:
                        session_manager.update_session(
                            session_id,
                            status=OperationStatus.FAILED,
                            metadata_update={'error': str(e)}
                        )
                    raise
                
                finally:
                    # Clean up session after some time (let it expire naturally)
                    pass
                    
            except Exception as e:
                logger.warning(f"Session tracking failed for {func.__name__}: {e}")
                # Continue with handler execution even if session tracking fails
                return await func(update, context, *args, **kwargs)
        
        return wrapper
    return decorator


# Combined decorators for common use cases
def audit_escrow_with_session(action: Optional[str] = None):
    """Combined escrow handler with session tracking"""
    def decorator(func: Callable) -> Callable:
        @with_session_tracking(SessionType.ESCROW_CREATE)
        @audit_escrow_handler(action)
        @wraps(func)
        async def wrapper(*args, **kwargs):
            return await func(*args, **kwargs)
        return wrapper
    return decorator


def audit_exchange_with_session(action: Optional[str] = None):
    """Combined exchange handler with session tracking"""
    def decorator(func: Callable) -> Callable:
        @with_session_tracking(SessionType.DIRECT_EXCHANGE)
        @audit_exchange_handler(action)
        @wraps(func)
        async def wrapper(*args, **kwargs):
            return await func(*args, **kwargs)
        return wrapper
    return decorator


def audit_wallet_with_session(action: Optional[str] = None):
    """Combined wallet handler with session tracking"""
    def decorator(func: Callable) -> Callable:
        @with_session_tracking(SessionType.WALLET_OPERATION)
        @audit_wallet_handler(action)
        @wraps(func)
        async def wrapper(*args, **kwargs):
            return await func(*args, **kwargs)
        return wrapper
    return decorator


# Error recovery decorator
def with_error_recovery(recovery_action: str = "show_main_menu"):
    """
    Decorator to add error recovery to handlers
    
    Args:
        recovery_action: Action to take on error (e.g., "show_main_menu", "show_error_message")
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
            try:
                return await func(update, context, *args, **kwargs)
            except Exception as e:
                logger.error(f"Handler {func.__name__} failed with error recovery: {e}")
                
                # Log the error with recovery action
                try:
                    audit_logger.log(
                        level=AuditLevel.ERROR,
                        event_type=AuditEventType.SYSTEM,
                        action="error_recovery_triggered",
                        user_id=update.effective_user.id if update.effective_user else None,
                        chat_id=update.effective_chat.id if update.effective_chat else None,
                        payload_metadata={
                            'original_handler': func.__name__,
                            'recovery_action': recovery_action,
                            'error_type': type(e).__name__,
                            'error_message': str(e)[:200]
                        }
                    )
                except Exception as log_error:
                    logger.error(f"Failed to log error recovery: {log_error}")
                
                # Implement recovery actions
                if recovery_action == "show_main_menu":
                    try:
                        from utils.keyboards import main_menu_keyboard
                        if update.effective_message:
                            await update.effective_message.reply_text(
                                "⚠️ An error occurred. Returning to main menu.",
                                reply_markup=main_menu_keyboard()
                            )
                    except Exception as recovery_error:
                        logger.error(f"Error recovery failed: {recovery_error}")
                
                elif recovery_action == "show_error_message":
                    try:
                        if update.effective_message:
                            await update.effective_message.reply_text(
                                "❌ An error occurred. Please try again or contact support."
                            )
                    except Exception as recovery_error:
                        logger.error(f"Error recovery failed: {recovery_error}")
                
                # Don't re-raise the exception when using error recovery
                return None
        
        return wrapper
    return decorator


# Performance monitoring decorator
def with_performance_monitoring(warning_threshold_ms: float = 1000):
    """
    Decorator to monitor handler performance
    
    Args:
        warning_threshold_ms: Threshold in milliseconds to log performance warnings
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            start_time = time.time()
            
            try:
                result = await func(*args, **kwargs)
                end_time = time.time()
                latency_ms = (end_time - start_time) * 1000
                
                # Log performance warning if threshold exceeded
                if latency_ms > warning_threshold_ms:
                    logger.warning(f"Handler {func.__name__} took {latency_ms:.2f}ms (threshold: {warning_threshold_ms}ms)")
                    
                    try:
                        audit_logger.log(
                            level=AuditLevel.WARNING,
                            event_type=AuditEventType.SYSTEM,
                            action="performance_warning",
                            payload_metadata={
                                'handler_name': func.__name__,
                                'latency_ms': latency_ms,
                                'threshold_ms': warning_threshold_ms,
                                'performance_issue': True
                            }
                        )
                    except Exception as log_error:
                        logger.error(f"Failed to log performance warning: {log_error}")
                
                return result
                
            except Exception as e:
                end_time = time.time()
                latency_ms = (end_time - start_time) * 1000
                logger.error(f"Handler {func.__name__} failed after {latency_ms:.2f}ms: {e}")
                raise
        
        return wrapper
    return decorator