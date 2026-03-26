"""
Example Usage of Handler Decorators for Audit Logging
Demonstrates how to apply the audit decorators to different types of handlers
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler

from utils.handler_decorators import (
    audit_handler,
    audit_admin_handler,
    audit_conversation_handler,
    audit_dispute_handler,
    audit_callback_handler,
    audit_escrow_with_session,
    audit_exchange_with_session,
    audit_wallet_with_session,
    with_error_recovery,
    with_performance_monitoring,
    AuditEventType
)


# Example 1: Basic handler with automatic audit logging
@audit_handler(event_type=AuditEventType.USER_INTERACTION, action="start_command")
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command handler with automatic logging"""
    user = update.effective_user
    
    await update.message.reply_text(
        f"👋 Welcome to LockBay, {user.first_name}!\n"
        "Your secure escrow service for cryptocurrency exchanges.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🏪 Start Trade", callback_data="start_trade")],
            [InlineKeyboardButton("💰 My Wallet", callback_data="wallet_menu")],
            [InlineKeyboardButton("📞 Support", callback_data="support_menu")]
        ])
    )
    
    return ConversationHandler.END


# Example 2: Admin handler with specialized decorator
@audit_admin_handler(action="view_admin_dashboard")
async def admin_dashboard_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin dashboard handler with admin-specific logging"""
    query = update.callback_query
    await query.answer()
    
    # Admin statistics would be fetched here
    stats_text = (
        "📊 *Admin Dashboard*\n\n"
        "• Active Trades: 42\n"
        "• Today's Volume: $12,345\n"
        "• Pending Disputes: 2\n"
        "• System Status: ✅ Operational"
    )
    
    await query.edit_message_text(
        stats_text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 Detailed Stats", callback_data="admin_detailed_stats")],
            [InlineKeyboardButton("⚠️ Manage Disputes", callback_data="admin_disputes")],
            [InlineKeyboardButton("🔧 System Config", callback_data="admin_config")]
        ])
    )


# Example 3: Escrow handler with session tracking
@audit_escrow_with_session(action="create_escrow_trade")
async def create_escrow_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Escrow creation handler with automatic session tracking and audit logging"""
    query = update.callback_query
    await query.answer()
    
    # Store escrow creation in context for related ID tracking
    context.user_data['current_escrow_id'] = "ESC123456"
    context.user_data['escrow_stage'] = "amount_selection"
    
    await query.edit_message_text(
        "💰 *Create New Escrow Trade*\n\n"
        "Please enter the amount you want to trade:\n"
        "• Minimum: $10 USD\n"
        "• Maximum: $50,000 USD\n\n"
        "Example: `250` for $250 USD",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel_escrow")]
        ])
    )


# Example 4: Exchange handler with session tracking
@audit_exchange_with_session(action="direct_crypto_exchange")
async def direct_exchange_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct exchange handler with session tracking"""
    query = update.callback_query
    await query.answer()
    
    # Store exchange order in context
    context.user_data['exchange_order_id'] = "EXC789012"
    context.user_data['exchange_type'] = "crypto_to_fiat"
    
    await query.edit_message_text(
        "🔄 *Direct Cryptocurrency Exchange*\n\n"
        "Convert your crypto to fiat currency instantly:\n\n"
        "Select cryptocurrency to exchange:",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("₿ Bitcoin", callback_data="exchange_BTC"),
                InlineKeyboardButton("Ξ Ethereum", callback_data="exchange_ETH")
            ],
            [
                InlineKeyboardButton("Ł Litecoin", callback_data="exchange_LTC"),
                InlineKeyboardButton("❌ Cancel", callback_data="cancel_exchange")
            ]
        ])
    )


# Example 5: Wallet handler with session tracking
@audit_wallet_with_session(action="wallet_operations")
async def wallet_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Wallet menu handler with session tracking"""
    query = update.callback_query
    await query.answer()
    
    # Simulate fetching wallet balance
    usd_balance = "1,234.56"
    btc_balance = "0.02345678"
    
    await query.edit_message_text(
        f"💰 *Your Wallet*\n\n"
        f"💵 USD Balance: ${usd_balance}\n"
        f"₿ BTC Balance: {btc_balance} BTC\n\n"
        "Choose an option:",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📥 Deposit", callback_data="wallet_deposit")],
            [InlineKeyboardButton("📤 Withdraw", callback_data="wallet_withdraw")],
            [InlineKeyboardButton("📊 History", callback_data="wallet_history")],
            [InlineKeyboardButton("🔙 Back", callback_data="main_menu")]
        ])
    )


# Example 6: Callback handler for button interactions
@audit_callback_handler(action="button_navigation")
async def navigation_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generic callback handler for button navigation with detailed button logging"""
    query = update.callback_query
    callback_data = query.data
    
    await query.answer()
    
    # Route to specific handlers based on callback data
    if callback_data == "main_menu":
        await query.edit_message_text(
            "🏠 *Main Menu*\n\nWelcome back! Choose an option:",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏪 Start Trade", callback_data="start_trade")],
                [InlineKeyboardButton("💰 My Wallet", callback_data="wallet_menu")],
                [InlineKeyboardButton("📞 Support", callback_data="support_menu")]
            ])
        )
    elif callback_data == "support_menu":
        await query.edit_message_text(
            "📞 *Support Center*\n\nHow can we help you?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💬 Live Chat", callback_data="support_chat")],
                [InlineKeyboardButton("❓ FAQ", callback_data="support_faq")],
                [InlineKeyboardButton("🔙 Back", callback_data="main_menu")]
            ])
        )


# Example 7: Dispute handler with specialized logging
@audit_dispute_handler(action="create_dispute")
async def create_dispute_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Dispute creation handler with dispute-specific logging"""
    query = update.callback_query
    await query.answer()
    
    # Store dispute information
    context.user_data['dispute_id'] = "DIS456789"
    context.user_data['disputed_escrow_id'] = context.user_data.get('current_escrow_id')
    
    await query.edit_message_text(
        "⚠️ *Create Dispute*\n\n"
        "Please describe the issue with your trade:\n"
        "• Be specific and clear\n"
        "• Include relevant details\n"
        "• Attach evidence if available\n\n"
        "Our support team will review your case within 24 hours.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel_dispute")]
        ])
    )


# Example 8: Handler with error recovery
@with_error_recovery(recovery_action="show_main_menu")
@audit_handler(event_type=AuditEventType.TRANSACTION)
async def risky_operation_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler that might fail, with automatic error recovery"""
    query = update.callback_query
    await query.answer()
    
    # Simulate a risky operation that might fail
    import random
    if random.choice([True, False]):
        raise Exception("Simulated operation failure")
    
    await query.edit_message_text(
        "✅ Operation completed successfully!"
    )


# Example 9: Performance-monitored handler
@with_performance_monitoring(warning_threshold_ms=500)
@audit_handler(event_type=AuditEventType.SYSTEM)
async def heavy_computation_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler with performance monitoring for slow operations"""
    import asyncio
    
    # Simulate heavy computation
    await asyncio.sleep(0.6)  # This will trigger performance warning
    
    await update.message.reply_text(
        "🔄 Heavy computation completed!"
    )


# Example 10: Conversation handler with state tracking
@audit_conversation_handler(action="escrow_conversation_flow")
async def escrow_amount_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Conversation handler for escrow amount input with state tracking"""
    message_text = update.message.text
    
    try:
        amount = float(message_text.replace('$', '').replace(',', ''))
        
        if amount < 10:
            await update.message.reply_text(
                "❌ Minimum amount is $10 USD. Please enter a valid amount:"
            )
            return "WAITING_FOR_AMOUNT"
        
        if amount > 50000:
            await update.message.reply_text(
                "❌ Maximum amount is $50,000 USD. Please enter a valid amount:"
            )
            return "WAITING_FOR_AMOUNT"
        
        # Store the amount and move to next step
        context.user_data['escrow_amount'] = amount
        
        await update.message.reply_text(
            f"✅ Amount set: ${amount:,.2f} USD\n\n"
            "Now, please provide the seller's Telegram username or phone number:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancel", callback_data="cancel_escrow")]
            ])
        )
        
        return "WAITING_FOR_SELLER_CONTACT"
        
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid amount format. Please enter a number (e.g., 250):"
        )
        return "WAITING_FOR_AMOUNT"


# Example Usage in Handler Registration:
"""
# In your main.py or handler registration file:

from utils.handler_decorators_example import *

# Register handlers with automatic audit logging
application.add_handler(CommandHandler("start", start_handler))
application.add_handler(CallbackQueryHandler(admin_dashboard_handler, pattern="^admin_dashboard$"))
application.add_handler(CallbackQueryHandler(create_escrow_handler, pattern="^start_trade$"))
application.add_handler(CallbackQueryHandler(direct_exchange_handler, pattern="^direct_exchange$"))
application.add_handler(CallbackQueryHandler(wallet_menu_handler, pattern="^wallet_menu$"))
application.add_handler(CallbackQueryHandler(navigation_callback_handler, pattern="^(main_menu|support_menu)$"))

# Note: ConversationHandler example removed - not needed for demonstration
# ConversationHandlers should only be created when actually used
"""