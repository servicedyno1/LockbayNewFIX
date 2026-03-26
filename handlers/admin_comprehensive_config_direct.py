"""
Direct handlers for admin comprehensive config - replaces ConversationHandler
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
# from services.comprehensive_config_service import ComprehensiveConfigService

logger = logging.getLogger(__name__)

# State management functions
async def set_admin_config_state(user_id: int, state: str, data: dict = None, context: ContextTypes.DEFAULT_TYPE = None):
    """Set admin config conversation state in database"""
    session = SessionLocal()
    try:
        user = session.query(User).filter(User.telegram_id == str(user_id)).first()
        if user:
            state_data = {"flow": "admin_config", "step": state}
            if data:
                state_data.update(data)
            set_conversation_state_db_sync(user, f"admin_config_{state}", context)
            session.commit()
            logger.debug(f"Set admin {user_id} config state to: {state}")
    except Exception as e:
        logger.error(f"Error setting admin config state: {e}")
    finally:
        session.close()

async def get_admin_config_state(user_id: int) -> str:
    """Get admin config conversation state from database"""
    session = SessionLocal()
    try:
        user = session.query(User).filter(User.telegram_id == str(user_id)).first()
        if user and user.conversation_state and user.conversation_state.startswith("admin_config_"):
            return user.conversation_state.replace("admin_config_", "")
        return ""
    except Exception as e:
        logger.error(f"Error getting admin config state: {e}")
        return ""
    finally:
        session.close()

async def clear_admin_config_state(user_id: int, context: ContextTypes.DEFAULT_TYPE = None):
    """Clear admin config conversation state"""
    await set_admin_config_state(user_id, "", {}, context)

# Direct handler implementations
async def direct_config_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for config main menu"""
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "🏛️ Config")
    
    # Admin security check
    if not is_admin_secure(update.effective_user.id):
        return
    
    # Set main menu state
    await set_admin_config_state(update.effective_user.id, "main", None, context)
    
    # Import and call original handler
    try:
        from handlers.admin_comprehensive_config import AdminComprehensiveConfigHandler
        handler = AdminComprehensiveConfigHandler()
        await handler.show_config_main_menu(update, context)
    except ImportError:
        await safe_answer_callback_query(query, "⚠️ Service unavailable")
        return

async def direct_phase1_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for phase 1 config menu"""
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "💰 Phase 1")
    
    # Admin security check
    if not is_admin_secure(update.effective_user.id):
        return
    
    # Set phase 1 state
    await set_admin_config_state(update.effective_user.id, "phase1", None, context)
    
    # Import and call original handler
    from handlers.admin_comprehensive_config import AdminComprehensiveConfigHandler
    handler = AdminComprehensiveConfigHandler()
    await handler.show_phase1_menu(update, context)

async def direct_phase2_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for phase 2 config menu"""
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "🛡️ Phase 2")
    
    # Admin security check
    if not is_admin_secure(update.effective_user.id):
        return
    
    # Set phase 2 state
    await set_admin_config_state(update.effective_user.id, "phase2", None, context)
    
    # Import and call original handler
    from handlers.admin_comprehensive_config import AdminComprehensiveConfigHandler
    handler = AdminComprehensiveConfigHandler()
    await handler.show_phase2_menu(update, context)

async def direct_phase3_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for phase 3 config menu"""
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "⚡ Phase 3")
    
    # Admin security check
    if not is_admin_secure(update.effective_user.id):
        return
    
    # Set phase 3 state
    await set_admin_config_state(update.effective_user.id, "phase3", None, context)
    
    # Import and call original handler
    from handlers.admin_comprehensive_config import AdminComprehensiveConfigHandler
    handler = AdminComprehensiveConfigHandler()
    await handler.show_phase3_menu(update, context)

async def direct_edit_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for editing configuration values"""
    if not update.message or not update.message.text:
        return
    
    # Admin security check
    if not is_admin_secure(update.effective_user.id):
        return
    
    # Check state
    state = await get_admin_config_state(update.effective_user.id)
    if state != "edit":
        return
    
    logger.info(f"✅ ADMIN DIRECT HANDLER: Processing config edit for admin {update.effective_user.id}")
    
    # Clear state after edit
    await clear_admin_config_state(update.effective_user.id, context)
    
    # Import and call original handler
    from handlers.admin_comprehensive_config import AdminComprehensiveConfigHandler
    handler = AdminComprehensiveConfigHandler()
    await handler.handle_value_edit(update, context)

async def direct_audit_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for audit menu"""
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "📋 Audit")
    
    # Admin security check
    if not is_admin_secure(update.effective_user.id):
        return
    
    # Set audit state
    await set_admin_config_state(update.effective_user.id, "audit", None, context)
    
    # Import and call original handler
    from handlers.admin_comprehensive_config import AdminComprehensiveConfigHandler
    handler = AdminComprehensiveConfigHandler()
    await handler.show_audit_menu(update, context)

async def direct_ab_test_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for A/B testing menu"""
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "📊 A/B Test")
    
    # Admin security check
    if not is_admin_secure(update.effective_user.id):
        return
    
    # Set AB test state
    await set_admin_config_state(update.effective_user.id, "ab_test", None, context)
    
    # Import and call original handler
    from handlers.admin_comprehensive_config import AdminComprehensiveConfigHandler
    handler = AdminComprehensiveConfigHandler()
    await handler.show_ab_test_menu(update, context)

async def direct_rollback_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for rollback menu"""
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "🔄 Rollback")
    
    # Admin security check
    if not is_admin_secure(update.effective_user.id):
        return
    
    # Set rollback state
    await set_admin_config_state(update.effective_user.id, "rollback", None, context)
    
    # Import and call original handler
    from handlers.admin_comprehensive_config import AdminComprehensiveConfigHandler
    handler = AdminComprehensiveConfigHandler()
    await handler.show_rollback_menu(update, context)

async def direct_start_edit_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for starting value edit"""
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "✏️ Edit")
    
    # Admin security check
    if not is_admin_secure(update.effective_user.id):
        return
    
    # Set edit state
    await set_admin_config_state(update.effective_user.id, "edit", None, context)
    
    # Import and call original handler
    from handlers.admin_comprehensive_config import AdminComprehensiveConfigHandler
    handler = AdminComprehensiveConfigHandler()
    await handler.start_value_edit(update, context)

# Router for admin config text input
async def admin_config_text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route admin config text messages to correct handler based on state"""
    if not update.message or not update.message.text:
        return
    
    # Admin security check
    if not is_admin_secure(update.effective_user.id):
        return
    
    user_id = update.effective_user.id
    state = await get_admin_config_state(user_id)
    
    logger.debug(f"🔀 ADMIN CONFIG ROUTER: Admin {user_id} in state '{state}', message: {update.message.text}")
    
    if state == "edit":
        await direct_edit_value(update, context)

# Direct handlers list for registration
DIRECT_ADMIN_CONFIG_HANDLERS = [
    # Main config menu
    CallbackQueryHandler(direct_config_main_menu, pattern="^admin_config$"),
    CallbackQueryHandler(direct_config_main_menu, pattern="^config_main$"),
    CallbackQueryHandler(direct_config_main_menu, pattern="^comprehensive_config$"),
    
    # Phase menus
    CallbackQueryHandler(direct_phase1_menu, pattern="^config_phase1$"),
    CallbackQueryHandler(direct_phase2_menu, pattern="^config_phase2$"),
    CallbackQueryHandler(direct_phase3_menu, pattern="^config_phase3$"),
    
    # Special menus
    CallbackQueryHandler(direct_audit_menu, pattern="^config_audit$"),
    CallbackQueryHandler(direct_ab_test_menu, pattern="^config_ab_testing$"),
    CallbackQueryHandler(direct_rollback_menu, pattern="^config_rollback$"),
    
    # Edit actions
    CallbackQueryHandler(direct_start_edit_value, pattern="^edit_config_.*$"),
    CallbackQueryHandler(direct_start_edit_value, pattern="^config_edit_.*$"),
    
    # Navigation
    CallbackQueryHandler(direct_config_main_menu, pattern="^back_to_config$"),
    
    # Text input router
    MessageHandler(filters.TEXT & ~filters.COMMAND, admin_config_text_router),
]