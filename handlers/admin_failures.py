"""
Admin Failures Handler
Telegram bot interface for managing failed transactions requiring admin intervention
"""

import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from utils.admin_security import admin_required
from services.admin_failure_service import admin_failure_service
from services.admin_email_alerts import admin_email_service
from utils.database_pool_manager import database_pool
from utils.helpers import format_amount
from utils.callback_utils import safe_answer_callback_query

logger = logging.getLogger(__name__)


class AdminFailuresHandler:
    """Handler for admin management of failed transactions"""
    
    ITEMS_PER_PAGE = 8  # Reasonable number for mobile screens
    
    @staticmethod
    @admin_required
    async def show_failures_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show the main failures management dashboard"""
        try:
            with database_pool.get_session("admin_failures_dashboard") as session:
                # Get pending failures overview
                failures_data = admin_failure_service.get_pending_failures(
                    session, limit=AdminFailuresHandler.ITEMS_PER_PAGE, offset=0
                )
                
                summary = failures_data.get('summary', {})
                total_failures = summary.get('total_failures', 0)
                total_amount = summary.get('total_amount', 0)
                high_priority = summary.get('high_priority_count', 0)
                currency_breakdown = summary.get('currency_breakdown', {})
                
                # Build overview message
                message = f"""
🚨 **Failed Transactions Dashboard**

📊 **Overview**:
• Total pending: {total_failures} transactions
• High priority: {high_priority} transactions
• Total value: ${total_amount:,.2f}

💰 **Currency Breakdown**:
"""
                
                # Add currency breakdown
                for currency, data in currency_breakdown.items():
                    count = data['count']
                    amount = data['total_amount']
                    message += f"• {currency}: {count} transactions (${amount:,.2f})\n"
                
                if not currency_breakdown:
                    message += "• No pending failures ✅\n"
                
                # Build keyboard
                keyboard = []
                
                if total_failures > 0:
                    keyboard.extend([
                        [
                            InlineKeyboardButton("📋 View All Failures", 
                                               callback_data="admin_failures_list:0"),
                            InlineKeyboardButton("🔥 High Priority", 
                                               callback_data="admin_failures_priority")
                        ],
                        [
                            InlineKeyboardButton("📊 Statistics", 
                                               callback_data="admin_failures_stats"),
                            InlineKeyboardButton("🔄 Refresh", 
                                               callback_data="admin_failures_dashboard")
                        ]
                    ])
                else:
                    keyboard.append([
                        InlineKeyboardButton("🔄 Refresh", 
                                           callback_data="admin_failures_dashboard")
                    ])
                
                keyboard.append([
                    InlineKeyboardButton("⚙️ Settings", 
                                       callback_data="admin_failures_settings"),
                    InlineKeyboardButton("🔙 Back to Admin", 
                                       callback_data="admin_main")
                ])
                
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
            logger.error(f"Error showing failures dashboard: {e}")
            await update.effective_message.reply_text(
                f"❌ Error loading dashboard: {str(e)}"
            )
    
    @staticmethod
    @admin_required
    async def show_failures_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show paginated list of failed transactions"""
        try:
            # Extract page offset from callback data
            callback_data = update.callback_query.data if update.callback_query else ""
            if ":" in callback_data:
                offset = int(callback_data.split(":")[1])
            else:
                offset = 0
            
            with database_pool.get_session("admin_failures_list") as session:
                failures_data = admin_failure_service.get_pending_failures(
                    session, 
                    limit=AdminFailuresHandler.ITEMS_PER_PAGE, 
                    offset=offset
                )
                
                failures = failures_data.get('failures', [])
                pagination = failures_data.get('pagination', {})
                
                if not failures:
                    message = "✅ No failed transactions requiring admin attention"
                    keyboard = [[
                        InlineKeyboardButton("🔙 Back to Dashboard", 
                                           callback_data="admin_failures_dashboard")
                    ]]
                else:
                    # Build message with failures list
                    current_page = pagination.get('current_page', 1)
                    total_pages = pagination.get('total_pages', 1)
                    
                    message = f"🚨 **Failed Transactions** (Page {current_page}/{total_pages})\n\n"
                    
                    for i, failure in enumerate(failures, 1):
                        cashout_id = failure.get('id', 'Unknown')
                        user_name = failure.get('user_name', 'Unknown User')
                        username = failure.get('user_username')
                        amount_str = failure.get('formatted_amount', 'Unknown')
                        hours_pending = failure.get('hours_pending', 0)
                        error_msg = failure.get('error_message', 'No error message')
                        
                        # Truncate error message for display
                        if len(error_msg) > 50:
                            error_msg = error_msg[:47] + "..."
                        
                        # Add priority indicator
                        priority_icon = "🔥" if failure.get('is_high_priority') else "📄"
                        
                        # Format user info
                        user_display = user_name
                        if username:
                            user_display += f" (@{username})"
                        
                        message += f"{priority_icon} **{cashout_id[:8]}**\n"
                        message += f"👤 {user_display}\n"
                        message += f"💰 {amount_str}\n"
                        message += f"⏰ {hours_pending:.1f}h ago\n"
                        message += f"❌ {error_msg}\n\n"
                    
                    # Build keyboard with action buttons for each failure
                    keyboard = []
                    
                    # Add transaction action rows (2 per row for compact layout)
                    for i in range(0, len(failures), 2):
                        row = []
                        for j in range(2):
                            if i + j < len(failures):
                                failure = failures[i + j]
                                cashout_id = failure.get('id')
                                display_id = cashout_id[:6] if cashout_id else 'N/A'
                                row.append(InlineKeyboardButton(
                                    f"⚙️ {display_id}", 
                                    callback_data=f"admin_failure_detail:{cashout_id}"
                                ))
                        keyboard.append(row)
                    
                    # Navigation buttons
                    nav_row = []
                    if pagination.get('has_prev'):
                        prev_offset = pagination.get('prev_offset', 0)
                        nav_row.append(InlineKeyboardButton(
                            "⬅️ Previous", 
                            callback_data=f"admin_failures_list:{prev_offset}"
                        ))
                    
                    if pagination.get('has_next'):
                        next_offset = pagination.get('next_offset', 0)
                        nav_row.append(InlineKeyboardButton(
                            "Next ➡️", 
                            callback_data=f"admin_failures_list:{next_offset}"
                        ))
                    
                    if nav_row:
                        keyboard.append(nav_row)
                    
                    # Control buttons
                    keyboard.append([
                        InlineKeyboardButton("🔄 Refresh", 
                                           callback_data=f"admin_failures_list:{offset}"),
                        InlineKeyboardButton("🔙 Dashboard", 
                                           callback_data="admin_failures_dashboard")
                    ])
                
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
            logger.error(f"Error showing failures list: {e}")
            await update.effective_message.reply_text(
                f"❌ Error loading failures: {str(e)}"
            )
    
    @staticmethod
    @admin_required
    async def show_failure_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show detailed view of a specific failed transaction"""
        try:
            callback_data = update.callback_query.data
            cashout_id = callback_data.split(":")[1]
            
            with database_pool.get_session("admin_failure_detail") as session:
                details = admin_failure_service.get_failure_details(session, cashout_id)
                
                if not details:
                    await safe_answer_callback_query(update.callback_query, "❌ Transaction not found")
                    return
                
                cashout = details['cashout']
                user = details['user']
                
                # Format detailed information
                message = f"""
🚨 **Transaction Details**

🆔 **ID**: `{cashout['id']}`
👤 **User**: {user['name']}
"""
                
                if user.get('username'):
                    message += f"📱 **Username**: @{user['username']}\n"
                
                message += f"""
💰 **Amount**: {format_amount(cashout['amount'], cashout['currency'])}
🏦 **Destination**: {cashout['destination_type']} ({cashout['destination_id'][:10]}...)
📅 **Created**: {datetime.fromisoformat(cashout['created_at']).strftime('%Y-%m-%d %H:%M')}
🔄 **Retries**: {cashout['retry_count']}

❌ **Error**: {cashout['error_message'] or 'No error message'}
"""
                
                if cashout.get('failure_reason'):
                    message += f"📋 **Reason**: {cashout['failure_reason']}\n"
                
                if cashout.get('admin_notes'):
                    message += f"📝 **Admin Notes**: {cashout['admin_notes']}\n"
                
                # Get current status from details
                funds_status = None
                for wh in details.get('wallet_holds', []):
                    if wh.get('amount') == cashout['amount']:
                        funds_status = wh.get('status')
                        break
                
                if funds_status:
                    message += f"💳 **Funds Status**: {funds_status.replace('_', ' ').title()}\n"
                
                # Build action keyboard
                keyboard = []
                
                # Check action eligibility
                can_retry = True  # Simplified for now
                can_refund = funds_status in ['held', 'failed_held'] if funds_status else False
                
                # Action buttons row
                action_row = []
                if can_retry:
                    action_row.append(InlineKeyboardButton(
                        "🔄 Retry", 
                        callback_data=f"admin_failure_action:retry:{cashout_id}"
                    ))
                
                if can_refund:
                    action_row.append(InlineKeyboardButton(
                        "💰 Refund", 
                        callback_data=f"admin_failure_action:refund:{cashout_id}"
                    ))
                
                action_row.append(InlineKeyboardButton(
                    "❌ Decline", 
                    callback_data=f"admin_failure_action:decline:{cashout_id}"
                ))
                
                if action_row:
                    keyboard.append(action_row)
                
                # Management buttons
                keyboard.append([
                    InlineKeyboardButton("📧 Send Email Alert", 
                                       callback_data=f"admin_failure_email:{cashout_id}"),
                    InlineKeyboardButton("📝 Add Notes", 
                                       callback_data=f"admin_failure_notes:{cashout_id}")
                ])
                
                # Navigation
                keyboard.append([
                    InlineKeyboardButton("🔄 Refresh", 
                                       callback_data=f"admin_failure_detail:{cashout_id}"),
                    InlineKeyboardButton("📋 Back to List", 
                                       callback_data="admin_failures_list:0")
                ])
                
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await update.callback_query.edit_message_text(
                    message, 
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
                
        except Exception as e:
            logger.error(f"Error showing failure detail: {e}")
            await safe_answer_callback_query(update.callback_query, f"❌ Error loading details: {str(e)}")
    
    @staticmethod
    @admin_required
    async def handle_failure_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle admin actions on failed transactions"""
        try:
            callback_data = update.callback_query.data
            parts = callback_data.split(":")
            action = parts[1]  # retry, refund, decline
            cashout_id = parts[2]
            
            # Show confirmation dialog
            with database_pool.get_session("admin_failure_action") as session:
                details = admin_failure_service.get_failure_details(session, cashout_id)
                
                if not details:
                    await safe_answer_callback_query(update.callback_query, "❌ Transaction not found")
                    return
                
                cashout = details['cashout']
                user = details['user']
                amount_str = format_amount(cashout['amount'], cashout['currency'])
                
                # Build confirmation message
                action_descriptions = {
                    'retry': {
                        'icon': '🔄',
                        'title': 'Retry Transaction',
                        'description': 'Queue this transaction for automatic retry',
                        'warning': 'This will attempt to process the cashout again.'
                    },
                    'refund': {
                        'icon': '💰',
                        'title': 'Refund to Wallet',
                        'description': 'Return funds to user\'s available balance',
                        'warning': 'This action will credit the user\'s wallet immediately.'
                    },
                    'decline': {
                        'icon': '❌',
                        'title': 'Decline Transaction',
                        'description': 'Permanently decline this transaction',
                        'warning': 'Funds will remain frozen for manual review.'
                    }
                }
                
                action_info = action_descriptions.get(action, {})
                
                message = f"""
{action_info.get('icon', '⚠️')} **{action_info.get('title', 'Confirm Action')}**

🆔 **Transaction**: `{cashout_id[:8]}...`
👤 **User**: {user['name']}
💰 **Amount**: {amount_str}

📋 **Action**: {action_info.get('description', action.title())}

⚠️ **Warning**: {action_info.get('warning', 'This action cannot be undone.')}

Are you sure you want to proceed?
"""
                
                keyboard = [
                    [
                        InlineKeyboardButton(f"✅ Confirm {action.title()}", 
                                           callback_data=f"admin_failure_confirm:{action}:{cashout_id}"),
                        InlineKeyboardButton("❌ Cancel", 
                                           callback_data=f"admin_failure_detail:{cashout_id}")
                    ]
                ]
                
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await update.callback_query.edit_message_text(
                    message, 
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
                
        except Exception as e:
            logger.error(f"Error handling failure action: {e}")
            await safe_answer_callback_query(update.callback_query, f"❌ Error: {str(e)}")
    
    @staticmethod
    @admin_required
    async def confirm_failure_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Execute confirmed admin action on failed transaction"""
        try:
            callback_data = update.callback_query.data
            parts = callback_data.split(":")
            action = parts[1]
            cashout_id = parts[2]
            
            admin_user_id = update.effective_user.id
            
            # Execute action using admin failure service
            with database_pool.get_session("admin_confirm_action") as session:
                if action == 'retry':
                    success, message = admin_failure_service.retry_transaction(
                        session, cashout_id, admin_user_id, 
                        f"Manual retry via Telegram by admin {admin_user_id}"
                    )
                    
                elif action == 'refund':
                    success, message = admin_failure_service.refund_transaction(
                        session, cashout_id, admin_user_id,
                        f"Manual refund via Telegram by admin {admin_user_id}"
                    )
                    
                elif action == 'decline':
                    # For decline, we need admin notes
                    success, message = admin_failure_service.decline_transaction(
                        session, cashout_id, admin_user_id,
                        f"Declined via Telegram by admin {admin_user_id} - review required"
                    )
                    
                else:
                    success = False
                    message = f"Unknown action: {action}"
                
                # Show result
                if success:
                    result_message = f"✅ **Action Completed**\n\n{message}"
                    result_icon = "✅"
                    
                    # Log the admin action
                    logger.info(f"Admin {admin_user_id} successfully executed {action} on cashout {cashout_id}")
                    
                else:
                    result_message = f"❌ **Action Failed**\n\n{message}"
                    result_icon = "❌"
                    
                    logger.error(f"Admin {admin_user_id} failed to execute {action} on cashout {cashout_id}: {message}")
                
                keyboard = [
                    [
                        InlineKeyboardButton("📋 Back to List", 
                                           callback_data="admin_failures_list:0"),
                        InlineKeyboardButton("🏠 Dashboard", 
                                           callback_data="admin_failures_dashboard")
                    ]
                ]
                
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await update.callback_query.edit_message_text(
                    result_message, 
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
                
                # Send confirmation via callback query answer
                await safe_answer_callback_query(update.callback_query, f"{result_icon} {action.title()} {'completed' if success else 'failed'}")
                
        except Exception as e:
            logger.error(f"Error confirming failure action: {e}")
            await safe_answer_callback_query(update.callback_query, f"❌ Error executing action: {str(e)}")
    
    @staticmethod
    @admin_required
    async def send_failure_email_alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Send email alert with action buttons for a specific failure"""
        try:
            callback_data = update.callback_query.data
            cashout_id = callback_data.split(":")[1]
            
            with database_pool.get_session("admin_failure_email") as session:
                details = admin_failure_service.get_failure_details(session, cashout_id)
                
                if not details:
                    await safe_answer_callback_query(update.callback_query, "❌ Transaction not found")
                    return
                
                cashout = details['cashout']
                user = details['user']
                
                # Send email alert using enhanced admin_email_service
                success = await admin_email_service.send_failure_alert_with_actions(
                    cashout_id=cashout_id,
                    amount=cashout['amount'],
                    currency=cashout['currency'],
                    user_name=user['name'],
                    error_message=cashout['error_message'],
                    admin_user_id=update.effective_user.id
                )
                
                if success:
                    message = f"✅ **Email Alert Sent**\n\nSecure action email sent for transaction `{cashout_id[:8]}...`"
                    icon = "✅"
                else:
                    message = f"❌ **Email Failed**\n\nFailed to send email alert for transaction `{cashout_id[:8]}...`"
                    icon = "❌"
                
                keyboard = [[
                    InlineKeyboardButton("🔙 Back to Details", 
                                       callback_data=f"admin_failure_detail:{cashout_id}")
                ]]
                
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await update.callback_query.edit_message_text(
                    message, 
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
                
                await safe_answer_callback_query(update.callback_query, f"{icon} Email {'sent' if success else 'failed'}")
                
        except Exception as e:
            logger.error(f"Error sending failure email alert: {e}")
            await safe_answer_callback_query(update.callback_query, f"❌ Error sending email: {str(e)}")
    
    @staticmethod
    @admin_required
    async def show_failures_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show statistics about failed transactions"""
        try:
            with database_pool.get_session("admin_failures_stats") as session:
                # Get comprehensive stats
                failures_data = admin_failure_service.get_pending_failures(
                    session, limit=1000, offset=0  # Get all for stats
                )
                
                summary = failures_data.get('summary', {})
                failures = failures_data.get('failures', [])
                
                # Calculate additional statistics
                total_failures = len(failures)
                total_amount = summary.get('total_amount', 0)
                high_priority = summary.get('high_priority_count', 0)
                
                # Time-based analysis
                recent_failures = [f for f in failures if f.get('hours_pending', 0) < 24]
                old_failures = [f for f in failures if f.get('hours_pending', 0) >= 48]
                
                # Error analysis
                error_types = {}
                for failure in failures:
                    error = failure.get('error_message', 'Unknown error')
                    # Simplified error categorization
                    if 'timeout' in error.lower():
                        error_types['Timeout'] = error_types.get('Timeout', 0) + 1
                    elif 'network' in error.lower() or 'connection' in error.lower():
                        error_types['Network'] = error_types.get('Network', 0) + 1
                    elif 'insufficient' in error.lower():
                        error_types['Insufficient Funds'] = error_types.get('Insufficient Funds', 0) + 1
                    else:
                        error_types['Other'] = error_types.get('Other', 0) + 1
                
                message = f"""
📊 **Failure Statistics**

📈 **Overview**:
• Total pending: {total_failures}
• High priority: {high_priority}
• Total value: ${total_amount:,.2f}

⏰ **Time Analysis**:
• Recent (< 24h): {len(recent_failures)}
• Old (> 48h): {len(old_failures)}
• Average age: {sum(f.get('hours_pending', 0) for f in failures) / max(len(failures), 1):.1f}h

🔍 **Error Types**:
"""
                
                for error_type, count in error_types.items():
                    percentage = (count / max(total_failures, 1)) * 100
                    message += f"• {error_type}: {count} ({percentage:.1f}%)\n"
                
                message += "\n💰 **Currency Breakdown**:\n"
                currency_breakdown = summary.get('currency_breakdown', {})
                for currency, data in currency_breakdown.items():
                    count = data['count']
                    amount = data['total_amount']
                    percentage = (count / max(total_failures, 1)) * 100
                    message += f"• {currency}: {count} ({percentage:.1f}%) - ${amount:,.2f}\n"
                
                keyboard = [
                    [
                        InlineKeyboardButton("🔄 Refresh Stats", 
                                           callback_data="admin_failures_stats"),
                        InlineKeyboardButton("📋 View List", 
                                           callback_data="admin_failures_list:0")
                    ],
                    [
                        InlineKeyboardButton("🔙 Dashboard", 
                                           callback_data="admin_failures_dashboard")
                    ]
                ]
                
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await update.callback_query.edit_message_text(
                    message, 
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
                
        except Exception as e:
            logger.error(f"Error showing failures stats: {e}")
            await safe_answer_callback_query(update.callback_query, f"❌ Error loading stats: {str(e)}")


# Export handler instance
admin_failures_handler = AdminFailuresHandler()