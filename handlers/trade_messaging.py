"""
Trade Messaging Handler
Handles trade communication callbacks between buyers and sellers
"""

import logging
from telegram import Update
from telegram.ext import ContextTypes
from utils.callback_utils import safe_answer_callback_query

logger = logging.getLogger(__name__)


async def handle_message_buyer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle message_buyer callback - redirect to unified chat interface"""
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "💬")

    if not update.effective_user:
        return

    # Extract escrow_id from callback data (format: "message_buyer:12")
    callback_data = (query.data or "") if query else ""
    if not callback_data.startswith('message_buyer:'):
        if query:
            await query.edit_message_text("❌ Unable to access trade chat. Please try again from your trades list.")
        return

    try:
        escrow_id = callback_data.split(':')[1]
        # Validate escrow_id is not empty and is numeric
        if not escrow_id or not escrow_id.isdigit():
            raise ValueError("Invalid escrow ID format")
        
        # Redirect to unified trade chat interface
        from handlers.messages_hub import open_trade_chat
        
        # Update callback data to match the unified format
        if query:
            query.data = f"trade_chat_open:{escrow_id}"
        
        # Call the unified chat handler
        await open_trade_chat(update, context)
        return
        
    except (IndexError, ValueError):
        if query:
            await query.edit_message_text("❌ Invalid trade ID. Please access chat from your active trades.")
        return


async def handle_message_seller(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle message_seller callback - redirect to unified chat interface"""
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "💬")

    if not update.effective_user:
        return

    # Extract escrow_id from callback data (format: "message_seller:12")
    callback_data = (query.data or "") if query else ""
    if not callback_data.startswith('message_seller:'):
        if query:
            await query.edit_message_text("❌ Unable to access trade chat. Please try again from your trades list.")
        return

    try:
        escrow_id = callback_data.split(':')[1]
        # Validate escrow_id is not empty and is numeric
        if not escrow_id or not escrow_id.isdigit():
            raise ValueError("Invalid escrow ID format")
        
        # Redirect to unified trade chat interface
        from handlers.messages_hub import open_trade_chat
        
        # Update callback data to match the unified format
        if query:
            query.data = f"trade_chat_open:{escrow_id}"
        
        # Call the unified chat handler
        await open_trade_chat(update, context)
        return
    except (IndexError, ValueError):
        if query:
            await query.edit_message_text("❌ Invalid trade ID. Please access chat from your active trades.")
        return