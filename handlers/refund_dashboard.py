"""
User-facing Refund Dashboard Handler
Comprehensive refund tracking and history interface for users via bot commands
"""

import logging
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, ConversationHandler

from database import SessionLocal
from models import User, Refund, RefundStatus
from utils.refund_status_tracking import refund_status_tracker
from utils.refund_progress_tracker import real_time_refund_tracker
from utils.callback_utils import safe_edit_message_text, safe_answer_callback_query
from utils.markdown_escaping import escape_markdown
from handlers.commands import get_user_from_update
from config import Config

logger = logging.getLogger(__name__)

# Conversation states
REFUND_MAIN_MENU = 0
REFUND_HISTORY = 1
REFUND_DETAILS = 2
REFUND_LOOKUP = 3
REFUND_FILTER = 4


class UserRefundDashboard:
    """User-facing refund dashboard with comprehensive tracking capabilities"""
    
    @staticmethod
    async def refunds_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle /refunds command - Main entry point"""
        logger.info(f"🔄 refunds_command called by user {update.effective_user.id if update.effective_user else 'None'}")
        
        # Immediate feedback
        if update.callback_query:
            await safe_answer_callback_query(update.callback_query, "📊 Loading refunds...")
        
        user = await get_user_from_update(update)
        if not user:
            message_text = "👋 Welcome! You'll need to register first.\n\nJust type /start to get set up!"
            if update.message:
                await update.message.reply_text(message_text)
            return ConversationHandler.END
        
        # Show main refund dashboard
        await UserRefundDashboard.show_refund_main_menu(update, context, user)
        return REFUND_MAIN_MENU
    
    @staticmethod
    async def show_refund_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, user: User):
        """Display the main refund dashboard menu"""
        try:
            # Get user's refund summary
            refund_summary = refund_status_tracker.get_user_refunds_summary(user.id, limit=20)
            
            # Get active refunds from real-time tracker
            active_refunds = real_time_refund_tracker.get_user_active_refunds(user.id)
            
            # Build main menu message
            total_refunds = refund_summary.get("total_refunds", 0)
            completed_refunds = refund_summary.get("completed_refunds", 0)
            pending_refunds = refund_summary.get("pending_refunds", 0)
            success_rate = refund_summary.get("success_rate", 0)
            
            # Welcome message with branding
            menu_text = f"""🔄 **{Config.PLATFORM_NAME} Refund Center**

👋 Hi {escape_markdown(str(user.first_name) if user.first_name else 'there')}!

📊 **Your Refund Summary:**
• Total Refunds: {total_refunds}
• Completed: ✅ {completed_refunds}
• Pending: 🔄 {pending_refunds}
• Success Rate: {success_rate:.1f}%

"""

            # Active refunds section
            if active_refunds:
                menu_text += "🔥 **Active Refunds:**\n"
                for refund in active_refunds[:3]:  # Show up to 3 active refunds
                    stage = refund.get("current_stage", "unknown")
                    progress = refund.get("progress_percent", 0)
                    refund_id = refund.get("refund_id", "")
                    
                    stage_emoji = UserRefundDashboard._get_stage_emoji(stage)
                    menu_text += f"{stage_emoji} `{refund_id}` - {progress}% {UserRefundDashboard._get_stage_display(stage)}\n"
                
                if len(active_refunds) > 3:
                    menu_text += f"   ... and {len(active_refunds) - 3} more\n"
                menu_text += "\n"
            else:
                menu_text += "✅ **No Active Refunds**\n\n"
            
            # Recent activity
            recent_refunds = refund_summary.get("refunds", [])
            if recent_refunds:
                menu_text += "📋 **Recent Activity:**\n"
                for refund in recent_refunds[:3]:  # Show last 3 refunds
                    refund_id = refund.get("refund_id", "")
                    amount = refund.get("amount", 0)
                    status = refund.get("status", "unknown")
                    created_at = refund.get("created_at", "")
                    
                    status_emoji = UserRefundDashboard._get_status_emoji(status)
                    date_str = UserRefundDashboard._format_date(created_at)
                    menu_text += f"{status_emoji} `{refund_id}` - ${amount:.2f} ({date_str})\n"
                menu_text += "\n"
            
            menu_text += "🛠️ Choose an option below or type a refund ID to check status."
            
            # Create main menu keyboard
            keyboard = [
                [
                    InlineKeyboardButton("📋 View All Refunds", callback_data="refund_view_all"),
                    InlineKeyboardButton("🔍 Lookup by ID", callback_data="refund_lookup")
                ],
                [
                    InlineKeyboardButton("🔄 Active Refunds", callback_data="refund_view_active"),
                    InlineKeyboardButton("📊 Refund Stats", callback_data="refund_stats")
                ],
                [
                    InlineKeyboardButton("🔧 Filter Options", callback_data="refund_filters"),
                    InlineKeyboardButton("❓ Help", callback_data="refund_help")
                ],
                [
                    InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")
                ]
            ]
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Send or edit message
            if update.callback_query:
                await safe_edit_message_text(
                    update.callback_query,
                    menu_text,
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.MARKDOWN
                )
            elif update.message:
                await update.message.reply_text(
                    menu_text,
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.MARKDOWN
                )
                
        except Exception as e:
            logger.error(f"❌ Error displaying refund main menu: {e}")
            error_text = "❌ Error loading refund dashboard. Please try again."
            
            if update.callback_query:
                await safe_edit_message_text(update.callback_query, error_text)
            elif update.message:
                await update.message.reply_text(error_text)
    
    @staticmethod
    async def show_refund_history(update: Update, context: ContextTypes.DEFAULT_TYPE, filter_type: str = "all"):
        """Display paginated refund history"""
        try:
            user = await get_user_from_update(update)
            if not user:
                return
            
            # Get page number from context
            page = context.user_data.get("refund_page", 1)
            per_page = 5
            
            # Get filtered refunds
            with SessionLocal() as session:
                query = session.query(Refund).filter(Refund.user_id == user.id)
                
                # Apply filters
                if filter_type == "completed":
                    query = query.filter(Refund.status == RefundStatus.COMPLETED.value)
                elif filter_type == "pending":
                    query = query.filter(Refund.status == RefundStatus.PENDING.value)
                elif filter_type == "failed":
                    query = query.filter(Refund.status == RefundStatus.FAILED.value)
                elif filter_type == "recent":
                    # Last 30 days
                    since_date = datetime.utcnow() - timedelta(days=30)
                    query = query.filter(Refund.created_at >= since_date)
                
                # Pagination
                total_count = query.count()
                refunds = (
                    query.order_by(Refund.created_at.desc())
                    .offset((page - 1) * per_page)
                    .limit(per_page)
                    .all()
                )
                
                total_pages = (total_count + per_page - 1) // per_page
            
            # Build history message
            filter_title = {
                "all": "All Refunds",
                "completed": "Completed Refunds", 
                "pending": "Pending Refunds",
                "failed": "Failed Refunds",
                "recent": "Recent Refunds (30 days)"
            }.get(filter_type, "Refund History")
            
            history_text = f"📋 **{filter_title}**\n\n"
            
            if not refunds:
                history_text += f"📭 No refunds found for '{filter_title.lower()}'.\n\n"
            else:
                history_text += f"📊 Showing {len(refunds)} of {total_count} refunds (Page {page}/{total_pages})\n\n"
                
                for refund in refunds:
                    # Get status emoji and progress info
                    status_emoji = UserRefundDashboard._get_status_emoji(refund.status)
                    refund_type_display = UserRefundDashboard._get_refund_type_display(refund.refund_type)
                    date_str = UserRefundDashboard._format_date(refund.created_at.isoformat())
                    
                    # Check if refund is actively being tracked
                    detailed_progress = real_time_refund_tracker.get_detailed_progress(refund.refund_id)
                    progress_text = ""
                    if detailed_progress and detailed_progress.get("current_stage"):
                        stage = detailed_progress["current_stage"]
                        progress_percent = detailed_progress.get("progress_percent", 0)
                        progress_text = f" - {progress_percent}% {UserRefundDashboard._get_stage_display(stage)}"
                    
                    history_text += f"""
{status_emoji} **{refund.refund_id}**{progress_text}
💰 Amount: ${float(refund.amount):,.2f} USD
📝 Type: {refund_type_display}
📅 Date: {date_str}
📄 Reason: {refund.reason[:50]}...

"""
            
            # Create navigation keyboard
            keyboard = []
            
            # Pagination controls
            nav_row = []
            if page > 1:
                nav_row.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"refund_history_{filter_type}_{page-1}"))
            if page < total_pages:
                nav_row.append(InlineKeyboardButton("➡️ Next", callback_data=f"refund_history_{filter_type}_{page+1}"))
            if nav_row:
                keyboard.append(nav_row)
            
            # Detail view for individual refunds
            if refunds:
                keyboard.append([
                    InlineKeyboardButton("🔍 View Details", callback_data="refund_select_detail"),
                ])
            
            # Filter options
            keyboard.append([
                InlineKeyboardButton("🔧 Change Filter", callback_data="refund_filters"),
                InlineKeyboardButton("🔄 Refresh", callback_data=f"refund_history_{filter_type}_{page}")
            ])
            
            # Back button
            keyboard.append([
                InlineKeyboardButton("⬅️ Back to Menu", callback_data="refund_main_menu")
            ])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await safe_edit_message_text(
                update.callback_query,
                history_text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
            
        except Exception as e:
            logger.error(f"❌ Error displaying refund history: {e}")
            await safe_edit_message_text(
                update.callback_query,
                "❌ Error loading refund history. Please try again."
            )
    
    @staticmethod
    async def show_refund_details(update: Update, context: ContextTypes.DEFAULT_TYPE, refund_id: str):
        """Display detailed information for a specific refund"""
        try:
            user = await get_user_from_update(update)
            if not user:
                return
            
            # Get refund from database
            with SessionLocal() as session:
                refund = (
                    session.query(Refund)
                    .filter(Refund.refund_id == refund_id, Refund.user_id == user.id)
                    .first()
                )
                
                if not refund:
                    await safe_edit_message_text(
                        update.callback_query,
                        f"❌ Refund `{refund_id}` not found or you don't have access to it.",
                        parse_mode=ParseMode.MARKDOWN
                    )
                    return
            
            # Get real-time progress data
            detailed_progress = real_time_refund_tracker.get_detailed_progress(refund_id)
            
            # Build detailed view
            details_text = f"🔍 **Refund Details: {refund_id}**\n\n"
            
            # Basic information
            status_emoji = UserRefundDashboard._get_status_emoji(refund.status)
            refund_type_display = UserRefundDashboard._get_refund_type_display(refund.refund_type)
            
            details_text += f"""📊 **Status:** {status_emoji} {refund.status.title()}
💰 **Amount:** ${float(refund.amount):,.2f} {refund.currency}
📝 **Type:** {refund_type_display}
📅 **Created:** {UserRefundDashboard._format_date(refund.created_at.isoformat())}
📄 **Reason:** {refund.reason}

"""
            
            # Progress tracking information
            if detailed_progress:
                current_stage = detailed_progress.get("current_stage", "unknown")
                progress_percent = detailed_progress.get("progress_percent", 0)
                estimated_completion = detailed_progress.get("estimated_completion")
                
                details_text += f"""🔄 **Current Progress:**
Stage: {UserRefundDashboard._get_stage_emoji(current_stage)} {UserRefundDashboard._get_stage_display(current_stage)}
Progress: {progress_percent}%
"""
                
                if estimated_completion:
                    details_text += f"⏰ Estimated Completion: {UserRefundDashboard._format_date(estimated_completion)}\n"
                
                # Progress history (last 5 updates)
                progress_history = detailed_progress.get("progress_history", [])
                if progress_history:
                    details_text += "\n📋 **Progress Timeline:**\n"
                    for update_item in progress_history[-5:]:  # Last 5 updates
                        stage = update_item.get("stage", "unknown")
                        timestamp = update_item.get("timestamp", "")
                        details = update_item.get("details", "")
                        progress = update_item.get("progress_percent", 0)
                        
                        stage_emoji = UserRefundDashboard._get_stage_emoji(stage)
                        time_str = UserRefundDashboard._format_date(timestamp)
                        details_text += f"{stage_emoji} {time_str}: {details} ({progress}%)\n"
                
                details_text += "\n"
            
            # Related transaction information
            if refund.transaction_id:
                details_text += f"🔗 **Related Transaction:** `{refund.transaction_id}`\n"
            if refund.cashout_id:
                details_text += f"🏦 **Related Cashout:** `{refund.cashout_id}`\n"
            if refund.escrow_id:
                details_text += f"⚖️ **Related Escrow:** `{refund.escrow_id}`\n"
            
            # Completion information
            if refund.completed_at:
                details_text += f"✅ **Completed:** {UserRefundDashboard._format_date(refund.completed_at.isoformat())}\n"
            elif refund.failed_at:
                details_text += f"❌ **Failed:** {UserRefundDashboard._format_date(refund.failed_at.isoformat())}\n"
                if refund.error_message:
                    details_text += f"📄 **Error:** {refund.error_message[:100]}...\n"
            
            # Create action keyboard
            keyboard = []
            
            # Real-time tracking actions
            if detailed_progress and detailed_progress.get("current_stage") in ["user_notified", "confirming"]:
                keyboard.append([
                    InlineKeyboardButton("✅ Confirm Receipt", callback_data=f"refund_confirm_{refund_id}")
                ])
            
            # General actions
            keyboard.append([
                InlineKeyboardButton("🔄 Refresh Status", callback_data=f"refund_detail_{refund_id}"),
                InlineKeyboardButton("📊 Track Progress", callback_data=f"refund_track_{refund_id}")
            ])
            
            # Navigation
            keyboard.append([
                InlineKeyboardButton("⬅️ Back to History", callback_data="refund_view_all"),
                InlineKeyboardButton("🏠 Main Menu", callback_data="refund_main_menu")
            ])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await safe_edit_message_text(
                update.callback_query,
                details_text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
            
        except Exception as e:
            logger.error(f"❌ Error displaying refund details for {refund_id}: {e}")
            await safe_edit_message_text(
                update.callback_query,
                f"❌ Error loading details for refund `{refund_id}`. Please try again.",
                parse_mode=ParseMode.MARKDOWN
            )
    
    @staticmethod
    async def show_filter_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Display refund filtering options"""
        filter_text = """🔧 **Refund Filters**

Choose how you'd like to view your refunds:

📋 **By Status:**
• All refunds
• Completed refunds only
• Pending refunds only 
• Failed refunds only

📅 **By Time:**
• Recent (last 30 days)
• All time

💰 **By Type:**
• All refund types
• Cashout failed refunds
• Escrow refunds
• Dispute refunds
• Admin refunds
"""
        
        keyboard = [
            [
                InlineKeyboardButton("📋 All Refunds", callback_data="refund_history_all_1"),
                InlineKeyboardButton("✅ Completed", callback_data="refund_history_completed_1")
            ],
            [
                InlineKeyboardButton("🔄 Pending", callback_data="refund_history_pending_1"),
                InlineKeyboardButton("❌ Failed", callback_data="refund_history_failed_1")
            ],
            [
                InlineKeyboardButton("📅 Recent (30 days)", callback_data="refund_history_recent_1"),
                InlineKeyboardButton("🏦 Cashout Failed", callback_data="refund_type_cashout_failed")
            ],
            [
                InlineKeyboardButton("⚖️ Escrow Refunds", callback_data="refund_type_escrow"),
                InlineKeyboardButton("🛡️ Dispute Refunds", callback_data="refund_type_dispute")
            ],
            [
                InlineKeyboardButton("⬅️ Back to Menu", callback_data="refund_main_menu")
            ]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await safe_edit_message_text(
            update.callback_query,
            filter_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    
    @staticmethod
    async def show_refund_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Display comprehensive refund statistics for user"""
        try:
            user = await get_user_from_update(update)
            if not user:
                return
            
            # Get comprehensive stats
            with SessionLocal() as session:
                # Basic counts
                total_refunds = session.query(Refund).filter(Refund.user_id == user.id).count()
                completed_refunds = session.query(Refund).filter(
                    Refund.user_id == user.id,
                    Refund.status == RefundStatus.COMPLETED.value
                ).count()
                pending_refunds = session.query(Refund).filter(
                    Refund.user_id == user.id,
                    Refund.status == RefundStatus.PENDING.value
                ).count()
                failed_refunds = session.query(Refund).filter(
                    Refund.user_id == user.id,
                    Refund.status == RefundStatus.FAILED.value
                ).count()
                
                # Amount statistics
                from sqlalchemy import func as sql_func
                total_amount_result = session.query(sql_func.sum(Refund.amount)).filter(
                    Refund.user_id == user.id,
                    Refund.status == RefundStatus.COMPLETED.value
                ).scalar()
                total_refunded = float(total_amount_result) if total_amount_result else 0.0
                
                # By type statistics
                refund_types = session.query(
                    Refund.refund_type,
                    sql_func.count(Refund.id).label('count'),
                    sql_func.sum(Refund.amount).label('amount')
                ).filter(
                    Refund.user_id == user.id
                ).group_by(Refund.refund_type).all()
                
                # Recent activity (last 30 days)
                recent_date = datetime.utcnow() - timedelta(days=30)
                recent_refunds = session.query(Refund).filter(
                    Refund.user_id == user.id,
                    Refund.created_at >= recent_date
                ).count()
            
            # Calculate success rate
            success_rate = (completed_refunds / total_refunds * 100) if total_refunds > 0 else 0
            
            # Build stats message
            stats_text = f"""📊 **Your Refund Statistics**

🔢 **Overview:**
• Total Refunds: {total_refunds}
• Completed: ✅ {completed_refunds}
• Pending: 🔄 {pending_refunds}
• Failed: ❌ {failed_refunds}
• Success Rate: {success_rate:.1f}%

💰 **Financial Summary:**
• Total Refunded: ${total_refunded:,.2f} USD
• Average Refund: ${total_refunded/completed_refunds if completed_refunds > 0 else 0:,.2f} USD

📅 **Recent Activity:**
• Last 30 Days: {recent_refunds} refunds

📋 **By Refund Type:**
"""
            
            # Add type breakdown
            for refund_type, count, amount in refund_types:
                type_display = UserRefundDashboard._get_refund_type_display(refund_type)
                amount_val = float(amount) if amount else 0
                stats_text += f"• {type_display}: {count} ({amount_val:,.2f} USD)\n"
            
            # Add active tracking info
            active_refunds = real_time_refund_tracker.get_user_active_refunds(user.id)
            if active_refunds:
                stats_text += f"\n🔄 **Currently Tracking:** {len(active_refunds)} active refunds"
            
            keyboard = [
                [
                    InlineKeyboardButton("📋 View History", callback_data="refund_view_all"),
                    InlineKeyboardButton("🔄 Active Refunds", callback_data="refund_view_active")
                ],
                [
                    InlineKeyboardButton("🔄 Refresh Stats", callback_data="refund_stats"),
                    InlineKeyboardButton("⬅️ Back to Menu", callback_data="refund_main_menu")
                ]
            ]
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await safe_edit_message_text(
                update.callback_query,
                stats_text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
            
        except Exception as e:
            logger.error(f"❌ Error displaying refund stats: {e}")
            await safe_edit_message_text(
                update.callback_query,
                "❌ Error loading refund statistics. Please try again."
            )
    
    @staticmethod
    async def confirm_refund_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE, refund_id: str):
        """Handle user confirmation of refund receipt"""
        try:
            user = await get_user_from_update(update)
            if not user:
                return
            
            # Confirm with real-time tracker
            success = await real_time_refund_tracker.user_confirm_refund(refund_id, user.id)
            
            if success:
                await safe_answer_callback_query(
                    update.callback_query,
                    "✅ Refund receipt confirmed! Thank you.",
                    show_alert=True
                )
                
                # Refresh the detail view
                await UserRefundDashboard.show_refund_details(update, context, refund_id)
            else:
                await safe_answer_callback_query(
                    update.callback_query,
                    "❌ Could not confirm refund. Please try again or contact support.",
                    show_alert=True
                )
                
        except Exception as e:
            logger.error(f"❌ Error confirming refund receipt for {refund_id}: {e}")
            await safe_answer_callback_query(
                update.callback_query,
                "❌ Error confirming refund. Please try again.",
                show_alert=True
            )
    
    # Helper methods for formatting and display
    
    @staticmethod
    def _get_status_emoji(status: str) -> str:
        """Get emoji for refund status"""
        emoji_map = {
            "pending": "🔄",
            "completed": "✅", 
            "failed": "❌",
            "cancelled": "🚫"
        }
        return emoji_map.get(status.lower(), "❓")
    
    @staticmethod
    def _get_stage_emoji(stage: str) -> str:
        """Get emoji for progress stage"""
        emoji_map = {
            "initiated": "🚀",
            "validating": "🔍",
            "processing": "⚙️",
            "wallet_crediting": "💳",
            "wallet_credited": "✅",
            "user_notifying": "📨",
            "user_notified": "📬",
            "confirming": "⏳",
            "confirmed": "✅",
            "completed": "🏁",
            "failed": "❌",
            "cancelled": "🚫"
        }
        return emoji_map.get(stage.lower(), "📋")
    
    @staticmethod
    def _get_stage_display(stage: str) -> str:
        """Get user-friendly display text for stage"""
        display_map = {
            "initiated": "Started",
            "validating": "Validating",
            "processing": "Processing",
            "wallet_crediting": "Crediting Wallet",
            "wallet_credited": "Wallet Credited",
            "user_notifying": "Sending Notifications",
            "user_notified": "Notifications Sent",
            "confirming": "Awaiting Confirmation",
            "confirmed": "Confirmed",
            "completed": "Completed",
            "failed": "Failed",
            "cancelled": "Cancelled"
        }
        return display_map.get(stage.lower(), stage.title())
    
    @staticmethod
    def _get_refund_type_display(refund_type: str) -> str:
        """Get user-friendly display text for refund type"""
        display_map = {
            "cashout_failed": "💳 Cashout Failed",
            "escrow_refund": "⚖️ Escrow Refund",
            "dispute_refund": "🛡️ Dispute Resolution",
            "admin_refund": "👤 Admin Refund",
            "error_refund": "⚠️ System Error"
        }
        return display_map.get(refund_type.lower(), refund_type.title())
    
    @staticmethod
    def _format_date(iso_string: str) -> str:
        """Format ISO date string to user-friendly format"""
        try:
            if not iso_string:
                return "Unknown"
            
            dt = datetime.fromisoformat(iso_string.replace('Z', '+00:00'))
            now = datetime.utcnow()
            diff = now - dt.replace(tzinfo=None)
            
            if diff.days == 0:
                if diff.seconds < 3600:  # Less than 1 hour
                    minutes = diff.seconds // 60
                    return f"{minutes}m ago"
                else:  # Less than 24 hours
                    hours = diff.seconds // 3600
                    return f"{hours}h ago"
            elif diff.days == 1:
                return "Yesterday"
            elif diff.days < 7:
                return f"{diff.days}d ago"
            else:
                return dt.strftime("%b %d, %Y")
                
        except Exception as e:
            logger.error(f"❌ Error formatting date {iso_string}: {e}")
            return "Unknown"


# Global dashboard instance
user_refund_dashboard = UserRefundDashboard()