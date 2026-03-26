"""
Direct handlers for admin rating management - replaces ConversationHandler
Maintains exact same UI while using database state tracking
"""

import logging
from telegram import Update
from telegram.ext import ContextTypes, CallbackQueryHandler, MessageHandler, filters
from models import User
from database import SessionLocal
from utils.callback_utils import safe_answer_callback_query
from utils.admin_security import is_admin_secure
from utils.conversation_state_helper import set_conversation_state_db_sync

logger = logging.getLogger(__name__)

# State management functions
async def set_admin_rating_state(user_id: int, state: str, data: dict = None, context: ContextTypes.DEFAULT_TYPE = None):
    """Set admin rating conversation state in database"""
    session = SessionLocal()
    try:
        user = session.query(User).filter(User.telegram_id == str(user_id)).first()
        if user:
            state_data = {"flow": "admin_rating", "step": state}
            if data:
                state_data.update(data)
            set_conversation_state_db_sync(user, f"admin_rating_{state}", context)
            session.commit()
            logger.debug(f"Set admin {user_id} rating state to: {state}")
    except Exception as e:
        logger.error(f"Error setting admin rating state: {e}")
    finally:
        session.close()

async def get_admin_rating_state(user_id: int) -> str:
    """Get admin rating conversation state from database"""
    session = SessionLocal()
    try:
        user = session.query(User).filter(User.telegram_id == str(user_id)).first()
        if user and user.conversation_state and user.conversation_state.startswith("admin_rating_"):
            return user.conversation_state.replace("admin_rating_", "")
        return ""
    except Exception as e:
        logger.error(f"Error getting admin rating state: {e}")
        return ""
    finally:
        session.close()

async def clear_admin_rating_state(user_id: int, context: ContextTypes.DEFAULT_TYPE = None):
    """Clear admin rating conversation state"""
    await set_admin_rating_state(user_id, "", {}, context)

# Direct handler implementations
async def direct_rating_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for rating detail view"""
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "⭐ Details")
    
    # Admin security check
    if not is_admin_secure(update.effective_user.id):
        return
    
    # Set detail state
    await set_admin_rating_state(update.effective_user.id, "detail", None, context)
    
    # Import and call original handler
    from handlers.admin_rating import handle_rating_detail
    await handle_rating_detail(update, context)

async def direct_rating_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for rating actions"""
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "⚡ Action")
    
    # Admin security check
    if not is_admin_secure(update.effective_user.id):
        return
    
    # Check state
    state = await get_admin_rating_state(update.effective_user.id)
    if state != "detail":
        return
    
    logger.info(f"✅ ADMIN DIRECT HANDLER: Processing rating action for admin {update.effective_user.id}")
    
    # Set action state
    await set_admin_rating_state(update.effective_user.id, "action", None, context)
    
    # Import and call original handler
    from handlers.admin_rating import handle_rating_action
    await handle_rating_action(update, context)

async def direct_rating_moderate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for rating moderation"""
    if not update.message or not update.message.text:
        return
    
    # Admin security check
    if not is_admin_secure(update.effective_user.id):
        return
    
    # Check state
    state = await get_admin_rating_state(update.effective_user.id)
    if state != "action":
        return
    
    logger.info(f"✅ ADMIN DIRECT HANDLER: Processing rating moderation for admin {update.effective_user.id}")
    
    # Clear state after moderation
    await clear_admin_rating_state(update.effective_user.id, context)
    
    # Import and call original handler
    from handlers.admin_rating import handle_rating_moderation_input
    await handle_rating_moderation_input(update, context)

async def direct_admin_ratings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for admin ratings main menu"""
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "⭐ Ratings")
    
    # Admin security check
    if not is_admin_secure(update.effective_user.id):
        return
    
    # Clear any existing state
    await clear_admin_rating_state(update.effective_user.id, context)
    
    # Import and call original handler
    from handlers.admin_rating import handle_admin_ratings
    await handle_admin_ratings(update, context)

# Router for admin rating text input
async def admin_rating_text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route admin rating text messages to correct handler based on state"""
    if not update.message or not update.message.text:
        return
    
    # Admin security check
    if not is_admin_secure(update.effective_user.id):
        return
    
    user_id = update.effective_user.id
    state = await get_admin_rating_state(user_id)
    
    logger.debug(f"🔀 ADMIN RATING ROUTER: Admin {user_id} in state '{state}', message: {update.message.text}")
    
    if state == "action":
        await direct_rating_moderate(update, context)

# Direct handlers list for registration
DIRECT_ADMIN_RATING_HANDLERS = [
    # Main ratings menu
    CallbackQueryHandler(direct_admin_ratings_menu, pattern="^admin_ratings$"),
    CallbackQueryHandler(direct_admin_ratings_menu, pattern="^rating_management$"),
    
    # Rating details
    CallbackQueryHandler(direct_rating_detail, pattern="^rating_detail_.*$"),
    CallbackQueryHandler(direct_rating_detail, pattern="^view_rating_.*$"),
    
    # Rating actions
    CallbackQueryHandler(direct_rating_action, pattern="^rating_action_.*$"),
    CallbackQueryHandler(direct_rating_action, pattern="^moderate_rating_.*$"),
    
    # Rating navigation
    CallbackQueryHandler(direct_admin_ratings_menu, pattern="^back_to_ratings$"),
    
    # Text input router
    MessageHandler(filters.TEXT & ~filters.COMMAND, admin_rating_text_router),
]