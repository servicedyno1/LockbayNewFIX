"""
Direct handlers for contact management - replaces ConversationHandler
Handles user contact methods and notification preferences with database state tracking
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
async def set_user_contact_state(user_id: int, state: str, data: dict = None, context: ContextTypes.DEFAULT_TYPE = None):
    """Set user contact conversation state in database"""
    session = SessionLocal()
    try:
        user = session.query(User).filter(User.telegram_id == str(user_id)).first()
        if user:
            state_data = {"flow": "contact", "step": state}
            if data:
                state_data.update(data)
            set_conversation_state_db_sync(user, f"contact_{state}", context)
            session.commit()
            logger.debug(f"Set user {user_id} contact state to: {state}")
    except Exception as e:
        logger.error(f"Error setting user contact state: {e}")
    finally:
        session.close()

async def get_user_contact_state(user_id: int) -> str:
    """Get user contact conversation state from database"""
    session = SessionLocal()
    try:
        user = session.query(User).filter(User.telegram_id == str(user_id)).first()
        if user and user.conversation_state and user.conversation_state.startswith("contact_"):
            return user.conversation_state.replace("contact_", "")
        return ""
    except Exception as e:
        logger.error(f"Error getting user contact state: {e}")
        return ""
    finally:
        session.close()

async def clear_user_contact_state(user_id: int, context: ContextTypes.DEFAULT_TYPE = None):
    """Clear user contact conversation state"""
    await set_user_contact_state(user_id, "", {}, context)

# Direct handler implementations
async def direct_contact_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for contact management menu"""
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "📞 Contact management")
    
    # Set initial state
    await set_user_contact_state(update.effective_user.id, "menu", None, context)
    
    # Import and call original handler
    from handlers.contact_management import ContactManagementHandler
    handler = ContactManagementHandler()
    await handler.contact_management_menu(update, context)

async def direct_add_contact_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for adding contact type selection"""
    query = update.callback_query
    if not query:
        return
    
    # Check state
    state = await get_user_contact_state(update.effective_user.id)
    if state != "menu":
        return
    
    logger.info(f"✅ DIRECT HANDLER: Processing contact type selection for user {update.effective_user.id}")
    
    # Set next state
    await set_user_contact_state(update.effective_user.id, "add_type", None, context)
    
    # Import and call original handler
    from handlers.contact_management import ContactManagementHandler
    handler = ContactManagementHandler()
    await handler.add_contact_type_selection(update, context)

async def direct_add_contact_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for contact value input"""
    if not update.message or not update.message.text:
        return
    
    # Check state
    state = await get_user_contact_state(update.effective_user.id)
    if state != "add_value":
        return
    
    logger.info(f"✅ DIRECT HANDLER: Processing contact value for user {update.effective_user.id}")
    
    # Set verification state
    await set_user_contact_state(update.effective_user.id, "verify", None, context)
    
    # Import and call original handler
    from handlers.contact_management import ContactManagementHandler
    handler = ContactManagementHandler()
    await handler.handle_contact_value_input(update, context)

async def direct_verify_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for contact verification"""
    if not update.message or not update.message.text:
        return
    
    # Check state
    state = await get_user_contact_state(update.effective_user.id)
    if state != "verify":
        return
    
    logger.info(f"✅ DIRECT HANDLER: Processing contact verification for user {update.effective_user.id}")
    
    # Clear state after verification
    await clear_user_contact_state(update.effective_user.id, context)
    
    # Import and call original handler
    from handlers.contact_management import ContactManagementHandler
    handler = ContactManagementHandler()
    await handler.handle_verification_code(update, context)

async def direct_notification_preferences(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for notification preferences"""
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "🔔 Preferences")
    
    # Set preferences state
    await set_user_contact_state(update.effective_user.id, "preferences", None, context)
    
    # Import and call original handler
    from handlers.contact_management import ContactManagementHandler
    handler = ContactManagementHandler()
    await handler.notification_preferences_menu(update, context)

# Router for contact-related text input
async def contact_text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route contact text messages to correct handler based on state"""
    if not update.message or not update.message.text:
        return
    
    user_id = update.effective_user.id
    state = await get_user_contact_state(user_id)
    
    logger.debug(f"🔀 CONTACT ROUTER: User {user_id} in state '{state}', message: {update.message.text}")
    
    if state == "add_value":
        await direct_add_contact_value(update, context)
    elif state == "verify":
        await direct_verify_contact(update, context)

# Direct handlers list for registration
DIRECT_CONTACT_HANDLERS = [
    # Contact menu
    CallbackQueryHandler(direct_contact_menu, pattern="^contact_management$"),
    CallbackQueryHandler(direct_contact_menu, pattern="^manage_contacts$"),
    
    # Add contact type
    CallbackQueryHandler(direct_add_contact_type, pattern="^add_contact_.*$"),
    
    # Notification preferences
    CallbackQueryHandler(direct_notification_preferences, pattern="^notification_prefs$"),
    CallbackQueryHandler(direct_notification_preferences, pattern="^contact_notifications$"),
    
    # Contact actions (excluding contact_support which goes to UX improvement handlers)
    CallbackQueryHandler(direct_contact_menu, pattern="^contact_(?!support).*$"),
    
    # Text input router
    MessageHandler(filters.TEXT & ~filters.COMMAND, contact_text_router),
]