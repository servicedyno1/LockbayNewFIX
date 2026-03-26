"""
Refund Command Registry
Registers all refund-related bot commands and callbacks with the application
"""

import logging
from telegram.ext import (
    Application, 
    CommandHandler, 
    CallbackQueryHandler, 
    ConversationHandler,
    MessageHandler,
    filters
)

from handlers.refund_dashboard import user_refund_dashboard, REFUND_MAIN_MENU, REFUND_HISTORY, REFUND_DETAILS, REFUND_LOOKUP
from handlers.enhanced_admin_refund_dashboard import enhanced_admin_refund_dashboard
from telegram import InlineKeyboardButton
from utils.admin_security import is_admin_secure
from utils.callback_utils import safe_answer_callback_query

logger = logging.getLogger(__name__)


class RefundCommandRegistry:
    """Registry for all refund-related commands and callbacks"""
    
    @staticmethod
    def register_user_commands(application: Application):
        """Register user-facing refund commands"""
        try:
            # Main /refunds command
            application.add_handler(CommandHandler("refunds", user_refund_dashboard.refunds_command))
            
            # Refund conversation handler for interactive navigation
            refund_conversation = ConversationHandler(
                entry_points=[
                    CommandHandler("refunds", user_refund_dashboard.refunds_command),
                    CallbackQueryHandler(user_refund_dashboard.refunds_command, pattern="^user_refunds$")
                ],
                states={
                    REFUND_MAIN_MENU: [
                        CallbackQueryHandler(
                            lambda update, context: user_refund_dashboard.show_refund_history(update, context, "all"),
                            pattern="^refund_view_all$"
                        ),
                        CallbackQueryHandler(
                            lambda update, context: user_refund_dashboard.show_refund_history(update, context, "pending"),
                            pattern="^refund_view_active$"
                        ),
                        CallbackQueryHandler(
                            user_refund_dashboard.show_refund_stats,
                            pattern="^refund_stats$"
                        ),
                        CallbackQueryHandler(
                            user_refund_dashboard.show_filter_options,
                            pattern="^refund_filters$"
                        ),
                        CallbackQueryHandler(
                            RefundCommandRegistry._show_refund_help,
                            pattern="^refund_help$"
                        ),
                        CallbackQueryHandler(
                            lambda update, context: RefundCommandRegistry._handle_refund_lookup_start(update, context),
                            pattern="^refund_lookup$"
                        )
                    ],
                    REFUND_HISTORY: [
                        # History navigation callbacks
                        CallbackQueryHandler(
                            RefundCommandRegistry._handle_history_pagination,
                            pattern="^refund_history_(.+)_(\d+)$"
                        ),
                        CallbackQueryHandler(
                            user_refund_dashboard.show_filter_options,
                            pattern="^refund_filters$"
                        ),
                        CallbackQueryHandler(
                            lambda update, context: user_refund_dashboard.show_refund_main_menu(update, context, None),
                            pattern="^refund_main_menu$"
                        )
                    ],
                    REFUND_DETAILS: [
                        # Detail view callbacks
                        CallbackQueryHandler(
                            RefundCommandRegistry._handle_refund_detail_view,
                            pattern="^refund_detail_(.+)$"
                        ),
                        CallbackQueryHandler(
                            RefundCommandRegistry._handle_refund_confirmation,
                            pattern="^refund_confirm_(.+)$"
                        ),
                        CallbackQueryHandler(
                            RefundCommandRegistry._handle_refund_tracking,
                            pattern="^refund_track_(.+)$"
                        )
                    ],
                    REFUND_LOOKUP: [
                        # Lookup state handlers
                        MessageHandler(
                            filters.TEXT & ~filters.COMMAND,
                            RefundCommandRegistry._handle_refund_id_input
                        ),
                        CallbackQueryHandler(
                            lambda update, context: user_refund_dashboard.show_refund_main_menu(update, context, None),
                            pattern="^refund_main_menu$"
                        )
                    ]
                },
                fallbacks=[
                    CommandHandler("cancel", RefundCommandRegistry._cancel_refund_conversation),
                    CallbackQueryHandler(
                        lambda update, context: user_refund_dashboard.show_refund_main_menu(update, context, None),
                        pattern="^refund_main_menu$"
                    )
                ],
                name="refund_conversation",
                persistent=False,
                per_message=False,
                per_chat=True,
                per_user=True
            )
            
            application.add_handler(refund_conversation)
            
            # Direct callback handlers for refund operations
            application.add_handler(CallbackQueryHandler(
                RefundCommandRegistry._handle_refund_confirmation,
                pattern="^refund_confirm_(.+)$"
            ))
            
            logger.info("✅ User refund commands registered successfully")
            
        except Exception as e:
            logger.error(f"❌ Error registering user refund commands: {e}")
    
    @staticmethod
    def register_admin_commands(application: Application):
        """Register admin-facing refund commands"""
        try:
            # Main admin refund dashboard
            application.add_handler(CallbackQueryHandler(
                RefundCommandRegistry._admin_refund_dashboard_guard(
                    enhanced_admin_refund_dashboard.show_main_refund_dashboard
                ),
                pattern="^admin_refund_dashboard$"
            ))
            
            # Admin refund analytics
            application.add_handler(CallbackQueryHandler(
                RefundCommandRegistry._admin_refund_dashboard_guard(
                    enhanced_admin_refund_dashboard.show_refund_analytics
                ),
                pattern="^admin_refund_analytics$"
            ))
            
            # Admin live tracking
            application.add_handler(CallbackQueryHandler(
                RefundCommandRegistry._admin_refund_dashboard_guard(
                    enhanced_admin_refund_dashboard.show_live_tracking
                ),
                pattern="^admin_refund_tracking$"
            ))
            
            # Admin performance metrics
            application.add_handler(CallbackQueryHandler(
                RefundCommandRegistry._admin_refund_dashboard_guard(
                    enhanced_admin_refund_dashboard.show_performance_metrics
                ),
                pattern="^admin_refund_performance$"
            ))
            
            # Admin alerts and issues
            application.add_handler(CallbackQueryHandler(
                RefundCommandRegistry._admin_refund_dashboard_guard(
                    enhanced_admin_refund_dashboard.show_alerts_and_issues
                ),
                pattern="^admin_refund_alerts$"
            ))
            
            # Additional admin callback patterns
            admin_patterns = [
                "^admin_analytics_(.+)$",
                "^admin_tracking_(.+)$", 
                "^admin_perf_(.+)$",
                "^admin_alerts_(.+)$",
                "^admin_refund_management$",
                "^admin_refund_reports$",
                "^admin_refund_auto_refresh$",
                "^admin_refund_realtime$"
            ]
            
            for pattern in admin_patterns:
                application.add_handler(CallbackQueryHandler(
                    RefundCommandRegistry._handle_admin_refund_callback,
                    pattern=pattern
                ))
            
            logger.info("✅ Admin refund commands registered successfully")
            
        except Exception as e:
            logger.error(f"❌ Error registering admin refund commands: {e}")
    
    @staticmethod
    def register_all_commands(application: Application):
        """Register all refund-related commands"""
        try:
            RefundCommandRegistry.register_user_commands(application)
            RefundCommandRegistry.register_admin_commands(application)
            
            # Register general refund callback patterns
            application.add_handler(CallbackQueryHandler(
                RefundCommandRegistry._handle_general_refund_callback,
                pattern="^refund_(.+)$"
            ))
            
            logger.info("✅ All refund commands registered successfully")
            return True
            
        except Exception as e:
            logger.error(f"❌ Error registering refund commands: {e}")
            return False
    
    # Helper methods for command handling
    
    @staticmethod
    def _admin_refund_dashboard_guard(handler_func):
        """Decorator to guard admin refund handlers with security check"""
        async def wrapper(update, context):
            user = update.effective_user
            if not user or not is_admin_secure(user.id):
                if update.callback_query:
                    await safe_answer_callback_query(update.callback_query, "❌ Admin access required", show_alert=True)
                return
            
            return await handler_func(update, context)
        
        return wrapper
    
    @staticmethod
    async def _handle_history_pagination(update, context):
        """Handle refund history pagination"""
        try:
            callback_data = update.callback_query.data
            parts = callback_data.replace("refund_history_", "").split("_")
            
            if len(parts) >= 2:
                filter_type = parts[0]
                page = int(parts[1])
                
                # Store page in context
                context.user_data["refund_page"] = page
                
                # Show history with filter
                await user_refund_dashboard.show_refund_history(update, context, filter_type)
            
        except Exception as e:
            logger.error(f"❌ Error handling history pagination: {e}")
            await safe_answer_callback_query(update.callback_query, "❌ Error loading page", show_alert=True)
    
    @staticmethod
    async def _handle_refund_detail_view(update, context):
        """Handle refund detail view"""
        try:
            callback_data = update.callback_query.data
            refund_id = callback_data.replace("refund_detail_", "")
            
            await user_refund_dashboard.show_refund_details(update, context, refund_id)
            
        except Exception as e:
            logger.error(f"❌ Error handling refund detail view: {e}")
            await safe_answer_callback_query(update.callback_query, "❌ Error loading details", show_alert=True)
    
    @staticmethod
    async def _handle_refund_confirmation(update, context):
        """Handle refund confirmation"""
        try:
            callback_data = update.callback_query.data
            refund_id = callback_data.replace("refund_confirm_", "")
            
            await user_refund_dashboard.confirm_refund_receipt(update, context, refund_id)
            
        except Exception as e:
            logger.error(f"❌ Error handling refund confirmation: {e}")
            await safe_answer_callback_query(update.callback_query, "❌ Error confirming refund", show_alert=True)
    
    @staticmethod
    async def _handle_refund_tracking(update, context):
        """Handle refund tracking request"""
        try:
            callback_data = update.callback_query.data
            refund_id = callback_data.replace("refund_track_", "")
            
            # Show detailed tracking information
            await user_refund_dashboard.show_refund_details(update, context, refund_id)
            
        except Exception as e:
            logger.error(f"❌ Error handling refund tracking: {e}")
            await safe_answer_callback_query(update.callback_query, "❌ Error loading tracking", show_alert=True)
    
    @staticmethod
    async def _handle_refund_lookup_start(update, context):
        """Start refund lookup by ID"""
        try:
            lookup_text = """🔍 **Refund Lookup**

Please enter your refund ID to check its status.

Refund IDs typically look like:
• `REF_123456789`
• `CASHOUT_FAILED_123456`
• `ESC_REFUND_123456`

Type your refund ID or tap Cancel to return to the main menu."""
            
            keyboard = [[
                InlineKeyboardButton("❌ Cancel", callback_data="refund_main_menu")
            ]]
            
            from telegram import InlineKeyboardMarkup
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            if update.callback_query:
                from utils.callback_utils import safe_edit_message_text
                await safe_edit_message_text(
                    update.callback_query,
                    lookup_text,
                    reply_markup=reply_markup,
                    parse_mode="Markdown"
                )
            
            return REFUND_LOOKUP
            
        except Exception as e:
            logger.error(f"❌ Error starting refund lookup: {e}")
            return REFUND_MAIN_MENU
    
    @staticmethod
    async def _handle_refund_id_input(update, context):
        """Handle refund ID input for lookup"""
        try:
            refund_id = update.message.text.strip().upper()
            
            # Validate refund ID format
            if not refund_id or len(refund_id) < 5:
                await update.message.reply_text(
                    "❌ Invalid refund ID format. Please enter a valid refund ID.",
                    parse_mode="Markdown"
                )
                return REFUND_LOOKUP
            
            # Try to show refund details
            await user_refund_dashboard.show_refund_details(update, context, refund_id)
            
            return REFUND_DETAILS
            
        except Exception as e:
            logger.error(f"❌ Error handling refund ID input: {e}")
            await update.message.reply_text(
                "❌ Error looking up refund. Please try again or contact support.",
                parse_mode="Markdown"
            )
            return REFUND_LOOKUP
    
    @staticmethod
    async def _show_refund_help(update, context):
        """Show refund help information"""
        try:
            help_text = """❓ **Refund Help & Support**

**What is a refund?**
A refund returns money to your wallet when a transaction cannot be completed or needs to be reversed.

**Common refund types:**
• 💳 **Cashout Failed** - When a withdrawal cannot be processed
• ⚖️ **Escrow Refund** - When an escrow trade is cancelled
• 🛡️ **Dispute Resolution** - Refunds from dispute outcomes
• 👤 **Admin Refund** - Manual refunds by support

**Refund process:**
1. 🚀 **Initiated** - Refund request is received
2. 🔍 **Validating** - Checking eligibility and funds
3. ⚙️ **Processing** - Processing the refund
4. ✅ **Wallet Credited** - Funds added to your wallet
5. 📨 **User Notified** - You receive confirmation
6. ✅ **Completed** - Process finished

**How to track refunds:**
• Use `/refunds` command to see all your refunds
• Filter by status, type, or date
• Click on any refund ID for detailed tracking
• Get real-time progress updates

**Need help?**
• Contact support via the main menu
• Include your refund ID in support messages
• Check your notification settings for updates

**Typical processing times:**
• Simple refunds: 2-10 minutes
• Complex refunds: 15-30 minutes
• Manual review cases: 1-24 hours

**Still have questions?**
Our support team is available 24/7 to help with any refund-related issues."""
            
            keyboard = [[
                InlineKeyboardButton("📞 Contact Support", callback_data="contact_support"),
                InlineKeyboardButton("⬅️ Back", callback_data="refund_main_menu")
            ]]
            
            from telegram import InlineKeyboardMarkup
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            from utils.callback_utils import safe_edit_message_text
            await safe_edit_message_text(
                update.callback_query,
                help_text,
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
            
        except Exception as e:
            logger.error(f"❌ Error showing refund help: {e}")
    
    @staticmethod
    async def _cancel_refund_conversation(update, context):
        """Cancel refund conversation"""
        try:
            await update.message.reply_text(
                "❌ Refund conversation cancelled. Use `/refunds` to start again.",
                parse_mode="Markdown"
            )
            return ConversationHandler.END
            
        except Exception as e:
            logger.error(f"❌ Error cancelling refund conversation: {e}")
            return ConversationHandler.END
    
    @staticmethod
    async def _handle_admin_refund_callback(update, context):
        """Handle admin refund callbacks"""
        try:
            callback_data = update.callback_query.data
            
            # Security check
            user = update.effective_user
            if not user or not is_admin_secure(user.id):
                await safe_answer_callback_query(update.callback_query, "❌ Admin access required", show_alert=True)
                return
            
            # Route to appropriate handler based on callback pattern
            if callback_data.startswith("admin_analytics_"):
                await RefundCommandRegistry._handle_admin_analytics_callback(update, context)
            elif callback_data.startswith("admin_tracking_"):
                await RefundCommandRegistry._handle_admin_tracking_callback(update, context)
            elif callback_data.startswith("admin_perf_"):
                await RefundCommandRegistry._handle_admin_performance_callback(update, context)
            elif callback_data.startswith("admin_alerts_"):
                await RefundCommandRegistry._handle_admin_alerts_callback(update, context)
            else:
                # Default to showing main dashboard
                await enhanced_admin_refund_dashboard.show_main_refund_dashboard(update, context)
            
        except Exception as e:
            logger.error(f"❌ Error handling admin refund callback: {e}")
            await safe_answer_callback_query(update.callback_query, "❌ Error processing request", show_alert=True)
    
    @staticmethod
    async def _handle_admin_analytics_callback(update, context):
        """Handle admin analytics callbacks"""
        callback_data = update.callback_query.data
        
        if callback_data == "admin_analytics_daily":
            # Show daily analytics view
            await enhanced_admin_refund_dashboard.show_refund_analytics(update, context)
        elif callback_data == "admin_analytics_weekly":
            # Show weekly analytics view  
            await enhanced_admin_refund_dashboard.show_refund_analytics(update, context)
        elif callback_data == "admin_analytics_trends":
            # Show trend analysis
            await enhanced_admin_refund_dashboard.show_refund_analytics(update, context)
        elif callback_data == "admin_analytics_patterns":
            # Show pattern analysis
            await enhanced_admin_refund_dashboard.show_refund_analytics(update, context)
        elif callback_data == "admin_analytics_insights":
            # Show insights
            await enhanced_admin_refund_dashboard.show_refund_analytics(update, context)
        else:
            await enhanced_admin_refund_dashboard.show_refund_analytics(update, context)
    
    @staticmethod
    async def _handle_admin_tracking_callback(update, context):
        """Handle admin tracking callbacks"""
        callback_data = update.callback_query.data
        
        if callback_data == "admin_tracking_refresh":
            # Refresh live tracking
            await enhanced_admin_refund_dashboard.show_live_tracking(update, context)
        elif callback_data == "admin_tracking_websocket":
            # Show WebSocket status
            await enhanced_admin_refund_dashboard.show_live_tracking(update, context)
        elif callback_data == "admin_tracking_sessions":
            # Show session details
            await enhanced_admin_refund_dashboard.show_live_tracking(update, context)
        else:
            await enhanced_admin_refund_dashboard.show_live_tracking(update, context)
    
    @staticmethod
    async def _handle_admin_performance_callback(update, context):
        """Handle admin performance callbacks"""
        callback_data = update.callback_query.data
        
        if callback_data == "admin_perf_trends":
            # Show performance trends
            await enhanced_admin_refund_dashboard.show_performance_metrics(update, context)
        elif callback_data == "admin_perf_issues":
            # Show performance issues
            await enhanced_admin_refund_dashboard.show_performance_metrics(update, context)
        elif callback_data == "admin_perf_optimize":
            # Show optimization suggestions
            await enhanced_admin_refund_dashboard.show_performance_metrics(update, context)
        else:
            await enhanced_admin_refund_dashboard.show_performance_metrics(update, context)
    
    @staticmethod
    async def _handle_admin_alerts_callback(update, context):
        """Handle admin alerts callbacks"""
        callback_data = update.callback_query.data
        
        if callback_data == "admin_alerts_critical":
            # Show critical alerts only
            await enhanced_admin_refund_dashboard.show_alerts_and_issues(update, context)
        elif callback_data == "admin_alerts_all":
            # Show all alerts
            await enhanced_admin_refund_dashboard.show_alerts_and_issues(update, context)
        elif callback_data == "admin_alerts_anomalies":
            # Show anomaly details
            await enhanced_admin_refund_dashboard.show_alerts_and_issues(update, context)
        else:
            await enhanced_admin_refund_dashboard.show_alerts_and_issues(update, context)
    
    @staticmethod
    async def _handle_general_refund_callback(update, context):
        """Handle general refund callbacks"""
        try:
            callback_data = update.callback_query.data
            
            # Route to user dashboard for general refund callbacks
            if callback_data == "refund_main_menu":
                user = await RefundCommandRegistry._get_user_from_update(update)
                if user:
                    await user_refund_dashboard.show_refund_main_menu(update, context, user)
            elif callback_data.startswith("refund_"):
                # Handle other refund patterns
                if callback_data.startswith("refund_status_"):
                    from handlers.refund_notification_handlers import RefundNotificationHandlers
                    await RefundNotificationHandlers.handle_refund_status(update, context)
                elif callback_data == "refund_faq":
                    from handlers.refund_notification_handlers import RefundNotificationHandlers
                    await RefundNotificationHandlers.handle_refund_faq(update, context)
                else:
                    # Let the conversation handler deal with it
                    pass
            else:
                # Let the conversation handler deal with it
                pass
            
        except Exception as e:
            logger.error(f"❌ Error handling general refund callback: {e}")
    
    @staticmethod
    async def _get_user_from_update(update):
        """Get user from update (simplified version)"""
        try:
            from handlers.commands import get_user_from_update
            return await get_user_from_update(update)
        except Exception as e:
            logger.error(f"❌ Error getting user from update: {e}")
            return None


# Global registry instance
refund_command_registry = RefundCommandRegistry()

# Module-level handlers for lazy loader compatibility
# These are wrapper functions that the lazy loader can access

def refunds_command_handler(update, context):
    """Module-level refunds command handler for lazy loader"""
    from handlers.refund_dashboard import user_refund_dashboard
    return user_refund_dashboard.refunds_command(update, context)

def refund_status_handler(update, context):
    """Module-level refund status handler for lazy loader"""
    from handlers.refund_notification_handlers import RefundNotificationHandlers
    return RefundNotificationHandlers.handle_refund_status(update, context)

# Create the conversation handler as a module-level variable for lazy loader

def _create_refund_conversation_handler():
    """Create the refund conversation handler"""
    from handlers.refund_dashboard import user_refund_dashboard, REFUND_MAIN_MENU
    
    return ConversationHandler(
        entry_points=[
            CommandHandler("refunds", refunds_command_handler),
            CallbackQueryHandler(refunds_command_handler, pattern="^user_refunds$")
        ],
        states={
            REFUND_MAIN_MENU: [
                CallbackQueryHandler(
                    lambda update, context: user_refund_dashboard.show_refund_history(update, context, "all"),
                    pattern="^refund_view_all$"
                ),
                CallbackQueryHandler(
                    lambda update, context: user_refund_dashboard.show_refund_history(update, context, "pending"),
                    pattern="^refund_view_active$"
                ),
                CallbackQueryHandler(
                    user_refund_dashboard.show_refund_stats,
                    pattern="^refund_stats$"
                ),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(RefundCommandRegistry._handle_general_refund_callback, pattern="^refund_")
        ],
        per_message=False,
        per_chat=False,
        per_user=True,
        persistent=False,
        # TIMEOUT: Auto-cleanup abandoned refund sessions after 10 minutes
        conversation_timeout=600  # 10 minutes for refund flows
    )

# Handler function for lazy loader - returns ConversationHandler when called  
def refund_conversation_handler():
    """Return the refund conversation handler for lazy loader"""
    return _create_refund_conversation_handler()