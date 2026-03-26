"""
Direct handlers for messages hub - replaces ConversationHandler
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
async def set_messages_state(user_id: int, state: str, data: dict = None, context: ContextTypes.DEFAULT_TYPE = None):
    """Set messages hub conversation state in database"""
    session = SessionLocal()
    try:
        user = session.query(User).filter(User.telegram_id == str(user_id)).first()
        if user:
            state_data = {"flow": "messages", "step": state}
            if data:
                state_data.update(data)
            set_conversation_state_db_sync(user, f"messages_{state}", context)
            session.commit()
            logger.debug(f"Set user {user_id} messages state to: {state}")
    except Exception as e:
        logger.error(f"Error setting messages state: {e}")
    finally:
        session.close()

async def get_messages_state(user_id: int) -> str:
    """Get messages hub conversation state from database"""
    session = SessionLocal()
    try:
        user = session.query(User).filter(User.telegram_id == str(user_id)).first()
        if user and user.conversation_state and user.conversation_state.startswith("messages_"):
            return user.conversation_state.replace("messages_", "")
        return ""
    except Exception as e:
        logger.error(f"Error getting messages state: {e}")
        return ""
    finally:
        session.close()

async def clear_messages_state(user_id: int, context: ContextTypes.DEFAULT_TYPE = None):
    """Clear messages hub conversation state"""
    await set_messages_state(user_id, "", {}, context)

# Direct handler implementations
async def direct_chat_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for chat view navigation"""
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "💬 Chat")
    
    # Set chat view state
    await set_messages_state(update.effective_user.id, "view", None, context)
    
    # Import and call original handler
    from handlers.messages_hub import handle_chat_view
    await handle_chat_view(update, context)

async def direct_message_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for message input"""
    if not update.message or not update.message.text:
        return
    
    # Check state
    state = await get_messages_state(update.effective_user.id)
    if state != "input":
        return
    
    logger.info(f"✅ DIRECT HANDLER: Processing message input for user {update.effective_user.id}")
    
    # Clear state after message sent
    await clear_messages_state(update.effective_user.id, context)
    
    # Import and call original handler
    from handlers.messages_hub import handle_message_input
    await handle_message_input(update, context)

async def direct_show_messages_hub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for showing messages hub main view"""
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "💬 Messages")
    
    # Clear any existing state and show main hub
    await clear_messages_state(update.effective_user.id, context)
    
    # Import and call original handler
    from handlers.messages_hub import show_trades_messages_hub
    await show_trades_messages_hub(update, context)

async def direct_enter_chat_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for entering chat mode"""
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "✏️ Type")
    
    # Set input state
    await set_messages_state(update.effective_user.id, "input", None, context)
    
    # Import and call original handler
    from handlers.messages_hub import enter_chat_mode
    await enter_chat_mode(update, context)

async def direct_exit_chat_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for exiting chat mode"""
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "🔙 Back")
    
    # Clear state
    await clear_messages_state(update.effective_user.id, context)
    
    # Import and call original handler
    from handlers.messages_hub import exit_chat_mode
    await exit_chat_mode(update, context)

# Router for messages hub text input
async def messages_text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route messages hub text messages to correct handler based on state"""
    if not update.message or not update.message.text:
        return
    
    user_id = update.effective_user.id
    state = await get_messages_state(user_id)
    
    logger.debug(f"🔀 MESSAGES ROUTER: User {user_id} in state '{state}', message: {update.message.text}")
    
    if state == "input":
        await direct_message_input(update, context)

# Direct handlers list for registration
DIRECT_MESSAGES_HANDLERS = [
    # Main messages hub
    CallbackQueryHandler(direct_show_messages_hub, pattern="^messages_hub$"),
    CallbackQueryHandler(direct_show_messages_hub, pattern="^trades_messages$"),
    CallbackQueryHandler(direct_show_messages_hub, pattern="^show_messages$"),
    
    # Chat view navigation
    CallbackQueryHandler(direct_chat_view, pattern="^chat_view_.*$"),
    CallbackQueryHandler(direct_chat_view, pattern="^view_chat_.*$"),
    
    # Enter/exit chat mode
    CallbackQueryHandler(direct_enter_chat_mode, pattern="^enter_chat_.*$"),
    CallbackQueryHandler(direct_enter_chat_mode, pattern="^type_message_.*$"),
    CallbackQueryHandler(direct_exit_chat_mode, pattern="^exit_chat$"),
    CallbackQueryHandler(direct_exit_chat_mode, pattern="^back_to_messages$"),
    
    # Message navigation
    CallbackQueryHandler(direct_show_messages_hub, pattern="^refresh_messages$"),
    
    # Text input router
    MessageHandler(filters.TEXT & ~filters.COMMAND, messages_text_router),
]