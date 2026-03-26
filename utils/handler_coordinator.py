"""
Handler Coordination System
Ensures proper handler routing without message consumption conflicts
"""

import logging
from typing import Callable
from telegram import Update
from telegram.ext import ContextTypes
from functools import wraps

logger = logging.getLogger(__name__)


class HandlerCoordinator:
    """Coordinates handler execution to prevent message consumption conflicts"""
    
    @staticmethod
    def should_handle_wallet_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        """
        Determine if wallet handlers should process this input
        Returns False if another conversation is active
        """
        if not update or not update.message or not update.message.text:
            return False
            
        # Check for active escrow conversation
        if context.user_data and "escrow_data" in context.user_data:
            escrow_data = context.user_data["escrow_data"]
            if escrow_data.get("status") == "creating" or "early_escrow_id" in escrow_data:
                logger.debug(f"User {update.effective_user.id} in escrow flow - wallet handler skipping")
                return False
        
        # Check for active exchange conversation  
        if context.user_data and context.user_data.get("exchange_data"):
            logger.debug(f"User {update.effective_user.id} in exchange flow - wallet handler skipping")
            return False
            
        # Check for other conversation states
        if context.user_data and context.user_data.get("in_conversation"):
            logger.debug(f"User {update.effective_user.id} in conversation - wallet handler skipping")
            return False
            
        return True
    
    @staticmethod
    def should_handle_messages_hub(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        """
        Determine if messages hub should process this input
        Returns False if a conversation handler should take priority
        """
        if not update or not update.message or not update.message.text:
            return False
            
        # Check for active escrow conversation
        if context.user_data and "escrow_data" in context.user_data:
            escrow_data = context.user_data["escrow_data"]
            if escrow_data.get("status") == "creating":
                logger.debug(f"User {update.effective_user.id} in escrow creation - messages hub skipping")
                return False
                
        # Check for active exchange conversation
        if context.user_data and context.user_data.get("exchange_data"):
            logger.debug(f"User {update.effective_user.id} in exchange - messages hub skipping")
            return False
            
        # Check for wallet flow
        if context.user_data and context.user_data.get("wallet_flow"):
            logger.debug(f"User {update.effective_user.id} in wallet flow - messages hub skipping")
            return False
            
        return True


def conversation_aware_handler(handler_type: str = "general"):
    """
    Decorator that makes handlers conversation-aware
    Handlers will only execute if no higher priority conversation is active
    """
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
            coordinator = HandlerCoordinator()
            
            # Check if this handler should process the update
            if handler_type == "wallet":
                should_handle = coordinator.should_handle_wallet_input(update, context)
            elif handler_type == "messages_hub":
                should_handle = coordinator.should_handle_messages_hub(update, context)
            else:
                # Default: check for any active conversation
                should_handle = not (
                    context.user_data and (
                        context.user_data.get("escrow_data", {}).get("status") == "creating" or
                        context.user_data.get("exchange_data") or
                        context.user_data.get("in_conversation") or
                        context.user_data.get("wallet_flow")
                    )
                )
            
            if not should_handle:
                # DON'T consume the update - return without processing
                logger.debug(f"Handler {func.__name__} skipping - conversation active")
                return
            
            # Process normally
            return await func(update, context, *args, **kwargs)
            
        return wrapper
    return decorator


# Export singleton instance
handler_coordinator = HandlerCoordinator()