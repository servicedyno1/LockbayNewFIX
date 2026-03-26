"""
Admin Retry Commands Handler
Comprehensive observability and control for the cashout retry system
"""

import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from utils.admin_security import is_admin_secure
from utils.callback_utils import safe_answer_callback_query, safe_edit_message_text
from services.cashout_retry_metrics import RetryMetricsService
from database import managed_session
from models import Cashout, CashoutStatus, CashoutFailureType

logger = logging.getLogger(__name__)

# ===== COMMAND HANDLERS =====

async def admin_retry_queue_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command: /admin_retry_queue - Inspect current retry queue"""
    user = update.effective_user
    if not user or not is_admin_secure(user.id):
        await update.message.reply_text("❌ Access denied. Admin access required.")
        return
    
    logger.info(f"ADMIN_RETRY_CMD: {user.id} accessed retry queue inspection")
    await handle_admin_retry_queue(update, context)

async def admin_force_retry_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command: /admin_force_retry <cashout_id> - Force immediate retry"""
    user = update.effective_user
    if not user or not is_admin_secure(user.id):
        await update.message.reply_text("❌ Access denied. Admin access required.")
        return
    
    if not context.args or len(context.args) != 1:
        await update.message.reply_text(
            "❌ Usage: /admin_force_retry <cashout_id>\n\n"
            "Example: /admin_force_retry CSH123456"
        )
        return
    
    cashout_id = context.args[0]
    logger.info(f"ADMIN_RETRY_CMD: {user.id} requesting force retry for {cashout_id}")
    await handle_admin_force_retry(update, context, cashout_id)

async def admin_force_refund_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command: /admin_force_refund <cashout_id> - Convert stuck retry to refund"""
    user = update.effective_user
    if not user or not is_admin_secure(user.id):
        await update.message.reply_text("❌ Access denied. Admin access required.")
        return
    
    if not context.args or len(context.args) != 1:
        await update.message.reply_text(
            "❌ Usage: /admin_force_refund <cashout_id>\n\n"
            "Example: /admin_force_refund CSH123456"
        )
        return
    
    cashout_id = context.args[0]
    logger.info(f"ADMIN_RETRY_CMD: {user.id} requesting force refund for {cashout_id}")
    await handle_admin_force_refund(update, context, cashout_id)

async def admin_retry_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command: /admin_retry_stats - Show comprehensive retry statistics"""
    user = update.effective_user
    if not user or not is_admin_secure(user.id):
        await update.message.reply_text("❌ Access denied. Admin access required.")
        return
    
    logger.info(f"ADMIN_RETRY_CMD: {user.id} accessed retry statistics")
    await handle_admin_retry_stats(update, context)

# ===== CALLBACK HANDLERS =====

async def handle_admin_retry_queue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current retry queue status and management options"""
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "🔄")
    
    try:
        # Get queue metrics
        metrics = await RetryMetricsService.get_queue_metrics()
        
        # Format queue overview
        message = f"""🔄 **Retry Queue Overview**

📊 **Current Status:**
• Ready to Retry: {metrics['ready_for_retry']} cashouts
• Scheduled Future: {metrics['pending_retries']} cashouts
• Technical Failures: {metrics['technical_failures']} cashouts
• Max Retries Reached: {metrics['max_retries_reached']} cashouts

⏱️ **Timing:**
• Next Retry: {metrics['next_retry_time']}
• Oldest Pending: {metrics['oldest_pending_duration']}
• Avg Wait Time: {metrics['average_retry_delay']}min

🎯 **Processing:**
• Queue Depth: {metrics['queue_depth']}/100 capacity
• Last Processed: {metrics['last_processing_time']}
• Success Rate: {metrics['retry_success_rate']}%"""

        # Create action buttons
        keyboard = [
            [
                InlineKeyboardButton("📋 View Details", callback_data="admin_retry_queue_details"),
                InlineKeyboardButton("🔄 Process Now", callback_data="admin_retry_force_process")
            ],
            [
                InlineKeyboardButton("📈 Statistics", callback_data="admin_retry_stats"),
                InlineKeyboardButton("⚠️ Failed Items", callback_data="admin_retry_failed_items")
            ],
            [
                InlineKeyboardButton("🔄 Refresh", callback_data="admin_retry_queue"),
                InlineKeyboardButton("🏠 Admin", callback_data="admin_main")
            ]
        ]
        
        if query:
            await safe_edit_message_text(
                query, message, parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await update.message.reply_text(
                message, parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            
    except Exception as e:
        logger.error(f"ADMIN_RETRY_CMD: Error showing retry queue: {e}")
        error_msg = "❌ Error loading retry queue. Please try again."
        if query:
            await safe_edit_message_text(query, error_msg)
        else:
            await update.message.reply_text(error_msg)

async def handle_admin_retry_queue_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show detailed queue items with individual actions"""
    query = update.callback_query
    await safe_answer_callback_query(query, "📋")
    
    try:
        # Get detailed queue items
        queue_items = await RetryMetricsService.get_queue_items(limit=10)
        
        message = "📋 **Retry Queue Details**\n\n"
        
        if not queue_items:
            message += "✅ No items in retry queue"
        else:
            for item in queue_items:
                status_emoji = "🔄" if item['ready_now'] else "⏳"
                message += f"""{status_emoji} **{item['cashout_id']}**
• Amount: ${item['amount']:.2f} {item['currency']}
• Type: {item['failure_type'].title()}
• Error: {item['error_code']}
• Retry #{item['retry_count']} - Next: {item['next_retry']}
• User: {item['user_id']}

"""

        keyboard = [
            [InlineKeyboardButton("🔄 Back to Queue", callback_data="admin_retry_queue")],
            [InlineKeyboardButton("🏠 Admin", callback_data="admin_main")]
        ]
        
        await safe_edit_message_text(
            query, message, parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
    except Exception as e:
        logger.error(f"ADMIN_RETRY_CMD: Error showing queue details: {e}")
        await safe_edit_message_text(query, "❌ Error loading queue details.")

async def handle_admin_retry_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show comprehensive retry system statistics"""
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "📈")
    
    try:
        # Get comprehensive metrics
        stats = await RetryMetricsService.get_comprehensive_stats()
        
        message = f"""📈 **Retry System Statistics**

📊 **Performance (24h):**
• Total Processed: {stats['daily']['total_processed']}
• Success Rate: {stats['daily']['success_rate']}%
• Avg Processing Time: {stats['daily']['avg_processing_time']}s
• Recovery Rate: {stats['daily']['recovery_rate']}%

🔧 **Error Classification:**
• Technical Failures: {stats['errors']['technical_count']} ({stats['errors']['technical_percentage']}%)
• User Errors: {stats['errors']['user_count']} ({stats['errors']['user_percentage']}%)
• Unknown Errors: {stats['errors']['unknown_count']}

🎯 **Top Error Codes:**"""

        # Add top error codes
        for error in stats['errors']['top_codes'][:5]:
            message += f"\n• {error['code']}: {error['count']} occurrences"

        message += f"""

⏱️ **Timing Analysis:**
• Avg Retry Delay: {stats['timing']['avg_retry_delay']}min
• Max Recovery Time: {stats['timing']['max_recovery_time']}
• Success on Retry #1: {stats['timing']['first_retry_success']}%
• Success on Retry #2: {stats['timing']['second_retry_success']}%

🏥 **System Health:**
• Queue Backlog: {stats['health']['backlog_size']} items
• Processing Rate: {stats['health']['processing_rate']}/min
• Error Rate Trend: {stats['health']['error_trend']}
• Status: {stats['health']['overall_status']}"""

        keyboard = [
            [
                InlineKeyboardButton("🔄 Refresh", callback_data="admin_retry_stats"),
                InlineKeyboardButton("🔙 Back", callback_data="admin_retry_queue")
            ]
        ]
        
        if query:
            await safe_edit_message_text(
                query, message, parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await update.message.reply_text(
                message, parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            
    except Exception as e:
        logger.error(f"ADMIN_RETRY_CMD: Error showing retry stats: {e}")
        error_msg = "❌ Error loading retry statistics. Please try again."
        if query:
            await safe_edit_message_text(query, error_msg)
        else:
            await update.message.reply_text(error_msg)

async def handle_admin_force_retry(update: Update, context: ContextTypes.DEFAULT_TYPE, cashout_id: str):
    """Force immediate retry of a specific cashout"""
    try:
        with managed_session() as db:
            # Find the cashout
            cashout = db.query(Cashout).filter(
                Cashout.cashout_id == cashout_id
            ).first()
            
            if not cashout:
                await update.message.reply_text(f"❌ Cashout {cashout_id} not found.")
                return
            
            # Check if it's eligible for retry
            if cashout.status != CashoutStatus.FAILED.value:
                await update.message.reply_text(
                    f"❌ Cashout {cashout_id} is not in failed status (current: {cashout.status})"
                )
                return
            
            if cashout.failure_type != CashoutFailureType.TECHNICAL.value:
                await update.message.reply_text(
                    f"❌ Cashout {cashout_id} has user error type - cannot retry, use force refund instead"
                )
                return
            
            # Force immediate retry
            cashout.next_retry_at = datetime.utcnow()
            cashout.retry_count += 1
            db.commit()
            
            logger.info(f"ADMIN_RETRY_CMD: Force retry scheduled for {cashout_id} by admin {update.effective_user.id}")
            
            await update.message.reply_text(
                f"✅ **Force Retry Scheduled**\n\n"
                f"Cashout: {cashout_id}\n"
                f"Amount: ${float(cashout.amount):.2f} {cashout.currency}\n"
                f"Retry #: {cashout.retry_count}\n"
                f"Error: {cashout.last_error_code}\n\n"
                f"⏱️ Retry will be processed by the unified retry system (≤2 minutes)",
                parse_mode=ParseMode.MARKDOWN
            )
            
    except Exception as e:
        logger.error(f"ADMIN_RETRY_CMD: Error in force retry for {cashout_id}: {e}")
        await update.message.reply_text(f"❌ Error processing force retry: {str(e)}")

async def handle_admin_force_refund(update: Update, context: ContextTypes.DEFAULT_TYPE, cashout_id: str):
    """Convert a stuck retry to refund"""
    try:
        with managed_session() as db:
            # Find the cashout
            cashout = db.query(Cashout).filter(
                Cashout.cashout_id == cashout_id
            ).first()
            
            if not cashout:
                await update.message.reply_text(f"❌ Cashout {cashout_id} not found.")
                return
            
            # Check if it's eligible for refund
            if cashout.status not in [CashoutStatus.FAILED.value, CashoutStatus.PENDING.value]:
                await update.message.reply_text(
                    f"❌ Cashout {cashout_id} cannot be refunded (current: {cashout.status})"
                )
                return
            
            # Clear retry schedule and trigger refund
            cashout.next_retry_at = None
            cashout.status = CashoutStatus.FAILED.value
            cashout.failed_at = datetime.utcnow()
            db.commit()
            
            # Trigger refund through existing service
            from services.automatic_refund_service import AutomaticRefundService
            refund_result = await AutomaticRefundService.trigger_refund_for_failed_cashout(cashout_id, "admin_force_refund")
            
            logger.info(f"ADMIN_RETRY_CMD: Force refund triggered for {cashout_id} by admin {update.effective_user.id}")
            
            if refund_result:
                await update.message.reply_text(
                    f"✅ **Force Refund Triggered**\n\n"
                    f"Cashout: {cashout_id}\n"
                    f"Amount: ${float(cashout.amount):.2f} {cashout.currency}\n"
                    f"User: {cashout.user_id}\n\n"
                    f"💰 Refund has been processed and user credited",
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await update.message.reply_text(
                    f"⚠️ **Refund Failed**\n\n"
                    f"Cashout {cashout_id} marked for refund but automatic refund failed.\n"
                    f"Please check refund logs and process manually.",
                    parse_mode=ParseMode.MARKDOWN
                )
            
    except Exception as e:
        logger.error(f"ADMIN_RETRY_CMD: Error in force refund for {cashout_id}: {e}")
        await update.message.reply_text(f"❌ Error processing force refund: {str(e)}")

async def handle_admin_retry_force_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Force immediate processing of retry queue"""
    query = update.callback_query
    await safe_answer_callback_query(query, "🔄")
    
    try:
        # Trigger immediate retry processing using unified system
        from services.unified_retry_service import unified_retry_service
        results = await unified_retry_service.process_retry_queue(limit=50)
        
        await safe_edit_message_text(
            query,
            f"✅ **Forced Queue Processing Complete**\n\n"
            f"📊 **Results (Unified System):**\n"
            f"• Processed: {results.get('processed', 0)} transactions\n"
            f"• Successful: {results.get('successful', 0)}\n"
            f"• Rescheduled: {results.get('rescheduled', 0)}\n"
            f"• Refunded: {results.get('refunded', 0)}\n"
            f"• Failed: {results.get('failed', 0)}\n"
            f"• Processing Time: {results.get('processing_time_seconds', 0):.2f}s\n\n"
            f"🔄 Processing via unified retry infrastructure",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Back to Queue", callback_data="admin_retry_queue")]
            ])
        )
        
        logger.info(f"ADMIN_RETRY_CMD: Force processing completed by admin {update.effective_user.id}: {results}")
        
    except Exception as e:
        logger.error(f"ADMIN_RETRY_CMD: Error in force processing: {e}")
        await safe_edit_message_text(query, f"❌ Error processing queue: {str(e)}")

async def handle_admin_retry_failed_items(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show cashouts that have reached maximum retries"""
    query = update.callback_query
    await safe_answer_callback_query(query, "⚠️")
    
    try:
        # Get failed items that need attention
        failed_items = await RetryMetricsService.get_max_retry_items(limit=10)
        
        message = "⚠️ **Max Retries Reached**\n\n"
        
        if not failed_items:
            message += "✅ No cashouts have reached maximum retries"
        else:
            message += f"Found {len(failed_items)} cashouts requiring attention:\n\n"
            
            for item in failed_items:
                days_stuck = (datetime.utcnow() - item['technical_failure_since']).days if item['technical_failure_since'] else 0
                message += f"""⚠️ **{item['cashout_id']}**
• Amount: ${item['amount']:.2f} {item['currency']}
• Error: {item['error_code']}
• Stuck for: {days_stuck} days
• Retries: {item['retry_count']}
• User: {item['user_id']}

"""

        keyboard = [
            [InlineKeyboardButton("🔄 Back to Queue", callback_data="admin_retry_queue")],
            [InlineKeyboardButton("🏠 Admin", callback_data="admin_main")]
        ]
        
        await safe_edit_message_text(
            query, message, parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
    except Exception as e:
        logger.error(f"ADMIN_RETRY_CMD: Error showing failed items: {e}")
        await safe_edit_message_text(query, "❌ Error loading failed items.")