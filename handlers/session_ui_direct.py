"""
Direct handlers for session management UI - replaces ConversationHandler
Maintains exact same UI while using database state tracking
"""

import logging
from telegram import Update
from telegram.ext import ContextTypes, CallbackQueryHandler
from models import User
from database import SessionLocal
from utils.callback_utils import safe_answer_callback_query
from utils.conversation_state_helper import set_conversation_state_db_sync

logger = logging.getLogger(__name__)

# State management functions
async def set_session_ui_state(user_id: int, state: str, data: dict = None, context: ContextTypes.DEFAULT_TYPE = None):
    """Set session UI conversation state in database"""
    session = SessionLocal()
    try:
        user = session.query(User).filter(User.telegram_id == int(user_id)).first()
        if user:
            state_data = {"flow": "session_ui", "step": state}
            if data:
                state_data.update(data)
            set_conversation_state_db_sync(user, f"session_ui_{state}", context)
            session.commit()
            logger.debug(f"Set user {user_id} session UI state to: {state}")
    except Exception as e:
        logger.error(f"Error setting session UI state: {e}")
    finally:
        session.close()

async def get_session_ui_state(user_id: int) -> str:
    """Get session UI conversation state from database"""
    session = SessionLocal()
    try:
        user = session.query(User).filter(User.telegram_id == int(user_id)).first()
        if user and user.conversation_state and user.conversation_state.startswith("session_ui_"):
            return user.conversation_state.replace("session_ui_", "")
        return ""
    except Exception as e:
        logger.error(f"Error getting session UI state: {e}")
        return ""
    finally:
        session.close()

async def clear_session_ui_state(user_id: int, context: ContextTypes.DEFAULT_TYPE = None):
    """Clear session UI conversation state"""
    await set_session_ui_state(user_id, "", {}, context)

# Direct handler implementations
async def direct_show_active_sessions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for showing active sessions"""
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "📋 Sessions")
    
    # Clear any existing state
    await clear_session_ui_state(update.effective_user.id, context)
    
    # Import and call original handler
    from handlers.session_ui import show_active_sessions
    await show_active_sessions(update, context)

async def direct_switch_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for switching sessions"""
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "🔄 Switching")
    
    # Import and call original handler
    from handlers.session_ui import switch_to_session
    await switch_to_session(update, context)

async def direct_terminate_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for terminating sessions"""
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "🗑️ Terminating")
    
    # Import and call original handler
    from handlers.session_ui import terminate_session
    await terminate_session(update, context)

async def direct_session_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for session details view"""
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "ℹ️ Details")
    
    # Set details state
    await set_session_ui_state(update.effective_user.id, "details", None, context)
    
    # Import and call original handler
    from handlers.session_ui import show_session_details
    await show_session_details(update, context)

async def direct_refresh_sessions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for refreshing sessions list"""
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "🔄 Refreshing")
    
    # Clear state and refresh
    await clear_session_ui_state(update.effective_user.id, context)
    
    # Import and call original handler
    from handlers.session_ui import refresh_sessions_list
    await refresh_sessions_list(update, context)

# Direct handlers list for registration
DIRECT_SESSION_UI_HANDLERS = [
    # Main session management
    CallbackQueryHandler(direct_show_active_sessions, pattern="^show_sessions$"),
    CallbackQueryHandler(direct_show_active_sessions, pattern="^active_sessions$"),
    CallbackQueryHandler(direct_show_active_sessions, pattern="^session_manager$"),
    
    # Session actions
    CallbackQueryHandler(direct_switch_session, pattern="^switch_session_.*$"),
    CallbackQueryHandler(direct_terminate_session, pattern="^terminate_session_.*$"),
    CallbackQueryHandler(direct_session_details, pattern="^session_details_.*$"),
    
    # Session navigation
    CallbackQueryHandler(direct_refresh_sessions, pattern="^refresh_sessions$"),
    CallbackQueryHandler(direct_show_active_sessions, pattern="^back_to_sessions$"),
]