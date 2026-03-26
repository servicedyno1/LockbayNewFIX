"""
Direct handlers for admin broadcast system - replaces ConversationHandler
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
async def set_admin_broadcast_state(user_id: int, state: str, data: dict | None = None, context: ContextTypes.DEFAULT_TYPE | None = None):
    """Set admin broadcast conversation state in database"""
    session = SessionLocal()
    try:
        user = session.query(User).filter(User.telegram_id == str(user_id)).first()
        if user:
            state_data = {"flow": "admin_broadcast", "step": state}
            if data:
                state_data.update(data)
            set_conversation_state_db_sync(user, f"admin_broadcast_{state}", context)
            session.commit()
            logger.debug(f"Set admin {user_id} broadcast state to: {state}")
    except Exception as e:
        logger.error(f"Error setting admin broadcast state: {e}")
    finally:
        session.close()

async def get_admin_broadcast_state(user_id: int) -> str:
    """Get admin broadcast conversation state from database"""
    session = SessionLocal()
    try:
        user = session.query(User).filter(User.telegram_id == str(user_id)).first()
        if user and user.conversation_state and user.conversation_state.startswith("admin_broadcast_"):
            return user.conversation_state.replace("admin_broadcast_", "")
        return ""
    except Exception as e:
        logger.error(f"Error getting admin broadcast state: {e}")
        return ""
    finally:
        session.close()

async def clear_admin_broadcast_state(user_id: int, context: ContextTypes.DEFAULT_TYPE | None = None):
    """Clear admin broadcast conversation state"""
    await set_admin_broadcast_state(user_id, "", {}, context)

# Direct handler implementations
# Note: Other handlers removed due to missing functions in admin_broadcast.py

async def direct_start_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for starting broadcast flow from admin panel button"""
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "📢 Broadcast")
    
    # Admin security check
    if not update.effective_user or not is_admin_secure(update.effective_user.id):
        return
    
    # Clear any existing state first (important for clean flow)
    await clear_admin_broadcast_state(update.effective_user.id, context)
    
    # Import and call the main admin notifications handler
    from handlers.admin_broadcast import handle_admin_notifications
    await handle_admin_notifications(update, context)

async def direct_admin_notifications_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for admin notifications main menu"""
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "🔔 Notifications")
    
    # Admin security check
    if not update.effective_user or not is_admin_secure(update.effective_user.id):
        return
    
    # Clear any existing state
    await clear_admin_broadcast_state(update.effective_user.id, context)
    
    # Import and call original handler
    from handlers.admin_broadcast import handle_admin_notifications
    await handle_admin_notifications(update, context)

async def direct_compose_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for compose broadcast button"""
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "📤 Compose")
    
    # Admin security check
    if not update.effective_user or not is_admin_secure(update.effective_user.id):
        return
    
    # Import and call the compose broadcast handler
    from handlers.admin_broadcast import handle_compose_broadcast
    await handle_compose_broadcast(update, context)

# Router for admin broadcast text input
async def admin_broadcast_text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route admin broadcast text messages to correct handler based on state"""
    if not update.message or not update.message.text:
        return
    
    # Admin security check
    if not update.effective_user or not is_admin_secure(update.effective_user.id):
        return
    
    user_id = update.effective_user.id
    state = await get_admin_broadcast_state(user_id)
    
    logger.debug(f"🔀 ADMIN BROADCAST ROUTER: Admin {user_id} in state '{state}', message: {update.message.text}")
    
    # Check if admin is composing a broadcast
    if state == "composing":
        from handlers.admin_broadcast import handle_broadcast_message
        await handle_broadcast_message(update, context)

# Direct handlers list for registration
DIRECT_ADMIN_BROADCAST_HANDLERS = [
    # Start broadcast flow from /broadcast command button
    CallbackQueryHandler(direct_start_broadcast, pattern="^admin_broadcast$"),
    
    # Main notifications menu
    CallbackQueryHandler(direct_admin_notifications_menu, pattern="^admin_notifications$"),
    CallbackQueryHandler(direct_admin_notifications_menu, pattern="^notification_management$"),
    
    # Compose broadcast button
    CallbackQueryHandler(direct_compose_broadcast, pattern="^admin_compose_broadcast$"),
    
    # Text input router
    MessageHandler(filters.TEXT & ~filters.COMMAND, admin_broadcast_text_router),
]