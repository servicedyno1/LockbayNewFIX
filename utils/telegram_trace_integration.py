"""
Telegram Bot Trace Integration
Provides automatic trace correlation for all Telegram bot operations including
message handling, callback queries, and user interactions
"""

import asyncio
import json
from functools import wraps
from typing import Dict, Any, Optional, Callable
from datetime import datetime

from telegram import Update, Message
from telegram.ext import ContextTypes

from utils.trace_correlation import (
    trace_manager, OperationType, TraceStatus
)
from utils.trace_logging_integration import (
    get_trace_logger, MonitoringIntegration
)

logger = get_trace_logger(__name__)

class TelegramTraceExtractor:
    """Extract trace-relevant information from Telegram updates"""
    
    @staticmethod
    def extract_user_info(update: Update) -> Dict[str, Any]:
        """Extract user information from update"""
        user_info = {}
        
        telegram_user = None
        if update.message and update.message.from_user:
            telegram_user = update.message.from_user
        elif update.callback_query and update.callback_query.from_user:
            telegram_user = update.callback_query.from_user
        elif update.edited_message and update.edited_message.from_user:
            telegram_user = update.edited_message.from_user
            
        if telegram_user:
            user_info = {
                'telegram_user_id': telegram_user.id,
                'username': telegram_user.username,
                'first_name': telegram_user.first_name,
                'last_name': telegram_user.last_name,
                'is_bot': telegram_user.is_bot,
                'language_code': telegram_user.language_code
            }
            
        return user_info
    
    @staticmethod
    def extract_message_info(update: Update) -> Dict[str, Any]:
        """Extract message information from update"""
        message_info = {}
        
        message = None
        if update.message:
            message = update.message
        elif update.edited_message:
            message = update.edited_message
        elif update.callback_query and update.callback_query.message:
            message = update.callback_query.message
            
        if message:
            message_info = {
                'message_id': message.message_id,
                'chat_id': message.chat.id,
                'chat_type': message.chat.type,
                'message_date': message.date.isoformat() if message.date else None,
                'has_text': bool(message.text),
                'has_photo': bool(message.photo),
                'has_document': bool(message.document),
                'message_type': TelegramTraceExtractor._determine_message_type(message)
            }
            
            # Add text content (truncated for security)
            if message.text:
                message_info['text_preview'] = message.text[:100]
                message_info['text_length'] = len(message.text)
                
        return message_info
    
    @staticmethod
    def extract_callback_info(update: Update) -> Dict[str, Any]:
        """Extract callback query information from update"""
        callback_info = {}
        
        if update.callback_query:
            callback_query = update.callback_query
            callback_info = {
                'callback_query_id': callback_query.id,
                'callback_data': callback_query.data[:100] if callback_query.data else None,  # Truncated
                'callback_data_length': len(callback_query.data) if callback_query.data else 0,
                'inline_message_id': callback_query.inline_message_id
            }
            
        return callback_info
    
    @staticmethod
    def _determine_message_type(message: Message) -> str:
        """Determine the type of message"""
        if message.text:
            if message.text.startswith('/'):
                return 'command'
            return 'text'
        elif message.photo:
            return 'photo'
        elif message.document:
            return 'document'
        elif message.voice:
            return 'voice'
        elif message.video:
            return 'video'
        elif message.sticker:
            return 'sticker'
        elif message.contact:
            return 'contact'
        elif message.location:
            return 'location'
        else:
            return 'unknown'
    
    @staticmethod
    def extract_update_correlation(update: Update) -> Dict[str, Any]:
        """Extract all correlation data from Telegram update"""
        correlation_data = {
            'update_id': update.update_id,
            'update_type': TelegramTraceExtractor._determine_update_type(update)
        }
        
        # Merge all extracted information
        correlation_data.update(TelegramTraceExtractor.extract_user_info(update))
        correlation_data.update(TelegramTraceExtractor.extract_message_info(update))
        correlation_data.update(TelegramTraceExtractor.extract_callback_info(update))
        
        return correlation_data
    
    @staticmethod
    def _determine_update_type(update: Update) -> str:
        """Determine the type of Telegram update"""
        if update.message:
            return 'message'
        elif update.edited_message:
            return 'edited_message'
        elif update.callback_query:
            return 'callback_query'
        elif update.inline_query:
            return 'inline_query'
        elif update.chosen_inline_result:
            return 'chosen_inline_result'
        else:
            return 'unknown'

def telegram_traced(
    operation_name: Optional[str] = None,
    capture_update: bool = True,
    capture_context: bool = False,
    auto_correlate_user: bool = True
):
    """
    Decorator for Telegram handler functions to add automatic trace correlation
    
    Args:
        operation_name: Custom operation name (defaults to function name)
        capture_update: Whether to capture update data in trace
        capture_context: Whether to capture bot context data
        auto_correlate_user: Whether to automatically correlate with user database record
    """
    
    def decorator(func: Callable) -> Callable:
        actual_operation_name = operation_name or func.__name__
        
        @wraps(func)
        async def async_wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
            # Extract correlation data from Telegram update
            correlation_data = TelegramTraceExtractor.extract_update_correlation(update)
            
            # Determine operation type based on update
            if update.callback_query:
                operation_type = OperationType.TELEGRAM_CALLBACK
            else:
                operation_type = OperationType.TELEGRAM_MESSAGE
                
            # Get user ID for correlation
            user_id = correlation_data.get('telegram_user_id')
            
            # Auto-correlate with database user if requested
            db_user_id = None
            if auto_correlate_user and user_id:
                db_user_id = await TelegramTraceExtractor._get_database_user_id(user_id)
                if db_user_id:
                    correlation_data['database_user_id'] = db_user_id
                    
            # Create trace context
            trace_context = trace_manager.create_trace_context(
                operation_type=operation_type,
                operation_name=actual_operation_name,
                user_id=db_user_id,
                correlation_data=correlation_data
            )
            
            if not trace_context:
                logger.warning(f"Failed to create trace context for {actual_operation_name}")
                return await func(update, context, *args, **kwargs)
            
            # Set trace context
            trace_manager.set_trace_context(trace_context)
            
            # Start main operation span
            span = trace_manager.start_span(f"telegram_{actual_operation_name}", operation_type.value)
            
            try:
                # Add tags to span
                if span:
                    span.add_tag('telegram_update_id', update.update_id)
                    span.add_tag('telegram_user_id', user_id)
                    span.add_tag('update_type', correlation_data.get('update_type'))
                    span.add_tag('handler_function', func.__name__)
                    
                    if capture_update:
                        span.add_tag('update_data', json.dumps(correlation_data, default=str)[:1000])
                        
                    if capture_context and context.user_data:
                        span.add_tag('user_context', str(context.user_data)[:500])
                        
                # Log operation start
                logger.info(
                    f"🤖 Telegram Operation Started: {actual_operation_name}",
                    operation_details={
                        'update_id': update.update_id,
                        'user_id': user_id,
                        'update_type': correlation_data.get('update_type'),
                        'message_type': correlation_data.get('message_type'),
                        'chat_id': correlation_data.get('chat_id')
                    }
                )
                
                # Execute the handler
                start_time = datetime.utcnow()
                result = await func(update, context, *args, **kwargs)
                execution_time = (datetime.utcnow() - start_time).total_seconds() * 1000
                
                # Log successful completion
                logger.info(
                    f"✅ Telegram Operation Completed: {actual_operation_name}",
                    performance_metrics={
                        'execution_time_ms': execution_time,
                        'result_type': type(result).__name__ if result is not None else 'None'
                    }
                )
                
                # Complete span and trace
                if span:
                    span.add_tag('execution_time_ms', execution_time)
                    span.add_tag('result_type', type(result).__name__ if result is not None else 'None')
                    trace_manager.finish_span(span, TraceStatus.COMPLETED)
                    
                trace_manager.complete_trace(
                    trace_context.trace_id, 
                    TraceStatus.COMPLETED,
                    performance_metrics={'execution_time_ms': execution_time}
                )
                
                # Integrate with monitoring systems
                MonitoringIntegration.correlate_performance_metrics({
                    'operation': actual_operation_name,
                    'execution_time_ms': execution_time,
                    'telegram_operation': True,
                    'user_id': user_id
                })
                
                return result
                
            except Exception as e:
                # Handle errors with full trace correlation
                execution_time = (datetime.utcnow() - trace_context.start_time).total_seconds() * 1000
                
                error_info = {
                    'error_type': type(e).__name__,
                    'error_message': str(e),
                    'handler_function': func.__name__,
                    'telegram_context': {
                        'update_id': update.update_id,
                        'user_id': user_id,
                        'update_type': correlation_data.get('update_type')
                    }
                }
                
                # Log error with trace correlation
                logger.error(
                    f"❌ Telegram Operation Failed: {actual_operation_name}",
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
        def sync_wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
            # Similar implementation for sync functions (rare in modern Telegram bots)
            correlation_data = TelegramTraceExtractor.extract_update_correlation(update)
            
            operation_type = OperationType.TELEGRAM_CALLBACK if update.callback_query else OperationType.TELEGRAM_MESSAGE
            user_id = correlation_data.get('telegram_user_id')
            
            trace_context = trace_manager.create_trace_context(
                operation_type=operation_type,
                operation_name=actual_operation_name,
                user_id=user_id,
                correlation_data=correlation_data
            )
            
            if not trace_context:
                return func(update, context, *args, **kwargs)
                
            trace_manager.set_trace_context(trace_context)
            span = trace_manager.start_span(f"telegram_{actual_operation_name}", operation_type.value)
            
            try:
                if span:
                    span.add_tag('telegram_update_id', update.update_id)
                    span.add_tag('telegram_user_id', user_id)
                    
                result = func(update, context, *args, **kwargs)
                
                if span:
                    trace_manager.finish_span(span, TraceStatus.COMPLETED)
                    
                trace_manager.complete_trace(trace_context.trace_id, TraceStatus.COMPLETED)
                return result
                
            except Exception as e:
                error_info = {
                    'error_type': type(e).__name__,
                    'error_message': str(e),
                    'handler_function': func.__name__
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

# Utility functions for common Telegram operations
async def correlate_telegram_user_session(telegram_user_id: int, session_data: Dict[str, Any]):
    """Correlate Telegram user with session data"""
    current_context = trace_manager.get_current_trace_context()
    if current_context:
        current_context.correlation_data.update({
            'session_correlation': True,
            'session_data': session_data,
            'telegram_user_id': telegram_user_id
        })
        
async def add_conversation_correlation(conversation_state: str, step_data: Optional[Dict[str, Any]] = None):
    """Add conversation flow correlation to current trace"""
    current_context = trace_manager.get_current_trace_context()
    if current_context:
        current_context.correlation_data.update({
            'conversation_state': conversation_state,
            'conversation_step_data': step_data or {},
            'conversation_flow': True
        })
        
async def trace_telegram_api_call(api_method: str, api_params: Dict[str, Any]):
    """Trace Telegram API calls for debugging"""
    current_context = trace_manager.get_current_trace_context()
    if current_context:
        api_span = trace_manager.start_span(f"telegram_api_{api_method}", "telegram_api")
        
        if api_span:
            api_span.add_tag('api_method', api_method)
            api_span.add_tag('api_params', json.dumps(api_params, default=str)[:500])
            
            # This span will be finished by the caller
            return api_span
    return None

# Enhanced TelegramTraceExtractor methods
class TelegramTraceExtractorV2:
    # ... (previous methods remain the same)
    
    @staticmethod
    async def _get_database_user_id(telegram_user_id: int) -> Optional[int]:
        """Get database user ID from Telegram user ID"""
        try:
            from database import SessionLocal
            from models import User
            
            with SessionLocal() as session:
                user = session.query(User).filter(User.telegram_id == telegram_user_id).first()
                return user.id if user else None
                
        except Exception as e:
            logger.warning(f"Failed to correlate Telegram user {telegram_user_id} with database: {e}")
            return None

class TelegramPerformanceTracker:
    """Track performance metrics for Telegram operations"""
    
    @staticmethod
    def track_message_processing_time(message_type: str, processing_time_ms: float):
        """Track message processing performance"""
        current_context = trace_manager.get_current_trace_context()
        if current_context:
            if 'performance_metrics' not in current_context.correlation_data:
                current_context.correlation_data['performance_metrics'] = {}
                
            current_context.correlation_data['performance_metrics'][f'{message_type}_processing_ms'] = processing_time_ms
            
    @staticmethod
    def track_callback_response_time(callback_type: str, response_time_ms: float):
        """Track callback query response performance"""
        current_context = trace_manager.get_current_trace_context()
        if current_context:
            if 'performance_metrics' not in current_context.correlation_data:
                current_context.correlation_data['performance_metrics'] = {}
                
            current_context.correlation_data['performance_metrics'][f'{callback_type}_response_ms'] = response_time_ms

# Integration utilities
def setup_telegram_trace_integration():
    """Setup Telegram trace integration with existing systems"""
    logger.info("🤖 Setting up Telegram trace integration...")
    
    # This function can be called during bot initialization
    # to ensure trace correlation is properly configured
    
    logger.info("✅ Telegram trace integration configured")

logger.info("🤖 Telegram trace integration module initialized")