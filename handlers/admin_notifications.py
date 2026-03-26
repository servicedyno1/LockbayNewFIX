"""
Admin Notification Dashboard Handler
Provides admin interface for monitoring and managing notifications
"""

import logging
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from utils.admin_auth import admin_required
from services.notification_monitor import notification_monitor
from utils.health_checks import health_service
from utils.callback_utils import safe_answer_callback_query

logger = logging.getLogger(__name__)

class AdminNotificationHandler:
    """Handler for admin notification management"""
    
    @staticmethod
    @admin_required
    async def show_notification_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show the main notification dashboard"""
        try:
            # Get health summary
            health_results = await health_service.run_all_checks()
            overall_status = health_service.get_overall_status(health_results)
            
            # Get notification stats
            stats = notification_monitor.stats
            failure_summary = notification_monitor.get_failure_summary(hours=24)
            
            # Status emojis
            status_emoji = {
                "healthy": "✅",
                "warning": "⚠️", 
                "critical": "❌",
                "unknown": "❓"
            }
            
            message = f"""
📊 **Notification System Dashboard**

🔧 **Overall Status**: {status_emoji.get(overall_status, '❓')} {overall_status.upper()}

📈 **Delivery Statistics**:
• Total sent: {stats.total_sent}
• Success rate: {stats.success_rate:.1f}%
• Telegram: {stats.telegram_sent} sent, {stats.telegram_failed} failed
• Email: {stats.email_sent} sent, {stats.email_failed} failed

⚠️ **Last 24 Hours**:
• Total failures: {failure_summary['total_failures']}
• Telegram failures: {failure_summary['telegram_failures']}
• Email failures: {failure_summary['email_failures']}
• Affected users: {failure_summary['affected_users']}

🔍 **Service Health**:
"""
            
            # Add individual service status
            for service_name, result in health_results.items():
                emoji = status_emoji.get(result.status, '❓')
                message += f"• {emoji} {service_name}: {result.message}\n"
            
            # Create keyboard
            keyboard = [
                [
                    InlineKeyboardButton("📋 Failure Details", callback_data="admin_notif_failures"),
                    InlineKeyboardButton("📊 Daily Stats", callback_data="admin_notif_daily")
                ],
                [
                    InlineKeyboardButton("🔄 Manual Resend", callback_data="admin_notif_resend"),
                    InlineKeyboardButton("🧹 Clear Old Data", callback_data="admin_notif_cleanup")
                ],
                [
                    InlineKeyboardButton("🔍 Health Details", callback_data="admin_notif_health"),
                    InlineKeyboardButton("⚙️ Settings", callback_data="admin_notif_settings")
                ],
                [InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_main")]
            ]
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            if update.callback_query:
                await update.callback_query.edit_message_text(
                    message, 
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
            else:
                await update.message.reply_text(
                    message, 
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
                
        except Exception as e:
            logger.error(f"Error showing notification dashboard: {e}")
            await update.effective_message.reply_text(
                f"❌ Error loading dashboard: {str(e)}"
            )
    
    @staticmethod
    @admin_required
    async def show_failure_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show detailed failure information"""
        try:
            failure_summary = notification_monitor.get_failure_summary(hours=24)
            
            message = f"""
📋 **Notification Failures (Last 24h)**

📊 **Summary**:
• Total failures: {failure_summary['total_failures']}
• Telegram: {failure_summary['telegram_failures']}
• Email: {failure_summary['email_failures']}
• Affected users: {failure_summary['affected_users']}

🔍 **Common Error Types**:
"""
            
            if failure_summary['most_common_errors']:
                for error, count in failure_summary['most_common_errors'].items():
                    message += f"• {error[:50]}{'...' if len(error) > 50 else ''} ({count}x)\n"
            else:
                message += "• No failures in the last 24 hours ✅\n"
            
            # Recent failures
            recent_failures = [f for f in notification_monitor.failures 
                             if f.timestamp > datetime.utcnow() - timedelta(hours=6)][:5]
            
            if recent_failures:
                message += "\n🕒 **Recent Failures (Last 6h)**:\n"
                for failure in recent_failures:
                    time_str = failure.timestamp.strftime("%H:%M")
                    message += f"• {time_str} - User {failure.user_id} ({failure.notification_type}): {failure.error_message[:30]}...\n"
            
            keyboard = [
                [
                    InlineKeyboardButton("📊 Export Log", callback_data="admin_notif_export")
                ],
                [InlineKeyboardButton("🔙 Back", callback_data="admin_notifications")]
            ]
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.callback_query.edit_message_text(
                message, 
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
            
        except Exception as e:
            logger.error(f"Error showing failure details: {e}")
            await safe_answer_callback_query(update.callback_query, "❌ Error loading failure details")
    
    @staticmethod
    @admin_required
    async def show_daily_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show daily statistics"""
        try:
            daily_stats = notification_monitor.get_daily_stats(days=7)
            
            message = "📊 **Daily Notification Statistics (Last 7 Days)**\n\n"
            
            for date, stats in daily_stats.items():
                if stats.total_sent > 0:
                    message += f"**{date}**:\n"
                    message += f"• Sent: {stats.total_sent}, Failed: {stats.total_failed}\n"
                    message += f"• Success: {stats.success_rate:.1f}%\n"
                    message += f"• TG: {stats.telegram_sent}/{stats.telegram_failed}, Email: {stats.email_sent}/{stats.email_failed}\n\n"
                else:
                    message += f"**{date}**: No notifications\n\n"
            
            keyboard = [
                [
                    InlineKeyboardButton("📈 Trends", callback_data="admin_notif_trends")
                ],
                [InlineKeyboardButton("🔙 Back", callback_data="admin_notifications")]
            ]
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.callback_query.edit_message_text(
                message, 
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
            
        except Exception as e:
            logger.error(f"Error showing daily stats: {e}")
            await safe_answer_callback_query(update.callback_query, "❌ Error loading daily statistics")
    
    @staticmethod
    @admin_required
    async def manual_resend_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show manual resend options"""
        try:
            message = """
🔄 **Manual Notification Resend**

Choose what to resend:

🎯 **By User**: Resend failed notifications for specific user
📊 **By Type**: Resend all failed notifications of specific type
⏰ **By Time**: Resend all failures from last N hours

⚠️ **Warning**: This will attempt to resend failed notifications. Make sure the underlying issues are resolved first.
"""
            
            keyboard = [
                [
                    InlineKeyboardButton("👤 By User ID", callback_data="admin_resend_user"),
                    InlineKeyboardButton("📱 Telegram Only", callback_data="admin_resend_telegram")
                ],
                [
                    InlineKeyboardButton("📧 Email Only", callback_data="admin_resend_email"), 
                    InlineKeyboardButton("⏰ Last Hour", callback_data="admin_resend_hour")
                ],
                [InlineKeyboardButton("🔙 Back", callback_data="admin_notifications")]
            ]
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.callback_query.edit_message_text(
                message, 
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
            
        except Exception as e:
            logger.error(f"Error showing resend menu: {e}")
            await safe_answer_callback_query(update.callback_query, "❌ Error loading resend menu")
    
    @staticmethod
    @admin_required
    async def cleanup_old_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Clean up old notification data"""
        try:
            # Clear old failures (30+ days)
            original_count = len(notification_monitor.failures)
            notification_monitor.clear_old_failures(days=30)
            cleared_count = original_count - len(notification_monitor.failures)
            
            message = f"""
🧹 **Data Cleanup Complete**

• Cleared {cleared_count} old failure records
• Retained {len(notification_monitor.failures)} recent failures
• Daily stats preserved

✅ **System cleaned up successfully**
"""
            
            keyboard = [
                [InlineKeyboardButton("🔙 Back", callback_data="admin_notifications")]
            ]
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.callback_query.edit_message_text(
                message, 
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
            
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")
            await safe_answer_callback_query(update.callback_query, "❌ Error during cleanup")

# Handler registration functions
def register_admin_notification_handlers(application):
    """Register all admin notification handlers"""
    from telegram.ext import CallbackQueryHandler
    
    application.add_handler(CallbackQueryHandler(
        AdminNotificationHandler.show_notification_dashboard, 
        pattern="^admin_notifications$"
    ))
    application.add_handler(CallbackQueryHandler(
        AdminNotificationHandler.show_failure_details, 
        pattern="^admin_notif_failures$"
    ))
    application.add_handler(CallbackQueryHandler(
        AdminNotificationHandler.show_daily_stats, 
        pattern="^admin_notif_daily$"
    ))
    application.add_handler(CallbackQueryHandler(
        AdminNotificationHandler.manual_resend_menu, 
        pattern="^admin_notif_resend$"
    ))
    application.add_handler(CallbackQueryHandler(
        AdminNotificationHandler.cleanup_old_data, 
        pattern="^admin_notif_cleanup$"
    ))