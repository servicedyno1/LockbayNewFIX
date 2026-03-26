"""
Direct handlers for escrow flow - replaces ConversationHandler for better reliability
Maintains exact same UI while fixing message routing conflicts
"""

import logging
from decimal import Decimal
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, CallbackQueryHandler
from models import User
from database import SessionLocal
from utils.callback_utils import safe_answer_callback_query
from utils.constants import CallbackData
from utils.markdown_escaping import format_username_html

# Import all the original handlers to maintain same UI
from handlers.escrow import (
    start_secure_trade,
    handle_seller_input,
    handle_amount_input,
    handle_description_input,
    handle_delivery_time_input,
    handle_cancel_escrow,
    handle_amount_callback,
    handle_back_to_trade_review_callback,
    handle_delivery_time_callback,
    handle_fee_split_selection,
    handle_trade_review_callbacks,
    handle_payment_method_selection,
    handle_copy_address,
    handle_show_qr,
    handle_back_to_payment,
    handle_wallet_payment_confirmation,
    handle_escrow_crypto_switching,
    clean_seller_identifier,
    format_trade_review_message
)

logger = logging.getLogger(__name__)

def _is_numeric_amount(text: str) -> bool:
    """Check if text looks like a numeric amount (with decimals, commas, etc.)"""
    if not text:
        return False
    
    # Remove common currency symbols and whitespace
    cleaned = text.strip().replace(',', '').replace('$', '').replace('€', '').replace('£', '')
    
    # Check if it's a valid decimal number
    try:
        float(cleaned)
        return True
    except ValueError:
        return False

async def handle_trade_review_amount_input(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    """Handle direct amount input on trade review screen (smart UX feature)"""
    user_id = update.effective_user.id if update.effective_user else 0
    
    try:
        # Parse and validate the amount
        cleaned_text = text.strip().replace(',', '').replace('$', '').replace('€', '').replace('£', '')
        amount = float(cleaned_text)
        
        if amount <= 0:
            await update.message.reply_text("❌ Amount must be greater than 0. Please try again.")
            return True
        
        if amount > 100000:  # Reasonable upper limit
            await update.message.reply_text("❌ Amount is too large. Please enter a reasonable amount.")
            return True
        
        # Update the escrow data with new amount
        if context.user_data and "escrow_data" in context.user_data:
            escrow_data = context.user_data["escrow_data"]
            escrow_data["amount"] = amount
            
            # Check if this is a first trade free promotion
            fee_breakdown = escrow_data.get("fee_breakdown", {})
            is_first_trade_free = fee_breakdown.get("is_first_trade_free", False)
            
            if is_first_trade_free:
                # Preserve first trade free promotion - no fees!
                escrow_data["buyer_fee"] = Decimal("0.0")
                escrow_data["seller_fee"] = Decimal("0.0")
                
                # Update fee_breakdown to reflect new amount while preserving first trade free
                fee_breakdown["escrow_amount"] = Decimal(str(amount))
                fee_breakdown["total_platform_fee"] = Decimal("0.00")
                fee_breakdown["buyer_fee_amount"] = Decimal("0.00")
                fee_breakdown["seller_fee_amount"] = Decimal("0.00")
                fee_breakdown["buyer_total_payment"] = Decimal(str(amount))
                fee_breakdown["seller_net_amount"] = Decimal(str(amount))
                fee_breakdown["refundable_amount"] = Decimal(str(amount))
                fee_breakdown["platform_fee"] = Decimal("0.00")
                fee_breakdown["total_payment"] = Decimal(str(amount))
                escrow_data["fee_breakdown"] = fee_breakdown
                
                logger.info(f"🎉 SMART AMOUNT: Preserving first trade free for user {user_id} - keeping fees at $0.00")
            else:
                # Recalculate fees based on new amount
                from config import Config
                total_fee = Decimal(str(amount)) * Decimal(str(Config.ESCROW_FEE_PERCENTAGE / 100))
                
                # Apply existing fee split if set
                fee_split = escrow_data.get("fee_split_option", "split")
                if fee_split == "buyer_pays":
                    escrow_data["buyer_fee"] = total_fee
                    escrow_data["seller_fee"] = Decimal("0.0")
                elif fee_split == "seller_pays":
                    escrow_data["buyer_fee"] = Decimal("0.0")
                    escrow_data["seller_fee"] = total_fee
                else:  # split
                    escrow_data["buyer_fee"] = total_fee / 2
                    escrow_data["seller_fee"] = total_fee / 2
            
            context.user_data["escrow_data"] = escrow_data
            
            # Keep user in trade review state after amount update
            await set_user_state(user_id, "trade_review")
            await update.message.reply_text(f"✅ Amount updated to ${amount:.2f}")
            
            # Send new trade review message instead of trying to edit
            await send_updated_trade_review(update, context)
            
            logger.info(f"🎯 SMART AMOUNT: Successfully updated amount to ${amount} for user {user_id}")
            return True
        
        await update.message.reply_text("❌ Session expired. Please start a new trade.")
        return True
        
    except ValueError:
        await update.message.reply_text("❌ Invalid amount format. Please enter a valid number (e.g., 100 or 99.50)")
        return True
    except Exception as e:
        logger.error(f"Error processing smart amount input for user {user_id}: {e}")
        await update.message.reply_text("❌ Error processing amount. Please try again.")
        return True

async def send_updated_trade_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send updated trade review as a new message (for amount updates)"""
    try:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        
        if not context.user_data or "escrow_data" not in context.user_data:
            await update.message.reply_text("❌ Session expired. Please start a new trade.")
            return
        
        escrow_data = context.user_data["escrow_data"]
        amount = Decimal(str(escrow_data["amount"]))
        buyer_fee = Decimal(str(escrow_data["buyer_fee"]))
        total_to_pay = amount + buyer_fee
        
        # Use HTML escaping since message uses parse_mode="HTML"
        import html
        seller_identifier_clean = clean_seller_identifier(escrow_data.get('seller_identifier', ''))
        # Update stored value to keep it clean
        escrow_data['seller_identifier'] = seller_identifier_clean
        context.user_data["escrow_data"] = escrow_data
        
        seller_display = (
            format_username_html(f"@{seller_identifier_clean}", include_link=False)
            if escrow_data["seller_type"] == "username"
            else html.escape(seller_identifier_clean)
        )
        
        # Get trade details from database if available
        description = escrow_data.get("description", "Buying goods")
        escrow_id = escrow_data.get("escrow_id", "PENDING")
        delivery_hours = escrow_data.get("delivery_hours", 24)
        
        # Format fee option
        fee_option = escrow_data.get("fee_split_option", "split")
        fee_display = {"split": "Split Fees", "buyer_pays": "Buyer Pays All", "seller_pays": "Seller Pays All"}.get(fee_option, "Split")
        
        # Format payment method display
        payment_method = escrow_data.get("payment_method")
        payment_display = ""
        if payment_method:
            if payment_method == "wallet":
                payment_display = "\n💳 Payment: Wallet Balance"
            elif payment_method.startswith("crypto_"):
                crypto = payment_method.replace("crypto_", "").upper()
                crypto_names = {"BTC": "Bitcoin", "ETH": "Ethereum", "USDT": "USDT", "LTC": "Litecoin", "DOGE": "Dogecoin"}
                crypto_name = crypto_names.get(crypto, crypto)
                payment_display = f"\n💳 Payment: {crypto_name}"
            elif payment_method == "ngn_bank":
                payment_display = "\n💳 Payment: NGN Bank Transfer"
        
        # Format escrow ID display (show full ID if short/placeholder, otherwise last 6 chars)
        if len(escrow_id) <= 8 or escrow_id in ["PENDING", "N/A", "CREATING"]:
            escrow_id_display = escrow_id
        else:
            escrow_id_display = escrow_id[-6:]
        
        # Extract username if seller_type is username
        seller_username = None
        if escrow_data.get("seller_type") == "username":
            seller_username = seller_identifier_clean
        
        text = format_trade_review_message(
            escrow_id_display=escrow_id_display,
            total_to_pay=total_to_pay,
            seller_display=seller_display,
            description=description,
            fee_display=fee_display,
            payment_display=payment_display,
            use_html_link=False,
            seller_username=seller_username,
            delivery_hours=delivery_hours
        )
        
        # Create keyboard with payment selection logic
        payment_method_selected = escrow_data.get("payment_method") is not None
        
        if payment_method_selected:
            payment_button = InlineKeyboardButton("🔄 Switch Payment", callback_data="switch_payment_method")
        else:
            payment_button = InlineKeyboardButton("💳 Select Payment", callback_data="switch_payment_method")
        
        # Check if this is a first-trade-free promotion
        fee_breakdown = escrow_data.get("fee_breakdown", {})
        is_first_trade_free = fee_breakdown.get("is_first_trade_free", False)
        
        keyboard = [
            [payment_button],
            [
                InlineKeyboardButton("✏️ Edit Amount", callback_data="edit_trade_amount"),
                InlineKeyboardButton("✏️ Edit Item", callback_data="edit_trade_description")
            ]
        ]
        
        # Only show "Change Fees" and "Change Delivery" if NOT first-trade-free
        # (first trade is free, so no fees to change)
        if is_first_trade_free:
            # For first-trade-free, only show Change Delivery button
            keyboard.append([
                InlineKeyboardButton("⏱️ Change Delivery", callback_data="edit_delivery_time")
            ])
        else:
            # For normal trades, show both Change Delivery and Change Fees
            keyboard.append([
                InlineKeyboardButton("⏱️ Change Delivery", callback_data="edit_delivery_time"),
                InlineKeyboardButton("💸 Change Fees", callback_data="edit_fee_split")
            ])
        
        # Only show "Confirm Trade" button if payment method is selected
        if payment_method_selected:
            keyboard.append([InlineKeyboardButton("✅ Confirm Trade", callback_data="confirm_trade_final")])
        
        keyboard.append([InlineKeyboardButton("❌ Cancel Trade", callback_data="cancel_escrow")])
        
        await update.message.reply_text(
            text, 
            parse_mode=ParseMode.HTML, 
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
    except Exception as e:
        logger.error(f"Error sending updated trade review: {e}")
        await update.message.reply_text("❌ Error displaying updated trade. Please try again.")

# State tracking helper functions
async def set_user_state(user_id: int, state: str):
    """Set user conversation state in database"""
    session = SessionLocal()
    try:
        # Ensure user_id is integer for bigint comparison
        user_id_int = int(user_id) if user_id else 0
        user = session.query(User).filter(User.telegram_id == user_id_int).first()
        if user:
            setattr(user, 'conversation_state', state)
            session.commit()
            logger.debug(f"Set user {user_id_int} state to: {state}")
    except Exception as e:
        logger.error(f"Error setting user state: {e}")
    finally:
        session.close()

async def get_user_state(user_id: int) -> str:
    """Get user conversation state from database"""
    session = SessionLocal()
    try:
        # Ensure user_id is integer for bigint comparison
        user_id_int = int(user_id) if user_id else 0
        user = session.query(User).filter(User.telegram_id == user_id_int).first()
        return str(getattr(user, 'conversation_state', '')) if user and hasattr(user, 'conversation_state') else ""
    except Exception as e:
        logger.error(f"Error getting user state: {e}")
        return ""
    finally:
        session.close()

async def clear_user_state(user_id: int):
    """Clear user conversation state"""
    await set_user_state(user_id, "")

# CRITICAL: Route text messages to appropriate escrow handlers
async def route_text_message_to_escrow_flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Route text messages to appropriate escrow handler based on context and state"""
    if not update.effective_user or not update.message or not update.message.text:
        return False
    
    user_id = update.effective_user.id
    text = update.message.text.strip()
    
    # Get current database state
    db_state = await get_user_state(user_id)
    
    # Get context state
    escrow_data = context.user_data.get("escrow_data", {}) if context.user_data else {}
    
    logger.info(f"🔄 ROUTING: User {user_id} text '{text}' - DB state: '{db_state}', Context: {escrow_data}")
    
    try:
        # SMART UX FIX: Handle direct amount input on trade review screen
        if db_state == "trade_review" and text and _is_numeric_amount(text):
            logger.info(f"🎯 SMART AMOUNT: User {user_id} typing amount '{text}' on trade review → processing directly")
            return await handle_trade_review_amount_input(update, context, text)
        
        # Determine the appropriate handler based on state and context
        if db_state == "seller_input" or (not db_state and escrow_data.get("status") == "creating"):
            # Route to seller input handler - let it handle state transition
            logger.info(f"📝 ROUTING: Directing to seller input handler for user {user_id}")
            result = await handle_seller_input(update, context)
            # Update state to amount_input after successful seller input processing
            if result:  # If handler processed successfully
                await set_user_state(user_id, "amount_input")
                logger.info(f"✅ ROUTING: Updated state to amount_input for user {user_id}")
            return True
            
        elif db_state == "amount_input":
            # Route to amount input handler - let it handle state transition
            logger.info(f"💰 ROUTING: Directing to amount input handler for user {user_id}")
            result = await handle_amount_input(update, context)
            # Map returned state to database state
            if result:  # If handler returned a state
                from handlers.escrow import EscrowStates
                state_map = {
                    EscrowStates.AMOUNT_INPUT: "amount_input",  # Validation error, stay in same state
                    EscrowStates.DESCRIPTION_INPUT: "description_input",  # Normal flow, move to next
                    EscrowStates.TRADE_REVIEW: "trade_review",  # Smart routing detected edit from review
                }
                new_state = state_map.get(result)
                if new_state:
                    await set_user_state(user_id, new_state)
                    logger.info(f"✅ ROUTING: Updated state to {new_state} for user {user_id}")
            return True
            
        elif db_state == "description_input":
            # Route to description input handler - let it handle state transition
            logger.info(f"📄 ROUTING: Directing to description input handler for user {user_id}")
            result = await handle_description_input(update, context)
            # Map returned state to database state
            if result:  # If handler returned a state
                from handlers.escrow import EscrowStates
                state_map = {
                    EscrowStates.DESCRIPTION_INPUT: "description_input",  # Validation error, stay in same state
                    EscrowStates.DELIVERY_TIME: "delivery_time",  # Normal flow, move to next
                    EscrowStates.TRADE_REVIEW: "trade_review",  # Smart routing detected edit from review
                }
                new_state = state_map.get(result)
                if new_state:
                    await set_user_state(user_id, new_state)
                    logger.info(f"✅ ROUTING: Updated state to {new_state} for user {user_id}")
            return True
            
        elif db_state == "delivery_time":
            # Route to delivery time input handler - let it handle state transition
            logger.info(f"⏰ ROUTING: Directing to delivery time input handler for user {user_id}")
            result = await handle_delivery_time_input(update, context)
            # Clear state after successful delivery time processing (flow complete)
            if result:  # If handler processed successfully
                await clear_user_state(user_id)
                logger.info(f"✅ ROUTING: Cleared state for user {user_id} - escrow flow complete")
            return True
            
        else:
            # Unknown or no state - try to determine from text pattern
            if text.startswith('@') or '@' in text:
                # Looks like seller input, start escrow flow
                logger.info(f"🔄 ROUTING: Text pattern suggests seller input, starting escrow flow for user {user_id}")
                result = await handle_seller_input(update, context)
                # Update state to amount_input after successful seller input processing
                if result:  # If handler processed successfully
                    await set_user_state(user_id, "amount_input")
                    logger.info(f"✅ ROUTING: Updated state to amount_input for user {user_id}")
                return True
            else:
                logger.warning(f"❓ ROUTING: Cannot determine appropriate handler for user {user_id} in state '{db_state}' with text '{text}'")
                return False
                
    except Exception as e:
        logger.error(f"❌ ROUTING ERROR: Failed to route message for user {user_id}: {e}")
        # Clear corrupted state
        await clear_user_state(user_id)
        if context.user_data and "escrow_data" in context.user_data:
            context.user_data.pop("escrow_data", None)
        return False

# Direct handler wrappers that maintain exact same UI

async def direct_start_secure_trade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for starting secure trade"""
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "🛡️")
    
    # Clear any previous state
    if update.effective_user:
        await clear_user_state(update.effective_user.id)
    
    # Set initial state
    if update.effective_user:
        await set_user_state(update.effective_user.id, "seller_input")
    
    # Call original handler
    return await start_secure_trade(update, context)

async def direct_handle_seller_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for seller input"""
    # State transition handled by route_text_message_to_escrow_flow
    return await handle_seller_input(update, context)

async def direct_handle_amount_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for amount input"""
    # State transition handled by route_text_message_to_escrow_flow
    return await handle_amount_input(update, context)

async def direct_handle_description_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for description input"""
    # State transition handled by route_text_message_to_escrow_flow
    return await handle_description_input(update, context)

async def direct_handle_delivery_time_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for delivery time input"""
    # State transition handled by route_text_message_to_escrow_flow
    return await handle_delivery_time_input(update, context)

async def direct_handle_cancel_escrow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for cancel escrow"""
    if update.effective_user:
        await clear_user_state(update.effective_user.id)
    return await handle_cancel_escrow(update, context)

# Register all direct handlers
DIRECT_ESCROW_HANDLERS = [
    # Entry points
    CallbackQueryHandler(direct_start_secure_trade, pattern=f"^{CallbackData.MENU_CREATE}$"),
    CallbackQueryHandler(direct_start_secure_trade, pattern="^create_secure_trade$"),
    
    # Cancellation (highest priority)
    CallbackQueryHandler(direct_handle_cancel_escrow, pattern="^cancel_escrow$"),
    
    # Callback handlers for navigation
    CallbackQueryHandler(handle_amount_callback, pattern="^(back_to_trade_review|cancel_escrow)"),
    CallbackQueryHandler(handle_back_to_trade_review_callback, pattern="^back_to_trade_review$"),
    CallbackQueryHandler(handle_delivery_time_callback, pattern="^(delivery_|back_to_delivery|back_to_trade_review|cancel_escrow)"),
    CallbackQueryHandler(handle_fee_split_selection, pattern="^(fee_|back_to_delivery|back_to_fee_options|back_to_trade_review|cancel_escrow)"),
    CallbackQueryHandler(handle_trade_review_callbacks, pattern="^(switch_payment_method|edit_trade_|edit_delivery_time|edit_fee_split|confirm_trade_final|cancel_escrow|back_to_trade_review|escrow_add_funds)"),
    CallbackQueryHandler(handle_payment_method_selection, pattern="^(payment_|crypto_|confirm_wallet_payment|back_to_trade_review|cancel_escrow)"),
    CallbackQueryHandler(handle_copy_address, pattern="^copy_address_"),
    CallbackQueryHandler(handle_show_qr, pattern="^show_qr$"),
    CallbackQueryHandler(handle_back_to_payment, pattern="^back_to_payment$"),
    CallbackQueryHandler(handle_wallet_payment_confirmation, pattern="^confirm_wallet_payment$"),
    CallbackQueryHandler(handle_escrow_crypto_switching, pattern="^switch_crypto_escrow$"),
]