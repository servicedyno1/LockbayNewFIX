"""
Financial Security Decorator
Protects financial operations from split-brain scenarios during Redis outages
Ensures operations fail closed when coordination is unsafe
"""

import logging
import functools
from typing import Callable, Any, Optional
from datetime import datetime

from services.state_manager import state_manager
from services.idempotency_service import IdempotencySecurityError
from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


class FinancialSecurityError(Exception):
    """Exception raised when financial operations cannot be safely processed"""
    pass


def require_financial_coordination(
    operation_type: str,
    enable_degraded_mode: bool = False,
    user_message: Optional[str] = None
):
    """
    CRITICAL SECURITY: Decorator to protect financial operations
    
    Ensures operations fail closed when Redis coordination is unavailable
    and no safe database-backed fallback is configured.
    
    Args:
        operation_type: Type of financial operation (for logging)
        enable_degraded_mode: Allow operation in degraded mode with warnings
        user_message: Custom message to show user when blocking operation
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            # Extract update and context from common handler patterns
            update: Optional[Update] = None
            context: Optional[ContextTypes.DEFAULT_TYPE] = None
            
            for arg in args:
                if isinstance(arg, Update):
                    update = arg
                elif hasattr(arg, 'user_data'):  # ContextTypes
                    context = arg
                    break
            
            # Extract user info for logging
            user_id = None
            if update and update.effective_user:
                user_id = update.effective_user.id
            elif context and hasattr(context, 'user_data'):
                user_id = getattr(context, '_user_id', None)
            
            # CRITICAL SECURITY CHECK: Verify financial coordination is safe
            try:
                if not state_manager.is_financial_safe():
                    # Log critical security block
                    logger.critical(f"🚨 FINANCIAL_SECURITY_BLOCK: {operation_type}")
                    logger.critical(f"   User ID: {user_id}")
                    logger.critical(f"   Redis Available: {state_manager.is_redis_available()}")
                    logger.critical(f"   Fallback Mode: {getattr(state_manager, '_fallback_mode', 'unknown')}")
                    logger.critical("   Reason: Cannot safely coordinate financial operation")
                    
                    # Check if degraded mode is allowed
                    if enable_degraded_mode and state_manager._fallback_mode == "DB_BACKED":
                        logger.warning(f"🛡️ DEGRADED_MODE_ALLOWED: {operation_type} proceeding with database fallback")
                        logger.warning(f"   User ID: {user_id}")
                        logger.warning("   Risk: Reduced coordination guarantees")
                    else:
                        # Block the operation for safety
                        await _send_service_unavailable_message(update, context, operation_type, user_message)
                        raise FinancialSecurityError(f"Financial coordination unavailable for {operation_type}")
                
                # Add coordination metadata to context if available
                if context:
                    if not hasattr(context, 'user_data'):
                        context.user_data = {}
                    
                    context.user_data['_coordination_info'] = {
                        'redis_available': state_manager.is_redis_available(),
                        'fallback_mode': getattr(state_manager, '_fallback_mode', None),
                        'operation_type': operation_type,
                        'check_timestamp': datetime.utcnow().isoformat()
                    }
                
                # Log successful coordination check
                logger.debug(f"✅ FINANCIAL_COORDINATION_OK: {operation_type} (user: {user_id})")
                
                # Execute the protected function
                return await func(*args, **kwargs)
                
            except FinancialSecurityError:
                # Re-raise security errors (already handled)
                raise
            except IdempotencySecurityError as e:
                # Handle idempotency coordination errors
                logger.critical(f"🚨 IDEMPOTENCY_SECURITY_BLOCK: {operation_type}")
                logger.critical(f"   User ID: {user_id}")
                logger.critical(f"   Error: {e}")
                
                await _send_service_unavailable_message(update, context, operation_type, user_message)
                raise FinancialSecurityError(f"Idempotency coordination failed for {operation_type}")
            except Exception as e:
                # Log unexpected errors but don't block (might be unrelated)
                logger.error(f"❌ FINANCIAL_SECURITY_CHECK_ERROR: {operation_type} - {e}")
                logger.error(f"   User ID: {user_id}")
                logger.error("   Proceeding with operation (error unrelated to coordination)")
                
                # Still execute the function - don't let security checks break unrelated functionality
                return await func(*args, **kwargs)
        
        return wrapper
    return decorator


async def _send_service_unavailable_message(
    update: Optional[Update],
    context: Optional[ContextTypes.DEFAULT_TYPE],
    operation_type: str,
    custom_message: Optional[str] = None
) -> None:
    """Send user-friendly service unavailable message"""
    
    if not update or not update.effective_user:
        return
    
    # Default message based on operation type
    default_messages = {
        'cashout': "💳 Cashout services are temporarily unavailable due to system maintenance. Please try again in a few minutes.",
        'deposit': "💰 Deposit processing is temporarily unavailable. Please try again shortly.",
        'escrow': "🔒 Escrow services are temporarily unavailable due to system maintenance.",
        'transfer': "💸 Transfer services are temporarily unavailable. Please try again later.",
        'payment': "💳 Payment processing is temporarily unavailable due to maintenance.",
    }
    
    # Use custom message or default based on operation type
    message = custom_message or default_messages.get(operation_type.lower(), 
        f"🛠 {operation_type} services are temporarily unavailable due to system maintenance. Please try again in a few minutes.")
    
    # Add technical info for admins
    admin_info = ""
    if context and hasattr(context, 'user_data'):
        coordination_info = context.user_data.get('_coordination_info', {})
        if coordination_info.get('fallback_mode') == 'FAIL_CLOSED':
            admin_info = "\n\n🔧 (Admin: Redis coordination unavailable, operating in FAIL_CLOSED mode)"
        elif not coordination_info.get('redis_available'):
            admin_info = "\n\n🔧 (Admin: Redis unavailable, safe fallback not configured)"
    
    try:
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(
                message + admin_info,
                parse_mode='HTML'
            )
        else:
            await update.message.reply_text(
                message + admin_info,
                parse_mode='HTML'
            )
    except Exception as e:
        logger.error(f"❌ Failed to send service unavailable message: {e}")


def check_financial_coordination_status() -> dict:
    """
    Get current status of financial coordination infrastructure
    
    Returns:
        Dict with coordination status information
    """
    return {
        'redis_available': state_manager.is_redis_available(),
        'financial_safe': state_manager.is_financial_safe(),
        'fallback_mode': getattr(state_manager, '_fallback_mode', None),
        'using_fallback': getattr(state_manager, '_using_fallback', False),
        'coordination_timestamp': datetime.utcnow().isoformat()
    }