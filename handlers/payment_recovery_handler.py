"""
Payment Recovery Handler
Handles user interactions for payment recovery options (complete payment, proceed partial, cancel & refund)
"""

import logging
from typing import Dict, Any
from decimal import Decimal
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from datetime import datetime, timedelta

from services.crypto import CryptoServiceAtomic
# from services.payment_address_service import PaymentAddressService  # Will be available when needed
# from utils.helpers import generate_qr_code_base64  # Will be available when needed
from models import Escrow
from sqlalchemy import update as sqlalchemy_update
from utils.callback_utils import safe_answer_callback_query

logger = logging.getLogger(__name__)


class PaymentRecoveryHandler:
    """Handles payment recovery user interactions"""
    
    @staticmethod
    async def show_payment_recovery_options(
        update: Update, 
        context: ContextTypes.DEFAULT_TYPE,
        user_id: int,
        transaction_id: str,
        decision_data: Dict[str, Any]
    ):
        """Show payment recovery options to user"""
        try:
            variance_usd = abs(decision_data["variance_usd"])
            action_options = decision_data["action_options"]
            
            # Create user-friendly message
            message = "💰 **Payment Recovery Options**\n\n"
            message += f"Your payment was ${float(variance_usd):.2f} short of the required amount.\n\n"
            message += "**Choose your preferred option:**"
            
            # Create inline keyboard with options
            keyboard = []
            
            # Option 1: Complete Payment
            if "complete_payment" in action_options:
                needed_amount = action_options["complete_payment"]["amount_needed"]
                keyboard.append([
                    InlineKeyboardButton(
                        f"💳 Complete Payment (+${float(needed_amount):.2f})",
                        callback_data=f"pay_complete:{transaction_id}:{needed_amount}"
                    )
                ])
            
            # Option 2: Proceed with Partial
            if "proceed_partial" in action_options:
                escrow_amount = action_options["proceed_partial"]["escrow_amount"]
                keyboard.append([
                    InlineKeyboardButton(
                        f"📉 Proceed with ${float(escrow_amount):.2f} escrow",
                        callback_data=f"pay_partial:{transaction_id}:{escrow_amount}"
                    )
                ])
            
            # Option 3: Cancel & Refund to Wallet
            if "cancel_refund" in action_options:
                refund_amount = action_options["cancel_refund"]["refund_amount"]
                keyboard.append([
                    InlineKeyboardButton(
                        f"❌ Cancel & Refund to Wallet (${float(refund_amount):.2f})",
                        callback_data=f"pay_cancel:{transaction_id}:{refund_amount}"
                    )
                ])
            
            # Add timeout information
            keyboard.append([
                InlineKeyboardButton("⏰ Options expire in 10 minutes", callback_data="pay_info")
            ])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            if update.callback_query:
                await update.callback_query.edit_message_text(
                    message, 
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
            else:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=message,
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
                
        except Exception as e:
            logger.error(f"Error showing payment recovery options: {e}")
            error_message = "❌ Error loading payment options. Please contact support."
            
            if update.callback_query:
                await update.callback_query.edit_message_text(error_message)
            else:
                await context.bot.send_message(chat_id=user_id, text=error_message)
    
    @staticmethod
    async def handle_complete_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle user choosing to complete payment"""
        try:
            query = update.callback_query
            await safe_answer_callback_query(query, "⏳")
            await query.edit_message_text("⏳ Processing payment completion...")  # Instant visual feedback
            
            # Parse callback data
            _, transaction_id, amount_needed = query.data.split(':')
            user_id = query.from_user.id
            
            # SECURITY: Validate session and get server-side amounts
            from services.state_manager import StateManager
            
            state_manager = StateManager()
            session_key = f"payment_recovery_{user_id}_{transaction_id}"
            session_data = await state_manager.get_state(session_key)
            
            if not session_data:
                await query.edit_message_text("❌ Session expired. Please restart your payment.")
                return
            
            # SECURITY: Validate user ownership and session integrity
            if session_data.get("user_id") != user_id:
                await query.edit_message_text("❌ Access denied.")
                return
            
            # SECURITY: Use server-side calculated amounts, not client data
            amount_needed = abs(session_data.get("calculated_variance", 0))
            
            logger.info(f"User {user_id} chose to complete payment for {transaction_id} (+${float(amount_needed):.2f})")
            
            # For now, show a message that payment completion will be available soon
            # TODO: Integrate with payment address service when available
            
            message = "💳 **Complete Payment Feature**\n\n"
            message += f"**Amount Needed**: ${float(amount_needed):.2f}\n\n"
            message += "🔧 **This feature will be available soon!**\n"
            message += "For now, please choose one of the other options below:\n\n"
            message += "• **Proceed with partial amount**\n"
            message += "• **Cancel and get refund to wallet**"
            
            await query.edit_message_text(message, parse_mode='Markdown')
                
        except Exception as e:
            logger.error(f"Error in handle_complete_payment: {e}")
            await query.edit_message_text("❌ Error processing payment completion. Please contact support.")
    
    @staticmethod
    async def handle_proceed_partial(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle user choosing to proceed with partial amount"""
        try:
            query = update.callback_query
            await safe_answer_callback_query(query, "⏳")
            await query.edit_message_text("⏳ Processing partial payment...")  # Instant visual feedback
            
            # Parse callback data
            _, transaction_id, escrow_amount = query.data.split(':')
            user_id = query.from_user.id
            
            # Fetch escrow from database instead of relying on session (more reliable)
            from database import async_managed_session
            from sqlalchemy import select
            
            async with async_managed_session() as check_session:
                stmt = select(Escrow).where(Escrow.escrow_id == transaction_id)
                result = await check_session.execute(stmt)
                check_escrow = result.scalar_one_or_none()
                
                if not check_escrow:
                    await query.edit_message_text("❌ Escrow not found. Please contact support.")
                    return
                
                # SECURITY: Validate user ownership
                if check_escrow.buyer_id != user_id:
                    await query.edit_message_text("❌ Access denied.")
                    return
                
                # Use the amount from button (which came from the underpayment detection)
                # This is safe because we've validated ownership
                escrow_amount = Decimal(str(escrow_amount))
            
            logger.info(f"User {user_id} chose to proceed with partial escrow: ${float(escrow_amount):.2f} for {transaction_id}")
            
            # Process the escrow with reduced amount
            async with async_managed_session() as session:
                stmt = select(Escrow).where(Escrow.escrow_id == transaction_id)
                result = await session.execute(stmt)
                escrow = result.scalar_one_or_none()
                
                if not escrow:
                    await query.edit_message_text("❌ Escrow not found. Please contact support.")
                    return
                
                # CRITICAL FIX: For partial payments, preserve buyer_fee_amount for accurate refunds
                # The buyer already paid the fee, so we must record it for seller decline refunds
                # 
                # Example: Buyer creates $13 escrow + $5 fee = $18 total
                #          Buyer pays $12.75 (partial)
                #          Escrow proceeds with $7.75 ($12.75 - $5 fee)
                #          If seller declines: refund = $7.75 + $5 = $12.75 ✓
                
                # Get the original buyer fee that was paid
                original_buyer_fee = escrow.buyer_fee_amount if escrow.buyer_fee_amount else Decimal("0")
                
                # CRITICAL: Must satisfy constraints:
                # 1. fee_amount = buyer_fee_amount + seller_fee_amount
                # 2. total_amount = amount + fee_amount
                
                # Set amounts correctly
                amount = escrow_amount  # The adjusted escrow amount (received - buyer_fee)
                buyer_fee_amount = original_buyer_fee  # Preserve the fee the buyer paid
                seller_fee_amount = Decimal("0")  # Waive seller fee for partial payments
                fee_amount = buyer_fee_amount + seller_fee_amount  # Total fees
                total_amount = amount + fee_amount  # Total = escrow + fees (what buyer paid)
                
                logger.info(
                    f"💰 PARTIAL_PAYMENT_FINALIZED: Escrow {transaction_id} - "
                    f"Amount: ${float(amount):.2f}, Buyer Fee: ${float(buyer_fee_amount):.2f}, "
                    f"Total: ${float(total_amount):.2f} (ensures correct refund if seller declines)"
                )
                
                # CRITICAL: Calculate delivery deadline and auto-release time
                from sqlalchemy import func
                current_time_result = await session.execute(select(func.now()))
                current_time = current_time_result.scalar()
                
                # Get delivery hours from pricing_snapshot
                delivery_hours = 24  # Default to 24 hours
                if escrow.pricing_snapshot and 'delivery_hours' in escrow.pricing_snapshot:
                    delivery_hours = int(escrow.pricing_snapshot['delivery_hours'])
                
                delivery_deadline = current_time + timedelta(hours=delivery_hours) if current_time else None
                auto_release_at = delivery_deadline + timedelta(hours=24) if delivery_deadline else None
                
                logger.info(f"⏰ DELIVERY_DEADLINE_SET: Partial payment escrow {transaction_id} delivery countdown starts - {delivery_hours}h")
                
                # Update escrow to match payment
                stmt = sqlalchemy_update(Escrow).where(
                    Escrow.escrow_id == transaction_id
                ).values(
                    amount=amount,
                    fee_amount=fee_amount,
                    buyer_fee_amount=buyer_fee_amount,
                    seller_fee_amount=seller_fee_amount,
                    total_amount=total_amount,
                    status="payment_confirmed",
                    payment_confirmed_at=current_time,
                    delivery_deadline=delivery_deadline,
                    auto_release_at=auto_release_at
                )
                await session.execute(stmt)
                
                # Log the adjustment
                from models import Transaction
                from utils.universal_id_generator import UniversalIDGenerator
                
                adjustment_transaction = Transaction(
                    transaction_id=UniversalIDGenerator.generate_transaction_id(),
                    user_id=user_id,
                    transaction_type="admin_adjustment",
                    amount=escrow_amount,
                    currency="USD",
                    status="completed",
                    description=f"Escrow {transaction_id} adjusted to ${float(escrow_amount):.2f} due to partial payment acceptance",
                    confirmed_at=datetime.utcnow()
                )
                session.add(adjustment_transaction)
                await session.commit()
            
            # REMOVED: Seller and buyer notifications - webhook handler sends these
            # When buyer accepts partial payment, we only adjust the escrow amount here.
            # The webhook handler will detect the confirmed payment and send all notifications
            # (seller offer + buyer confirmation) to avoid duplicates
            logger.info(f"Escrow {transaction_id} adjusted for partial payment - webhook handler will send notifications")
            
            # Send enhanced confirmation to buyer with buttons and fee information
            try:
                async with async_managed_session() as confirm_session:
                    from sqlalchemy.orm import selectinload
                    stmt = select(Escrow).options(
                        selectinload(Escrow.seller)
                    ).where(Escrow.escrow_id == transaction_id)
                    result = await confirm_session.execute(stmt)
                    confirm_escrow = result.scalar_one_or_none()
                    
                    if confirm_escrow:
                        # Get seller display name
                        seller_display = "Seller"
                        if confirm_escrow.seller:
                            seller_username = confirm_escrow.seller.username
                            seller_first_name = confirm_escrow.seller.first_name
                            if seller_username:
                                seller_display = f"@{seller_username}"
                            elif seller_first_name:
                                seller_display = seller_first_name
                        
                        # Format delivery deadline
                        delivery_text = "Not set"
                        if confirm_escrow.delivery_deadline:
                            deadline = confirm_escrow.delivery_deadline
                            if isinstance(deadline, datetime):
                                delivery_text = deadline.strftime("%b %d, %Y %I:%M %p UTC")
                        
                        # Get last 6 chars of escrow ID for branding
                        display_id = transaction_id[-6:] if len(transaction_id) >= 6 else transaction_id
                        
                        # Calculate fee information
                        buyer_fee = Decimal(str(confirm_escrow.buyer_fee_amount or 0))
                        total_paid = escrow_amount + buyer_fee
                        
                        message = "🎉 Payment Confirmed!\n\n"
                        message += "✅ Status: Escrow Active\n"
                        message += f"💰 Amount: ${float(escrow_amount):.2f}\n"
                        message += f"💸 Total Paid: ${float(total_paid):.2f} (inc. ${float(buyer_fee):.2f} fee)\n"
                        message += f"🆔 Trade ID: #{display_id}\n"
                        message += f"👤 Seller: {seller_display}\n"
                        message += f"⏰ Delivery: {delivery_text}\n\n"
                        message += "Next Steps:\n"
                        message += "• Wait for seller confirmation\n"
                        message += "• Seller will deliver within deadline\n"
                        message += "• Your funds are secured in escrow\n\n"
                        message += "💡 Use /orders to track your trade"
                        
                        # Add buttons
                        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
                        keyboard = [
                            [InlineKeyboardButton("📋 My Trades", callback_data=f"view_trade_{confirm_escrow.id}")],
                            [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
                        ]
                        reply_markup = InlineKeyboardMarkup(keyboard)
                    else:
                        # Fallback if escrow not found
                        message = "✅ Escrow Confirmed\n\n"
                        message += f"Amount: ${float(escrow_amount):.2f}\n"
                        message += f"Escrow ID: {transaction_id}\n\n"
                        message += "🔒 Your escrow is now active!\n"
                        message += "💡 Use /orders to view details"
                        reply_markup = None
            except Exception as msg_error:
                logger.error(f"Error formatting buyer confirmation: {msg_error}")
                # Fallback message
                message = "✅ Escrow Confirmed\n\n"
                message += f"Amount: ${float(escrow_amount):.2f}\n"
                message += f"Escrow ID: {transaction_id}\n\n"
                message += "🔒 Your escrow is now active!\n"
                message += "💡 Use /orders to view details"
                reply_markup = None
            
            await query.edit_message_text(message, reply_markup=reply_markup)
                
        except Exception as e:
            logger.error(f"Error in handle_proceed_partial: {e}")
            await query.edit_message_text("❌ Error processing partial escrow. Please contact support.")
    
    @staticmethod
    async def handle_cancel_and_refund(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle user choosing to cancel and refund to wallet"""
        try:
            query = update.callback_query
            await safe_answer_callback_query(query, "⏳")
            await query.edit_message_text("⏳ Processing refund...")  # Instant visual feedback
            
            # Parse callback data
            _, transaction_id, refund_amount = query.data.split(':')
            user_id = query.from_user.id
            
            # Fetch escrow from database instead of relying on session (more reliable)
            from database import async_managed_session
            from sqlalchemy import select
            
            async with async_managed_session() as check_session:
                stmt = select(Escrow).where(Escrow.escrow_id == transaction_id)
                result = await check_session.execute(stmt)
                check_escrow = result.scalar_one_or_none()
                
                if not check_escrow:
                    await query.edit_message_text("❌ Escrow not found. Please contact support.")
                    return
                
                # SECURITY: Validate user ownership
                if check_escrow.buyer_id != user_id:
                    await query.edit_message_text("❌ Access denied.")
                    return
                
                # Use the amount from button (which came from the underpayment detection)
                # This is safe because we've validated ownership
                refund_amount = Decimal(str(refund_amount))
            
            logger.info(f"User {user_id} chose to cancel and refund ${float(refund_amount):.2f} to wallet for {transaction_id}")
            
            # Process refund to wallet balance
            from database import async_managed_session
            async with async_managed_session() as session:
                # Get escrow integer ID from escrow_id string for transaction linking
                from sqlalchemy import select
                escrow_stmt = select(Escrow).where(Escrow.escrow_id == transaction_id)
                escrow_result = await session.execute(escrow_stmt)
                escrow_record = escrow_result.scalar_one_or_none()
                
                escrow_int_id = escrow_record.id if escrow_record else None
                
                # FIXED: Added await and escrow_id to link refund transaction to escrow
                refund_success = await CryptoServiceAtomic.credit_user_wallet_atomic(
                    user_id=user_id,
                    amount=refund_amount,
                    currency="USD",
                    escrow_id=escrow_int_id,
                    transaction_type="escrow_refund",
                    description=f"Refund from cancelled escrow {transaction_id}: user chose wallet refund over completion",
                    session=session
                )
                
                if not refund_success:
                    await query.edit_message_text("❌ Error processing refund. Please contact support.")
                    return
                
                # Update escrow status to cancelled
                stmt = sqlalchemy_update(Escrow).where(
                    Escrow.escrow_id == transaction_id
                ).values(
                    status="cancelled"
                )
                await session.execute(stmt)
            
            message = "💰 **Refund Processed**\n\n"
            message += f"**Amount**: ${float(refund_amount):.2f}\n"
            message += "**Destination**: Your LockBay wallet\n\n"
            message += "✅ Funds are now available in your wallet balance!\n"
            message += "💡 You can use them for other transactions or withdraw to bank/crypto."
            
            # Add keyboard to view wallet or create new escrow
            keyboard = [
                [InlineKeyboardButton("💰 View Wallet", callback_data="menu_wallet")],
                [InlineKeyboardButton("🔒 Create New Escrow", callback_data="start_secure_trade")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                message, 
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
                
        except Exception as e:
            logger.error(f"Error in handle_cancel_and_refund: {e}")
            await query.edit_message_text("❌ Error processing refund. Please contact support.")


# Register the handlers for callback queries
async def register_payment_recovery_handlers(application):
    """Register payment recovery callback handlers"""
    from telegram.ext import CallbackQueryHandler
    
    # Payment completion handlers
    application.add_handler(CallbackQueryHandler(
        PaymentRecoveryHandler.handle_complete_payment,
        pattern=r'^pay_complete:'
    ))
    
    application.add_handler(CallbackQueryHandler(
        PaymentRecoveryHandler.handle_proceed_partial,
        pattern=r'^pay_partial:'
    ))
    
    application.add_handler(CallbackQueryHandler(
        PaymentRecoveryHandler.handle_cancel_and_refund,
        pattern=r'^pay_cancel:'
    ))
    
    logger.info("✅ Payment recovery handlers registered")