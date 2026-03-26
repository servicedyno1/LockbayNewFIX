"""
Direct handlers for admin transactions management - replaces ConversationHandler
Maintains exact same UI while using database state tracking
"""

import logging
from telegram import Update
from telegram.ext import ContextTypes, CallbackQueryHandler
from models import User
from database import SessionLocal
from utils.callback_utils import safe_answer_callback_query
from utils.admin_security import is_admin_secure
from utils.conversation_state_helper import set_conversation_state_db_sync

logger = logging.getLogger(__name__)

# State management functions
async def set_admin_transaction_state(user_id: int, state: str, data: dict = None, context: ContextTypes.DEFAULT_TYPE = None):
    """Set admin transaction conversation state in database"""
    session = SessionLocal()
    try:
        user = session.query(User).filter(User.telegram_id == int(user_id)).first()
        if user:
            state_data = {"flow": "admin_transaction", "step": state}
            if data:
                state_data.update(data)
            set_conversation_state_db_sync(user, f"admin_transaction_{state}", context)
            session.commit()
            logger.debug(f"Set admin {user_id} transaction state to: {state}")
    except Exception as e:
        logger.error(f"Error setting admin transaction state: {e}")
    finally:
        session.close()

async def get_admin_transaction_state(user_id: int) -> str:
    """Get admin transaction conversation state from database"""
    session = SessionLocal()
    try:
        user = session.query(User).filter(User.telegram_id == int(user_id)).first()
        if user and user.conversation_state and user.conversation_state.startswith("admin_transaction_"):
            return user.conversation_state.replace("admin_transaction_", "")
        return ""
    except Exception as e:
        logger.error(f"Error getting admin transaction state: {e}")
        return ""
    finally:
        session.close()

async def clear_admin_transaction_state(user_id: int, context: ContextTypes.DEFAULT_TYPE = None):
    """Clear admin transaction conversation state"""
    await set_admin_transaction_state(user_id, "", {}, context)

# Direct handler implementations
async def direct_transaction_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for transaction detail view"""
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "📊 Details")
    
    # Admin security check
    if not is_admin_secure(update.effective_user.id):
        return
    
    # Set detail state
    await set_admin_transaction_state(update.effective_user.id, "detail", None, context)
    
    # Import and call original handler
    from handlers.admin_transactions import handle_transaction_detail
    await handle_transaction_detail(update, context)

async def direct_transaction_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for transaction actions"""
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "⚡ Action")
    
    # Admin security check
    if not is_admin_secure(update.effective_user.id):
        return
    
    # Check state
    state = await get_admin_transaction_state(update.effective_user.id)
    if state != "detail":
        return
    
    logger.info(f"✅ ADMIN DIRECT HANDLER: Processing transaction action for admin {update.effective_user.id}")
    
    # Set action state
    await set_admin_transaction_state(update.effective_user.id, "action", None, context)
    
    # Import and call original handler
    from handlers.admin_transactions import handle_transaction_action
    await handle_transaction_action(update, context)

async def direct_admin_transactions_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for admin transactions main menu"""
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "💰 Transactions")
    
    # Admin security check
    if not is_admin_secure(update.effective_user.id):
        return
    
    # Clear any existing state
    await clear_admin_transaction_state(update.effective_user.id, context)
    
    # Import and call original handler
    from handlers.admin_transactions import handle_admin_transactions
    await handle_admin_transactions(update, context)

# Direct handlers list for registration
DIRECT_ADMIN_TRANSACTION_HANDLERS = [
    # Main transactions menu
    CallbackQueryHandler(direct_admin_transactions_menu, pattern="^admin_transactions$"),
    CallbackQueryHandler(direct_admin_transactions_menu, pattern="^transactions_overview$"),
    
    # Transaction details
    CallbackQueryHandler(direct_transaction_detail, pattern="^transaction_detail_.*$"),
    CallbackQueryHandler(direct_transaction_detail, pattern="^view_transaction_.*$"),
    
    # Transaction actions
    CallbackQueryHandler(direct_transaction_action, pattern="^transaction_action_.*$"),
    CallbackQueryHandler(direct_transaction_action, pattern="^modify_transaction_.*$"),
    
    # Transaction navigation
    CallbackQueryHandler(direct_admin_transactions_menu, pattern="^back_to_transactions$"),
]