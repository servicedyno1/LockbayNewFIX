"""
Advanced Notification Management System  
Admin controls for notification preferences, delivery monitoring, and communication oversight
"""

import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from sqlalchemy import desc, and_

from database import SessionLocal
from models import User, NotificationPreference, NotificationActivity
from utils.admin_security import is_admin_secure
from utils.callback_utils import safe_edit_message_text, safe_answer_callback_query


# Compatibility shim for testing infrastructure
def is_admin_authorized(user_id: int) -> bool:
    """Admin authorization compatibility shim - delegates to existing security function"""
    return is_admin_secure(user_id)

logger = logging.getLogger(__name__)

# Conversation states
NOTIFICATION_TYPE, NOTIFICATION_CONFIG, NOTIFICATION_TEST = range(3)



async def handle_admin_notifications(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Main notification management dashboard"""
    user = update.effective_user
    if not user or not is_admin_secure(user.id):
        if update.callback_query:
            await safe_answer_callback_query(update.callback_query, "❌ Access denied", show_alert=True)
        elif update.message:
            await update.message.reply_text("❌ Access denied. Admin access required.")
        return ConversationHandler.END

    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "🔔")

    try:
        session = SessionLocal()
        try:
            now = datetime.utcnow()
            today = now.replace(hour=0, minute=0, second=0, microsecond=0)
            
            # === NOTIFICATION STATISTICS ===
            total_users = session.query(User).count()
            
            # Users with notification preferences
            users_with_prefs = session.query(NotificationPreference).count()
            
            # Recent notification logs
            notifications_today = session.query(NotificationActivity).filter(
                NotificationActivity.created_at >= today
            ).count()
            
            # Success rates
            successful_today = session.query(NotificationActivity).filter(
                NotificationActivity.created_at >= today,
                NotificationActivity.delivery_status == 'delivered'
            ).count()
            
            success_rate = (successful_today / max(notifications_today, 1)) * 100
            
            # Notification types breakdown
            email_notifications = session.query(NotificationActivity).filter(
                NotificationActivity.created_at >= today,
                NotificationActivity.channel_type == 'email'
            ).count()
            
            telegram_notifications = session.query(NotificationActivity).filter(
                NotificationActivity.created_at >= today,
                NotificationActivity.channel_type == 'telegram'
            ).count()
            
            message = f"""🔔 **Notification Management Center**

📊 **Today's Activity**
• Total Sent: {notifications_today:,}
• Success Rate: {success_rate:.1f}%
• Email: {email_notifications} • Telegram: {telegram_notifications}

👥 **User Preferences**
• Total Users: {total_users:,}
• Configured Preferences: {users_with_prefs:,}
• Coverage: {(users_with_prefs/max(total_users, 1)*100):.1f}%

🎛️ **Notification Channels**"""
            
            # Channel status
            import os
            brevo_configured = bool(os.getenv('BREVO_API_KEY'))
            telegram_configured = True  # Always configured
            
            message += f"""
• 📧 Email (Brevo): {'✅ Active' if brevo_configured else '❌ Inactive'}
• 💬 Telegram: {'✅ Active' if telegram_configured else '❌ Inactive'}"""
            
            message += f"\n\n⏰ Updated: {now.strftime('%H:%M UTC')}"
            
        finally:
            session.close()
        
        keyboard = [
            [
                InlineKeyboardButton("📤 Send Broadcast", callback_data="admin_compose_broadcast"),
            ],
            [
                InlineKeyboardButton("👥 User Preferences", callback_data="admin_notif_preferences"),
                InlineKeyboardButton("📊 Delivery Stats", callback_data="admin_notif_stats"),
            ],
            [
                InlineKeyboardButton("🔧 Channel Config", callback_data="admin_notif_channels"),
                InlineKeyboardButton("🧪 Test Notifications", callback_data="admin_notif_test"),
            ],
            [
                InlineKeyboardButton("📋 Notification Log", callback_data="admin_notif_log"),
                InlineKeyboardButton("⚙️ Templates", callback_data="admin_notif_templates"),
            ],
            [
                InlineKeyboardButton("🏠 Admin", callback_data="admin_main")
            ]
        ]
        
        if query:
            await safe_edit_message_text(
                query,
                message,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        elif update.message:
            await update.message.reply_text(
                message,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        
    except Exception as e:
        logger.error(f"Admin notifications dashboard failed: {e}")
        if query:
            await safe_answer_callback_query(query, f"❌ Notifications dashboard failed: {str(e)}", show_alert=True)
        elif update.message:
            await update.message.reply_text(f"❌ Notifications dashboard failed: {str(e)}")
        return ConversationHandler.END

    return ConversationHandler.END


async def handle_admin_notif_preferences(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User notification preferences management"""
    user = update.effective_user
    if not user or not is_admin_secure(user.id):
        if update.callback_query:
            await safe_answer_callback_query(update.callback_query, "❌ Access denied", show_alert=True)
        return ConversationHandler.END

    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "👥")

    try:
        session = SessionLocal()
        try:
            # Preference statistics
            total_users = session.query(User).count()
            
            # Users by notification preference
            email_enabled = session.query(NotificationPreference).filter(
                NotificationPreference.email_enabled == True
            ).count()
            
            telegram_enabled = session.query(NotificationPreference).filter(
                NotificationPreference.telegram_enabled == True
            ).count()
            
            both_enabled = session.query(NotificationPreference).filter(
                and_(NotificationPreference.email_enabled == True,
                     NotificationPreference.telegram_enabled == True)
            ).count()
            
            # Recent preference changes
            recent_changes = session.query(NotificationPreference).order_by(
                desc(NotificationPreference.updated_at)
            ).limit(5).all()
            
            message = f"""👥 **User Notification Preferences**

📊 **Preference Distribution**
• Total Users: {total_users:,}
• Email Enabled: {email_enabled:,} ({(email_enabled/max(total_users,1)*100):.1f}%)
• Telegram Enabled: {telegram_enabled:,} ({(telegram_enabled/max(total_users,1)*100):.1f}%)
• Both Channels: {both_enabled:,}

🔄 **Recent Changes**"""
            
            if recent_changes:
                for pref in recent_changes:
                    user_obj = session.query(User).filter(User.id == pref.user_id).first()
                    email_status = "✅" if bool(pref.email_enabled) else "❌"
                    telegram_status = "✅" if bool(pref.telegram_enabled) else "❌"
                    
                    message += f"""
{user_obj.first_name if user_obj else 'Unknown'}
   📧 {email_status} • 💬 {telegram_status}"""
            else:
                message += "\n📝 No recent preference changes"
            
            # Notification type preferences
            message += "\n\n📋 **Notification Types**"
            escrow_notifs = session.query(NotificationPreference).filter(
                NotificationPreference.escrow_updates == True
            ).count()
            payment_notifs = session.query(NotificationPreference).filter(
                NotificationPreference.payment_notifications == True
            ).count()
            
            message += f"\n• Escrow Updates: {escrow_notifs:,}"
            message += f"\n• Payment Alerts: {payment_notifs:,}"
            
        finally:
            session.close()
        
        keyboard = [
            [
                InlineKeyboardButton("📊 Detailed Stats", callback_data="admin_notif_pref_stats"),
                InlineKeyboardButton("🔧 Bulk Config", callback_data="admin_notif_pref_bulk"),
            ],
            [
                InlineKeyboardButton("📧 Email Settings", callback_data="admin_notif_pref_email"),
                InlineKeyboardButton("💬 Telegram Settings", callback_data="admin_notif_pref_telegram"),
            ],
            [
                InlineKeyboardButton("🔔 Notifications", callback_data="admin_notifications"),
                InlineKeyboardButton("🏠 Admin", callback_data="admin_main"),
            ]
        ]
        
        if query:
            await safe_edit_message_text(
                query,
                message,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        
    except Exception as e:
        logger.error(f"Notification preferences failed: {e}")
        if query:
            await safe_answer_callback_query(query, f"❌ Preferences management failed: {str(e)}", show_alert=True)
        return ConversationHandler.END

    return ConversationHandler.END


async def handle_admin_notif_test(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Test notification system"""
    user = update.effective_user
    if not user or not is_admin_secure(user.id):
        if update.callback_query:
            await safe_answer_callback_query(update.callback_query, "❌ Access denied", show_alert=True)
        return ConversationHandler.END

    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "🧪")

    try:
        message = """🧪 **Notification System Testing**

🎯 **Available Tests**
• Test email delivery to admin
• Test Telegram message formatting
• Test notification templates
• Verify delivery channels

⚠️ **Test Options**"""
        
        keyboard = [
            [
                InlineKeyboardButton("📧 Test Email", callback_data="admin_notif_test_email"),
                InlineKeyboardButton("💬 Test Telegram", callback_data="admin_notif_test_telegram"),
            ],
            [
                InlineKeyboardButton("📋 Test Template", callback_data="admin_notif_test_template"),
                InlineKeyboardButton("🔄 Test All Systems", callback_data="admin_notif_test_all"),
            ],
            [
                InlineKeyboardButton("🔔 Notifications", callback_data="admin_notifications"),
                InlineKeyboardButton("🏠 Admin", callback_data="admin_main"),
            ]
        ]
        
        if query:
            await safe_edit_message_text(
                query,
                message,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        
    except Exception as e:
        logger.error(f"Notification testing failed: {e}")
        if query:
            await safe_answer_callback_query(query, f"❌ Testing interface failed: {str(e)}", show_alert=True)
        return ConversationHandler.END

    return ConversationHandler.END


async def handle_compose_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle broadcast composition"""
    user = update.effective_user
    if not user or not is_admin_secure(user.id):
        if update.callback_query:
            await safe_answer_callback_query(update.callback_query, "❌ Access denied", show_alert=True)
        return ConversationHandler.END

    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "📤")
        
    try:
        # Store that admin is composing a broadcast
        from handlers.admin_broadcast_direct import set_admin_broadcast_state
        await set_admin_broadcast_state(user.id, "composing", None, context)
        
        message = """📤 **Send Broadcast Message**

Please send your broadcast message now. You can send:

📝 **Text Message** - Type your message
📷 **Photo** - Send a photo with optional caption
🎬 **Video** - Send a video with optional caption  
📄 **Document** - Send a file/document
🎵 **Audio** - Send audio/voice message

The message will be sent to all active users.

💡 Tip: Use Markdown formatting for rich text."""

        keyboard = [
            [
                InlineKeyboardButton("❌ Cancel", callback_data="admin_notifications"),
            ]
        ]

        if query:
            await safe_edit_message_text(
                query,
                message,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        elif update.message:
            await update.message.reply_text(
                message,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            
    except Exception as e:
        logger.error(f"Compose broadcast failed: {e}")
        if query:
            await safe_answer_callback_query(query, f"❌ Failed: {str(e)}", show_alert=True)
        return ConversationHandler.END
        
    return ConversationHandler.END


async def handle_broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the actual broadcast message from admin"""
    user = update.effective_user
    if not user or not is_admin_secure(user.id):
        return
    
    # Check if admin is composing a broadcast
    from handlers.admin_broadcast_direct import get_admin_broadcast_state, clear_admin_broadcast_state
    state = await get_admin_broadcast_state(user.id)
    
    if state != "composing":
        return
    
    # Check if message exists
    if not update.message:
        return
    
    try:
        # Clear the composing state
        await clear_admin_broadcast_state(user.id, context)
        
        # Import broadcast service
        from services.broadcast_service import BroadcastService
        
        broadcast_service = BroadcastService(context.bot)
        
        # Get the message content
        message_text = None
        if update.message.text:
            message_text = update.message.text
        elif update.message.caption:
            message_text = update.message.caption
        else:
            # For media without captions
            message_text = "📢 Broadcast from Admin"
        
        # Wrap message in branded format (Option 2: Admin Broadcast Style)
        branded_message = (
            f"📣 <b>System Notice</b>\n\n"
            f"{message_text}\n\n"
            f"---\n"
            f"✅ LockBay | Secure Crypto Escrow"
        )
        
        # Send confirmation
        await update.message.reply_text(
            "🚀 <b>Broadcast Started!</b>\n\n"
            "Your message is being sent to all users...\n"
            "You'll receive progress updates.",
            parse_mode="HTML"
        )
        
        # Start broadcast campaign  
        campaign_id = await broadcast_service.start_broadcast_campaign(
            message=branded_message,
            admin_user_id=user.id,
            target_users=None  # Send to all users
        )
        
        logger.info(f"✅ Broadcast campaign {campaign_id} started by admin {user.id}")
        
    except Exception as e:
        logger.error(f"Broadcast message handling failed: {e}")
        if update.message:
            await update.message.reply_text(
                f"❌ Broadcast failed: {str(e)}"
            )