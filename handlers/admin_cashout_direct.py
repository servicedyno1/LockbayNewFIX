"""
Direct handlers for admin cashout approval - replaces ConversationHandler
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
async def set_admin_cashout_state(user_id: int, state: str, data: dict = None, context: ContextTypes.DEFAULT_TYPE = None):
    """Set admin cashout conversation state in database"""
    session = SessionLocal()
    try:
        user = session.query(User).filter(User.telegram_id == str(user_id)).first()
        if user:
            state_data = {"flow": "admin_cashout", "step": state}
            if data:
                state_data.update(data)
            set_conversation_state_db_sync(user, f"admin_cashout_{state}", context)
            session.commit()
            logger.debug(f"Set admin {user_id} cashout state to: {state}")
    except Exception as e:
        logger.error(f"Error setting admin cashout state: {e}")
    finally:
        session.close()

async def get_admin_cashout_state(user_id: int) -> str:
    """Get admin cashout conversation state from database"""
    session = SessionLocal()
    try:
        user = session.query(User).filter(User.telegram_id == str(user_id)).first()
        if user and user.conversation_state and user.conversation_state.startswith("admin_cashout_"):
            return user.conversation_state.replace("admin_cashout_", "")
        return ""
    except Exception as e:
        logger.error(f"Error getting admin cashout state: {e}")
        return ""
    finally:
        session.close()

async def clear_admin_cashout_state(user_id: int, context: ContextTypes.DEFAULT_TYPE = None):
    """Clear admin cashout conversation state"""
    await set_admin_cashout_state(user_id, "", {}, context)

# Direct handler implementations
async def direct_cashout_approval_hash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for cashout hash input"""
    if not update.message or not update.message.text:
        return
    
    # Admin security check
    if not is_admin_secure(update.effective_user.id):
        return
    
    # Check state
    state = await get_admin_cashout_state(update.effective_user.id)
    if state != "hash":
        return
    
    logger.info(f"✅ ADMIN DIRECT HANDLER: Processing cashout hash for admin {update.effective_user.id}")
    
    # Set next state
    await set_admin_cashout_state(update.effective_user.id, "bank_ref", None, context)
    
    # Import and call original handler
    from handlers.admin import handle_cashout_approval_hash_input
    await handle_cashout_approval_hash_input(update, context)

async def direct_cashout_approval_bank_ref(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for bank reference input"""
    if not update.message or not update.message.text:
        return
    
    # Admin security check
    if not is_admin_secure(update.effective_user.id):
        return
    
    # Check state
    state = await get_admin_cashout_state(update.effective_user.id)
    if state != "bank_ref":
        return
    
    logger.info(f"✅ ADMIN DIRECT HANDLER: Processing bank reference for admin {update.effective_user.id}")
    
    # Clear state after completion
    await clear_admin_cashout_state(update.effective_user.id, context)
    
    # Import and call original handler
    from handlers.admin import handle_cashout_approval_bank_ref_input
    await handle_cashout_approval_bank_ref_input(update, context)

async def direct_start_cashout_approval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for starting cashout approval process"""
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "💰 Processing")
    
    # Admin security check
    if not is_admin_secure(update.effective_user.id):
        return
    
    # Set initial state
    await set_admin_cashout_state(update.effective_user.id, "hash", None, context)
    
    # Import and call original handler
    from handlers.admin import start_cashout_approval
    await start_cashout_approval(update, context)

# Router for admin cashout text input
async def admin_cashout_text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route admin cashout text messages to correct handler based on state"""
    if not update.message or not update.message.text:
        return
    
    # Admin security check
    if not is_admin_secure(update.effective_user.id):
        return
    
    user_id = update.effective_user.id
    state = await get_admin_cashout_state(user_id)
    
    logger.debug(f"🔀 ADMIN CASHOUT ROUTER: Admin {user_id} in state '{state}', message: {update.message.text}")
    
    if state == "hash":
        await direct_cashout_approval_hash(update, context)
    elif state == "bank_ref":
        await direct_cashout_approval_bank_ref(update, context)

# Direct handlers list for registration
DIRECT_ADMIN_CASHOUT_HANDLERS = [
    # Start cashout approval
    CallbackQueryHandler(direct_start_cashout_approval, pattern="^approve_cashout_.*$"),
    CallbackQueryHandler(direct_start_cashout_approval, pattern="^cashout_approval.*$"),
    
    # Text input router
    MessageHandler(filters.TEXT & ~filters.COMMAND, admin_cashout_text_router),
]