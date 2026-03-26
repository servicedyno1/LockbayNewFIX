"""
Session Management UI for 50,000+ Users
Provides interface for switching between multiple concurrent operations
"""

import logging
from typing import List, Dict
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, CallbackQueryHandler

from utils.universal_session_manager import (
    universal_session_manager, SessionType, OperationStatus
)
from utils.admin_security import is_admin_silent
from utils.callback_utils import safe_edit_message_text, safe_answer_callback_query

logger = logging.getLogger(__name__)


async def show_active_sessions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Display all active sessions for a user with switching capability"""
    user = update.effective_user
    if not user:
        return ConversationHandler.END
    
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "📋")
    
    # Get all user sessions
    sessions = universal_session_manager.get_user_sessions(user.id)
    
    if not sessions:
        message = "📭 No Active Sessions\n\nYou don't have any active operations."
        keyboard = [[InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]]
        
        if query:
            await safe_edit_message_text(query, message, 
                                        reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.message.reply_text(message,
                                           reply_markup=InlineKeyboardMarkup(keyboard))
        return ConversationHandler.END
    
    # Group sessions by type
    session_groups: Dict[SessionType, List] = {}
    for session in sessions:
        if session.session_type not in session_groups:
            session_groups[session.session_type] = []
        session_groups[session.session_type].append(session)
    
    # Build display message
    message = f"📋 Your Active Sessions ({len(sessions)} total)\n\n"
    
    # Display statistics
    universal_session_manager.get_statistics()
    if user.id in [s.user_id for s in sessions]:
        sessions_per_type = {}
        for session in sessions:
            st = session.session_type.value
            sessions_per_type[st] = sessions_per_type.get(st, 0) + 1
        
        message += "Your Activity:\n"
        for stype, count in sessions_per_type.items():
            emoji = get_session_emoji(stype)
            message += f"{emoji} {stype.replace('_', ' ').title()}: {count}\n"
        message += "\n"
    
    # Build keyboard with session options
    keyboard = []
    
    # Add section for each session type
    for session_type, type_sessions in session_groups.items():
        type_name = session_type.value.replace('_', ' ').title()
        emoji = get_session_emoji(session_type.value)
        
        # Add header button (non-interactive)
        keyboard.append([InlineKeyboardButton(
            f"━━━ {emoji} {type_name} ({len(type_sessions)}) ━━━",
            callback_data="noop"
        )])
        
        # Add buttons for each session (max 3 per type for UI clarity)
        for session in type_sessions[:3]:
            status_emoji = get_status_emoji(session.status)
            session_label = get_session_label(session)
            keyboard.append([InlineKeyboardButton(
                f"{status_emoji} {session_label}",
                callback_data=f"view_session:{session.session_id}"
            )])
        
        if len(type_sessions) > 3:
            keyboard.append([InlineKeyboardButton(
                f"View all {len(type_sessions)} {type_name}...",
                callback_data=f"view_all_sessions:{session_type.value}"
            )])
    
    # Add management options
    keyboard.append([
        InlineKeyboardButton("🔄 Refresh", callback_data="refresh_sessions"),
        InlineKeyboardButton("🧹 Clean Up", callback_data="cleanup_sessions")
    ])
    keyboard.append([InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")])
    
    # Add admin options if applicable (silent check to avoid security alerts)
    if is_admin_silent(user.id):
        keyboard.append([InlineKeyboardButton(
            "👨‍💼 Admin: System Stats", callback_data="admin_session_stats"
        )])
    
    if query:
        await safe_edit_message_text(query, message,
                                    reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(message,
                                       reply_markup=InlineKeyboardMarkup(keyboard))
    
    return ConversationHandler.END


async def view_session_details(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """View details of a specific session"""
    query = update.callback_query
    user = update.effective_user
    
    if not query or not user:
        return ConversationHandler.END
    
    await safe_answer_callback_query(query, "📄")
    
    # Extract session ID
    try:
        _, session_id = query.data.split(":")
    except ValueError:
        await safe_answer_callback_query(query, "Invalid session", show_alert=True)
        return ConversationHandler.END
    
    # Get session details
    session = universal_session_manager.get_session(session_id)
    if not session or session.user_id != user.id:
        await safe_answer_callback_query(query, "Session not found", show_alert=True)
        return ConversationHandler.END
    
    # Build detailed view
    message = "📄 Session Details\n\n"
    message += f"Type: {session.session_type.value.replace('_', ' ').title()}\n"
    message += f"Status: {get_status_emoji(session.status)} {session.status.value}\n"
    message += f"Created: {session.created_at.strftime('%Y-%m-%d %H:%M')}\n"
    message += f"Updated: {session.updated_at.strftime('%Y-%m-%d %H:%M')}\n"
    
    if session.expires_at:
        remaining = (session.expires_at - datetime.utcnow()).total_seconds()
        if remaining > 0:
            minutes = int(remaining / 60)
            message += f"Expires in: {minutes} minutes\n"
        else:
            message += "Status: ⏰ Expired\n"
    
    # Add metadata if available
    if session.metadata:
        message += "\nDetails:\n"
        for key, value in session.metadata.items():
            if key not in ['password', 'token', 'secret']:  # Hide sensitive data
                message += f"• {key.replace('_', ' ').title()}: {value}\n"
    
    # Build action keyboard
    keyboard = []
    
    # Add action buttons based on session type and status
    if session.status == OperationStatus.ACTIVE:
        keyboard.append([
            InlineKeyboardButton("▶️ Continue", callback_data=f"continue_session:{session_id}"),
            InlineKeyboardButton("⏸️ Pause", callback_data=f"pause_session:{session_id}")
        ])
    elif session.status == OperationStatus.PENDING:
        keyboard.append([
            InlineKeyboardButton("▶️ Resume", callback_data=f"resume_session:{session_id}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_session:{session_id}")
        ])
    
    keyboard.append([
        InlineKeyboardButton("🔄 Refresh", callback_data=f"view_session:{session_id}"),
        InlineKeyboardButton("🔙 Back", callback_data="show_sessions")
    ])
    
    await safe_edit_message_text(query, message,
                                reply_markup=InlineKeyboardMarkup(keyboard))
    
    return ConversationHandler.END


async def switch_session(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Switch to a different session"""
    query = update.callback_query
    user = update.effective_user
    
    if not query or not user:
        return ConversationHandler.END
    
    await safe_answer_callback_query(query, "🔄")
    
    # Extract action and session ID
    try:
        action, session_id = query.data.split(":")
    except ValueError:
        await safe_answer_callback_query(query, "Invalid action", show_alert=True)
        return ConversationHandler.END
    
    session = universal_session_manager.get_session(session_id)
    if not session or session.user_id != user.id:
        await safe_answer_callback_query(query, "Session not found", show_alert=True)
        return ConversationHandler.END
    
    success = False
    message = ""
    
    if action == "continue_session" or action == "resume_session":
        # Activate this session
        success = universal_session_manager.switch_active_session(
            user.id, session.session_type, session_id
        )
        if success:
            message = f"✅ Switched to {session.session_type.value.replace('_', ' ').title()}"
            
            # Route to appropriate handler based on type
            if session.session_type == SessionType.TRADE_CHAT:
                # Open trade chat
                from handlers.messages_hub import open_trade_chat
                trade_id = session.metadata.get('trade_id')
                if trade_id:
                    query.data = f"trade_chat_open:{trade_id}"
                    await open_trade_chat(update, context)
                    return ConversationHandler.END
            
            elif session.session_type == SessionType.DIRECT_EXCHANGE:
                # Resume exchange
                from handlers.exchange_handler import ExchangeHandler
                await ExchangeHandler.start_exchange(update, context)
                return ConversationHandler.END
            
            elif session.session_type == SessionType.CASHOUT:
                # Resume cashout
                from handlers.wallet_direct import show_wallet_dashboard
                await show_wallet_dashboard(update, context)
                return ConversationHandler.END
    
    elif action == "pause_session":
        success = universal_session_manager.update_session(
            session_id, status=OperationStatus.PENDING
        )
        message = "⏸️ Session paused"
    
    elif action == "cancel_session":
        success = universal_session_manager.close_session(session_id)
        message = "❌ Session cancelled"
    
    if success:
        await safe_answer_callback_query(query, message)
        # Refresh the session list
        await show_active_sessions(update, context)
    else:
        await safe_answer_callback_query(query, "Failed to update session", show_alert=True)
    
    return ConversationHandler.END


async def cleanup_user_sessions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Clean up expired or completed sessions for a user"""
    query = update.callback_query
    user = update.effective_user
    
    if not query or not user:
        return ConversationHandler.END
    
    await safe_answer_callback_query(query, "🧹")
    
    # Get user sessions
    sessions = universal_session_manager.get_user_sessions(user.id)
    
    cleaned = 0
    for session in sessions:
        # Clean up expired or completed sessions
        if session.status in [OperationStatus.COMPLETED, OperationStatus.FAILED, OperationStatus.EXPIRED]:
            if universal_session_manager.close_session(session.session_id):
                cleaned += 1
    
    if cleaned > 0:
        await safe_answer_callback_query(query, f"✅ Cleaned up {cleaned} sessions")
    else:
        await safe_answer_callback_query(query, "No sessions to clean up")
    
    # Refresh the list
    await show_active_sessions(update, context)
    return ConversationHandler.END


async def show_admin_session_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show system-wide session statistics for admins"""
    query = update.callback_query
    user = update.effective_user
    
    if not query or not user or not is_admin_silent(user.id):
        return ConversationHandler.END
    
    await safe_answer_callback_query(query, "📊")
    
    # Get system statistics
    stats = universal_session_manager.get_statistics()
    
    message = "📊 **System Session Statistics**\n\n"
    message += f"**Active Sessions:** {stats['current_sessions']:,}\n"
    message += f"**Active Users:** {stats['active_users']:,}\n"
    message += f"**Peak Concurrent:** {stats['peak_concurrent']:,}\n"
    message += f"**Total Created:** {stats['total_sessions_created']:,}\n"
    message += f"**Avg per User:** {stats['average_sessions_per_user']:.2f}\n\n"
    
    if stats['session_breakdown']:
        message += "**Sessions by Type:**\n"
        for stype, count in stats['session_breakdown'].items():
            emoji = get_session_emoji(stype)
            message += f"{emoji} {stype.replace('_', ' ').title()}: {count}\n"
    
    # Calculate system capacity
    max_capacity = 50000 * 20  # 50k users * avg 20 sessions each
    current_load = (stats['current_sessions'] / max_capacity) * 100 if max_capacity > 0 else 0
    
    message += f"\n**System Load:** {current_load:.1f}%\n"
    
    if current_load > 80:
        message += "⚠️ **Warning:** High system load detected\n"
    elif current_load > 60:
        message += "⚡ **Status:** Moderate load\n"
    else:
        message += "✅ **Status:** System running smoothly\n"
    
    keyboard = [
        [
            InlineKeyboardButton("🔄 Refresh", callback_data="admin_session_stats"),
            InlineKeyboardButton("💾 Persist", callback_data="admin_persist_sessions")
        ],
        [
            InlineKeyboardButton("🧹 Global Cleanup", callback_data="admin_cleanup_sessions"),
            InlineKeyboardButton("📊 Detailed Report", callback_data="admin_detailed_report")
        ],
        [InlineKeyboardButton("🔙 Back", callback_data="show_sessions")]
    ]
    
    await safe_edit_message_text(query, message,
                                reply_markup=InlineKeyboardMarkup(keyboard))
    
    return ConversationHandler.END


# Helper functions
def get_session_emoji(session_type: str) -> str:
    """Get emoji for session type"""
    emojis = {
        "trade_chat": "💬",
        "dispute_chat": "⚖️",
        "direct_exchange": "💱",
        "wallet_operation": "💰",
        "cashout": "💸",
        "deposit": "💳",
        "escrow_create": "🔒",
        "escrow_messaging": "📨"
    }
    return emojis.get(session_type, "📄")


def get_status_emoji(status: OperationStatus) -> str:
    """Get emoji for operation status"""
    emojis = {
        OperationStatus.PENDING: "⏳",
        OperationStatus.ACTIVE: "🟢",
        OperationStatus.PROCESSING: "⚡",
        OperationStatus.COMPLETED: "✅",
        OperationStatus.FAILED: "❌",
        OperationStatus.EXPIRED: "⏰"
    }
    return emojis.get(status, "❓")


def get_session_label(session) -> str:
    """Get descriptive label for a session"""
    if session.session_type == SessionType.TRADE_CHAT:
        trade_ref = session.metadata.get('trade_ref', 'Unknown')
        return f"Trade {trade_ref[:8]}"
    elif session.session_type == SessionType.DIRECT_EXCHANGE:
        return f"Exchange #{session.session_id[-6:]}"
    elif session.session_type == SessionType.CASHOUT:
        amount = session.metadata.get('amount', 'N/A')
        return f"Cashout ${amount}"
    else:
        return f"Session #{session.session_id[-6:]}"


# Register handlers
def register_session_ui_handlers(application):
    """Register session UI handlers with the application"""
    application.add_handler(CallbackQueryHandler(
        show_active_sessions, pattern="^show_sessions$"
    ))
    application.add_handler(CallbackQueryHandler(
        view_session_details, pattern="^view_session:"
    ))
    application.add_handler(CallbackQueryHandler(
        switch_session, pattern="^(continue|resume|pause|cancel)_session:"
    ))
    application.add_handler(CallbackQueryHandler(
        cleanup_user_sessions, pattern="^cleanup_sessions$"
    ))
    application.add_handler(CallbackQueryHandler(
        show_active_sessions, pattern="^refresh_sessions$"
    ))
    application.add_handler(CallbackQueryHandler(
        show_admin_session_stats, pattern="^admin_session_stats$"
    ))
    
    logger.info("✅ Session UI handlers registered")