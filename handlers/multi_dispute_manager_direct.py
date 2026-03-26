"""
Direct handlers for multi-dispute manager - replaces ConversationHandler
Handles multiple simultaneous disputes with database state tracking
"""

import logging
from telegram import Update
from telegram.ext import ContextTypes, CallbackQueryHandler, MessageHandler, filters
from models import User, Dispute
from database import async_managed_session
from sqlalchemy import select
from utils.callback_utils import safe_answer_callback_query
from utils.conversation_state_helper import set_conversation_state_db_sync

logger = logging.getLogger(__name__)

# State management functions
async def set_multi_dispute_state(user_id: int, state: str, data: dict = None, context: ContextTypes.DEFAULT_TYPE = None):
    """Set multi-dispute conversation state in database"""
    async with async_managed_session() as session:
        try:
            stmt = select(User).where(User.telegram_id == user_id)
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()
            
            if user:
                state_data = {"flow": "multi_dispute", "step": state}
                if data:
                    state_data.update(data)
                set_conversation_state_db_sync(user, f"multi_dispute_{state}", context)
                await session.commit()
                logger.debug(f"Set user {user_id} multi-dispute state to: {state}")
        except Exception as e:
            logger.error(f"Error setting multi-dispute state: {e}")
            await session.rollback()

async def get_multi_dispute_state(user_id: int) -> str:
    """Get multi-dispute conversation state from database"""
    async with async_managed_session() as session:
        try:
            stmt = select(User).where(User.telegram_id == user_id)
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()
            
            if user and user.conversation_state and user.conversation_state.startswith("multi_dispute_"):
                return user.conversation_state.replace("multi_dispute_", "")
            return ""
        except Exception as e:
            logger.error(f"Error getting multi-dispute state: {e}")
            return ""

async def clear_multi_dispute_state(user_id: int, context: ContextTypes.DEFAULT_TYPE = None):
    """Clear multi-dispute conversation state"""
    await set_multi_dispute_state(user_id, "", {}, context)

# Direct handler implementations
async def direct_show_disputes_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for disputes dashboard"""
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "⚖️ Disputes")
    
    # Clear any existing state
    await clear_multi_dispute_state(update.effective_user.id, context)
    
    # Import and call original handler
    from handlers.multi_dispute_manager import show_disputes_dashboard
    await show_disputes_dashboard(update, context)

async def direct_select_dispute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for selecting a specific dispute"""
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "⚖️ Selected")
    
    # Parse callback data - can be either:
    # 1. "dispute_trade:{escrow_id}" from trade view (Report Issue button)
    # 2. "select_dispute_{dispute_id}" from disputes dashboard
    callback_data = query.data if query else None
    
    if not callback_data:
        logger.error("No callback data provided for dispute selection")
        return
    
    # Extract dispute_id based on format
    dispute_id = None
    escrow_id = None
    
    if "dispute_trade:" in callback_data or "select_dispute_trade:" in callback_data:
        # Format: dispute_trade:170 or select_dispute_trade:170 (escrow_id)
        try:
            escrow_id = int(callback_data.split(":")[-1])
            logger.info(f"🔍 Extracting dispute from escrow_id: {escrow_id}")
        except (ValueError, IndexError):
            logger.error(f"Invalid escrow ID in callback: {callback_data}")
            return
        
        # Look up dispute by escrow_id
        async with async_managed_session() as session:
            stmt = select(Dispute).where(Dispute.escrow_id == escrow_id)
            result = await session.execute(stmt)
            dispute = result.scalar_one_or_none()
            
            if dispute:
                dispute_id = dispute.id
                logger.info(f"✅ Found dispute {dispute_id} for escrow {escrow_id}")
            else:
                logger.error(f"No dispute found for escrow {escrow_id}")
                await safe_answer_callback_query(query, "❌ No active dispute found for this trade.", show_alert=True)
                return
    elif "view_dispute:" in callback_data:
        # Format: view_dispute:123 (dispute_id)
        try:
            dispute_id = int(callback_data.split(":")[-1])
            logger.info(f"🔍 View dispute selection: {dispute_id}")
        except (ValueError, IndexError):
            logger.error(f"Invalid dispute ID in callback: {callback_data}")
            return
    else:
        # Format: select_dispute_123 (dispute_id)
        try:
            dispute_id = int(callback_data.split("_")[-1])
            logger.info(f"🔍 Direct dispute selection: {dispute_id}")
        except (ValueError, IndexError):
            logger.error(f"Invalid dispute ID in callback: {callback_data}")
            return
    
    if not dispute_id:
        logger.error("Could not extract dispute_id from callback")
        return
    
    # Set selected state with dispute context
    await set_multi_dispute_state(update.effective_user.id, "selected", {"dispute_id": dispute_id}, context)
    
    # CRITICAL FIX: Register dispute with dispute_manager so process_dispute_message can find it
    from handlers.dispute_chat import dispute_manager, active_dispute_chat
    dispute_manager.add_dispute_session(update.effective_user.id, dispute_id)
    active_dispute_chat[update.effective_user.id] = dispute_id
    logger.info(f"✅ Registered dispute {dispute_id} for user {update.effective_user.id} in dispute_manager")
    
    # Import and call original handler
    from handlers.multi_dispute_manager import handle_dispute_selection
    await handle_dispute_selection(update, context)

async def direct_handle_dispute_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for dispute actions"""
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "⚡ Action")
    
    # Check state
    state = await get_multi_dispute_state(update.effective_user.id)
    if state != "selected":
        return
    
    logger.info(f"✅ DIRECT HANDLER: Processing dispute action for user {update.effective_user.id}")
    
    # Import and call original handler
    from handlers.multi_dispute_manager import handle_dispute_action
    await handle_dispute_action(update, context)

async def direct_resolve_dispute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for resolving disputes"""
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "✅ Resolving")
    
    # Clear state after resolution
    await clear_multi_dispute_state(update.effective_user.id, context)
    
    # Import and call original handler
    from handlers.multi_dispute_manager import resolve_dispute
    await resolve_dispute(update, context)

async def direct_escalate_dispute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for escalating disputes"""
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "🔺 Escalating")
    
    # Clear state after escalation
    await clear_multi_dispute_state(update.effective_user.id, context)
    
    # Import and call original handler
    from handlers.multi_dispute_manager import escalate_dispute
    await escalate_dispute(update, context)

async def direct_add_dispute_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for adding dispute notes"""
    if not update.message or not update.message.text:
        return
    
    # Check state
    state = await get_multi_dispute_state(update.effective_user.id)
    if state != "note_input":
        return
    
    logger.info(f"✅ DIRECT HANDLER: Processing dispute note for user {update.effective_user.id}")
    
    # Clear state after note added
    await clear_multi_dispute_state(update.effective_user.id, context)
    
    # Import and call original handler
    from handlers.multi_dispute_manager import add_dispute_note
    await add_dispute_note(update, context)

async def direct_start_note_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for starting note input"""
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "📝 Note")
    
    # Set note input state
    await set_multi_dispute_state(update.effective_user.id, "note_input", None, context)
    
    # Import and call original handler
    from handlers.multi_dispute_manager import start_note_input
    await start_note_input(update, context)

# Router for multi-dispute text input
async def multi_dispute_text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route multi-dispute text messages to correct handler based on state"""
    if not update.message or not update.message.text:
        return
    
    user_id = update.effective_user.id
    state = await get_multi_dispute_state(user_id)
    
    logger.debug(f"🔀 MULTI-DISPUTE ROUTER: User {user_id} in state '{state}', message: {update.message.text}")
    
    if state == "note_input":
        await direct_add_dispute_note(update, context)

# Direct handlers list for registration
DIRECT_MULTI_DISPUTE_HANDLERS = [
    # Main dispute dashboard
    CallbackQueryHandler(direct_show_disputes_dashboard, pattern="^disputes_dashboard$"),
    CallbackQueryHandler(direct_show_disputes_dashboard, pattern="^manage_disputes$"),
    CallbackQueryHandler(direct_show_disputes_dashboard, pattern="^multi_disputes$"),
    
    # Dispute selection
    CallbackQueryHandler(direct_select_dispute, pattern="^select_dispute_.*$"),
    CallbackQueryHandler(direct_select_dispute, pattern="^view_dispute:.*$"),
    
    # Dispute actions
    CallbackQueryHandler(direct_handle_dispute_action, pattern="^dispute_action_.*$"),
    CallbackQueryHandler(direct_resolve_dispute, pattern="^resolve_dispute_.*$"),
    CallbackQueryHandler(direct_escalate_dispute, pattern="^escalate_dispute_.*$"),
    
    # Note management
    CallbackQueryHandler(direct_start_note_input, pattern="^add_dispute_note_.*$"),
    CallbackQueryHandler(direct_start_note_input, pattern="^note_dispute_.*$"),
    
    # Navigation
    CallbackQueryHandler(direct_show_disputes_dashboard, pattern="^back_to_disputes$"),
    
    # Text input router
    MessageHandler(filters.TEXT & ~filters.COMMAND, multi_dispute_text_router),
]