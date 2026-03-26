"""
UI state validation system to prevent duplicate displays and conflicts
"""
from telegram import Update
from telegram.ext import ContextTypes
from typing import Dict
import time
import logging

logger = logging.getLogger(__name__)

class UIStateValidator:
    """Validates UI state to prevent duplicate displays and conflicts"""
    
    # Track last UI actions to prevent rapid duplicates
    _last_actions: Dict[int, Dict[str, float]] = {}
    
    @classmethod
    def can_show_ui(
        cls, 
        user_id: int, 
        ui_type: str, 
        cooldown_seconds: float = 1.0
    ) -> bool:
        """
        Check if UI can be shown (prevents rapid duplicate displays)
        
        Args:
            user_id: Telegram user ID
            ui_type: Type of UI being shown (e.g., 'payment_menu', 'wallet_menu')
            cooldown_seconds: Minimum time between identical UI displays
            
        Returns:
            True if UI can be shown, False if too soon
        """
        current_time = time.time()
        
        if user_id not in cls._last_actions:
            cls._last_actions[user_id] = {}
        
        user_actions = cls._last_actions[user_id]
        last_time = user_actions.get(ui_type, 0)
        
        if current_time - last_time < cooldown_seconds:
            logger.debug(f"UI cooldown active for user {user_id}, type {ui_type}")
            return False
        
        # Update last action time
        user_actions[ui_type] = current_time
        
        # Cleanup old entries (keep only last 10 actions per user)
        if len(user_actions) > 10:
            oldest_key = min(user_actions.keys(), key=lambda k: user_actions[k])
            del user_actions[oldest_key]
        
        return True
    
    @classmethod
    def validate_context_integrity(cls, context: ContextTypes.DEFAULT_TYPE) -> bool:
        """Validate that context data is consistent"""
        if not context.user_data:
            return True  # Empty context is valid
        
        # Check for conflicting conversation states
        if "escrow_data" in context.user_data and "exchange_data" in context.user_data:
            # User shouldn't be in both escrow and exchange flow simultaneously
            logger.warning("Context has both escrow_data and exchange_data - potential conflict")
            return False
        
        return True
    
    @classmethod
    def clear_user_ui_history(cls, user_id: int) -> None:
        """Clear UI action history for user"""
        if user_id in cls._last_actions:
            del cls._last_actions[user_id]
    
    @classmethod
    def prevent_duplicate_callback(
        cls, 
        update: Update, 
        context: ContextTypes.DEFAULT_TYPE,
        callback_type: str
    ) -> bool:
        """
        Prevent duplicate callback processing
        
        Returns:
            True if callback should be processed, False if duplicate
        """
        if not update.callback_query:
            return True
        
        user_id = update.effective_user.id if update.effective_user else 0
        if user_id == 0:
            return True
        
        callback_data = update.callback_query.data
        if not callback_data:
            return True
        
        # Create unique key for this callback
        callback_key = f"{callback_type}:{callback_data}"
        
        return cls.can_show_ui(user_id, callback_key, cooldown_seconds=0.5)

def ui_validation_required(ui_type: str, cooldown: float = 1.0):
    """Decorator to add UI validation to handler functions"""
    def decorator(func):
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
            user_id = update.effective_user.id if update.effective_user else 0
            
            if not UIStateValidator.can_show_ui(user_id, ui_type, cooldown):
                logger.debug(f"UI validation blocked duplicate {ui_type} for user {user_id}")
                return
            
            if not UIStateValidator.validate_context_integrity(context):
                # Clean conflicted context
                if context.user_data:
                    context.user_data.clear()
                logger.warning(f"Context integrity issue resolved for user {user_id}")
            
            return await func(update, context, *args, **kwargs)
        return wrapper
    return decorator