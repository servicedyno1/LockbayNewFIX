"""
Direct handlers for dispute resolution - replaces ConversationHandler
Maintains exact same UI while using database state tracking
"""

import logging
from telegram import Update
from telegram.ext import ContextTypes, CallbackQueryHandler, MessageHandler, filters
from models import User
from database import SessionLocal
from utils.callback_utils import safe_answer_callback_query
from utils.conversation_state_helper import set_conversation_state_db_sync

logger = logging.getLogger(__name__)

# State management functions
async def set_dispute_state(user_id: int, state: str, data: dict = None, context: ContextTypes.DEFAULT_TYPE = None):
    """Set dispute conversation state in database"""
    session = SessionLocal()
    try:
        user = session.query(User).filter(User.telegram_id == str(user_id)).first()
        if user:
            state_data = {"flow": "dispute", "step": state}
            if data:
                state_data.update(data)
            set_conversation_state_db_sync(user, f"dispute_{state}", context)
            session.commit()
            logger.debug(f"Set user {user_id} dispute state to: {state}")
    except Exception as e:
        logger.error(f"Error setting dispute state: {e}")
    finally:
        session.close()

async def get_dispute_state(user_id: int) -> str:
    """Get dispute conversation state from database"""
    session = SessionLocal()
    try:
        user = session.query(User).filter(User.telegram_id == str(user_id)).first()
        if user and user.conversation_state and user.conversation_state.startswith("dispute_"):
            return user.conversation_state.replace("dispute_", "")
        return ""
    except Exception as e:
        logger.error(f"Error getting dispute state: {e}")
        return ""
    finally:
        session.close()

async def clear_dispute_state(user_id: int, context: ContextTypes.DEFAULT_TYPE = None):
    """Clear dispute conversation state"""
    await set_dispute_state(user_id, "", {}, context)

# Direct handler implementations
async def direct_dispute_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for dispute chat messages"""
    if not update.message or not update.message.text:
        return
    
    # Check state
    state = await get_dispute_state(update.effective_user.id)
    if state != "chat":
        return
    
    logger.info(f"✅ DIRECT HANDLER: Processing dispute chat for user {update.effective_user.id}")
    
    # Import and call original handler
    from handlers.dispute_chat import process_dispute_message
    await process_dispute_message(update, context)

async def direct_escrow_messaging(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for escrow messaging"""
    if not update.message or not update.message.text:
        return
    
    # Check state
    state = await get_dispute_state(update.effective_user.id)
    if state != "messaging":
        return
    
    logger.info(f"✅ DIRECT HANDLER: Processing escrow messaging for user {update.effective_user.id}")
    
    # Import and call original handler
    from handlers.dispute_chat import handle_escrow_message
    await handle_escrow_message(update, context)

async def direct_multi_dispute_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for multi-dispute chat messages"""
    if not update.message or not update.message.text:
        return
    
    # Check multi-dispute state
    from handlers.multi_dispute_manager_direct import get_multi_dispute_state
    state = await get_multi_dispute_state(update.effective_user.id)
    if state != "selected":
        return
    
    logger.info(f"✅ DIRECT HANDLER: Processing multi-dispute chat for user {update.effective_user.id}")
    
    # Import and call original handler - use the same dispute message handler
    from handlers.dispute_chat import process_dispute_message
    await process_dispute_message(update, context)

async def direct_start_dispute_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for starting dispute chat"""
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "💬 Dispute")
    
    # Extract dispute_id from callback data
    dispute_id = query.data.split("_")[-1] if query and "_" in query.data else None
    
    if not dispute_id:
        logger.error("No dispute ID provided for chat")
        return
    
    # Set chat state with dispute context
    await set_dispute_state(update.effective_user.id, "chat", {"dispute_id": dispute_id}, context)
    
    # Import and call original handler
    from handlers.dispute_chat import show_dispute_chat
    await show_dispute_chat(update, context)

async def direct_start_escrow_messaging(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for starting escrow messaging"""
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "💬 Chat")
    
    # Extract escrow_id from callback data
    escrow_id = query.data.split("_")[-1] if query and "_" in query.data else None
    
    if not escrow_id:
        logger.error("No escrow ID provided for messaging")
        return
    
    # Set messaging state with escrow context
    await set_dispute_state(update.effective_user.id, "messaging", {"escrow_id": escrow_id}, context)
    
    # Import and call original handler - handle_escrow_message shows the messaging interface
    from handlers.dispute_chat import handle_escrow_message
    await handle_escrow_message(update, context)

async def direct_end_dispute_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for ending dispute/escrow chat"""
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "✅ Done")
    
    # Clear state
    await clear_dispute_state(update.effective_user.id, context)
    
    # Import and call original handler
    from handlers.dispute_chat import exit_dispute_chat
    await exit_dispute_chat(update, context)

# Router for dispute-related text input
async def dispute_text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route dispute text messages to correct handler based on state"""
    if not update.message or not update.message.text:
        return
    
    user_id = update.effective_user.id
    state = await get_dispute_state(user_id)
    
    # Check for multi-dispute state
    from handlers.multi_dispute_manager_direct import get_multi_dispute_state
    multi_state = await get_multi_dispute_state(user_id)
    
    logger.debug(f"🔀 DISPUTE ROUTER: User {user_id} in state '{state}', multi_state '{multi_state}', message: {update.message.text}")
    
    # Handle multi-dispute selected state (user has selected a dispute and is sending messages)
    if multi_state == "selected":
        await direct_multi_dispute_chat(update, context)
    elif state == "chat":
        await direct_dispute_chat(update, context)
    elif state == "messaging":
        await direct_escrow_messaging(update, context)

# Direct handlers list for registration
DIRECT_DISPUTE_HANDLERS = [
    # Start dispute chat
    CallbackQueryHandler(direct_start_dispute_chat, pattern="^dispute_chat_.*$"),
    CallbackQueryHandler(direct_start_dispute_chat, pattern="^chat_dispute_.*$"),
    
    # Start escrow messaging
    CallbackQueryHandler(direct_start_escrow_messaging, pattern="^escrow_chat_.*$"),
    CallbackQueryHandler(direct_start_escrow_messaging, pattern="^message_escrow_.*$"),
    
    # End chat
    CallbackQueryHandler(direct_end_dispute_chat, pattern="^end_chat$"),
    CallbackQueryHandler(direct_end_dispute_chat, pattern="^finish_chat$"),
    
    # Text input router
    MessageHandler(filters.TEXT & ~filters.COMMAND, dispute_text_router),
]