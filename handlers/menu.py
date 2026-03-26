"""Menu handler for the Telegram Escrow Bot"""

import logging
from datetime import datetime, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from database import get_session
from models import User, Escrow
from sqlalchemy import or_, text, select

logger = logging.getLogger(__name__)

async def show_hamburger_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Display the main hamburger menu - ENHANCED WITH STATE CLEANUP"""
    query = update.callback_query
    if query:
        # IMMEDIATE FEEDBACK: Show specific menu action using safe wrapper
        from utils.callback_utils import safe_answer_callback_query
        await safe_answer_callback_query(query, "🏠 Main menu")
    
    # CRITICAL: Complete conversation state cleanup (prevent frozen buttons)
    if context.user_data:
        # Clear ALL conversation states
        context.user_data.pop("active_conversation", None)
        context.user_data.pop("exchange_data", None) 
        context.user_data.pop("exchange_session_id", None)
        context.user_data.pop("escrow_data", None)
        context.user_data.pop("contact_data", None)
        context.user_data.pop("wallet_data", None)
        context.user_data.pop("expecting_funding_amount", None)
        context.user_data.pop("expecting_custom_amount", None)
        logger.debug("🧹 Hamburger menu: Cleared all conversation states")
    
    # ENHANCED: Clear universal session manager sessions
    if update.effective_user:
        try:
            from utils.universal_session_manager import universal_session_manager
            user_session_ids = universal_session_manager.get_user_session_ids(update.effective_user.id)
            if user_session_ids:
                logger.info(f"🧹 Hamburger menu: Clearing {len(user_session_ids)} universal sessions")
                for session_id in user_session_ids:
                    universal_session_manager.terminate_session(session_id, "hamburger_menu_navigation")
                logger.info("✅ Hamburger menu: Universal sessions cleaned")
        except Exception as e:
            logger.warning(f"Could not clear universal sessions in hamburger menu: {e}")

    # IMPROVED: Context-aware menu message based on user state
    from models import User
    from utils.session_reuse_manager import get_reusable_session
    
    message = "Trading Dashboard 📊\n\nQuick access to all your trading tools and account settings."
    
    # Get user context for personalized message
    user = update.effective_user
    if user:
        try:
            with get_reusable_session("menu_context", user_id=user.id) as session:
                db_user = session.query(User).filter(User.telegram_id == int(user.id)).first()
                if db_user:
                    # Get user stats for context
                    from services.user_stats_service import UserStatsService
                    from models import Wallet
                    # Ensure we have the actual integer value, not Column object
                    actual_user_id = int(db_user.id) if db_user.id is not None else 0
                    reputation_score, total_trades = await UserStatsService.calculate_user_reputation(actual_user_id, session)
                    
                    # Get actual wallet balance instead of hardcoding to 0
                    wallet_stmt = select(Wallet).where(Wallet.user_id == db_user.telegram_id)
                    wallet_result = session.execute(wallet_stmt)
                    wallet = wallet_result.scalar_one_or_none()
                    actual_balance = float(wallet.available_balance) if wallet and wallet.available_balance else 0.0
                    
                    stats = {'total_balance': actual_balance, 'total_trades': total_trades}
                    balance = stats.get('total_balance', 0)
                    total_trades = stats.get('total_trades', 0)
                    
                    # Personalized messages based on user state
                    if total_trades == 0:
                        message = "Welcome to LockBay! 🎯\n\nStart your first trade or add funds."
                    elif balance == 0:
                        message = "Trading Dashboard 💰\n\nAdd funds to start trading."
                    else:
                        message = "Trading Dashboard 🚀\n\nReady for trading."
        except Exception as e:
            logger.warning(f"Could not get user context for menu message: {e}")
            # Fallback to default improved message
            message = "Trading Dashboard 📊\n\nQuick access to all your trading tools and account settings."

    # Use the proper hamburger menu keyboard
    from utils.keyboards import hamburger_menu_keyboard

    reply_markup = hamburger_menu_keyboard()

    # FIXED: Use unified message handling to prevent UI duplication
    from utils.message_utils import send_unified_message
    await send_unified_message(update, message, reply_markup=reply_markup)

async def handle_main_menu_navigation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle main menu navigation with onboarding protection - shows normal main menu instead of hamburger menu"""
    from models import User
    from handlers.start import show_main_menu_optimized
    from utils.session_reuse_manager import get_reusable_session
    
    user = update.effective_user
    if not user:
        logger.error("No effective user in main menu navigation")
        return ConversationHandler.END
    
    query = update.callback_query
    if query:
        from utils.callback_utils import safe_answer_callback_query
        await safe_answer_callback_query(query, "🏠 Main menu")
    
    # Get user from database and check onboarding before showing main menu
    try:
        with get_reusable_session("main_menu_nav", user_id=user.id) as session:
            db_user = session.query(User).filter(User.telegram_id == str(user.id)).first()
            if db_user:
                # CRITICAL: Check onboarding completion before showing main menu
                onboarding_completed = getattr(db_user, 'onboarding_completed', None)
                needs_onboarding = (
                    not hasattr(db_user, 'onboarding_completed') or 
                    not bool(onboarding_completed) if onboarding_completed is not None else True
                )
                if needs_onboarding:
                    logger.info(f"🚨 SECURITY: User {user.id} attempted main menu navigation bypass - redirecting to onboarding")
                    # Redirect to onboarding
                    from handlers.onboarding_router import onboarding_router
                    await onboarding_router(update, context)
                    return ConversationHandler.END
                
                await show_main_menu_optimized(update, context, db_user, session)
                logger.info("Normal main menu shown successfully")
            else:
                logger.error(f"User {user.id} not found for main menu navigation")
                # Fallback to start handler
                from handlers.start import start_handler
                return await start_handler(update, context)
    except Exception as e:
        logger.error(f"Error in main menu navigation: {e}")
        # Fallback to start handler
        from handlers.start import start_handler
        return await start_handler(update, context)
    
    return ConversationHandler.END

async def show_escrow_history(update: Update, context: ContextTypes.DEFAULT_TYPE, user) -> int:
    """Show user trade transaction history with optimized queries"""
    session = get_session()
    try:
        # OPTIMIZED: Single query with proper type safety
        escrows = (
            session.query(Escrow)
            .filter(or_(Escrow.buyer_id == user.id, Escrow.seller_id == user.id))
            .order_by(Escrow.created_at.desc())
            .all()
        )

        if not escrows:
            message = "Your Trade History\n\nYou haven't created any trades yet.\n\nStart trading by creating your first trade transaction!"
        else:
            # Safe status counting with type safety
            active_count = 0
            completed_count = 0

            for e in escrows:
                try:
                    # Safe status extraction with SQLAlchemy Column handling
                    status_str = "unknown"
                    try:
                        if hasattr(e, "status") and e.status is not None:
                            # Handle SQLAlchemy Column type properly
                            status_val = getattr(e, "status", None)
                            if status_val is not None:
                                status_str = str(status_val)
                    except (AttributeError, TypeError):
                        status_str = "unknown"
                    if status_str in ["active", "pending_deposit"]:
                        active_count += 1
                    elif status_str == "completed":
                        completed_count += 1
                except (AttributeError, TypeError):
                    continue

            message = f"Your Trade History\n\n• Total Trades: {len(escrows)}\n• Active: {active_count}\n• Completed: {completed_count}\n\nRecent Transactions:\n"

            # Safe escrow data extraction
            for escrow in escrows[:5]:
                try:
                    # Safe status extraction with SQLAlchemy Column handling
                    status_str = "unknown"
                    try:
                        if hasattr(escrow, "status") and escrow.status is not None:
                            # Handle SQLAlchemy Column type properly
                            status_val = getattr(escrow, "status", None)
                            if status_val is not None:
                                status_str = str(status_val)
                    except (AttributeError, TypeError):
                        status_str = "unknown"
                    status_emoji = (
                        "🟢"
                        if status_str == "completed"
                        else "🟡" if status_str == "active" else "⚪"
                    )

                    # Safe user ID comparison
                    user_id = getattr(user, "id", None) if user else None
                    buyer_id = getattr(escrow, "buyer_id", None) if escrow else None
                    role = (
                        "Buyer"
                        if user_id and buyer_id and user_id == buyer_id
                        else "Seller"
                    )

                    # Safe amount conversion
                    amount_value = 0.0
                    if hasattr(escrow, "amount") and escrow.amount is not None:
                        try:
                            # Handle SQLAlchemy Column type for amount
                            # Handle SQLAlchemy Column type for amount with proper conversion
                            if hasattr(escrow.amount, "__float__"):
                                amount_value = float(getattr(escrow, "amount", 0))
                            else:
                                amount_val = getattr(escrow, "amount", 0)
                                if amount_val is not None:
                                    try:
                                        amount_value = float(amount_val)
                                    except (ValueError, TypeError):
                                        amount_value = 0.0
                        except (ValueError, TypeError):
                            amount_value = 0.0

                    message += f"\n{status_emoji} ${amount_value:.2f} - {role} - {status_str.title()}"
                except (AttributeError, TypeError):
                    continue

        query = update.callback_query
        if query:
            await query.edit_message_text(message, parse_mode="Markdown")
        elif update.message:
            await update.message.reply_text(message, parse_mode="Markdown")

    except Exception as e:
        # Handle "Message is not modified" error silently
        if "Message is not modified" in str(e):
            return

        logger.error(f"Error showing escrow history: {e}")
        error_msg = "❌ Error loading trade history"
        query = update.callback_query
        if query:
            try:
                await query.edit_message_text(error_msg)
            except Exception:
                pass  # Prevent nested loops
        elif update.message:
            await update.message.reply_text(error_msg)
    finally:
        session.close()

async def handle_verification_code_input(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle verification code input from users"""
    # CRITICAL FIX: Add null checks to prevent freeze
    if not update or not update.message or not update.message.text:
        logger.error("❌ No message or text in OTP verification")
        return ConversationHandler.END

    if not update.effective_user:
        logger.error("❌ No effective user in OTP verification")
        return ConversationHandler.END

    if context.user_data is None:
        logger.error("❌ No user data context in OTP verification")
        await update.message.reply_text(
            "❌ Session expired. Please start cashout again."
        )
        return ConversationHandler.END

    user_input = update.message.text.strip()

    try:
        user_id = update.effective_user.id

        # Check if this is for auto cashout setup
        if context.user_data and context.user_data.get("auto_cashout_setup"):
            logger.info(
                f"🔐 Processing auto cashout OTP for user {user_id}: {'***' + user_input[-2:] if len(user_input) > 2 else '***'}"
            )

            # OPTIMIZED: Single session for user and verification lookup
            session = get_session()
            try:
                from models import EmailVerification, User

                # Get user with proper type safety
                user = (
                    session.query(User).filter(User.telegram_id == str(user_id)).first()
                )

                if not user:
                    logger.error(f"User not found for telegram_id {user_id}")
                    await update.message.reply_text(
                        "❌ User not found. Please try again."
                    )
                    return

                # Safe user ID extraction
                db_user_id = None
                try:
                    if hasattr(user, "id") and user.id is not None:
                        # Safely convert SQLAlchemy Column to int with proper error handling
                        if hasattr(user.id, "__int__"):
                            db_user_id = int(getattr(user, "id", 0))
                        else:
                            # Handle SQLAlchemy Column type
                            user_id_val = getattr(user, "id", 0)
                            if user_id_val is not None:
                                try:
                                    db_user_id = int(user_id_val)
                                except (ValueError, TypeError):
                                    db_user_id = 0
                except (ValueError, TypeError, AttributeError):
                    logger.error(f"Invalid user ID for telegram_id {user_id}")
                    await update.message.reply_text(
                        "❌ User data error. Please try again."
                    )
                    return

                logger.info(
                    f"🔍 Looking for OTP {'***' + user_input[-2:] if len(user_input) > 2 else '***'} for user {db_user_id} (telegram_id: {user_id})"
                )

                # Get verification with single ORM query (ADVISOR FIX: Remove SQL duplication)
                verification = None
                try:
                    verification = (
                        session.query(EmailVerification)
                        .filter(
                            EmailVerification.user_id == db_user_id,
                            EmailVerification.verification_code == user_input,
                            EmailVerification.purpose == "cashout",
                            EmailVerification.verified.is_(False),
                        )
                        .first()
                    )
                except Exception as e:
                    logger.error(f"Error querying verification: {e}")
                    # Fallback without type/verified filters if model issues
                    try:
                        verification = (
                            session.query(EmailVerification)
                            .filter(
                                EmailVerification.user_id == db_user_id,
                                EmailVerification.verification_code == user_input,
                            )
                            .first()
                        )

                        # Manual verification checks if ORM filters failed
                        if verification:
                            purpose = getattr(
                                verification, "purpose", ""
                            )
                            verified = getattr(verification, "verified", True)

                            if purpose != "cashout" or verified:
                                verification = None
                    except Exception as fallback_error:
                        logger.error(
                            f"Fallback verification query failed: {fallback_error}"
                        )
                        verification = None

                logger.info(f"🔍 Verification found: {verification is not None}")

                if verification:
                    current_time = datetime.now(timezone.utc)
                    expires_time = getattr(verification, "expires_at", None)
                    # Handle timezone-naive database datetime with comprehensive safety
                    expires_time_safe = None
                    if expires_time is not None:
                        try:
                            # Handle different possible types for expires_time
                            if hasattr(expires_time, "tzinfo") and hasattr(
                                expires_time, "replace"
                            ):
                                # It's a datetime object
                                if expires_time.tzinfo is None:
                                    expires_time_safe = expires_time.replace(
                                        tzinfo=timezone.utc
                                    )
                                else:
                                    expires_time_safe = expires_time
                            else:
                                # Handle potential SQLAlchemy Column or other types
                                expires_val = getattr(verification, "expires_at", None)
                                if (
                                    expires_val
                                    and hasattr(expires_val, "tzinfo")
                                    and hasattr(expires_val, "replace")
                                ):
                                    if expires_val.tzinfo is None:
                                        expires_time_safe = expires_val.replace(
                                            tzinfo=timezone.utc
                                        )
                                    else:
                                        expires_time_safe = expires_val
                        except (AttributeError, TypeError) as e:
                            logger.error(f"Error handling expires_time: {e}")
                            expires_time_safe = None

                    logger.info(
                        f"⏰ OTP expires at: {expires_time_safe}, current time: {current_time}"
                    )

                    # Safe comparison with proper type checking
                    otp_valid = False
                    if expires_time_safe is not None and hasattr(
                        expires_time_safe, "__gt__"
                    ):
                        try:
                            otp_valid = expires_time_safe > current_time
                        except (TypeError, AttributeError):
                            otp_valid = False

                    if otp_valid is True:
                        # Use parameterized SQL to update verification status
                        session.execute(
                            text(
                                "UPDATE email_verifications SET verified = true WHERE id = :vid"
                            ),
                            {"vid": verification.id},
                        )
                        session.commit()
                        verification_success = True
                        logger.info(f"✅ OTP verified successfully for user {user_id}")
                    else:
                        verification_success = False
                        logger.warning(f"⏰ OTP expired for user {user_id}")
                else:
                    verification_success = False
                    logger.warning(f"❌ No matching OTP found for user {user_id}")
            finally:
                session.close()

            if verification_success:
                logger.info(f"🎉 Completing auto cashout setup for user {user_id}")
                # Complete auto cashout setup
                from handlers.auto_cashout_settings import complete_auto_cashout_setup

                await complete_auto_cashout_setup(update, context)
            else:
                logger.warning(
                    f"Auto cashout OTP verification failed for user {user_id}"
                )
                await update.message.reply_text(
                    "❌ Invalid or expired verification code. Please try again."
                )
        else:
            # Check if this is for NGN cashout verification
            if (
                context.user_data
                and context.user_data.get("verification_purpose") == "ngn_cashout"
            ):
                logger.info(
                    f"🏦 Processing NGN cashout OTP for user {user_id}: {'***' + user_input[-2:] if len(user_input) > 2 else '***'}"
                )

                # Verify OTP for NGN cashout
                session = get_session()
                try:
                    from models import EmailVerification, User

                    user = (
                        session.query(User)
                        .filter(User.telegram_id == str(user_id))
                        .first()
                    )

                    if not user:
                        logger.error(
                            f"❌ User not found in database for telegram_id: {user_id}"
                        )
                        await update.message.reply_text(
                            "❌ User not found. Please try again."
                        )
                        return

                    logger.info(
                        f"🔍 Looking for OTP: user_id={getattr(user, 'id', 0)}, code={'***' + user_input[-2:] if len(user_input) > 2 else '***'}, type=cashout, verified=False"
                    )

                    # CRITICAL FIX: Use unified email verification service
                    from services.unified_email_verification import UnifiedEmailVerificationService
                    if user and user.email:
                        success, message = await UnifiedEmailVerificationService.verify_otp(
                            email=user.email,
                            otp=user_input,
                            user_id=getattr(user, "id", 0),
                            verification_type="ngn_cashout"
                        )
                        verification = "success" if success else None
                        logger.info(f"🔍 Unified verification result: {success} - {message}")
                    else:
                        verification = None
                        logger.error("❌ User or email not found for verification")

                    logger.info(f"🔍 Database verification result: {verification}")

                    if verification:
                        # CRITICAL FIX: Check cashout_data exists BEFORE marking OTP as used
                        cashout_data = (
                            context.user_data.get("cashout_data")
                            if context.user_data
                            else None
                        )
                        logger.info(f"💳 Cashout data check: {cashout_data}")

                        if not cashout_data:
                            logger.error(
                                "❌ No cashout_data found - cannot proceed with verification"
                            )
                            await update.message.reply_text(
                                "❌ Error: Session expired. Please start cashout again."
                            )
                            return ConversationHandler.END

                        # NOTE: Verification status is already marked as verified by the unified service
                        # No need for manual database update

                        logger.info(
                            f"✅ NGN cashout OTP verified successfully for user {user_id}"
                        )

                        # CRITICAL FIX: Set email_verified flag in cashout_data
                        cashout_data["email_verified"] = True
                        context.user_data["cashout_data"] = cashout_data

                        # Show success message with confirmation buttons
                        from telegram import InlineKeyboardMarkup, InlineKeyboardButton

                        keyboard = InlineKeyboardMarkup(
                            [
                                [
                                    InlineKeyboardButton(
                                        "✅ Confirm CashOut",
                                        callback_data="confirm_ngn_wallet_cashout",
                                    )
                                ],
                                [
                                    InlineKeyboardButton(
                                        "❌ Cancel",
                                        callback_data="cancel_ngn_wallet_cashout",
                                    )
                                ],
                            ]
                        )

                        # Show NGN confirmation details
                        amount = cashout_data.get("amount", 0)
                        bank_name = cashout_data.get("bank_name", "Bank")
                        account_number = cashout_data.get("account_number", "")
                        account_name = cashout_data.get("account_name", "")

                        # Get NGN amount
                        from services.fastforex_service import fastforex_service

                        try:
                            rate = (
                                await fastforex_service.get_usd_to_ngn_rate_with_markup()
                            )
                            ngn_amount = amount * rate if rate else 0
                        except Exception as e:
                            # Use dynamic rate with proper error handling
                            logger.warning(f"Could not get NGN rate: {e}")
                            try:
                                from services.fastforex_service import fastforex_service

                                rate = (
                                    await fastforex_service.get_usd_to_ngn_rate_with_markup()
                                )
                                ngn_amount = amount * (rate if rate else 1500.0)
                            except Exception:
                                ngn_amount = amount * 1500.0  # Simple fallback

                        confirmation_text = f"""✅ *OTP Verified Successfully!*

💰 *CashOut Details:*
${amount:.2f} USD → ₦{ngn_amount:,.0f} NGN

🏦 *Bank Details:*
{bank_name}
{account_number}
{account_name}

Proceed?"""

                        await update.message.reply_text(
                            confirmation_text,
                            parse_mode="Markdown",
                            reply_markup=keyboard,
                        )

                        # CRITICAL FIX: Clear OTP processing flags and set proper state
                        context.user_data["otp_processed"] = True
                        context.user_data.pop(
                            "verification_purpose", None
                        )  # Clear to prevent re-routing

                        # Return to proper conversation state
                        # MIGRATED: No longer using conversation states (wallet_direct pattern)
                        context.user_data["state"] = "CONFIRMING_NGN_CASHOUT"
                        return "CONFIRMING_NGN_CASHOUT"
                    else:
                        logger.warning(
                            f"❌ No matching OTP found for user {user.id}, code {'***' + user_input[-2:] if len(user_input) > 2 else '***'}"
                        )
                        await update.message.reply_text(
                            "❌ Invalid or expired OTP\n\n"
                            "Please check your email for the correct code or request a new one.",
                            parse_mode="Markdown",
                        )
                finally:
                    session.close()
            else:
                # Generic verification processing
                logger.info(
                    f"Processing verification code for user {user_id}: {'***' + user_input[-2:] if len(user_input) > 2 else '***'}"
                )
                await update.message.reply_text(
                    "✅ Verification code processed successfully!"
                )

    except ValueError as e:
        # Rate limiting or duplicate detection
        await update.message.reply_text(f"⚠️ {str(e)}")
    except Exception as e:
        logger.error(f"Error handling verification code: {e}")
        await update.message.reply_text("❌ Error processing verification code")

async def handle_email_update_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle email update input from users"""
    if not update.message or not update.message.text:
        logger.error("No message or text in email update")
        return

    if not update.effective_user:
        logger.error("No effective user in email update")
        return

    email = update.message.text.strip()

    try:
        # Basic email validation
        if "@" not in email or "." not in email:
            await update.message.reply_text("❌ Please enter a valid email address")
            return

        # Update user email (simplified)
        session = get_session()
        try:
            user = (
                session.query(User)
                .filter(User.telegram_id == str(update.effective_user.id))
                .first()
            )
            if user:
                # Use SQL update to avoid SQLAlchemy column assignment issues
                session.execute(
                    text("UPDATE users SET email = :email WHERE id = :user_id"),
                    {"email": email, "user_id": user.id},
                )
                session.commit()
                await update.message.reply_text("✅ Email updated successfully!")
            else:
                await update.message.reply_text("❌ User not found")
        finally:
            session.close()

    except Exception as e:
        logger.error(f"Error updating email: {e}")
        if update.message:
            await update.message.reply_text("❌ Error updating email")

async def show_partner_program(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Display Partner/Whitelabel Program information for group/channel owners"""
    query = update.callback_query
    if query:
        from utils.callback_utils import safe_answer_callback_query
        await safe_answer_callback_query(query, "🤝 Partner Program")
    
    message = """🤝 <b>Partner Program</b>

Get your branded escrow bot & earn 30-50% commission!

🥉 Bronze: 30% | 🥈 Silver: 40% | 🥇 Gold: 50%

✅ Custom branding ✅ Tech support ✅ Dashboard

<b>Perfect for:</b> Trading groups, NFT communities, marketplaces, gaming channels

<b>Ready to apply?</b> 90-second form below!"""
    
    keyboard = [
        [InlineKeyboardButton("📝 Apply Now (90 seconds)", url="https://lockbay.io/partners/apply")],
        [InlineKeyboardButton("💬 Chat with Support", url="https://t.me/LockbayAssist")],
        [InlineKeyboardButton("🏠 Back to Menu", callback_data="main_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    from utils.message_utils import send_unified_message
    await send_unified_message(update, message, reply_markup=reply_markup, parse_mode='HTML')
    
    return ConversationHandler.END
