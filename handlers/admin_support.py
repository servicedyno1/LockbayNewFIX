"""
Admin Support Dashboard - Phase 2 Implementation
Provides admins with ticket management, assignment, and analytics
"""

import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from sqlalchemy import desc

from database import SessionLocal
from models import User, SupportTicket, SupportMessage
from utils.callback_utils import safe_edit_message_text, safe_answer_callback_query
from utils.admin_security import is_admin_secure, is_admin_silent
from utils.comprehensive_audit_logger import (
    ComprehensiveAuditLogger
)
from utils.handler_decorators import audit_admin_handler

logger = logging.getLogger(__name__)

# Initialize communication audit logger
communication_audit = ComprehensiveAuditLogger("communication")


@audit_admin_handler("admin_support_dashboard")
async def admin_support_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin support dashboard with ticket overview and analytics"""
    user = update.effective_user
    if not user or not is_admin_secure(user.id):
        query = update.callback_query
        if query:
            await safe_answer_callback_query(query, "❌ Access denied")
        return

    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "📊")

    session = SessionLocal()
    try:
        # Get current admin user
        admin_user = session.query(User).filter(User.telegram_id == str(user.id)).first()
        if not admin_user:
            if query:
                await safe_edit_message_text(query, "❌ Admin user not found")
            return

        # === SUPPORT ANALYTICS ===
        from datetime import timezone
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        
        # Ticket counts
        session.query(SupportTicket).count()
        open_tickets = session.query(SupportTicket).filter(
            SupportTicket.status.in_(["open", "assigned"])
        ).count()
        
        assigned_to_me = session.query(SupportTicket).filter(
            SupportTicket.assigned_to == admin_user.id,
            SupportTicket.status.in_(["open", "assigned"])
        ).count()
        
        today_tickets = session.query(SupportTicket).filter(
            SupportTicket.created_at >= today_start
        ).count()
        
        resolved_today = session.query(SupportTicket).filter(
            SupportTicket.resolved_at >= today_start
        ).count()

        # Recent unassigned tickets (priority) - with user data to prevent N+1
        from sqlalchemy.orm import joinedload
        unassigned_tickets = session.query(SupportTicket).filter(
            SupportTicket.assigned_to.is_(None),
            SupportTicket.status == "open"
        ).options(joinedload(SupportTicket.user)).order_by(desc(SupportTicket.created_at)).limit(5).all()

        # Recently assigned to this admin - with user data to prevent N+1
        my_tickets = session.query(SupportTicket).filter(
            SupportTicket.assigned_to == admin_user.id,
            SupportTicket.status.in_(["open", "assigned"])
        ).options(joinedload(SupportTicket.user)).order_by(desc(SupportTicket.updated_at)).limit(5).all()

        # Build dashboard text
        dashboard_text = f"""📊 **Support Dashboard**

**📈 Today's Statistics:**
• 🎫 New tickets: {today_tickets}
• ✅ Resolved: {resolved_today}
• ⏳ Open: {open_tickets}
• 🎯 Assigned to me: {assigned_to_me}

**🚨 Unassigned Tickets ({len(unassigned_tickets)}):**"""

        if unassigned_tickets:
            for ticket in unassigned_tickets[:3]:
                time_ago = get_time_ago(ticket.created_at)
                dashboard_text += f"\n• {ticket.ticket_id} • {time_ago} ago"
        else:
            dashboard_text += "\n• ✅ All tickets assigned!"

        dashboard_text += f"\n\n**🎯 My Active Tickets ({len(my_tickets)}):**"
        
        if my_tickets:
            for ticket in my_tickets[:3]:
                time_ago = get_time_ago(ticket.updated_at)
                dashboard_text += f"\n• {ticket.ticket_id} • Updated {time_ago} ago"
        else:
            dashboard_text += "\n• 📭 No tickets assigned to you"

        # Dashboard buttons
        keyboard_buttons = []
        
        # Quick actions row
        if unassigned_tickets:
            keyboard_buttons.append([
                InlineKeyboardButton("🚨 View Unassigned", callback_data="admin_unassigned_tickets"),
                InlineKeyboardButton("🎯 My Tickets", callback_data="admin_my_tickets")
            ])
        else:
            keyboard_buttons.append([
                InlineKeyboardButton("🎯 My Tickets", callback_data="admin_my_tickets"),
                InlineKeyboardButton("📋 All Tickets", callback_data="admin_all_tickets")
            ])

        # Management row
        keyboard_buttons.append([
            InlineKeyboardButton("📊 Analytics", callback_data="admin_support_analytics"),
            InlineKeyboardButton("⚙️ Settings", callback_data="admin_support_settings")
        ])

        # Navigation
        keyboard_buttons.append([
            InlineKeyboardButton("🏠 Admin Menu", callback_data="admin_menu")
        ])

        keyboard = InlineKeyboardMarkup(keyboard_buttons)

        if query:
            await safe_edit_message_text(query, dashboard_text, reply_markup=keyboard, parse_mode="Markdown")
        else:
            await update.message.reply_text(dashboard_text, reply_markup=keyboard, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error showing admin support dashboard: {e}")
        if query:
            await safe_edit_message_text(query, "❌ Error loading dashboard. Please try again.")
    finally:
        session.close()


async def admin_assign_ticket(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Assign support ticket to admin"""
    user = update.effective_user
    if not user or not is_admin_secure(user.id):
        query = update.callback_query
        if query:
            await safe_answer_callback_query(query, "❌ Access denied")
        return

    query = update.callback_query
    if not query or not query.data:
        return

    try:
        # Extract ticket ID
        ticket_id = int(query.data.split(":")[1])
        
        session = SessionLocal()
        try:
            # Get ticket and admin user
            ticket = session.query(SupportTicket).filter(SupportTicket.id == ticket_id).first()
            admin_user = session.query(User).filter(User.telegram_id == str(user.id)).first()
            
            if not ticket or not admin_user:
                await safe_answer_callback_query(query, "❌ Ticket or admin not found")
                return

            # Assign ticket
            ticket.assigned_to = admin_user.id
            ticket.status = "assigned"
            session.commit()

            # OPTIMIZATION: Invalidate support cache after admin assignment
            from utils.support_prefetch import invalidate_support_cache
            invalidate_support_cache(context.user_data)

            await safe_answer_callback_query(query, f"✅ Assigned {ticket.ticket_id} to you")

            # Notify user about assignment
            try:
                user_ticket = ticket.user
                if user_ticket:
                    notification_text = f"""🎯 **Support Update**

📋 **Ticket:** {ticket.ticket_id}
👨‍💼 **Assigned to:** {admin_user.first_name or 'Admin'}

Your support request has been assigned to an admin. You'll receive faster responses now!"""

                    await context.bot.send_message(
                        chat_id=user_ticket.telegram_id,
                        text=notification_text,
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("💬 Continue Chat", callback_data=f"support_chat_open:{ticket.id}")]
                        ]),
                        parse_mode="Markdown"
                    )
            except Exception as e:
                logger.error(f"Failed to notify user about assignment: {e}")

            # Update the message to show assignment
            await admin_support_chat(update, context)

        finally:
            session.close()

    except (ValueError, IndexError):
        logger.error(f"Invalid ticket assignment callback: {query.data}")
        await safe_answer_callback_query(query, "❌ Invalid request")


async def admin_support_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin view of support chat"""
    # CRITICAL DEBUG: Log that this handler is being called
    user_id = update.effective_user.id if update.effective_user else "unknown"
    logger.warning(f"🚀 ADMIN TICKET DETAILS HANDLER CALLED by user {user_id}")
    
    user = update.effective_user
    if not user or not is_admin_secure(user.id):
        logger.warning(f"❌ Admin access denied for user {user_id}")
        query = update.callback_query
        if query:
            await safe_answer_callback_query(query, "❌ Access denied")
        return

    query = update.callback_query
    if not query or not query.data:
        logger.warning(f"❌ No callback query or data for user {user_id}")
        return

    try:
        # Extract ticket ID
        ticket_id = int(query.data.split(":")[1])
        
        session = SessionLocal()
        try:
            # Get ticket with user data (prevents N+1 query)
            from sqlalchemy.orm import joinedload
            
            ticket = (session.query(SupportTicket)
                     .filter(SupportTicket.id == ticket_id)
                     .options(joinedload(SupportTicket.user), joinedload(SupportTicket.assigned_admin))
                     .first())
            if not ticket:
                await safe_answer_callback_query(query, "❌ Ticket not found")
                return

            await safe_answer_callback_query(query, "💬")

            # Get recent messages
            messages = session.query(SupportMessage).filter(
                SupportMessage.ticket_id == ticket.id
            ).order_by(desc(SupportMessage.created_at)).limit(15).all()
            messages.reverse()

            # Build admin chat view
            chat_text = "🎫 **Admin Support Chat**\n\n"
            chat_text += f"📋 **Ticket:** {ticket.ticket_id}\n"
            chat_text += f"👤 **User:** {ticket.user.first_name or 'User'} (@{ticket.user.username or 'no username'})\n"
            chat_text += f"📧 **Email:** {ticket.user.email}\n"
            chat_text += f"📊 **Status:** {ticket.status.title()}\n"
            
            if ticket.assigned_admin:
                chat_text += f"🎯 **Assigned:** {ticket.assigned_admin.first_name or 'Admin'}\n"
            else:
                chat_text += "🎯 **Assigned:** Unassigned\n"
                
            chat_text += f"🕐 **Created:** {ticket.created_at.strftime('%b %d, %H:%M')}\n\n"

            if messages:
                chat_text += "**💬 Recent Messages:**\n"
                for msg in messages:
                    sender_name = "👨‍💼 Admin" if msg.is_admin_reply else f"👤 {ticket.user.first_name or 'User'}"
                    time_str = msg.created_at.strftime('%H:%M')
                    
                    message_text = msg.message
                    if len(message_text) > 150:
                        message_text = message_text[:150] + "..."
                        
                    chat_text += f"{sender_name} ({time_str}):\n{message_text}\n\n"
            else:
                chat_text += "💬 **No messages yet.**\n\n"
            
            # Add clear instruction for replying
            chat_text += "✍️ **To reply:** Just type your message below\n\n"

            # Admin action buttons
            keyboard_buttons = []
            
            if not ticket.assigned_to:
                keyboard_buttons.append([
                    InlineKeyboardButton("🎯 Assign to Me", callback_data=f"admin_assign_ticket:{ticket.id}"),
                ])
            
            # Status management
            if ticket.status in ["open", "assigned"]:
                keyboard_buttons.append([
                    InlineKeyboardButton("✅ Resolve Ticket", callback_data=f"admin_resolve_ticket:{ticket.id}"),
                    InlineKeyboardButton("🔒 Close Ticket", callback_data=f"admin_close_ticket:{ticket.id}")
                ])

            # Navigation
            keyboard_buttons.append([
                InlineKeyboardButton("📊 Dashboard", callback_data="admin_support_dashboard")
            ])

            keyboard = InlineKeyboardMarkup(keyboard_buttons)

            await safe_edit_message_text(query, chat_text, reply_markup=keyboard, parse_mode="Markdown")

        finally:
            session.close()

    except (ValueError, IndexError):
        logger.error(f"Invalid support chat callback: {query.data}")
        await safe_answer_callback_query(query, "❌ Invalid request")


async def admin_unassigned_tickets(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show all unassigned support tickets"""
    user = update.effective_user
    if not user or not is_admin_secure(user.id):
        query = update.callback_query
        if query:
            await safe_answer_callback_query(query, "❌ Access denied")
        return

    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "🚨")

    session = SessionLocal()
    try:
        # Get unassigned tickets
        unassigned = session.query(SupportTicket).filter(
            SupportTicket.assigned_to.is_(None),
            SupportTicket.status == "open"
        ).order_by(desc(SupportTicket.created_at)).all()

        if not unassigned:
            text = """🚨 **Unassigned Tickets**

✅ No unassigned tickets!

All support tickets have been assigned to admins."""
            
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("📊 Dashboard", callback_data="admin_support_dashboard")]
            ])
        else:
            text = f"🚨 **Unassigned Tickets** ({len(unassigned)} total)\n\n"
            
            keyboard_buttons = []
            
            for ticket in unassigned:
                time_ago = get_time_ago(ticket.created_at)
                user_name = ticket.user.first_name or "User"
                
                text += f"📋 **{ticket.ticket_id}** • {time_ago} ago\n"
                text += f"👤 {user_name} • {ticket.priority.title()} priority\n"
                text += f"📝 {ticket.subject or 'Live Chat Support'}\n\n"
                
                # Buttons for each ticket
                keyboard_buttons.append([
                    InlineKeyboardButton(
                        f"🎯 Assign {ticket.ticket_id}",
                        callback_data=f"admin_assign_ticket:{ticket.id}"
                    ),
                    InlineKeyboardButton(
                        f"💬 View {ticket.ticket_id}",
                        callback_data=f"admin_support_chat:{ticket.id}"
                    )
                ])
            
            # Navigation
            keyboard_buttons.append([
                InlineKeyboardButton("📊 Dashboard", callback_data="admin_support_dashboard")
            ])
            
            keyboard = InlineKeyboardMarkup(keyboard_buttons)

        if query:
            await safe_edit_message_text(query, text, reply_markup=keyboard, parse_mode="Markdown")
        else:
            await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error showing unassigned tickets: {e}")
        if query:
            await safe_edit_message_text(query, "❌ Error loading tickets. Please try again.")
    finally:
        session.close()


async def admin_my_tickets(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show tickets assigned to current admin"""
    user = update.effective_user
    if not user or not is_admin_secure(user.id):
        query = update.callback_query
        if query:
            await safe_answer_callback_query(query, "❌ Access denied")
        return

    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "🎯")

    session = SessionLocal()
    try:
        # Get admin user
        admin_user = session.query(User).filter(User.telegram_id == str(user.id)).first()
        if not admin_user:
            if query:
                await safe_edit_message_text(query, "❌ Admin user not found")
            return

        # Get tickets assigned to this admin
        my_tickets = session.query(SupportTicket).filter(
            SupportTicket.assigned_to == admin_user.id
        ).order_by(desc(SupportTicket.updated_at)).all()

        if not my_tickets:
            text = """🎯 **My Assigned Tickets**

📭 No tickets assigned to you.

Check the unassigned tickets to help users waiting for support!"""
            
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🚨 Unassigned Tickets", callback_data="admin_unassigned_tickets")],
                [InlineKeyboardButton("📊 Dashboard", callback_data="admin_support_dashboard")]
            ])
        else:
            text = f"🎯 **My Assigned Tickets** ({len(my_tickets)} total)\n\n"
            
            keyboard_buttons = []
            
            for ticket in my_tickets:
                status_emoji = {
                    "assigned": "🟡",
                    "resolved": "✅", 
                    "closed": "⚫"
                }.get(ticket.status, "🟢")
                
                time_ago = get_time_ago(ticket.updated_at)
                user_name = ticket.user.first_name or "User"
                
                text += f"{status_emoji} **{ticket.ticket_id}** • {ticket.status.title()}\n"
                text += f"👤 {user_name} • Updated {time_ago} ago\n"
                text += f"📝 {ticket.subject or 'Live Chat Support'}\n\n"
                
                # Button for each ticket
                keyboard_buttons.append([
                    InlineKeyboardButton(
                        f"💬 {ticket.ticket_id} • {ticket.status.title()}",
                        callback_data=f"admin_support_chat:{ticket.id}"
                    )
                ])
            
            # Navigation
            keyboard_buttons.append([
                InlineKeyboardButton("📊 Dashboard", callback_data="admin_support_dashboard")
            ])
            
            keyboard = InlineKeyboardMarkup(keyboard_buttons)

        if query:
            await safe_edit_message_text(query, text, reply_markup=keyboard, parse_mode="Markdown")
        else:
            await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error showing admin tickets: {e}")
        if query:
            await safe_edit_message_text(query, "❌ Error loading tickets. Please try again.")
    finally:
        session.close()


# Admin reply state tracking
admin_reply_states = {}

async def admin_reply_ticket(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle admin reply to support ticket"""
    user = update.effective_user
    if not user or not is_admin_secure(user.id):
        query = update.callback_query
        if query:
            await safe_answer_callback_query(query, "❌ Access denied")
        return

    query = update.callback_query
    if not query or not query.data:
        return

    try:
        # Extract ticket ID
        ticket_id = int(query.data.split(":")[1])
        
        session = SessionLocal()
        try:
            # Get ticket
            ticket = session.query(SupportTicket).filter(SupportTicket.id == ticket_id).first()
            if not ticket:
                await safe_answer_callback_query(query, "❌ Ticket not found")
                return

            # Set admin in reply mode
            admin_reply_states[user.id] = {
                'ticket_id': ticket_id,
                'user_id': ticket.user.telegram_id
            }

            await safe_answer_callback_query(query, "✍️ Type your reply...")

            # Show reply interface
            reply_text = f"""💬 **Reply to Support Ticket**

📋 **Ticket:** {ticket.ticket_id}
👤 **User:** {ticket.user.first_name or 'User'}

✍️ **Type your message below:**
Your next message will be sent to the user."""

            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancel Reply", callback_data=f"admin_support_chat:{ticket.id}")]
            ])

            await safe_edit_message_text(query, reply_text, reply_markup=keyboard, parse_mode="Markdown")

        finally:
            session.close()

    except (ValueError, IndexError):
        logger.error(f"Invalid admin reply callback: {query.data}")
        await safe_answer_callback_query(query, "❌ Invalid request")


async def admin_resolve_ticket(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Resolve support ticket"""
    user = update.effective_user
    if not user or not is_admin_secure(user.id):
        query = update.callback_query
        if query:
            await safe_answer_callback_query(query, "❌ Access denied")
        return

    query = update.callback_query
    if not query or not query.data:
        return

    try:
        # Extract ticket ID
        ticket_id = int(query.data.split(":")[1])
        
        session = SessionLocal()
        try:
            # Get ticket
            ticket = session.query(SupportTicket).filter(SupportTicket.id == ticket_id).first()
            if not ticket:
                await safe_answer_callback_query(query, "❌ Ticket not found")
                return

            # Update ticket status
            from datetime import timezone
            ticket.status = "resolved"
            ticket.resolved_at = datetime.now(timezone.utc)
            if not ticket.assigned_to:
                # Assign to resolving admin if not already assigned
                admin_user = session.query(User).filter(User.telegram_id == str(user.id)).first()
                if admin_user:
                    ticket.assigned_to = admin_user.id
            
            session.commit()

            await safe_answer_callback_query(query, "✅ Ticket resolved")

            # Send resolution message to user
            try:
                await context.bot.send_message(
                    chat_id=int(ticket.user.telegram_id),
                    text=f"✅ **Support Ticket Resolved**\n\nYour support ticket {ticket.ticket_id} has been resolved.\n\nThank you for using our support system!",
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Failed to notify user about ticket resolution: {e}")

            # Refresh the ticket view
            await admin_support_chat(update, context)

        finally:
            session.close()

    except (ValueError, IndexError):
        logger.error(f"Invalid admin resolve ticket callback: {query.data}")
        await safe_answer_callback_query(query, "❌ Invalid request")


async def admin_close_ticket(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Close support ticket"""
    # CRITICAL DEBUG: Log that this handler is being called
    user_id = update.effective_user.id if update.effective_user else "unknown"
    logger.warning(f"🚀 ADMIN CLOSE TICKET HANDLER CALLED by user {user_id}")
    
    user = update.effective_user
    if not user or not is_admin_secure(user.id):
        logger.warning(f"❌ Admin close ticket access denied for user {user_id}")
        query = update.callback_query
        if query:
            await safe_answer_callback_query(query, "❌ Access denied")
        return

    query = update.callback_query
    if not query or not query.data:
        logger.warning(f"❌ No callback query or data for close ticket user {user_id}")
        return

    try:
        # Extract ticket ID
        ticket_id = int(query.data.split(":")[1])
        
        session = SessionLocal()
        try:
            # Get ticket
            ticket = session.query(SupportTicket).filter(SupportTicket.id == ticket_id).first()
            if not ticket:
                await safe_answer_callback_query(query, "❌ Ticket not found")
                return

            # Update ticket status
            from datetime import timezone
            ticket.status = "closed"
            ticket.closed_at = datetime.now(timezone.utc)
            if not ticket.assigned_to:
                # Assign to closing admin if not already assigned
                admin_user = session.query(User).filter(User.telegram_id == str(user.id)).first()
                if admin_user:
                    ticket.assigned_to = admin_user.id
            
            session.commit()

            await safe_answer_callback_query(query, "🔒 Ticket closed")

            # Send closure message to user
            try:
                await context.bot.send_message(
                    chat_id=int(ticket.user.telegram_id),
                    text=f"🔒 **Support Ticket Closed**\n\nYour support ticket {ticket.ticket_id} has been closed.\n\nIf you need further assistance, feel free to create a new support ticket.",
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Failed to notify user about ticket closure: {e}")

            # Return to dashboard instead of refreshing current view
            await admin_support_dashboard(update, context)

        finally:
            session.close()

    except (ValueError, IndexError):
        logger.error(f"Invalid admin close ticket callback: {query.data}")
        await safe_answer_callback_query(query, "❌ Invalid request")


async def handle_admin_reply_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Process admin reply message and send to user"""
    user = update.effective_user
    # CRITICAL FIX: Use is_admin_silent() instead of is_admin_secure() to avoid false security alerts
    # This is just routing logic, not a security check, so we don't want to trigger alerts for normal users
    if not user or not is_admin_silent(user.id):
        return False

    # Check if admin is in explicit reply mode first
    if user.id in admin_reply_states:
        # Handle explicit reply mode (when admin clicked "Reply to User" button)
        return await _process_admin_reply_in_mode(update, context, admin_reply_states[user.id])
    
    # AUTO-DETECT: Check if admin is trying to reply to recent support ticket
    admin_message = update.message.text if update.message else None
    if not admin_message:
        return False
    
    # Look for active support tickets that might need a reply
    session = SessionLocal()
    try:
        # Get most recent open ticket that has messages from users
        recent_ticket = session.query(SupportTicket).filter(
            SupportTicket.status.in_(["open", "assigned"])
        ).order_by(desc(SupportTicket.updated_at)).first()
        
        if recent_ticket:
            # Auto-set reply mode for this ticket
            admin_reply_data = {
                'ticket_id': recent_ticket.id,
                'user_id': recent_ticket.user.telegram_id
            }
            logger.info(f"🔄 AUTO-REPLY: Admin {user.id} auto-replying to ticket {recent_ticket.ticket_id}")
            return await _process_admin_reply_in_mode(update, context, admin_reply_data)
        
    except Exception as e:
        logger.error(f"Error in auto-reply detection: {e}")
    finally:
        session.close()
    
    return False  # Not handled by this function


async def _process_admin_reply_in_mode(update: Update, context: ContextTypes.DEFAULT_TYPE, reply_data: dict) -> bool:
    """Process admin reply when in reply mode (explicit or auto-detected)"""
    user = update.effective_user
    admin_message = update.message.text

    if not admin_message:
        return False

    session = SessionLocal()
    try:
        # Get ticket and admin user
        ticket = session.query(SupportTicket).filter(SupportTicket.id == reply_data['ticket_id']).first()
        admin_user = session.query(User).filter(User.telegram_id == str(user.id)).first()
        
        if not ticket or not admin_user:
            await update.message.reply_text("❌ Error: Ticket or admin not found")
            del admin_reply_states[user.id]
            return True

        # Save admin message to database
        admin_support_message = SupportMessage(
            ticket_id=ticket.id,
            sender_id=admin_user.id,
            message=admin_message,
            is_admin_reply=True
        )
        session.add(admin_support_message)
        
        # Ticket's updated_at will auto-update via onupdate
        session.commit()

        # Send message to user
        try:
            user_notification = f"""💬 **Support Reply**

📋 **Ticket:** {ticket.ticket_id}
👨‍💼 **Admin:** {admin_user.first_name or 'Support Team'}

{admin_message}"""

            await context.bot.send_message(
                chat_id=reply_data['user_id'],
                text=user_notification,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("💬 Continue Chat", callback_data=f"support_chat_open:{ticket.id}")]
                ]),
                parse_mode="Markdown"
            )

            # Confirm to admin
            await update.message.reply_text(
                f"✅ Reply sent to {ticket.user.first_name or 'user'}!\n\n📋 Ticket: {ticket.ticket_id}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("💬 View Ticket", callback_data=f"admin_support_chat:{ticket.id}")]
                ])
            )

        except Exception as e:
            logger.error(f"Failed to send admin reply to user: {e}")
            await update.message.reply_text("❌ Failed to send reply to user. Please try again.")

    except Exception as e:
        logger.error(f"Error processing admin reply: {e}")
        await update.message.reply_text("❌ Error processing reply. Please try again.")
    finally:
        session.close()
        # Clear admin reply state
        if user.id in admin_reply_states:
            del admin_reply_states[user.id]
        
    return True  # Message handled


def get_time_ago(timestamp: datetime) -> str:
    """Get human-readable time ago string - handles both timezone-aware and naive datetimes"""
    from datetime import timezone
    
    # Ensure we're working with timezone-aware UTC datetimes
    if timestamp.tzinfo is None:
        # If input is naive, assume it's UTC
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    else:
        # Convert to UTC if it has a different timezone
        timestamp = timestamp.astimezone(timezone.utc)
    
    # Get current time in UTC (timezone-aware)
    now = datetime.now(timezone.utc)
    
    # Now we can safely subtract
    diff = now - timestamp
    
    if diff.days > 0:
        return f"{diff.days}d"
    elif diff.seconds > 3600:
        hours = diff.seconds // 3600
        return f"{hours}h"
    elif diff.seconds > 60:
        minutes = diff.seconds // 60
        return f"{minutes}m"
    else:
        return "now"