"""Command handlers for Telegram Bot Menu Button"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from models import User, Wallet, Escrow, Rating
from database import SessionLocal, async_managed_session
from sqlalchemy import func, select
from utils.keyboards import main_menu_keyboard
from utils.branding import UserRetentionElements
from utils.markdown_escaping import (
    escape_markdown,
)
from utils.data_sanitizer import safe_error_log
from config import Config
from utils.user_access_control import require_onboarding
from utils.callback_utils import safe_answer_callback_query

# Enhanced branding imports
from utils.branding_utils import BrandingUtils, make_header, make_trust_footer
from utils.trusted_trader import TrustedTraderSystem

logger = logging.getLogger(__name__)

async def show_main_menu_with_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE, user: User, message: str
) -> int:
    """Show main menu with a custom message - OPTIMIZED with async queries"""
    menu_text = f"""
🏠 {Config.PLATFORM_NAME}

{message}

Hi {escape_markdown(str(user.first_name) if user.first_name else 'there')}! 👋
📊 {UserRetentionElements.get_reputation_display(float(getattr(user, 'reputation_score', 0) or 0), int(getattr(user, 'total_ratings', 0) or 0))} | 🤝 {int(getattr(user, 'total_trades', 0) or 0)} trades
"""

    # PERFORMANCE FIX: Use async query instead of blocking sync query
    async with async_managed_session() as session:
        wallet_stmt = select(Wallet).where(
            Wallet.user_id == user.id, 
            Wallet.currency == "USD"
        )
        wallet_result = await session.execute(wallet_stmt)
        wallet = wallet_result.scalar_one_or_none()
        
        balance = (
            float(getattr(wallet, "balance", 0))
            if wallet and getattr(wallet, "balance", None) is not None
            else 0.0
        )
        total_trades = int(getattr(user, "total_trades", 0) or 0) if user else 0

    keyboard = main_menu_keyboard(
        balance=balance, total_trades=total_trades, active_escrows=0,
        user_telegram_id=str(update.effective_user.id) if update.effective_user else None
    )
    if update.message:
        await update.message.reply_text(
            menu_text, parse_mode="Markdown", reply_markup=keyboard
        )
    return 0

async def get_user_from_update(update: Update) -> User | None:
    """Get user from database based on update - OPTIMIZED with async query"""
    if not update.effective_user:
        logger.warning("🔍 No effective user in update")
        return None
    
    telegram_id = update.effective_user.id
    logger.info(f"🔍 Getting user from update for user {telegram_id}")
    
    # PERFORMANCE FIX: Use async query instead of blocking sync query
    try:
        async with async_managed_session() as session:
            stmt = select(User).where(User.telegram_id == telegram_id)
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()
            logger.info(f"🔍 Database query completed, user found: {user is not None}")
            return user
    except Exception as e:
        logger.error(f"🔍 Error getting user from update: {e}")
        return None

async def get_user_with_wallet_from_update(update: Update) -> tuple[User | None, float]:
    """
    PERFORMANCE OPTIMIZED: Get user and wallet balance in single database round-trip
    Returns: (user, balance) tuple
    """
    if not update.effective_user:
        logger.warning("🔍 No effective user in update")
        return None, 0.0
    
    telegram_id = update.effective_user.id
    logger.info(f"⚡ OPTIMIZED: Fetching user + wallet in single query for {telegram_id}")
    
    try:
        async with async_managed_session() as session:
            # PERFORMANCE: Single query fetches both user and wallet
            stmt = (
                select(User, Wallet)
                .outerjoin(Wallet, (Wallet.user_id == User.id) & (Wallet.currency == "USD"))
                .where(User.telegram_id == telegram_id)
            )
            result = await session.execute(stmt)
            row = result.first()
            
            if not row:
                logger.info(f"🔍 User not found: {telegram_id}")
                return None, 0.0
            
            user, wallet = row
            balance = float(getattr(wallet, "balance", 0)) if wallet else 0.0
            
            logger.info(f"⚡ OPTIMIZED: Query completed - user found with balance ${balance}")
            return user, balance
            
    except Exception as e:
        logger.error(f"🔍 Error getting user with wallet: {e}")
        return None, 0.0

@require_onboarding
async def exchange_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /exchange command with access control"""
    # Check if exchange features are enabled
    if not Config.ENABLE_EXCHANGE_FEATURES:
        if update.message:
            await update.message.reply_text("⚠️ Exchange features are currently unavailable. Please try again later.")
        return 0
    
    # Check access control
    if update.effective_user:
        from utils.user_access_control import check_feature_access, get_access_denied_message
        user_telegram_id = str(update.effective_user.id)
        
        if not check_feature_access(user_telegram_id, "exchange"):
            denied_message = get_access_denied_message("exchange")
            if update.message:
                await update.message.reply_text(denied_message)
            return 0
    
    from handlers.exchange_handler import ExchangeHandler
    await ExchangeHandler.start_exchange(update, context)
    return 0

@require_onboarding
async def create_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /create command"""
    user = await get_user_from_update(update)

    if not user:
        message_text = "👋 Welcome! Register first with /start"
        if update.message:
            await update.message.reply_text(message_text)
        return 0

    # Show main menu with create escrow highlighted
    await show_main_menu_with_message(
        update,
        context,
        user,
        "🚀 Ready to trade? Tap 'Create New Trade' below!",
    )
    return 0

@require_onboarding
async def escrows_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /trades command"""
    # IMMEDIATE FEEDBACK: Escrows command
    if update.callback_query:
        await safe_answer_callback_query(update.callback_query, "📋 Loading trades")

    user = await get_user_from_update(update)

    if not user:
        message_text = "👋 Welcome! Register with /start first!"
        if update.callback_query:
            await update.callback_query.edit_message_text(message_text)
        elif update.message:
            await update.message.reply_text(message_text)
        return 0

    # Show consolidated trades & messages interface
    from handlers.messages_hub import show_trades_messages_hub
    await show_trades_messages_hub(update, context)
    return 0

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /help command"""
    from utils.fee_policy_messages import FeePolicyMessages
    
    # Get user and check onboarding status
    user = await get_user_from_update(update)
    is_onboarded = user and getattr(user, 'onboarding_completed', False)
    
    # MOBILE-OPTIMIZED: Compact 5-line help message
    help_text = f"""🔒 **{Config.PLATFORM_NAME} Help**

🚀 Quick Exchange • Secure Trade
{FeePolicyMessages.get_fee_policy_short()}

Commands: /start /wallet /help • Support: {Config.SUPPORT_EMAIL}"""

    if not is_onboarded:
        # For non-onboarded users: simplified help with /start guidance, NO main menu
        non_onboarded_text = f"""🔒 **{Config.PLATFORM_NAME}**

🛡️ **Escrow-Protected Crypto Trading**
💰 Safe payments • Instant cashouts
{FeePolicyMessages.get_fee_policy_short()}

⚠️ **Complete setup to get started:** /start

Support: {Config.SUPPORT_EMAIL}"""
        
        # Simple keyboard with /start button only
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🚀 Start Onboarding", callback_data="start_onboarding")]
        ])
        
        try:
            from utils.message_utils import send_unified_message
            await send_unified_message(
                update, non_onboarded_text, reply_markup=keyboard, parse_mode="Markdown"
            )
        except Exception as e:
            safe_error = safe_error_log(e)
            logger.error(f"Help command Markdown error (non-onboarded): {safe_error}")
            
            # Fallback without formatting
            non_onboarded_plain = f"""🔒 {Config.PLATFORM_NAME}

🛡️ Escrow-Protected Crypto Trading
💰 Safe payments • Instant cashouts
5% fee • Refundable on early cancellation

⚠️ Complete setup to get started: /start

Support: {Config.SUPPORT_EMAIL}"""
            
            if update.callback_query:
                await update.callback_query.edit_message_text(
                    non_onboarded_plain, reply_markup=keyboard
                )
            elif update.message:
                await update.message.reply_text(non_onboarded_plain, reply_markup=keyboard)
    else:
        # For onboarded users: full help with main menu keyboard
        # At this point, user is guaranteed to exist (is_onboarded check above)
        keyboard = main_menu_keyboard(
            user_telegram_id=str(user.telegram_id) if user else ""
        )

        # ROBUST ERROR HANDLING: Graceful fallback if Markdown fails
        try:
            from utils.message_utils import send_unified_message
            await send_unified_message(
                update, help_text, reply_markup=keyboard, parse_mode="Markdown"
            )
        except Exception as e:
            safe_error = safe_error_log(e)
            logger.error(f"Help command Markdown error: {safe_error}")

            # Compact fallback without formatting
            help_text_plain = f"""🔒 {Config.PLATFORM_NAME} Help

🚀 Quick Exchange • Secure Trade
5% fee • Refundable on early cancellation

Commands: /start /wallet /help • Support: {Config.SUPPORT_EMAIL}"""

            if update.callback_query:
                await update.callback_query.edit_message_text(
                    help_text_plain, reply_markup=keyboard
                )
            elif update.message:
                await update.message.reply_text(help_text_plain, reply_markup=keyboard)
    
    return 0

@require_onboarding
async def cashout_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /cashout command - Direct access to cashout flow"""
    logger.info(
        f"cashout_command called by user {update.effective_user.id if update.effective_user else 'unknown'}"
    )

    user = await get_user_from_update(update)

    if not user:
        logger.warning("cashout_command: User not found")
        # Branded registration message for cashout command
        registration_msg = BrandingUtils.get_branded_error_message(
            "validation", "Registration required for cashout access"
        )
        if update.message:
            await update.message.reply_text(registration_msg, parse_mode="Markdown")
        return 0

    logger.info(
        f"cashout_command: Starting cashout flow for user {user.telegram_id}"
    )
    # Import and start the cashout flow directly
    from handlers.wallet_direct import start_cashout

    return await start_cashout(update, context)

@require_onboarding
async def wallet_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /wallet command - Directly open wallet interface"""
    user = await get_user_from_update(update)

    if not user:
        # Branded registration message for wallet command
        registration_msg = BrandingUtils.get_branded_error_message(
            "validation", "Registration required for wallet access"
        )
        if update.callback_query:
            await update.callback_query.edit_message_text(registration_msg, parse_mode="Markdown")
        elif update.message:
            await update.message.reply_text(registration_msg, parse_mode="Markdown")
        return 0

    # Directly show wallet interface
    from handlers.wallet_direct import show_wallet_menu
    await show_wallet_menu(update, context)
    return 0

@require_onboarding
async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /profile command"""
    logger.info(f"🔍 PROFILE HANDLER: Called by user {update.effective_user.id if update.effective_user else 'unknown'}")
    
    user = await get_user_from_update(update)

    if not user:
        message_text = "❌ You need to register first. Please use /start to begin."
        if update.callback_query:
            await update.callback_query.edit_message_text(message_text)
        elif update.message:
            await update.message.reply_text(message_text)
        return 0

    # Get user balance and stats from database
    session = SessionLocal()
    try:
        # Query actual data from database
        total_trades = session.query(func.count(Escrow.id)).filter(
            ((Escrow.buyer_id == user.id) | (Escrow.seller_id == user.id)),
            Escrow.status == "completed"
        ).scalar() or 0
        
        total_ratings = session.query(func.count(Rating.id)).filter(
            Rating.rated_id == user.id
        ).scalar() or 0
        
        total_volume = session.query(func.sum(Escrow.amount)).filter(
            ((Escrow.buyer_id == user.id) | (Escrow.seller_id == user.id)),
            Escrow.status == "completed"
        ).scalar() or 0
        
        # Calculate reputation score from ratings
        avg_rating = session.query(func.avg(Rating.rating)).filter(
            Rating.rated_id == user.id
        ).scalar()
        reputation_score = float(avg_rating) if avg_rating else 0.0
        
        successful_trades = total_trades
        
        # Get wallet balance
        wallet = session.query(Wallet).filter(Wallet.user_id == user.id, Wallet.currency == "USD").first()
        (
            float(getattr(wallet, "balance", 0))
            if wallet and getattr(wallet, "balance", None) is not None
            else 0.0
        )
        
        # Get trader badge and level (must be inside try block before session closes)
        level_info = TrustedTraderSystem.get_trader_level(user, session)
        badge = level_info['badge']
        trader_status = level_info['name']
    finally:
        session.close()

    # Safely format user information with escaping
    def escape_markdown_simple(text):
        """Simple markdown escaping"""
        if not text:
            return ""
        chars_to_escape = ['*', '_', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '!']
        for char in chars_to_escape:
            text = text.replace(char, f'\\{char}')
        return text
    
    safe_name = escape_markdown_simple(user.username or user.first_name or "User")
    safe_email = escape_markdown_simple(
        getattr(user, "email", "Not provided") or "Not provided"
    )
    safe_created = escape_markdown_simple(user.created_at.strftime("%B %Y"))
    safe_trader_status = escape_markdown_simple(trader_status)

    # Get public profile URL
    from utils.helpers import get_public_profile_url
    profile_url = get_public_profile_url(user)

    # Create compact mobile-friendly profile display
    header = make_header("My Profile")
    
    profile_text = f"""{header}
👤 **{safe_name}** • {safe_trader_status} {badge}
📧 {safe_email} • 📅 {safe_created}

⭐ {reputation_score:.1f}/5 ({total_ratings}) • 🤝 {total_trades} trades • 💰 ${total_volume:.2f}
✅ Success: {(successful_trades / max(total_trades, 1) * 100):.1f}%

{'✅' if getattr(user, 'email_verified', False) else '❌'} Email • {'🔐' if getattr(user, 'two_factor_enabled', False) else '🔓'} 2FA

🔗 **Share Your Profile:**
{profile_url}

{make_trust_footer()}"""

    # Create comprehensive profile management keyboard with account management
    # Conditionally build account management row based on ENABLE_NGN_FEATURES
    account_mgmt_row = []
    if Config.ENABLE_NGN_FEATURES:
        account_mgmt_row.append(InlineKeyboardButton("🏦 Bank Accounts", callback_data="manage_bank_accounts"))
    account_mgmt_row.append(InlineKeyboardButton("🔐 Wallet Addresses", callback_data="manage_crypto_addresses"))
    
    profile_keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💰 My Wallet", callback_data="menu_wallet"),
            InlineKeyboardButton("⚙️ Account Settings", callback_data="user_settings")
        ],
        account_mgmt_row,
        [InlineKeyboardButton("📞 Help & Support", callback_data="menu_help")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
    ])
    
    # FIXED: Use unified message handling to prevent UI duplication
    from utils.message_utils import send_unified_message
    await send_unified_message(
        update, profile_text, reply_markup=profile_keyboard, parse_mode="Markdown"
    )
    return 0

async def exchanges_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /exchanges command - redirect to exchange menu"""
    # Exchange functionality integrated into wallet handlers
    from handlers.start import show_main_menu
    await show_main_menu(update, context)
    return 0

@require_onboarding
async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /menu command - Show main dashboard"""
    from handlers.start import show_main_menu
    return await show_main_menu(update, context)

@require_onboarding
async def escrow_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /escrow command - Create new trade (directly start flow)"""
    # Directly start the secure trade creation flow
    from handlers.escrow_direct import direct_start_secure_trade
    return await direct_start_secure_trade(update, context)

@require_onboarding
async def orders_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /orders command - View transaction history directly"""
    # Directly show transaction history
    from handlers.transaction_history import show_transaction_history
    return await show_transaction_history(update, context)

@require_onboarding
async def support_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /support command - Start support chat directly"""
    # Directly start support chat (working version from 1 hour ago)
    from handlers.support_chat import start_support_chat
    return await start_support_chat(update, context)

@require_onboarding
async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /settings command - Account settings"""
    return await show_account_settings(update, context)

# Missing handler implementations for button coverage

async def show_account_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for user_settings callback - Account settings (NOT auto-cashout)"""
    query = update.callback_query
    if query:
        from utils.callback_utils import safe_answer_callback_query
        await safe_answer_callback_query(query, "⚙️ Account settings")
    
    if not update.effective_user:
        return
    
    session = SessionLocal()
    try:
        from utils.repository import UserRepository
        user = UserRepository.get_user_by_telegram_id(session, update.effective_user.id)
        
        if not user:
            await query.edit_message_text("❌ User not found")
            return
            
        # Get user stats for display
        total_trades = getattr(user, 'total_trades', 0)
        
        # Clean, concise account settings interface
        email_status = '✅' if getattr(user, 'email_verified', False) else '❌'
        tfa_status = '✅' if getattr(user, 'two_factor_enabled', False) else '❌'
        
        settings_text = f"""⚙️ Settings

📧 {email_status} • 🔐 {tfa_status} • 🤝 {total_trades} • ⭐ {user.level if hasattr(user, 'level') else 'Standard'}"""

        # Conditionally build account management row based on ENABLE_NGN_FEATURES
        account_mgmt_row = []
        if Config.ENABLE_NGN_FEATURES:
            account_mgmt_row.append(InlineKeyboardButton("🏦 Manage Bank Accounts", callback_data="manage_bank_accounts"))
        account_mgmt_row.append(InlineKeyboardButton("🔐 Manage Addresses", callback_data="manage_crypto_addresses"))
        
        keyboard = [
            [
                InlineKeyboardButton("📧 Email Settings", callback_data="email_settings"),
                InlineKeyboardButton("🔐 Security", callback_data="security_settings")
            ],
            account_mgmt_row,
        ]
        
        # Conditionally show Auto CashOut Settings based on ENABLE_AUTO_CASHOUT_FEATURES
        if Config.ENABLE_AUTO_CASHOUT_FEATURES:
            keyboard.append([
                InlineKeyboardButton("💰 Auto CashOut Settings", callback_data="cashout_settings")
            ])
        
        keyboard.extend([
            [
                InlineKeyboardButton("👤 Back to Profile", callback_data="menu_profile"),
                InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")
            ]
        ])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if query:
            await query.edit_message_text(settings_text, reply_markup=reply_markup, parse_mode='Markdown')
            
    except Exception as e:
        logger.error(f"Error in account settings: {e}")
        error_msg = "❌ Error loading settings"
        if query:
            await query.edit_message_text(error_msg)
    finally:
        session.close()

async def show_cashout_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for cashout_settings callback - show auto-cashout settings"""
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "⚙️")
    
    # Feature guard: silently return if auto cashout features disabled
    if not Config.ENABLE_AUTO_CASHOUT_FEATURES:
        return
    
    if not update.effective_user:
        return
    
    session = SessionLocal()
    try:
        from utils.repository import UserRepository
        from models import SavedAddress, SavedBankAccount
        
        user = UserRepository.get_user_by_telegram_id(session, update.effective_user.id)
        if not user:
            if query:
                await query.edit_message_text("❌ User not found")
            return
        
        # Get current auto-cashout status
        auto_enabled = getattr(user, 'auto_cashout_enabled', False)
        preference = getattr(user, 'cashout_preference', None)
        crypto_id = getattr(user, 'auto_cashout_crypto_address_id', None)
        bank_id = getattr(user, 'auto_cashout_bank_account_id', None)
        
        # Get destination details
        destination_text = "Not set"
        if auto_enabled:
            if preference == "CRYPTO" and crypto_id:
                crypto_addr = session.query(SavedAddress).filter_by(id=crypto_id).first()
                if crypto_addr:
                    destination_text = f"{crypto_addr.currency} - {crypto_addr.label}"
            elif preference == "NGN_BANK" and bank_id:
                bank_acc = session.query(SavedBankAccount).filter_by(id=bank_id).first()
                if bank_acc:
                    destination_text = f"{bank_acc.bank_name} - {bank_acc.label}"
        
        # Build settings message
        status_icon = "🟢" if auto_enabled else "🔴"
        settings_text = f"""⚙️ **Auto CashOut Settings**

{status_icon} Status: **{'Enabled' if auto_enabled else 'Disabled'}**
💰 Preference: **{preference or 'Not set'}**
📍 Destination: **{destination_text}**

When enabled, your escrow earnings will automatically cash out to your chosen destination.
"""
        
        # Build keyboard
        keyboard = []
        
        # Toggle button
        toggle_text = "❌ Disable Auto CashOut" if auto_enabled else "✅ Enable Auto CashOut"
        keyboard.append([InlineKeyboardButton(toggle_text, callback_data="toggle_auto_cashout")])
        
        # Preference selection (only if enabled)
        if auto_enabled:
            keyboard.append([
                InlineKeyboardButton("💎 Set Crypto", callback_data="auto_cashout_set_crypto"),
                InlineKeyboardButton("🏦 Set Bank", callback_data="auto_cashout_set_bank")
            ])
        
        # Back button
        keyboard.append([InlineKeyboardButton("⬅️ Back to Settings", callback_data="user_settings")])
        
        if query:
            await query.edit_message_text(
                settings_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
    
    except Exception as e:
        logger.error(f"Error in show_cashout_settings: {e}")
        if query:
            await query.edit_message_text("❌ Error loading auto-cashout settings")
    finally:
        session.close()

async def show_notification_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for notification_settings callback"""
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "🔔")
    
    if not update.effective_user:
        return
    
    session = SessionLocal()
    try:
        from utils.repository import UserRepository
        user = UserRepository.get_user_by_telegram_id(session, update.effective_user.id)
        
        if not user:
            if query:
                await query.edit_message_text("❌ User not found.")
            return
        
        # Show notification settings directly
        settings_text = f"""🔔 **Notification Settings**

Configure how you receive updates:

✅ Telegram notifications (always enabled)
📧 Email notifications: {'✅ Enabled' if getattr(user, 'email_notifications', True) else '❌ Disabled'}
📱 SMS notifications: {'✅ Enabled' if getattr(user, 'sms_notifications', False) else '❌ Disabled'}

Manage your preferences below:"""
        
        keyboard = [
            [InlineKeyboardButton("📧 Email Settings", callback_data="email_settings")],
            [InlineKeyboardButton("📱 SMS Settings", callback_data="sms_settings")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
        ]
        
        if query:
            await query.edit_message_text(settings_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"Error in show_notification_settings: {e}")
        if query:
            await safe_answer_callback_query(query, "❌ An error occurred")
    finally:
        session.close()
