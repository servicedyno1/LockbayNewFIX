#!/usr/bin/env python3
"""
Fincra Nigeria Naira Payment Handler
Handles NGN payment flows for escrow and wallet funding
Superior pricing: 1% fees capped at NGN{int(Config.NGN_FEE_CAP_NAIRA)} (vs competitors at NGN2000)
"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler, MessageHandler, filters
from decimal import Decimal
from datetime import datetime
from database import async_managed_session
from models import User, Escrow, Transaction
from sqlalchemy import select
from utils.atomic_transactions import async_atomic_transaction
from services.fincra_service import fincra_service
from config import Config
from utils.normalizers import normalize_telegram_id
from utils.financial_audit_logger import (
    FinancialAuditLogger, 
    FinancialEventType, 
    EntityType, 
    FinancialContext
)
from utils.callback_utils import safe_answer_callback_query

logger = logging.getLogger(__name__)

# Initialize financial audit logger for comprehensive NGN payment flow tracking
audit_logger = FinancialAuditLogger()

class FincraPaymentHandler:
    """Handler for Fincra NGN payment operations"""

    @staticmethod
    async def start_wallet_funding(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Start NGN wallet funding process"""
        query = update.callback_query
        telegram_user_id = query.from_user.id
        
        # INSTANT FEEDBACK: Give immediate response before slow operations
        await safe_answer_callback_query(query, "⏳ Loading...")
        
        # CRITICAL FIX: Get database user ID instead of Telegram ID for audit
        async with async_managed_session() as session:
            stmt = select(User).where(User.telegram_id == normalize_telegram_id(telegram_user_id))
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()
            if not user:
                await query.edit_message_text("❌ User account not found. Please start over with /start")
                return
            db_user_id = user.id
        
        # AUDIT: Log NGN wallet funding initiation
        audit_logger.log_financial_event(
            event_type=FinancialEventType.NGN_WALLET_FUNDING_INITIATED,
            entity_type=EntityType.NGN_PAYMENT,
            entity_id=f"wallet_funding_{telegram_user_id}_{int(__import__('time').time())}",
            user_id=db_user_id,  # FIXED: Use database user ID, not Telegram ID
            financial_context=FinancialContext(currency="NGN"),
            previous_state=None,
            new_state="initiated",
            additional_data={
                "source": "fincra_payment.start_wallet_funding"
            }
        )

        # Import Config locally to avoid scoping issues
        from config import Config

        if not fincra_service.is_available():
            text = """NGN NGN Wallet Funding

❌ Service Unavailable

NGN payments are currently not configured.

For now, please use cryptocurrency funding options.
"""
            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "💎 Use Cryptocurrency",
                            callback_data="crypto_funding_start",
                        )
                    ],
                    [InlineKeyboardButton("⬅️ Back", callback_data="wallet_add_funds")],
                ]
            )
        else:
            # PERFORMANCE FIX: Don't fetch rate here - it delays button response by 2+ seconds
            # Rate calculation happens when user actually enters amount
            min_amount = int(Config.MIN_ESCROW_AMOUNT_USD)
            
            text = f"""💰 Fund Wallet (NGN)

Enter USD amount to add:

Range: ${min_amount} - $5,000 USD

💡 Current exchange rate will be shown after you enter amount.

Type amount (numbers only):"""

            keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Back", callback_data="wallet_add_funds")]]
            )

            # CRITICAL FIX: Set correct wallet_state for unified_text_router
            # The router expects "selecting_amount_ngn" not "ngn_funding_flow"
            # Use set_wallet_state() for proper Redis-backed session storage
            from handlers.wallet_direct import set_wallet_state
            from utils.callback_utils import safe_user_data_set
            await set_wallet_state(telegram_user_id, context, "selecting_amount_ngn")
            safe_user_data_set(context, "ngn_funding_flow", True)
            
            # TIMESTAMP FIX: Set database conversation_state with timestamp
            async with async_managed_session() as db_session:
                from utils.conversation_state_helper import set_conversation_state_db
                await set_conversation_state_db(telegram_user_id, "wallet_input", db_session)
                await db_session.commit()

        await query.edit_message_text(
            text, parse_mode="Markdown", reply_markup=keyboard
        )

    @staticmethod
    async def show_ngn_payment_options(
        update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """Show NGN payment method options"""
        query = update.callback_query
        # IMMEDIATE FEEDBACK: NGN payment options
        await safe_answer_callback_query(query, "🌍 NGN payment options")

        if not fincra_service.is_available():
            await query.edit_message_text(
                "❌ NGN payments are currently unavailable.\n"
                "Please contact support for assistance.",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "← Back to Payment Methods",
                                callback_data="payment_methods",
                            )
                        ]
                    ]
                ),
            )
            return

        # Get context data
        payment_context = context.user_data.get("payment_context", {})
        amount_usd = payment_context.get("amount_usd", Decimal("0"))

        if amount_usd <= 0:
            await query.edit_message_text(
                "❌ Invalid payment amount.\n"
                "Please start the payment process again.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("← Back", callback_data="back_to_main")]]
                ),
            )
            return

        # Convert USD to NGN using superior rates
        amount_ngn = await fincra_service.convert_usd_to_ngn(Decimal(str(amount_usd or 0)))

        if not amount_ngn:
            await query.edit_message_text(
                "❌ Unable to get current NGN exchange rate.\n"
                "Please try again later or use another payment method.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("← Back", callback_data="wallet_add_funds")]]
                ),
            )
            return

        # Calculate superior pricing for escrow payment
        markup_info = fincra_service.calculate_ngn_payment_markup(amount_ngn, purpose="escrow")

        # Show payment options  
        amount_text = f"${float(amount_usd):.2f}"
        fee_cap = int(Decimal(str(Config.NGN_FEE_CAP_NAIRA or 0)))
        markup_amount = Decimal(str(markup_info['markup_amount'] or 0))
        final_amount = Decimal(str(markup_info['final_amount'] or 0))
        
        # Format amounts with proper separators
        ngn_formatted = f"{amount_ngn:,}"
        markup_formatted = f"{markup_amount:,}"
        final_formatted = f"{final_amount:,}"
        fee_cap_formatted = f"{fee_cap:,}"
        
        text = f"""Pay with Nigerian Naira
Fast processing 1% fee capped at NGN{fee_cap_formatted}

Money Amount: {amount_text} USD
Amount in NGN: NGN{ngn_formatted}
Card Processing Fee: NGN{markup_formatted} (1% capped at NGN{fee_cap_formatted})
Total Amount: NGN{final_formatted}

Choose your payment method:
"""

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🏦 Bank Transfer", callback_data="fincra_bank_transfer"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "Card Payment Link", callback_data="fincra_payment_link"
                    )
                ],
                [InlineKeyboardButton("⬅️ Back", callback_data="wallet_add_funds")],
            ]
        )

        await query.edit_message_text(
            text, parse_mode="Markdown", reply_markup=keyboard
        )

    @staticmethod
    async def handle_bank_transfer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle bank transfer payment with Fincra virtual accounts"""
        query = update.callback_query
        # IMMEDIATE FEEDBACK: Bank transfer setup
        await safe_answer_callback_query(query, "🏦 Setting up bank transfer")

        # Get payment context
        payment_context = context.user_data.get("payment_context", {})
        amount_usd = payment_context.get("amount_usd", 0)
        purpose = payment_context.get("purpose", "wallet_funding")
        escrow_id = payment_context.get("escrow_id")

        if amount_usd <= 0:
            await query.edit_message_text("❌ Invalid payment amount.")
            return

        # Convert to NGN
        amount_ngn = await fincra_service.convert_usd_to_ngn(Decimal(str(amount_usd or 0)))
        if not amount_ngn:
            await query.edit_message_text(
                "❌ Unable to process payment. Exchange rate unavailable."
            )
            return

        # Create virtual account for bank transfer
        # Get database user ID from Telegram user ID
        async with async_managed_session() as session:
            stmt = select(User).where(User.telegram_id == normalize_telegram_id(query.from_user.id))
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()
            if not user:
                await query.edit_message_text("❌ User account not found. Please start over with /start")
                return
            db_user_id = user.id
        
        payment_data = await fincra_service.create_virtual_account(
            amount_ngn, db_user_id, purpose, escrow_id
        )

        if payment_data and payment_data.get("success"):
            # Determine purpose from payment context
            payment_context = context.user_data.get("payment_context", {})
            purpose = payment_context.get("purpose", "escrow")
            markup_info = fincra_service.calculate_ngn_payment_markup(amount_ngn, purpose=purpose)

            fee_cap = int(Decimal(str(Config.NGN_FEE_CAP_NAIRA or 0)))
            final_amount = Decimal(str(markup_info['final_amount'] or 0))
            markup_amount = Decimal(str(markup_info['markup_amount'] or 0))
            
            text = f"""🏦 Bank Transfer Payment
Superior 1% fee capped at ₦{fee_cap:,.2f}

Bank: {payment_data['bank_name']}
Account Number: `{payment_data['account_number']}`
Account Name: {payment_data['account_name']}
Amount: ₦{final_amount:,.2f}
Transaction ID: `{payment_data['reference']}`
Reference: {payment_data['reference']}

How to Pay:
1. Transfer exactly ₦{final_amount:,.2f} to the account above
2. Use this reference: {payment_data['reference']}
3. Your payment will be confirmed automatically within 2 minutes

Important: Transfer the exact amount shown above including processing fee.
Total Fee: Only ₦{markup_amount:,.2f} (1% capped at ₦{fee_cap:,.2f}) - Better than competitors!
"""

            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "❌ Cancel", callback_data="cancel_fincra_payment"
                        )
                    ]
                ]
            )

            # Store payment reference for status checking
            context.user_data["pending_fincra_payment"] = {
                "reference": payment_data["reference"],
                "amount_usd": amount_usd,
                "amount_ngn": Decimal(str(markup_info["final_amount"] or 0)),
                "purpose": purpose,
                "escrow_id": escrow_id,
                "bank_name": payment_data["bank_name"],
                "account_number": payment_data["account_number"],
                "account_name": payment_data["account_name"],
                "va_id": payment_data["va_id"],
            }

            await query.edit_message_text(
                text, parse_mode="Markdown", reply_markup=keyboard
            )
        else:
            await query.edit_message_text(
                "❌ Unable to create virtual account. Please try again later.",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "⬅️ Back", callback_data="fincra_ngn_payment"
                            )
                        ]
                    ]
                ),
            )

    @staticmethod
    async def handle_payment_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle payment link generation for card/online payments"""
        query = update.callback_query
        # IMMEDIATE FEEDBACK: Payment link generation
        await safe_answer_callback_query(query, "💳 Creating payment link")

        # Get payment context
        payment_context = context.user_data.get("payment_context", {})
        amount_usd = payment_context.get("amount_usd", 0)
        purpose = payment_context.get("purpose", "wallet_funding")
        escrow_id = payment_context.get("escrow_id")

        if amount_usd <= 0:
            await query.edit_message_text("❌ Invalid payment amount.")
            return

        # Convert to NGN
        amount_ngn = await fincra_service.convert_usd_to_ngn(Decimal(str(amount_usd or 0)))
        if not amount_ngn:
            await query.edit_message_text(
                "❌ Unable to process payment. Exchange rate unavailable."
            )
            return

        # Create payment link
        # Get database user ID from Telegram user ID
        async with async_managed_session() as session:
            stmt = select(User).where(User.telegram_id == normalize_telegram_id(query.from_user.id))
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()
            if not user:
                await query.edit_message_text("❌ User account not found. Please start over with /start")
                return
            db_user_id = user.id
        
        payment_data = await fincra_service.create_payment_link(
            amount_ngn, db_user_id, purpose, escrow_id
        )

        if payment_data and payment_data.get("success"):
            fee_cap = int(Decimal(str(Config.NGN_FEE_CAP_NAIRA or 0)))
            text = f"""💳 Secure Card Payment
Superior 1% fee capped at ₦{fee_cap:,.2f}

Amount: ${amount_usd} USD
NGN Amount: ₦{payment_data['amount_ngn']:,.2f}
Processing Fee: ₦{payment_data['markup_amount']:,.2f}
Reference: {payment_data['reference']}

Payment Methods Available:
- Nigerian Debit/Credit Cards
- Bank Transfer
- USSD Codes
- Mobile Money

Click the button below to complete payment securely:
"""

            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "Card Pay Now", url=payment_data["payment_link"]
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "❌ Cancel", callback_data="cancel_fincra_payment"
                        )
                    ],
                ]
            )

            # Store payment reference for status checking
            context.user_data["pending_fincra_payment"] = {
                "reference": payment_data["reference"],
                "amount_usd": amount_usd,
                "amount_ngn": payment_data["amount_ngn"],
                "purpose": purpose,
                "escrow_id": escrow_id,
                "fincra_ref": payment_data["fincra_ref"],
                "payment_link": payment_data["payment_link"],
            }

            await query.edit_message_text(
                text, parse_mode="Markdown", reply_markup=keyboard
            )
        else:
            await query.edit_message_text(
                "❌ Unable to create payment link. Please try again later.",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "⬅️ Back", callback_data="fincra_ngn_payment"
                            )
                        ]
                    ]
                ),
            )

    # Removed manual payment status check - payments auto-confirm via background jobs

    @staticmethod
    async def _process_successful_payment(
        query, context, payment_status, pending_payment
    ) -> int:
        """Process successful payment completion"""
        try:
            # Get user with async session
            async with async_managed_session() as session:
                stmt = select(User).where(User.telegram_id == normalize_telegram_id(query.from_user.id))
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()
                
                if not user:
                    await query.edit_message_text("❌ User not found.")
                    return

                amount_usd = Decimal(str(pending_payment.get("amount_usd", "0")))
                purpose = pending_payment.get("purpose", "wallet_funding")
                reference = payment_status["reference"]

                if purpose == "wallet_funding":
                    # Add funds to wallet - use the crypto service to credit wallet
                    from services.crypto import CryptoServiceAtomic

                    # FIXED: credit_user_wallet_atomic is NOT async, removed await
                    success = CryptoServiceAtomic.credit_user_wallet_atomic(
                        user.id,
                        Decimal(str(amount_usd or 0)),
                        "USD",
                        f"NGN payment via Fincra - {reference}",
                    )

                    if not success:
                        await query.edit_message_text(
                            "❌ Error processing payment. Please contact support."
                        )
                        return

                elif purpose == "escrow_payment":
                    # Use atomic transaction for escrow payment
                    escrow_id = pending_payment.get("escrow_id")
                    if escrow_id:
                        async with async_atomic_transaction() as txn_session:
                            # Get escrow
                            escrow_stmt = select(Escrow).where(Escrow.id == escrow_id)
                            escrow_result = await txn_session.execute(escrow_stmt)
                            escrow = escrow_result.scalar_one_or_none()
                            
                            if escrow:
                                escrow.status = "active"
                                escrow.deposit_confirmed_at = datetime.now()

                                # Create transaction record
                                transaction = Transaction(
                                    escrow_id=escrow.id,
                                    transaction_type="deposit",
                                    amount=Decimal(str(amount_usd or 0)),
                                    currency="NGN",
                                    blockchain_address=payment_status.get(
                                        "fincra_ref", reference
                                    ),
                                    status="completed",
                                )
                                txn_session.add(transaction)

            # Clear payment context
            context.user_data.pop("pending_fincra_payment", None)
            context.user_data.pop("payment_context", None)

            # Use wallet-specific notification service for wallet funding
            if purpose == 'wallet_funding':
                try:
                    from services.wallet_notification_service import wallet_notification_service
                    notification_sent = await wallet_notification_service.send_ngn_deposit_confirmation(
                        user_id=user.id,
                        amount_usd=Decimal(str(amount_usd)),
                        amount_ngn=Decimal(str(payment_status['amount'])),
                        reference=reference
                    )
                    
                    if notification_sent:
                        success_text = "Total Wallet funded successfully! Check your messages for details."
                    else:
                        success_text = f"""Total Wallet Funded!
Lightning Superior rates!

Money ${amount_usd} USD added to your wallet

Your wallet balance has been updated instantly!
Use /wallet to view your balance."""
                except Exception as e:
                    logger.warning(f"Failed to send wallet notification, using fallback: {e}")
                    success_text = f"""Total Wallet Funded!
Lightning Superior rates!

Money ${amount_usd} USD added to your wallet

Your wallet balance has been updated instantly!
Use /wallet to view your balance."""
            else:
                success_text = f"""Total Payment Successful!
Lightning Superior rates!

Transaction Details:
- Amount: ${amount_usd} USD
- NGN Amount: ₦{payment_status['amount']:,.2f}
- Reference: {reference}
- Method: NGN Bank Transfer
- Fee: Only 1% capped at ₦{int(Decimal(str(Config.NGN_FEE_CAP_NAIRA or 0))):,.2f}

Your escrow payment is confirmed successfully!"""

            keyboard = [
                [InlineKeyboardButton("Total Continue", callback_data="back_to_main")]
            ]

            await query.edit_message_text(
                success_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown",
            )

        except ValueError:
            # Provide specific error message for invalid amounts
            error_msg = "Invalid amount format"
            
            try:
                await query.edit_message_text(
                    f"❌ {error_msg}",
                    parse_mode='Markdown'
                )
            except Exception:
                pass
            return
            
        except Exception as e:
            logger.error(f"Error processing Fincra payment: {e}")
            await query.edit_message_text(
                "❌ Error processing payment. Please contact support.",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "🆘 Contact Support",
                                callback_data="contact_support",
                            )
                        ]
                    ]
                ),
            )

    @staticmethod
    async def handle_ngn_amount_input(
        update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """Handle NGN funding amount input"""
        # Only process if user is in NGN funding flow
        if not context.user_data.get("ngn_funding_flow"):
            return  # Not in NGN funding flow, ignore

        # INSTANT FEEDBACK: Show processing message immediately
        processing_msg = await update.message.reply_text("⏳ Processing your request...")

        try:
            # SECURITY FIX: Use secure parser instead of dangerous .replace(",", "")
            from utils.secure_amount_parser import SecureAmountParser
            
            input_text = update.message.text.strip()
            amount_decimal, validation_msg = SecureAmountParser.validate_and_parse(input_text, "$")
            amount_usd = Decimal(str(amount_decimal or 0))
            logger.info(f"🔒 {validation_msg}")

            # DYNAMIC MINIMUM VALIDATION: Check minimum funding amount
            min_funding_usd = getattr(Config, 'MIN_FINCRA_FUNDING_USD', 10)
            if amount_usd < min_funding_usd:
                await processing_msg.edit_text(
                    f"❌ Minimum Funding: ${float(min_funding_usd):.0f} USD\n\n"
                    f"Your amount: ${float(amount_usd):.2f} USD\n\n"
                    f"Please enter at least ${float(min_funding_usd):.0f} USD to continue.",
                    parse_mode="Markdown"
                )
                return

            if amount_usd > 5000:
                await processing_msg.edit_text(
                    "❌ Maximum funding amount is $5,000 USD per transaction. Please try again."
                )
                return

            # Convert to NGN using superior rates
            ngn_amount = await fincra_service.convert_usd_to_ngn(Decimal(str(amount_usd or 0)))
            if not ngn_amount:
                await processing_msg.edit_text(
                    "❌ Unable to get current exchange rate. Please try again later."
                )
                return

            # Calculate superior pricing with 2% wallet deposit markup
            markup_info = fincra_service.calculate_ngn_payment_markup(ngn_amount, purpose="wallet_funding")

            # Clear the funding flow flag
            context.user_data.pop("ngn_funding_flow", None)

            # Store amount and show payment options
            context.user_data["payment_context"] = {
                "amount_usd": amount_usd,
                "amount_ngn": ngn_amount,
                "purpose": "wallet_funding",
            }

            Decimal(str(markup_info['markup_amount'] or 0))
            Decimal(str(markup_info['markup_percentage'] or 0))
            Decimal(str(markup_info['final_amount'] or 0))
            
            text = f"""💰 Confirm NGN Wallet Funding

Amount: ${amount_usd} USD
NGN Amount: ₦{ngn_amount:,.2f}

Choose your payment method:"""

            # Generate payment URL for miniwebapp
            try:
                # Get database user ID from Telegram user ID
                async with async_managed_session() as session:
                    stmt = select(User).where(User.telegram_id == normalize_telegram_id(update.effective_user.id))
                    result = await session.execute(stmt)
                    user = result.scalar_one_or_none()
                    if not user:
                        await processing_msg.edit_text("❌ User account not found. Please start over with /start")
                        return
                    db_user_id = user.id
                
                payment_result = await fincra_service.create_payment_link(
                    amount_ngn=ngn_amount,
                    user_id=db_user_id,
                    purpose="wallet_funding"
                )
                
                if payment_result and payment_result.get('payment_link'):
                    from telegram import WebAppInfo
                    keyboard = InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    "🏦 Bank Transfer (₦{:,.0f})".format(ngn_amount),
                                    web_app=WebAppInfo(url=payment_result['payment_link'])
                                )
                            ],
                            [
                                InlineKeyboardButton(
                                    "❌ Cancel", callback_data="wallet_add_funds"
                                )
                            ],
                        ]
                    )
                else:
                    # Fallback if payment link generation fails
                    keyboard = InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    "🏦 Bank Transfer", callback_data="fincra_bank_transfer"
                                )
                            ],
                            [
                                InlineKeyboardButton(
                                    "❌ Cancel", callback_data="wallet_add_funds"
                                )
                            ],
                        ]
                    )
            except Exception as e:
                logger.error(f"Failed to generate payment link for wallet funding: {e}")
                # Fallback keyboard
                keyboard = InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "🏦 Bank Transfer", callback_data="fincra_bank_transfer"
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                "❌ Cancel", callback_data="wallet_add_funds"
                            )
                        ],
                    ]
                )

            # Replace processing message with final result
            await processing_msg.edit_text(
                text, parse_mode="Markdown", reply_markup=keyboard
            )

        except ValueError:
            await processing_msg.edit_text(
                "❌ Please enter a valid amount in USD (numbers only). Example: 50"
            )
        except Exception as e:
            logger.error(f"Error processing NGN amount input: {e}")
            await processing_msg.edit_text(
                "❌ Error processing amount. Please try again."
            )


def register_fincra_handlers(application):
    """Register Fincra payment handlers"""

    # Import filters for message handling

    # Add callback handlers for Fincra payments
    application.add_handler(
        CallbackQueryHandler(
            FincraPaymentHandler.show_ngn_payment_options,
            pattern="^fincra_ngn_payment$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            FincraPaymentHandler.handle_bank_transfer, pattern="^fincra_bank_transfer$"
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            FincraPaymentHandler.handle_payment_link, pattern="^fincra_payment_link$"
        )
    )

    # Removed manual payment status check handler - payments auto-confirm via background jobs

    # Message handler for NGN amount input - ONLY when in NGN funding flow
    # Uses group=2 to avoid blocking the unified text router (group=0)
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            FincraPaymentHandler.handle_ngn_amount_input,
        ),
        group=2
    )

    logger.info("Fincra payment handlers registered successfully")
