"""Admin Payment Processor Status Handler"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from services.crypto_enhanced import CryptoServiceEnhanced
from utils.callback_utils import safe_answer_callback_query

logger = logging.getLogger(__name__)


async def handle_admin_payment_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle admin request for payment processor status"""
    try:
        query = update.callback_query
        await safe_answer_callback_query(query)
        
        # Get comprehensive payment processor status
        status = await CryptoServiceEnhanced.get_payment_processor_status()
        
        # Format status message
        message = "🔧 Payment Processor Status\n\n"
        
        if status.get('service_available', False):
            message += "✅ Service Status: OPERATIONAL\n\n"
        else:
            message += "❌ Service Status: DEGRADED\n\n"
        
        # Provider details
        for provider_name, provider_info in status.items():
            if isinstance(provider_info, dict) and 'available' in provider_info:
                provider_type = provider_info.get('type', 'unknown')
                available = provider_info.get('available', False)
                configured = provider_info.get('configured', False)
                
                emoji = "✅" if available else "❌"
                type_emoji = "🥇" if provider_type == "primary" else "🥈"
                
                message += f"{type_emoji} {provider_name.upper()} ({provider_type})\n"
                message += f"{emoji} Available: {'Yes' if available else 'No'}\n"
                message += f"🔧 Configured: {'Yes' if configured else 'No'}\n"
                
                if 'error' in provider_info:
                    message += f"⚠️ Error: {provider_info['error']}\n"
                
                message += "\n"
        
        # Failover status
        failover_enabled = status.get('failover_enabled', False)
        message += f"🔄 Failover: {'Enabled' if failover_enabled else 'Disabled'}\n"
        
        # Available providers
        available_providers = status.get('available_providers', [])
        if available_providers:
            message += f"🟢 Available: {', '.join(available_providers)}\n"
        else:
            message += "🔴 No providers available\n"
        
        # Provider priority
        priority = status.get('provider_priority', [])
        if priority:
            message += f"📊 Priority: {' → '.join(priority)}\n"
        
        # Create keyboard
        keyboard = [
            [InlineKeyboardButton("🔄 Refresh Status", callback_data="admin_payment_status")],
            [InlineKeyboardButton("📋 Currency Support", callback_data="admin_currency_support")],
            [InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_panel")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            text=message,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Error handling admin payment status: {e}")
        await query.edit_message_text(
            "❌ Error retrieving payment processor status. Please try again.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_panel")
            ]])
        )


async def handle_admin_currency_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle admin request for currency support across providers"""
    try:
        query = update.callback_query
        await safe_answer_callback_query(query, "Loading currency support...")
        
        # Get currency support from all providers
        currency_data = await CryptoServiceEnhanced.get_supported_currencies_all_providers()
        
        if 'error' in currency_data:
            message = f"❌ Error loading currency data: {currency_data['error']}"
        else:
            message = "💱 Currency Support by Provider\n\n"
            
            total_currencies = currency_data.get('total_currencies', 0)
            message += f"📊 Total Supported: {total_currencies} currencies\n\n"
            
            # Provider-specific support
            providers = currency_data.get('providers', {})
            for provider_name, currencies in providers.items():
                if isinstance(currencies, dict) and 'error' not in currencies:
                    currency_count = len(currencies)
                    message += f"🔹 {provider_name.upper()}: {currency_count} currencies\n"
                elif 'error' in currencies:
                    message += f"🔹 {provider_name.upper()}: ❌ {currencies['error']}\n"
                else:
                    message += f"🔹 {provider_name.upper()}: No data\n"
            
            message += "\n"
            
            # Combined support overview
            combined = currency_data.get('combined_support', {})
            if combined:
                message += "🎯 Currency Availability:\n"
                
                # Count currencies by provider support
                both_providers = sum(1 for providers in combined.values() if len(providers) >= 2)
                single_provider = sum(1 for providers in combined.values() if len(providers) == 1)
                
                message += f"✅ Both providers: {both_providers} currencies\n"
                message += f"⚠️ Single provider: {single_provider} currencies\n"
        
        # Create keyboard
        keyboard = [
            [InlineKeyboardButton("🔙 Payment Status", callback_data="admin_payment_status")],
            [InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_panel")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            text=message,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Error handling currency support request: {e}")
        await query.edit_message_text(
            "❌ Error loading currency support data. Please try again.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_panel")
            ]])
        )