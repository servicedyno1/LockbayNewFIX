"""
Admin Refund Monitoring Dashboard
Real-time monitoring and management interface for refund operations
"""

import logging
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from utils.refund_monitor import refund_monitor
from utils.callback_utils import safe_answer_callback_query
from models import RefundStatus
from database import async_managed_session
from sqlalchemy import select
from models import Refund

logger = logging.getLogger(__name__)


class AdminRefundDashboard:
    """Admin dashboard for comprehensive refund monitoring"""
    
    @staticmethod
    async def show_refund_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Display the main refund monitoring dashboard"""
        try:
            # Get current metrics
            metrics = refund_monitor.get_hourly_metrics(24)
            
            # Get recent alerts
            alerts = refund_monitor.check_alert_conditions()
            
            # Build dashboard message
            dashboard_text = "🔄 **REFUND MONITORING DASHBOARD**\n\n"
            
            # Overview stats
            total_refunds = sum(metrics.get("refund_counts", {}).values())
            total_amount = sum(metrics.get("refund_amounts", {}).values())
            
            dashboard_text += "📊 **Last 24 Hours Overview:**\n"
            dashboard_text += f"• Total Refunds: {total_refunds}\n"
            dashboard_text += f"• Total Amount: ${total_amount:,.2f}\n\n"
            
            # Refund counts by type
            if metrics.get("refund_counts"):
                dashboard_text += "📈 **Refunds by Type:**\n"
                for refund_type, count in metrics["refund_counts"].items():
                    amount = metrics["refund_amounts"].get(refund_type, 0)
                    dashboard_text += f"• {refund_type}: {count} (${amount:,.2f})\n"
                dashboard_text += "\n"
            
            # Success rates
            if metrics.get("success_rates"):
                dashboard_text += "✅ **Success Rates:**\n"
                for refund_type, rates in metrics["success_rates"].items():
                    completed_rate = rates.get("completed", 0)
                    rates.get("failed", 0)
                    dashboard_text += f"• {refund_type}: {completed_rate:.1f}% success\n"
                dashboard_text += "\n"
            
            # Active alerts
            if alerts:
                dashboard_text += "🚨 **Active Alerts:**\n"
                for alert in alerts:
                    severity_emoji = "🔴" if alert["severity"] == "critical" else "🟡"
                    dashboard_text += f"{severity_emoji} {alert['message']}\n"
                dashboard_text += "\n"
            else:
                dashboard_text += "✅ **No Active Alerts**\n\n"
            
            # Generate timestamp
            dashboard_text += f"🕐 Updated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"
            
            # Create navigation keyboard
            keyboard = [
                [
                    InlineKeyboardButton("📊 Detailed Stats", callback_data="refund_detailed_stats"),
                    InlineKeyboardButton("🔍 Recent Refunds", callback_data="refund_recent_list")
                ],
                [
                    InlineKeyboardButton("⚠️ Failed Refunds", callback_data="refund_failed_list"),
                    InlineKeyboardButton("🔄 Refresh", callback_data="refund_dashboard_refresh")
                ],
                [
                    InlineKeyboardButton("⬅️ Back to Admin", callback_data="admin_main_menu")
                ]
            ]
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            if update.callback_query:
                await update.callback_query.edit_message_text(
                    dashboard_text,
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
            else:
                await update.message.reply_text(
                    dashboard_text,
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
                
        except Exception as e:
            logger.error(f"Error displaying refund dashboard: {e}")
            error_text = "❌ Error loading refund dashboard. Please try again."
            
            if update.callback_query:
                await update.callback_query.edit_message_text(error_text)
            else:
                await update.message.reply_text(error_text)
    
    @staticmethod
    async def show_detailed_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show detailed refund statistics"""
        try:
            from sqlalchemy import func
            async with async_managed_session() as session:
                # Get comprehensive stats
                now = datetime.utcnow()
                
                # Last 7 days stats
                week_ago = now - timedelta(days=7)
                weekly_stats_stmt = (
                    select(
                        Refund.refund_type,
                        Refund.status,
                        func.count(Refund.id),
                        func.sum(Refund.amount)
                    )
                    .where(Refund.created_at >= week_ago)
                    .group_by(Refund.refund_type, Refund.status)
                )
                weekly_stats_result = await session.execute(weekly_stats_stmt)
                weekly_stats = weekly_stats_result.fetchall()
                
                # Processing time stats (if we had this data)
                
                stats_text = "📊 **DETAILED REFUND STATISTICS**\n\n"
                stats_text += "📅 **Last 7 Days Breakdown:**\n\n"
                
                # Organize stats by type
                type_stats = {}
                for refund_type, status, count, total_amount in weekly_stats:
                    if refund_type not in type_stats:
                        type_stats[refund_type] = {}
                    type_stats[refund_type][status] = {
                        'count': count,
                        'amount': float(total_amount) if total_amount else 0
                    }
                
                for refund_type, statuses in type_stats.items():
                    stats_text += f"🔹 **{refund_type.upper()}:**\n"
                    total_type_count = sum(s['count'] for s in statuses.values())
                    total_type_amount = sum(s['amount'] for s in statuses.values())
                    
                    stats_text += f"   Total: {total_type_count} refunds (${total_type_amount:,.2f})\n"
                    
                    for status, data in statuses.items():
                        percentage = (data['count'] / total_type_count * 100) if total_type_count > 0 else 0
                        stats_text += f"   • {status}: {data['count']} ({percentage:.1f}%)\n"
                    stats_text += "\n"
                
                keyboard = [
                    [InlineKeyboardButton("⬅️ Back to Dashboard", callback_data="refund_dashboard_refresh")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await update.callback_query.edit_message_text(
                    stats_text,
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
                
        except Exception as e:
            logger.error(f"Error showing detailed stats: {e}")
            await update.callback_query.edit_message_text(
                "❌ Error loading detailed statistics."
            )
    
    @staticmethod
    async def show_recent_refunds(update: Update, context: ContextTypes.DEFAULT_TYPE, limit: int = 10):
        """Show list of recent refunds"""
        try:

            async with async_managed_session() as session:
                recent_refunds_stmt = (
                    select(Refund)
                    .order_by(Refund.created_at.desc())
                    .limit(limit)
                )
                recent_refunds_result = await session.execute(recent_refunds_stmt)
                recent_refunds = recent_refunds_result.scalars().all()
                
                refunds_text = f"🔍 **RECENT {limit} REFUNDS**\n\n"
                
                if not recent_refunds:
                    refunds_text += "No recent refunds found.\n"
                else:
                    for refund in recent_refunds:
                        status_emoji = "✅" if refund.status == "completed" else "🔄" if refund.status == "processing" else "❌"
                        refunds_text += (
                            f"{status_emoji} **{refund.refund_id}**\n"
                            f"   Type: {refund.refund_type}\n"
                            f"   Amount: ${refund.amount:.2f}\n"
                            f"   Status: {refund.status}\n"
                            f"   Created: {refund.created_at.strftime('%Y-%m-%d %H:%M')}\n\n"
                        )
                
                keyboard = [
                    [InlineKeyboardButton("⬅️ Back to Dashboard", callback_data="refund_dashboard_refresh")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await update.callback_query.edit_message_text(
                    refunds_text,
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
                
        except Exception as e:
            logger.error(f"Error showing recent refunds: {e}")
            await update.callback_query.edit_message_text(
                "❌ Error loading recent refunds."
            )
    
    @staticmethod
    async def show_failed_refunds(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show failed refunds that may need attention"""
        try:

            async with async_managed_session() as session:
                failed_refunds_stmt = (
                    select(Refund)
                    .where(Refund.status == RefundStatus.FAILED.value)
                    .order_by(Refund.created_at.desc())
                    .limit(20)
                )
                failed_refunds_result = await session.execute(failed_refunds_stmt)
                failed_refunds = failed_refunds_result.scalars().all()
                
                failed_text = "❌ **FAILED REFUNDS**\n\n"
                
                if not failed_refunds:
                    failed_text += "✅ No failed refunds found!\n"
                else:
                    failed_text += f"Found {len(failed_refunds)} failed refunds:\n\n"
                    
                    for refund in failed_refunds:
                        failed_text += (
                            f"🔴 **{refund.refund_id}**\n"
                            f"   User: {refund.user_id}\n"
                            f"   Amount: ${refund.amount:.2f}\n"
                            f"   Type: {refund.refund_type}\n"
                            f"   Failed: {refund.failed_at.strftime('%Y-%m-%d %H:%M') if refund.failed_at else 'Unknown'}\n"
                            f"   Error: {refund.error_message[:100] if refund.error_message else 'No details'}...\n\n"
                        )
                
                keyboard = [
                    [InlineKeyboardButton("⬅️ Back to Dashboard", callback_data="refund_dashboard_refresh")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await update.callback_query.edit_message_text(
                    failed_text,
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
                
        except Exception as e:
            logger.error(f"Error showing failed refunds: {e}")
            await update.callback_query.edit_message_text(
                "❌ Error loading failed refunds."
            )


# Add these handlers to your admin.py file:
async def handle_refund_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle refund dashboard callback"""
    query = update.callback_query
    await safe_answer_callback_query(query)
    
    if query.data == "refund_dashboard_refresh":
        await AdminRefundDashboard.show_refund_dashboard(update, context)
    elif query.data == "refund_detailed_stats":
        await AdminRefundDashboard.show_detailed_stats(update, context)
    elif query.data == "refund_recent_list":
        await AdminRefundDashboard.show_recent_refunds(update, context)
    elif query.data == "refund_failed_list":
        await AdminRefundDashboard.show_failed_refunds(update, context)