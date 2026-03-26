"""
Escrow Context Rehydration Helper
Fixes race condition where context.user_data["escrow_data"] is missing due to rapid escrow creation
"""

import logging
from typing import Optional, Any, Tuple
from telegram.ext import ContextTypes
from database import async_managed_session
from sqlalchemy import select
from models import User

logger = logging.getLogger(__name__)


async def _get_user_conversation_state(user_id: int) -> Tuple[Optional[str], Optional[Any]]:
    """
    Get user conversation state from database.
    
    Args:
        user_id: Telegram user ID
        
    Returns:
        Tuple of (conversation_state, conversation_state_timestamp)
    """
    async with async_managed_session() as session:
        try:
            stmt = select(User).where(User.telegram_id == user_id)
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()
            
            if user:
                state = getattr(user, 'conversation_state', None)
                timestamp = getattr(user, 'conversation_state_timestamp', None)
                return (state, timestamp)
            
            return (None, None)
        except Exception as e:
            logger.error(f"Error getting user state for {user_id}: {e}")
            return (None, None)


async def ensure_escrow_context(
    user_id: int,
    context: ContextTypes.DEFAULT_TYPE
) -> bool:
    """
    Ensure escrow context exists in context.user_data, rehydrating from database if needed.
    
    This prevents "Session expired" errors caused by race conditions where:
    - User clicks "Create Escrow" quickly
    - User types seller username before context.user_data is fully initialized
    - Multiple rapid escrow creations clear/reinitialize context
    
    Args:
        user_id: Telegram user ID
        context: Bot context
        
    Returns:
        True if context exists or was successfully rehydrated, False otherwise
    """
    # Step 1: Check if context already exists
    if context.user_data and "escrow_data" in context.user_data:
        logger.debug(f"✅ ESCROW_CONTEXT: Already exists for user {user_id}")
        return True
    
    # Step 2: Check if user is in an escrow flow via database state
    db_state, _ = await _get_user_conversation_state(user_id)
    
    # Valid escrow states that should have context
    escrow_states = [
        "seller_input", "amount_input", "description_input", 
        "delivery_time", "trade_review", "payment_pending"
    ]
    
    if db_state not in escrow_states:
        logger.debug(f"❌ ESCROW_CONTEXT: User {user_id} not in escrow flow (state: {db_state})")
        return False
    
    # Step 3: Rehydrate context from database state
    logger.warning(f"🔄 ESCROW_CONTEXT: Rehydrating missing context for user {user_id} in state {db_state}")
    
    # Initialize context.user_data if needed
    if context.user_data is None:
        context.user_data = {}
    
    # Create fresh escrow_data structure
    context.user_data["escrow_data"] = {
        "status": "creating",
        "created_at": None,  # Will be set when needed
        "rehydrated": True,  # Mark as rehydrated for debugging
        "rehydrated_from_state": db_state
    }
    
    logger.info(f"✅ ESCROW_CONTEXT: Rehydrated for user {user_id} from state {db_state}")
    return True


async def ensure_escrow_context_with_fallback(
    user_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    update,
    error_message: str = "❌ Session expired. Please start a new trade."
) -> bool:
    """
    Ensure escrow context exists, with user-friendly error message fallback.
    
    Args:
        user_id: Telegram user ID
        context: Bot context
        update: Telegram update object
        error_message: Error message to show if context cannot be rehydrated
        
    Returns:
        True if context exists, False if error was shown to user
    """
    if await ensure_escrow_context(user_id, context):
        return True
    
    # Show error to user
    if update.message:
        await update.message.reply_text(error_message)
    elif update.callback_query:
        await update.callback_query.edit_message_text(error_message)
    
    return False
