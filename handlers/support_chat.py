"""
Support Chat Handler - Real-time customer support system
Provides live chat between users and admins with dual Telegram + Email notifications
"""

import logging
from typing import Dict
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from sqlalchemy import desc

from database import SyncSessionLocal, async_managed_session
from models import User, SupportTicket, SupportMessage
from utils.callback_utils import safe_edit_message_text, safe_answer_callback_query
from utils.support_prefetch import (
    invalidate_support_cache,
    get_or_prefetch_support_context
)
from utils.admin_security import is_admin_silent
from utils.admin import get_admin_user_ids
from services.email import EmailService
from utils.comprehensive_audit_logger import (
    ComprehensiveAuditLogger, AuditEventType, RelatedIDs, PayloadMetadata
)
from utils.handler_decorators import audit_handler

logger = logging.getLogger(__name__)

# Initialize communication audit logger
communication_audit = ComprehensiveAuditLogger("communication")

# Conversation states
SUPPORT_CHAT_VIEW = 1
SUPPORT_MESSAGE_INPUT = 2

# Active support chat sessions - tracking user conversation states
# Stores user_id -> (ticket_id, timestamp) for stale session detection
active_support_sessions: Dict[int, int] = {}  # user_id -> ticket_id
_support_session_timestamps: Dict[int, float] = {}  # user_id -> epoch timestamp
SUPPORT_SESSION_TTL_SECONDS = 1800  # 30 minutes


def _record_support_session(user_id: int, ticket_id: int):
    """Record a support session with timestamp for staleness detection"""
    import time
    active_support_sessions[user_id] = ticket_id
    _support_session_timestamps[user_id] = time.time()


def cleanup_stale_support_sessions(max_age_seconds: int = SUPPORT_SESSION_TTL_SECONDS) -> int:
    """
    Remove support sessions older than max_age_seconds.
    Called periodically by the scheduler to prevent stale sessions from hijacking other flows.
    Returns the number of sessions cleaned up.
    """
    import time
    current_time = time.time()
    stale_users = [
        uid for uid, ts in _support_session_timestamps.items()
        if current_time - ts > max_age_seconds
    ]
    for uid in stale_users:
        active_support_sessions.pop(uid, None)
        _support_session_timestamps.pop(uid, None)
    if stale_users:
        logger.info(f"🧹 SUPPORT_CLEANUP: Removed {len(stale_users)} stale support sessions (>{max_age_seconds}s old): {stale_users}")
    return len(stale_users)


@audit_handler(AuditEventType.COMMUNICATION, "support_chat_start")
async def start_support_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start a new support chat or resume existing one"""
    user = update.effective_user
    if not user:
        return ConversationHandler.END

    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "🆘")

    try:
        # OPTIMIZATION: First get database user ID (1 query - required for prefetch)
        session_sync = SyncSessionLocal()
        try:
            db_user = session_sync.query(User).filter(User.telegram_id == int(user.id)).first()
            if not db_user:
                if query:
                    await safe_edit_message_text(query, "❌ User not found. Please restart with /start")
                return ConversationHandler.END
            db_user_id = db_user.id
        finally:
            session_sync.close()

        # OPTIMIZATION: Prefetch support context (2 queries instead of 23+)
        async with async_managed_session() as session:
            support_data = await get_or_prefetch_support_context(
                db_user_id, session, context.user_data
            )
            
            if not support_data:
                if query:
                    await safe_edit_message_text(query, "❌ Error loading support data. Please try again.")
                return ConversationHandler.END

            # Check if user has existing active session (from prefetch)
            if support_data.has_active_session and support_data.session_id:
                # Resume existing chat using prefetched data
                # Re-query ticket to get ORM object for operations
                from sqlalchemy import select
                stmt = select(SupportTicket).where(SupportTicket.id == support_data.session_id)
                result = await session.execute(stmt)
                ticket = result.scalar_one_or_none()
                
                if ticket:
                    logger.info(f"📞 Resuming support chat for user {user.id}, ticket {ticket.ticket_id}")
                else:
                    # Fallback: session_id was cached but ticket is gone
                    invalidate_support_cache(context.user_data)
                    ticket = None
            else:
                ticket = None

            if not ticket:
                # Create new ticket
                from sqlalchemy import select, func
                count_stmt = select(func.count(SupportTicket.id))
                count_result = await session.execute(count_stmt)
                ticket_count = count_result.scalar() or 0
                ticket_id = f"SUP-{(ticket_count + 1):03d}"
                
                ticket = SupportTicket(
                    ticket_id=ticket_id,
                    user_id=db_user_id,
                    subject="Live Chat Support",
                    status="open",
                    priority="normal"
                )
                session.add(ticket)
                await session.commit()
                
                # Refresh to get generated ID
                await session.refresh(ticket)
                
                logger.info(f"📞 Created new support ticket {ticket_id} for user {user.id}")
                
                # Invalidate cache since we created a new ticket
                invalidate_support_cache(context.user_data)
                
                # Notify admins about new ticket (use db_user object for notification)
                db_user_obj = User(
                    id=support_data.user_id,
                    telegram_id=support_data.telegram_id,
                    username=support_data.username,
                    first_name=support_data.first_name,
                    email=support_data.email
                )
                await notify_admins_new_ticket(context, ticket, db_user_obj)

            # Track active session - get actual ticket ID value
            _record_support_session(user.id, ticket.id if hasattr(ticket, 'id') and ticket.id is not None else 0)
            
            # Log support chat session start with communication metadata
            session_metadata = PayloadMetadata(
                communication_type="support",
                message_thread_id=ticket.ticket_id,
                participant_count=2,  # user and admin
                is_admin_message=False,
                navigation_depth=1
            )
            
            related_ids = RelatedIDs(
                support_ticket_id=ticket.ticket_id,
                conversation_id=f"support_{ticket.id}"
            )
            
            communication_audit.audit(
                event_type=AuditEventType.COMMUNICATION,
                action="support_chat_session_started",
                result="success",
                user_id=user.id,
                related_ids=related_ids,
                payload_metadata=session_metadata
            )

            # Show chat interface with prefetched data
            db_user_obj = User(
                id=support_data.user_id,
                telegram_id=support_data.telegram_id,
                username=support_data.username,
                first_name=support_data.first_name,
                email=support_data.email
            )
            await show_support_chat_interface(update, context, ticket, db_user_obj, support_data)
            
            return SUPPORT_CHAT_VIEW

    except Exception as e:
        logger.error(f"Error starting support chat: {e}")
        if query:
            await safe_edit_message_text(query, "❌ Error starting support chat. Please try again.")
        return ConversationHandler.END


async def show_support_chat_interface(update: Update, context: ContextTypes.DEFAULT_TYPE, ticket: SupportTicket, user: User, support_data=None) -> None:
    """Display the support chat interface with message history"""
    query = update.callback_query
    
    try:
        # OPTIMIZATION: Use prefetched/cached data if available
        if support_data and support_data.recent_messages:
            # Use cached messages (last 10 for display)
            messages_data = support_data.recent_messages[-10:]  # Get last 10 messages
            admin_username = support_data.admin_username
            admin_first_name = support_data.admin_first_name if hasattr(support_data, 'admin_first_name') else None
        else:
            # Fallback: Query database if no prefetch data
            session = SyncSessionLocal()
            try:
                from sqlalchemy.orm import selectinload
                
                # Eagerly load the assigned_admin to avoid lazy loading issues
                ticket_with_admin = session.query(SupportTicket).options(
                    selectinload(SupportTicket.assigned_admin)
                ).filter(SupportTicket.id == ticket.id).first()
                
                messages = session.query(SupportMessage).filter(
                    SupportMessage.ticket_id == ticket.id
                ).order_by(desc(SupportMessage.created_at)).limit(10).all()
                messages.reverse()  # Show chronologically
                
                # Convert to data format
                messages_data = [
                    type('obj', (object,), {
                        'is_admin_reply': msg.is_admin_reply,
                        'created_at': msg.created_at,
                        'message': msg.message
                    })()
                    for msg in messages
                ]
                
                # Access admin data INSIDE session context
                admin_username = ticket_with_admin.assigned_admin.username if ticket_with_admin and ticket_with_admin.assigned_admin else None
                admin_first_name = ticket_with_admin.assigned_admin.first_name if ticket_with_admin and ticket_with_admin.assigned_admin else None
            finally:
                session.close()

        # Build chat display
        chat_text = f"🆘 Support Chat #{ticket.ticket_id}\n\n"
        
        if admin_username or admin_first_name:
            admin_name = admin_first_name or "Admin"
            chat_text += f"👨‍💼 Assigned to: {admin_name}\n"
        else:
            chat_text += "⏳ Status: Waiting for admin response\n"
            
        chat_text += f"🕐 Created: {ticket.created_at.strftime('%b %d, %H:%M')}\n\n"

        if messages_data:
            chat_text += "💬 Recent Messages:\n"
            for msg in messages_data:
                sender_name = "👨‍💼 Admin" if getattr(msg, 'is_admin_reply', getattr(msg, 'is_admin', False)) else "👤 You"
                time_str = msg.created_at.strftime('%H:%M')
                
                # Truncate long messages
                message_preview = msg.message
                if len(message_preview) > 100:
                    message_preview = message_preview[:100] + "..."
                    
                chat_text += f"{sender_name} ({time_str}):\n{message_preview}\n\n"
        else:
            chat_text += "💬 No messages yet. Type your question below and an admin will respond shortly.\n\n"

        chat_text += "📝 Type your message below to continue the conversation..."

        # Chat interface buttons (auto-refresh every 30 seconds)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Close Ticket", callback_data=f"support_close_ticket:{ticket.id}")],
            [InlineKeyboardButton("🔙 Back to Support", callback_data="contact_support")]
        ])
        
        # Auto-refresh is now managed by AutoRefreshManager - no manual scheduling needed

        if query:
            await safe_edit_message_text(query, chat_text, reply_markup=keyboard, parse_mode="Markdown")
        else:
            await update.message.reply_text(chat_text, reply_markup=keyboard, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error showing support chat interface: {e}")


async def handle_support_message_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle incoming support messages from users"""
    user = update.effective_user
    message = update.message
    
    if not user or not message or not message.text:
        return SUPPORT_CHAT_VIEW

    # CRITICAL FIX: Check if user is in exclusive states (OTP verification, etc.) before processing support messages
    # This prevents auto-restoration of support sessions during other operations
    from handlers.wallet_direct import get_wallet_state
    wallet_state = await get_wallet_state(user.id, context)
    
    # Define exclusive states where support messages should NOT be auto-processed
    exclusive_states = [
        'verifying_crypto_otp',
        'verifying_bank_otp', 
        'verifying_ngn_otp',
        'entering_address',
        'entering_amount'
    ]
    
    if wallet_state in exclusive_states:
        logger.info(f"🚫 User {user.id} in exclusive state '{wallet_state}' - not processing as support message")
        return ConversationHandler.END  # Exit conversation, let other handlers process

    # Check if user has active support session (restore from DB if missing after restart)
    ticket_id = active_support_sessions.get(user.id)
    
    if not ticket_id:
        # SECURITY FIX: Only auto-restore support sessions if user explicitly interacted with support interface
        # Check for recent support interface interaction within last 10 minutes to prevent accidental restoration
        session_check = SyncSessionLocal()
        try:
            db_user = session_check.query(User).filter(User.telegram_id == str(user.id)).first()
            if db_user:
                active_ticket = session_check.query(SupportTicket).filter(
                    SupportTicket.user_id == db_user.id,
                    SupportTicket.status.in_(["open", "assigned"])
                ).order_by(desc(SupportTicket.created_at)).first()
                
                if active_ticket:
                    # SECURITY CHECK: Only restore if there was recent activity in support ticket
                    # This prevents auto-restoration for old tickets during unrelated operations
                    recent_cutoff = datetime.utcnow() - timedelta(minutes=10)
                    
                    # Check if ticket has recent activity OR if user recently interacted with support interface
                    if (active_ticket.last_message_at and active_ticket.last_message_at > recent_cutoff):
                        # Restore active session ONLY if recent activity
                        _record_support_session(user.id, active_ticket.id)
                        ticket_id = active_ticket.id
                        logger.info(f"🔄 Restored support session for user {user.id}, ticket {active_ticket.ticket_id} (recent activity)")
                    else:
                        logger.info(f"🚫 NOT restoring support session for user {user.id} - no recent activity (last: {active_ticket.last_message_at})")
        finally:
            session_check.close()
    
    if not ticket_id:
        # No active support session, exit conversation and let other handlers process this message
        return ConversationHandler.END
    
    session = SyncSessionLocal()
    try:
        # Get ticket and user
        ticket = session.query(SupportTicket).filter(SupportTicket.id == ticket_id).first()
        db_user = session.query(User).filter(User.telegram_id == int(user.id)).first()
        
        if not ticket or not db_user:
            await message.reply_text("❌ Support session not found. Please start a new chat.")
            return ConversationHandler.END

        # Create message record
        support_message = SupportMessage(
            ticket_id=ticket.id,
            sender_id=db_user.id,
            message=message.text,
            is_admin_reply=False
        )
        session.add(support_message)
        
        # Update ticket last message time
        ticket.last_message_at = datetime.utcnow()
        session.commit()

        logger.info(f"📝 Support message from user {user.id} in ticket {ticket.ticket_id}")

        # OPTIMIZATION: Invalidate cache after new message sent
        invalidate_support_cache(context.user_data)

        # Send confirmation to user
        await message.reply_text(
            f"✅ Message sent to support team\n\n"
            f"📋 Ticket: {ticket.ticket_id}\n"
            f"💬 Your message: \"{message.text[:50]}{'...' if len(message.text) > 50 else ''}\"\n\n"
            f"⏰ You'll receive a notification when an admin responds.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💬 Continue Chat", callback_data=f"support_chat_open:{ticket.id}")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
            ]),
            parse_mode="Markdown"
        )

        # Notify admins about new message
        await notify_admins_new_message(context, ticket, db_user, message.text)

        # Message processed successfully - stay in support chat view
        return SUPPORT_CHAT_VIEW

    except Exception as e:
        logger.error(f"Error handling support message: {e}")
        await message.reply_text("❌ Error sending message. Please try again.")
        # Error occurred but stay in support chat to allow retry
        return SUPPORT_CHAT_VIEW
    finally:
        session.close()


async def open_support_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Open specific support chat by ticket ID"""
    user = update.effective_user
    if not user:
        return

    query = update.callback_query
    if not query or not query.data:
        return
    
    try:
        # Extract ticket ID from callback data
        ticket_id = int(query.data.split(":")[1])
        
        # OPTIMIZATION: First get database user ID
        session_sync = SyncSessionLocal()
        try:
            db_user = session_sync.query(User).filter(User.telegram_id == int(user.id)).first()
            if not db_user:
                await safe_answer_callback_query(query, "❌ User not found")
                return
            db_user_id = db_user.id
        finally:
            session_sync.close()

        # OPTIMIZATION: Use async session for ticket query and prefetch
        async with async_managed_session() as session:
            # Get ticket and verify access
            from sqlalchemy import select
            stmt = select(SupportTicket).where(SupportTicket.id == ticket_id)
            result = await session.execute(stmt)
            ticket = result.scalar_one_or_none()
            
            if not ticket:
                await safe_answer_callback_query(query, "❌ Ticket not found")
                return

            # Verify user access (user owns ticket OR user is admin)
            is_admin = is_admin_silent(user.id)
            if ticket.user_id != db_user_id and not is_admin:
                await safe_answer_callback_query(query, "❌ Access denied")
                return

            await safe_answer_callback_query(query, "💬")

            # Track active session
            _record_support_session(user.id, ticket.id)

            # OPTIMIZATION: Prefetch support data for display
            support_data = await get_or_prefetch_support_context(
                db_user_id, session, context.user_data
            )

            # Show chat interface with prefetched data
            db_user_obj = User(
                id=db_user_id,
                telegram_id=str(user.id),
                username=db_user.username,
                first_name=db_user.first_name,
                email=db_user.email
            )
            await show_support_chat_interface(update, context, ticket, db_user_obj, support_data)

    except (ValueError, IndexError):
        logger.error(f"Invalid support chat callback data: {query.data}")
        await safe_answer_callback_query(query, "❌ Invalid request")


async def view_support_tickets(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show user's support tickets"""
    user = update.effective_user
    if not user:
        return

    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "📋")

    try:
        # OPTIMIZATION: First get database user ID
        session_sync = SyncSessionLocal()
        try:
            db_user = session_sync.query(User).filter(User.telegram_id == int(user.id)).first()
            if not db_user:
                if query:
                    await safe_edit_message_text(query, "❌ User not found. Please restart with /start")
                return
            db_user_id = db_user.id
        finally:
            session_sync.close()

        # OPTIMIZATION: Use async session for ticket query
        async with async_managed_session() as session:
            # Get user's tickets
            from sqlalchemy import select
            from sqlalchemy.orm import selectinload
            stmt = (
                select(SupportTicket)
                .options(selectinload(SupportTicket.assigned_admin))
                .where(SupportTicket.user_id == db_user_id)
                .order_by(desc(SupportTicket.created_at))
                .limit(10)
            )
            result = await session.execute(stmt)
            tickets = result.scalars().all()

        if not tickets:
            text = """📋 My Support Tickets

🔍 No support tickets found.

You can start a new support chat anytime for help with trades, payments, or account issues."""
            
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🆘 Start Live Chat", callback_data="start_support_chat")],
                [InlineKeyboardButton("🔙 Back to Support", callback_data="contact_support")]
            ])
        else:
            text = f"📋 My Support Tickets ({len(tickets)} total)\n\n"
            
            keyboard_buttons = []
            
            for ticket in tickets:
                # Status emoji
                status_emoji = {
                    "open": "🟢",
                    "assigned": "🟡", 
                    "resolved": "✅",
                    "closed": "⚫"
                }.get(ticket.status, "❓")
                
                # Time format
                time_str = ticket.created_at.strftime('%b %d, %H:%M')
                
                # Admin info
                admin_info = ""
                if ticket.assigned_admin:
                    admin_info = f" • 👨‍💼 {ticket.assigned_admin.first_name or 'Admin'}"
                
                text += f"{status_emoji} {ticket.ticket_id} • {ticket.status.title()}{admin_info}\n"
                text += f"📅 {time_str} • {ticket.subject or 'Live Chat Support'}\n\n"
                
                # Add button for each ticket
                button_text = f"{ticket.ticket_id} • {ticket.status.title()}"
                keyboard_buttons.append([
                    InlineKeyboardButton(button_text, callback_data=f"support_chat_open:{ticket.id}")
                ])
            
            # Navigation buttons
            keyboard_buttons.extend([
                [InlineKeyboardButton("🆘 Start New Chat", callback_data="start_support_chat")],
                [InlineKeyboardButton("🔙 Back to Support", callback_data="contact_support")]
            ])
            
            keyboard = InlineKeyboardMarkup(keyboard_buttons)

        if query:
            await safe_edit_message_text(query, text, reply_markup=keyboard, parse_mode="Markdown")
        else:
            await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error viewing support tickets: {e}")
        if query:
            await safe_edit_message_text(query, "❌ Error loading tickets. Please try again.")


async def notify_admins_new_ticket(context: ContextTypes.DEFAULT_TYPE, ticket: SupportTicket, user: User) -> None:
    """Notify all admins about new support ticket"""
    try:
        admin_ids = get_admin_user_ids()
        if not admin_ids:
            logger.warning("No admin user IDs found for support notification")
            return

        # Telegram notification
        notification_text = f"""🆘 New Support Ticket

📋 Ticket: {ticket.ticket_id}
👤 User: {user.first_name or 'User'} (@{user.username or 'no username'})
📧 Email: {user.email}
🕐 Created: {ticket.created_at.strftime('%b %d, %Y at %H:%M')}

💬 User is waiting for support assistance."""

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎯 Assign to Me", callback_data=f"admin_assign_ticket:{ticket.id}")],
            [InlineKeyboardButton("💬 Open Chat", callback_data=f"admin_support_chat:{ticket.id}")],
            [InlineKeyboardButton("📊 Support Dashboard", callback_data="admin_support_dashboard")]
        ])

        # Send to all admins
        for admin_id in admin_ids:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=notification_text,
                    reply_markup=keyboard,
                    parse_mode="Markdown"
                )
                logger.info(f"✅ New ticket notification sent to admin {admin_id}")
            except Exception as e:
                logger.error(f"Failed to notify admin {admin_id}: {e}")

        # Email notification to admins
        try:
            from config import Config
            admin_email = getattr(Config, 'ADMIN_EMAIL', 'moxxcompany@gmail.com')
            
            email_subject = f"New Support Ticket: {ticket.ticket_id}"
            email_body = f"""
New support ticket created:

Ticket ID: {ticket.ticket_id}
User: {user.first_name or 'User'} ({user.email})
Username: @{user.username or 'no username'}
Created: {ticket.created_at.strftime('%B %d, %Y at %H:%M UTC')}

QUICK REPLY OPTIONS:
1. 📱 Reply via Telegram Bot (instant)
2. 📧 Reply to this email (subject must contain {ticket.ticket_id})

When replying via email, keep the ticket ID {ticket.ticket_id} in the subject line.
Your reply will be automatically forwarded to the user.

The user is waiting for support assistance. Please respond promptly.

LockBay Support System
"""
            
            email_service = EmailService()
            
            # Set up reply-to address for webhook routing
            reply_to_email = f"support+{ticket.ticket_id.lower()}@lockbay.io"
            
            email_sent = email_service.send_email_with_reply_to(
                to_email=admin_email,
                subject=email_subject,
                text_content=email_body,
                reply_to=reply_to_email
            )
            
            if email_sent:
                logger.info(f"✅ New ticket email sent to {admin_email}")
            else:
                logger.error(f"❌ Failed to send new ticket email to {admin_email}")
                logger.error(f"   Ticket ID: {ticket.ticket_id}")
                logger.error("   🔧 Check BREVO_API_KEY configuration in production secrets")
            
        except Exception as e:
            logger.error(f"Failed to send new ticket email: {e}")

    except Exception as e:
        logger.error(f"Error notifying admins about new ticket: {e}")


async def notify_admins_new_message(context: ContextTypes.DEFAULT_TYPE, ticket: SupportTicket, user: User, message_text: str) -> None:
    """Notify admins about new message in support ticket"""
    try:
        # If ticket is assigned, only notify assigned admin
        if ticket.assigned_admin:
            admin_ids = [int(ticket.assigned_admin.telegram_id)]
        else:
            admin_ids = get_admin_user_ids()

        if not admin_ids:
            logger.error("No admin IDs found for support notification")
            return

        # Telegram notification
        message_preview = message_text[:150] + "..." if len(message_text) > 150 else message_text
        
        notification_text = f"""💬 New Support Message

📋 Ticket: {ticket.ticket_id}
👤 From: {user.first_name or 'User'}
🕐 Time: {datetime.utcnow().strftime('%H:%M')}

Message:
"{message_preview}"

⏰ User is waiting for your response."""

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("💬 Reply", callback_data=f"admin_support_chat:{ticket.id}")],
            [InlineKeyboardButton("🎯 Assign to Me", callback_data=f"admin_assign_ticket:{ticket.id}")]
        ])

        # Send to relevant admins
        for admin_id in admin_ids:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=notification_text,
                    reply_markup=keyboard,
                    parse_mode="Markdown"
                )
                logger.info(f"✅ Support notification sent to admin {admin_id}")
            except Exception as e:
                logger.error(f"Failed to notify admin {admin_id}: {e}")

        # Email notification to admins for follow-up messages
        try:
            from config import Config
            admin_email = getattr(Config, 'ADMIN_EMAIL', 'moxxcompany@gmail.com')
            
            email_subject = f"Support Message: {ticket.ticket_id} - {user.first_name or 'User'}"
            email_body = f"""
New message in support ticket:

Ticket ID: {ticket.ticket_id}
From: {user.first_name or 'User'} ({user.email})
Username: @{user.username or 'no username'}
Time: {datetime.utcnow().strftime('%B %d, %Y at %H:%M UTC')}

MESSAGE:
"{message_text}"

QUICK REPLY OPTIONS:
1. 📱 Reply via Telegram Bot (instant)
2. 📧 Reply to this email (subject must contain {ticket.ticket_id})

When replying via email, keep the ticket ID {ticket.ticket_id} in the subject line.

This is a follow-up message in an ongoing support conversation.
"""

            from services.email import EmailService
            email_service = EmailService()
            
            # Set up reply-to address for webhook routing
            reply_to_email = f"support+{ticket.ticket_id.lower()}@lockbay.io"
            
            success = email_service.send_email_with_reply_to(
                to_email=admin_email,
                subject=email_subject,
                text_content=email_body,
                reply_to=reply_to_email
            )
            
            if success:
                logger.info(f"✅ Support message email sent to {admin_email}")
            else:
                logger.error(f"❌ Failed to send support message email to {admin_email}")
                
        except Exception as e:
            logger.error(f"Error sending admin email for support message: {e}")

    except Exception as e:
        logger.error(f"Error notifying admins about new message: {e}")


async def auto_refresh_support_chat(update: Update, context: ContextTypes.DEFAULT_TYPE, ticket_id: int) -> bool:
    """Auto-refresh support chat interface every 30 seconds"""
    try:
        session = SyncSessionLocal()
        try:
            ticket = session.query(SupportTicket).filter(SupportTicket.id == ticket_id).first()
            if not ticket or ticket.status in ["resolved", "closed"]:
                return False  # Stop auto-refresh for closed tickets
                
            user = ticket.user
            await show_support_chat_interface(update, context, ticket, user)
            
            # Return True to continue auto-refresh (managed by AutoRefreshManager)
            return ticket.status in ["open", "assigned"]
            
        finally:
            session.close()
    except Exception as e:
        logger.error(f"Error in auto-refresh support chat: {e}")
        return False  # Stop refresh on error


async def user_support_ticket_details(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show detailed view of user's own support ticket"""
    user = update.effective_user
    if not user:
        return

    query = update.callback_query
    if not query or not query.data:
        return

    await safe_answer_callback_query(query, "📋")

    try:
        # Extract ticket ID from callback data
        ticket_id = int(query.data.split(":")[1])
    except (ValueError, IndexError):
        await safe_edit_message_text(query, "❌ Invalid ticket ID")
        return

    session = SyncSessionLocal()
    try:
        # Get user from database
        db_user = session.query(User).filter(User.telegram_id == int(user.id)).first()
        if not db_user:
            await safe_edit_message_text(query, "❌ User not found. Please restart with /start")
            return

        # Get ticket - ensure user owns this ticket
        ticket = session.query(SupportTicket).filter(
            SupportTicket.id == ticket_id,
            SupportTicket.user_id == db_user.id  # Security: user can only view own tickets
        ).first()

        if not ticket:
            await safe_edit_message_text(query, "❌ Ticket not found or access denied")
            return

        # Get all messages for this ticket
        messages = session.query(SupportMessage).filter(
            SupportMessage.ticket_id == ticket.id
        ).order_by(SupportMessage.created_at).all()

        # Build detailed ticket view
        details_text = f"📋 Ticket Details: {ticket.ticket_id}\n\n"
        
        # Status and timing
        status_emoji = {
            "open": "⏳",
            "assigned": "👨‍💼", 
            "resolved": "✅",
            "closed": "🔒"
        }.get(ticket.status, "❓")
        
        details_text += f"{status_emoji} Status: {ticket.status.title()}\n"
        details_text += f"🕐 Created: {ticket.created_at.strftime('%b %d, %H:%M')}\n"
        
        if ticket.assigned_admin:
            admin_name = ticket.assigned_admin.first_name or "Admin"
            details_text += f"👨‍💼 Assigned to: {admin_name}\n"
        else:
            details_text += "⏳ Status: Waiting for admin response\n"
            
        if ticket.resolved_at:
            details_text += f"✅ Resolved: {ticket.resolved_at.strftime('%b %d, %H:%M')}\n"

        # Message history
        details_text += f"\n💬 Messages ({len(messages)}):\n"
        
        if messages:
            for msg in messages:
                sender = "👨‍💼 Admin" if msg.is_admin_reply else "👤 You"
                time_str = msg.created_at.strftime('%H:%M')
                # Truncate long messages
                message_preview = msg.message[:100] + "..." if len(msg.message) > 100 else msg.message
                details_text += f"{sender} ({time_str}):\n{message_preview}\n\n"
        else:
            details_text += "📭 No messages yet\n"

        # Build keyboard based on ticket status
        keyboard_buttons = []
        
        if ticket.status in ["open", "assigned"]:
            keyboard_buttons.append([
                InlineKeyboardButton("💬 Continue Chat", callback_data=f"support_chat_open:{ticket.id}"),
                InlineKeyboardButton("🔒 Close Ticket", callback_data=f"support_close_ticket:{ticket.id}")
            ])
        elif ticket.status == "resolved":
            keyboard_buttons.append([
                InlineKeyboardButton("✅ Accept Resolution", callback_data=f"support_close_ticket:{ticket.id}"),
                InlineKeyboardButton("💬 Reopen Chat", callback_data=f"support_chat_open:{ticket.id}")
            ])
        
        keyboard_buttons.append([
            InlineKeyboardButton("📋 My Tickets", callback_data="view_support_tickets"),
            InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")
        ])

        await safe_edit_message_text(
            query,
            details_text,
            reply_markup=InlineKeyboardMarkup(keyboard_buttons)
        )

    except Exception as e:
        logger.error(f"Error showing ticket details: {e}")
        await safe_edit_message_text(query, "❌ Error loading ticket details. Please try again.")
    finally:
        session.close()


async def user_support_close_ticket(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Allow user to close their own support ticket
    
    Note: This handler manually ends the support conversation by clearing
    conversation state, so buttons in the closure confirmation remain responsive.
    """
    user = update.effective_user
    if not user:
        return ConversationHandler.END

    query = update.callback_query
    if not query or not query.data:
        return ConversationHandler.END

    await safe_answer_callback_query(query, "🔒")

    try:
        # Extract ticket ID from callback data
        ticket_id = int(query.data.split(":")[1])
    except (ValueError, IndexError):
        await safe_edit_message_text(query, "❌ Invalid ticket ID")
        return ConversationHandler.END

    session = SyncSessionLocal()
    try:
        # Get user from database
        db_user = session.query(User).filter(User.telegram_id == int(user.id)).first()
        if not db_user:
            await safe_edit_message_text(query, "❌ User not found. Please restart with /start")
            return ConversationHandler.END

        # Get ticket - ensure user owns this ticket
        ticket = session.query(SupportTicket).filter(
            SupportTicket.id == ticket_id,
            SupportTicket.user_id == db_user.id  # Security: user can only close own tickets
        ).first()

        if not ticket:
            await safe_edit_message_text(query, "❌ Ticket not found or access denied")
            return ConversationHandler.END

        if ticket.status == "closed":
            # Ticket already closed - show message with helpful buttons
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 My Tickets", callback_data="view_support_tickets")],
                [InlineKeyboardButton("🆘 New Support Ticket", callback_data="start_support_chat")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
            ])
            await safe_edit_message_text(
                query, 
                "✅ This ticket is already closed.\n\nYou can create a new ticket if you need further assistance.",
                reply_markup=keyboard
            )
            return ConversationHandler.END

        # Close the ticket
        ticket.status = "closed"
        ticket.resolved_at = datetime.utcnow()
        
        # Add closing message
        closing_message = SupportMessage(
            ticket_id=ticket.id,
            sender_id=db_user.id,
            message="Ticket closed by user",
            is_admin_reply=False
        )
        session.add(closing_message)
        session.commit()

        # OPTIMIZATION: Invalidate cache after ticket closure
        invalidate_support_cache(context.user_data)

        # Remove from active sessions
        if user.id in active_support_sessions:
            del active_support_sessions[user.id]
        _support_session_timestamps.pop(user.id, None)

        # Notify admins about ticket closure via Telegram
        try:
            admin_ids = get_admin_user_ids()
            for admin_id in admin_ids:
                admin_message = "📋 Ticket Closed by User\n\n"
                admin_message += f"🎫 Ticket: {ticket.ticket_id}\n"
                admin_message += f"👤 User: {db_user.first_name or 'Unknown'}\n"
                admin_message += f"🕐 Closed: {datetime.utcnow().strftime('%b %d, %H:%M')}\n"
                
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=admin_message,
                    parse_mode='Markdown'
                )
        except Exception as e:
            logger.error(f"Error notifying admins about ticket closure: {e}")

        # Send email notification to admin about ticket closure
        try:
            from services.email import EmailService
            from config import Config
            
            admin_email = Config.ADMIN_EMAIL
            if admin_email:
                email_service = EmailService()
                
                email_subject = f"🔒 Support Ticket Closed - {ticket.ticket_id}"
                email_body = f"""Support Ticket Closure Notification

Ticket Details:
• Ticket ID: {ticket.ticket_id}
• User: {db_user.first_name or 'Unknown'} (@{db_user.username or 'no_username'})
• Closed: {datetime.utcnow().strftime('%B %d, %Y at %H:%M UTC')}
• Status: Closed by user

The support ticket has been closed by the user. No further action is required unless the user creates a new ticket.

---
LockBay Support System
This is an automated notification."""

                success = email_service.send_email(
                    to_email=admin_email,
                    subject=email_subject,
                    text_content=email_body
                )
                
                if success:
                    logger.info(f"✅ Ticket closure email sent to {admin_email}")
                else:
                    logger.error(f"❌ Failed to send ticket closure email to {admin_email}")
                    
        except Exception as e:
            logger.error(f"Error sending admin email for ticket closure: {e}")

        # Show confirmation to user  
        confirmation_text = "✅ Ticket Closed Successfully\n\n"
        confirmation_text += f"🎫 Ticket: {ticket.ticket_id}\n"
        confirmation_text += f"🕐 Closed: {datetime.utcnow().strftime('%b %d, %H:%M')}\n\n"
        confirmation_text += "Thank you for using our support system!\n"
        confirmation_text += "You can always create a new ticket if you need further assistance."

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 My Tickets", callback_data="view_support_tickets")],
            [InlineKeyboardButton("🆘 New Support Ticket", callback_data="start_support_chat")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
        ])

        await safe_edit_message_text(
            query,
            confirmation_text,
            reply_markup=keyboard
        )

        logger.info(f"✅ User {user.id} closed support ticket {ticket.ticket_id}")
        
        # CRITICAL: Manually end the conversation so buttons remain responsive
        # Clear conversation state for this user
        if 'conversation_states' in context.user_data:
            context.user_data['conversation_states'].pop('support_chat', None)
        
        return ConversationHandler.END

    except Exception as e:
        logger.error(f"Error closing ticket: {e}")
        await safe_edit_message_text(query, "❌ Error closing ticket. Please try again or contact admin.")
        session.close()
        return ConversationHandler.END


# Conversation handler for support chat
def create_support_conversation_handler():
    """Create the support chat conversation handler
    
    The close ticket handler is included as a fallback so it can:
    1. Be called from any conversation state
    2. Return ConversationHandler.END to properly exit the conversation
    3. Show confirmation with responsive buttons (since conversation ends cleanly)
    """
    from telegram.ext import MessageHandler, CallbackQueryHandler, filters
    from handlers.missing_handlers import handle_main_menu_callback
    
    async def exit_to_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Exit support chat and show main menu"""
        await handle_main_menu_callback(update, context)
        return ConversationHandler.END
    
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(start_support_chat, pattern="^start_support_chat$"),
            CallbackQueryHandler(open_support_chat, pattern="^support_chat_open:")
        ],
        states={
            SUPPORT_CHAT_VIEW: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_support_message_input),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(user_support_close_ticket, pattern="^support_close_ticket:"),
            CallbackQueryHandler(exit_to_main_menu, pattern="^main_menu$"),
        ],
        name="support_chat",
        persistent=False,
        per_message=False,
        per_user=True,
        per_chat=True,
        # TIMEOUT: Auto-cleanup abandoned support chats after 30 minutes
        conversation_timeout=1800  # 30 minutes for support conversations
    )