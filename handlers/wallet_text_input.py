"""
Wallet Text Input Handler
Processes text inputs for wallet operations, especially crypto addresses
"""

import logging
import re
from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


def is_valid_eth_address(address: str) -> bool:
    """Validate ETH address format"""
    if not address:
        return False
    
    # Basic ETH address validation - starts with 0x and 40 hex chars
    eth_pattern = r'^0x[a-fA-F0-9]{40}$'
    return bool(re.match(eth_pattern, address))


def is_valid_crypto_address(address: str, currency: str = None) -> bool:
    """Validate various crypto address formats"""
    if not address:
        return False
    
    # ETH addresses
    if is_valid_eth_address(address):
        return True
    
    # BTC addresses (basic validation)
    if re.match(r'^[13][a-km-zA-HJ-NP-Z1-9]{25,34}$', address):
        return True
        
    # BTC bech32 (segwit)
    if re.match(r'^bc1[a-z0-9]{39,59}$', address):
        return True
    
    # Add more crypto address patterns as needed
    return False


async def handle_wallet_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text input during wallet operations"""
    try:
        user = update.effective_user
        text = update.message.text.strip()
        
        if not user or not text:
            return
            
        user_id = user.id
        
        # CRITICAL FIX: Use unified state management system instead of legacy context.user_data
        from handlers.wallet_direct import get_wallet_state
        wallet_state = await get_wallet_state(user_id, context)
        context.user_data.get('current_state', '')
        
        logger.info(f"🎯 WALLET_TEXT_INPUT: User {user_id} in state '{wallet_state}' sent: '{text[:50]}...'")
        
        # CRITICAL FIX: Don't handle "entering_custom_amount" here - that's for cashout DOLLAR amounts
        # handled by handle_custom_amount_input in wallet_direct.py, NOT crypto addresses!
        
        # CRITICAL FIX (Dec 2025): Delegate to wallet_direct.py for state '305' (WalletStates.ENTERING_WITHDRAW_ADDRESS)
        # The new cashout flow sets state as str(WalletStates.ENTERING_WITHDRAW_ADDRESS) = '305'
        # This handler was missing the numeric state, causing bot to become unresponsive after address entry
        if wallet_state in ['305', 305, 'entering_crypto_details']:
            from handlers.wallet_direct import handle_crypto_address_input
            logger.info(f"📤 Delegating to wallet_direct.handle_crypto_address_input for state {wallet_state}")
            return await handle_crypto_address_input(update, context)
        
        # Handle other crypto address inputs (legacy states)
        if wallet_state in ['entering_crypto_address', 'entering_withdraw_address']:
            if is_valid_crypto_address(text):
                logger.info(f"✅ Valid crypto address detected: {text}")
                
                # Store the address
                if 'cashout_data' not in context.user_data:
                    context.user_data['cashout_data'] = {}
                
                context.user_data['cashout_data']['address'] = text
                
                # Determine currency from address format
                if is_valid_eth_address(text):
                    context.user_data['cashout_data']['network'] = 'ETH'
                    context.user_data['cashout_data']['currency'] = 'ETH'
                
                # Continue with confirmation flow  
                from handlers.wallet_direct import show_ngn_payout_confirmation_screen
                return await show_ngn_payout_confirmation_screen(update, context)
            else:
                await update.message.reply_text(
                    "❌ Invalid crypto address format.\n\n"
                    "Please enter a valid cryptocurrency address."
                )
                return
        
        # Handle NGN wallet funding amount input
        elif wallet_state == 'selecting_amount_ngn':
            logger.info(f"💵 Processing NGN wallet funding amount for user {user_id}: '{text}'")
            from handlers.fincra_payment import FincraPaymentHandler
            return await FincraPaymentHandler.handle_ngn_amount_input(update, context)
        
        # Handle numeric amount inputs for withdraw flow only (NOT cashout - that's handled by wallet_direct.py)
        elif wallet_state in ['entering_withdraw_amount']:
            try:
                from decimal import Decimal
                amount = Decimal(text.replace('$', '').replace(',', ''))
                
                if amount <= 0:
                    await update.message.reply_text("❌ Please enter an amount greater than $0.")
                    return
                    
                # Store the amount
                if 'cashout_data' not in context.user_data:
                    context.user_data['cashout_data'] = {}
                
                context.user_data['cashout_data']['amount'] = amount
                context.user_data['cashout_balance'] = amount
                
                # Continue to next step (address entry)
                context.user_data['wallet_state'] = 'entering_crypto_address'
                
                await update.message.reply_text(
                    f"💰 Amount: ${amount}\n\n"
                    "Please enter your cryptocurrency address where you want to receive the funds:"
                )
                return
                
            except Exception:
                await update.message.reply_text(
                    "❌ Invalid amount format.\n\n"
                    "Please enter a valid number (e.g., 50.00 or 100)"
                )
                return
        
        # OTP verification is now handled by dedicated OTP verification handler
        elif wallet_state in ['verifying_crypto_otp', 'verifying_ngn_otp']:
            logger.info(f"🔄 Redirecting OTP verification to dedicated handler for user {user_id}")
            from handlers.otp_verification import handle_otp_verification
            return await handle_otp_verification(update, context)
        
        # Default fallback
        logger.warning(f"⚠️ Unhandled wallet text input: state='{wallet_state}', text='{text[:100]}'")
        await update.message.reply_text(
            "ℹ️ I didn't understand that input in the current context.\n\n"
            "Type 'menu' to return to the main menu or 'cancel' to cancel the current operation."
        )
        
    except Exception as e:
        logger.error(f"❌ Error in wallet text input handler: {e}")
        await update.message.reply_text(
            "❌ An error occurred processing your input. Please try again or contact support."
        )