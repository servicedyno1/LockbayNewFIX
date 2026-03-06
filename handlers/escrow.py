"""
New Payment-First Escrow Flow Handlers
Implements the improved UX flow where buyers pay immediately before seller notification
"""

import logging
import asyncio
import html
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, Final, cast
from decimal import Decimal, ROUND_HALF_UP
import qrcode

try:
    import qrcode
    from qrcode.main import QRCode
except ImportError:
    QRCode = None

def _is_phone_number(text: str) -> bool:
    """Helper function to detect phone numbers"""
    # Simple phone number detection - starts with + and contains digits
    if not text.startswith("+"):
        return False
    # Remove + and check if remaining characters are mostly digits
    digits_only = (
        text[1:].replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    )
    return len(digits_only) >= 10 and digits_only.isdigit()

from telegram import Update as TelegramUpdate, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)
from models import User, Escrow, EscrowStatus, Wallet, TransactionType, EscrowHolding, Dispute, Rating
from utils.keyboards import *
from utils.helpers import *
from utils.wallet_manager import get_or_create_wallet, get_user_wallet
from utils.markdown_escaping import escape_markdown, format_username_html
from utils.constants import States, CallbackData, EscrowStates
from utils.callback_utils import safe_edit_message_text, safe_answer_callback_query
from utils.error_handler import handle_error
from utils.production_validator import ProductionValidator
from utils.universal_id_generator import UniversalIDGenerator
from config import Config
from database import async_managed_session, get_async_session
from sqlalchemy import or_, and_, select, update as sqlalchemy_update
from services.crypto import CryptoServiceAtomic
from services.fincra_service import FincraService
from services.admin_trade_notifications import admin_trade_notifications

# Import per-update caching system
from utils.update_cache import get_cached_user, invalidate_user_cache

# ASYNC BUTTON HANDLER FOR <500MS PERFORMANCE
from utils.button_handler_async import button_callback_wrapper

# UNIFIED TRANSACTION SYSTEM INTEGRATION
from services.unified_transaction_service import (
    UnifiedTransactionService, TransactionRequest, UnifiedTransactionType, 
    UnifiedTransactionPriority
)
from services.conditional_otp_service import ConditionalOTPService
from services.dual_write_adapter import DualWriteConfig, DualWriteMode, DualWriteStrategy

# BRANDING INTEGRATION
from utils.branding_utils import BrandingUtils

# WALLET VALIDATION
from utils.wallet_validation import WalletValidator

# ID GENERATORS
from utils.universal_id_generator import UniversalIDGenerator

# STATE TRANSITION VALIDATION
from utils.escrow_state_validator import EscrowStateValidator, StateTransitionError

# PRECISION MONEY UTILITIES
from utils.precision_money import format_money, decimal_to_string, safe_multiply, safe_divide, safe_add, safe_subtract, calculate_percentage
from utils.decimal_precision import MonetaryDecimal

# ESCROW PREFETCH CACHE MANAGEMENT
from utils.escrow_prefetch import invalidate_prefetch_cache

# TRANSACTION HISTORY CACHE MANAGEMENT
from utils.transaction_history_prefetch import invalidate_transaction_history_cache

# ADMIN TRADE NOTIFICATIONS
from services.admin_trade_notifications import AdminTradeNotificationService

# Constants for conversation flow
CONV_END: Final[int] = cast(int, ConversationHandler.END)

# Decimal import moved to top

logger = logging.getLogger(__name__)

def invalidate_all_escrow_caches(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Invalidate all escrow-related caches when trade completes/cancels
    
    Called when:
    - Escrow is completed (funds released)
    - Escrow is cancelled
    - Wallet balance changes significantly
    - Before final confirmation (to refresh balance)
    """
    if context and context.user_data:
        # Invalidate escrow prefetch cache
        invalidate_prefetch_cache(context.user_data)
        
        # Invalidate transaction history cache (escrow affects transaction history)
        invalidate_transaction_history_cache(context.user_data)
        logger.info("🗑️ TX_CACHE: Invalidated transaction history cache after escrow state change")
        
        # Invalidate seller lookup cache if present
        if 'seller_data' in context.user_data:
            del context.user_data['seller_data']
            logger.info("🗑️ CACHE: Invalidated seller data cache")

def clean_seller_identifier(value: Optional[str]) -> str:
    """Remove markdown escape backslashes from seller identifiers"""
    if not value:
        return ""
    return str(value).replace('\\', '')

def format_trade_review_message(
    escrow_id_display: str,
    total_to_pay,
    seller_display: str,
    description: str,
    fee_display: str,
    payment_display: str = "",
    seller_reviews_text: str = "",
    use_html_link: bool = False,
    seller_username: Optional[str] = None,
    delivery_hours: Optional[int] = None
) -> str:
    """
    Centralized trade review message formatting
    Used by both show_trade_review() and send_updated_trade_review()
    """
    import html
    
    # If use_html_link is True and we have a username, format as clickable HTML link
    if use_html_link and seller_username:
        # Clickable link version
        seller_display_formatted = format_username_html(f"@{seller_username}", include_link=True)
    else:
        # Plain text version (no link, no backslash)
        seller_display_formatted = format_username_html(seller_display, include_link=False)
    
    # Escape all other text content for HTML safety
    description_escaped = html.escape(description)
    fee_display_escaped = html.escape(fee_display)
    payment_display_escaped = html.escape(payment_display) if payment_display else ""
    seller_reviews_escaped = html.escape(seller_reviews_text) if seller_reviews_text else ""
    
    # Format delivery time display
    delivery_display = ""
    if delivery_hours:
        if delivery_hours == 1:
            delivery_display = f"\n⏰ 1 hour"
        elif delivery_hours < 24:
            delivery_display = f"\n⏰ {delivery_hours} hours"
        elif delivery_hours == 24:
            delivery_display = f"\n⏰ 24 hours"
        elif delivery_hours % 24 == 0:
            days = delivery_hours // 24
            delivery_display = f"\n⏰ {days} day{'s' if days != 1 else ''}"
        else:
            days = delivery_hours // 24
            hours = delivery_hours % 24
            delivery_display = f"\n⏰ {days}d {hours}h"
    
    # Convert total_to_pay to Decimal if needed
    if not isinstance(total_to_pay, Decimal):
        total_to_pay = Decimal(str(total_to_pay))
    
    return f"""<b>#{html.escape(escrow_id_display)} • {format_money(total_to_pay, 'USD')}</b>

{seller_display_formatted}{seller_reviews_escaped}
📦 {description_escaped}{delivery_display}
💸 {fee_display_escaped}{payment_display_escaped}

🛡️ You control release • Refund if not satisfied

Ready to proceed?"""

# URL normalization helper
def normalize_webhook_base_url(base_url: str) -> str:
    """Ensure base URL ends with /webhook for proper callback routing"""
    url = base_url.rstrip('/')
    if not url.endswith('/webhook'):
        url = url.rstrip('/') + '/webhook'
    return url

# Global cache for trade data auto-refresh
_trade_cache = {"last_refresh": None, "stats": {}}

async def auto_refresh_trade_interfaces() -> None:
    """
    Auto-refresh trade data cache every 3 minutes
    Updates global trade statistics for faster page loads
    """
    try:
        logger.info("Starting trade data auto-refresh job...")

        from sqlalchemy import func
        async with async_managed_session() as session:
            # Get current stats
            from datetime import timezone
            current_time = datetime.now(timezone.utc)

            # Query essential trade stats efficiently
            total_trades_stmt = select(func.count(Escrow.id))
            total_trades_result = await session.execute(total_trades_stmt)
            total_trades = total_trades_result.scalar()
            
            active_trades_stmt = select(func.count(Escrow.id)).where(
                Escrow.status.in_(
                    [
                        "active",
                        "pending_acceptance",
                        "pending_deposit",
                        "payment_confirmed",
                    ]
                )
            )
            active_trades_result = await session.execute(active_trades_stmt)
            active_trades = active_trades_result.scalar()

            # Get start of today for accurate completed count
            today_start = current_time.replace(hour=0, minute=0, second=0, microsecond=0)
            today_end = today_start + timedelta(days=1)
            
            # FIXED: Use completed_at field for accurate completion tracking
            completed_today_stmt = select(func.count(Escrow.id)).where(
                Escrow.status == "completed",
                # Primary check: Use completed_at if available
                or_(
                    and_(
                        Escrow.completed_at.isnot(None),
                        Escrow.completed_at >= today_start,
                        Escrow.completed_at < today_end
                    ),
                    # Fallback: Use updated_at for trades without completed_at
                    and_(
                        Escrow.completed_at.is_(None),
                        Escrow.updated_at >= today_start,
                        Escrow.updated_at < today_end
                    )
                )
            )
            completed_today_result = await session.execute(completed_today_stmt)
            completed_today = completed_today_result.scalar()

            # Query for disputed trades
            disputed_trades_stmt = select(func.count(Escrow.id)).where(Escrow.status == "disputed")
            disputed_trades_result = await session.execute(disputed_trades_stmt)
            disputed_trades = disputed_trades_result.scalar()

            # Update cache
            _trade_cache["last_refresh"] = current_time
            _trade_cache["stats"] = {
                "total_trades": total_trades,
                "active_trades": active_trades,
                "completed_today": completed_today,
                "disputed_trades": disputed_trades,
                "last_updated": current_time.isoformat(),
            }

            logger.info(
                f"Trade stats refreshed: {total_trades} total, {active_trades} active, {completed_today} completed today, {disputed_trades} disputed"
            )

    except Exception as e:
        logger.error(f"Error in trade auto-refresh: {e}")
        import traceback

        logger.error(f"Full traceback: {traceback.format_exc()}")

def get_trade_cache_stats() -> Dict[str, Any]:
    """Get cached trade statistics"""
    return _trade_cache.get("stats", {})

def get_trade_last_refresh_time() -> Optional[datetime]:
    """Get last trade refresh timestamp"""
    return _trade_cache.get("last_refresh")

# Type safety helper functions
def safe_get_user_id(query: Optional[Any]) -> Optional[int]:
    """Safely get user ID from callback query - returns integer for database compatibility"""
    if (
        query
        and hasattr(query, "from_user")
        and query.from_user
        and hasattr(query.from_user, "id")
    ):
        return query.from_user.id
    return None

def safe_get_context_data(
    context: ContextTypes.DEFAULT_TYPE, key: str
) -> Dict[str, Any]:
    """Safely get data from context.user_data"""
    if context and context.user_data and key in context.user_data:
        return context.user_data[key]
    return {}

def get_default_fee(amount: Decimal) -> Decimal:
    """Calculate default platform fee based on Config.ESCROW_FEE_PERCENTAGE
    
    Use this instead of hardcoded 0.05 (5%) fallback values.
    """
    fee_percentage = Decimal(str(Config.ESCROW_FEE_PERCENTAGE)) / Decimal("100")
    return (amount * fee_percentage).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

def as_decimal(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    """Convert value to Decimal safely"""
    if value is None:
        return default
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(Decimal(str(value))))
    except (ValueError, TypeError):
        return default

def _check_wallet_sufficiency(
    wallet_balance: Decimal,
    amount_needed: Decimal
) -> tuple[bool, str]:
    """Check if wallet balance is sufficient and return status text
    
    Returns:
        tuple[bool, str]: (is_sufficient, button_text)
    """
    if wallet_balance >= amount_needed:
        return True, f"💰 Wallet {format_money(wallet_balance, 'USD')}"
    else:
        return False, f"💰 Wallet {format_money(wallet_balance, 'USD')} ❌ Insufficient"

def _create_payment_keyboard(
    wallet_balance_text: str,
    include_back: bool = False,
    back_callback: str = "back_to_fee_options",
    total_amount: Optional[Decimal] = None,
    user_id: Optional[int] = None,
    wallet_balance: Optional[Decimal] = None
) -> InlineKeyboardMarkup:
    """Centralized payment keyboard creation with dynamic wallet handling
    
    Shows wallet as disabled with 'Add Funds' button when balance is insufficient
    """
    # FIXED: Use keyboard caching to prevent redundant recreation
    from utils.keyboard_cache import KeyboardCache
    
    # Check cache first - but only if no wallet balance check is needed
    # Don't cache when checking wallet balance to ensure real-time accuracy
    if total_amount is None or user_id is None:
        cached = KeyboardCache.get_cached_keyboard(
            "payment", 
            wallet_text=wallet_balance_text, 
            include_back=include_back, 
            back_callback=back_callback
        )
        if cached:
            return cached
    
    keyboard = []
    
    # Check wallet sufficiency and create dynamic button
    if wallet_balance is not None and total_amount is not None:
        is_sufficient, wallet_text = _check_wallet_sufficiency(wallet_balance, total_amount)
        
        if is_sufficient:
            # Wallet has enough funds - show clickable button
            keyboard.append([InlineKeyboardButton(wallet_text, callback_data="payment_wallet")])
            logger.info(f"Wallet button shown (sufficient): {wallet_text}")
        else:
            # Wallet insufficient - show disabled button + Add Funds button
            keyboard.append([InlineKeyboardButton(wallet_text, callback_data="wallet_insufficient")])
            keyboard.append([InlineKeyboardButton("➕ Add Funds to Wallet", callback_data="escrow_add_funds")])
            logger.info(f"Wallet insufficient - Add Funds button shown: {wallet_text}")
    else:
        # Fallback to original behavior when balance check not available
        keyboard.append([InlineKeyboardButton(wallet_balance_text, callback_data="payment_wallet")])
        logger.info(f"Wallet button shown (no check): {wallet_balance_text}")
    
    # DynoPay supported cryptocurrencies only
    keyboard.extend([
        [
            InlineKeyboardButton("₿ BTC", callback_data="crypto_BTC"),
            InlineKeyboardButton("Ξ ETH", callback_data="crypto_ETH"),
            InlineKeyboardButton("Ł LTC", callback_data="crypto_LTC"),
        ],
        [
            InlineKeyboardButton("₮ USDT-ERC20", callback_data="crypto_USDT"),
            InlineKeyboardButton("₮ USDT-TRC20", callback_data="crypto_USDT-TRC20"),
        ],
    ])
    
    if Config.ENABLE_NGN_FEATURES:
        keyboard.append([InlineKeyboardButton("🇳🇬 ₦ Bank Transfer", callback_data="payment_ngn")])

    if include_back:
        keyboard.append(
            [InlineKeyboardButton("⬅️ Back to Fee Options", callback_data=back_callback)]
        )

    keyboard.append(
        [InlineKeyboardButton("❌ Cancel Trade", callback_data="cancel_escrow")]
    )
    
    # FIXED: Cache the created keyboard for future use - but only if no wallet check
    markup = InlineKeyboardMarkup(keyboard)
    if not (total_amount and user_id):
        KeyboardCache.cache_keyboard(
            "payment", 
            markup,
            wallet_text=wallet_balance_text, 
            include_back=include_back, 
            back_callback=back_callback
        )
    return markup


async def start_secure_trade(update: TelegramUpdate, context: ContextTypes.DEFAULT_TYPE) -> int:
    """🛡️ Start new secure trade creation"""
    import time
    start_time = time.perf_counter()
    
    query = update.callback_query
    # SINGLE CALLBACK ANSWER: Trade creation start
    if query:
        await safe_answer_callback_query(query, "🛡️ Creating secure trade")

    # CACHE OPTIMIZATION: Use cached user to eliminate redundant queries
    user = await get_cached_user(update, context)
    if not user:
        if query:
            await safe_edit_message_text(query, "❌ Account issue. Tap /start to refresh")  # type: ignore
        return CONV_END
    
    # Send admin notification for trade creation initiated (non-blocking)
    asyncio.create_task(
        admin_trade_notifications.notify_trade_creation_initiated({
            'user_id': user.id,
            'username': user.username,
            'first_name': user.first_name,
            'last_name': user.last_name,
            'initiated_at': datetime.utcnow()
        })
    )

    # BATCH OPTIMIZATION PHASE 1: Prefetch user + wallet + fee config in ONE query
    # Target: <100ms vs ~300ms for 3 sequential queries
    async with async_managed_session() as session:
        from utils.escrow_prefetch import prefetch_escrow_context, cache_prefetch_data
        
        prefetch_start = time.perf_counter()
        prefetch_data = await prefetch_escrow_context(user.id, session)
        prefetch_elapsed = (time.perf_counter() - prefetch_start) * 1000
        
        if not prefetch_data:
            logger.error(f"❌ PREFETCH_FAILED: Could not prefetch escrow context for user {user.id}")
            if query:
                await safe_edit_message_text(query, "❌ Account issue. Please try again")  # type: ignore
            return CONV_END
        
        logger.info(
            f"⏱️ BATCH_OPTIMIZATION: Prefetch completed in {prefetch_elapsed:.1f}ms "
            f"(target: <100ms) - Saved ~200ms vs sequential queries ✅"
        )
        
        # Cache prefetch data for reuse in all subsequent steps (eliminates ALL queries in steps 2-6)
        if not context.user_data:
            context.user_data = {}
        cache_prefetch_data(context.user_data, prefetch_data)
        
        # Use cached wallet balance from prefetch
        wallet_balance = prefetch_data.total_usable_balance

        # STEP 1 of 4: Seller Contact - Username Only
        wallet_amount = BrandingUtils.format_branded_amount(wallet_balance, "USD")
        
        text = f"""📝 Create Trade (1/4)
━▫️▫️▫️ 25%


{wallet_amount}


👤 Seller's Telegram Username

📌 e.g., @johndoe


✍️ Type here 👇"""

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "❌ Cancel Trade", callback_data="cancel_escrow"
                    )
                ],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")],
            ]
        )
        
        # Handle both callback queries and direct commands
        from utils.message_utils import send_unified_message
        if query:
            await safe_edit_message_text(query, text, reply_markup=keyboard)  # type: ignore
        else:
            await send_unified_message(update, text, reply_markup=keyboard)

    # Initialize escrow data without ID generation (ID created on confirmation only)
    if not context.user_data:
        logger.warning("user_data is None during escrow creation - initializing safely")
        # CRITICAL FIX: Initialize user_data if None to prevent setdefault failures
        if context.user_data is None:
            context.user_data = {}
        logger.info("✅ user_data initialized successfully")
    
    # FIX 1: Clear any stale escrow_data from previous escrows to prevent ID reuse
    context.user_data["escrow_data"] = {}
    logger.info("✅ Cleared stale escrow_data to prevent duplicate IDs")
    
    # Use setdefault for type-safe escrow_data handling
    # NO ID GENERATION HERE - IDs only created when user confirms trade
    escrow_data = context.user_data.setdefault("escrow_data", {})
    
    # Initialize basic escrow state (no ID yet)
    escrow_data.update({
        "status": "creating",  # Track status for early-stage operations
        "created_at": datetime.now().timestamp()  # Add timestamp for debugging
    })
    
    logger.info(f"✅ Escrow flow started (no ID generated yet) for user {update.effective_user.id if update.effective_user else 'unknown'}")
    
    # CRITICAL FIX: Set database state for direct handlers to work
    if update.effective_user:
        async with async_managed_session() as session:
            try:
                user_stmt = select(User).where(User.telegram_id == update.effective_user.id)
                user_result = await session.execute(user_stmt)
                user_obj = user_result.scalar_one_or_none()
                if user_obj:
                    # TIMESTAMP FIX: Use helper to set conversation state with timestamp
                    from utils.conversation_state_helper import set_conversation_state_both
                    await set_conversation_state_both(update.effective_user.id, "seller_input", context, session)
                    await session.commit()
                    logger.info("✅ Set user conversation_state to 'seller_input' with timestamp for direct handlers")
                else:
                    logger.error(f"User {update.effective_user.id} not found for state setting")
            except Exception as e:
                logger.error(f"Failed to set conversation state: {e}")
    else:
        logger.error("No effective_user found for database state setting")
    logger.info(f"✅ RETURNING EscrowStates.SELLER_INPUT state for ConversationHandler")
    
    # TIMING LOG: Total start_secure_trade execution time
    total_elapsed = (time.perf_counter() - start_time) * 1000
    logger.info(f"⏱️ BATCH_OPTIMIZATION: start_secure_trade completed in {total_elapsed:.1f}ms (target: <300ms) ✅")
    
    return EscrowStates.SELLER_INPUT

async def handle_seller_input(update: TelegramUpdate, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle seller @username or email input with exchange conflict prevention"""
    import time
    start_time = time.perf_counter()
    
    logger.info(f"✅ ESCROW HANDLER: handle_seller_input ENTRY for user {update.effective_user.id if update.effective_user else 'unknown'}")
    
    # INSTANT FEEDBACK: Send typing indicator IMMEDIATELY before any processing
    if update.message and update.message.chat:
        try:
            await update.message.chat.send_action("typing")
            logger.info("⚡ INSTANT FEEDBACK: Typing indicator sent BEFORE database operations")
        except Exception as e:
            logger.warning(f"Could not send typing indicator: {e}")
    
    # RACE CONDITION FIX: Rehydrate context from database if missing
    from utils.escrow_context_helper import ensure_escrow_context_with_fallback
    
    user_id = update.effective_user.id if update.effective_user else 0
    
    context_start = time.perf_counter()
    if not await ensure_escrow_context_with_fallback(user_id, context, update):
        logger.error(f"❌ ESCROW_CONTEXT: Failed to ensure context for user {user_id}")
        return ConversationHandler.END
    context_elapsed = (time.perf_counter() - context_start) * 1000
    logger.info(f"⏱️ TIMING: Context rehydration took {context_elapsed:.1f}ms")
    
    # At this point, context.user_data["escrow_data"] is guaranteed to exist
    if context.user_data is None or "escrow_data" not in context.user_data:
        # This should never happen after ensure_escrow_context, but keep as safety net
        logger.error(f"🚨 ESCROW STATE: Context still missing after rehydration for user {user_id}")
        if update.message:
            await update.message.reply_text("❌ Session error. Please start a new trade.")
        return ConversationHandler.END

    # ENHANCED IMMEDIATE FEEDBACK: Multi-stage progress indicators
    logger.info(f"📝 ESCROW HANDLER: Processing seller input for user {update.effective_user.id if update.effective_user else 'unknown'}")
    processing_msg = None
    if update.message:
        processing_msg = await update.message.reply_text(
            "⏳ Processing seller details...\n\n"
            "🔍 Validating seller information\n"
            "📊 Loading profile data\n"
            "✨ Preparing trade setup\n\n"
            "_This should take less than a second_",
            parse_mode="Markdown"
        )
    
    seller_input = (
        update.message.text.strip() if update.message and update.message.text else ""
    )

    # Comprehensive validation using our validation functions
    from utils.input_validation import InputValidator, ValidationError

    try:
        # SAFETY FIX: Detect crypto addresses to prevent stale state validation errors
        # Crypto addresses are 26-42 chars (Bitcoin: 26-35, Ethereum: 42, others: 26-44)
        def looks_like_crypto_address(text: str) -> bool:
            """Detect if text looks like a crypto address to prevent validation errors"""
            if not text or len(text) < 26:
                return False
            # Ethereum/ERC20 addresses start with 0x
            if text.startswith('0x') and len(text) == 42 and all(c in '0123456789abcdefABCDEF' for c in text[2:]):
                return True
            # Bitcoin/Litecoin/Dogecoin addresses are alphanumeric, 26-44 chars
            if 26 <= len(text) <= 44 and text.replace('_', '').replace('-', '').isalnum():
                # Check if it has characteristics of crypto addresses (mix of letters and numbers)
                has_letters = any(c.isalpha() for c in text)
                has_numbers = any(c.isdigit() for c in text)
                if has_letters and has_numbers:
                    return True
            return False
        
        # STALE STATE FIX: If input looks like crypto address, reject it immediately
        if looks_like_crypto_address(seller_input):
            if processing_msg:
                try:
                    await processing_msg.delete()
                except Exception:
                    pass
            
            error_msg = (
                "❌ Invalid seller username\n\n"
                f"The text you entered looks like a crypto wallet address.\n\n"
                "Please enter the seller's Telegram username:\n"
                "• @johndoe (with @ symbol)\n"
                "• johndoe (without @ symbol)\n\n"
                "💡 Tip: If you want to cash out crypto, use /start → 💰 Wallet"
            )
            if update.message:
                await update.message.reply_text(error_msg, parse_mode="HTML")
            return ConversationHandler.END
        
        if seller_input.startswith("@") or (
            "@" not in seller_input and not _is_phone_number(seller_input)
        ):
            # Username validation
            seller_type = "username"
            validated_username = InputValidator.validate_username(seller_input)
            seller_identifier = validated_username[1:]  # Remove @ for storage
            
            # CLEANUP: Remove processing message on success
            try:
                if processing_msg:
                    await processing_msg.delete()
            except Exception:
                pass

        elif "@" in seller_input and "." in seller_input:
            # Email validation
            seller_type = "email"
            seller_identifier = InputValidator.validate_email(seller_input)

        elif _is_phone_number(seller_input):
            # Phone validation using centralized validator
            seller_type = "phone"
            formatted_phone = InputValidator.validate_phone(seller_input)
            
            # Process phone seller with SMS eligibility checks
            from services.seller_invitation import SellerInvitationService
            
            # Get buyer user for eligibility check
            if not update.effective_user:
                raise ValidationError("Unable to verify your account")
                

            async with async_managed_session() as session:
                try:
                    user_stmt = select(User).where(User.telegram_id == update.effective_user.id)
                    user_result = await session.execute(user_stmt)
                    buyer_user = user_result.scalar_one_or_none()
                    if not buyer_user:
                        raise ValidationError("Unable to verify your account")
                    
                    seller_info = await SellerInvitationService._process_phone_seller(formatted_phone, buyer_user)
                    seller_type = seller_info.get("type", "phone")
                    seller_identifier = seller_info.get("seller_identifier", formatted_phone)
                    
                    # Store seller info for error messaging
                    if context.user_data:
                        context.user_data["temp_seller_info"] = seller_info
                    
                except Exception as e:
                    logger.error(f"Error processing phone seller: {e}")
                    raise ValidationError("Unable to process phone number. Please try again.")
        else:
            raise ValidationError("Invalid format")

    except ValidationError as e:
        # CLEANUP: Remove processing message before error
        try:
            if processing_msg:
                await processing_msg.delete()
        except Exception:
            pass
        if update.message:
            # Safely escape error message to prevent injection
            safe_error = escape_markdown(str(e))
            await update.message.reply_text(
                f"❌ {safe_error}\n\nPlease enter a valid Telegram username:\n• @johndoe (with @ symbol)\n• johndoe (without @ symbol, 5-32 characters)",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "❌ Cancel Trade", callback_data="cancel_escrow"
                            )
                        ]
                    ]
                ),
            )
        return EscrowStates.SELLER_INPUT

    # Get buyer user info for validation and BATCH OPTIMIZATION: Use same session for seller lookup
    # This eliminates redundant session creation and connection overhead
    
    async with async_managed_session() as session:
        if not update.effective_user:
            if update.message:
                await update.message.reply_text("❌ Unable to identify user.")
            return CONV_END
        user_stmt = select(User).where(User.telegram_id == update.effective_user.id)
        user_result = await session.execute(user_stmt)
        buyer_user = user_result.scalar_one_or_none()
        if not buyer_user:
            if update.message:
                await update.message.reply_text(
                    "❌ Account issue. Tap /start to refresh",
                    reply_markup=InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    "❌ Cancel Trade", callback_data="cancel_escrow"
                                )
                            ]
                        ]
                    ),
                )
            return CONV_END
        
        # BATCH OPTIMIZATION PHASE 2: Async batched seller lookup with ratings
        # Target: <100ms vs ~300ms for sequential queries
        from services.fast_seller_lookup_service import FastSellerLookupService
        
        seller_lookup_start = time.perf_counter()
        seller_profile = await FastSellerLookupService.get_seller_profile_async(
            seller_identifier, seller_type, session
        )
        seller_lookup_elapsed = (time.perf_counter() - seller_lookup_start) * 1000
        logger.info(
            f"⏱️ BATCH_OPTIMIZATION: Seller lookup completed in {seller_lookup_elapsed:.1f}ms "
            f"(target: <100ms) - Saved ~200ms vs sequential queries ✅"
        )

    # Check if seller is not the same as buyer
    if seller_type == "username":
        if (
            update.effective_user.username
            and seller_identifier.lower() == update.effective_user.username.lower()
        ):
            if update.message:
                await update.message.reply_text(
                    "❌ Can't trade with yourself. Enter someone else's @username:",
                    reply_markup=InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    "❌ Cancel Trade", callback_data="cancel_escrow"
                                )
                            ]
                        ]
                    ),
                )
            return EscrowStates.SELLER_INPUT
    elif seller_type == "email":
        if (
            getattr(buyer_user, "email", None)
            and seller_identifier.lower() == getattr(buyer_user, "email", "").lower()
        ):
            if update.message:
                await update.message.reply_text(
                    "❌ That's your own email. Enter the seller's email address:",
                    reply_markup=InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    "❌ Cancel Trade", callback_data="cancel_escrow"
                                )
                            ]
                        ]
                    ),
                )
            return EscrowStates.SELLER_INPUT
    elif seller_type == "phone":
        # Phone numbers are now supported for SMS invitations
        if update.message:
            await update.message.reply_text(
                f"✅ Phone Number Confirmed\n\n📱 {seller_identifier}\n\n"
                f"The seller will receive an SMS invitation when you create the trade.",
            )
    elif seller_type == "phone_restricted":
        # SMS invitations not allowed for this user
        if update.message:
            # Get restriction details from context if available
            restriction_reason = "SMS restrictions apply"
            if context.user_data and context.user_data.get("temp_seller_info"):
                temp_seller_info = context.user_data["temp_seller_info"]
                restriction_reason = temp_seller_info.get('restriction_reason', restriction_reason)
            
            await update.message.reply_text(
                f"❌ SMS not available\n\n📱 {seller_identifier}\n\n{restriction_reason}\n\nTry @username or email instead",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "❌ Cancel Trade", callback_data="cancel_escrow"
                            )
                        ]
                    ]
                ),
            )
        return EscrowStates.SELLER_INPUT

    # Store seller info (ensure no markdown escaping is stored)
    # Clean any potential backslashes that might have been added by markdown escaping
    clean_seller_identifier = seller_identifier.replace('\\', '') if seller_identifier else seller_identifier
    context.user_data["escrow_data"].update(
        {"seller_type": seller_type, "seller_identifier": clean_seller_identifier}
    )
    
    # REMOVED FRAGILE DATABASE UPDATE: Previously tried to update escrow records that may not exist yet
    # Replaced with robust validation at escrow creation time in both crypto and NGN payment flows
    # This ensures seller information is properly validated when escrows are actually created
    logger.info(f"✅ Seller information collected: {seller_type}={seller_identifier}")
    logger.info(f"🎯 ROBUST VALIDATION: Seller assignment will be validated at escrow creation time")
    
    # Display seller profile if found (optimized for fast display)
    reputation_text = ""
    # Format seller name with @ prefix for usernames (no clickable link, no backslash)
    if seller_type == 'username':
        seller_display_name = format_username_html(f"@{clean_seller_identifier}", include_link=False)
    else:
        seller_display_name = html.escape(clean_seller_identifier or "")
    
    try:
        if seller_profile:
            # Store fast seller profile for later use
            context.user_data["escrow_data"]["seller_profile"] = {
                "user_id": seller_profile.user_id,
                "display_name": seller_profile.display_name,
                "exists_on_platform": seller_profile.exists_on_platform,
                "basic_rating": seller_profile.basic_rating,
                "total_ratings": seller_profile.total_ratings,
                "trust_level": seller_profile.trust_level,
                "warning_flags": seller_profile.warning_flags
            }
            
            # CRITICAL FIX: DO NOT overwrite seller_display_name with profile name
            # Keep using the buyer's entered contact (clean_seller_identifier) 
            # This ensures consistency with what buyer typed, matching other screens
            # seller_display_name already set to clean_seller_identifier above (line 738/740)
            
            # Build Badge-First Seller Card UI - MOBILE-OPTIMIZED
            if seller_profile.exists_on_platform:
                # Fetch Trader Level Info
                trader_level_name = "New User"
                fee_discount_pct = 0
                trade_count = 0
                
                if seller_profile.trader_badge:
                    # Get full trader level info for better display
                    from utils.trusted_trader import TrustedTraderSystem
                    
                    # Find matching level by badge
                    for threshold, level_info in TrustedTraderSystem.TRADER_LEVELS.items():
                        if level_info['badge'] == seller_profile.trader_badge:
                            trader_level_name = level_info['name']
                            # Calculate fee discount (from utils/fee_calculator.py logic)
                            if threshold >= 100:
                                fee_discount_pct = 50
                            elif threshold >= 50:
                                fee_discount_pct = 40
                            elif threshold >= 25:
                                fee_discount_pct = 30
                            elif threshold >= 10:
                                fee_discount_pct = 20
                            elif threshold >= 5:
                                fee_discount_pct = 10
                            break
                    
                    # Get trade count if user exists
                    if seller_profile.user_id:
                        from models import Escrow
                        from sqlalchemy import func
                        
                        async with get_async_session() as session:
                            result = await session.execute(
                                select(func.count(Escrow.id)).where(
                                    or_(Escrow.buyer_id == seller_profile.user_id, Escrow.seller_id == seller_profile.user_id),
                                    Escrow.status == "completed"
                                )
                            )
                            trade_count = result.scalar() or 0
                
                # Badge display (first position)
                badge_display = seller_profile.trader_badge if seller_profile.trader_badge else "⭐"
                
                # Trade stats line
                if trade_count > 0:
                    trade_stats_line = f"{trade_count} trade{'s' if trade_count != 1 else ''}"
                    if fee_discount_pct > 0:
                        trade_stats_line += f" • {fee_discount_pct}% fee discount ✨"
                else:
                    trade_stats_line = "New seller"
                
                # Rating display
                if seller_profile.total_ratings and seller_profile.total_ratings > 0:
                    rating_display = f"⭐ {seller_profile.basic_rating}/5 rating • {seller_profile.total_ratings} review{'s' if seller_profile.total_ratings != 1 else ''}"
                else:
                    rating_display = "📧 No reviews yet"
                
                # Review display with attribution
                review_display = ""
                if seller_profile.recent_review:
                    # Get reviewer name and date (User and Rating already imported at top)
                    async with get_async_session() as session:
                        # Get most recent rating for attribution
                        result = await session.execute(
                            select(Rating).where(
                                Rating.rated_id == seller_profile.user_id
                            ).order_by(Rating.created_at.desc()).limit(1)
                        )
                        latest_rating = result.scalar_one_or_none()
                        
                        if latest_rating:
                            result = await session.execute(
                                select(User).where(User.id == latest_rating.rater_id)
                            )
                            reviewer = result.scalar_one_or_none()
                            reviewer_name = f"@{reviewer.username}" if reviewer and reviewer.username else "Anonymous"
                            review_date = latest_rating.created_at.strftime("%b %d")
                            
                            review_text = seller_profile.recent_review[:50] + "..." if len(seller_profile.recent_review) > 50 else seller_profile.recent_review
                            review_display = f'💬 "{html.escape(review_text)}"\n   — {reviewer_name} • {review_date}'
                
                # Build seller card with new structure (5% bigger)
                seller_card = f"""{badge_display} {seller_display_name} • {trader_level_name}
{trade_stats_line}
━━━━━━━━━━━━━━━━━━━━━━━━━━━
{rating_display}"""
                
                if review_display:
                    seller_card += f"\n{review_display}"
                
                reputation_text = seller_card
            else:
                # User does NOT exist on platform - show username/identifier
                reputation_text = f"⭐ {seller_display_name} • New User\nNo trades yet\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n📧 Will receive invite"
        
        elif seller_type in ['username', 'email']:
            # Seller not found on platform (safely escaped) - show username/identifier
            reputation_text = f"⭐ {seller_display_name} • New User\nNo trades yet\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n📧 Will receive invite"
    
    except Exception as e:
        logger.error(f"Error displaying seller reputation for {seller_identifier}: {e}")
        # Fallback reputation display on error
        reputation_text = f"\n\n👤 <b>Seller Profile</b>\n"
        reputation_text += f"• {html.escape(seller_identifier)}\n"
        reputation_text += f"• Profile information temporarily unavailable\n"

    # STEP 2 of 4: Amount Input - Badge-First Seller Card UI (5% bigger)
    text = f"""🛡️ SELLER PROFILE

{reputation_text}

━━━━━━━━━━━━━━━━━━━━━━━━━━━

💰 Amount (USD): Min ${int(Config.MIN_ESCROW_AMOUNT_USD)}

Enter amount 👇"""

    if update.message:
        await update.message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "❌ Cancel", callback_data="cancel_escrow"
                        ),
                        InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu"),
                    ],
                ]
            ),
        )

    total_elapsed = (time.perf_counter() - start_time) * 1000
    logger.info(f"⏱️ TIMING: handle_seller_input completed in {total_elapsed:.1f}ms (target: <300ms)")
    return EscrowStates.AMOUNT_INPUT

async def handle_amount_callback(update: TelegramUpdate, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle escrow navigation callbacks"""
    query = update.callback_query
    # PERFORMANCE: Instant acknowledgment
    if query:
        # IMMEDIATE FEEDBACK: Escrow action
        await safe_answer_callback_query(query, "🛡️ Escrow action")

    if query and query.data == "cancel_escrow":
        # CRITICAL FIX: Actually cancel the escrow in database if it was created
        escrow_id_to_cancel = None
        if context.user_data and "escrow_data" in context.user_data:
            escrow_data = context.user_data["escrow_data"]
            # Check for escrow ID in various possible locations
            escrow_id_to_cancel = (
                escrow_data.get("unified_escrow_id") or 
                escrow_data.get("escrow_id") or 
                escrow_data.get("database_escrow_id")
            )
        
        # If an escrow was created, cancel it in the database
        if escrow_id_to_cancel:
            try:
                async with async_managed_session() as session:
                    # Find the escrow - support both string ID and numeric ID
                    if isinstance(escrow_id_to_cancel, str):
                        stmt = select(Escrow).where(Escrow.escrow_id == escrow_id_to_cancel)
                    else:
                        stmt = select(Escrow).where(Escrow.id == escrow_id_to_cancel)
                    
                    result = await session.execute(stmt)
                    escrow_to_cancel = result.scalar_one_or_none()
                    
                    if escrow_to_cancel and escrow_to_cancel.status == "payment_pending":
                        # Only cancel if it's in payment_pending status
                        escrow_to_cancel.status = "cancelled"
                        escrow_to_cancel.cancelled_at = datetime.now(timezone.utc)
                        await session.commit()
                        logger.info(f"✅ ESCROW_CANCELLED: {escrow_id_to_cancel} cancelled in database by user")
                    elif escrow_to_cancel:
                        logger.info(f"⚠️ ESCROW_NOT_CANCELLED: {escrow_id_to_cancel} in status {escrow_to_cancel.status}, not cancelling")
            except Exception as e:
                logger.error(f"❌ Error cancelling escrow {escrow_id_to_cancel}: {e}")
        
        await safe_edit_message_text(
            query,  # type: ignore
            "❌ Trade creation cancelled.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🏠 Main Menu", callback_data="back_to_main")]]
            ),
        )
        if context.user_data:
            context.user_data.pop("escrow_data", None)
        return CONV_END
    
    if query and query.data == "back_to_trade_review":
        # Return to trade review from amount editing
        await show_trade_review(query, context)
        return EscrowStates.TRADE_REVIEW

    # No amount buttons to handle anymore - direct text input only
    return EscrowStates.AMOUNT_INPUT

async def handle_amount_input(update: TelegramUpdate, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle custom amount text input"""
    import time
    start_time = time.perf_counter()
    
    # INSTANT FEEDBACK: Send typing indicator IMMEDIATELY before any processing
    if update.message and update.message.chat:
        try:
            await update.message.chat.send_action("typing")
            logger.info("⚡ INSTANT FEEDBACK: Typing indicator sent BEFORE database operations")
        except Exception as e:
            logger.warning(f"Could not send typing indicator: {e}")
    
    # RACE CONDITION FIX: Rehydrate context from database if missing
    from utils.escrow_context_helper import ensure_escrow_context_with_fallback
    
    user_id = update.effective_user.id if update.effective_user else 0
    
    context_start = time.perf_counter()
    if not await ensure_escrow_context_with_fallback(user_id, context, update):
        logger.error(f"❌ ESCROW_CONTEXT: Failed to ensure context for user {user_id} in amount_input")
        return CONV_END
    context_elapsed = (time.perf_counter() - context_start) * 1000
    logger.info(f"⏱️ TIMING: Context rehydration took {context_elapsed:.1f}ms")

    try:
        # IMMEDIATE FEEDBACK: Amount processing indicator
        processing_msg = None
        if update.message:
            processing_msg = await update.message.reply_text("⏳ Processing amount...")
        
        # Enhanced: Use Decimal for precise money calculations with validation
        amount_str = (
            update.message.text.strip().replace("$", "").replace(",", "")
            if update.message and update.message.text
            else "0"
        )
        
        # Validate that amount_str contains only valid numeric characters
        if not amount_str or not amount_str.replace(".", "", 1).replace("-", "", 1).isdigit():
            if processing_msg:
                try:
                    await processing_msg.delete()
                except Exception as e:
                    logger.debug(f"Could not delete processing message: {e}")
                    pass
            
            if update.message:
                await update.message.reply_text(
                    "❌ Invalid Amount\n\nPlease enter a valid number (e.g., 50, 100.50):",
                    reply_markup=InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    "❌ Cancel Trade", callback_data="cancel_escrow"
                                )
                            ]
                        ]
                    ),
                )
            return EscrowStates.AMOUNT_INPUT
        
        amount = Decimal(amount_str).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # DYNAMIC MINIMUM VALIDATION: Check against selected payment method
        payment_method = context.user_data.get("escrow_data", {}).get("payment_method") if context.user_data else None
        min_usd = Decimal(str(Config.MIN_ESCROW_AMOUNT_USD))
        
        if payment_method and payment_method.startswith("crypto_"):
            # For crypto payments, validate crypto amount in USD equivalent
            crypto = payment_method.replace("crypto_", "").upper()
            from utils.dynamic_minimum_validator import DynamicMinimumValidator
            
            is_valid, error_msg, min_crypto = await DynamicMinimumValidator.validate_crypto_amount(
                crypto, amount, min_usd, "escrow"
            )
            
            if not is_valid:
                if update.message:
                    await update.message.reply_text(
                        error_msg,
                        parse_mode="Markdown",
                        reply_markup=InlineKeyboardMarkup(
                            [
                                [
                                    InlineKeyboardButton(
                                        "❌ Cancel Trade", callback_data="cancel_escrow"
                                    )
                                ]
                            ]
                        ),
                    )
                return EscrowStates.AMOUNT_INPUT
        else:
            # For USD/fiat payments, use simple USD minimum check
            if amount < min_usd:
                if update.message:
                    await update.message.reply_text(
                        f"❌ Minimum trade is ${min_usd} USD. Enter a higher amount:",
                        reply_markup=InlineKeyboardMarkup(
                            [
                                [
                                    InlineKeyboardButton(
                                        "❌ Cancel Trade", callback_data="cancel_escrow"
                                    )
                                ]
                            ]
                        ),
                    )
                return EscrowStates.AMOUNT_INPUT
        
        # MAXIMUM VALIDATION: Check against maximum escrow amount
        max_usd = Config.MAX_ESCROW_AMOUNT_USD
        if amount > max_usd:
            if update.message:
                await update.message.reply_text(
                    f"❌ Maximum trade is ${max_usd:,.0f} USD. Enter a lower amount:",
                    reply_markup=InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    "❌ Cancel Trade", callback_data="cancel_escrow"
                                )
                            ]
                        ]
                    ),
                )
            return EscrowStates.AMOUNT_INPUT

        # CLEANUP: Remove processing message on success
        try:
            if processing_msg:
                await processing_msg.delete()
        except Exception:
            pass
            
        # Store amount
        if context.user_data and "escrow_data" in context.user_data:
            escrow_data = context.user_data["escrow_data"]
            escrow_data["amount"] = amount
            
            # SMART ROUTING: Check if user is editing from trade review
            is_trade_review_edit = escrow_data.get("trade_review_active") or escrow_data.get("last_screen") == "trade_review"
            
            if is_trade_review_edit:
                # User is editing from trade review - recalculate fees and return to review
                logger.info(f"💰 SMART_AMOUNT: User editing from trade review, recalculating fees and returning to review")
                
                # Preserve first-trade-free status by checking existing fee breakdown
                is_first_trade_free = escrow_data.get("fee_breakdown", {}).get("is_first_trade_free", False)
                
                # Recalculate fees with preserved first-trade-free status
                from utils.fee_calculator import FeeCalculator
                from models import User
                
                async with async_managed_session() as db_session:
                    user = update.effective_user
                    db_user = None
                    if user:
                        user_stmt = select(User).where(User.telegram_id == user.id)
                        user_result = await db_session.execute(user_stmt)
                        db_user = user_result.scalar_one_or_none()
                    
                    # Recalculate with preserved first-trade-free flag using async version
                    fee_breakdown = await FeeCalculator.calculate_escrow_breakdown_async(
                        escrow_amount=amount,
                        fee_split_option=escrow_data.get("fee_split_option", "buyer_pays"),
                        user=db_user,
                        session=db_session,
                        is_first_trade=is_first_trade_free  # Preserve promotion status
                    )
                    
                    # Update fee breakdown
                    escrow_data["fee_breakdown"] = fee_breakdown
                    escrow_data["buyer_fee"] = Decimal(str(fee_breakdown["buyer_fee_amount"]))
                    escrow_data["seller_fee"] = Decimal(str(fee_breakdown["seller_fee_amount"]))
                    context.user_data["escrow_data"] = escrow_data  # type: ignore[index]
                    
                    logger.info(f"💰 SMART_AMOUNT: Fees recalculated - first_trade_free={is_first_trade_free}, buyer_fee=${fee_breakdown['buyer_fee_amount']:.2f}")
                
                # Update database state to trade_review for direct handler routing
                user = update.effective_user
                if user:
                    from handlers.escrow_direct import set_user_state
                    await set_user_state(user.id, "trade_review")
                    logger.info(f"💰 SMART_AMOUNT: Set user {user.id} state to trade_review")
                
                # Show updated trade review (function is defined later in this file)
                await show_trade_review(None, context, update)
                
                return EscrowStates.TRADE_REVIEW

        text = """📦 Trade Description (Step 3 of 4)
━━━▫️ 75% Complete

What's this trade for?
Describe the transaction clearly:

💡 Your progress is saved automatically"""

        if update.message:
            await update.message.reply_text(
                text,
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "❌ Cancel Trade", callback_data="cancel_escrow"
                            )
                        ]
                    ]
                ),
            )

        total_elapsed = (time.perf_counter() - start_time) * 1000
        logger.info(f"⏱️ TIMING: handle_amount_input completed in {total_elapsed:.1f}ms (target: <300ms)")
        return EscrowStates.DESCRIPTION_INPUT

    except ValueError:
        if update.message:
            await update.message.reply_text(
                "❌ Invalid amount\n\n💡 Please enter numbers only (like: 100, 500.50)\n\nYour previous progress is saved",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "🔄 Try Again", callback_data="retry_amount"
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                "❌ Cancel", callback_data="cancel_escrow"
                            ),
                            InlineKeyboardButton(
                                "🏠 Main Menu", callback_data="main_menu"
                            ),
                        ],
                    ]
                ),
            )
        return EscrowStates.AMOUNT_INPUT

async def handle_description_input(update: TelegramUpdate, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle trade description input"""
    import time
    start_time = time.perf_counter()
    
    # INSTANT FEEDBACK: Send typing indicator IMMEDIATELY before any processing
    if update.message and update.message.chat:
        try:
            await update.message.chat.send_action("typing")
            logger.info("⚡ INSTANT FEEDBACK: Typing indicator sent BEFORE database operations")
        except Exception as e:
            logger.warning(f"Could not send typing indicator: {e}")
    
    # RACE CONDITION FIX: Rehydrate context from database if missing
    from utils.escrow_context_helper import ensure_escrow_context_with_fallback
    
    user_id = update.effective_user.id if update.effective_user else 0
    
    context_start = time.perf_counter()
    if not await ensure_escrow_context_with_fallback(user_id, context, update):
        logger.error(f"❌ ESCROW_CONTEXT: Failed to ensure context for user {user_id} in description_input")
        return CONV_END
    context_elapsed = (time.perf_counter() - context_start) * 1000
    logger.info(f"⏱️ TIMING: Context rehydration took {context_elapsed:.1f}ms")
    
    description = (
        update.message.text.strip() if update.message and update.message.text else ""
    )

    if len(description) < 10:
        if update.message:
            await update.message.reply_text(
                "❌ Description too short\n\n💡 Please provide more details (minimum 10 characters)\n\nYour progress is still saved",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "🔄 Try Again", callback_data="retry_description"
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                "❌ Cancel", callback_data="cancel_escrow"
                            ),
                            InlineKeyboardButton(
                                "🏠 Main Menu", callback_data="main_menu"
                            ),
                        ],
                    ]
                ),
            )
        return EscrowStates.DESCRIPTION_INPUT

    # Store description
    if context.user_data and "escrow_data" in context.user_data:
        escrow_data = context.user_data["escrow_data"]
        escrow_data["description"] = description
        
        # SMART ROUTING: Check if user is editing from trade review
        is_trade_review_edit = escrow_data.get("trade_review_active") or escrow_data.get("last_screen") == "trade_review"
        
        if is_trade_review_edit:
            # User is editing from trade review - return to review
            logger.info(f"📝 SMART_DESCRIPTION: User editing from trade review, returning to review")
            
            # Update database state to trade_review for direct handler routing
            user = update.effective_user
            if user:
                from handlers.escrow_direct import set_user_state
                await set_user_state(user.id, "trade_review")
                logger.info(f"📝 SMART_DESCRIPTION: Set user {user.id} state to trade_review")
            
            # Show updated trade review (function is defined later in this file)
            await show_trade_review(None, context, update)
            
            return EscrowStates.TRADE_REVIEW

    # STEP 4 of 4: Delivery & Fees (Final Step)
    text = """⏰ Delivery & Fees (Step 4 of 4)
━━━━ 100% Complete

Delivery deadline:"""

    if update.message:
        await update.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("⚡ 24h", callback_data="delivery_24"),
                        InlineKeyboardButton("🔥 48h", callback_data="delivery_48"),
                    ],
                    [
                        InlineKeyboardButton("⭐ 72h", callback_data="delivery_72"),
                        InlineKeyboardButton(
                            "🕐 Custom", callback_data="delivery_custom"
                        ),
                    ],
                    [
                        InlineKeyboardButton(
                            "❌ Cancel", callback_data="cancel_escrow"
                        ),
                        InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu"),
                    ],
                ]
            ),
        )

    total_elapsed = (time.perf_counter() - start_time) * 1000
    logger.info(f"⏱️ TIMING: handle_description_input completed in {total_elapsed:.1f}ms (target: <300ms)")
    return EscrowStates.DELIVERY_TIME

async def handle_delivery_time_callback(
    update: TelegramUpdate, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle delivery time selection"""
    query = update.callback_query
    # PERFORMANCE: Instant acknowledgment
    if query:
        # IMMEDIATE FEEDBACK: Escrow action
        await safe_answer_callback_query(query, "🛡️ Escrow action")

    if query and query.data == "cancel_escrow":
        # CRITICAL FIX: Actually cancel the escrow in database if it was created
        escrow_id_to_cancel = None
        if context.user_data and "escrow_data" in context.user_data:
            escrow_data = context.user_data["escrow_data"]
            # Check for escrow ID in various possible locations
            escrow_id_to_cancel = (
                escrow_data.get("unified_escrow_id") or 
                escrow_data.get("escrow_id") or 
                escrow_data.get("database_escrow_id")
            )
        
        # If an escrow was created, cancel it in the database
        if escrow_id_to_cancel:
            try:
                async with async_managed_session() as session:
                    # Find the escrow - support both string ID and numeric ID
                    if isinstance(escrow_id_to_cancel, str):
                        stmt = select(Escrow).where(Escrow.escrow_id == escrow_id_to_cancel)
                    else:
                        stmt = select(Escrow).where(Escrow.id == escrow_id_to_cancel)
                    
                    result = await session.execute(stmt)
                    escrow_to_cancel = result.scalar_one_or_none()
                    
                    if escrow_to_cancel and escrow_to_cancel.status == "payment_pending":
                        # Only cancel if it's in payment_pending status
                        escrow_to_cancel.status = "cancelled"
                        escrow_to_cancel.cancelled_at = datetime.now(timezone.utc)
                        await session.commit()
                        logger.info(f"✅ ESCROW_CANCELLED: {escrow_id_to_cancel} cancelled in database by user")
                    elif escrow_to_cancel:
                        logger.info(f"⚠️ ESCROW_NOT_CANCELLED: {escrow_id_to_cancel} in status {escrow_to_cancel.status}, not cancelling")
            except Exception as e:
                logger.error(f"❌ Error cancelling escrow {escrow_id_to_cancel}: {e}")
        
        await safe_edit_message_text(
            query,  # type: ignore
            "❌ Trade creation cancelled.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🏠 Main Menu", callback_data="back_to_main")]]
            ),
        )
        if context.user_data:
            context.user_data.pop("escrow_data", None)
        return CONV_END

    if query and query.data == "back_to_trade_review":
        # Return to trade review from delivery time editing
        await show_trade_review(query, context)
        return EscrowStates.TRADE_REVIEW

    if query and query.data == "delivery_24":
        hours = 24
    elif query and query.data == "delivery_48":
        hours = 48
    elif query and query.data == "delivery_72":
        hours = 72
    elif query and query.data == "delivery_custom":
        await safe_edit_message_text(
            query,  # type: ignore
            "⏱️ Enter custom delivery time in hours (1-168):",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "❌ Cancel Trade", callback_data="cancel_escrow"
                        )
                    ]
                ]
            ),
        )
        return EscrowStates.DELIVERY_TIME
    else:
        # Invalid callback data - return to delivery time selection
        return EscrowStates.DELIVERY_TIME

    # Store delivery time and show summary
    if context.user_data and "escrow_data" in context.user_data:
        context.user_data["escrow_data"]["delivery_hours"] = hours
        
        # SMART ROUTING: Check if editing from trade review
        escrow_data = context.user_data.get("escrow_data", {})
        is_trade_review_edit = escrow_data.get("trade_review_active") and escrow_data.get("last_screen") == "trade_review"
        
        if is_trade_review_edit:
            # User is editing from trade review - return to review
            logger.info(f"⏰ SMART_DELIVERY: User editing from trade review, returning to review")
            
            # Update database state to trade_review for direct handler routing
            user = update.effective_user
            if user:
                from handlers.escrow_direct import set_user_state
                await set_user_state(user.id, "trade_review")
                logger.info(f"⏰ SMART_DELIVERY: Set user {user.id} state to trade_review")
            
            # Show updated trade review (function is defined later in this file)
            await show_trade_review(query, context)
            
            return EscrowStates.TRADE_REVIEW

    # NEW: Redirect to fee split selection instead of payment
    await show_fee_split_options(query, context)
    return EscrowStates.FEE_SPLIT_OPTION

async def handle_delivery_time_input(
    update: TelegramUpdate, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle custom delivery time input"""
    import time
    start_time = time.perf_counter()
    
    # INSTANT FEEDBACK: Send typing indicator IMMEDIATELY before any processing
    if update.message and update.message.chat:
        try:
            await update.message.chat.send_action("typing")
            logger.info("⚡ INSTANT FEEDBACK: Typing indicator sent BEFORE database operations")
        except Exception as e:
            logger.warning(f"Could not send typing indicator: {e}")
    
    # RACE CONDITION FIX: Rehydrate context from database if missing
    from utils.escrow_context_helper import ensure_escrow_context_with_fallback
    
    user_id = update.effective_user.id if update.effective_user else 0
    
    context_start = time.perf_counter()
    if not await ensure_escrow_context_with_fallback(user_id, context, update):
        logger.error(f"❌ ESCROW_CONTEXT: Failed to ensure context for user {user_id} in delivery_time_input")
        return CONV_END
    context_elapsed = (time.perf_counter() - context_start) * 1000
    logger.info(f"⏱️ TIMING: Context rehydration took {context_elapsed:.1f}ms")
    
    try:
        hours = (
            int(update.message.text.strip())
            if update.message and update.message.text
            else 24
        )

        if hours < 1 or hours > 168:
            if update.message:
                await update.message.reply_text(
                    "❌ Between 1-168 hours only (1 week max). Try again:",
                    reply_markup=InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    "❌ Cancel Trade", callback_data="cancel_escrow"
                                )
                            ]
                        ]
                    ),
                )
            return EscrowStates.DELIVERY_TIME

        # Store delivery time
        if context.user_data and "escrow_data" in context.user_data:
            context.user_data["escrow_data"]["delivery_hours"] = hours
            
            # SMART ROUTING: Check if editing from trade review (for custom time input)
            escrow_data = context.user_data.get("escrow_data", {})
            is_trade_review_edit = escrow_data.get("trade_review_active") and escrow_data.get("last_screen") == "trade_review"
            
            if is_trade_review_edit:
                # User is editing from trade review - return to review
                logger.info(f"⏰ SMART_DELIVERY_CUSTOM: User editing from trade review, returning to review")
                
                # Update database state to trade_review for direct handler routing
                user = update.effective_user
                if user:
                    from handlers.escrow_direct import set_user_state
                    await set_user_state(user.id, "trade_review")
                    logger.info(f"⏰ SMART_DELIVERY_CUSTOM: Set user {user.id} state to trade_review")
                
                # Show updated trade review (function is defined later in this file)
                await show_trade_review(None, context, update)
                
                return EscrowStates.TRADE_REVIEW

        # CRITICAL FIX: Check if amount exists before showing fee options
        if (
            context.user_data
            and "escrow_data" in context.user_data
            and "amount" not in context.user_data["escrow_data"]
        ):
            # Amount input was skipped - ask for amount now
            text = """💰 Trade Amount
            
Enter trade amount in USD:"""
            if update.message:
                await update.message.reply_text(
                    text,
                    reply_markup=InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    "❌ Cancel Trade", callback_data="cancel_escrow"
                                )
                            ]
                        ]
                    ),
                )
            return EscrowStates.AMOUNT_INPUT

        # Amount exists, proceed to fee split selection
        await show_fee_split_options_from_message(update, context)
        
        total_elapsed = (time.perf_counter() - start_time) * 1000
        logger.info(f"⏱️ TIMING: handle_delivery_time_input completed in {total_elapsed:.1f}ms (target: <300ms)")
        return EscrowStates.FEE_SPLIT_OPTION

    except ValueError:
        if update.message:
            await update.message.reply_text(
                "❌ Enter hours as numbers only (like: 24, 48, 72):",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "❌ Cancel Trade", callback_data="cancel_escrow"
                            )
                        ]
                    ]
                ),
            )
        return EscrowStates.DELIVERY_TIME

# NEW: Fee Split Selection Handlers
async def show_fee_split_options(query, context: ContextTypes.DEFAULT_TYPE) -> Optional[int]:
    """Show fee split options from callback query"""
    escrow_data = (
        context.user_data["escrow_data"]
        if context.user_data and "escrow_data" in context.user_data
        else {}
    )

    # CRITICAL FIX: Handle missing amount gracefully
    if "amount" not in escrow_data:
        await safe_edit_message_text(
            query,
            "❌ Error: Missing trade amount. Please restart trade creation.",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🤝 Start New Trade", callback_data="create_secure_trade"
                        )
                    ]
                ]
            ),
        )
        return CONV_END

    # Enhanced: Use Decimal for precise fee calculations
    amount = escrow_data["amount"]  # Amount is already Decimal from amount input
    if isinstance(amount, float):  # Backward compatibility
        amount = Decimal(str(amount))

    # FIRST TRADE FREE: Use FeeCalculator for proper fee calculation including first trade free logic
    from utils.fee_calculator import FeeCalculator
    from models import User
    
    # Get user and session for first trade detection
    user = query.from_user if query else None

    db_user = None
    
    async with async_managed_session() as db_session:
        if user:
            user_stmt = select(User).where(User.telegram_id == user.id)
            user_result = await db_session.execute(user_stmt)
            db_user = user_result.scalar_one_or_none()
        
        # Check first trade status using async method
        is_first_trade = None
        if db_user:
            is_first_trade = await FeeCalculator.is_users_first_escrow_async(db_user.id, db_session)  # type: ignore[arg-type]
        
        # Calculate fees using async FeeCalculator (includes first trade free logic and trader discounts)
        fee_breakdown = await FeeCalculator.calculate_escrow_breakdown_async(
            escrow_amount=amount,
            fee_split_option="buyer_pays",  # Default for display
            user=db_user,
            session=db_session,
            is_first_trade=is_first_trade
        )
        
        total_fee = Decimal(str(fee_breakdown["total_platform_fee"]))
        is_first_trade_free = fee_breakdown.get("is_first_trade_free", False)
        
        # Calculate split fee (half of total fee)
        split_fee = (total_fee / Decimal("2")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    # UX IMPROVEMENT: Skip fee split selection if fee is $0.00 (first trade free)
    if total_fee == Decimal("0.00"):
        # Store default fee split option and fee breakdown
        escrow_data["fee_split_option"] = "buyer_pays"
        escrow_data["fee_breakdown"] = fee_breakdown
        escrow_data["buyer_fee"] = Decimal("0.00")
        escrow_data["seller_fee"] = Decimal("0.00")
        context.user_data["escrow_data"] = escrow_data  # type: ignore[index]
        
        # Calculate normal fee with minimum fee system (what they WOULD have paid)
        fee_percentage = Decimal(str(Config.ESCROW_FEE_PERCENTAGE)) / Decimal("100")
        calculated_fee = (amount * fee_percentage).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        
        # Apply minimum fee logic: escrows under threshold have minimum fee
        min_fee_threshold = Decimal(str(Config.MIN_ESCROW_FEE_THRESHOLD))
        min_fee_amount = Decimal(str(Config.MIN_ESCROW_FEE_AMOUNT))
        
        if amount < min_fee_threshold and calculated_fee < min_fee_amount:
            normal_fee = min_fee_amount  # They would have paid minimum fee
        else:
            normal_fee = calculated_fee  # They would have paid configured %
        
        # Show celebration message with Continue button
        text = (
            f"🎉 <b>First Trade Free!</b>\n\n"
            f"Your first escrow is on us - no platform fees!\n\n"
            f"📦 Escrow Amount: {format_money(amount, 'USD')}\n"
            f"💰 Platform Fee: <b>$0.00</b>\n"
            f"💸 You saved: <b>{format_money(normal_fee, 'USD')}</b>\n\n"
            f"Click Continue when you're ready to proceed..."
        )
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Continue ➡️", callback_data="fee_split_free_continue")],
            [InlineKeyboardButton("❌ Cancel Trade", callback_data="cancel_escrow")]
        ])
        
        if query:
            await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)
        
        # Stay in FEE_SPLIT_OPTION state to handle the Continue button callback
        return EscrowStates.FEE_SPLIT_OPTION
    
    # Include fee policy transparency with first trade free messaging
    if is_first_trade_free:
        text = f"""🎉 First Trade FREE! - {format_money(amount, 'USD')} + $0.00 USD fee

🤝 Split: {format_money(amount, 'USD')} (No fees!)
💳 You pay: {format_money(amount, 'USD')}
🏪 Seller pays: {format_money(amount, 'USD')}

🎁 Welcome promotion - Zero platform fees on your first trade!"""
    else:
        # Use Decimal for precise calculations - no float conversion needed
        amount_display = format_money(amount, 'USD')
        split_fee_display = format_money(split_fee, 'USD')
        total_fee_display = format_money(total_fee, 'USD')
        
        # Check if minimum fee was applied (only show tooltip when relevant)
        min_fee_threshold = Decimal(str(Config.MIN_ESCROW_FEE_THRESHOLD))
        min_fee_amount = Decimal(str(Config.MIN_ESCROW_FEE_AMOUNT))
        fee_percentage = Decimal(str(Config.ESCROW_FEE_PERCENTAGE)) / Decimal("100")
        calculated_fee = (amount * fee_percentage).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        
        # Show tooltip if: escrow under threshold AND minimum fee was applied
        show_min_fee_note = (
            amount < min_fee_threshold and 
            min_fee_amount > 0 and 
            calculated_fee < min_fee_amount and
            total_fee >= min_fee_amount
        )
        
        buyer_split = format_money(amount + split_fee, 'USD')
        seller_split = format_money(amount - split_fee, 'USD')
        buyer_pays_all = format_money(amount + total_fee, 'USD')
        seller_pays_all = format_money(amount - total_fee, 'USD')
        
        text = f"""Who pays {total_fee_display} fee?

🤝 Split 50/50: You {buyer_split} • Seller {seller_split} ({split_fee_display} each)
💳 You Pay All: You {buyer_pays_all} • Seller {amount_display}
🏪 Seller Pays: You {amount_display} • Seller {seller_pays_all}

✅ Refundable if cancelled"""
        
        # Add subtle minimum fee note only when applicable
        if show_min_fee_note:
            text += f"\nℹ️ Minimum ${min_fee_amount:.0f} fee applies to escrows under ${min_fee_threshold:.0f}"

    # Context-aware back button: if editing from trade review, go back to review
    # Otherwise, go back to delivery time (previous step in flow)
    from_trade_review = escrow_data.get("from_trade_review", False)
    back_callback = "back_to_trade_review" if from_trade_review else "back_to_delivery"
    
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🤝 Split", callback_data="fee_split")],
            [InlineKeyboardButton("💳 I Pay All", callback_data="fee_buyer_pays")],
            [InlineKeyboardButton("🏪 Seller Pays", callback_data="fee_seller_pays")],
            [InlineKeyboardButton("⬅️ Back", callback_data=back_callback)],
            [InlineKeyboardButton("❌ Cancel Trade", callback_data="cancel_escrow")],
        ]
    )

    if query:
        await safe_edit_message_text(query, text, reply_markup=keyboard)

async def show_fee_split_options_from_message(
    update, context: ContextTypes.DEFAULT_TYPE
) -> Optional[int]:
    """Show fee split options from message update"""
    if not context.user_data or "escrow_data" not in context.user_data:
        if update.message:
            await update.message.reply_text(
                "⏰ Your session timed out.\n\nTap /start to continue where you left off."
            )
        return CONV_END
    escrow_data = context.user_data["escrow_data"]

    # CRITICAL FIX: Handle missing amount gracefully
    if "amount" not in escrow_data:
        await update.message.reply_text(
            "❌ Error: Missing trade amount. Please restart trade creation.",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🤝 Start New Trade", callback_data="create_secure_trade"
                        )
                    ]
                ]
            ),
        )
        return CONV_END

    # Enhanced: Use Decimal for precise fee calculations
    amount = escrow_data["amount"]  # Amount is already Decimal from amount input
    if isinstance(amount, float):  # Backward compatibility
        amount = Decimal(str(amount))

    # FIRST TRADE FREE: Use FeeCalculator for proper fee calculation including first trade free logic
    from utils.fee_calculator import FeeCalculator
    from models import User
    
    # Get user for first trade detection using async session
    user = update.effective_user if update else None
    db_user = None
    

    async with async_managed_session() as db_session:
        if user:
            user_stmt = select(User).where(User.telegram_id == user.id)
            user_result = await db_session.execute(user_stmt)
            db_user = user_result.scalar_one_or_none()
        
        # Check first trade status using async method
        is_first_trade = None
        if db_user:
            is_first_trade = await FeeCalculator.is_users_first_escrow_async(db_user.id, db_session)  # type: ignore[arg-type]
        
        # Calculate fees using async FeeCalculator (includes first trade free logic and trader discounts)
        fee_breakdown = await FeeCalculator.calculate_escrow_breakdown_async(
            escrow_amount=amount,
            fee_split_option="buyer_pays",  # Default for display
            user=db_user,
            session=db_session,
            is_first_trade=is_first_trade
        )
        
        total_fee = Decimal(str(fee_breakdown["total_platform_fee"]))
        is_first_trade_free = fee_breakdown.get("is_first_trade_free", False)
        
        # Calculate split fee (half of total fee)
        split_fee = (total_fee / Decimal("2")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    # UX IMPROVEMENT: Skip fee split selection if fee is $0.00 (first trade free)
    if total_fee == Decimal("0.00"):
        # Store default fee split option and fee breakdown
        escrow_data["fee_split_option"] = "buyer_pays"
        escrow_data["fee_breakdown"] = fee_breakdown
        escrow_data["buyer_fee"] = Decimal("0.00")
        escrow_data["seller_fee"] = Decimal("0.00")
        context.user_data["escrow_data"] = escrow_data  # type: ignore[index]
        
        # Calculate normal fee with minimum fee system (what they WOULD have paid)
        fee_percentage = Decimal(str(Config.ESCROW_FEE_PERCENTAGE)) / Decimal("100")
        calculated_fee = (amount * fee_percentage).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        
        # Apply minimum fee logic: escrows under threshold have minimum fee
        min_fee_threshold = Decimal(str(Config.MIN_ESCROW_FEE_THRESHOLD))
        min_fee_amount = Decimal(str(Config.MIN_ESCROW_FEE_AMOUNT))
        
        if amount < min_fee_threshold and calculated_fee < min_fee_amount:
            normal_fee = min_fee_amount  # They would have paid minimum fee
        else:
            normal_fee = calculated_fee  # They would have paid configured %
        
        # Show celebration message with Continue button
        celebration_text = (
            f"🎉 <b>First Trade Free!</b>\n\n"
            f"Your first escrow is on us - no platform fees!\n\n"
            f"📦 Escrow Amount: {format_money(amount, 'USD')}\n"
            f"💰 Platform Fee: <b>$0.00</b>\n"
            f"💸 You saved: <b>{format_money(normal_fee, 'USD')}</b>\n\n"
            f"Click Continue when you're ready to proceed..."
        )
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Continue ➡️", callback_data="fee_split_free_continue")],
            [InlineKeyboardButton("❌ Cancel Trade", callback_data="cancel_escrow")]
        ])
        
        if update.message:
            await update.message.reply_text(celebration_text, parse_mode="HTML", reply_markup=keyboard)
        
        # Stay in FEE_SPLIT_OPTION state to handle the Continue button callback
        return EscrowStates.FEE_SPLIT_OPTION
    
    # Include fee policy transparency with first trade free messaging
    if is_first_trade_free:
        text = f"""🎉 First Trade FREE! - {format_money(amount, 'USD')} + $0.00 USD fee

🤝 Split: {format_money(amount, 'USD')} (No fees!)
💳 You pay: {format_money(amount, 'USD')}
🏪 Seller pays: {format_money(amount, 'USD')}

🎁 Welcome promotion - Zero platform fees on your first trade!"""
    else:
        # Use Decimal for precise calculations - no float conversion needed
        amount_display = format_money(amount, 'USD')
        split_fee_display = format_money(split_fee, 'USD')
        total_fee_display = format_money(total_fee, 'USD')
        
        # Check if minimum fee was applied (only show tooltip when relevant)
        min_fee_threshold = Decimal(str(Config.MIN_ESCROW_FEE_THRESHOLD))
        min_fee_amount = Decimal(str(Config.MIN_ESCROW_FEE_AMOUNT))
        fee_percentage = Decimal(str(Config.ESCROW_FEE_PERCENTAGE)) / Decimal("100")
        calculated_fee = (amount * fee_percentage).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        
        # Show tooltip if: escrow under threshold AND minimum fee was applied
        show_min_fee_note = (
            amount < min_fee_threshold and 
            min_fee_amount > 0 and 
            calculated_fee < min_fee_amount and
            total_fee >= min_fee_amount
        )
        
        buyer_split = format_money(amount + split_fee, 'USD')
        seller_split = format_money(amount - split_fee, 'USD')
        buyer_pays_all = format_money(amount + total_fee, 'USD')
        seller_pays_all = format_money(amount - total_fee, 'USD')
        
        text = f"""Who pays {total_fee_display} fee?

🤝 Split 50/50: You {buyer_split} • Seller {seller_split} ({split_fee_display} each)
💳 You Pay All: You {buyer_pays_all} • Seller {amount_display}
🏪 Seller Pays: You {amount_display} • Seller {seller_pays_all}

✅ Refundable if cancelled"""
        
        # Add subtle minimum fee note only when applicable
        if show_min_fee_note:
            text += f"\nℹ️ Minimum ${min_fee_amount:.0f} fee applies to escrows under ${min_fee_threshold:.0f}"

    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🤝 Split", callback_data="fee_split")],
            [InlineKeyboardButton("💳 I Pay All", callback_data="fee_buyer_pays")],
            [InlineKeyboardButton("🏪 Seller Pays", callback_data="fee_seller_pays")],
            [InlineKeyboardButton("⬅️ Back", callback_data="back_to_delivery")],
            [InlineKeyboardButton("❌ Cancel Trade", callback_data="cancel_escrow")],
        ]
    )

    await update.message.reply_text(text, reply_markup=keyboard)

async def handle_fee_split_selection(
    update: TelegramUpdate, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle fee split option selection"""
    query = update.callback_query
    if query:
        # IMMEDIATE FEEDBACK: Escrow action
        await safe_answer_callback_query(query, "🛡️ Escrow action")

    # Handle Continue button from first trade free celebration
    if query and query.data == "fee_split_free_continue":
        # Proceed to trade review
        await show_trade_review(query, context)
        return EscrowStates.TRADE_REVIEW

    # Handle back to fee options from payment method
    if query and query.data == "back_to_fee_options":
        await show_fee_split_options(query, context)
        return EscrowStates.FEE_SPLIT_OPTION

    if query and query.data == "cancel_escrow":
        await safe_edit_message_text(
            query,
            "❌ Trade creation cancelled.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🏠 Main Menu", callback_data="back_to_main")]]
            ),
        )
        if context.user_data:
            context.user_data.pop("escrow_data", None)
        return CONV_END

    if query and query.data == "back_to_trade_review":
        # Back to trade review (when editing from review page)
        # Clear the from_trade_review flag
        if context.user_data and "escrow_data" in context.user_data:
            context.user_data["escrow_data"].pop("from_trade_review", None)
        await show_trade_review(query, context)
        return EscrowStates.TRADE_REVIEW
    
    if query and query.data == "back_to_delivery":
        # Back to delivery time selection
        text = """⏱️ Delivery Time

Choose delivery deadline:"""

        await safe_edit_message_text(
            query,
            text,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("24h", callback_data="delivery_24"),
                        InlineKeyboardButton("48h", callback_data="delivery_48"),
                    ],
                    [
                        InlineKeyboardButton("72h", callback_data="delivery_72"),
                        InlineKeyboardButton("Custom", callback_data="delivery_custom"),
                    ],
                    [
                        InlineKeyboardButton(
                            "❌ Cancel Trade", callback_data="cancel_escrow"
                        )
                    ],
                ]
            ),
        )
        return EscrowStates.DELIVERY_TIME

    # Process fee split choice
    if not context.user_data or "escrow_data" not in context.user_data:
        return EscrowStates.FEE_SPLIT_OPTION
    escrow_data = context.user_data["escrow_data"]
    if "amount" not in escrow_data:
        return EscrowStates.FEE_SPLIT_OPTION
    amount = Decimal(str(escrow_data["amount"]))  # Use Decimal for precision

    # Determine fee split option based on button clicked
    fee_split_option = None
    if query and query.data == "fee_split":
        fee_split_option = "split"
        option_text = "Split Fees"
    elif query and query.data == "fee_buyer_pays":
        fee_split_option = "buyer_pays"
        option_text = "Buyer Pays All"
    elif query and query.data == "fee_seller_pays":
        fee_split_option = "seller_pays"
        option_text = "Seller Pays All"
    else:
        return EscrowStates.FEE_SPLIT_OPTION

    # CRITICAL FIX: Calculate complete fee_breakdown with first-trade-free detection and trader discounts
    from utils.fee_calculator import FeeCalculator
    from database import async_managed_session
    from models import User
    
    user_id = safe_get_user_id(query)
    is_first_trade = False
    db_user = None
    
    # Check if this is user's first trade and get user for trader discounts (async)
    if user_id:
        try:
            async with async_managed_session() as session:
                # Get user for trader discount calculation
                user_stmt = select(User).where(User.telegram_id == user_id)
                user_result = await session.execute(user_stmt)
                db_user = user_result.scalar_one_or_none()
                
                # Check first trade status
                is_first_trade = await FeeCalculator.is_users_first_escrow_async(user_id, session)
                
                # Calculate fees with async version to apply trader discounts
                fee_breakdown = await FeeCalculator.calculate_escrow_breakdown_async(
                    escrow_amount=amount,
                    fee_split_option=fee_split_option,
                    user=db_user,
                    session=session,
                    is_first_trade=is_first_trade
                )
        except Exception as e:
            logger.error(f"Error checking first trade status or calculating fees: {e}")
            is_first_trade = False
            # Fallback to sync calculation without discounts
            fee_breakdown = FeeCalculator.calculate_escrow_breakdown(
                escrow_amount=amount,
                fee_split_option=fee_split_option,
                is_first_trade=is_first_trade
            )
    else:
        # No user ID - use basic calculation
        fee_breakdown = FeeCalculator.calculate_escrow_breakdown(
            escrow_amount=amount,
            fee_split_option=fee_split_option,
            is_first_trade=is_first_trade
        )
    
    # Store complete fee_breakdown (preserves is_first_trade_free flag)
    escrow_data["fee_split_option"] = fee_split_option
    escrow_data["fee_breakdown"] = fee_breakdown
    escrow_data["buyer_fee"] = Decimal(str(fee_breakdown["buyer_fee_amount"]))
    escrow_data["seller_fee"] = Decimal(str(fee_breakdown["seller_fee_amount"]))
    
    logger.info(f"💰 FEE_SPLIT_SELECTION: option={fee_split_option}, is_first_trade={is_first_trade}, buyer_fee=${fee_breakdown['buyer_fee_amount']}")

    # Store the selection and proceed to trade review
    context.user_data["escrow_data"] = escrow_data
    
    # Clear from_trade_review flag since we're now moving forward to trade review
    escrow_data.pop("from_trade_review", None)

    # Show trade review page (NEW: Match exchange flow pattern)
    await show_trade_review(query, context)
    return EscrowStates.TRADE_REVIEW

async def show_trade_review(query, context: ContextTypes.DEFAULT_TYPE, update=None) -> Optional[int]:
    """NEW: Show comprehensive trade review page matching exchange flow pattern"""
    if not context.user_data or "escrow_data" not in context.user_data:
        if query:
            await safe_edit_message_text(
                query,
                "⏰ Session Expired\n\nYour trade session timed out due to inactivity.\n\n⚡ Tap /start to create a new trade.",
            )
        elif update and update.message:
            await update.message.reply_text("⏰ Session Expired\n\nYour trade session timed out due to inactivity.\n\n⚡ Tap /start to create a new trade.")
        return CONV_END

    escrow_data = context.user_data["escrow_data"]
    
    # SMART UX FIX: Mark user as being in trade review mode for intelligent amount input handling
    user_id = safe_get_user_id(query)
    if user_id:
        escrow_data["trade_review_active"] = True
        escrow_data["last_screen"] = "trade_review"
        context.user_data["escrow_data"] = escrow_data  # type: ignore[index]
        
        # Set lightweight database state for smart routing
        from handlers.escrow_direct import set_user_state
        await set_user_state(user_id, "trade_review")
        logger.info(f"🎯 Set user {user_id} in trade_review mode for smart amount handling")

    amount = Decimal(str(escrow_data["amount"]))
    buyer_fee = Decimal(str(escrow_data["buyer_fee"]))
    total_to_pay = amount + buyer_fee

    # Fix: Use HTML escaping since message uses parse_mode="HTML"
    import html
    seller_identifier_clean = escrow_data.get('seller_identifier', '')
    
    # Remove any markdown escaping characters if they exist (critical for underscores)
    if seller_identifier_clean:
        seller_identifier_clean = str(seller_identifier_clean).replace('\\', '')
        # Also update the stored value to prevent future issues
        escrow_data['seller_identifier'] = seller_identifier_clean
        context.user_data["escrow_data"] = escrow_data  # type: ignore[index]
    
    seller_display = (
        format_username_html(f"@{seller_identifier_clean}", include_link=False)
        if escrow_data["seller_type"] == "username"
        else html.escape(seller_identifier_clean)  # Keep for non-username types
    )
    
    # GET SELLER REVIEWS FOR BUYER TO SEE
    seller_reviews_text = ""
    if escrow_data["seller_type"] == "username":
        # Import Rating model if not already imported
        try:
            from models import Rating

            # Get seller user and their recent reviews using async session (case-insensitive)
            async with async_managed_session() as session:
                from sqlalchemy import func
                seller_user_stmt = select(User).where(func.lower(User.username) == func.lower(escrow_data['seller_identifier']))
                seller_user_result = await session.execute(seller_user_stmt)
                seller_user = seller_user_result.scalar_one_or_none()
                
                if seller_user:
                    # Get recent reviews for this seller (as seller in trades)
                    reviews_stmt = select(Rating).where(
                        Rating.rated_id == seller_user.id,
                        Rating.category == 'seller'
                    ).order_by(Rating.created_at.desc()).limit(3)
                    reviews_result = await session.execute(reviews_stmt)
                    recent_reviews = reviews_result.scalars().all()
                    
                    if recent_reviews:
                        # Calculate average rating
                        avg_rating = sum(r.rating for r in recent_reviews) / len(recent_reviews)
                        stars = "⭐" * int(avg_rating)  # type: ignore
                        
                        seller_reviews_text = f"\n📊 Seller Reviews: {stars} ({avg_rating:.1f}/5)"
                        
                        # Show most recent review preview
                        latest_review = recent_reviews[0]
                        if getattr(latest_review, 'comment', None):
                            seller_reviews_text += f"\n💬 Latest: \"{latest_review.comment[:40]}...\""
                    else:
                        seller_reviews_text = "\n📊 New seller (no reviews yet)"
        except Exception as e:
            logger.warning(f"Could not fetch seller reviews: {e}")
    
    # FIXED: Get all trade details from database instead of incomplete context data
    description = escrow_data.get("description", "Buying goods")
    delivery_hours = escrow_data.get("delivery_hours", 24)
    fee_option = escrow_data.get("fee_split_option", "split")
    escrow_id = escrow_data.get("early_escrow_id")
    
    # CRITICAL FIX: Retrieve actual database values if escrow exists
    if escrow_id or escrow_data.get("escrow_id"):
        try:
            async with async_managed_session() as db_session:
                from models import Escrow
                # Try both escrow_id formats
                db_escrow_id = escrow_id or escrow_data.get("escrow_id")
                
                if db_escrow_id:
                    # Look up by string escrow_id first
                    stmt = select(Escrow).where(Escrow.escrow_id == db_escrow_id)
                    result = await db_session.execute(stmt)
                    escrow_from_db = result.scalar_one_or_none()
                    
                    if not escrow_from_db and str(db_escrow_id).isdigit():
                        # Fallback: try numeric ID lookup
                        stmt = select(Escrow).where(Escrow.id == int(db_escrow_id))
                        result = await db_session.execute(stmt)
                        escrow_from_db = result.scalar_one_or_none()
                    
                    if escrow_from_db:
                        # Use actual database values
                        description = getattr(escrow_from_db, 'description', None) or description
                        escrow_id = getattr(escrow_from_db, 'escrow_id', escrow_id)
                        
                        # Also get delivery deadline if available  
                        delivery_deadline = getattr(escrow_from_db, 'delivery_deadline', None)
                        if delivery_deadline:
                            try:
                                from datetime import datetime, timezone
                                # Handle timezone-aware datetime properly
                                if delivery_deadline.tzinfo is not None:
                                    now = datetime.now(timezone.utc)
                                else:
                                    now = datetime.now()
                                hours_until_deadline = int((delivery_deadline - now).total_seconds() / 3600)
                                if hours_until_deadline > 0:
                                    delivery_hours = hours_until_deadline
                            except Exception as e:
                                logger.warning(f"Error calculating delivery deadline: {e}")
                                # Continue with default delivery_hours
                                
                        logger.info(f"✅ Retrieved real trade data: {description}, ID: {escrow_id}")
                        # CRITICAL FIX: Set the existing escrow ID so payment creation uses it
                        escrow_data["existing_escrow_id"] = escrow_id
                        escrow_data["early_escrow_id"] = escrow_id  # Ensure payment logic finds this
                    else:
                        # RACE_CONDITION_FIX: Handle case where early escrow ID exists in context but not yet in database
                        # This can happen during the brief window between ID generation and database creation
                        # Instead of blocking the user, log the info and continue gracefully
                        if str(db_escrow_id).startswith("ES") and len(str(db_escrow_id)) >= 10:
                            # This looks like a valid early escrow ID format - likely a timing issue
                            logger.info(f"ℹ️ Early escrow ID {db_escrow_id} not yet in database - using context data")
                            # Use the context data we have - this is normal during early trade creation
                        else:
                            # This might be a genuine issue - log as warning but don't block user
                            logger.warning(f"⚠️ Could not find escrow in database: {db_escrow_id} (continuing with context data)")
                        
                        # Continue gracefully - don't block the user's flow
        except Exception as e:
            logger.error(f"Error retrieving trade data from database: {e}")
    
    # Ensure we have valid display values
    if not escrow_id or escrow_id == "N/A":
        escrow_id = "PENDING"
    if not description or description == "N/A":
        description = "Trade details will be confirmed"
    
    # Format delivery time
    if delivery_hours >= 24:
        delivery_text = f"{delivery_hours // 24} day{'s' if delivery_hours >= 48 else ''}"
    else:
        delivery_text = f"{delivery_hours} hours"
    
    # Format fee option
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
        seller_username = escrow_data.get("seller_identifier", "").replace("\\", "")
    
    text = format_trade_review_message(
        escrow_id_display=escrow_id_display,
        total_to_pay=total_to_pay,
        seller_display=seller_display,
        description=description,
        fee_display=fee_display,
        payment_display=payment_display,
        seller_reviews_text=seller_reviews_text,
        use_html_link=False,
        seller_username=seller_username if seller_username else None,
        delivery_hours=delivery_hours
    )

    # NEW: Trade review keyboard with payment selection logic
    # Check if user has already selected a payment method
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
    
    # Only show "Change Fees" button if NOT first-trade-free
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
    
    # Handle both query (callback) and message update cases
    if query:
        await safe_edit_message_text(query, text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))
    elif update and update.message:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))
    
# NEW: Trade Review Handlers
async def handle_trade_review_callbacks(update: TelegramUpdate, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle all trade review page callbacks (matching exchange flow pattern)"""
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "🛡️ Trade review")
    
    if not query or not query.data:
        return EscrowStates.TRADE_REVIEW
    
    if query.data == "switch_payment_method":
        # Show payment method selection
        return await handle_switch_payment_method(update, context)
    elif query.data == "edit_trade_amount":
        # Go back to amount input
        return await handle_edit_trade_amount(update, context)
    elif query.data == "edit_trade_description":
        # Go back to description input
        return await handle_edit_trade_description(update, context)
    elif query.data == "edit_delivery_time":
        # Go back to delivery time selection
        return await handle_edit_delivery_time(update, context)
    elif query.data == "edit_fee_split":
        # Go back to fee split selection
        return await handle_edit_fee_split(update, context)
    elif query.data == "confirm_trade_final":
        # Proceed to payment method selection
        return await handle_confirm_trade_final(update, context)
    elif query.data == "back_to_trade_review":
        # Return to trade review (for consistency with other states)
        # Clear the from_trade_review flag to prevent it from persisting
        if context.user_data and "escrow_data" in context.user_data:
            context.user_data["escrow_data"].pop("from_trade_review", None)
        await show_trade_review(query, context)
        return EscrowStates.TRADE_REVIEW
    elif query.data == "escrow_add_funds":
        # Handle wallet funding from insufficient balance page
        # INSTANT FEEDBACK: Acknowledge immediately
        await safe_answer_callback_query(query, "⏳ Loading funding options...")
        from handlers.wallet_direct import start_add_funds
        result = await start_add_funds(update, context)
        return result if result is not None else EscrowStates.TRADE_REVIEW
    elif query.data == "cancel_escrow":
        # Cancel the entire trade
        return await handle_cancel_escrow(update, context)
    
    return EscrowStates.TRADE_REVIEW

async def handle_switch_payment_method(update: TelegramUpdate, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Switch payment method from trade review (matches exchange pattern)"""
    query = update.callback_query
    
    if not context.user_data or "escrow_data" not in context.user_data:
        if query:
            await safe_edit_message_text(query, "⏰ Session Expired\n\nInactive too long.\n\n⚡ Tap /start to restart.")
        return CONV_END
    
    escrow_data = context.user_data["escrow_data"]
    amount = Decimal(str(escrow_data["amount"]))
    buyer_fee = Decimal(str(escrow_data["buyer_fee"]))
    total_to_pay = amount + buyer_fee
    
    # Get wallet balance for display and sufficiency check
    wallet_balance_text = "💰 Wallet Balance"
    wallet_balance_decimal: Optional[Decimal] = None
    try:
        user_id = safe_get_user_id(query)
        if user_id:
            async with async_managed_session() as session:
                user_stmt = select(User).where(User.telegram_id == user_id)
                user_result = await session.execute(user_stmt)
                user = user_result.scalar_one_or_none()
                
                if user:
                    wallet_stmt = select(Wallet).where(
                        Wallet.user_id == user.id,
                        Wallet.currency == "USD"
                    )
                    wallet_result = await session.execute(wallet_stmt)
                    usd_wallet = wallet_result.scalar_one_or_none()
                    
                    if usd_wallet:
                        # Include both available_balance AND trading_credit for escrow payments
                        available_value = Decimal(str(usd_wallet.available_balance or 0))
                        trading_credit_value = Decimal(str(usd_wallet.trading_credit or 0))
                        wallet_balance_decimal = safe_add(available_value, trading_credit_value)
                        wallet_balance_text = f"💰 Wallet: {format_money(wallet_balance_decimal, 'USD')}"
                    else:
                        wallet_balance_decimal = Decimal("0")
                        wallet_balance_text = "💰 Wallet: $0.00 USD"
    except Exception as e:
        logger.error(f"Error getting wallet balance: {e}")
        wallet_balance_text = "💰 Wallet Balance"
        wallet_balance_decimal = None
    
    # Show payment method selection
    text = f"""💳 Choose Payment Method

Trade Amount: {format_money(total_to_pay, 'USD')}

Select how you want to pay:"""
    
    # Get payment options with dynamic wallet button
    # Context-aware back button: if we're editing from trade review, go back to review
    payment_keyboard = _create_payment_keyboard(
        wallet_balance_text=wallet_balance_text,
        include_back=True,
        back_callback="back_to_trade_review",  # Always back to review when called from trade review
        total_amount=total_to_pay,
        user_id=safe_get_user_id(query),
        wallet_balance=wallet_balance_decimal
    )
    
    if query:
        await safe_edit_message_text(query, text, reply_markup=payment_keyboard)
    
    return EscrowStates.PAYMENT_METHOD

async def handle_confirm_trade_final(update: TelegramUpdate, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Final trade confirmation - creates escrow and processes selected payment method"""
    query = update.callback_query
    
    if not context.user_data or "escrow_data" not in context.user_data:
        if query:
            await safe_edit_message_text(query, "⏰ Session Expired\n\nInactive too long.\n\n⚡ Tap /start to restart.")
        return CONV_END
    
    escrow_data = context.user_data["escrow_data"]
    payment_method = escrow_data.get("payment_method")
    
    if not payment_method:
        # Should not happen due to UI logic, but safety check
        await safe_edit_message_text(query, "❌ Please select a payment method first.")
        return EscrowStates.TRADE_REVIEW
    
    # CRITICAL UX: Immediate feedback for ALL payment methods
    # User knows their trade confirmation was received and escrow is being created
    if query:
        await safe_answer_callback_query(query, "🔄 Creating your escrow...")
        # INSTANT FEEDBACK: Update message immediately to show processing
        processing_text = f"""🔄 Processing Your Trade...

⏳ Creating secure escrow
⏳ Setting up payment address
⏳ Sending notifications

*Please wait while we set up your trade...*"""
        await safe_edit_message_text(query, processing_text, parse_mode="Markdown")
    
    # Route to appropriate payment processor based on selected method
    if payment_method == "wallet":
        # Calculate total amount for wallet payment
        amount = Decimal(str(escrow_data["amount"]))
        buyer_fee = Decimal(str(escrow_data["buyer_fee"]))
        total_amount = amount + buyer_fee
        return await execute_wallet_payment(query, context, total_amount)
    elif payment_method.startswith("crypto_"):
        return await execute_crypto_payment(update, context)
    elif payment_method == "ngn_bank":
        return await execute_ngn_payment(update, context)
    else:
        await safe_edit_message_text(query, f"❌ Unknown payment method: {payment_method}")
        return EscrowStates.TRADE_REVIEW

# Payment execution functions for confirmed trades
async def execute_wallet_payment(query, context: ContextTypes.DEFAULT_TYPE, total_amount) -> int:
    """Execute wallet payment for confirmed trade - skip confirmation since user already confirmed"""
    # Check if user has sufficient wallet balance first using async session

    async with async_managed_session() as session:
        user_id = safe_get_user_id(query)
        if not user_id:
            await safe_edit_message_text(
                query, "❌ Unable to identify user. Please try again.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("⬅️ Back to Review", callback_data="back_to_trade_review")]]
                )
            )
            return CONV_END

        user_stmt = select(User).where(User.telegram_id == user_id)
        user_result = await session.execute(user_stmt)
        user = user_result.scalar_one_or_none()
        if not user:
            await safe_edit_message_text(
                query, "❌ User not found. Please try again.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("⬅️ Back to Review", callback_data="back_to_trade_review")]]
                )
            )
            return CONV_END

        # Enhanced: Use WalletValidator for robust balance validation
        is_valid, error_message = await WalletValidator.validate_sufficient_balance(
            user_id=int(user.id),  # type: ignore
            required_amount=total_amount,
            currency="USD",
            session=session,
            include_frozen=False,
            purpose="trade payment"
        )
        
        if not is_valid:
            # Get current wallet balance for detailed error display using async query
            wallet_stmt = select(Wallet).where(
                Wallet.user_id == user.id, 
                Wallet.currency == "USD"
            )
            wallet_result = await session.execute(wallet_stmt)
            usd_wallet = wallet_result.scalar_one_or_none()
            # Include both available_balance AND trading_credit for escrow payments
            if usd_wallet:
                available_value = Decimal(str(usd_wallet.available_balance or 0))
                trading_credit_value = Decimal(str(usd_wallet.trading_credit or 0))
                current_balance = safe_add(available_value, trading_credit_value)
            else:
                current_balance = Decimal("0")
            shortage = max(safe_subtract(total_amount, current_balance), Decimal("0"))
            
            # Create branded error message
            error_header = BrandingUtils.make_header("Payment Error")
            error_footer = BrandingUtils.make_trust_footer()
            
            text = f"""{error_header}

❌ Insufficient Balance

💰 Required: {format_money(total_amount, 'USD')}
💳 Available: {format_money(current_balance, 'USD')}
📉 Shortage: {format_money(shortage, 'USD')}

Please add funds to your wallet before proceeding with this trade.

{error_footer}"""

            keyboard = [
                [InlineKeyboardButton("💎 Add Funds", callback_data="escrow_add_funds")],
                [InlineKeyboardButton("⬅️ Back to Review", callback_data="back_to_trade_review")],
                [InlineKeyboardButton("❌ Cancel Trade", callback_data="cancel_escrow")]
            ]

            await safe_edit_message_text(query, text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
            return EscrowStates.TRADE_REVIEW
        
        # Sufficient funds - process payment directly (no extra confirmation needed)
        from telegram import Update as TelegramUpdate
        mock_update = TelegramUpdate(
            update_id=0,
            callback_query=query
        )
        return await handle_wallet_payment_confirmation(mock_update, context)

async def execute_crypto_payment(update: TelegramUpdate, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Execute crypto payment for confirmed trade"""
    # Get the crypto currency from stored escrow data instead of callback
    escrow_data = context.user_data.get("escrow_data", {})  # type: ignore
    crypto_currency = escrow_data.get("crypto_currency")
    
    if not crypto_currency:
        query = update.callback_query
        if query:
            await safe_edit_message_text(query, "❌ Crypto currency not found. Please select payment method again.")
        return EscrowStates.TRADE_REVIEW
    
    # FIXED: Call crypto payment handler directly without modifying query.data
    query = update.callback_query
    if query:
        # Set a flag in context to indicate this is final confirmation
        if not context.user_data:
            context.user_data = {}
        context.user_data["is_final_confirmation"] = True
        
        # Call crypto payment handler with context flag
        result = await handle_crypto_payment_direct(update, context)
        
        # Clean up the flag
        context.user_data.pop("is_final_confirmation", None)
        return result
    
    return EscrowStates.TRADE_REVIEW

async def execute_ngn_payment(update: TelegramUpdate, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Execute NGN payment for confirmed trade"""
    return await handle_ngn_payment(update, context)

async def handle_make_payment(update: TelegramUpdate, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle make_payment callback for pending crypto payments"""
    query = update.callback_query
    if not query:
        return CONV_END
    
    # CRITICAL FIX: Clear any active conversation state AND establish edit session
    user_id = safe_get_user_id(query)
    if user_id:
        # Clear conversation state from both context and database
        if hasattr(context, 'user_data') and context.user_data:
            context.user_data.pop('conversation_state', None)
            context.user_data.pop('active_conversation', None)
        
        # Clear database conversation state
        try:
            async with async_managed_session() as session:
                stmt = select(User).where(User.telegram_id == user_id)
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()
                if user and hasattr(user, 'conversation_state'):
                    setattr(user, 'conversation_state', '')
                    await session.commit()
                    logger.info(f"🧹 Cleared conversation state for user {user_id} to enable direct handlers")
        except Exception as e:
            logger.error(f"Error clearing conversation state: {e}")
            
        # Establish edit session to prevent conflicts
        if hasattr(context, 'user_data') and context.user_data:
            from datetime import datetime
            context.user_data["active_edit_session"] = {
                "type": "escrow_payment",
                "started_at": datetime.now().isoformat(),
                "user_id": user_id
            }
            logger.info(f"✅ Established edit session for user {user_id}")
    
    # Extract escrow ID from callback data: make_payment_{escrow_id}, pay_escrow_{id}, or pay_escrow:{id}
    callback_data = query.data
    
    if callback_data.startswith("make_payment_"):  # type: ignore
        escrow_id = callback_data.replace("make_payment_", "")  # type: ignore
    elif callback_data.startswith("pay_escrow_"):  # type: ignore
        escrow_id = callback_data.replace("pay_escrow_", "")  # type: ignore
    elif callback_data.startswith("pay_escrow:"):  # type: ignore
        escrow_id = callback_data.replace("pay_escrow:", "")  # type: ignore
    else:
        await safe_answer_callback_query(query, "❌ Invalid payment request")
        return CONV_END
    
    # Immediate feedback
    await safe_answer_callback_query(query, "🔄 Processing payment...")
    
    # Get the escrow from database
    try:
        async with async_managed_session() as session:
            # Try to find escrow by database ID first (for pay_escrow_38 format)
            try:
                db_id = int(escrow_id)
                stmt = select(Escrow).where(Escrow.id == db_id)
                result = await session.execute(stmt)
                escrow = result.scalar_one_or_none()
            except ValueError:
                # If not a number, try readable escrow_id (for legacy formats)
                stmt = select(Escrow).where(Escrow.escrow_id == escrow_id)
                result = await session.execute(stmt)
                escrow = result.scalar_one_or_none()
                
            if not escrow:
                await safe_edit_message_text(query, "❌ Escrow not found. Please try again.")
                return CONV_END
            
            # Verify user is the buyer
            user_id = safe_get_user_id(query)
            if not user_id:
                await safe_edit_message_text(query, "❌ Unable to identify user.")
                return CONV_END
                
            stmt = select(User).where(User.telegram_id == user_id)
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()
            if not user or user.id != escrow.buyer_id:  # type: ignore
                await safe_edit_message_text(query, "❌ Only the buyer can make payment.")
                return CONV_END
        
            # Check escrow status
            if escrow.status not in ['payment_pending', 'created']:
                await safe_edit_message_text(query, f"❌ Payment not available for this trade status: {escrow.status}")
                return CONV_END
            
            # Set up escrow data in context for payment processing
            # FIX: Use seller_contact_display which is the UI-safe format, not seller_contact_value
            # FALLBACK: If seller_contact_display is empty but seller_id exists, fetch username from database
            seller_display = escrow.seller_contact_display if escrow.seller_contact_display is not None else None  # type: ignore[truthy-bool]
            
            if not seller_display and escrow.seller_id is not None:  # type: ignore[truthy-bool]
                # Fallback: Fetch seller username from database
                logger.warning(f"⚠️ MISSING_SELLER_CONTACT: Escrow {escrow.escrow_id} has seller_id but no seller_contact_display, fetching from database")
                seller_stmt = select(User.username, User.first_name).where(User.id == escrow.seller_id)
                seller_result = await session.execute(seller_stmt)
                seller_data = seller_result.first()
                
                if seller_data and seller_data.username is not None:  # type: ignore[truthy-bool]
                    seller_display = f"@{seller_data.username}"
                    logger.info(f"✅ FALLBACK_SUCCESS: Retrieved seller username '{seller_data.username}' for escrow {escrow.escrow_id}")
                elif seller_data and seller_data.first_name is not None:  # type: ignore[truthy-bool]
                    seller_display = str(seller_data.first_name)  # type: ignore[assignment]
                    logger.info(f"✅ FALLBACK_SUCCESS: Using seller first name '{seller_data.first_name}' for escrow {escrow.escrow_id}")
                else:
                    seller_display = "unknown"
                    logger.error(f"❌ FALLBACK_FAILED: Could not retrieve seller info for escrow {escrow.escrow_id}")
            elif not seller_display:  # type: ignore[truthy-bool]
                seller_display = "unknown"
            
            # Detect if this was a first-trade-free promotion (both fees are 0)
            buyer_fee_amount = escrow.buyer_fee_amount if escrow.buyer_fee_amount is not None else Decimal("0")
            seller_fee_amount = escrow.seller_fee_amount if escrow.seller_fee_amount is not None else Decimal("0")
            is_first_trade_free = (buyer_fee_amount == 0 and seller_fee_amount == 0 and escrow.fee_amount == 0)  # type: ignore[truthy-bool]
            
            escrow_data = {
                "escrow_id": escrow.escrow_id,
                "amount": str(escrow.amount),
                "buyer_fee": str(buyer_fee_amount),
                "seller_fee": str(seller_fee_amount),
                "payment_method": f"crypto_{escrow.currency}",
                "crypto_currency": escrow.currency,
                "buyer_id": escrow.buyer_id,
                "seller_type": escrow.seller_contact_type if escrow.seller_contact_type is not None else "username",  # type: ignore[truthy-bool]
                "seller_identifier": clean_seller_identifier(seller_display),  # type: ignore[arg-type]
                "fee_split_option": escrow.fee_split_option if escrow.fee_split_option else "buyer_pays",  # type: ignore[truthy-bool]
                "fee_breakdown": {
                    "buyer_fee_amount": decimal_to_string(buyer_fee_amount, precision=2),
                    "seller_fee_amount": decimal_to_string(seller_fee_amount, precision=2),
                    "total_platform_fee": decimal_to_string(Decimal(str(escrow.fee_amount)) if escrow.fee_amount else Decimal('0'), precision=2),
                    "is_first_trade_free": is_first_trade_free
                }
            }
            
            # Modify user_data in place (cannot assign new dict to context.user_data)
            context.user_data["escrow_data"] = escrow_data  # type: ignore[index]  # type: ignore
            
            # Show payment method selection directly (wallet/crypto/NGN options)
            # Calculate total amount from escrow data
            amount = Decimal(str(escrow_data["amount"]))
            buyer_fee = Decimal(str(escrow_data.get("buyer_fee", get_default_fee(amount))))
            total_amount = amount + buyer_fee
            
            # Get wallet balance for display
            wallet_balance = Decimal("0")
            try:
                async with async_managed_session() as session_for_wallet:
                    stmt = select(Wallet).where(Wallet.user_id == escrow_data["buyer_id"], Wallet.currency == "USD")
                    result = await session_for_wallet.execute(stmt)
                    wallet = result.scalar_one_or_none()
                    if wallet:
                        wallet_balance = wallet.available_balance
            except Exception as e:
                logger.error(f"Error getting wallet balance: {e}")

            wallet_balance_text = f"💰 Pay from Wallet: {format_money(wallet_balance, 'USD')}"

            # Show payment method selection UI
            text = f"""💸 Payment Required: {format_money(total_amount, 'USD')}

🛡️ You control release • Refund if not satisfied

Choose payment method:"""

            # Create payment keyboard with dynamic wallet button
            keyboard = _create_payment_keyboard(
                wallet_balance_text, 
                include_back=False,
                total_amount=total_amount,
                user_id=escrow_data["buyer_id"],
                wallet_balance=wallet_balance
            ).inline_keyboard

            await safe_edit_message_text(
                query, text, reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return ConversationHandler.END
        
    except Exception as e:
        logger.error(f"Error processing make_payment for {escrow_id}: {e}")
        await safe_edit_message_text(query, "❌ Payment processing error. Please try again.")
        return CONV_END

# Edit handlers that return to specific states
async def handle_edit_trade_amount(update: TelegramUpdate, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Edit trade amount from review page"""
    query = update.callback_query
    
    text = """💰 Trade Amount (Edit)

Enter new amount or choose preset:"""
    
    keyboard = [
        [
            InlineKeyboardButton("$100 USD", callback_data="amount_100"),
            InlineKeyboardButton(f"${int(Config.MEDIUM_TRADE_EXAMPLE_USD)}", callback_data="amount_500")
        ],
        [InlineKeyboardButton(f"${int(Config.LARGE_TRADE_EXAMPLE_USD)}", callback_data="amount_1000")],
        [InlineKeyboardButton("⬅️ Back to Review", callback_data="back_to_trade_review")],
        [InlineKeyboardButton("❌ Cancel Trade", callback_data="cancel_escrow")]
    ]
    
    if query:
        await safe_edit_message_text(query, text, reply_markup=InlineKeyboardMarkup(keyboard))
    
    # CRITICAL FIX: Set conversation state in database to accept amount input
    user_id = safe_get_user_id(query)
    if user_id:
        from handlers.escrow_direct import set_user_state
        await set_user_state(user_id, "amount_input")
        logger.info(f"🔧 Set user {user_id} state to amount_input for edit")
    
    return EscrowStates.AMOUNT_INPUT

async def handle_edit_trade_description(update: TelegramUpdate, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Edit trade description from review page"""
    query = update.callback_query
    
    text = """📦 Edit Trade Description

What are you buying?
Describe the item/service clearly:

💡 Type your new description below"""
    
    keyboard = [
        [InlineKeyboardButton("⬅️ Back to Review", callback_data="back_to_trade_review")],
        [InlineKeyboardButton("❌ Cancel Trade", callback_data="cancel_escrow")]
    ]
    
    if query:
        await safe_edit_message_text(query, text, reply_markup=InlineKeyboardMarkup(keyboard))
    
    # CRITICAL FIX: Set conversation state in database to accept description input
    user_id = safe_get_user_id(query)
    if user_id:
        from handlers.escrow_direct import set_user_state
        await set_user_state(user_id, "description_input")
        logger.info(f"🔧 Set user {user_id} state to description_input for edit")
    
    return EscrowStates.DESCRIPTION_INPUT

async def handle_edit_delivery_time(update: TelegramUpdate, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Edit delivery time from review page"""
    query = update.callback_query
    
    text = """⏱️ Edit Delivery Time

Choose new delivery deadline:"""
    
    keyboard = [
        [
            InlineKeyboardButton("24h", callback_data="delivery_24"),
            InlineKeyboardButton("48h", callback_data="delivery_48")
        ],
        [
            InlineKeyboardButton("72h", callback_data="delivery_72"),
            InlineKeyboardButton("Custom", callback_data="delivery_custom")
        ],
        [InlineKeyboardButton("⬅️ Back to Review", callback_data="back_to_trade_review")],
        [InlineKeyboardButton("❌ Cancel Trade", callback_data="cancel_escrow")]
    ]
    
    if query:
        await safe_edit_message_text(query, text, reply_markup=InlineKeyboardMarkup(keyboard))
    
    return EscrowStates.DELIVERY_TIME

async def handle_edit_fee_split(update: TelegramUpdate, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Edit fee split from review page"""
    query = update.callback_query
    
    # Set flag to indicate we're editing from trade review (for context-aware back button)
    if context.user_data and "escrow_data" in context.user_data:
        context.user_data["escrow_data"]["from_trade_review"] = True
    
    # Reshow fee split options
    await show_fee_split_options(query, context)
    
    return EscrowStates.FEE_SPLIT_OPTION

async def handle_back_to_trade_review_callback(update: TelegramUpdate, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Return to trade review from any edit state"""
    query = update.callback_query
    if query:
        logger.info(f"🔙 BACK_TO_REVIEW: callback_data={query.data}, user={query.from_user.id}")
        await safe_answer_callback_query(query, "⬅️ Back to review")
    
    # Show trade review page
    await show_trade_review(query, context)
    return EscrowStates.TRADE_REVIEW

async def show_trade_review_with_fees_OLD(query, context: ContextTypes.DEFAULT_TYPE) -> Optional[int]:
    """OLD FUNCTION - REPLACED BY show_trade_review above"""
    # Get user's wallet balance for display
    wallet_balance_text = "💰 Wallet Balance"
    wallet_balance = None  # Initialize to prevent unbound variable
    try:

        async with async_managed_session() as session:
            user_id = safe_get_user_id(query)
            if user_id:
                user_stmt = select(User).where(User.telegram_id == user_id)
                user_result = await session.execute(user_stmt)
                user = user_result.scalar_one_or_none()
                if user and hasattr(user, "id"):
                    wallet_balance = Decimal("0")
                    wallets_stmt = select(Wallet).where(
                        Wallet.user_id == user.id,
                        Wallet.currency.in_(
                            ["USD", "USDT", "USDC", "USDT-TRC20", "USDT-ERC20"]
                        )
                    )
                    wallets_result = await session.execute(wallets_stmt)
                    usd_wallets = wallets_result.scalars().all()

                    for wallet in usd_wallets:
                        try:
                            # Safely get balance value from SQLAlchemy instance
                            balance_val = as_decimal(getattr(wallet, "balance", None))
                            if balance_val > 0:
                                wallet_balance += balance_val
                        except (ValueError, TypeError, AttributeError):
                            continue

                if wallet_balance is not None:
                    wallet_balance_text = f"💰 Wallet: {format_money(Decimal(str(wallet_balance)), 'USD')}"
                else:
                    wallet_balance_text = "💰 Wallet: $0.00 USD"
    except Exception as e:
        logger.error(f"Error getting wallet balance: {e}")

    # Use centralized payment keyboard function
    # This is an OLD function - wallet button hiding not implemented here
    payment_keyboard = _create_payment_keyboard(
        f"{wallet_balance_text}", 
        include_back=True, 
        back_callback="back_to_fee_options"
    )

    # Define the text for trade review display
    text = "💰 Payment Options\n\nChoose your payment method:"
    
    await safe_edit_message_text(query, text, reply_markup=payment_keyboard)

async def show_trade_review_OLD_DUPLICATE(query_or_update, context: ContextTypes.DEFAULT_TYPE) -> Optional[int]:
    """OLD DUPLICATE FUNCTION - DO NOT USE - Show trade review and payment options"""
    if not context.user_data or "escrow_data" not in context.user_data:
        # Enhanced error recovery with navigation
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🔄 Start New Trade", callback_data="secure_trade"
                    )
                ],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")],
            ]
        )

        if hasattr(query_or_update, "edit_message_text"):
            await query_or_update.edit_message_text(
                "⏰ Session Expired\n\nInactive too long.\n\n⚡ Tap /start to create a new trade.",
                reply_markup=keyboard,
            )
        elif hasattr(query_or_update, "message"):
            await query_or_update.message.reply_text(
                "⏰ Session Expired\n\nInactive too long.\n\n⚡ Tap /start to create a new trade.",
                reply_markup=keyboard,
            )
        return CONV_END

    escrow_data = context.user_data["escrow_data"]

    # FIXED: Use buyer_fee from fee split calculation instead of hardcoded platform fee
    amount = Decimal(str(escrow_data["amount"]))
    buyer_fee = Decimal(str(escrow_data.get("buyer_fee", get_default_fee(amount))))
    total_amount = amount + buyer_fee

    # Fix: Use HTML escaping since message uses HTML format
    import html
    seller_identifier_clean = escrow_data['seller_identifier'].replace('\\', '')
    seller_display = (
        format_username_html(f"@{seller_identifier_clean}", include_link=False)
        if escrow_data["seller_type"] == "username"
        else html.escape(seller_identifier_clean)  # Keep for non-username types
    )

    # Get user's wallet balance for display
    wallet_balance_text = "💰 Wallet Balance"
    try:
        # Determine if query or update to get user info
        user_id = None
        if hasattr(query_or_update, "from_user"):
            user_id = query_or_update.from_user.id
        elif hasattr(query_or_update, "message") and hasattr(
            query_or_update.message, "from_user"
        ):
            user_id = query_or_update.message.from_user.id

        if user_id:

            async with async_managed_session() as session:
                user_stmt = select(User).where(User.telegram_id == user_id)
                user_result = await session.execute(user_stmt)
                user = user_result.scalar_one_or_none()
                if user:
                    from models import Wallet

                    wallets_stmt = select(Wallet).where(Wallet.user_id == user.id)
                    wallets_result = await session.execute(wallets_stmt)
                    wallets = wallets_result.scalars().all()

                    # Calculate total USD value
                    from services.crypto import CryptoServiceAtomic

                    crypto_service = CryptoServiceAtomic()
                    rates = await crypto_service.get_crypto_rates()

                    total_usd = Decimal("0")
                    for wallet in wallets:
                        try:
                            # Safely get values from SQLAlchemy instance
                            balance_val = as_decimal(getattr(wallet, "balance", None))
                            currency_str = str(getattr(wallet, "currency", "USD"))

                            if balance_val > 0:
                                rate = as_decimal(rates.get(currency_str, 1.0))
                                wallet_usd = balance_val * rate
                                total_usd += wallet_usd
                        except (ValueError, TypeError, AttributeError):
                            continue

                    total_usd = total_usd.quantize(Decimal("0.01"))
                    wallet_balance_text = f"💰 Wallet Balance ({format_money(total_usd, 'USD')})"
    except Exception as e:
        logger.error(f"Error getting wallet balance for display: {e}")

    # Get NGN conversion for display - OPTIMIZED: Single API call instead of duplicate
    # FIXED: Move Config import to top level to avoid local variable issues
    backup_rate = getattr(Config, "LAST_KNOWN_USD_NGN_RATE", 1500.0)
    
    try:
        from services.financial_gateway import financial_gateway
        # OPTIMIZATION: Use single rate call instead of redundant FastForex + financial_gateway calls
        dynamic_rate = await financial_gateway.get_usd_to_ngn_rate_clean()
        if dynamic_rate:
            ngn_amount = safe_multiply(Decimal(str(total_amount)), dynamic_rate, precision=2)
            # Use clean rate for display (without adding markup confusion)
            ngn_display = f"\n🇳🇬 NGN Equivalent: ₦{ngn_amount:,.2f} @ ₦{dynamic_rate:.0f}/USD"
            logger.info(f"🚀 Escrow payment: Eliminated duplicate FastForex call - using single rate fetch")
        else:
            raise Exception("Exchange rate unavailable")
    except Exception as e:
        logger.error(f"Failed to get dynamic NGN rate: {e}")
        # Use backup rate already retrieved from Config at function start
        ngn_amount = safe_multiply(Decimal(str(total_amount)), Decimal(str(backup_rate)), precision=2)
        ngn_display = f"\n🇳🇬 NGN Equivalent: ₦{ngn_amount:,.2f} @ ₦{backup_rate:.0f}/USD (fallback)"

    text = f"""💰 Secure Payment

{seller_display} • {format_money(total_amount, 'USD')}{ngn_display}

🛡️ You control release • Refund if not satisfied

Choose payment method:"""

    # Use centralized payment keyboard function
    # This is an OLD function - wallet button hiding not implemented here
    keyboard = _create_payment_keyboard(
        wallet_balance_text, 
        include_back=False
    ).inline_keyboard

    if hasattr(query_or_update, "edit_message_text"):
        await query_or_update.edit_message_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await query_or_update.message.reply_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard)
        )

async def handle_payment_method_selection(
    update: TelegramUpdate, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle payment method selection with Exchange-style callback architecture"""
    query = update.callback_query
    
    if not query or not query.data:
        return EscrowStates.PAYMENT_METHOD
    
    # CRITICAL: Exclude wallet funding callbacks from escrow handler
    wallet_funding_callbacks = ["crypto_funding_start", "fincra_start_payment", "deposit_currency:", "show_deposit_qr"]
    if any(query.data.startswith(pattern.replace(":", "")) or query.data == pattern for pattern in wallet_funding_callbacks):
        logger.info(f"⚠️ ESCROW_HANDLER: Skipping wallet funding callback {query.data}")
        # This should be handled by wallet handlers, not escrow
        # Return CONV_END to let other handlers process it
        return ConversationHandler.END
    
    # CRITICAL DEBUG: Log callback data
    logger.info(f"💳 PAYMENT_HANDLER: callback_data={query.data}, user={query.from_user.id}")
    logger.info(f"💳 PAYMENT_HANDLER: context.user_data={bool(context.user_data)}, has_escrow_data={('escrow_data' in context.user_data) if context.user_data else False}")
    
    # CRITICAL FIX: Immediate acknowledgment like Exchange
    if query.data.startswith("crypto_"):
        crypto = query.data.replace("crypto_", "")
        await safe_answer_callback_query(query, f"✅ {crypto} selected")
    elif query.data.startswith("payment_"):
        await safe_answer_callback_query(query, "🛡️ Payment method")
    else:
        await safe_answer_callback_query(query, "🛡️ Escrow action")

    if query and query.data:
        logger.info(f"Payment method selection: {query.data}")

    # Check if context exists
    if not context.user_data or "escrow_data" not in context.user_data:
        logger.error(f"❌ PAYMENT_HANDLER: Session expired for user {query.from_user.id} - escrow_data missing")
        await safe_edit_message_text(query, "⏰ Session Expired\n\nInactive too long.\n\n⚡ Tap /start to continue.")  # type: ignore
        return CONV_END

    escrow_data = context.user_data["escrow_data"]
    # FIXED: Use buyer_fee from fee split calculation instead of hardcoded 5%
    amount = Decimal(str(escrow_data["amount"]))
    buyer_fee = Decimal(str(escrow_data.get("buyer_fee", get_default_fee(amount))))
    total_amount = amount + buyer_fee

    if query and query.data == "payment_wallet":
        # VALIDATION: Re-check wallet balance before allowing wallet payment
        try:
            async with async_managed_session() as session:
                user_stmt = select(User).where(User.telegram_id == query.from_user.id)
                user_result = await session.execute(user_stmt)
                user = user_result.scalar_one_or_none()
                
                if user:
                    wallet_stmt = select(Wallet).where(
                        Wallet.user_id == user.id,
                        Wallet.currency == "USD"
                    )
                    wallet_result = await session.execute(wallet_stmt)
                    usd_wallet = wallet_result.scalar_one_or_none()
                    
                    current_balance = Decimal("0")
                    if usd_wallet:
                        # Include both available_balance AND trading_credit for escrow payments
                        available_value = Decimal(str(usd_wallet.available_balance or 0))
                        trading_credit_value = Decimal(str(usd_wallet.trading_credit or 0))
                        current_balance = safe_add(available_value, trading_credit_value)
                    
                    # Check if balance is sufficient
                    if current_balance < total_amount:
                        # Balance insufficient - redirect to add funds
                        shortfall = safe_subtract(total_amount, current_balance)
                        await safe_edit_message_text(
                            query,
                            f"""❌ Insufficient Wallet Balance

Current Balance: {format_money(current_balance, 'USD')}
Amount Needed: {format_money(total_amount, 'USD')}
Shortfall: {format_money(shortfall, 'USD')}

Please add funds to your wallet to proceed.""",
                            reply_markup=InlineKeyboardMarkup([
                                [InlineKeyboardButton("➕ Add Funds to Wallet", callback_data="escrow_add_funds")],
                                [InlineKeyboardButton("⬅️ Choose Other Payment", callback_data="back_to_trade_review")]
                            ])
                        )
                        return EscrowStates.PAYMENT_METHOD
        except Exception as e:
            logger.error(f"Error validating wallet balance: {e}")
        
        # Store payment method selection and return to trade review
        escrow_data["payment_method"] = "wallet"
        context.user_data["escrow_data"] = escrow_data  # type: ignore[index]
        await show_trade_review(query, context)
        return EscrowStates.TRADE_REVIEW
    elif query and query.data == "wallet_insufficient":
        # User clicked on insufficient wallet button - show Add Funds screen
        await safe_answer_callback_query(query, "⚠️ Wallet balance too low")
        
        # Get current wallet balance to show user
        try:
            async with async_managed_session() as session:
                user_stmt = select(User).where(User.telegram_id == query.from_user.id)
                user_result = await session.execute(user_stmt)
                user = user_result.scalar_one_or_none()
                
                current_balance = Decimal("0")
                if user:
                    wallet_stmt = select(Wallet).where(
                        Wallet.user_id == user.id,
                        Wallet.currency == "USD"
                    )
                    wallet_result = await session.execute(wallet_stmt)
                    usd_wallet = wallet_result.scalar_one_or_none()
                    
                    if usd_wallet:
                        # Include both available_balance AND trading_credit for escrow payments
                        available_value = Decimal(str(usd_wallet.available_balance or 0))
                        trading_credit_value = Decimal(str(usd_wallet.trading_credit or 0))
                        current_balance = safe_add(available_value, trading_credit_value)
                
                # Show helpful message with Add Funds option
                shortfall = safe_subtract(total_amount, current_balance)
                await safe_edit_message_text(
                    query,
                    f"""❌ Insufficient Wallet Balance

Current Balance: {format_money(current_balance, 'USD')}
Amount Needed: {format_money(total_amount, 'USD')}
Shortfall: {format_money(shortfall, 'USD')}

Please add funds to your wallet to proceed.""",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("➕ Add Funds to Wallet", callback_data="escrow_add_funds")],
                        [InlineKeyboardButton("⬅️ Choose Other Payment", callback_data="back_to_trade_review")]
                    ])
                )
        except Exception as e:
            logger.error(f"Error showing wallet insufficient screen: {e}")
            # Fallback to simple message if DB error
            await safe_edit_message_text(
                query,
                f"""❌ Insufficient Wallet Balance

Amount Needed: {format_money(total_amount, 'USD')}

Please add funds to your wallet to proceed.""",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("➕ Add Funds to Wallet", callback_data="escrow_add_funds")],
                    [InlineKeyboardButton("⬅️ Choose Other Payment", callback_data="back_to_trade_review")]
                ])
            )
        
        return EscrowStates.PAYMENT_METHOD
    elif query and query.data == "confirm_wallet_payment":
        return await handle_wallet_payment_confirmation(update, context)
    elif query and query.data and query.data.startswith("crypto_"):
        # Store crypto payment method selection and return to trade review
        crypto = query.data.replace("crypto_", "")
        escrow_data["payment_method"] = f"crypto_{crypto}"
        escrow_data["crypto_currency"] = crypto.upper()
        context.user_data["escrow_data"] = escrow_data  # type: ignore[index]
        
        # CRITICAL FIX: Ensure we return to trade review for user confirmation
        # This prevents auto-confirmation bug where escrow was created immediately
        await show_trade_review(query, context)
        
        # IMPORTANT: Return TRADE_REVIEW state to wait for user confirmation
        # Do NOT proceed to payment processing until user clicks "Confirm Trade"
        return EscrowStates.TRADE_REVIEW
    elif query and query.data == "payment_ngn":
        # Store NGN payment method selection and return to trade review
        escrow_data["payment_method"] = "ngn_bank"
        context.user_data["escrow_data"] = escrow_data  # type: ignore[index]
        await show_trade_review(query, context)
        return EscrowStates.TRADE_REVIEW
    elif query and query.data == "payment_help":
        await safe_edit_message_text(
            query,  # type: ignore
            """❓ Payment Help

💰 Wallet Balance: Use your {Config.PLATFORM_NAME} wallet funds
₿ Crypto: Pay with Bitcoin, Ethereum, USDT, etc.
🇳🇬 Bank Transfer: Pay with ₦ NGN via Nigerian bank transfer

Funds held in escrow. You control release to seller.""",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Back", callback_data="back_to_payment")]]
            ),
        )
        return EscrowStates.PAYMENT_METHOD
    elif query and query.data == "back_to_fee_options":
        # Navigate back to fee split options
        await show_fee_split_options(query, context)
        return EscrowStates.FEE_SPLIT_OPTION
    elif query and query.data == "back_to_trade_review":
        # Return to trade review from payment method selection  
        await show_trade_review(query, context)
        return EscrowStates.TRADE_REVIEW
    elif query and query.data == "cancel_escrow":
        await safe_edit_message_text(
            query,
            "❌ Trade creation cancelled.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🏠 Main Menu", callback_data="back_to_main")]]
            ),
        )
        if context.user_data:
            context.user_data.pop("escrow_data", None)
        return CONV_END

    # CRITICAL FIX: Do not auto-advance to PAYMENT_PROCESSING
    # This was causing auto-confirmation bug
    return EscrowStates.PAYMENT_METHOD

async def handle_escrow_crypto_selection(
    update: TelegramUpdate, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle crypto selection with Exchange-style architecture - no complex state dependencies"""
    query = update.callback_query
    
    if not query or not query.data:
        return EscrowStates.PAYMENT_METHOD
    
    if not context.user_data or "escrow_data" not in context.user_data:
        await safe_edit_message_text(query, "⏰ Session Expired\n\nInactive too long.\n\n⚡ Tap /start to continue.")
        return CONV_END
    
    # Extract crypto currency from callback
    crypto = query.data.replace("crypto_", "")
    escrow_data = context.user_data["escrow_data"]
    
    # CRITICAL FIX: Process crypto payment directly without state transitions
    try:
        await handle_crypto_payment_direct(update, context)
        # Return to PAYMENT_PROCESSING state for consistent handling
        return EscrowStates.PAYMENT_PROCESSING
    except Exception as e:
        logger.error(f"Error in crypto selection: {e}")
        await safe_edit_message_text(
            query,
            f"❌ Error setting up {crypto} payment. Please try again.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Try Again", callback_data="back_to_payment")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
            ])
        )
        return EscrowStates.PAYMENT_METHOD

async def show_crypto_selection(
    query, context: ContextTypes.DEFAULT_TYPE, total_amount
) -> int:
    """Show cryptocurrency selection for payment"""
    total_amount_decimal = Decimal(str(total_amount)) if not isinstance(total_amount, Decimal) else total_amount
    text = f"""₿ Crypto Payment

Amount: {format_money(total_amount_decimal, 'USD')}

Select cryptocurrency:"""

    # Use simplified crypto keyboard for this context
    keyboard = [
        [
            InlineKeyboardButton("₿ Bitcoin", callback_data="crypto_BTC"),
            InlineKeyboardButton("Ξ Ethereum", callback_data="crypto_ETH"),
            InlineKeyboardButton("₮ USDT", callback_data="crypto_USDT"),
        ],
        [
            InlineKeyboardButton("Ł Litecoin", callback_data="crypto_LTC"),
            InlineKeyboardButton("⬅️ Back", callback_data="back_to_payment"),
            InlineKeyboardButton("❌ Cancel Trade", callback_data="cancel_escrow"),
        ],
    ]

    await safe_edit_message_text(
        query, text, reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return EscrowStates.SELECTING_CRYPTO

async def handle_crypto_payment_direct(
    update: TelegramUpdate, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle direct cryptocurrency payment from review page"""
    from decimal import Decimal

    
    query = update.callback_query
    if not query or not query.data:
        return CONV_END

    # Get crypto from escrow_data if this was called from confirm_trade_final  # type: ignore
    escrow_data = context.user_data.get("escrow_data", {})  # type: ignore
    
    # CRITICAL FIX: Only proceed with escrow creation if this is from confirm_trade_final
    # Crypto selection from payment menu should ONLY save the choice and return to review  # type: ignore
    is_final_confirmation = context.user_data.get("is_final_confirmation", False) or query.data == "confirm_trade_final"  # type: ignore
    if is_final_confirmation:
        # This is the final confirmation - proceed with escrow creation
        crypto_currency = escrow_data.get("crypto_currency")
        if not crypto_currency:
            await safe_edit_message_text(query, "❌ Crypto currency not selected. Please select payment method.")
            return EscrowStates.TRADE_REVIEW
        crypto = crypto_currency
        
        # CRITICAL FIX: Immediate acknowledgment like Exchange
        await safe_answer_callback_query(query, f"💰 Setting up {crypto} payment")
    elif query.data.startswith("crypto_"):
        # FIXED: This is just payment method selection - save choice and return to review
        crypto = query.data.replace("crypto_", "")
        
        # Save the crypto choice to escrow_data
        escrow_data["payment_method"] = f"crypto_{crypto}"
        escrow_data["crypto_currency"] = crypto  # type: ignore
        context.user_data["escrow_data"] = escrow_data  # type: ignore[index]  # type: ignore
        
        # Acknowledge selection and return to trade review for confirmation
        await safe_answer_callback_query(query, f"✅ {crypto} selected")
        
        # Import here to avoid circular imports
        from handlers.escrow import show_trade_review
        await show_trade_review(query, context)
        return EscrowStates.TRADE_REVIEW
    else:
        await safe_edit_message_text(query, "❌ Invalid payment method selection.")
        return EscrowStates.TRADE_REVIEW

    if not context.user_data or "escrow_data" not in context.user_data:
        await safe_edit_message_text(query, "⏰ Session Expired\n\nInactive too long.\n\n⚡ Tap /start to continue.")  # type: ignore
        return CONV_END

    escrow_data = context.user_data["escrow_data"]
    # FIXED: Use buyer_fee from fee split calculation instead of hardcoded 5%
    amount = Decimal(str(escrow_data["amount"]))
    buyer_fee = Decimal(str(escrow_data.get("buyer_fee", get_default_fee(amount))))
    total_amount = amount + buyer_fee

    # Prevent duplicate processing
    if escrow_data.get("payment_currency") == crypto:
        return EscrowStates.PAYMENT_PROCESSING

    # Get crypto rates and calculate amount needed
    try:
        crypto_service = CryptoServiceAtomic()
        rates = await crypto_service.get_crypto_rates()

        if crypto not in rates:
            await safe_edit_message_text(
                query,  # type: ignore
                f"❌ {crypto} rate not available. Please try another currency.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("⬅️ Back", callback_data="back_to_payment")]]
                ),
            )
            return CONV_END

        crypto_rate = Decimal(str(rates[crypto]))  # Convert to Decimal for precision
        
        # SECURITY FIX: Check for currency switch and recalculate from USD value
        if escrow_data.get("payment_currency") and escrow_data.get("payment_currency") != crypto:
            from utils.currency_validation import CurrencyValidator, ConversionAuditLogger
            
            logger.info(f"ESCROW CRYPTO SWITCH DETECTED: {escrow_data.get('payment_currency')} -> {crypto}")
            # Use stored USD value to calculate new crypto amount
            stored_usd_value = Decimal(str(escrow_data.get("usd_value", total_amount)))
            old_crypto = escrow_data.get("payment_currency")
            old_rate = Decimal(str(escrow_data.get("original_crypto_rate", 1)))
            old_amount = stored_usd_value / old_rate if old_rate > 0 else Decimal("0")
            
            # Calculate new amount using validation framework
            try:
                crypto_amount, validation_info = CurrencyValidator.calculate_equivalent_amount(
                    source_amount=old_amount,
                    source_rate=old_rate,
                    target_rate=crypto_rate,
                    source_currency=old_crypto,
                    target_currency=crypto,
                    context="escrow_payment_switch"
                )
            except Exception as validation_error:
                from utils.context_security import ErrorRecoveryManager
                logger.error(f"Crypto switch validation failed: {validation_error}")
                
                # Handle error and provide recovery
                user_id = update.effective_user.id if update.effective_user else 0
                recovery_result = ErrorRecoveryManager.handle_conversion_error(
                    error=validation_error,
                    context_data=escrow_data,
                    operation="escrow_crypto_switch",
                    user_id=user_id
                )
                
                await safe_edit_message_text(
                    query, recovery_result["user_message"]
                )
                return CONV_END
            
            # Log the switch for audit
            user_id = update.effective_user.id if update.effective_user else 0
            ConversionAuditLogger.log_crypto_switch(
                user_id=user_id,
                old_crypto=old_crypto,
                new_crypto=crypto,
                old_amount=old_amount,
                new_amount=crypto_amount,
                usd_value=stored_usd_value,
                context="escrow_payment"
            )
            
            logger.info(f"USD value preserved: ${Decimal(str(stored_usd_value)):.2f} -> {Decimal(str(crypto_amount)):.8f} {crypto}")
        else:
            # First time selection - calculate normally
            crypto_amount = total_amount / crypto_rate
            escrow_data["usd_value"] = Decimal(str(total_amount))
        
        # Import Config to avoid scoping issues
        from config import Config as AppConfig
        
        # FIXED: Use configurable exchange markup instead of hardcoded 2%
        markup_percentage = Decimal(str(AppConfig.EXCHANGE_MARKUP_PERCENTAGE)) / Decimal("100")
        markup_rate = crypto_amount * (Decimal("1") + markup_percentage)
        
        # Apply $2 crypto fee padding for non-USDT currencies (display crypto amount includes padding)
        USDT_CURRENCIES = {"USDT", "USDT_TRC20", "USDT_ERC20", "USDT_BEP20"}
        if crypto.upper() not in USDT_CURRENCIES:
            padding_crypto = Decimal("2") / crypto_rate  # Convert $2 to crypto equivalent
            markup_rate = markup_rate + padding_crypto
        
        # Update payment currency tracking
        escrow_data["payment_currency"] = crypto
        escrow_data["original_crypto_rate"] = Decimal(str(crypto_rate))

        # Generate deposit address using payment processor manager
        from services.payment_processor_manager import payment_manager

        # Enhanced: Create escrow record with async context manager and idempotency checks

        async with async_managed_session() as session:
            if query and query.from_user:
                user_stmt = select(User).where(User.telegram_id == query.from_user.id)
                user_result = await session.execute(user_stmt)
                user = user_result.scalar_one_or_none()
            else:
                user = None
                
            if not user:
                await safe_edit_message_text(
                    query,
                    "❌ User account not found. Please contact support.",
                    reply_markup=InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    "⬅️ Back", callback_data="back_to_payment"
                                )
                            ]
                        ]
                    ),
                )
                return CONV_END

            # CRITICAL FIX: Use existing escrow ID if available (from pay_escrow handler)
            # Only generate new ID for new trades
            existing_escrow_id = escrow_data.get("existing_escrow_id") or escrow_data.get("early_escrow_id")  # Check if pay_escrow handler stored existing ID
            
            if existing_escrow_id:
                # User clicked "Pay Now" from existing trade - reuse that ID
                escrow_id = existing_escrow_id
                logger.info(f"♻️ REUSING_EXISTING_ID: {escrow_id} (from existing payment_pending trade)")
            else:
                # New trade creation - generate fresh ID
                from utils.universal_id_generator import UniversalIDGenerator
                escrow_id = UniversalIDGenerator.generate_escrow_id()
                logger.info(f"🆔 NEW_ID_GENERATED: {escrow_id} (fresh ID for new trade)")
            
            escrow_data["unified_escrow_id"] = escrow_id
            
            # CRITICAL FIX: Check if escrow already exists - if so, USE IT instead of creating new one
            escrow_stmt = select(Escrow).where(Escrow.escrow_id == escrow_id)
            escrow_result = await session.execute(escrow_stmt)
            existing_escrow = escrow_result.scalar_one_or_none()
            
            if existing_escrow:
                logger.info(f"✅ EXISTING_ESCROW_FOUND: Using existing escrow {escrow_id} instead of creating new one")
                
                # CRITICAL: Check if crypto currency matches before reusing payment address
                existing_crypto = existing_escrow.currency
                existing_payment_address = existing_escrow.deposit_address
                
                # ALWAYS generate new payment address (never reuse old ones)
                logger.info(f"🔄 ALWAYS_NEW_ADDRESS: Generating fresh payment address for {crypto} on escrow {escrow_id}")
                # Always generate new address and update existing escrow
            else:
                logger.info(f"🆕 NEW_ESCROW: No existing escrow found for {escrow_id}, will create new one")
            
            # Determine if we need to create new escrow or update existing one
            should_create_new_escrow = not existing_escrow
            should_update_existing_crypto = existing_escrow  # Always update existing escrow with new payment address
            
            if should_create_new_escrow:
                logger.info(f"🆕 CREATING_NEW_ESCROW: Will create new escrow {escrow_id}")
            elif should_update_existing_crypto:
                logger.info(f"🔄 UPDATING_ESCROW_ADDRESS: Will update escrow {escrow_id} with fresh {crypto} payment address")
            
            # NO SEPARATE UTID GENERATION - use the same unified ID
            if should_create_new_escrow:
                escrow_utid = escrow_id  # Same ID for consistency - no separate generation

            # AMOUNT AND FEE CALCULATION (CRITICAL PRECISION): Use pre-calculated fees from UI to preserve first-trade-free
    
            from utils.fee_calculator import FeeCalculator
            
            escrow_amount = escrow_data["amount"]  # Already Decimal from amount input
            fee_split_option = escrow_data.get("fee_split_option", "buyer_pays")
            
            # CRITICAL FIX: Use pre-calculated fee_breakdown from UI to preserve first-trade-free promotion
            # If fee_breakdown exists in escrow_data, use it (includes first-trade-free status)
            # Otherwise, calculate fresh fees (fallback for edge cases)
            if "fee_breakdown" in escrow_data and escrow_data["fee_breakdown"]:
                fee_breakdown = escrow_data["fee_breakdown"]
                is_first_trade_free = fee_breakdown.get("is_first_trade_free", False)
                logger.info(f"💰 ESCROW_CREATION: Using pre-calculated fees from UI (first_trade_free={is_first_trade_free})")
                
                # BACKWARD COMPATIBILITY FIX: Normalize legacy fee_breakdown structure
                # Legacy escrows may only have 'total_payment' instead of 'buyer_total_payment'
                if 'buyer_total_payment' not in fee_breakdown:
                    logger.warning(f"🔧 LEGACY_FEE_STRUCTURE: Normalizing fee_breakdown for escrow {escrow_id}")
                    
                    # Fallback to legacy 'total_payment' key if available
                    if 'total_payment' in fee_breakdown:
                        fee_breakdown['buyer_total_payment'] = fee_breakdown['total_payment']
                        logger.info(f"✅ NORMALIZED: Copied 'total_payment' → 'buyer_total_payment': {fee_breakdown['buyer_total_payment']}")
                    else:
                        # Last resort: Recalculate entire fee breakdown
                        logger.warning(f"⚠️ RECALCULATING: Missing both 'buyer_total_payment' and 'total_payment', recalculating fees")
                        fee_breakdown = FeeCalculator.calculate_escrow_breakdown(
                            escrow_amount=escrow_amount,
                            fee_split_option=fee_split_option
                        )
            else:
                # Fallback: Calculate fresh fees (shouldn't normally happen)
                logger.warning(f"⚠️ ESCROW_CREATION: No fee_breakdown in escrow_data, recalculating fees")
                fee_breakdown = FeeCalculator.calculate_escrow_breakdown(
                    escrow_amount=escrow_amount,
                    fee_split_option=fee_split_option
                )
            
            # Extract precise amounts from fee breakdown
            buyer_fee_amount = Decimal(str(fee_breakdown['buyer_fee_amount']))
            seller_fee_amount = Decimal(str(fee_breakdown['seller_fee_amount']))
            total_platform_fee = Decimal(str(fee_breakdown['total_platform_fee']))
            buyer_total_payment = Decimal(str(fee_breakdown['buyer_total_payment']))
            
            # CRITICAL FIX: Use the correct base amount from fee_breakdown, not escrow_data["amount"]
            # escrow_data["amount"] may be corrupted by crypto quotes, but fee_breakdown has the original value
            escrow_amount = Decimal(str(fee_breakdown['escrow_amount']))
            
            logger.info(
                f"Escrow {escrow_id} fee calculation: Base=${escrow_amount}, "
                f"Buyer fee=${buyer_fee_amount}, Seller fee=${seller_fee_amount}, "
                f"Buyer total=${buyer_total_payment}, Fee split={fee_split_option}"
            )
            
            # DELIVERY TIMING: Store delivery_hours for later use when payment is confirmed
            # Delivery deadline will be set AFTER payment confirmation, not at creation
            delivery_hours = escrow_data.get("delivery_hours", 72)  # Default 72 hours if not specified
            from datetime import timezone
            current_time = datetime.now(timezone.utc)
            
            # TIMING: Separate timeouts for seller response vs payment 
            # When no seller assigned: Use seller response timeout (15 min)
            # When seller accepts: Use payment timeout (24 hours)
            seller_response_expiry = current_time + timedelta(minutes=Config.SELLER_RESPONSE_TIMEOUT_MINUTES)
            payment_expiry = current_time + timedelta(minutes=Config.PAYMENT_TIMEOUT_MINUTES)
            
            # ===================================================================
            # CRYPTO UPDATE PATH: Handle existing escrow crypto changes
            # ===================================================================
            
            if should_update_existing_crypto:
                # CRITICAL FIX: Get current values with explicit async query to prevent lazy loading
                escrow_values_stmt = select(
                    Escrow.currency, 
                    Escrow.amount, 
                    Escrow.fee_amount, 
                    Escrow.deposit_address
                ).where(Escrow.escrow_id == escrow_id)
                escrow_values_result = await session.execute(escrow_values_stmt)
                escrow_values = escrow_values_result.first()
                
                if not escrow_values:
                    logger.error(f"❌ CRYPTO_UPDATE_FAILED: Could not fetch escrow values for {escrow_id}")
                    raise Exception("Escrow values not found")
                
                current_currency = escrow_values.currency
                current_amount = escrow_values.amount
                current_fee_amount = escrow_values.fee_amount
                
                logger.info(f"🔄 CRYPTO_UPDATE_PATH: Updating existing escrow {escrow_id} from {current_currency} to {crypto}")
                
                # CRITICAL FIX: Get UTID from existing escrow for consistent payment provider calls
                existing_escrow_stmt = select(Escrow.utid, Escrow.escrow_id).where(Escrow.escrow_id == escrow_id)
                existing_escrow_result = await session.execute(existing_escrow_stmt)
                existing_escrow_record = existing_escrow_result.first()
                
                if existing_escrow_record:
                    # Use UTID if available, otherwise fall back to escrow_id (for older records)
                    escrow_branding_id = existing_escrow_record.utid or existing_escrow_record.escrow_id
                    logger.info(f"🔖 Using branding ID: {escrow_branding_id} (UTID: {existing_escrow_record.utid})")
                else:
                    logger.error(f"❌ Could not find existing escrow {escrow_id} for branding ID")
                    escrow_branding_id = escrow_id  # Fallback to escrow_id
                
                # Create payment address for new crypto
                from services.payment_processor_manager import PaymentProcessorManager
                payment_manager = PaymentProcessorManager()
                
                try:
                    # Import Config at function level to avoid scoping issues
                    from config import Config as AppConfig
                    
                    # CRITICAL FIX: Use USD amount for payment processor, not crypto amount
                    usd_amount = Decimal(str(current_amount or 0)) + Decimal(str(current_fee_amount or 0))
                    
                    # Apply DynoPay minimum amount requirement (same pattern as crypto.py)
                    payment_amount = max(usd_amount, 1.0)  # DynoPay minimum amount requirement
                    
                    # Normalize webhook URL to prevent double /webhook/ paths
                    base_url = normalize_webhook_base_url(AppConfig.WEBHOOK_URL)
                    provider = payment_manager.primary_provider.value
                    callback_url = f"{base_url}/dynopay/escrow" if provider == 'dynopay' else f"{base_url}/blockbee/callback/{escrow_branding_id}"
                    
                    address_data, provider_used = await payment_manager.create_payment_address(
                        currency=crypto,
                        amount=payment_amount,
                        callback_url=callback_url,
                        reference_id=escrow_branding_id,  # CRITICAL FIX: Use utid (user-facing Trade ID) as reference_id
                        metadata={'escrow_id': escrow_id, 'utid': escrow_branding_id, 'amount_usd': decimal_to_string(usd_amount, precision=2)}  # Include both IDs for compatibility
                    )
                    logger.info(f"✅ Using payment provider ({provider_used}) for escrow {escrow_id}")
                    
                    # CRITICAL FIX: Use async session update instead of direct object modification
                    new_deposit_address = address_data.get('address')
                    new_payment_provider = provider_used.value
                    
                    # Update escrow with async session.execute instead of direct object access
                    update_stmt = sqlalchemy_update(Escrow).where(
                        Escrow.escrow_id == escrow_id
                    ).values(
                        currency=crypto,
                        deposit_address=new_deposit_address
                    )
                    await session.execute(update_stmt)
                    
                    # UPSERT: Update existing payment address or create new one
                    from models import PaymentAddress
                    
                    # Get escrow database ID for foreign key
                    escrow_db_stmt = select(Escrow.id, Escrow.buyer_id).where(Escrow.escrow_id == escrow_id)
                    escrow_db_result = await session.execute(escrow_db_stmt)
                    escrow_db_data = escrow_db_result.first()
                    
                    if escrow_db_data:
                        # Check if payment address already exists for this UTID
                        existing_addr_stmt = select(PaymentAddress).where(PaymentAddress.utid == escrow_branding_id)
                        existing_addr_result = await session.execute(existing_addr_stmt)
                        existing_payment_address = existing_addr_result.scalar_one_or_none()
                        
                        if existing_payment_address:
                            # UPDATE existing record (user changed crypto currency)
                            existing_payment_address.address = new_deposit_address  # type: ignore[assignment]
                            existing_payment_address.currency = crypto  # type: ignore[assignment]
                            existing_payment_address.provider = provider_used.value  # type: ignore[assignment]
                            existing_payment_address.provider_data = address_data  # type: ignore[assignment]
                            existing_payment_address.is_used = False  # type: ignore[assignment]
                            logger.info(f"✅ PAYMENT_ADDRESS_UPDATED: Updated payment address for escrow {escrow_id} to {crypto}, address {new_deposit_address}")
                        else:
                            # INSERT new record (first time setting payment address)
                            payment_address_record = PaymentAddress(
                                utid=escrow_branding_id,
                                address=new_deposit_address,
                                currency=crypto,
                                provider=provider_used.value,
                                user_id=escrow_db_data.buyer_id,
                                escrow_id=escrow_db_data.id,
                                is_used=False,
                                provider_data=address_data
                            )
                            session.add(payment_address_record)
                            logger.info(f"✅ PAYMENT_ADDRESS_CREATED: Created payment_addresses record for escrow {escrow_id}, address {new_deposit_address}")
                    
                    await session.commit()
                    
                    logger.info(f"✅ CRYPTO_UPDATE_COMPLETE: Updated escrow {escrow_id} to {crypto} with new address {new_deposit_address}")
                    
                    # Delete the current message first (page transition to QR code)
                    if query and query.message and hasattr(query.message, 'delete'):
                        try:
                            await query.message.delete()  # type: ignore[attr-defined]
                            logger.info("✅ Previous message deleted for QR code display")
                        except Exception as del_err:
                            logger.warning(f"Could not delete previous message: {del_err}")
                    
                    # Generate and send QR code with payment details
                    if not new_deposit_address:
                        logger.error(f"No deposit address generated for escrow {escrow_id}")
                        await safe_answer_callback_query(query, "❌ Failed to generate payment address. Please try again.", show_alert=True)
                        return EscrowStates.PAYMENT_METHOD
                    
                    try:
                        from io import BytesIO
                        from services.qr_generator import QRCodeService
                        import base64
                        
                        # Generate QR code for the payment address
                        qr_base64 = QRCodeService.generate_deposit_qr(
                            address=new_deposit_address,
                            amount=crypto_amount,
                            currency=crypto
                        )
                        
                        if not qr_base64:
                            raise Exception("QR generation failed")
                        
                        # Convert base64 to bytes for Telegram
                        qr_bytes = base64.b64decode(qr_base64)
                        bio = BytesIO(qr_bytes)
                        bio.name = "qr_code.png"
                        
                        # Mobile-optimized caption with payment details
                        caption = f"""📱 Scan to Pay with {crypto}
🆔 {escrow_branding_id}

💰 {crypto_amount:.8f} {crypto}
💵 {format_money(total_amount, 'USD')}

<code>{new_deposit_address}</code>

🔒 Secure escrow • Payment protected"""
                        
                        keyboard = InlineKeyboardMarkup([
                            [InlineKeyboardButton("⬅️ Back to Review", callback_data="back_to_trade_review")],
                            [InlineKeyboardButton("❌ Cancel Trade", callback_data="cancel_escrow")]
                        ])
                        
                        if query and query.message:
                            # Send QR code as photo with payment details
                            await query.message.chat.send_photo(
                                photo=bio,
                                caption=caption,
                                parse_mode="HTML",
                                reply_markup=keyboard
                            )
                            logger.info(f"✅ QR code sent for {crypto} payment with address {new_deposit_address}")
                    
                    except Exception as qr_err:
                        logger.error(f"Error generating QR code: {qr_err}")
                        # Fallback to text-only display if QR generation fails
                        payment_text = f"""💳 Pay with {crypto}

Trade ID: {escrow_branding_id}
Amount: {crypto_amount:.8f} {crypto}
USD Value: {format_money(total_amount, 'USD')}

Payment Address:
<code>{new_deposit_address}</code>

⚠️ QR unavailable, copy address above"""
                        
                        keyboard = InlineKeyboardMarkup([
                            [InlineKeyboardButton("⬅️ Back to Review", callback_data="back_to_trade_review")],
                            [InlineKeyboardButton("❌ Cancel Trade", callback_data="cancel_escrow")]
                        ])
                        
                        if query and query.message:
                            await query.message.chat.send_message(
                                text=payment_text,
                                parse_mode="HTML",
                                reply_markup=keyboard
                            )
                    
                    return CONV_END
                    
                except Exception as e:
                    logger.error(f"❌ CRYPTO_UPDATE_FAILED: Could not update escrow {escrow_id} crypto to {crypto}: {e}")
                    await safe_edit_message_text(
                        query,
                        "❌ Unable to update payment method. Please try again.",
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("⬅️ Back to Review", callback_data="back_to_trade_review")
                        ]])
                    )
                    return CONV_END
            
            # ===================================================================
            # ORCHESTRATOR PATH: Use EscrowOrchestrator for new escrow creation
            # ===================================================================
            
            # Initialize deposit_address to prevent unbound issues
            deposit_address = None
            
            if should_create_new_escrow:
                from services.escrow_orchestrator import get_escrow_orchestrator, EscrowCreationRequest
            
                # Prepare seller information for orchestrator
                seller_id = None
                seller_contact_value = None
                seller_contact_display = None
                
                if escrow_data["seller_type"] == "username":
                    # Normalize username for lookup (remove @ and convert to lowercase)
                    normalized_username = clean_seller_identifier(escrow_data["seller_identifier"]).lstrip('@').lower()
                    
                    # Check if seller already exists in database
                    seller_stmt = select(User).where(
                        User.username.ilike(normalized_username)  # Case-insensitive lookup
                    )
                    seller_result = await session.execute(seller_stmt)
                    existing_seller = seller_result.scalar_one_or_none()
                    if existing_seller:
                        seller_id = existing_seller.id
                        logger.info(f"Escrow {escrow_id}: Linked to existing seller user_id={existing_seller.id}")
                    # CRITICAL FIX: ALWAYS set seller_contact_value and seller_contact_display, even when seller exists
                    # This ensures the contact info is available for display even if seller is already in database
                    seller_contact_value = normalized_username  # Normalized (no @, lowercase)
                    seller_contact_display = f"@{normalized_username}"  # UI display
                    if not existing_seller:
                        logger.info(f"Escrow {escrow_id}: Seller @{normalized_username} not found, stored in contact fields")
                elif escrow_data["seller_type"] == "email":
                    # Check if seller already exists in database (case-insensitive)
                    from sqlalchemy import func
                    seller_stmt = select(User).where(
                        func.lower(User.email) == func.lower(clean_seller_identifier(escrow_data["seller_identifier"]))
                    )
                    seller_result = await session.execute(seller_stmt)
                    existing_seller = seller_result.scalar_one_or_none()
                    if existing_seller:
                        seller_id = existing_seller.id
                    # CRITICAL FIX: ALWAYS set seller_contact_value and seller_contact_display, even when seller exists
                    seller_contact_value = clean_seller_identifier(escrow_data["seller_identifier"]).lower()  # Normalized
                    seller_contact_display = clean_seller_identifier(escrow_data["seller_identifier"]).lower()  # UI display
                elif escrow_data["seller_type"] == "phone":
                    # Check if seller already exists in database
                    # Note: User model may not have phone field - using telegram_id instead
                    seller_stmt = select(User).where(
                        User.telegram_id == clean_seller_identifier(escrow_data["seller_identifier"])
                    )
                    seller_result = await session.execute(seller_stmt)
                    existing_seller = seller_result.scalar_one_or_none()
                    if existing_seller:
                        seller_id = existing_seller.id
                    # CRITICAL FIX: ALWAYS set seller_contact_value and seller_contact_display, even when seller exists
                    seller_contact_value = clean_seller_identifier(escrow_data["seller_identifier"])  # Normalized (E.164)
                    seller_contact_display = clean_seller_identifier(escrow_data["seller_identifier"])  # UI display

                # Create escrow request for orchestrator
                escrow_request = EscrowCreationRequest(  # type: ignore
                user_id=int(user.id),  # type: ignore
                telegram_id=str(update.effective_user.id),  # type: ignore
                seller_identifier=clean_seller_identifier(escrow_data["seller_identifier"]),
                seller_type=escrow_data["seller_type"],
                amount=escrow_amount,
                currency="USD",
                description=escrow_data.get("description", "Buying goods"),
                expires_in_minutes=Config.PAYMENT_TIMEOUT_MINUTES,  # Use full payment timeout for buyer
                # Extended fields with calculated values
                fee_amount=total_platform_fee,
                total_amount=escrow_amount + total_platform_fee,  # CRITICAL: total_amount = amount + fee_amount (for DB constraint)
                fee_split_option=escrow_data.get("fee_split_option", "buyer_pays"),
                payment_method=f"crypto_{crypto}",
                delivery_hours=delivery_hours,  # Store hours for later use on payment confirmation
                delivery_deadline=None,  # Will be set when payment is confirmed
                auto_release_at=None,  # Will be set when payment is confirmed
                seller_id=seller_id,
                seller_contact_value=seller_contact_value,
                    seller_contact_display=seller_contact_display,
                    escrow_id=escrow_id
                )
                
                # Use orchestrator for idempotent escrow creation (no session - let orchestrator create async session)
                orchestrator = get_escrow_orchestrator()
                creation_response = await orchestrator.create_secure_trade(
                    escrow_request,
                    idempotency_key=f"crypto_payment_{escrow_id}",
                    session=None  # Let orchestrator create its own async session
                )
            
                # Check orchestrator response
                from services.escrow_orchestrator import EscrowCreationResult
                if creation_response.result != EscrowCreationResult.SUCCESS:
                    logger.error(f"❌ ORCHESTRATOR_ERROR: {creation_response.message}")
                    if creation_response.result == EscrowCreationResult.DUPLICATE_PREVENTED:
                        # Handle duplicate gracefully
                        logger.warning(f"🔒 DUPLICATE_PREVENTED: Using existing escrow {creation_response.existing_escrow_id}")
                        escrow_id = creation_response.existing_escrow_id
                        # Fetch deposit_address from existing escrow
                        existing_stmt = select(Escrow).where(Escrow.escrow_id == escrow_id)
                        existing_result = await session.execute(existing_stmt)
                        existing_esc = existing_result.scalar_one()
                        deposit_address = existing_esc.deposit_address
                    else:
                        raise ValueError(f"Escrow creation failed: {creation_response.message}")
                else:
                    escrow_id = creation_response.escrow_id
                    deposit_address = creation_response.deposit_address
                    logger.info(f"✅ ORCHESTRATOR_SUCCESS: Created escrow {escrow_id} with address {deposit_address}")
                
                # Get the created escrow for additional setup
                new_escrow_stmt = select(Escrow).where(Escrow.escrow_id == escrow_id)
                escrow_result = await session.execute(new_escrow_stmt)
                new_escrow = escrow_result.scalar_one()
            else:
                # Use existing escrow
                new_escrow = existing_escrow
                logger.info(f"✅ USING_EXISTING: Escrow {escrow_id} loaded from database")

            # Generate invitation token (additional business logic)
            from services.seller_invitation import SellerInvitationService

            setattr(
                new_escrow,
                "invitation_token",
                SellerInvitationService.generate_invitation_token(),
            )
            setattr(
                new_escrow,
                "invitation_expires_at",
                datetime.now(timezone.utc) + timedelta(days=7),
            )

            # CRITICAL VALIDATION: Prevent orphaned escrows without seller assignment
            # This prevents the exact issue where buyer pays but no seller exists
            seller_validation_passed = False
            seller_info_summary = "No seller information"
            
            # Check that seller information was properly collected and assigned
            if not escrow_data.get("seller_type") or not escrow_data.get("seller_identifier"):
                logger.error(f"❌ VALIDATION FAILED: Escrow {escrow_id} missing seller information in escrow_data")
                logger.error(f"   escrow_data seller_type: {escrow_data.get('seller_type')}")
                logger.error(f"   escrow_data seller_identifier: {escrow_data.get('seller_identifier')}")
                raise ValueError("Cannot create escrow without seller information")
            
            # Validate that seller information was actually assigned to the escrow record  # type: ignore
            if not new_escrow.seller_id and not (new_escrow.seller_contact_type and new_escrow.seller_contact_value):  # type: ignore
                logger.error(f"❌ VALIDATION FAILED: Escrow {escrow_id} has no seller_id or typed contact info assigned")
                logger.error(f"   new_escrow.seller_id: {new_escrow.seller_id}")
                logger.error(f"   new_escrow.seller_contact_type: {new_escrow.seller_contact_type}")
                logger.error(f"   new_escrow.seller_contact_value: {new_escrow.seller_contact_value}")
                raise ValueError(f"Escrow validation failed: No seller contact information assigned to escrow {escrow_id}")
            
            # Validation passed - log successful seller assignment  # type: ignore
            if new_escrow.seller_id:  # type: ignore
                seller_info_summary = f"seller_id={new_escrow.seller_id}"
                seller_validation_passed = True  # type: ignore
            elif new_escrow.seller_contact_type and new_escrow.seller_contact_value:  # type: ignore
                seller_info_summary = f"seller_contact={new_escrow.seller_contact_type}:{new_escrow.seller_contact_display}"
                seller_validation_passed = True
                
            logger.info(f"✅ VALIDATION PASSED: Escrow {escrow_id} has valid seller assignment: {seller_info_summary}")
            logger.info(f"🎯 ORPHANED ESCROW PREVENTION: Validation ensures buyer payment will have assigned seller")

            # Orchestrator already created and committed the escrow - no need to add/commit again
            # Just extract the needed IDs for context
            escrow_db_id = new_escrow.id
            saved_escrow_id = new_escrow.escrow_id

            # Store escrow info in context
            escrow_data["database_escrow_id"] = escrow_db_id
            escrow_data["escrow_id"] = escrow_id
            
            # Send admin notification about new escrow creation
            try:
                from services.admin_trade_notifications import admin_trade_notifications
                
                # Prepare escrow data for admin notification
                buyer_info = (
                    getattr(user, 'username', None) or getattr(user, 'first_name', None) or f"User_{getattr(user, 'telegram_id', 'unknown')}"
                    if user else "Unknown Buyer"
                )
                seller_info = (
                    f"@{escrow_data['seller_identifier']}" 
                    if escrow_data["seller_type"] == "username"
                    else escrow_data["seller_identifier"]
                )
                
                escrow_notification_data = {
                    'escrow_id': saved_escrow_id,
                    'amount': Decimal(str(escrow_data["amount"])),
                    'currency': 'USD',
                    'buyer_info': buyer_info,
                    'seller_info': seller_info,
                    'created_at': datetime.now(timezone.utc)
                }
                
                # Send admin notification asynchronously (don't block escrow creation)
                asyncio.create_task(
                    admin_trade_notifications.notify_escrow_created(escrow_notification_data)
                )
                # Send Telegram group notification
                asyncio.create_task(
                    admin_trade_notifications.send_group_notification_escrow_created(escrow_notification_data)
                )
                # Broadcast to all registered groups
                from services.group_event_service import group_event_service
                asyncio.create_task(
                    group_event_service.broadcast_trade_created(escrow_notification_data)
                )
                logger.info(f"Admin notification queued for escrow creation: {saved_escrow_id}")
                
            except Exception as e:
                logger.error(f"Failed to queue admin notification for escrow creation: {e}")
            
            # Send email confirmation to buyer if they have verified email  # type: ignore
            if user and user.email and getattr(user, 'is_verified', False):  # type: ignore
                try:
                    from services.email import EmailService
                    
                    email_service = EmailService()
                    
                    # Get seller display information (clean for plain text email)
                    seller_identifier = escrow_data["seller_identifier"].replace('\\', '')
                    seller_display = (
                        f"@{seller_identifier}"
                        if escrow_data["seller_type"] == "username"
                        else seller_identifier
                    )
                    
                    # Make email notification async to improve performance (prevent 540ms delay)
                    # Since send_trade_notification is already async, just create a task directly
                    asyncio.create_task(email_service.send_trade_notification(
                        str(getattr(user, "email", "")),
                        (
                            str(getattr(user, "first_name", ""))
                            if getattr(user, "first_name", "")
                            else (
                                str(getattr(user, "username", ""))
                                if getattr(user, "username", "")
                                else "Anonymous Buyer"
                            )
                        ),
                        str(saved_escrow_id),
                        "trade_created",
                        {
                            "amount": Decimal(str(escrow_data["amount"])),
                            "currency": "USD",
                            "total_amount": Decimal(str(total_amount)),
                            "status": "awaiting_payment",
                            "seller": seller_display,
                            "payment_method": f"crypto_{crypto}",
                            "description": str(escrow_data.get("description", "Buying goods")),
                        },
                    ))
                    logger.info(f"Email confirmation sent to buyer {user.email} for crypto payment")
                except Exception as e:
                    logger.error(f"Failed to send email confirmation to buyer for crypto payment: {e}")
            
            # REMOVED: Enhanced confirmation should NOT be sent here
            # In payment-first architecture, seller is ONLY notified after payment confirmation
            # This prevents misleading messages about "invitation sent" when payment hasn't been made
            logger.info(f"Escrow created and committed with ID: {saved_escrow_id}")
            # Seller notification will happen in BlockBee callback after payment is confirmed
            
        # IMPORTANT: After this point, new_escrow is detached from session
        # Use saved_escrow_id variable instead of accessing new_escrow attributes
        
        # Orchestrator already created payment address - use it directly
        # Check for None or empty string (both invalid)
        if not deposit_address:  # type: ignore[truthy-bool]
            logger.error(f"❌ ORCHESTRATOR_ERROR: No deposit address returned for escrow {escrow_id}")
            await safe_edit_message_text(
                query,  # type: ignore
                "❌ Payment setup failed. Try again.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("⬅️ Back", callback_data="back_to_payment")]]
                ),
            )
            return CONV_END
        
        # Use escrow_id for branding display
        lb_trade_id = escrow_id

        # Store payment details
        context.user_data["escrow_data"].update(
            {
                "payment_method": f"crypto_{crypto}",
                "crypto_currency": crypto,
                "crypto_amount": markup_rate,
                "deposit_address": deposit_address,
            }
        )

        # Enhanced confirmation is now handled inside the session context above
        # to avoid session binding issues

        # Use the saved escrow ID for display (not the detached object)
        escrow_id_display = saved_escrow_id
        
        # Use the already generated transaction ID for consistent display
        header = BrandingUtils.make_header("Crypto Payment")
        formatted_amount = BrandingUtils.format_branded_amount(markup_rate, crypto)
        formatted_usd = BrandingUtils.format_branded_amount(total_amount, "USD")
        
        # Store branding transaction ID for consistent display everywhere
        context.user_data["escrow_data"]["branding_trade_id"] = lb_trade_id
        display_trade_id = lb_trade_id  # Show full ID with prefix (ES, EX, CO, TX, RF)
        
        # Format fee breakdown for display
        formatted_escrow = BrandingUtils.format_branded_amount(escrow_amount, "USD")
        formatted_fee = BrandingUtils.format_branded_amount(buyer_fee_amount, "USD")
        
        # Extract just the numeric amounts without emojis for compact display
        escrow_numeric = str(escrow_amount) if escrow_amount else "0"
        fee_numeric = str(buyer_fee_amount) if buyer_fee_amount else "0"
        total_numeric = str(total_amount) if total_amount else "0"
        
        text = f"""{header}
{formatted_amount} ({formatted_usd})

Escrow: ${escrow_numeric}
Fee: ${fee_numeric}
Total: ${total_numeric}

To: <code>{deposit_address}</code>
🆔 {display_trade_id} • ⏰ {Config.PAYMENT_TIMEOUT_MINUTES}min

1️⃣ Send exact • 2️⃣ Wait 1-3min • 3️⃣ Done

{BrandingUtils.make_trust_footer()}"""

        # Remove crypto switching after payment address is generated (match exchange flow)
        keyboard = [
            [InlineKeyboardButton("📱 QR Code", callback_data="show_qr")],
            [InlineKeyboardButton("❌ Cancel Trade", callback_data="cancel_escrow")],
        ]

        try:
            await safe_edit_message_text(
                query, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard)  # type: ignore
            )
        except Exception as edit_error:
            # If message is identical, no need to edit
            if "not modified" in str(edit_error).lower():
                logger.info("Message content unchanged, skipping edit")
            else:
                # FALLBACK: Try with HTML parsing if MarkdownV2 fails
                logger.warning(f"MarkdownV2 failed, falling back to HTML: {edit_error}")
                try:
                    # Use consistent branding trade ID
                    display_trade_id = context.user_data.get("escrow_data", {}).get("branding_trade_id", lb_trade_id)  # Show full ID
                    fallback_text = f"""💰 Send {markup_rate:.8f} {crypto} (${total_amount:.2f})

To: {deposit_address}
🆔 Trade: {display_trade_id}

⏰ {Config.PAYMENT_TIMEOUT_MINUTES}min"""
                    await safe_edit_message_text(
                        query, fallback_text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard)  # type: ignore
                    )
                except Exception as fallback_error:
                    logger.error(f"Both MarkdownV2 and HTML failed: {fallback_error}")
                    # Final fallback - plain text
                    # Use consistent branding trade ID
                    display_trade_id = context.user_data.get("escrow_data", {}).get("branding_trade_id", lb_trade_id)  # Show full ID
                    plain_text = f"""💰 Send {markup_rate:.8f} {crypto} (${total_amount:.2f})

To: {deposit_address}
🆔 Trade: {display_trade_id}

⏰ Pay within {Config.PAYMENT_TIMEOUT_MINUTES}min or trade cancels
💡 Copy address above"""
                    await safe_edit_message_text(
                        query, plain_text, reply_markup=InlineKeyboardMarkup(keyboard)  # type: ignore
                    )
        
        return EscrowStates.PAYMENT_PROCESSING

    except Exception as e:
        logger.error(f"Error in crypto payment direct: {e}")
        # Mark escrow session data as failed so user can retry
        if context.user_data and "escrow_data" in context.user_data:
            context.user_data["escrow_data"]["status"] = "failed"
            logger.info("✅ Marked escrow session data as 'failed' to allow retry")
        
        # Provide specific error context
        error_msg = "❌ Payment Setup Failed\n\n"
        if "timeout" in str(e).lower():
            error_msg += "Payment provider timed out.\n\n⚡ Try again or use another payment method."
        elif "rate" in str(e).lower() or "price" in str(e).lower():
            error_msg += "Unable to fetch current crypto rates.\n\n⚡ Try again in a moment."
        else:
            error_msg += "Couldn't create payment address.\n\n⚡ Try again or contact support."
        
        try:
            await safe_edit_message_text(
                query,  # type: ignore
                error_msg,
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("⬅️ Back to Payment", callback_data="back_to_payment")]]
                ),
            )
        except Exception as e:
            # If edit fails, send new message
            logger.debug(f"Could not edit message, sending new message instead: {e}")
            if query and query.message and hasattr(query.message, "reply_text"):  # type: ignore
                await query.message.reply_text(  # type: ignore
                    error_msg,
                    reply_markup=InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    "⬅️ Back to Payment", callback_data="back_to_payment"
                                )
                            ]
                        ]
                    ),
                )
        return ConversationHandler.END

async def handle_crypto_payment(update: TelegramUpdate, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle specific cryptocurrency payment"""
    query = update.callback_query
    # PERFORMANCE: Instant acknowledgment
    if query:
        # IMMEDIATE FEEDBACK: Escrow action
        await safe_answer_callback_query(query, "🛡️ Escrow action")

    if not query or not query.data:
        logger.error("No query or query.data in handle_crypto_payment")
        return CONV_END

    crypto = query.data.replace("crypto_", "")

    if not context.user_data or "escrow_data" not in context.user_data:
        await safe_edit_message_text(
            query, "⏰ Session Expired\n\nInactive too long.\n\n⚡ Tap /start to continue."
        )
        return CONV_END

    escrow_data = context.user_data["escrow_data"]
    # FIXED: Use buyer_fee from fee split calculation instead of hardcoded 5%
    amount = Decimal(str(escrow_data["amount"]))
    buyer_fee = Decimal(str(escrow_data.get("buyer_fee", get_default_fee(amount))))
    total_amount = amount + buyer_fee

    # Get crypto rates and calculate amount needed
    try:
        crypto_service = CryptoServiceAtomic()
        rates = await crypto_service.get_crypto_rates()

        if crypto not in rates:
            await safe_edit_message_text(
                query,  # type: ignore
                f"❌ {crypto} rate not available. Please try another currency.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("⬅️ Back", callback_data="back_to_payment")]]
                ),
            )
            return CONV_END

        crypto_rate = Decimal(str(rates[crypto]))  # Convert to Decimal for precision
        
        # SECURITY FIX: Check for currency switch and recalculate from USD value
        if escrow_data.get("payment_currency") and escrow_data.get("payment_currency") != crypto:
            from utils.currency_validation import CurrencyValidator, ConversionAuditLogger
            
            logger.info(f"ESCROW CRYPTO SWITCH DETECTED: {escrow_data.get('payment_currency')} -> {crypto}")
            # Use stored USD value to calculate new crypto amount
            stored_usd_value = Decimal(str(escrow_data.get("usd_value", total_amount)))
            old_crypto = escrow_data.get("payment_currency")
            old_rate = Decimal(str(escrow_data.get("original_crypto_rate", 1)))
            old_amount = stored_usd_value / old_rate if old_rate > 0 else Decimal("0")
            
            # Calculate new amount using validation framework
            try:
                crypto_amount, validation_info = CurrencyValidator.calculate_equivalent_amount(
                    source_amount=old_amount,
                    source_rate=old_rate,
                    target_rate=crypto_rate,
                    source_currency=old_crypto,
                    target_currency=crypto,
                    context="escrow_payment_switch"
                )
            except Exception as validation_error:
                from utils.context_security import ErrorRecoveryManager
                logger.error(f"Crypto switch validation failed: {validation_error}")
                
                # Handle error and provide recovery
                user_id = update.effective_user.id if update.effective_user else 0
                recovery_result = ErrorRecoveryManager.handle_conversion_error(
                    error=validation_error,
                    context_data=escrow_data,
                    operation="escrow_crypto_switch",
                    user_id=user_id
                )
                
                await safe_edit_message_text(
                    query, recovery_result["user_message"]
                )
                return CONV_END
            
            # Log the switch for audit
            user_id = update.effective_user.id if update.effective_user else 0
            ConversionAuditLogger.log_crypto_switch(
                user_id=user_id,
                old_crypto=old_crypto,
                new_crypto=crypto,
                old_amount=old_amount,
                new_amount=crypto_amount,
                usd_value=stored_usd_value,
                context="escrow_payment"
            )
            
            logger.info(f"USD value preserved: ${Decimal(str(stored_usd_value)):.2f} -> {Decimal(str(crypto_amount)):.8f} {crypto}")
        else:
            # First time selection - calculate normally
            crypto_amount = total_amount / crypto_rate
            escrow_data["usd_value"] = Decimal(str(total_amount))
        
        # Import Config to avoid scoping issues
        from config import Config as AppConfig
        
        # FIXED: Use configurable exchange markup instead of hardcoded 2%
        markup_percentage = Decimal(str(AppConfig.EXCHANGE_MARKUP_PERCENTAGE)) / Decimal("100")
        markup_rate = crypto_amount * (Decimal("1") + markup_percentage)
        
        # Apply $2 crypto fee padding for non-USDT currencies (display crypto amount includes padding)
        USDT_CURRENCIES = {"USDT", "USDT_TRC20", "USDT_ERC20", "USDT_BEP20"}
        if crypto.upper() not in USDT_CURRENCIES:
            padding_crypto = Decimal("2") / crypto_rate  # Convert $2 to crypto equivalent
            markup_rate = markup_rate + padding_crypto
        
        # Update payment currency tracking
        escrow_data["payment_currency"] = crypto
        escrow_data["original_crypto_rate"] = Decimal(str(crypto_rate))

        # Generate deposit address using payment processor manager
        from services.payment_processor_manager import payment_manager
        # Import Config at function level to avoid scoping issues
        from config import Config as AppConfig

        # Ensure we have a real escrow ID
        if "real_escrow_id" not in escrow_data:
            escrow_data["real_escrow_id"] = UniversalIDGenerator.generate_escrow_id()

        escrow_id = escrow_data["real_escrow_id"]

        try:
            # CRITICAL FIX: Define branding ID (utid) for user-facing consistency
            escrow_branding_id = escrow_id  # In this function, escrow_id is actually the UTID generated by UTIDGenerator
            
            # VALIDATION LOG: Confirm we're passing UTID to payment providers
            logger.info(f"🔍 ESCROW_PAYMENT_VALIDATION: Calling payment provider with UTID '{escrow_branding_id}' for crypto {crypto}")
            logger.info(f"🔍 ESCROW_PAYMENT_METADATA: escrow_id='{escrow_id}', utid='{escrow_branding_id}'")
            
            # Normalize webhook URL to prevent double /webhook/ paths
            base_url = normalize_webhook_base_url(AppConfig.WEBHOOK_URL)
            provider = payment_manager.primary_provider.value
            callback_url = f"{base_url}/dynopay/escrow" if provider == 'dynopay' else f"{base_url}/blockbee/callback/{escrow_branding_id}"
            
            address_data, provider_used = await payment_manager.create_payment_address(
                currency=crypto,  # type: ignore
                amount=total_amount,  # type: ignore
                callback_url=callback_url,
                reference_id=escrow_branding_id,  # CRITICAL FIX: Use utid (user-facing Trade ID) as reference_id
                metadata={'escrow_id': escrow_id, 'utid': escrow_branding_id, 'amount_usd': total_amount}  # Include both IDs for compatibility
            )
            logger.info(f"✅ Using payment provider ({provider_used.value}) for escrow {escrow_id}")
        except Exception as e:
            logger.error(f"Payment provider error for {crypto}: {str(e)}")
            await safe_edit_message_text(
                query,  # type: ignore
                f"❌ Payment setup failed for {crypto}.\n\nError: {str(e)[:100]}\n\nPlease try again or contact support.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("⬅️ Back", callback_data="back_to_payment")]]
                ),
            )
            return CONV_END

        if not address_data.get("address"):
            await safe_edit_message_text(
                query,  # type: ignore
                "❌ Payment setup failed. Try again.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("⬅️ Back", callback_data="back_to_payment")]]
                ),
            )
            return CONV_END

        deposit_address = address_data["address"]
        
        # Calculate exact crypto amount using standardized fee calculation FIRST
        # Get escrow ID for display
        escrow_id_display = context.user_data.get("escrow_data", {}).get("early_escrow_id", "N/A")
        
        # Calculate buyer payment amount
        escrow_amount = context.user_data.get("escrow_data", {}).get("amount", 10.0)
        from utils.fee_calculator import FeeCalculator
        fee_breakdown = FeeCalculator.calculate_escrow_breakdown(escrow_amount=escrow_amount)
        buyer_pays_usd = fee_breakdown['buyer_total_payment']
        
        # CRITICAL FIX: Apply ESCROW markup structure: (Market Rate + 5% Markup) + Platform Fee
        # buyer_pays_usd already includes platform fee, now apply market rate markup
        markup_percentage = Decimal(str(Config.EXCHANGE_MARKUP_PERCENTAGE)) / Decimal("100")
        # Solve for crypto amount: crypto_amount * rate * (1 + markup) = buyer_pays_usd
        crypto_amount_precise = buyer_pays_usd / (crypto_rate * (Decimal("1") + markup_percentage))
        
        # TODO: Fix escrow_db_id reference - temporarily comment out database update
        # CRITICAL FIX: Save deposit address to database immediately
        # with SessionLocal() as update_session:
        #     escrow_to_update = update_session.query(Escrow).filter(Escrow.id == escrow_db_id).first()
        #     if escrow_to_update:
        #         escrow_to_update.deposit_address = deposit_address
        #         update_session.commit()
        #         logger.info(f"Saved deposit address {deposit_address} to escrow {escrow_id}")
        #     else:
        #         logger.error(f"Could not find escrow {escrow_id} to save deposit address")

        # Store payment details
        context.user_data["escrow_data"].update(
            {
                "payment_method": f"crypto_{crypto}",
                "crypto_currency": crypto,
                "crypto_amount": crypto_amount_precise,  # Use precise amount
                "crypto_amount_usd": buyer_pays_usd,  # Store USD amount too
                "deposit_address": deposit_address,
            }
        )

        # Show payment instructions
        network_info = "TRC20" if crypto == "USDT" else crypto.upper()
        
        logger.info(
            f"ESCROW payment instruction: USD ${buyer_pays_usd} / (${crypto_rate} * {1 + markup_percentage}) = {crypto_amount_precise:.8f} {crypto}"
        )
        
        # Use existing escrow ID for consistent display (no new ID generation)
        lb_trade_id = escrow_id  # Use unified escrow ID for display consistency
        header = BrandingUtils.make_header(f"{crypto} Payment")
        formatted_crypto = BrandingUtils.format_branded_amount(crypto_amount_precise, crypto)
        formatted_usd = BrandingUtils.format_branded_amount(buyer_pays_usd, "USD")
        
        # Format fee breakdown for display
        formatted_escrow = BrandingUtils.format_branded_amount(escrow_amount, "USD")
        platform_fee = buyer_pays_usd - Decimal(str(escrow_amount))
        formatted_fee = BrandingUtils.format_branded_amount(platform_fee, "USD")
        
        # Extract just the numeric amounts without emojis for compact display
        escrow_numeric = str(escrow_amount) if escrow_amount else "0"
        fee_numeric = str(platform_fee) if platform_fee else "0"
        total_numeric = str(buyer_pays_usd) if buyer_pays_usd else "0"
        
        text = f"""{header} ({network_info})
{formatted_crypto} ({formatted_usd})

Escrow: ${escrow_numeric}
Fee: ${fee_numeric}
Total: ${total_numeric}

Address: <code>{deposit_address}</code>
🆔 {lb_trade_id} • ⏳ 14:45

1️⃣ Send exact • 2️⃣ Wait 1-3min • 3️⃣ Done

{BrandingUtils.make_trust_footer()}"""

        # Remove crypto switching after payment address is generated (match exchange flow)
        keyboard = [
            [InlineKeyboardButton("📱 QR Code", callback_data="show_qr")],
            [InlineKeyboardButton("❌ Cancel Trade", callback_data="cancel_escrow")],
        ]

        try:
            await safe_edit_message_text(
                query, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard)  # type: ignore
            )
        except Exception as edit_error:
            # FALLBACK: Try with HTML parsing if MarkdownV2 fails
            logger.warning(f"MarkdownV2 failed for crypto payment, falling back to HTML: {edit_error}")
            try:
                fallback_text = f"""₮ {crypto} ({network_info})
Send: {markup_rate:.6f} {crypto}

Address: <code>{deposit_address}</code>
🆔 ...{escrow_id_display[-6:]} • ⏳ 14:45

Steps:
1️⃣ Send exact amount
2️⃣ Wait 1-3 min
3️⃣ Seller gets offer

Payment auto-confirmed"""
                await safe_edit_message_text(
                    query, fallback_text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard)  # type: ignore
                )
            except Exception as fallback_error:
                logger.error(f"Both MarkdownV2 and HTML failed for crypto payment: {fallback_error}")
                # Final fallback - plain text
                plain_text = f"""₮ {crypto} ({network_info})
Send: {markup_rate:.6f} {crypto}

Address: {deposit_address}
🆔 ...{escrow_id_display[-6:]} • ⏳ 14:45

Steps:
1️⃣ Send exact amount
2️⃣ Wait 1-3 min
3️⃣ Seller gets offer

Payment auto-confirmed"""
                await safe_edit_message_text(
                    query, plain_text, reply_markup=InlineKeyboardMarkup(keyboard)  # type: ignore
                )

        return EscrowStates.PAYMENT_PROCESSING
    
    except Exception as e:
        logger.error(f"Error setting up crypto payment: {e}")
        await safe_edit_message_text(
            query,  # type: ignore
            "❌ Error setting up payment. Please try again.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Back", callback_data="back_to_payment")]]
            ),
        )
        return ConversationHandler.END


async def handle_ngn_payment_direct(
    query, context: ContextTypes.DEFAULT_TYPE, total_amount: float
) -> int:
    """Handle NGN payment with direct Fincra mini web link"""
    try:
        async with async_managed_session() as session:
            # Get user info for payment link generation
            if query and hasattr(query, "from_user") and query.from_user:
                stmt = select(User).where(User.telegram_id == query.from_user.id)
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()
            else:
                user = None
        if not user or not hasattr(user, "id"):
            await safe_edit_message_text(
                query,  # type: ignore
                "❌ User not found. Please try crypto payment.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("⬅️ Back", callback_data="back_to_payment")]]
                ),
            )
            return CONV_END

        # Get current NGN rate with RATE LOCK protection
        from services.rate_lock_service import rate_lock_service

        # Create rate lock for escrow NGN payment - use existing user object
        rate_lock_info = await rate_lock_service.create_rate_lock(
            currency="USD", user_id=int(getattr(user, "id", 0))
        )

        if not rate_lock_info:
            await safe_edit_message_text(
                query,  # type: ignore
                "❌ Unable to lock exchange rate. Please try crypto payment.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("⬅️ Back", callback_data="back_to_payment")]]
                ),
            )
            return CONV_END

        # Use rate lock info properly based on RateLock model structure
        ngn_rate = rate_lock_info.ngn_rate
        # Calculate NGN amount from USD total using locked rate
        ngn_amount = Decimal(str(total_amount)) * Decimal(str(ngn_rate))

        # Create Fincra payment link
        from services.fincra_service import fincra_service

        payment_link_result = await fincra_service.create_payment_link(
            amount_ngn=ngn_amount,
            user_id=getattr(user, "id", 0),
            purpose="escrow_payment",
        )

        if not payment_link_result:
            await safe_edit_message_text(
                query,  # type: ignore
                "❌ Unable to generate payment link. Please try crypto payment.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("⬅️ Back", callback_data="back_to_payment")]]
                ),
            )
            return CONV_END

        # Store payment details  # type: ignore
        context.user_data["escrow_data"].update(  # type: ignore
            {
                "payment_method": "ngn_bank",
                "ngn_amount": ngn_amount,
                "ngn_rate": ngn_rate,
                "payment_link_result": payment_link_result,
            }
        )

        # Use existing escrow ID for consistent NGN payment display
        header = BrandingUtils.make_header("Bank Transfer")
        formatted_usd = BrandingUtils.format_branded_amount(total_amount, "USD")
        formatted_ngn = BrandingUtils.format_branded_amount(ngn_amount, "NGN")
        lb_trade_id = context.user_data.get("escrow_data", {}).get("unified_escrow_id", "N/A")  # Use unified escrow ID from context  # type: ignore
        
        text = f"""{header}

{formatted_usd}
{formatted_ngn} @ ₦{ngn_rate:.0f}/USD

🆔 Trade: {lb_trade_id}
Payment Link: {payment_link_result['payment_link']}

⏰ Pay within {Config.PAYMENT_TIMEOUT_MINUTES}min or trade cancels
✅ Auto-confirmation in 2-5 minutes

Tap link above to complete payment via Fincra's secure platform.

{BrandingUtils.make_trust_footer()}"""

        keyboard = [
            [
                InlineKeyboardButton(
                    "💳 Open Payment", url=payment_link_result["payment_link"]
                )
            ],
            [InlineKeyboardButton("❌ Cancel Trade", callback_data="cancel_escrow")],
        ]

        await safe_edit_message_text(
            query,  # type: ignore
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            disable_web_page_preview=False,
        )
        return EscrowStates.PAYMENT_PROCESSING

    except Exception as e:
        import logging

        logging.error(f"Error in NGN payment direct: {e}")
        await safe_edit_message_text(
            query,  # type: ignore
            "❌ Error processing NGN payment. Please try crypto payment.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Back", callback_data="back_to_payment")]]
            ),
        )
        return ConversationHandler.END


async def handle_ngn_payment(update: TelegramUpdate, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle NGN bank transfer payment"""
    query = update.callback_query
    if not context.user_data or "escrow_data" not in context.user_data:
        if query:
            await safe_edit_message_text(
                query,
                "⏰ Session Expired\n\nInactive too long.\n\n⚡ Tap /start to continue.",
            )
        return CONV_END

    escrow_data = context.user_data["escrow_data"]
    # FIXED: Use buyer_fee from fee split calculation instead of hardcoded 5%
    amount = Decimal(str(escrow_data["amount"]))
    buyer_fee = Decimal(str(escrow_data.get("buyer_fee", get_default_fee(amount))))
    total_amount = amount + buyer_fee

    # Get NGN exchange rate with RATE LOCK protection
    try:
        # Get user from database first
        async with async_managed_session() as session:
            if query and hasattr(query, "from_user") and query.from_user:
                stmt = select(User).where(User.telegram_id == query.from_user.id)
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()
            else:
                user = None
                
            if not user or not hasattr(user, "id"):
                await safe_edit_message_text(
                    query, "❌ User not found. Please try crypto payment."
                )
                return CONV_END

            from services.rate_lock_service import rate_lock_service

            # Create rate lock for escrow NGN payment - use existing user object
            rate_lock_info = await rate_lock_service.create_rate_lock(
                currency="USD", user_id=int(getattr(user, "id", 0))
            )

        if not rate_lock_info:
            await safe_edit_message_text(
                query,  # type: ignore
                "❌ Unable to lock exchange rate. Please try crypto payment.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("⬅️ Back", callback_data="back_to_payment")]]
                ),
            )
            return CONV_END

        # Use rate lock info properly based on RateLock model structure
        ngn_rate = rate_lock_info.ngn_rate
        # Calculate NGN amount from USD total using locked rate
        ngn_amount = Decimal(str(total_amount)) * Decimal(str(ngn_rate))

        # Generate virtual account via Fincra
        fincra = FincraService()

        # Create escrow record in database before payment
        async with async_managed_session() as session:
            if query and query.from_user:
                stmt = select(User).where(User.telegram_id == query.from_user.id)
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()
            else:
                user = None
            if not user:
                await safe_edit_message_text(
                    query,  # type: ignore
                    "❌ User not found. Please try crypto payment.",
                    reply_markup=InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    "⬅️ Back", callback_data="back_to_payment"
                                )
                            ]
                        ]
                    ),
                )
                return CONV_END

            # CRITICAL FIX: Use existing escrow ID if available (from pay_escrow handler)
            # Only generate new ID for new trades
            existing_escrow_id = escrow_data.get("existing_escrow_id") or escrow_data.get("early_escrow_id")  # Check if pay_escrow handler stored existing ID
            
            if existing_escrow_id:
                # User clicked "Pay Now" from existing trade - reuse that ID
                escrow_utid = existing_escrow_id
                logger.info(f"♻️ REUSING_EXISTING_ID: {escrow_utid} (from existing payment_pending trade)")
            else:
                # New trade creation - generate fresh ID
                from utils.universal_id_generator import UniversalIDGenerator
                escrow_utid = UniversalIDGenerator.generate_escrow_id()
                logger.info(f"🆔 NEW_ID_GENERATED: {escrow_utid} (fresh ID for new trade)")
            
            escrow_data["unified_escrow_id"] = escrow_utid

            # Validate escrow data before creation
            is_valid, validation_errors = ProductionValidator.validate_escrow_creation(  # type: ignore
                escrow_data, int(user.id)  # type: ignore
            )
            
            if not is_valid:
                error_msg = "❌ Invalid escrow data:\n" + "\n".join([f"• {err}" for err in validation_errors])
                await safe_edit_message_text(
                    query,
                    error_msg,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 Try Again", callback_data="back_to_payment")]
                    ])
                )
                return CONV_END

            # Calculate fees for escrow using same structure as crypto payments
            escrow_amount = Decimal(str(escrow_data["amount"]))
            
            # Apply same fee calculation logic as crypto payments
            fee_percentage = Decimal(str(Config.ESCROW_FEE_PERCENTAGE)) / Decimal("100")
            platform_fee = (escrow_amount * fee_percentage).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            total_with_fee = escrow_amount + platform_fee
            
            # Validate fee calculations
            fee_valid, fee_errors = ProductionValidator.validate_fee_calculation(
                escrow_amount, platform_fee, total_with_fee
            )
            
            if not fee_valid:
                logger.error(f"Fee calculation errors: {fee_errors}")
                # Error logging simplified after cleanup

            # Reuse existing ID (already generated earlier in this function)
            # This is the second reference after the earlier ID generation at line 3877-3883
            escrow_id_to_use = escrow_data.get("unified_escrow_id")
            
            # Should not happen since we generated ID earlier, but check anyway
            if not escrow_id_to_use:
                logger.error("❌ LOGIC_ERROR: No unified escrow ID found for NGN payment - this should never happen!")
                raise ValueError("Missing unified escrow ID - NGN payment flow corrupted")
            
            # DELIVERY TIMING: Store delivery_hours for later use when payment is confirmed
            # Delivery deadline will be set AFTER payment confirmation, not at creation
            delivery_hours = escrow_data.get("delivery_hours", 72)  # Default 72 hours if not specified
            from datetime import timezone
            current_time = datetime.now(timezone.utc)
            
            # PAYMENT TIMING: Calculate payment expiry based on configured timeout
            payment_expiry = current_time + timedelta(minutes=Config.PAYMENT_TIMEOUT_MINUTES)
            
            # ===================================================================
            # ORCHESTRATOR MIGRATION: Use EscrowOrchestrator for NGN payments
            # ===================================================================
            
            from services.escrow_orchestrator import get_escrow_orchestrator, EscrowCreationRequest
            
            # Prepare seller information for orchestrator
            seller_id = None
            seller_contact_value = None
            seller_contact_display = None
            
            if escrow_data["seller_type"] == "username":
                # Normalize username for lookup (remove @ and convert to lowercase)
                normalized_username = clean_seller_identifier(escrow_data["seller_identifier"]).lstrip('@').lower()
                
                # Check if seller already exists in database
                stmt = select(User).where(User.username.ilike(normalized_username))
                result = await session.execute(stmt)
                existing_seller = result.scalar_one_or_none()
                if existing_seller:
                    seller_id = existing_seller.id
                    logger.info(f"NGN Escrow {escrow_id_to_use}: Linked to existing seller user_id={existing_seller.id}")
                else:
                    seller_contact_value = normalized_username  # Normalized (no @, lowercase)
                    seller_contact_display = f"@{normalized_username}"  # UI display
                    logger.info(f"NGN Escrow {escrow_id_to_use}: Seller @{normalized_username} not found, stored in contact fields")
            elif escrow_data["seller_type"] == "email":
                # Check if seller already exists in database (case-insensitive)
                from sqlalchemy import func
                stmt = select(User).where(
                    func.lower(User.email) == func.lower(clean_seller_identifier(escrow_data["seller_identifier"]))
                )
                result = await session.execute(stmt)
                existing_seller = result.scalar_one_or_none()
                if existing_seller:
                    seller_id = existing_seller.id
                else:
                    seller_contact_value = clean_seller_identifier(escrow_data["seller_identifier"]).lower()  # Normalized
                    seller_contact_display = clean_seller_identifier(escrow_data["seller_identifier"]).lower()  # UI display
            elif escrow_data["seller_type"] == "phone":
                # Check if seller already exists in database
                stmt = select(User).where(
                    User.phone == clean_seller_identifier(escrow_data["seller_identifier"])  # type: ignore
                )
                result = await session.execute(stmt)
                existing_seller = result.scalar_one_or_none()
                if existing_seller:
                    seller_id = existing_seller.id
                else:
                    seller_contact_value = clean_seller_identifier(escrow_data["seller_identifier"])  # Normalized
                    seller_contact_display = clean_seller_identifier(escrow_data["seller_identifier"])  # UI display

            # Create escrow request for orchestrator
            escrow_request = EscrowCreationRequest(  # type: ignore
                user_id=int(user.id),  # type: ignore
                telegram_id=str(update.effective_user.id),  # type: ignore
                seller_identifier=clean_seller_identifier(escrow_data["seller_identifier"]),
                seller_type=escrow_data["seller_type"],
                amount=escrow_amount,
                currency="USD",
                description=escrow_data.get("description", "Buying goods"),
                expires_in_minutes=Config.PAYMENT_TIMEOUT_MINUTES,  # Use full payment timeout for buyer
                # Extended fields for NGN payment
                fee_amount=platform_fee,
                total_amount=total_with_fee,
                fee_split_option=escrow_data.get("fee_split_option", "buyer_pays"),
                payment_method="ngn_bank",
                delivery_hours=delivery_hours,  # Store hours for later use on payment confirmation
                delivery_deadline=None,  # Will be set when payment is confirmed
                auto_release_at=None,  # Will be set when payment is confirmed
                seller_id=seller_id,
                seller_contact_value=seller_contact_value,
                seller_contact_display=seller_contact_display,
                escrow_id=escrow_id_to_use
            )
            
            # Use orchestrator for idempotent escrow creation
            orchestrator = get_escrow_orchestrator()
            creation_response = await orchestrator.create_secure_trade(
                escrow_request,
                idempotency_key=f"ngn_payment_{escrow_id_to_use}",
                session=None  # Let orchestrator create its own async session
            )
            
            # Check orchestrator response
            from services.escrow_orchestrator import EscrowCreationResult
            if creation_response.result != EscrowCreationResult.SUCCESS:
                logger.error(f"❌ NGN_ORCHESTRATOR_ERROR: {creation_response.message}")
                if creation_response.result == EscrowCreationResult.DUPLICATE_PREVENTED:
                    # Handle duplicate gracefully
                    logger.warning(f"🔒 NGN_DUPLICATE_PREVENTED: Using existing escrow {creation_response.existing_escrow_id}")
                    escrow_id_to_use = creation_response.existing_escrow_id
                else:
                    await session.rollback()
                    raise ValueError(f"NGN escrow creation failed: {creation_response.message}")
            else:
                escrow_id_to_use = creation_response.escrow_id
                logger.info(f"✅ NGN_ORCHESTRATOR_SUCCESS: Created escrow {escrow_id_to_use}")
            
            # Get the created escrow for additional setup

            new_escrow_stmt = select(Escrow).where(Escrow.escrow_id == escrow_id_to_use)  # type: ignore
            escrow_result = await session.execute(new_escrow_stmt)  # type: ignore
            new_escrow = escrow_result.scalar_one()
            
            # Set seller contact information based on type
            if escrow_data["seller_type"] == "username":
                # Check if seller already exists in database using async query (username lookup)
                clean_username = clean_seller_identifier(escrow_data["seller_identifier"]).lower()
                existing_seller_stmt = select(User).where(User.username == clean_username)  # type: ignore
                existing_seller_result = await session.execute(existing_seller_stmt)  # type: ignore
                existing_seller = existing_seller_result.scalar_one_or_none()
                
                if existing_seller:
                    # OPTION 3 FIX: Link to existing user and populate seller_email
                    new_escrow.seller_id = existing_seller.id  # type: ignore
                    if existing_seller.email:
                        new_escrow.seller_email = str(existing_seller.email)  # type: ignore
                        logger.info(f"✅ SELLER_EMAIL_POPULATED: Escrow {escrow_id_to_use} seller_email={existing_seller.email} from onboarded user @{clean_username}")
                    
                    # Notify already-registered seller about new escrow
                    user_username = str(user.username) if user.username is not None else ""
                    user_first_name = str(user.first_name) if user.first_name is not None else ""
                    user_id_str = str(user.id) if user.id is not None else "0"
                    buyer_name = user_username or user_first_name or f"User {user_id_str}"
                    
                    seller_id_val = existing_seller.id
                    if isinstance(seller_id_val, int):
                        seller_id = seller_id_val
                    elif seller_id_val is not None:
                        try:
                            seller_id = int(str(seller_id_val))
                        except (ValueError, TypeError):
                            seller_id = 0
                    else:
                        seller_id = 0
                    seller_username = str(existing_seller.username) if existing_seller.username is not None else ""
                    seller_email = str(existing_seller.email) if existing_seller.email is not None else ""
                    escrow_amount = Decimal(str(new_escrow.amount)) if new_escrow.amount is not None else Decimal("0")
                    escrow_currency = str(new_escrow.currency) if new_escrow.currency is not None else "USD"
                    
                    if escrow_id_to_use is not None:
                        await _notify_registered_seller_new_escrow(
                            seller_id=seller_id,
                            seller_username=seller_username,
                            seller_email=seller_email,
                            escrow_id=escrow_id_to_use,
                            buyer_name=buyer_name,
                            amount=escrow_amount,
                            currency=escrow_currency
                        )
                else:
                    # Use typed contact fields for non-existent users
                    new_escrow.seller_contact_type = "username"  # type: ignore
                    new_escrow.seller_contact_value = clean_username  # type: ignore
                    new_escrow.seller_contact_display = clean_username  # type: ignore
                    
            elif escrow_data["seller_type"] == "phone":
                # Check if seller already exists in database using async query  # type: ignore
                existing_seller_stmt = select(User).where(User.phone == clean_seller_identifier(escrow_data["seller_identifier"]))  # type: ignore
                existing_seller_result = await session.execute(existing_seller_stmt)  # type: ignore
                existing_seller = existing_seller_result.scalar_one_or_none()
                if existing_seller:
                    new_escrow.seller_id = existing_seller.id  # type: ignore  # Link to existing user
                    
                    # Notify already-registered seller about new escrow (ensure proper types from Column)
                    # Convert Column types to primitives
                    user_username = str(user.username) if user.username is not None else ""
                    user_first_name = str(user.first_name) if user.first_name is not None else ""
                    user_id_str = str(user.id) if user.id is not None else "0"
                    buyer_name = user_username or user_first_name or f"User {user_id_str}"
                    
                    # Type cast Column types to primitives for function call
                    seller_id_val = existing_seller.id
                    if isinstance(seller_id_val, int):
                        seller_id = seller_id_val
                    elif seller_id_val is not None:
                        try:
                            seller_id = int(str(seller_id_val))
                        except (ValueError, TypeError):
                            seller_id = 0
                    else:
                        seller_id = 0
                    seller_username = str(existing_seller.username) if existing_seller.username is not None else ""
                    seller_email = str(existing_seller.email) if existing_seller.email is not None else ""
                    escrow_amount = Decimal(str(new_escrow.amount)) if new_escrow.amount is not None else Decimal("0")
                    escrow_currency = str(new_escrow.currency) if new_escrow.currency is not None else "USD"
                    
                    # Only notify if we have a valid escrow_id_to_use
                    if escrow_id_to_use is not None:
                        await _notify_registered_seller_new_escrow(
                            seller_id=seller_id,
                            seller_username=seller_username,
                            seller_email=seller_email,
                            escrow_id=escrow_id_to_use,
                            buyer_name=buyer_name,
                            amount=escrow_amount,
                            currency=escrow_currency
                        )
                else:
                    # Use typed contact fields for non-existent users
                    new_escrow.seller_contact_type = "phone"  # type: ignore
                    new_escrow.seller_contact_value = clean_seller_identifier(escrow_data["seller_identifier"])  # type: ignore  # Normalized (E.164)
                    new_escrow.seller_contact_display = clean_seller_identifier(escrow_data["seller_identifier"])  # type: ignore  # UI display

            # Generate invitation token
            from services.seller_invitation import SellerInvitationService

            setattr(
                new_escrow,
                "invitation_token",
                SellerInvitationService.generate_invitation_token(),
            )
            setattr(
                new_escrow,
                "invitation_expires_at",
                datetime.now(timezone.utc) + timedelta(days=7),
            )

            # CRITICAL VALIDATION: Prevent orphaned escrows without seller assignment (NGN Bank Payment Flow)
            # This mirrors the validation added to crypto payment flow to ensure complete coverage
            seller_validation_passed = False
            seller_info_summary = "No seller information"
            
            # Check that seller information was properly collected and assigned
            if not escrow_data.get("seller_type") or not escrow_data.get("seller_identifier"):
                logger.error(f"❌ NGN VALIDATION FAILED: Escrow {escrow_id_to_use} missing seller information in escrow_data")
                logger.error(f"   escrow_data seller_type: {escrow_data.get('seller_type')}")
                logger.error(f"   escrow_data seller_identifier: {escrow_data.get('seller_identifier')}")
                await session.rollback()
                raise ValueError("Cannot create NGN escrow without seller information")
            
            # Validate that seller information was actually assigned to the escrow record
            if new_escrow.seller_id is None and new_escrow.seller_email is None and not getattr(new_escrow, 'seller_phone', None):
                logger.error(f"❌ NGN VALIDATION FAILED: Escrow {escrow_id_to_use} has no seller_id, seller_email, or seller_phone assigned")
                logger.error(f"   new_escrow.seller_id: {new_escrow.seller_id}")
                logger.error(f"   new_escrow.seller_email: {new_escrow.seller_email}")
                logger.error(f"   new_escrow.seller_phone: {getattr(new_escrow, 'seller_phone', None)}")
                await session.rollback()
                raise ValueError(f"NGN escrow validation failed: No seller contact information assigned to escrow {escrow_id_to_use}")
            
            # Validation passed - log successful seller assignment
            if new_escrow.seller_id is not None:
                seller_info_summary = f"seller_id={new_escrow.seller_id}"
                seller_validation_passed = True
            elif new_escrow.seller_email is not None:
                seller_info_summary = f"seller_email={new_escrow.seller_email}"
                seller_validation_passed = True
            elif getattr(new_escrow, 'seller_phone', None):
                seller_info_summary = f"seller_phone={getattr(new_escrow, 'seller_phone', None)}"
                seller_validation_passed = True
                
            logger.info(f"✅ NGN VALIDATION PASSED: Escrow {escrow_id_to_use} has valid seller assignment: {seller_info_summary}")
            logger.info(f"🎯 ORPHANED ESCROW PREVENTION (NGN): Validation ensures buyer payment will have assigned seller")

            session.add(new_escrow)
            await session.flush()  # Get database ID

            escrow_id = new_escrow.escrow_id
            escrow_db_id = new_escrow.id

            # Store escrow info in context
            escrow_data["database_escrow_id"] = escrow_db_id
            escrow_data["escrow_id"] = escrow_id

            await session.commit()

            # Create Fincra payment link with escrow reference
            payment_link_result = await fincra.create_payment_link(
                amount_ngn=ngn_amount,
                user_id=int(getattr(user, "id", 0)),
                escrow_id=str(escrow_id),  # Pass escrow ID for webhook reference
                purpose="escrow_payment",
            )
            
            # CRITICAL FIX: Store payment reference for webhook matching
            if payment_link_result and payment_link_result.get("payment_reference"):
                # Note: payment_reference stored in transaction extra_data
                await session.commit()
                logger.info(f"Stored payment reference {payment_link_result['payment_reference']} for escrow {escrow_id}")

        if not payment_link_result:
            await safe_edit_message_text(
                query,  # type: ignore
                "❌ Unable to generate payment link. Please try crypto payment.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("⬅️ Back", callback_data="back_to_payment")]]
                ),
            )
            return CONV_END

        # Store payment details
        context.user_data["escrow_data"].update(
            {
                "payment_method": "ngn_bank",
                "ngn_amount": ngn_amount,
                "ngn_rate": ngn_rate,
                "payment_link_result": payment_link_result,
            }
        )

        # Open Fincra mini web immediately using web app
        payment_url = payment_link_result["payment_link"]

        # Use web app to open payment directly
        from telegram import WebAppInfo

        keyboard = [
            [
                InlineKeyboardButton(
                    "💳 Pay ₦{:,.0f}".format(ngn_amount),
                    web_app=WebAppInfo(url=payment_url),
                )
            ],
            [
                InlineKeyboardButton(
                    "🔄 Different Payment", callback_data="back_to_payment"
                )
            ],
            [InlineKeyboardButton("❌ Cancel Trade", callback_data="cancel_escrow")],
        ]

        # Calculate fee breakdown for display
        escrow_base = Decimal(str(escrow_amount))
        platform_fee = total_with_fee - escrow_base
        
        # CRITICAL FIX: Use plain text to avoid MarkdownV2 parsing errors
        text = f"""💳 ₦ Bank Transfer Ready

📦 Escrow: ${escrow_base:.2f}
💸 Platform Fee: ${platform_fee:.2f}
💰 Total Payment: ${total_with_fee:.2f} (₦{ngn_amount:,.0f})

Rate: ₦{ngn_rate:.0f}/USD

Tap "Pay" to open secure payment page"""

        await safe_edit_message_text(
            query, text, reply_markup=InlineKeyboardMarkup(keyboard)  # type: ignore
        )
        return EscrowStates.PAYMENT_PROCESSING

    except Exception as e:
        logger.error(f"Error setting up NGN payment: {e}", exc_info=True)
        await safe_edit_message_text(
            query,  # type: ignore
            f"❌ Error setting up bank transfer: {str(e)[:100]}... Please try crypto payment.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Back", callback_data="back_to_payment")]]
            ),
        )
        return CONV_END

async def handle_wallet_payment(
    query, context: ContextTypes.DEFAULT_TYPE, total_amount
) -> int:  # type: ignore
    """Handle payment from user's wallet balance"""
    try:
        async with async_managed_session() as session:
            # Get user's wallet balance
            user_id = safe_get_user_id(query)
            if not user_id:
                await safe_edit_message_text(
                    query,  # type: ignore
                    "❌ Unable to identify user. Please try again.",
                    reply_markup=InlineKeyboardMarkup(
                        [[InlineKeyboardButton("⬅️ Back", callback_data="back_to_payment")]]
                    ),
                )
                return CONV_END

            stmt = select(User).where(User.telegram_id == user_id)
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()
            if not user:
                await safe_edit_message_text(
                    query,  # type: ignore
                    "❌ User not found. Please try again.",
                    reply_markup=InlineKeyboardMarkup(
                        [[InlineKeyboardButton("⬅️ Back", callback_data="back_to_payment")]]
                    ),
                )
                return CONV_END

            # Get wallet balances
            # Get USD wallet for payment
            stmt = select(Wallet).where(Wallet.user_id == user.id, Wallet.currency == "USD")
            result = await session.execute(stmt)
            usd_wallet = result.scalar_one_or_none()
        wallets = [usd_wallet] if usd_wallet else []

        # Calculate total USD value
        from services.crypto import CryptoServiceAtomic

        crypto_service = CryptoServiceAtomic()
        rates = await crypto_service.get_crypto_rates()

        total_usd = Decimal("0")
        for wallet in wallets:
            try:
                # Safely get values from SQLAlchemy instance
                balance_val = as_decimal(getattr(wallet, "balance", None))
                currency_str = str(getattr(wallet, "currency", "USD"))

                if balance_val > 0:
                    rate = as_decimal(rates.get(currency_str, 1.0))
                    wallet_usd = balance_val * rate
                    total_usd += wallet_usd
            except (ValueError, TypeError, AttributeError):
                continue

        total_usd = total_usd.quantize(Decimal("0.01"))

        # Enhanced: Use WalletValidator for robust balance validation
        is_valid, error_message = await WalletValidator.validate_sufficient_balance(  # type: ignore
            user_id=int(user.id),  # type: ignore
            required_amount=total_amount,
            currency="USD",
            session=session,
            include_frozen=False,
            purpose="wallet payment"
        )

        if not is_valid:
            # Create branded error message with detailed balance info
            shortfall = max(total_amount - total_usd, Decimal("0"))
            error_header = BrandingUtils.make_header("Payment Error")
            error_footer = BrandingUtils.make_trust_footer()
            
            text = f"""{error_header}

❌ Insufficient Balance

💰 Required: ${total_amount:.2f} USD
💳 Available: ${total_usd:.2f} USD  
📉 Shortage: ${shortfall:.2f} USD

Please add funds to your wallet before proceeding.

{error_footer}"""

            keyboard = [
                [
                    InlineKeyboardButton(
                        "💎 Add Funds", callback_data="escrow_add_funds"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 Choose Different Payment", callback_data="back_to_payment"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "❌ Cancel Trade", callback_data="cancel_escrow"
                    )
                ],
            ]

            await safe_edit_message_text(
                query, text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard)  # type: ignore
            )
        else:
            # Sufficient funds - show confirmation first
            escrow_data = (
                context.user_data.get("escrow_data", {}) if context.user_data else {}
            )
            escrow_data["seller_identifier"]
            amount = escrow_data["amount"]
            escrow_data["description"]
            escrow_data["delivery_hours"]

            # Calculate dynamic fee percentage
            amount_decimal = Decimal(str(amount))
            fee_amount = total_amount - amount_decimal
            (
                (fee_amount / amount_decimal) * 100 if amount_decimal > 0 else 0
            )

            text = f"""💰 Wallet Payment

Pay ${total_amount:.2f} from your wallet
Balance: ${total_usd:.2f} USD

Confirm payment?"""

            keyboard = [
                [
                    InlineKeyboardButton(
                        "✅ Confirm Payment", callback_data="confirm_wallet_payment"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "⬅️ Back to Payment Methods", callback_data="back_to_payment"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "❌ Cancel Trade", callback_data="cancel_escrow"
                    )
                ],
            ]

            await safe_edit_message_text(
                query, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard)  # type: ignore
            )
            return EscrowStates.PAYMENT_METHOD

    except Exception as e:
        logger.error(f"Error checking wallet balance: {e}")
        await safe_edit_message_text(
            query,  # type: ignore
            "❌ Error checking wallet balance. Please try another payment method.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Back", callback_data="back_to_payment")]]
            ),
        )
        return CONV_END

async def process_immediate_wallet_payment(query, context, user, total_amount, session, retry_count=0, max_retries=3) -> int:  # type: ignore
    """Process immediate wallet payment and create escrow with retry mechanism"""
    # Local imports for LSP (already imported at top but LSP needs them in function scope)
    from datetime import datetime, timedelta, timezone
    from config import Config
    
    try:
        # Generate idempotency key to prevent duplicate transactions
        import hashlib
        import json

        idempotency_data = {
            "user_id": user.telegram_id,
            "amount": str(context.user_data.get('escrow_data', {}).get('amount', '')),
            "seller": context.user_data.get('escrow_data', {}).get('seller_identifier', ''),
            "timestamp": int(datetime.now(timezone.utc).timestamp())
        }
        idempotency_key = hashlib.sha256(
            json.dumps(idempotency_data, sort_keys=True).encode()
        ).hexdigest()[:16]
        
        # CRITICAL FIX: Robust duplicate detection with row-level locking to prevent double-wallet-debits
        # while allowing legitimate cancel-and-retry attempts
        #
        # APPROACH:
        # 1. Check for recent escrows with same buyer_id + amount within last 30 seconds
        # 2. Use SELECT FOR UPDATE to lock the row and get committed status
        # 3. Only reuse if status is EXACTLY 'payment_pending' (not cancelled/refunded/etc)
        # 4. If cancelled/refunded/etc, ignore it and create new escrow (allow retry)
        #
        # This prevents:
        # - Double-wallet-debits from rapid double-clicks (reuses payment_pending escrows)
        # - Blocking legitimate retries after cancellation (ignores terminal states)
        # - Race conditions (row locking ensures we see committed status)
        escrow_data = context.user_data.get('escrow_data', {})
        is_payment_update = escrow_data.get("existing_escrow_id") or escrow_data.get("early_escrow_id")
        
        if not is_payment_update:
            # Check for potential duplicates with row locking to see committed status
            thirty_seconds_ago = datetime.now(timezone.utc) - timedelta(seconds=30)
            stmt = select(Escrow).where(
                Escrow.buyer_id == user.id,
                Escrow.amount == Decimal(str(escrow_data.get('amount', 0))),
                Escrow.created_at >= thirty_seconds_ago
            ).with_for_update()  # Lock row to see committed status
            
            result = await session.execute(stmt)
            potential_duplicates = result.scalars().all()
            
            # Find the first payment_pending escrow (if any)
            existing_payment_pending = None
            for esc in potential_duplicates:
                if esc.status == 'payment_pending':
                    existing_payment_pending = esc
                    break
                # Ignore cancelled/refunded/expired/completed escrows - allow user to retry
                logger.info(f"🔄 DUPLICATE_CHECK: Ignoring {esc.status} escrow {esc.escrow_id} - allowing retry")
            
            if existing_payment_pending and retry_count == 0:
                logger.warning(f"⚠️ DUPLICATE_DETECTED: Reusing payment_pending escrow {existing_payment_pending.escrow_id} to prevent double-wallet-debit")
                # Reuse the existing payment_pending escrow to prevent double-processing
                context.user_data["escrow_data"]["escrow_id"] = existing_payment_pending.utid or existing_payment_pending.escrow_id
                context.user_data["escrow_data"]["real_escrow_id"] = existing_payment_pending.utid or existing_payment_pending.escrow_id
                return await show_payment_success_for_existing_escrow(query, context, existing_payment_pending)
        else:
            logger.info(f"💳 PAYMENT_UPDATE: Skipping duplicate detection for existing escrow {is_payment_update}")
        
        # Create the escrow record first
        escrow_data = context.user_data["escrow_data"]

        # Get fee split data with correct defaults based on split option
        fee_split_option = escrow_data.get("fee_split_option", "split")
        amount = Decimal(str(escrow_data["amount"]))

        # Apply correct fee defaults based on split option using Config fee percentage
        default_fee = get_default_fee(amount)
        half_fee = (default_fee / Decimal("2")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        
        if fee_split_option == "buyer_pays":
            buyer_fee = Decimal(
                str(escrow_data.get("buyer_fee", default_fee))
            )
            seller_fee = Decimal(str(escrow_data.get("seller_fee", Decimal("0"))))
        elif fee_split_option == "seller_pays":
            buyer_fee = Decimal(str(escrow_data.get("buyer_fee", Decimal("0"))))
            seller_fee = Decimal(
                str(escrow_data.get("seller_fee", default_fee))
            )
        else:  # split
            buyer_fee = Decimal(str(escrow_data.get("buyer_fee", half_fee)))
            seller_fee = Decimal(str(escrow_data.get("seller_fee", half_fee)))

        # CRITICAL FIX: Use existing escrow ID if available (from pay_escrow handler)
        # Only generate new ID for new trades
        existing_escrow_id = escrow_data.get("existing_escrow_id") or escrow_data.get("early_escrow_id")  # Check if pay_escrow handler stored existing ID
        
        if existing_escrow_id:
            # User clicked "Pay Now" from existing trade - reuse that ID
            escrow_id_to_use = existing_escrow_id
            logger.info(f"♻️ REUSING_EXISTING_ID: {escrow_id_to_use} (from existing payment_pending trade)")
            
            # CRITICAL FIX: Load description and delivery_hours from database when reusing existing trade
            existing_trade_stmt = select(Escrow).where(Escrow.escrow_id == existing_escrow_id)
            existing_trade_result = await session.execute(existing_trade_stmt)
            existing_trade = existing_trade_result.scalar_one_or_none()
            
            if existing_trade:
                # Populate missing fields from database
                if "description" not in escrow_data:
                    escrow_data["description"] = existing_trade.description or "Goods and services"
                if "delivery_hours" not in escrow_data:
                    # Calculate delivery_hours from delivery_deadline if available
                    if existing_trade.delivery_deadline:
                        time_diff = existing_trade.delivery_deadline - datetime.now(timezone.utc)
                        escrow_data["delivery_hours"] = max(1, int(time_diff.total_seconds() / 3600))
                    else:
                        escrow_data["delivery_hours"] = 72  # Default
                logger.info(f"✅ LOADED_MISSING_FIELDS: description='{escrow_data['description']}', delivery_hours={escrow_data['delivery_hours']}")
        else:
            # New trade creation - generate fresh ID
            from utils.universal_id_generator import UniversalIDGenerator
            escrow_id_to_use = UniversalIDGenerator.generate_escrow_id()
            logger.info(f"🆔 NEW_ID_GENERATED: {escrow_id_to_use} (fresh ID for new trade)")
        
        escrow_data["unified_escrow_id"] = escrow_id_to_use
        
        # DELIVERY TIMING: Store delivery_hours for later use when payment is confirmed
        # Delivery deadline will be set AFTER payment confirmation, not at creation
        delivery_hours = escrow_data.get("delivery_hours", 72)  # Default 72 hours if not specified
        current_time = datetime.now(timezone.utc)
        
        # ===================================================================
        # ORCHESTRATOR MIGRATION: Use EscrowOrchestrator for wallet payments
        # ===================================================================
        
        from services.escrow_orchestrator import get_escrow_orchestrator, EscrowCreationRequest
        
        # Prepare seller information for orchestrator
        seller_id = None
        seller_contact_value = None
        seller_contact_display = None
        
        if escrow_data["seller_type"] == "username":
            # Check if seller already exists in database (case-insensitive)
            from sqlalchemy import func
            stmt = select(User).where(
                func.lower(User.username) == func.lower(clean_seller_identifier(escrow_data["seller_identifier"]))
            )
            result = await session.execute(stmt)
            existing_seller = result.scalar_one_or_none()
            if existing_seller:
                seller_id = existing_seller.id
            # ALWAYS set seller_contact_value and seller_contact_display, even when seller exists
            seller_contact_value = clean_seller_identifier(escrow_data["seller_identifier"])  # Normalized (no @)
            seller_contact_display = f"@{clean_seller_identifier(escrow_data['seller_identifier'])}"  # UI display
        elif escrow_data["seller_type"] == "email":
            # Check if seller already exists in database (case-insensitive)
            from sqlalchemy import func
            stmt = select(User).where(
                func.lower(User.email) == func.lower(clean_seller_identifier(escrow_data["seller_identifier"]))
            )
            result = await session.execute(stmt)
            existing_seller = result.scalar_one_or_none()
            if existing_seller:
                seller_id = existing_seller.id
            # ALWAYS set seller_contact_value and seller_contact_display, even when seller exists
            seller_contact_value = clean_seller_identifier(escrow_data["seller_identifier"]).lower()  # Normalized
            seller_contact_display = clean_seller_identifier(escrow_data["seller_identifier"]).lower()  # UI display
        elif escrow_data["seller_type"] == "phone":
            # Check if seller already exists in database
            stmt = select(User).where(
                User.phone == clean_seller_identifier(escrow_data["seller_identifier"])  # type: ignore
            )
            result = await session.execute(stmt)
            existing_seller = result.scalar_one_or_none()
            if existing_seller:
                seller_id = existing_seller.id
            # ALWAYS set seller_contact_value and seller_contact_display, even when seller exists
            seller_contact_value = clean_seller_identifier(escrow_data["seller_identifier"])  # Normalized
            seller_contact_display = clean_seller_identifier(escrow_data["seller_identifier"])  # UI display

        # Create escrow request for orchestrator (wallet payment = immediate confirmation)
        escrow_request = EscrowCreationRequest(
            user_id=int(user.id),
            telegram_id=str(query.from_user.id),
            seller_identifier=clean_seller_identifier(escrow_data["seller_identifier"]),
            seller_type=escrow_data["seller_type"],
            amount=Decimal(str(escrow_data["amount"])),
            currency="USD",
            description=escrow_data["description"],
            expires_in_minutes=Config.SELLER_RESPONSE_TIMEOUT_MINUTES,  # 24 hours for seller to accept (matches crypto payment flow)
            # Extended fields for wallet payment
            fee_amount=buyer_fee + seller_fee,
            total_amount=None,  # Let orchestrator calculate: amount + fee_amount
            fee_split_option=escrow_data.get("fee_split_option", "buyer_pays"),
            payment_method="wallet",
            delivery_hours=delivery_hours,  # Store hours for later use on payment confirmation
            delivery_deadline=None,  # Will be set when payment is confirmed
            auto_release_at=None,  # Will be set when payment is confirmed
            seller_id=seller_id,
            seller_contact_value=seller_contact_value,
            seller_contact_display=seller_contact_display,
            escrow_id=escrow_id_to_use
        )
        
        # Use orchestrator for idempotent escrow creation
        orchestrator = get_escrow_orchestrator()
        creation_response = await orchestrator.create_secure_trade(
            escrow_request,
            idempotency_key=f"wallet_payment_{escrow_id_to_use}",
            session=None  # Let orchestrator create its own async session
        )
        
        # Check orchestrator response
        from services.escrow_orchestrator import EscrowCreationResult
        if creation_response.result != EscrowCreationResult.SUCCESS:
            logger.error(f"❌ WALLET_ORCHESTRATOR_ERROR: {creation_response.message}")
            if creation_response.result == EscrowCreationResult.DUPLICATE_PREVENTED:
                # Handle duplicate gracefully
                logger.warning(f"🔒 WALLET_DUPLICATE_PREVENTED: Using existing escrow {creation_response.existing_escrow_id}")
                escrow_id_to_use = creation_response.existing_escrow_id
            else:
                await session.rollback()
                raise ValueError(f"Wallet escrow creation failed: {creation_response.message}")
        else:
            escrow_id_to_use = creation_response.escrow_id
            logger.info(f"✅ WALLET_ORCHESTRATOR_SUCCESS: Created escrow {escrow_id_to_use}")
        
        # Get the created escrow for additional setup

        new_escrow_stmt = select(Escrow).where(Escrow.escrow_id == escrow_id_to_use)
        escrow_result = await session.execute(new_escrow_stmt)
        new_escrow = escrow_result.scalar_one()
        
        # Set payment confirmed status for wallet payments with validation
        try:
            EscrowStateValidator.validate_and_transition(
                new_escrow,
                EscrowStatus.PAYMENT_CONFIRMED,
                escrow_id_to_use,
                force=False
            )
            current_time = datetime.now(timezone.utc)
            new_escrow.payment_confirmed_at = current_time
            
            # DELIVERY COUNTDOWN: Set delivery_deadline based on payment confirmation time
            # Delivery time starts counting AFTER payment, not at creation
            if new_escrow.pricing_snapshot and 'delivery_hours' in new_escrow.pricing_snapshot:
                delivery_hours = int(new_escrow.pricing_snapshot['delivery_hours'])
                new_escrow.delivery_deadline = current_time + timedelta(hours=delivery_hours)
                new_escrow.auto_release_at = new_escrow.delivery_deadline + timedelta(hours=24)  # 24h grace
                logger.info(f"⏰ DELIVERY_DEADLINE_SET: Escrow {escrow_id_to_use} delivery countdown starts - {delivery_hours}h")
        except StateTransitionError as e:
            logger.error(f"❌ STATE_TRANSITION_ERROR: {e}")
            raise ValueError(f"Invalid state transition for wallet payment: {e}")
        
        # Set seller contact information based on type
        if escrow_data["seller_type"] == "username":
            # Check if seller already exists in database using async query (username lookup)
            clean_username = clean_seller_identifier(escrow_data["seller_identifier"]).lower()
            existing_seller_stmt = select(User).where(User.username == clean_username)  # type: ignore
            existing_seller_result = await session.execute(existing_seller_stmt)  # type: ignore
            existing_seller = existing_seller_result.scalar_one_or_none()
            
            if existing_seller:
                # OPTION 3 FIX: Link to existing user and populate seller_email
                new_escrow.seller_id = existing_seller.id  # type: ignore
                if existing_seller.email:
                    new_escrow.seller_email = str(existing_seller.email)  # type: ignore
                    logger.info(f"✅ SELLER_EMAIL_POPULATED: Escrow {escrow_id_to_use} seller_email={existing_seller.email} from onboarded user @{clean_username}")
                
                # Notify already-registered seller about new escrow
                buyer_name = user.username or user.first_name or f"User {user.id}"
                
                seller_id = int(existing_seller.id) if existing_seller.id is not None else 0
                seller_username = str(existing_seller.username) if existing_seller.username is not None else ""
                seller_email = str(existing_seller.email) if existing_seller.email is not None else ""
                escrow_amount = Decimal(str(new_escrow.amount)) if new_escrow.amount is not None else Decimal("0")
                escrow_currency = str(new_escrow.currency) if new_escrow.currency is not None else "USD"
                
                if escrow_id_to_use is not None:
                    await _notify_registered_seller_new_escrow(
                        seller_id=seller_id,
                        seller_username=seller_username,
                        seller_email=seller_email,
                        escrow_id=escrow_id_to_use,
                        buyer_name=buyer_name,
                        amount=escrow_amount,
                        currency=escrow_currency
                    )
            else:
                # Use typed contact fields for non-existent users
                new_escrow.seller_contact_type = "username"  # type: ignore
                new_escrow.seller_contact_value = clean_username  # type: ignore
                new_escrow.seller_contact_display = clean_username  # type: ignore
                
        elif escrow_data["seller_type"] == "phone":
            # Check if seller already exists in database using async query  # type: ignore
            existing_seller_stmt = select(User).where(User.phone == clean_seller_identifier(escrow_data["seller_identifier"]))  # type: ignore
            existing_seller_result = await session.execute(existing_seller_stmt)
            existing_seller = existing_seller_result.scalar_one_or_none()
            if existing_seller:
                new_escrow.seller_id = existing_seller.id  # type: ignore  # Link to existing user
                
                # Notify already-registered seller about new escrow (ensure proper types from Column)
                buyer_name = user.username or user.first_name or f"User {user.id}"
                
                # Type cast Column types to primitives for function call
                seller_id = int(existing_seller.id) if existing_seller.id is not None else 0
                seller_username = str(existing_seller.username) if existing_seller.username is not None else ""
                seller_email = str(existing_seller.email) if existing_seller.email is not None else ""
                escrow_amount = Decimal(str(new_escrow.amount)) if new_escrow.amount is not None else Decimal("0")
                escrow_currency = str(new_escrow.currency) if new_escrow.currency is not None else "USD"
                
                # Only notify if we have a valid escrow_id_to_use
                if escrow_id_to_use is not None:
                    await _notify_registered_seller_new_escrow(
                        seller_id=seller_id,
                        seller_username=seller_username,
                        seller_email=seller_email,
                        escrow_id=escrow_id_to_use,
                        buyer_name=buyer_name,
                        amount=escrow_amount,
                        currency=escrow_currency
                    )
            else:
                # Use typed contact fields for non-existent users
                new_escrow.seller_contact_type = "phone"  # type: ignore
                new_escrow.seller_contact_value = clean_seller_identifier(escrow_data["seller_identifier"])  # type: ignore  # Normalized (E.164)
                new_escrow.seller_contact_display = clean_seller_identifier(escrow_data["seller_identifier"])  # type: ignore  # UI display

        # Generate invitation token for seller notifications
        from services.seller_invitation import SellerInvitationService

        setattr(
            new_escrow,
            "invitation_token",
            SellerInvitationService.generate_invitation_token(),
        )

        # VALIDATION: Ensure escrow data integrity before saving
        from utils.fee_calculator import FeeCalculator

        is_valid, error_message = FeeCalculator.validate_escrow_data_integrity(
            new_escrow
        )
        if not is_valid:
            logger.error(f"Escrow validation failed: {error_message}")
            await session.rollback()
            raise ValueError(f"Escrow validation failed: {error_message}")

        session.add(new_escrow)
        await session.flush()  # Get the ID

        # Process wallet debit with pessimistic locking
        from utils.financial import FinancialCalculator
        from services.crypto import CryptoServiceAtomic
        from utils.atomic_transactions import locked_wallet_operation

        crypto_service = CryptoServiceAtomic()
        rates_raw = await crypto_service.get_crypto_rates()
        # Convert rates to Decimal for financial accuracy
        rates = {k: as_decimal(v) for k, v in rates_raw.items()}

        # Use locked USD wallet query to prevent race conditions
        wallet_stmt = select(Wallet).where(Wallet.user_id == user.id, Wallet.currency == "USD").with_for_update()
        wallet_result = await session.execute(wallet_stmt)
        usd_wallet = wallet_result.scalar_one_or_none()
        wallets = [usd_wallet] if usd_wallet else []

        success, debit_transactions = FinancialCalculator.process_wallet_debit(
            wallets, total_amount, rates
        )

        if not success:
            await session.rollback()
            await safe_edit_message_text(
                query,  # type: ignore
                "❌ Payment processing failed. Please try again.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("⬅️ Back", callback_data="back_to_payment")]]
                ),
            )
            return CONV_END

        # CRITICAL SECURITY FIX: Apply wallet debits using atomic operations with row locking
        from services.crypto import CryptoServiceAtomic

        for debit in debit_transactions:
            success = await CryptoServiceAtomic.debit_user_wallet_atomic(
                user_id=int(getattr(user, "id", 0)),
                amount=debit["usd_value"],  # Always debit in USD equivalent
                currency="USD",
                escrow_id=int(getattr(new_escrow, "id", 0)),
                transaction_type=TransactionType.WALLET_PAYMENT.value,
                description=f"Trade payment #{str(new_escrow.escrow_id)}: -${debit['usd_value']:.2f}",
                session=session,
            )

            if not success:
                # Check if it's an insufficient balance error
                wallet_check_stmt = select(Wallet).where(
                    Wallet.user_id == user.id,
                    Wallet.currency == "USD"
                )
                wallet_check_result = await session.execute(wallet_check_stmt)
                wallet_check = wallet_check_result.scalar_one_or_none()
                
                if wallet_check and wallet_check.available_balance < debit["usd_value"]:
                    logger.error(
                        f"Insufficient balance for user {user.id}: ${wallet_check.available_balance:.2f} < ${debit['usd_value']:.2f}"
                    )
                    raise ValueError(f"Insufficient balance: You have ${wallet_check.available_balance:.2f} but need ${debit['usd_value']:.2f}")
                
                # Retry with exponential backoff for transient failures
                if retry_count < max_retries:
                    backoff_time = 0.5 * (2 ** retry_count)  # 0.5s, 1s, 2s
                    logger.warning(
                        f"Wallet payment failed for escrow {str(new_escrow.escrow_id)}, retrying ({retry_count + 1}/{max_retries}) after {backoff_time}s"
                    )
                    await asyncio.sleep(backoff_time)
                    await session.rollback()
                    return await process_immediate_wallet_payment(
                        query, context, user, total_amount, session, 
                        retry_count + 1, max_retries
                    )
                else:
                    logger.error(
                        f"SECURITY: Atomic wallet payment failed after {max_retries} retries for escrow {str(new_escrow.escrow_id)}"
                    )
                    raise Exception("Wallet debit failed after multiple retries")

            logger.info(
                f"SECURE: Atomically debited ${debit['usd_value']:.2f} USD equivalent from {debit['currency']} wallet for escrow {str(new_escrow.escrow_id)}"
            )

            # Update the actual wallet balance to reflect the atomic operation
            for wallet in wallets:
                if wallet.id == debit["wallet_id"]:
                    # Refresh wallet balance from database after atomic operation
                    await session.refresh(wallet)
                    logger.info(
                        f"Wallet {wallet.currency} balance updated after atomic payment: {wallet.available_balance}"
                    )
                    break

        # CRITICAL FIX: Create EscrowHolding record for wallet payments
        # This is required for release_funds to work correctly
        holding = EscrowHolding(
            escrow_id=str(new_escrow.escrow_id),
            amount_held=Decimal(str(new_escrow.amount)),  # Escrow amount (not including buyer fee)
            currency="USD",
            status="active",  # Must be "active" for release_funds to find it
            original_amount=Decimal(str(new_escrow.amount))
        )
        session.add(holding)
        logger.info(f"✅ Created EscrowHolding for wallet payment: {new_escrow.escrow_id}, amount: ${new_escrow.amount}")

        # Get buyer info BEFORE committing (while session is still open)
        buyer = user
        
        # Save ALL escrow AND buyer attributes BEFORE expunge (prevent lazy loading errors)
        escrow_db_id = new_escrow.id
        escrow_public_id = str(new_escrow.escrow_id)
        escrow_amount = new_escrow.amount
        escrow_buyer_fee = new_escrow.buyer_fee_amount
        escrow_fee_amount = new_escrow.fee_amount
        escrow_fee_paid_by = new_escrow.fee_split_option
        escrow_payment_confirmed_at = new_escrow.payment_confirmed_at
        escrow_expires_at = new_escrow.expires_at
        escrow_seller_id = new_escrow.seller_id  # Cache for referral invite check
        buyer_id = buyer.id  # CRITICAL: Cache buyer.id before expunge to prevent greenlet_spawn errors
        buyer_referral_code = buyer.referral_code  # Cache for referral invite
        
        # CRITICAL FIX: Expunge new_escrow to detach it from session before commit
        # This prevents "greenlet_spawn has not been called" errors when accessing
        # escrow attributes after session closes in send_offer_to_seller_by_escrow()
        session.expunge(new_escrow)
        
        await session.commit()

        # Get seller identifier from context for admin notifications
        escrow_data = context.user_data.get("escrow_data", {})
        seller_identifier_raw = escrow_data.get("seller_identifier", "Unknown")
        seller_identifier_clean = seller_identifier_raw.replace('\\', '')
        
        # Send offer to seller after wallet payment confirmation
        try:
            success = await send_offer_to_seller_by_escrow(new_escrow)
            if success:
                logger.info(f"✅ Sent offer to seller for wallet-paid escrow {escrow_public_id}")
                
                # Send Telegram group notification for payment confirmed
                try:
                    buyer_display = f"@{buyer.username}" if buyer.username else buyer.first_name
                    payment_data = {
                        'escrow_id': escrow_public_id,
                        'amount': float(escrow_amount),
                        'payment_method': 'Wallet',
                        'buyer_info': buyer_display,
                        'seller_info': seller_identifier_clean,
                        'confirmed_at': datetime.now(timezone.utc)
                    }
                    admin_notif_service = AdminTradeNotificationService()
                    asyncio.create_task(admin_notif_service.send_group_notification_payment_confirmed(payment_data))
                    # Broadcast to all registered groups
                    from services.group_event_service import group_event_service
                    asyncio.create_task(group_event_service.broadcast_trade_funded(payment_data))
                    logger.info(f"📤 Queued group notification for payment confirmed: {escrow_public_id}")
                except Exception as notif_err:
                    logger.error(f"❌ Failed to queue payment confirmed group notification: {notif_err}")
            else:
                logger.error(f"❌ Failed to send offer to seller for wallet-paid escrow {escrow_public_id}")
        except Exception as e:
            logger.error(
                f"Error sending seller offer for wallet escrow {escrow_public_id}: {e}"
            )

        # Show success message to buyer
        escrow_data = context.user_data["escrow_data"]
        seller_identifier = escrow_data["seller_identifier"].replace('\\', '')
        import html
        seller_display = (
            format_username_html(f"@{seller_identifier}", include_link=False)
            if escrow_data["seller_type"] == "username"
            else html.escape(seller_identifier)  # Keep for non-username types
        )

        # Send wallet payment confirmation to buyer via consolidated notification service (bot + email)
        try:
            from services.consolidated_notification_service import ConsolidatedNotificationService, NotificationRequest, NotificationCategory, NotificationPriority
            from utils.referral import ReferralSystem
            
            notification_service = ConsolidatedNotificationService()
            
            # Calculate total amount paid (amount + fees)
            escrow_amount_dec = escrow_amount if escrow_amount else Decimal('0')
            buyer_fee_dec = escrow_buyer_fee if escrow_buyer_fee else Decimal('0')
            total_paid = escrow_amount_dec + buyer_fee_dec
            
            # Check if seller is onboarded (has user account)
            seller_onboarded = escrow_seller_id is not None
            
            # Build referral invite section if seller not onboarded
            referral_section = ""
            keyboard_buttons = []
            
            if not seller_onboarded and buyer_referral_code:
                # Only show referral section if buyer has a referral code
                from urllib.parse import quote
                referral_link = f"https://t.me/{Config.BOT_USERNAME}?start=ref_{buyer_referral_code}"
                share_text = quote("Hey! Join me on Lockbay for secure trades 🛡️")
                
                referral_section = f"""

⚠️ Seller not on platform

Tap Share Invite below"""
                # Add Share Invite button with URL-encoded parameters
                keyboard_buttons.append([{"text": "📤 Share Invite", "url": f"https://t.me/share/url?url={quote(referral_link)}&text={share_text}"}])
            
            # Create notification message (mobile-optimized)
            message = f"""✅ Payment Sent

#{escrow_public_id[-8:]} • {format_money(escrow_amount_dec, 'USD')}
Paid: {format_money(total_paid, 'USD')} (inc. {format_money(buyer_fee_dec, 'USD')} fee)
To: {seller_display}{referral_section}

⏰ Awaiting seller (24h)
🛡️ You control when seller gets paid"""

            # Build keyboard
            keyboard_buttons.extend([
                [{"text": "📋 View Trade", "callback_data": f"view_trade_{escrow_public_id}"}],
                [{"text": "🏠 Main Menu", "callback_data": "main_menu"}]
            ])

            # Send notification to buyer (both bot and email)
            request = NotificationRequest(
                user_id=buyer_id,  # Use cached buyer_id to prevent greenlet_spawn errors
                category=NotificationCategory.ESCROW_UPDATES,
                priority=NotificationPriority.HIGH,
                title="✅ Payment Sent",
                message=message,
                template_data={
                    "escrow_id": escrow_public_id,
                    "amount": decimal_to_string(escrow_amount_dec, precision=2),
                    "total_paid": decimal_to_string(total_paid, precision=2),
                    "buyer_fee": decimal_to_string(buyer_fee_dec, precision=2),
                    "seller": seller_display,
                    "parse_mode": "HTML",  # FIXED: Use HTML to match format_username_html() output
                    "keyboard": keyboard_buttons
                },
                broadcast_mode=True  # CRITICAL: Dual-channel delivery (Telegram + Email)
            )
            
            result = await notification_service.send_notification(request)
            logger.info(f"✅ Wallet payment confirmation sent to buyer {buyer_id} via {len(result)} channels (seller_onboarded={seller_onboarded})")
        except Exception as e:
            logger.error(f"Failed to send wallet payment confirmation to buyer: {e}")

        # Get fee information for display
        fee_amount = Decimal(str(escrow_fee_amount)) if escrow_fee_amount is not None else Decimal("0.0")
        total_paid = Decimal(str(escrow_amount or 0)) + fee_amount
        fee_paid_by = escrow_fee_paid_by
        
        # Build fee display based on who paid
        fee_info = ""
        if fee_paid_by in ("buyer", "buyer_pays") and fee_amount > 0:
            fee_info = f"\n💸 You paid: ${total_paid:.2f} (inc. ${fee_amount:.2f} fee)"
        elif fee_paid_by == "split" and fee_amount > 0:
            split_fee = fee_amount / 2
            fee_info = f"\n💸 You paid: ${Decimal(str(escrow_amount)) + split_fee:.2f} (inc. ${split_fee:.2f} fee)"
        elif fee_paid_by in ("seller", "seller_pays"):
            fee_info = f"\n💸 You paid: ${Decimal(str(escrow_amount)):.2f}"
        
        # Calculate dynamic seller acceptance time remaining
        payment_confirmed_at = escrow_payment_confirmed_at
        expires_at = escrow_expires_at
        
        # Calculate seller acceptance deadline - ALWAYS recalculate from payment_confirmed_at if available
        # This handles cases where expires_at wasn't properly updated after payment confirmation
        if payment_confirmed_at:
            seller_timeout_minutes = getattr(Config, 'SELLER_RESPONSE_TIMEOUT_MINUTES', 1440)  # 24 hours
            calculated_expires = payment_confirmed_at + timedelta(minutes=seller_timeout_minutes)
            # Use the later of: stored expires_at or calculated from payment_confirmed_at
            if not expires_at or calculated_expires > expires_at:
                expires_at = calculated_expires
        
        # Format time remaining
        seller_time_msg = "⏰ Seller has 24h to accept"  # Fallback
        if expires_at:
            current_time = datetime.now(timezone.utc)
            expires_at_aware = expires_at.replace(tzinfo=timezone.utc) if expires_at.tzinfo is None else expires_at
            time_remaining = expires_at_aware - current_time
            
            if time_remaining.total_seconds() > 0:
                total_minutes = int(time_remaining.total_seconds() / 60)
                hours = total_minutes // 60
                minutes = total_minutes % 60
                
                if hours > 0 and minutes > 0:
                    seller_time_msg = f"⏰ Seller has {hours}h {minutes}m left to accept"
                elif hours > 0:
                    seller_time_msg = f"⏰ Seller has {hours}h left to accept"
                else:
                    seller_time_msg = f"⏰ Seller has {minutes}m left to accept"
        
        # Clean seller display for plain text (no markdown escaping)
        seller_display_clean = clean_seller_identifier(seller_identifier)
        if escrow_data["seller_type"] == "username":
            seller_display_clean = f"@{seller_display_clean}"
        
        text = f"""✅ Payment Complete

📤 Offer sent to: {seller_display_clean}
💰 Trade: ${Decimal(str(escrow_amount)):.2f}{fee_info}
🆔 ID: {escrow_public_id}

{seller_time_msg}
🛡️ You control when seller gets paid"""

        keyboard = [
            # CRITICAL FIX: Only show contact button when trade is active, not during seller acceptance phase
            [
                InlineKeyboardButton(
                    "📋 View Trade", callback_data=f"view_trade_{escrow_public_id}"
                )
            ],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")],
        ]

        await safe_edit_message_text(
            query, text, parse_mode=None, reply_markup=InlineKeyboardMarkup(keyboard)  # type: ignore
        )

        # Clear escrow data
        context.user_data.pop("escrow_data", None)

    except Exception as e:
        logger.error(f"Error processing wallet payment: {e}")
        await session.rollback()
        
        # Provide specific error message based on failure type
        error_message = "❌ Wallet Payment Failed\n\n"
        keyboard = [[InlineKeyboardButton("📞 Support", callback_data="contact_support")]]
        
        if "Insufficient balance" in str(e) or "balance" in str(e).lower():
            error_message += "Not enough funds in your wallet.\n\n⚡ Add funds or use another payment method."
            keyboard = [
                [InlineKeyboardButton("💰 Add Funds", callback_data="escrow_add_funds")],
                [InlineKeyboardButton("⬅️ Other Methods", callback_data="back_to_payment")]
            ]
        elif "timeout" in str(e).lower() or "deadlock" in str(e).lower():
            error_message += "System is busy right now.\n\n⚡ Wait a moment and try again."
            keyboard = [
                [InlineKeyboardButton("🔄 Try Again", callback_data="pay_wallet")],
                [InlineKeyboardButton("⬅️ Other Methods", callback_data="back_to_payment")]
            ]
        elif "retry" in str(e).lower() or "retries" in str(e).lower():
            error_message += "Multiple payment attempts failed.\n\n⚡ Our team has been notified. Try another method."
            keyboard = [
                [InlineKeyboardButton("⬅️ Other Methods", callback_data="back_to_payment")],
                [InlineKeyboardButton("📞 Support", callback_data="contact_support")]
            ]
        elif "duplicate" in str(e).lower():
            error_message = "⚠️ Payment Already Processed?\n\nThis payment may have succeeded.\n\n⚡ Check your trades to confirm."
            keyboard = [
                [InlineKeyboardButton("📋 My Trades", callback_data="trades_messages_hub")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
            ]
        else:
            error_message += "Couldn't complete wallet payment.\n\n⚡ Try another payment method or contact support."
            keyboard = [
                [InlineKeyboardButton("⬅️ Other Methods", callback_data="back_to_payment")],
                [InlineKeyboardButton("📞 Support", callback_data="contact_support")]
            ]
        
        await safe_edit_message_text(
            query,  # type: ignore
            error_message,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

async def show_payment_success_for_existing_escrow(query, context, escrow) -> int:
    """Show success message for an existing escrow (duplicate prevention)"""
    try:
        escrow_data = context.user_data.get("escrow_data", {})
        seller_identifier = escrow_data.get("seller_identifier", "Unknown").replace('\\', '')
        import html
        seller_display = (
            format_username_html(f"@{seller_identifier}", include_link=False)
            if escrow_data.get("seller_type") == "username"
            else html.escape(seller_identifier)  # Keep for non-username types
        )
        
        # Get fee information for existing trade
        fee_amount_raw = getattr(escrow, 'fee_amount', None)
        fee_amount = Decimal(str(fee_amount_raw)) if fee_amount_raw is not None else Decimal("0.0")
        total_paid = Decimal(str(getattr(escrow, 'amount', 0) or 0)) + fee_amount
        fee_paid_by = getattr(escrow, 'fee_split_option', 'buyer_pays')
        
        # Build fee display based on who paid
        fee_info = ""
        if fee_paid_by in ("buyer", "buyer_pays") and fee_amount > 0:
            fee_info = f"\n💸 You paid: ${total_paid:.2f} (inc. ${fee_amount:.2f} fee)"
        elif fee_paid_by == "split" and fee_amount > 0:
            split_fee = fee_amount / 2
            fee_info = f"\n💸 You paid: ${Decimal(str(getattr(escrow, 'amount', 0))) + split_fee:.2f} (inc. ${split_fee:.2f} fee)"
        elif fee_paid_by in ("seller", "seller_pays"):
            fee_info = f"\n💸 You paid: ${Decimal(str(getattr(escrow, 'amount', 0))):.2f}"
        
        # Calculate dynamic seller acceptance time remaining
        from datetime import datetime, timezone, timedelta
        from config import Config as AppConfig
        
        payment_confirmed_at = getattr(escrow, 'payment_confirmed_at', None)
        expires_at = getattr(escrow, 'expires_at', None)
        
        # Calculate seller acceptance deadline - ALWAYS recalculate from payment_confirmed_at
        if payment_confirmed_at:
            seller_timeout_minutes = getattr(AppConfig, 'SELLER_RESPONSE_TIMEOUT_MINUTES', 1440)  # 24 hours
            calculated_expires = payment_confirmed_at + timedelta(minutes=seller_timeout_minutes)
            if not expires_at or calculated_expires > expires_at:
                expires_at = calculated_expires
        
        # Format time remaining
        seller_time_msg = "⏰ Seller has 24h to accept"  # Fallback
        if expires_at:
            current_time = datetime.now(timezone.utc)
            expires_at_aware = expires_at.replace(tzinfo=timezone.utc) if expires_at.tzinfo is None else expires_at
            time_remaining = expires_at_aware - current_time
            
            if time_remaining.total_seconds() > 0:
                total_minutes = int(time_remaining.total_seconds() / 60)
                hours = total_minutes // 60
                minutes = total_minutes % 60
                
                if hours > 0 and minutes > 0:
                    seller_time_msg = f"⏰ Seller has {hours}h {minutes}m left to accept"
                elif hours > 0:
                    seller_time_msg = f"⏰ Seller has {hours}h left to accept"
                else:
                    seller_time_msg = f"⏰ Seller has {minutes}m left to accept"
            
        text = f"""✅ Trade Already Created

📤 Offer was sent to: {seller_display}
💰 Trade: ${Decimal(str(getattr(escrow, 'amount', 0))):.2f}{fee_info}
🆔 ID: {str(escrow.escrow_id)}

{seller_time_msg}"""

        keyboard = [
            [
                InlineKeyboardButton(
                    "📋 View Trade", callback_data=f"view_trade_{escrow.id}"
                )
            ],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")],
        ]

        await safe_edit_message_text(
            query, text, reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
        # Clear escrow data
        context.user_data.pop("escrow_data", None)
        return CONV_END
        
    except Exception as e:
        logger.error(f"Error showing existing escrow success: {e}")
        return CONV_END

async def send_offer_to_seller_by_escrow(escrow) -> int:
    """Send offer notification to seller using dual-channel delivery (Telegram + Email)"""
    try:
        from services.consolidated_notification_service import (
            ConsolidatedNotificationService, 
            NotificationRequest, 
            NotificationCategory, 
            NotificationPriority
        )
        
        notification_service = ConsolidatedNotificationService()

        # Get buyer info for personalized message
        async with async_managed_session() as user_session:
            stmt = select(User).where(User.id == escrow.buyer_id)
            result = await user_session.execute(stmt)
            buyer = result.scalar_one_or_none()
            if buyer:
                buyer_name = (
                    getattr(buyer, "username", None)
                    or getattr(buyer, "first_name", None)
                    or "Anonymous Buyer"
                )
            else:
                buyer_name = "Anonymous Buyer"

        # Determine seller contact from escrow data using typed contact system
        seller_contact = None
        seller_type = None
        
        if escrow.seller_contact_type == "username" and escrow.seller_contact_value:
            seller_contact = escrow.seller_contact_value
            seller_type = "username"
        elif escrow.seller_contact_type == "email" and escrow.seller_contact_value:
            seller_contact = escrow.seller_contact_value
            seller_type = "email"
        elif escrow.seller_contact_type == "phone" and escrow.seller_contact_value:
            seller_contact = escrow.seller_contact_value
            seller_type = "phone"
        # Fallback to legacy fields if present
        elif hasattr(escrow, 'seller_email') and escrow.seller_email:
            seller_contact = escrow.seller_email
            seller_type = "email"
        elif hasattr(escrow, 'seller_phone') and escrow.seller_phone:
            seller_contact = escrow.seller_phone
            seller_type = "phone"

        if not seller_contact:
            logger.error(f"No seller contact found for escrow {escrow.escrow_id}")
            return False

        # For username type, find the seller's User record for dual-channel notification
        seller_user = None
        seller_user_id = None  # CRITICAL: Cache seller_user_id to prevent detached instance errors
        if seller_type == "username":
            async with async_managed_session() as session:
                from sqlalchemy import func
                username = seller_contact.replace("@", "")
                stmt = select(User).where(func.lower(User.username) == func.lower(username))
                result = await session.execute(stmt)
                seller_user = result.scalar_one_or_none()
                if seller_user:
                    seller_user_id = seller_user.id  # CRITICAL: Cache ID before session closes

        # If seller is a registered user, send dual-channel notification
        if seller_user_id:  # type: ignore[truthy-bool]
            # Calculate amounts for display
            amount = escrow.amount if escrow.amount else Decimal('0.0')
            buyer_fee = Decimal(str(getattr(escrow, 'buyer_fee_amount', 0) or 0))
            seller_fee = Decimal(str(getattr(escrow, 'seller_fee_amount', 0) or 0))
            
            # Build fee info (mobile-optimized)
            fee_text = ""
            if escrow.fee_split_option == "split":
                fee_text = f"Fees split ({format_money(buyer_fee, 'USD')} + {format_money(seller_fee, 'USD')})"
            elif escrow.fee_split_option == "buyer_pays":
                fee_text = f"Buyer pays fee ({format_money(buyer_fee, 'USD')})"
            elif escrow.fee_split_option == "seller_pays":
                fee_text = f"You pay fee: {format_money(seller_fee, 'USD')}"
            
            # Create notification message (mobile-optimized)
            description_short = escrow.description[:40] + '...' if len(escrow.description) > 40 else escrow.description
            escrow_id_display = escrow.escrow_id[-6:] if len(escrow.escrow_id) > 6 else escrow.escrow_id
            
            # Calculate seller's net amount for fee transparency
            seller_net = amount - seller_fee if seller_fee > 0 else amount
            
            message = f"""💰 New Trade Offer

🏷️ ID: #{escrow_id_display}

From: {buyer_name}

Amount: {format_money(amount, 'USD')}
{fee_text}
You receive: {format_money(seller_net, 'USD')}

{description_short}

✅ Secured • Expires 24h"""

            # Send dual-channel notification (Telegram bot + email)
            request = NotificationRequest(
                user_id=seller_user_id,  # type: ignore[arg-type]  # FIXED: Use cached ID instead of seller_user.id
                category=NotificationCategory.ESCROW_UPDATES,
                priority=NotificationPriority.HIGH,
                title="💰 New Trade Offer",
                message=message,
                template_data={
                    "escrow_id": str(escrow.escrow_id),
                    "amount": decimal_to_string(amount, precision=2),
                    "buyer_name": buyer_name,
                    "fee_text": fee_text,
                    "parse_mode": "HTML",
                    "keyboard": [
                        [{"text": "✅ Accept", "callback_data": f"accept_trade:{escrow.escrow_id}"}],
                        [{"text": "❌ Decline", "callback_data": f"decline_trade:{escrow.escrow_id}"}]
                    ]
                },
                broadcast_mode=True  # CRITICAL: Dual-channel delivery (Telegram + Email)
            )
            
            result = await notification_service.send_notification(request)
            logger.info(f"✅ Seller notification sent to user {seller_user_id} via {len(result)} channels for escrow {escrow.escrow_id}")
            return True
        else:
            # Fallback to old single-channel notification for non-registered sellers
            from services.notification_service import NotificationService
            legacy_service = NotificationService()
            
            success = await legacy_service.send_seller_invitation(
                escrow_id=escrow.escrow_id,
                seller_identifier=seller_contact,
                seller_type=seller_type,  # type: ignore[arg-type]
                amount=escrow.amount if escrow.amount else Decimal('0.0'),
            )
            
            if success:
                logger.info(f"✅ Seller invitation sent via legacy service for escrow {escrow.escrow_id}")
            else:
                logger.error(f"❌ Failed to send seller invitation for escrow {escrow.escrow_id}")
            
            return success

    except Exception as e:
        logger.error(f"Error in send_offer_to_seller_by_escrow: {e}", exc_info=True)
        return False

async def send_offer_to_seller(escrow, context) -> int:  # type: ignore
    """Send trade offer notification to seller"""
    try:
        async with async_managed_session() as session:
            # Get buyer info
            stmt = select(User).where(User.id == escrow.buyer_id)
            result = await session.execute(stmt)
            buyer = result.scalar_one_or_none()

            if escrow.seller_contact_type == "username" and escrow.seller_contact_value:
                # Send to Telegram user (remove @ if present, case-insensitive)
                from sqlalchemy import func
                username = escrow.seller_contact_value.replace("@", "")
                stmt = select(User).where(func.lower(User.username) == func.lower(username))
                result = await session.execute(stmt)
                seller = result.scalar_one_or_none()
                
                if seller:
                    logger.info(
                        f"Sending trade offer to @{username} (telegram_id: {seller.telegram_id})"
                    )
                    # Generate fee breakdown text
                    fee_text = ""
                    amount = Decimal(str(getattr(escrow, 'amount', 0) or 0))
                    buyer_fee = Decimal(str(getattr(escrow, 'buyer_fee_amount', None) or 0))
                    seller_fee = Decimal(str(getattr(escrow, 'seller_fee_amount', None) or 0))
                    
                    if escrow.fee_split_option == "split":
                        fee_text = f"\n🎯 You receive: ${amount:.2f}\n💳 Buyer paid: ${amount + buyer_fee:.2f} (includes fees)"
                    elif escrow.fee_split_option == "buyer_pays":
                        fee_text = f"\n💰 Fee: Buyer paid all (${buyer_fee:.2f})"
                    elif escrow.fee_split_option == "seller_pays":
                        fee_text = f"\n💰 Fee: You pay ${seller_fee:.2f}"

                    buyer_username = getattr(buyer, "username", None) if buyer else None
                    buyer_first_name = getattr(buyer, "first_name", None) if buyer else None
                    buyer_display = (
                        f"@{buyer_username}"
                        if buyer_username
                        else (buyer_first_name if buyer_first_name else "Anonymous Buyer")
                    )

                    description_short = escrow.description[:40] + '...' if len(escrow.description) > 40 else escrow.description
                    escrow_id_display = escrow.escrow_id[-6:] if len(escrow.escrow_id) > 6 else escrow.escrow_id
                    
                    # Calculate seller's net amount for transparency
                    seller_net = amount - seller_fee if seller_fee > 0 else amount
                    
                    # Build fee info display
                    if escrow.fee_split_option == "split":
                        fee_display = f"Fees split (${buyer_fee:.2f} + ${seller_fee:.2f})\nYou receive: ${seller_net:.2f} USD"
                    elif escrow.fee_split_option == "buyer_pays":
                        fee_display = f"Buyer pays fee (${buyer_fee:.2f})\nYou receive: ${amount:.2f} USD"
                    elif escrow.fee_split_option == "seller_pays":
                        fee_display = f"You pay fee: ${seller_fee:.2f}\nYou receive: ${seller_net:.2f} USD"
                    else:
                        fee_display = f"You receive: ${seller_net:.2f} USD"
                    
                    text = f"""💰 New Trade Offer

🏷️ ID: #{escrow_id_display}

From: {buyer_display}

Amount: ${amount:.2f} USD
{fee_display}

{description_short}

✅ Secured • Expires 24h"""

                    keyboard = [
                        [
                            InlineKeyboardButton(
                                "✅ Accept",
                                callback_data=f"accept_trade:{escrow.escrow_id}",
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                "❌ Decline", callback_data=f"decline_trade:{escrow.escrow_id}"
                            )
                        ],
                    ]

                    # Send notification via bot
                    try:
                        from telegram import Bot
                        # Config already imported globally
                        if not Config.BOT_TOKEN:
                            raise ValueError("Bot token not configured")
                        bot = Bot(Config.BOT_TOKEN)
                        await bot.send_message(  # type: ignore
                            chat_id=seller.telegram_id,  # type: ignore
                            text=text,
                            reply_markup=InlineKeyboardMarkup(keyboard),
                        )
                        logger.info(f"Trade offer sent successfully to @{username}")
                    except Exception as send_error:
                        logger.error(
                            f"Failed to send trade offer to @{username}: {send_error}"
                        )
                else:
                    logger.warning(f"Seller @{username} not found in database")

            elif escrow.seller_email:
                # Send email notification using existing invitation system

                # Config already imported globally

                # Generate invitation link for email seller (will be sent after payment confirmation)
                pass

                # Invitation will be sent after payment confirmation (not immediately)

            # Update escrow status - payment-first flow means no seller waiting
            escrow.offer_sent_at = datetime.now(timezone.utc)
            try:
                EscrowStateValidator.validate_and_transition(
                    escrow,
                    EscrowStatus.ACTIVE,
                    getattr(escrow, 'escrow_id', 'unknown'),
                    force=False
                )
            except StateTransitionError as e:
                logger.error(f"❌ STATE_TRANSITION_ERROR: {e}")
                raise ValueError(f"Invalid state transition to ACTIVE: {e}")
            await session.commit()

    except Exception as e:
        logger.error(f"Error sending offer to seller: {e}")

# Cancel handler
async def handle_cancel_escrow(update: TelegramUpdate, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle immediate escrow cancellation - preserve early ID for audit trail"""
    query = update.callback_query
    user = update.effective_user
    
    # CRITICAL FIX: Immediate acknowledgment and simple cancellation
    if query:
        await safe_answer_callback_query(query, "❌ Trade cancelled")
    
    # Get escrow ID before cleanup - check ALL possible field names
    early_escrow_id = None
    escrow_data = None
    if context.user_data and "escrow_data" in context.user_data:
        escrow_data = context.user_data["escrow_data"]
        # CRITICAL FIX: Check for escrow ID in various possible locations
        early_escrow_id = (
            escrow_data.get("early_escrow_id") or
            escrow_data.get("unified_escrow_id") or
            escrow_data.get("escrow_id") or
            escrow_data.get("database_escrow_id")
        ) if escrow_data else None
    
    # Use unified cleanup function to clear all state
    if user:
        from utils.conversation_cleanup import clear_user_conversation_state
        await clear_user_conversation_state(
            user_id=user.id,
            context=context,
            trigger="cancel_escrow"
        )
        logger.info(f"✅ Cleared all conversation state for user {user.id} on escrow cancellation")
        
        if early_escrow_id:
            # CRITICAL FIX: UPDATE existing escrow instead of creating duplicate
            async with async_managed_session() as session:
                try:
                    # Find the existing escrow record to update
                    stmt = select(Escrow).where(Escrow.escrow_id == early_escrow_id)
                    result = await session.execute(stmt)
                    existing_escrow = result.scalar_one_or_none()
                    
                    if existing_escrow:
                        # UPDATE existing record with cancelled status using validation
                        try:
                            EscrowStateValidator.validate_and_transition(
                                existing_escrow,
                                EscrowStatus.CANCELLED,
                                early_escrow_id,
                                force=False
                            )
                        except StateTransitionError as e:
                            logger.warning(f"⚠️ FORCED_CANCELLATION: {e} - allowing cancellation anyway")
                            # Allow cancellations even if transition is normally invalid  # type: ignore
                            existing_escrow.status = EscrowStatus.CANCELLED.value  # type: ignore
                        # Note: cancelled_reason stored in admin_notes
                        # Note: updated_at handled by SQLAlchemy onupdate
                        await session.commit()
                        
                        # Invalidate cached escrow data (balance has changed)
                        invalidate_all_escrow_caches(context)
                        
                        logger.info(f"✅ Updated existing trade record to cancelled: {existing_escrow.escrow_id}")
                        
                        # Send admin notification about escrow cancellation
                        try:
                            from services.admin_trade_notifications import admin_trade_notifications
                            
                            # Get buyer information
                            buyer_stmt = select(User).where(User.id == existing_escrow.buyer_id)
                            buyer_result = await session.execute(buyer_stmt)
                            buyer = buyer_result.scalar_one_or_none()
                            
                            seller = None
                            # Check seller_id is not None before querying
                            seller_id_val = existing_escrow.seller_id
                            if seller_id_val is not None:
                                seller_stmt = select(User).where(User.id == seller_id_val)
                                seller_result = await session.execute(seller_stmt)
                                seller = seller_result.scalar_one_or_none()
                            
                            buyer_info = (
                                getattr(buyer, 'username', None) or getattr(buyer, 'first_name', None) or f"User_{getattr(buyer, 'telegram_id', 'unknown')}"
                                if buyer else "Unknown Buyer"
                            )
                            
                            seller_info = "Unknown Seller"
                            if seller:
                                seller_info = getattr(seller, 'username', None) or getattr(seller, 'first_name', None) or f"User_{getattr(seller, 'telegram_id', 'unknown')}"
                            elif getattr(existing_escrow, 'seller_contact_display', None):
                                seller_info = existing_escrow.seller_contact_display  # type: ignore
                            elif getattr(existing_escrow, 'seller_contact_value', None):
                                seller_info = existing_escrow.seller_contact_value  # type: ignore
                            elif getattr(existing_escrow, 'seller_username', None):
                                seller_info = f"@{getattr(existing_escrow, 'seller_username', '')}"  # type: ignore
                            elif existing_escrow.seller_email:  # type: ignore
                                seller_info = existing_escrow.seller_email
                                
                            escrow_cancellation_data = {
                                'escrow_id': existing_escrow.escrow_id,  # type: ignore
                                'amount': Decimal(str(existing_escrow.amount)),  # type: ignore
                                'currency': existing_escrow.currency or 'USD',  # type: ignore
                                'buyer_info': buyer_info,
                                'seller_info': seller_info,
                                'cancelled_by': buyer_info,
                                'reason': 'Buyer cancelled during creation',
                                'cancelled_at': datetime.now(timezone.utc)
                            }
                            
                            # Send admin notification asynchronously
                            asyncio.create_task(
                                admin_trade_notifications.notify_escrow_cancelled(escrow_cancellation_data)
                            )
                            logger.info(f"Admin notification queued for escrow cancellation: {existing_escrow.escrow_id}")
                            
                            # Send user email notification if user has verified email
                            buyer_email = getattr(buyer, 'email', None)
                            buyer_verified = getattr(buyer, 'is_verified', False)
                            if buyer and buyer_email and buyer_verified:
                                try:
                                    from services.user_cancellation_notifications import user_cancellation_notifications
                                    
                                    asyncio.create_task(
                                        user_cancellation_notifications.notify_user_escrow_cancelled(
                                            buyer_email, escrow_cancellation_data
                                        )
                                    )
                                    logger.info(f"User email notification queued for escrow cancellation: {buyer_email}")
                                except Exception as email_error:
                                    logger.error(f"Failed to queue user email notification: {email_error}")
                            
                        except Exception as e:
                            logger.error(f"Failed to queue admin notification for escrow cancellation: {e}")
                            
                    else:
                        # Fallback: If record doesn't exist in database yet, create new one
                        # This handles race condition where user clicks cancel before DB write completes
                        if not update.effective_user:
                            logger.error("No effective_user found")
                            return CONV_END
                        user_stmt = select(User).where(
                            User.telegram_id == update.effective_user.id
                        )
                        user_result = await session.execute(user_stmt)
                        user = user_result.scalar_one_or_none()
                        
                        if user and escrow_data:
                            amount = escrow_data.get("amount", Decimal("0"))
                            buyer_fee = escrow_data.get("buyer_fee", amount * Decimal(str(Config.ESCROW_FEE_PERCENTAGE / 100)))
                            fee_amount = buyer_fee
                            
                            cancelled_escrow = Escrow(
                                escrow_id=early_escrow_id,
                                buyer_id=user.id,
                                amount=amount,
                                currency="USD",
                                fee_amount=fee_amount,
                                total_amount=amount + fee_amount,
                                description=escrow_data.get("description", "Cancelled during creation"),
                                status=EscrowStatus.CANCELLED.value,
                                # Note: fee split option is stored in application logic, not database
                                # Note: buyer_fee_amount stored in pricing_snapshot
                                # Note: cancelled_reason stored in admin_notes
                                created_at=datetime.now(timezone.utc),
                                # Note: updated_at handled by SQLAlchemy onupdate
                            )
                            
                            session.add(cancelled_escrow)
                            await session.commit()
                            logger.info(f"✅ Created cancelled trade record (fallback): {early_escrow_id}")
                            
                            # Send admin notification about escrow cancellation (fallback case)
                            try:
                                from services.admin_trade_notifications import admin_trade_notifications
                                
                                buyer_info = (
                                    getattr(user, 'username', None) or 
                                    getattr(user, 'first_name', None) or 
                                    f"User_{getattr(user, 'telegram_id', 'Unknown')}"
                                )
                                
                                # Use consistent seller display format
                                def get_seller_display_for_notification(seller_type, seller_identifier):
                                    """Generate consistent seller display format for notifications"""
                                    if not seller_type or not seller_identifier:
                                        return "Unknown Seller"
                                    if seller_type == "username":
                                        return f"@{seller_identifier}"
                                    elif seller_type == "email":
                                        return seller_identifier.lower()
                                    elif seller_type == "phone":
                                        return seller_identifier
                                    else:
                                        return seller_identifier
                                
                                seller_info = get_seller_display_for_notification(
                                    escrow_data.get("seller_type"), 
                                    escrow_data.get("seller_identifier")
                                )
                                    
                                escrow_cancellation_data = {
                                    'escrow_id': early_escrow_id,
                                    'amount': Decimal(str(amount)),
                                    'currency': 'USD',
                                    'buyer_info': buyer_info,
                                    'seller_info': seller_info,
                                    'cancelled_by': buyer_info,
                                    'reason': 'Buyer cancelled during creation (early stage)',
                                    'cancelled_at': datetime.now(timezone.utc)
                                }
                                
                                # Send admin notification asynchronously
                                asyncio.create_task(
                                    admin_trade_notifications.notify_escrow_cancelled(escrow_cancellation_data)
                                )
                                logger.info(f"Admin notification queued for escrow cancellation (fallback): {early_escrow_id}")
                                
                                # Send user email notification if user has verified email (fallback case)
                                user_email = getattr(user, 'email', None)
                                user_verified = getattr(user, 'is_verified', False)
                                if user and user_email and user_verified:
                                    try:
                                        from services.user_cancellation_notifications import user_cancellation_notifications
                                        
                                        asyncio.create_task(
                                            user_cancellation_notifications.notify_user_escrow_cancelled(
                                                user_email, escrow_cancellation_data
                                            )
                                        )
                                        logger.info(f"User email notification queued for escrow cancellation (fallback): {user_email}")
                                    except Exception as email_error:
                                        logger.error(f"Failed to queue user email notification (fallback): {email_error}")
                                
                            except Exception as e:
                                logger.error(f"Failed to queue admin notification for escrow cancellation (fallback): {e}")
                        
                except Exception as e:
                    logger.error(f"Failed to update/preserve cancelled trade record: {e}")
                    await session.rollback()
    
    # Simple cancellation message and return to main menu
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🛡️ Create New Trade", callback_data="menu_create")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
    ])
    
    await safe_edit_message_text(
        query,
        f"""❌ Trade Cancelled

Your trade has been cancelled.

⚡ Create a new trade anytime.""",
        reply_markup=keyboard
    )

    # Clear escrow data
    if context.user_data:
        context.user_data.pop("escrow_data", None)

    return ConversationHandler.END

# Handle QR Code display
async def handle_copy_address(update: TelegramUpdate, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle copy address button - provide feedback to user"""
    query = update.callback_query
    if not query or not query.data:
        return ConversationHandler.END
        
    # Extract address from callback data
    address = query.data.replace("copy_address_", "")
    
    # IMMEDIATE FEEDBACK: Copy action
    await safe_answer_callback_query(query, "📋 Address copied to clipboard!")
    
    # Show temporary message with address highlighted
    text = f"""📋 Address Copied!

`{address}`

Use this address for your crypto payment.

💡 The address has been copied to your clipboard."""
    
    keyboard = [
        [InlineKeyboardButton("📱 QR Code", callback_data="show_qr")],
        [InlineKeyboardButton("⬅️ Back to Payment", callback_data="back_to_payment")],
        [InlineKeyboardButton("❌ Cancel Trade", callback_data="cancel_escrow")]
    ]
    
    try:
        await safe_edit_message_text(
            query, text, reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logger.error(f"Error showing copy address confirmation: {e}")
        # Fallback to simple answer
        await safe_answer_callback_query(query, "📋 Address ready to copy!")
    
    return EscrowStates.PAYMENT_PROCESSING

async def handle_escrow_crypto_switching(update: TelegramUpdate, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle crypto switching from payment processing screen - Exchange style"""
    query = update.callback_query
    
    if not query or not query.data:
        return EscrowStates.PAYMENT_PROCESSING
    
    # CRITICAL FIX: Immediate acknowledgment like Exchange
    if query.data.startswith("crypto_"):
        crypto = query.data.replace("crypto_", "")
        await safe_answer_callback_query(query, f"🔄 Switching to {crypto}")
    else:
        await safe_answer_callback_query(query, "🔄 Switching payment")
    
    if not context.user_data or "escrow_data" not in context.user_data:
        await safe_edit_message_text(query, "⏰ Session Expired\n\nInactive too long.\n\n⚡ Tap /start to continue.")
        return CONV_END
    
    # Show crypto selection menu like Exchange does
    escrow_data = context.user_data["escrow_data"]
    # FIXED: Use buyer_fee from fee split calculation instead of hardcoded 5%
    amount = Decimal(str(escrow_data["amount"]))
    buyer_fee = Decimal(str(escrow_data.get("buyer_fee", get_default_fee(amount))))
    total_amount = amount + buyer_fee
    
    text = """🔄 Switch Cryptocurrency

💰 Select a different crypto for your trade:"""
    
    from utils.crypto_ui_components import CryptoUIComponents
    keyboard = CryptoUIComponents.get_crypto_selection_keyboard(
        callback_prefix="crypto_",
        layout="compact",
        back_callback="back_to_payment"
    )
    
    await safe_edit_message_text(
        query,
        text,
        reply_markup=keyboard
    )
    
    # CRITICAL FIX: Use dedicated crypto selection state to avoid handler conflicts
    return EscrowStates.CRYPTO_SELECTION

async def handle_show_qr(update: TelegramUpdate, context: ContextTypes.DEFAULT_TYPE) -> int:  # type: ignore
    """Show QR code for crypto payment"""
    query = update.callback_query
    
    # IMMEDIATE FEEDBACK: Toast notification
    if query:
        await safe_answer_callback_query(query, "📱 Loading QR Code...")

    logger.info("QR code display requested")
    # Get payment details from context
    escrow_data = context.user_data.get("escrow_data", {}) if context.user_data else {}
    deposit_address = escrow_data.get("deposit_address")
    crypto_amount = escrow_data.get("crypto_amount")
    crypto_currency = escrow_data.get("crypto_currency")
    escrow_id = escrow_data.get("escrow_id")

    if not deposit_address:
        await safe_edit_message_text(
            query,  # type: ignore
            "❌ No payment address found. Please try again.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Back", callback_data="back_to_payment")]]
            ),
        )
        return CONV_END

    # Delete the current message first (page transition)
    if query and query.message:
        try:
            await query.message.delete()  # type: ignore
            logger.info("✅ Previous payment message deleted for QR code display")
        except Exception as del_err:
            logger.warning(f"Could not delete previous message: {del_err}")

    # Generate and send actual QR code image using our improved service
    try:
        from io import BytesIO
        from services.qr_generator import QRCodeService
        import base64

        # Use our improved QR service that generates camera-compatible QR codes
        qr_base64 = QRCodeService.generate_deposit_qr(
            address=deposit_address,  # type: ignore
            amount=Decimal(str(crypto_amount)),  # type: ignore
            currency=crypto_currency
        )
        
        if not qr_base64:
            raise Exception("QR generation failed")
            
        # Convert base64 to bytes for Telegram
        qr_bytes = base64.b64decode(qr_base64)
        bio = BytesIO(qr_bytes)
        bio.name = "qr_code.png"

        # MOBILE-OPTIMIZED: Compact caption for small screens with Escrow ID
        caption = f"""📱 Scan to Pay
🆔 {escrow_id}

💰 {crypto_amount:.8f} {crypto_currency}

<code>{deposit_address}</code>

⏰ Expires in {Config.PAYMENT_TIMEOUT_MINUTES}min
🔒 Secure escrow • Payment protected"""

        # After payment confirmation, only allow cancel - no payment switching
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Cancel Trade", callback_data="cancel_escrow")]
        ])
        
        if query and query.message:
            try:
                # Send QR code as new photo message with HTML-formatted tap-to-copy address
                await query.message.chat.send_photo(  # type: ignore
                    photo=bio,
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=keyboard
                )
                logger.info("✅ QR code sent with tap-to-copy address")
            except Exception as photo_err:
                logger.error(f"Error with photo message: {photo_err}")

    except Exception as e:
        logger.error(f"Error generating QR code: {e}")
        # CRITICAL FIX: Use same keyboard format for consistency
        # After payment confirmation, only allow cancel - no payment switching
        fallback_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Cancel Trade", callback_data="cancel_escrow")]
        ])
        
        # MOBILE-OPTIMIZED: Compact fallback message with Escrow ID
        fallback_text = f"""📱 Payment Address
🆔 {escrow_id}

💰 {crypto_amount:.6f} {crypto_currency}

<code>{deposit_address}</code>

⚠️ QR unavailable, copy address above"""
        
        if query and query.message:
            await query.message.chat.send_message(  # type: ignore
                text=fallback_text,
                parse_mode="HTML",
                reply_markup=fallback_keyboard
            )

# Handle back to payment selection
async def handle_back_to_payment(update: TelegramUpdate, context: ContextTypes.DEFAULT_TYPE) -> int:  # type: ignore
    """Go back to payment method selection"""
    # Config already imported globally
    
    query = update.callback_query
    # PERFORMANCE: Instant acknowledgment
    if query:
        # IMMEDIATE FEEDBACK: Escrow action
        await safe_answer_callback_query(query, "🛡️ Escrow action")

    # SECURITY: Check if escrow exists and prevent switching after confirmation
    escrow_data = context.user_data.get("escrow_data", {}) if context.user_data else {}
    escrow_id = escrow_data.get("escrow_id")
    
    if escrow_id:
        try:
            async with async_managed_session() as session:
                stmt = select(Escrow).where(Escrow.escrow_id == escrow_id)
                result = await session.execute(stmt)
                escrow = result.scalar_one_or_none()
                if escrow:
                    # Prevent payment switching after confirmation (similar to exchange flow)
                    restricted_statuses = [
                        EscrowStatus.PAYMENT_CONFIRMED.value,
                        EscrowStatus.ACTIVE.value,
                        EscrowStatus.COMPLETED.value,
                        EscrowStatus.CANCELLED.value
                    ]
                    
                    if escrow.status in restricted_statuses:
                        await safe_edit_message_text(
                            query,  # type: ignore
                            f"❌ Cannot switch payment after confirmation.\n\nTrade: ES{escrow.escrow_id}\nStatus: {escrow.status}\n\nPayment method is locked for security.",
                            reply_markup=InlineKeyboardMarkup([
                                [InlineKeyboardButton("📞 Get Help", callback_data="contact_support")],
                                [InlineKeyboardButton("❌ Cancel Trade", callback_data="cancel_escrow")]
                            ])
                        )
                        return CONV_END
        except Exception as e:
            logger.error(f"Error checking escrow status for payment switch: {e}")

    # FIXED: Use proper message editing instead of creating new messages
    # This prevents UI duplication by ensuring only one payment interface exists

    # CRITICAL FIX: Calculate wallet balance for display and sufficiency check
    wallet_balance_text = "💰 Wallet Balance"
    wallet_balance_decimal: Optional[Decimal] = None
    try:
        async with async_managed_session() as session:
            if query and query.from_user:
                stmt = select(User).where(User.telegram_id == query.from_user.id)
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()
            else:
                user = None
                
            if user:
                from models import Wallet

                # Get USD wallet for payment (use available_balance for accurate check)
                stmt = select(Wallet).where(Wallet.user_id == user.id, Wallet.currency == "USD")
                result = await session.execute(stmt)
                usd_wallet = result.scalar_one_or_none()
                
                if usd_wallet:
                    # Include both available_balance AND trading_credit for escrow payments
                    available_value = Decimal(str(usd_wallet.available_balance or 0))
                    trading_credit_value = Decimal(str(usd_wallet.trading_credit or 0))
                    wallet_balance_decimal = available_value + trading_credit_value
                    wallet_balance_text = f"💰 Wallet Balance (${wallet_balance_decimal:.2f} USD)"
                else:
                    wallet_balance_decimal = Decimal("0")
                    wallet_balance_text = "💰 Wallet Balance ($0.00 USD)"
    except Exception as e:
        logger.error(f"Error getting wallet balance for display: {e}")
        wallet_balance_decimal = None

    # Create the trade review content manually to send as new message
    # FIXED: Use buyer_fee from fee split calculation instead of hardcoded platform fee
    amount = Decimal(str(escrow_data["amount"]))
    buyer_fee = Decimal(str(escrow_data.get("buyer_fee", get_default_fee(amount))))
    total_amount = amount + buyer_fee

    # Use proper seller display format that matches database storage
    def get_seller_display(seller_type, seller_identifier):
        """Generate consistent seller display format that matches seller_contact_display"""
        if seller_type == "username":
            return f"@{seller_identifier}"
        elif seller_type == "email":
            return seller_identifier.lower()
        elif seller_type == "phone":
            return seller_identifier
        else:
            return seller_identifier
    
    # Clean seller identifier and get display
    seller_identifier_clean = escrow_data["seller_identifier"].replace('\\', '')
    seller_display = get_seller_display(
        escrow_data["seller_type"], 
        seller_identifier_clean
    )

    # Get NGN conversion for display - OPTIMIZED: Single API call instead of duplicate
    try:
        from services.financial_gateway import financial_gateway
        # OPTIMIZATION: Use single rate call instead of redundant FastForex + financial_gateway calls
        dynamic_rate = await financial_gateway.get_usd_to_ngn_rate_clean()
        if dynamic_rate:
            ngn_amount = Decimal(str(Decimal(str(total_amount)) * dynamic_rate))
            # Use clean rate for display (without adding markup confusion)
            ngn_display = f"\n🇳🇬 NGN Equivalent: ₦{ngn_amount:,.2f} @ ₦{dynamic_rate:.0f}/USD"
            logger.info(f"🚀 Escrow payment: Eliminated duplicate FastForex call - using single rate fetch")
        else:
            raise Exception("Exchange rate unavailable")
    except Exception as e:
        logger.error(f"Failed to get dynamic NGN rate: {e}")
        # Use last known rate from config (already imported globally)
        backup_rate = getattr(Config, "LAST_KNOWN_USD_NGN_RATE", 1500.0)
        ngn_amount = Decimal(str(total_amount)) * Decimal(str(backup_rate))
        ngn_display = f"\n🇳🇬 NGN Equivalent: ₦{ngn_amount:,.2f} @ ₦{backup_rate:.0f}/USD (fallback)"
    except Exception as e:  # type: ignore
        logger.error(f"Error getting NGN rate for review: {e}")
        ngn_display = ""

    text = f"""💰 Secure Payment

{seller_display} • ${total_amount:.2f} USD{ngn_display}

🛡️ You control release • Refund if not satisfied

Choose payment method:"""

    # FIXED: Use centralized payment keyboard function - single call only
    # Add wallet balance check parameters for dynamic wallet button
    payment_keyboard = _create_payment_keyboard(
        wallet_balance_text, 
        include_back=False,
        total_amount=total_amount,
        user_id=query.from_user.id if query and query.from_user else None,
        wallet_balance=wallet_balance_decimal
    )

    # CRITICAL: Use edit instead of new message to prevent duplication
    await safe_edit_message_text(query, text, reply_markup=payment_keyboard)

# Add a simple callback handler for create_secure_trade button
async def handle_create_secure_trade_callback(
    update: TelegramUpdate, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle the create secure trade button press"""
    query = update.callback_query
    # PERFORMANCE: Instant acknowledgment
    if query:
        # IMMEDIATE FEEDBACK: Escrow action
        await safe_answer_callback_query(query, "🛡️ Escrow action")

    # Get user's wallet balance for display
    try:
        async with async_managed_session() as session:
            if query and query.from_user:
                stmt = select(User).where(User.telegram_id == query.from_user.id)
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()
            else:
                user = None
                
            if not user:
                await safe_edit_message_text(query, "❌ Account issue. Tap /start to refresh")  # type: ignore
                return CONV_END

            from models import Wallet

            wallet_balance = Decimal("0.0")  # Use Decimal for precision
            stmt = select(Wallet).where(
                Wallet.user_id == user.id,
                Wallet.currency.in_(
                    ["USD", "USDT", "USDC", "USDT-TRC20", "USDT-ERC20"]
                )
            )
            result = await session.execute(stmt)
            usd_wallets = result.scalars().all()

            for wallet in usd_wallets:
                balance_val = getattr(wallet, "balance", 0)
                if balance_val and Decimal(str(balance_val)) > 0:
                    wallet_balance += Decimal(str(balance_val))

        text = f"""🤝 New Trade

💰 Your {Config.PLATFORM_NAME} wallet: ${wallet_balance:.2f}

Who's the seller?
• @username (Telegram)
• email@domain.com
• +1234567890 (Phone)"""

        await safe_edit_message_text(
            query,  # type: ignore
            text,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🏠 Main Menu", callback_data="back_to_main")]]
            ),
        )
    except Exception as e:
        logger.error(f"Error in handle_create_secure_trade_callback: {e}")
        return CONV_END

    # Initialize escrow data
    if context.user_data is None:
        context.user_data = {}
    context.user_data["escrow_data"] = {}
    return EscrowStates.SELLER_INPUT

async def handle_wallet_payment_confirmation(
    update: TelegramUpdate, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle wallet payment confirmation after user confirms"""
    query = update.callback_query
    # PERFORMANCE: Instant acknowledgment
    if query:
        # IMMEDIATE FEEDBACK: Escrow action
        await safe_answer_callback_query(query, "🛡️ Escrow action")

    try:
        # Get user and recalculate amounts
        user_id = safe_get_user_id(query)
        if not user_id:
            await safe_edit_message_text(
                query,  # type: ignore
                "❌ Unable to identify user. Please try again.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("⬅️ Back", callback_data="back_to_payment")]]
                ),
            )
            return CONV_END

        async with async_managed_session() as session:
            stmt = select(User).where(User.telegram_id == user_id)
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()

            if not user:
                await safe_edit_message_text(
                    query,  # type: ignore
                    "❌ User not found. Please try again.",
                    reply_markup=InlineKeyboardMarkup(
                        [[InlineKeyboardButton("⬅️ Back", callback_data="back_to_payment")]]
                    ),
                )
                return CONV_END

            escrow_data = safe_get_context_data(context, "escrow_data")
            if not escrow_data or "amount" not in escrow_data:
                await safe_edit_message_text(
                    query,  # type: ignore
                    "⏰ Session Expired\n\nInactive too long.\n\n⚡ Tap /start to continue.",
                    reply_markup=InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    "🏠 Main Menu", callback_data="back_to_main"
                                )
                            ]
                        ]
                    ),
                )
                return CONV_END

            amount = as_decimal(escrow_data["amount"])

            # Use fee split data instead of fixed percentage - use Config for defaults
            default_fee = get_default_fee(amount)
            buyer_fee = as_decimal(escrow_data.get(
                "buyer_fee", default_fee
            ))  # Always convert to Decimal
            seller_fee = as_decimal(escrow_data.get(
                "seller_fee", default_fee
            ))  # Always convert to Decimal
            
            # IMPORTANT: Separate buyer wallet payment from database total_amount
            # - buyer_wallet_payment: What buyer pays from wallet (amount + buyer_fee only)
            # - total_amount: Database field (amount + buyer_fee + seller_fee)
            buyer_wallet_payment = amount + buyer_fee
            total_amount = amount + buyer_fee + seller_fee

            # CRITICAL: Validate sufficient balance before processing payment
            # Check buyer has enough for their wallet payment, NOT the full total_amount
            is_valid, error_message = await WalletValidator.validate_sufficient_balance(  # type: ignore
                user_id=user.id,  # type: ignore
                required_amount=buyer_wallet_payment,
                currency="USD",
                session=session,
                include_frozen=False,
                purpose="escrow payment"
            )
            
            if not is_valid:
                # Get current wallet balance for error display
                usd_wallet_stmt = select(Wallet).where(
                    Wallet.user_id == user.id, 
                    Wallet.currency == "USD"
                )
                usd_wallet_result = await session.execute(usd_wallet_stmt)
                usd_wallet = usd_wallet_result.scalar_one_or_none()
                # Include both available_balance AND trading_credit for escrow payments
                if usd_wallet:
                    available_value = Decimal(str(usd_wallet.available_balance or 0))
                    trading_credit_value = Decimal(str(usd_wallet.trading_credit or 0))
                    current_balance = available_value + trading_credit_value
                else:
                    current_balance = Decimal("0")
                shortage = max(buyer_wallet_payment - current_balance, Decimal("0"))
                
                # Create branded error message with balance details
                error_header = BrandingUtils.make_header("Insufficient Balance")
                error_footer = BrandingUtils.make_trust_footer()
                
                error_text = f"""{error_header}

❌ Insufficient Balance

💰 Required: ${buyer_wallet_payment:.2f} USD
💳 Available: ${current_balance:.2f} USD  
📉 Shortage: ${shortage:.2f} USD

Please add funds to your wallet before proceeding with this trade.

{error_footer}"""

                keyboard = [
                    [InlineKeyboardButton("💎 Add Funds", callback_data="escrow_add_funds")],
                    [InlineKeyboardButton("⬅️ Back to Payment", callback_data="back_to_payment")],
                    [InlineKeyboardButton("❌ Cancel Trade", callback_data="cancel_escrow")]
                ]

                await safe_edit_message_text(
                    query,  # type: ignore
                    error_text,
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                return EscrowStates.PAYMENT_METHOD

            logger.info(f"Balance validation passed for user {user.id}: ${buyer_wallet_payment:.2f} payment authorized")

            # Process the confirmed payment - keep user and session in same context
            # Pass buyer_wallet_payment for wallet debit, total_amount is recalculated inside
            return await process_immediate_wallet_payment(
                query, context, user, buyer_wallet_payment, session
            )

    except Exception as e:
        logger.error(f"Error in wallet payment confirmation: {e}")
        await safe_edit_message_text(
            query,  # type: ignore
            "❌ Payment confirmation failed. Please contact support.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Back", callback_data="back_to_payment")]]
            ),
        )
        return CONV_END

# NOTE: ARCHIVED ConversationHandler removed - was explicitly marked as unused
# Direct handlers are registered in main.py for better reliability

async def handle_share_link(update: TelegramUpdate, context: ContextTypes.DEFAULT_TYPE) -> int:  # type: ignore
    """Handle share link button for escrow invitations"""
    query = update.callback_query
    if not query:
        return CONV_END

    await safe_answer_callback_query(query, "📤")

    try:
        # Extract escrow ID from callback data
        if not query.data or ":" not in query.data:
            await safe_edit_message_text(query, "❌ Invalid request.")
            return CONV_END
        escrow_id = query.data.split(":")[1]

        async with async_managed_session() as session:
            stmt = select(Escrow).where(Escrow.escrow_id == escrow_id)
            result = await session.execute(stmt)
            escrow = result.scalar_one_or_none()
            
            if not escrow:
                await safe_edit_message_text(query, "❌ Trade not found.")
                return CONV_END

            # Generate the invitation link
            from utils.helpers import create_deep_link

            escrow_id_str = str(getattr(escrow, "escrow_id", ""))
            invitation_token_str = str(getattr(escrow, "invitation_token", ""))
            invitation_link = create_deep_link(escrow_id_str, invitation_token_str)

            # Show share options
            share_text = f"""📤 Share Trade Link

<code>{invitation_link}</code>

<i>Tap to copy • Link expires in 7 days</i>"""

            share_keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "📋 Copy",
                            url=f"https://t.me/share/url?url={invitation_link}",
                        )
                    ],
                    [InlineKeyboardButton("⬅️ Back", callback_data="main_menu")],
                ]
            )

            await safe_edit_message_text(query, share_text, reply_markup=share_keyboard)

    except Exception as e:
        logger.error(f"Error handling share link: {e}")
        await safe_edit_message_text(query, "❌ Error sharing link. Please try again.")

async def handle_seller_email_input(update: TelegramUpdate, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle email input from new sellers"""
    if not update.message or not update.message.text:
        return CONV_END

    # Only handle email input if user is in seller email collection flow
    if not context.user_data or not context.user_data.get("collecting_seller_email"):
        return CONV_END

    email = update.message.text.strip().lower()

    # Validate email format
    import re

    email_pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    if not re.match(email_pattern, email):
        await update.message.reply_text(
            "❌ Invalid Email Format\n\nPlease enter a valid email address:",
            parse_mode="Markdown",
        )
        return States.COLLECTING_EMAIL

    async with async_managed_session() as session:
        # Get user
        user_stmt = select(User).where(User.telegram_id == update.effective_user.id)  # type: ignore
        user_result = await session.execute(user_stmt)
        user = user_result.scalar_one_or_none()

        if not user:
            await update.message.reply_text("❌ User not found. Please try again.")
            return CONV_END

        # Check if email already exists for another user (case-insensitive)
        from sqlalchemy import func
        existing_user_stmt = select(User).where(func.lower(User.email) == func.lower(email), User.id != user.id)
        existing_user_result = await session.execute(existing_user_stmt)
        existing_user = existing_user_result.scalar_one_or_none()

        if existing_user:
            await update.message.reply_text(
                "❌ Email Already Registered\n\nThis email is already used by another account. Please use a different email:",
            )
            return States.COLLECTING_EMAIL

        # Store email and send verification
        setattr(user, "email", email)
        setattr(user, "is_verified", False)
        await session.commit()

        # Send OTP verification
        from models import EmailVerification
        from datetime import datetime, timedelta, timezone
        from sqlalchemy import delete
        # Generate cryptographically secure OTP
        from utils.secure_crypto import SecureCrypto
        otp = SecureCrypto.generate_secure_otp(6)
        datetime.now(timezone.utc) + timedelta(minutes=15)

        # Clean existing verification records
        delete_stmt = delete(EmailVerification).where(
            EmailVerification.user_id == user.id, EmailVerification.email == email
        )
        await session.execute(delete_stmt)

        # Create new verification record
        verification = EmailVerification(
            user_id=getattr(user, "id", 0),
            email=email,
            verification_code=otp,
            purpose="seller_acceptance",
            verified=False,
        )
        session.add(verification)
        await session.commit()

        # Send OTP email
        from services.email import EmailService

        try:
            email_service = EmailService()
            email_sent = email_service.send_email(
                to_email=email,
                subject=f"🔐 {Config.PLATFORM_NAME} - Email Verification Code",
                html_content=f"""
                <h2>Email Verification</h2>
                <p>Your verification code is: <strong>{otp}</strong></p>
                <p>This code expires in 15 minutes.</p>
                """,
            )
            if not email_sent:
                logger.error(f"❌ Failed to send OTP email to {email} - email service returned False")
        except Exception as e:
            logger.error(f"Failed to send OTP email: {e}")

        await update.message.reply_text(
            f"📧 Verification Code Sent\n\nWe've sent a 6-digit code to:\n{email}\n\nPlease enter the code to continue:",
            parse_mode="Markdown",
        )

        # Update context
        context.user_data["verifying_seller_email"] = True
        context.user_data["collecting_seller_email"] = False
        return States.VERIFYING_EMAIL_INVITATION

async def handle_seller_email_verification(
    update: TelegramUpdate, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle OTP verification for seller email"""
    if not update.message or not update.message.text:
        return CONV_END

    # Only handle if user is in email verification flow
    if not context.user_data or not context.user_data.get("verifying_seller_email"):
        return CONV_END

    otp_input = update.message.text.strip()

    # Validate OTP format (6 digits)
    if not otp_input.isdigit() or len(otp_input) != 6:
        await update.message.reply_text(
            "❌ Invalid Code Format\n\nPlease enter the 6-digit code sent to your email:",
            parse_mode="Markdown",
        )
        return States.VERIFYING_EMAIL_INVITATION

    async with async_managed_session() as session:
        if not update.effective_user or not hasattr(update.effective_user, "id"):
            await update.message.reply_text(
                "❌ Unable to identify user. Please try again."
            )
            return CONV_END

        user_stmt = select(User).where(User.telegram_id == update.effective_user.id)
        user_result = await session.execute(user_stmt)
        user = user_result.scalar_one_or_none()

        if not user:
            await update.message.reply_text("❌ User not found. Please try again.")
            return CONV_END

        # Use unified verification service with proper expiry validation
        from services.unified_email_verification import UnifiedEmailVerificationService
        
        success, message = await UnifiedEmailVerificationService.verify_otp(  # type: ignore
            email=user.email,  # type: ignore
            otp=otp_input,  # type: ignore
            user_id=user.id,  # type: ignore
            verification_type="seller_acceptance"
        )
        
        if not success:
            await update.message.reply_text(
                f"❌ {message}\n\nPlease check the code and try again:",
            )
            return States.VERIFYING_EMAIL_INVITATION

        # Mark email as verified
        setattr(user, "is_verified", True)
        await session.commit()

        await update.message.reply_text(
            "✅ Email Verified Successfully!\n\nYour account is now set up. You can now accept trade invitations.",
            parse_mode="Markdown",
        )

        # Clear verification context
        context.user_data.pop("verifying_seller_email", None)
        context.user_data.pop("collecting_seller_email", None)

        return CONV_END

async def handle_seller_invitation_response(
    update: TelegramUpdate,
    context: ContextTypes.DEFAULT_TYPE,
    escrow_id: str,
    action: str,
    session,
) -> int:  # type: ignore
    """Handle seller invitation acceptance/decline from deep links"""
    try:
        escrow = session.query(Escrow).filter(Escrow.escrow_id == escrow_id).first()
        if not escrow:
            message_text = f"❌ Error: Trade #{escrow_id} not found."
            if update.message:
                await update.message.reply_text(message_text)
            elif update.callback_query:
                await safe_edit_message_text(update.callback_query, message_text)
            return CONV_END

        stmt = select(User).where(User.telegram_id == update.effective_user.id)  # type: ignore
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()

        if not user:
            message_text = f"❌ Error: User account not found. Please restart the process."
            if update.message:
                await update.message.reply_text(message_text)
            elif update.callback_query:
                await safe_edit_message_text(update.callback_query, message_text)
            return CONV_END

        if action == "accept":
            # Accept the trade - COMPREHENSIVE VALIDATION AND FIX SYSTEM
            escrow.seller_id = user.id  # Direct assignment instead of setattr
            try:
                EscrowStateValidator.validate_and_transition(
                    escrow,
                    EscrowStatus.ACTIVE,
                    getattr(escrow, 'escrow_id', 'unknown'),
                    force=False
                )
            except StateTransitionError as e:
                logger.error(f"❌ STATE_TRANSITION_ERROR: {e}")
                raise ValueError(f"Invalid state transition to ACTIVE for seller acceptance: {e}")
            escrow.deposit_confirmed = True
            # Set seller acceptance timestamp for durable acceptance tracking (used for refund policy)  # type: ignore
            escrow.seller_accepted_at = datetime.now(timezone.utc)  # type: ignore

            # DELIVERY TIMING: Deadlines already set during escrow creation based on user selection
            # No need to recalculate - delivery_deadline and auto_release_at already exist from creation
            
            # CRITICAL FIX: Ensure database commit is successful WITH COMPREHENSIVE VALIDATION
            try:
                session.commit()
                session.flush()  # Ensure changes are written to database
                
                # INTEGRITY VALIDATION: Verify the seller_id was actually saved
                from utils.trade_integrity_validator import TradeIntegrityValidator
                validation = TradeIntegrityValidator.validate_seller_assignment(escrow.escrow_id, user.id)
                
                if not validation["valid"]:
                    logger.error(f"🚨 CRITICAL INTEGRITY FAILURE: {validation['error']}")
                    session.rollback()
                    raise ValueError(f"Seller assignment validation failed: {validation['error']}")
                
                # Fix missing notification preferences if needed
                if validation.get("notification_issue"):
                    logger.warning(f"⚠️ FIXING NOTIFICATION ISSUE: {validation['notification_issue']}")
                    TradeIntegrityValidator.fix_missing_notification_preferences(user.id)
                
                logger.info(f"✅ Trade {escrow.escrow_id} accepted by seller {user.id} - database updated and validated successfully")
                
                # Send Telegram group notification for seller accepted
                try:
                    buyer_name = "Unknown"
                    if escrow.buyer_id:
                        buyer_result = session.query(User).filter(User.id == escrow.buyer_id).first()
                        if buyer_result:
                            buyer_name = f"@{buyer_result.username}" if buyer_result.username else buyer_result.first_name
                    
                    seller_name = f"@{user.username}" if user.username else user.first_name
                    acceptance_data = {
                        'escrow_id': str(escrow.escrow_id),
                        'seller_info': seller_name,
                        'buyer_info': buyer_name,
                        'amount': float(escrow.amount),
                        'accepted_at': datetime.now(timezone.utc)
                    }
                    admin_notif_service = AdminTradeNotificationService()
                    asyncio.create_task(admin_notif_service.send_group_notification_seller_accepted(acceptance_data))
                    # Broadcast to all registered groups
                    from services.group_event_service import group_event_service
                    asyncio.create_task(group_event_service.broadcast_seller_accepted(acceptance_data))
                    logger.info(f"📤 Queued group notification for seller accepted: {escrow.escrow_id}")
                except Exception as notif_err:
                    logger.error(f"❌ Failed to queue seller accepted group notification: {notif_err}")
                
            except Exception as commit_error:
                logger.error(f"❌ Failed to commit seller acceptance for trade {escrow.escrow_id}: {commit_error}")
                session.rollback()
                raise commit_error

            # Calculate delivery hours from deadline if available
            timeout_hours = 24  # Default to 24 hours
            if hasattr(escrow, 'delivery_deadline') and escrow.delivery_deadline:
                try:
                    time_remaining = escrow.delivery_deadline - datetime.now(timezone.utc)  # type: ignore
                    timeout_hours = int(time_remaining.total_seconds() / 3600)
                except Exception:
                    timeout_hours = 24

            success_message = f"""✅ Trade Accepted!

🆔 #{getattr(escrow, 'escrow_id', 'N/A')[-6:]} • ${getattr(escrow, 'amount', 0):.2f}
⏰ Deliver within {timeout_hours} hours

Buyer has been notified. Trade is now active!"""

            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "💬 Message Buyer",
                            callback_data=f"trade_chat_open:{getattr(escrow, 'id', 0)}",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "📋 My Trades", callback_data="trades_messages_hub"
                        ),
                        InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu"),
                    ],
                ]
            )

            if update.message:
                await update.message.reply_text(
                    success_message, parse_mode="Markdown", reply_markup=keyboard
                )
            elif update.callback_query:
                await safe_edit_message_text(
                    update.callback_query,
                    success_message,
                    parse_mode="Markdown",
                    reply_markup=keyboard,
                )

            # Send comprehensive trade acceptance notifications
            try:
                from services.trade_acceptance_notification_service import trade_acceptance_notifications
                
                notification_results = await trade_acceptance_notifications.notify_trade_acceptance(
                    escrow_id=escrow.escrow_id,
                    buyer_id=escrow.buyer_id,
                    seller_id=escrow.seller_id,
                    amount=escrow.amount if escrow.amount else Decimal('0.0'),
                    currency="USD"
                )
                
                success_count = sum(1 for success in notification_results.values() if success)
                total_count = len(notification_results)
                logger.info(f"✅ Trade acceptance notifications for {escrow.escrow_id}: {success_count}/{total_count} sent successfully")
                
            except Exception as notification_error:
                logger.error(f"❌ Failed to send trade acceptance notifications for {escrow.escrow_id}: {notification_error}")

        elif action == "decline":
            # Decline the trade
            setattr(escrow, "status", EscrowStatus.CANCELLED.value)
            from datetime import datetime

            setattr(
                escrow,
                "cancelled_reason",
                f"Declined by seller at {datetime.now(timezone.utc).isoformat()}",
            )
            session.commit()
            
            # Send admin notification about escrow cancellation
            try:
                from services.admin_trade_notifications import admin_trade_notifications
                
                # Get buyer and seller information
                buyer_stmt = select(User).where(User.id == escrow.buyer_id)
                buyer_result = await session.execute(buyer_stmt)
                buyer = buyer_result.scalar_one_or_none()
                
                seller = None
                if escrow.seller_id:
                    seller_stmt = select(User).where(User.id == escrow.seller_id)
                    seller_result = await session.execute(seller_stmt)
                    seller = seller_result.scalar_one_or_none()
                
                buyer_info = (
                    buyer.username or buyer.first_name or f"User_{buyer.telegram_id}"
                    if buyer else "Unknown Buyer"
                )
                seller_info = "Unknown Seller"
                if seller:
                    seller_info = seller.username or seller.first_name or f"User_{seller.telegram_id}"
                elif getattr(escrow, 'seller_contact_display', None):
                    seller_info = escrow.seller_contact_display  # type: ignore
                elif getattr(escrow, 'seller_contact_value', None):
                    seller_info = escrow.seller_contact_value  # type: ignore
                elif getattr(escrow, 'seller_email', None):
                    seller_info = escrow.seller_email  # type: ignore
                
                escrow_cancellation_data = {
                    'escrow_id': escrow.escrow_id,
                    'amount': Decimal(str(escrow.amount)) if escrow.amount else Decimal("0.0"),
                    'currency': 'USD',
                    'buyer_info': buyer_info,
                    'seller_info': seller_info,
                    'cancelled_by': seller_info,
                    'reason': 'Seller declined trade',
                    'cancelled_at': datetime.now(timezone.utc)
                }
                
                # Send admin notification asynchronously with proper task tracking
                from utils.graceful_shutdown import create_managed_task
                create_managed_task(
                    admin_trade_notifications.notify_escrow_cancelled(escrow_cancellation_data)
                )
                logger.info(f"Admin notification queued for escrow cancellation: {escrow.escrow_id}")
                
            except Exception as e:
                logger.error(f"Failed to queue admin notification for escrow cancellation: {e}")

            decline_message = f"""❌ Trade Declined

🆔 Trade ID: #{(getattr(escrow, 'utid', None) or getattr(escrow, 'escrow_id', 'N/A'))[-6:]}
💰 Amount: ${getattr(escrow, 'amount', 0):.2f} USD

You have declined this trade. The buyer will be refunded automatically."""

            keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]]
            )

            if update.message:
                await update.message.reply_text(
                    decline_message, parse_mode="Markdown", reply_markup=keyboard
                )
            elif update.callback_query:
                await safe_edit_message_text(
                    update.callback_query,
                    decline_message,  # type: ignore
                    parse_mode="Markdown",
                    reply_markup=keyboard,
                )

            # Process refund and send notifications
            from services.consolidated_notification_service import (
                consolidated_notification_service as NotificationService,
            )

            await NotificationService.send_escrow_cancelled(escrow, "seller_declined")  # type: ignore

    except Exception as e:
        logger.error(f"Error in seller invitation response: {e}")
        error_message = "❌ Error processing trade response. Please try again."
        if update.message:
            await update.message.reply_text(error_message)
        elif update.callback_query:
            await safe_edit_message_text(update.callback_query, error_message)

async def handle_seller_response(update: TelegramUpdate, context: ContextTypes.DEFAULT_TYPE) -> int:  # type: ignore
    """SECURITY FIXED: Handle seller acceptance/decline with atomic operations"""
    query = update.callback_query
    # PERFORMANCE: Instant acknowledgment
    # IMMEDIATE FEEDBACK: Escrow management
    await safe_answer_callback_query(query, "🛡️ Escrow management")

    # SECURITY FIX: Use atomic transactions with proper locking
    from utils.atomic_transactions import atomic_transaction, locked_escrow_operation

    try:
        # Get escrow ID from context
        if not context.user_data:
            await safe_edit_message_text(query, "❌ Session expired. Please try again.")
            return CONV_END

        escrow_id = context.user_data.get("pending_escrow_id")
        if not escrow_id:
            await safe_edit_message_text(query, "❌ Escrow not found.")
            return CONV_END
  # type: ignore
        # SECURITY FIX: Use atomic transaction with escrow locking
        with atomic_transaction() as session:
            with locked_escrow_operation(str(escrow_id), session) as escrow:
                if not escrow:
                    await safe_edit_message_text(query, "❌ Escrow not found.")
                    return CONV_END

                stmt = select(User).where(User.telegram_id == str(update.effective_user.id))  # type: ignore
                result = session.execute(stmt)
                user = result.scalar_one_or_none()

                if query and query.data == CallbackData.ESCROW_ACCEPT:
                    # SECURITY FIX: Validate state transition before acceptance
                    from utils.escrow_state_validator import EscrowStateValidator

                    validator = EscrowStateValidator()

                    current_status = escrow.status
                    if not validator.is_valid_transition(
                        current_status, EscrowStatus.ACTIVE.value
                    ):
                        await safe_edit_message_text(
                            query, "❌ Trade cannot be accepted at this time."
                        )
                        return CONV_END

                    # Accept escrow with atomic operations
                    escrow.seller_id = user.id if user else None

                    # PAYMENT-FIRST: Trade is already funded, just activate it
                    escrow.status = EscrowStatus.ACTIVE.value
                    escrow.deposit_confirmed = True

                    # DELIVERY TIMING: Deadlines already set during escrow creation based on user selection
                    # No need to recalculate - delivery_deadline and auto_release_at already exist from creation

                    # Atomic transaction will auto-commit

                    # Show acceptance confirmation
                    await safe_edit_message_text(
                        query,
                        f"✅ Trade Accepted!\n\n"
                        f"🆔 Trade: #{escrow.escrow_id}\n"
                        f"💰 Amount: ${Decimal(str(escrow.amount)):.2f} USD\n\n"
                        f"The trade is now active. Deliver your service to complete the transaction.",  # type: ignore
                    )

                    # Send comprehensive trade acceptance notifications
                    try:
                        from services.trade_acceptance_notification_service import trade_acceptance_notifications
                        
                        notification_results = await trade_acceptance_notifications.notify_trade_acceptance(
                            escrow_id=escrow.escrow_id,
                            buyer_id=escrow.buyer_id,
                            seller_id=escrow.seller_id,  # type: ignore
                            amount=escrow.amount if escrow.amount else Decimal('0.0'),
                            currency="USD"
                        )
                        
                        success_count = sum(1 for success in notification_results.values() if success)
                        total_count = len(notification_results)
                        logger.info(f"✅ Trade acceptance notifications for {escrow.escrow_id}: {success_count}/{total_count} sent successfully")
                        
                    except Exception as notification_error:
                        logger.error(f"❌ Failed to send trade acceptance notifications for {escrow.escrow_id}: {notification_error}")

                    logger.info(
                        f"Seller accepted escrow {escrow.escrow_id} - trade activated"
                    )
                    return CONV_END

                elif query and query.data == CallbackData.ESCROW_DECLINE:
                    # SECURITY FIX: Use atomic state transition for decline
                    from utils.escrow_state_validator import EscrowStateValidator

                    validator = EscrowStateValidator()

                    current_status = escrow.status
                    if not validator.is_valid_transition(
                        current_status, EscrowStatus.CANCELLED.value
                    ):
                        await safe_edit_message_text(
                            query, "❌ Trade cannot be declined at this time."
                        )
                        return CONV_END

                    # CRITICAL FIX: Process refund when seller declines payment_confirmed trade
                    if escrow.status == "payment_confirmed":
                        # Release held funds back to buyer
                        from services.escrow_fund_manager import EscrowFundManager
                        
                        # Get held amount from escrow holding
                        holding = session.query(EscrowHolding).filter(
                            EscrowHolding.escrow_id == escrow.escrow_id,
                            EscrowHolding.status == "held"
                        ).first()
                        
                        if holding:
                            # FAIR REFUND POLICY: If seller never accepted, refund escrow + buyer fee
                            # If seller had accepted, refund only escrow amount (fees retained)
                            refund_amount = holding.amount_held
                            
                            if escrow.seller_accepted_at is None:
                                # Seller never accepted: Full refund including buyer fee
                                buyer_fee = Decimal(str(escrow.buyer_fee_amount or 0))
                                refund_amount = refund_amount + buyer_fee
                                logger.info(f"💰 FAIR_REFUND: Seller never accepted {escrow.escrow_id}, refunding escrow (${holding.amount_held}) + buyer fee (${buyer_fee}) = ${refund_amount}")
                            
                            # Credit buyer's wallet with refund
                            from services.crypto import CryptoServiceAtomic
                            refund_success = await CryptoServiceAtomic.credit_user_wallet_atomic(
                                user_id=escrow.buyer_id,
                                amount=Decimal(str(refund_amount)),  # type: ignore
                                currency="USD",
                                transaction_type="escrow_refund",  # FIX: Correct type for database constraint
                                description=f"Trade refund #{escrow.escrow_id}: Seller declined",
                                escrow_id=escrow.id,
                                session=session  # type: ignore
                            )
                            
                            if refund_success:
                                # Mark holding as released/refunded
                                holding.status = "refunded"  # type: ignore
                                holding.released_at = datetime.now(timezone.utc)  # type: ignore
                                holding.released_to_user_id = escrow.buyer_id
                                
                                # Create refund transaction record
                                from models import Transaction
                                from utils.helpers import generate_transaction_id
                                refund_tx = Transaction(
                                    transaction_id=UniversalIDGenerator.generate_transaction_id(),
                                    user_id=escrow.buyer_id,
                                    escrow_id=escrow.id,
                                    transaction_type="escrow_refund",  # FIX: Correct type for database constraint
                                    amount=Decimal(str(refund_amount)),  # type: ignore
                                    currency="USD",
                                    status="completed",
                                    description=f"Trade refund #{escrow.escrow_id}: Seller declined",
                                    created_at=datetime.now(timezone.utc)
                                )
                                session.add(refund_tx)
                                
                                logger.info(f"✅ Refunded ${refund_amount} to buyer {escrow.buyer_id} for declined trade {escrow.escrow_id}")
                            else:
                                logger.error(f"Failed to process refund for declined trade {escrow.escrow_id}")
                        else:
                            logger.warning(f"No held funds found for declined trade {escrow.escrow_id}")

                    # Decline the escrow with atomic operations
                    escrow.status = EscrowStatus.CANCELLED.value
                    # Note: cancelled_reason stored in admin_notes

                    # Send admin notification about escrow cancellation
                    try:
                        from services.admin_trade_notifications import admin_trade_notifications
                        from models import User
                        
                        # Get buyer and seller information
                        buyer_stmt = select(User).where(User.id == escrow.buyer_id)
                        buyer_result = session.execute(buyer_stmt)
                        buyer = buyer_result.scalar_one_or_none()
                        
                        seller = None
                        if escrow.seller_id:
                            seller_stmt = select(User).where(User.id == escrow.seller_id)
                            seller_result = session.execute(seller_stmt)
                            seller = seller_result.scalar_one_or_none()
                        
                        buyer_info = (
                            buyer.username or buyer.first_name or f"User_{buyer.telegram_id}"  # type: ignore
                            if buyer else "Unknown Buyer"
                        )
                        seller_info = "Unknown Seller"
                        if seller:
                            seller_info = seller.username or seller.first_name or f"User_{seller.telegram_id}"  # type: ignore
                        elif getattr(escrow, 'seller_contact_display', None):
                            seller_info = escrow.seller_contact_display  # type: ignore
                        elif getattr(escrow, 'seller_contact_value', None):
                            seller_info = escrow.seller_contact_value  # type: ignore
                        elif getattr(escrow, 'seller_email', None):
                            seller_info = escrow.seller_email  # type: ignore
                        
                        escrow_cancellation_data = {
                            'escrow_id': escrow.escrow_id,
                            'amount': Decimal(str(escrow.amount)) if escrow.amount else Decimal("0.0"),
                            'currency': 'USD',
                            'buyer_info': buyer_info,
                            'seller_info': seller_info,
                            'cancelled_by': seller_info,
                            'reason': 'Seller declined invitation',
                            'cancelled_at': datetime.now(timezone.utc)
                        }
                        
                        # Send admin notification asynchronously
                        asyncio.create_task(
                            admin_trade_notifications.notify_escrow_cancelled(escrow_cancellation_data)
                        )
                        logger.info(f"Admin notification queued for escrow cancellation: {escrow.escrow_id}")
                        
                    except Exception as e:
                        logger.error(f"Failed to queue admin notification for escrow cancellation: {e}")

                    # Atomic transaction will auto-commit
                    
                    # Add timestamp to ensure message is unique and forces UI update
                    decline_time = datetime.now(timezone.utc).strftime("%H:%M:%S")

                    await safe_edit_message_text(
                        query,
                        f"❌ Trade Declined\n\nYou have declined this trade invitation. The buyer has been refunded.\n_Declined at {decline_time} UTC_",
                    )

                    # Process refund and send notifications
                    from services.consolidated_notification_service import (
                        consolidated_notification_service as NotificationService,
                    )

                    await NotificationService.send_escrow_cancelled(  # type: ignore
                        escrow, "seller_declined"
                    )
                    
                    # Send email notification to buyer about seller declining
                    buyer_stmt = select(User).where(User.id == escrow.buyer_id)  # type: ignore
                    buyer_result = session.execute(buyer_stmt)
                    buyer = buyer_result.scalar_one_or_none()
                    if buyer and buyer.email and buyer.is_verified:  # type: ignore
                        try:
                            from services.email import EmailService
                            email_service = EmailService()
                            await email_service.send_trade_notification(
                                str(buyer.email),
                                str(buyer.first_name or buyer.username or "Buyer"),  # type: ignore
                                str(escrow.escrow_id),
                                "seller_declined",
                                {
                                    "amount": Decimal(str(escrow.amount)),
                                    "currency": str(escrow.currency or "USD"),
                                    "status": "cancelled",
                                    "seller": str(escrow.seller_contact_display or user.first_name or user.username or "Seller"),  # type: ignore
                                    "payment_method": str(escrow.payment_method or "N/A"),
                                    "description": str(escrow.description or "N/A"),
                                },
                            )
                            logger.info(f"Seller declined notification email sent to buyer {buyer.email}")
                        except Exception as e:
                            logger.error(f"Failed to send seller declined notification email to buyer: {e}")

                    logger.info(f"Seller declined escrow {escrow.escrow_id}")
                    return CONV_END

    except Exception as e:
        logger.error(f"Error in seller response: {e}")
        await safe_edit_message_text(query, "❌ An error occurred. Please try again.")
    finally:
        session.close()  # type: ignore


# CRITICAL FIX: Add missing view_trade and action handlers for My Trade functionality
async def handle_view_trade(update: TelegramUpdate, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle view_trade callback to show detailed trade information"""
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "📋 Loading trade details...")
    
    logger.info("🎯 handle_view_trade called!")
    
    if query:
        # Clear last_action flag if present
        last_action = context.user_data.get('last_action', '') if context.user_data else ''
        if "cancel" in str(last_action) and context.user_data:
            context.user_data.pop('last_action', None)
        logger.info(f"🎯 Callback data received: {query.data}")

    if not update.effective_user:
        logger.warning("🎯 No effective user found")
        return CONV_END

    # Extract escrow ID from callback data (formats: "view_trade_123" or "view_escrow_ES123ABC")
    callback_data = query.data if query else ""
    logger.info(f"🎯 Processing callback: {callback_data}")
    
    if not (callback_data.startswith('view_trade_') or callback_data.startswith('view_escrow_')):  # type: ignore
        await safe_edit_message_text(query, "❌ Invalid trade view request.")
        return CONV_END

    try:
        if callback_data.startswith('view_trade_'):  # type: ignore
            # Extract the ID part (could be numeric or escrow_id string)
            id_part = callback_data.split('_')[2]  # type: ignore
            
            # Try to parse as integer first (numeric ID)
            try:
                escrow_id = int(id_part)
            except ValueError:
                # It's a string ID (escrow_id like "ES100825FYWY"), look it up
                async with async_managed_session() as session:
                    escrow_lookup_stmt = select(Escrow).where(Escrow.escrow_id == id_part)
                    escrow_lookup_result = await session.execute(escrow_lookup_stmt)
                    escrow_lookup = escrow_lookup_result.scalar_one_or_none()
                    
                    if not escrow_lookup:
                        await safe_edit_message_text(query, "❌ Trade not found.")
                        return CONV_END
                    
                    escrow_id = escrow_lookup.id
                    logger.info(f"ID_MAPPING: Found escrow by escrow_id {id_part} -> numeric ID {escrow_id}")
        else:
            # Format: "view_escrow_QFU3KT35" (utid string - user-facing Trade ID)
            escrow_string_id = callback_data.split('_')[2]  # type: ignore
            # CRITICAL FIX: Look up by utid (user-facing Trade ID) instead of escrow_id
            async with async_managed_session() as session:
                escrow_lookup_stmt = select(Escrow).where(Escrow.utid == escrow_string_id)
                escrow_lookup_result = await session.execute(escrow_lookup_stmt)
                escrow_lookup = escrow_lookup_result.scalar_one_or_none()
                
                if not escrow_lookup:
                    # FALLBACK: If utid lookup fails, try escrow_id for backward compatibility
                    escrow_fallback_stmt = select(Escrow).where(Escrow.escrow_id == escrow_string_id)
                    escrow_fallback_result = await session.execute(escrow_fallback_stmt)
                    escrow_lookup = escrow_fallback_result.scalar_one_or_none()
                    
                    if not escrow_lookup:
                        await safe_edit_message_text(query, "❌ Trade not found.")
                        return CONV_END
                    logger.warning(f"ID_MAPPING_WARNING: Found escrow by escrow_id fallback for {escrow_string_id}")
                else:
                    logger.info(f"ID_MAPPING_SUCCESS: Found escrow by utid {escrow_string_id}")
                escrow_id = escrow_lookup.id
    except (IndexError, ValueError) as e:
        # ENHANCED: Use correlated error handling
        from utils.enhanced_error_handler import CorrelatedErrorHandler
        await CorrelatedErrorHandler.handle_error_with_correlation(
            query_or_update=query,
            user_message="❌ Invalid trade ID format.",
            backend_error=f"Trade ID parsing failed: callback='{callback_data}', error={str(e)}",
            handler_name="handle_view_trade",
            callback_data=callback_data
        )
        return CONV_END

    try:
        async with async_managed_session() as session:
            # Get the escrow
            escrow_stmt = select(Escrow).where(Escrow.id == escrow_id)
            escrow_result = await session.execute(escrow_stmt)
            escrow = escrow_result.scalar_one_or_none()
            
            if not escrow:
                await safe_edit_message_text(
                    query,
                    "❌ Trade Not Found\n\nThis trade may have been completed or cancelled.",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
                    ])
                )
                return CONV_END

            # Check user permissions
            user_stmt = select(User).where(User.telegram_id == update.effective_user.id)
            user_result = await session.execute(user_stmt)
            user = user_result.scalar_one_or_none()
            
            if not user:
                await safe_edit_message_text(query, "❌ User not found. Please restart with /start")
                return CONV_END

            # Verify user is involved in this trade
            user_role = None
            if getattr(escrow, 'buyer_id', None) == user.id:
                user_role = "buyer"
            elif getattr(escrow, 'seller_id', None) == user.id:
                user_role = "seller"
            elif getattr(escrow, 'seller_contact_type', None) == 'username' and getattr(escrow, 'seller_contact_value', None) == user.username and not escrow.seller_id:  # type: ignore
                user_role = "seller"
            
            if not user_role:
                await safe_edit_message_text(
                    query,
                    "❌ Access Denied\n\nYou can only view trades you're involved in.",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
                    ])
                )
                return CONV_END

            # Build detailed trade information using consistent branding ID
            escrow_branding_id = getattr(escrow, 'utid', None) or getattr(escrow, 'escrow_id', 'N/A')
            # Format branding ID consistently (last 8 chars for display)
            trade_id = escrow_branding_id if escrow_branding_id else "N/A"  # Show full Trade ID with prefix
            amount = Decimal(str(getattr(escrow, 'amount', 0)))
            status = getattr(escrow, 'status', 'unknown')
            description = getattr(escrow, 'description', 'No description')
            created_at = getattr(escrow, 'created_at', None)
        
            # Status display - check if cancelled trade was refunded
            is_refunded = False
            if status == "cancelled":
                # Check if a refund transaction exists for this trade
                from models import Transaction
                refund_stmt = select(Transaction).where(
                    Transaction.escrow_id == escrow.id,
                    Transaction.transaction_type.in_(["refund", "escrow_refund"]),
                    Transaction.status == "completed"
                )
                refund_result = await session.execute(refund_stmt)
                refund_transaction = refund_result.scalar_one_or_none()
                is_refunded = refund_transaction is not None
        
            # Simplified status messages
            STATUS_DISPLAY = {
                "active": "🔄 IN PROGRESS",
                "completed": "✅ COMPLETED", 
                "cancelled": "❌ CANCELLED",
                "refunded": "💸 REFUNDED",
                "disputed": "🔥 DISPUTED",
                "pending_deposit": "⏳ AWAITING PAYMENT",
                "payment_confirmed": "✅ PAYMENT RECEIVED",
                "awaiting_seller": "⏳ AWAITING SELLER",
                "pending_seller": "⏳ AWAITING SELLER",
                "payment_pending": "⏳ AWAITING PAYMENT",
                "partial_payment": "⏳ AWAITING PAYMENT",
                "payment_failed": "❌ PAYMENT FAILED",
                "created": "⏳ AWAITING PAYMENT",
                "expired": "⏰ EXPIRED",
            }
            status_display = STATUS_DISPLAY.get(status, "❓ UNKNOWN")

            # Get counterparty information (with new typed contact fields support)
            counterparty_name = "Unknown"
            if user_role == "buyer":
                # First priority: seller_contact_display (buyer's chosen contact method)
                if getattr(escrow, 'seller_contact_display', None):
                    counterparty_name = str(escrow.seller_contact_display)
                # Second priority: existing seller with ID
                elif getattr(escrow, 'seller_id', None):
                    seller_stmt = select(User).where(User.id == escrow.seller_id)
                    seller_result = await session.execute(seller_stmt)
                    seller = seller_result.scalar_one_or_none()
                    if seller:
                        counterparty_name = str(getattr(seller, 'first_name', 'Seller') or 'Seller')
                # Third priority: seller_email fallback
                elif getattr(escrow, 'seller_email', None):
                    counterparty_name = str(escrow.seller_email)
            elif user_role == "seller" and getattr(escrow, 'buyer_id', None):
                buyer_stmt = select(User).where(User.id == escrow.buyer_id)
                buyer_result = await session.execute(buyer_stmt)
                buyer = buyer_result.scalar_one_or_none()
                if buyer:
                    # Use buyer's username (platform users always have username)
                    counterparty_name = str(getattr(buyer, 'username', None) or getattr(buyer, 'first_name', 'Buyer') or 'Buyer')

            # Build message text with comprehensive timestamp information
            created_str = created_at.strftime("%b %d, %Y %I:%M %p") if created_at else "Unknown"
        
            # Comprehensive timestamp formatting for user context
            def format_timestamp(timestamp, default="Unknown"):
                """Format timestamp in user-friendly format"""
                if timestamp:
                    try:
                        return timestamp.strftime("%b %d, %Y %I:%M %p")
                    except (AttributeError, ValueError):
                        return default
                return default
        
            def format_timestamp_short(timestamp, default="Unknown"):
                """Format timestamp in shorter format (without year if current year)"""
                if timestamp:
                    try:
                        from datetime import datetime, timezone
                        current_year = datetime.now(timezone.utc).year
                        ts_year = timestamp.year if hasattr(timestamp, 'year') else None
                        if ts_year == current_year:
                            return timestamp.strftime("%b %d, %I:%M %p")
                        return timestamp.strftime("%b %d, %Y")
                    except (AttributeError, ValueError):
                        return default
                return default
        
            # Build simplified, status-specific timestamp information
            timestamp_info = ""
            from datetime import datetime, timezone, timedelta
            from config import Config as AppConfig
        
            # Query dispute details for any status (to show dispute history if applicable)
            from models import Dispute
            dispute_stmt = select(Dispute).where(Dispute.escrow_id == escrow.id).order_by(Dispute.created_at.desc())
            dispute_result = await session.execute(dispute_stmt)
            dispute = dispute_result.scalar_one_or_none()
            
            # Status-specific timestamps (only show what matters)
            if status == "completed":
                # Show complete trade timeline for completed trades
                timestamp_info = ""
                
                # Completed time (with fallback to delivered_at for legacy trades)
                completed_at = getattr(escrow, 'completed_at', None) or getattr(escrow, 'delivered_at', None)
                if completed_at:
                    timestamp_info = f"\n✅ Completed: {format_timestamp_short(completed_at)}"
                
                # Delivered time
                delivered_at = getattr(escrow, 'delivered_at', None)
                if delivered_at:
                    timestamp_info += f"\n📦 Delivered: {format_timestamp_short(delivered_at)}"
                
                # Paid time
                payment_confirmed_at = getattr(escrow, 'payment_confirmed_at', None)
                if payment_confirmed_at:
                    timestamp_info += f"\n✅ Paid: {format_timestamp_short(payment_confirmed_at)}"
                
                # Accepted time
                seller_accepted_at = getattr(escrow, 'seller_accepted_at', None)
                if seller_accepted_at:
                    timestamp_info += f"\n🤝 Accepted: {format_timestamp_short(seller_accepted_at)}"
                
                # Created time
                if created_at:
                    timestamp_info += f"\n📝 Created: {format_timestamp_short(created_at)}"
                
                # Show dispute history if trade was previously disputed
                if dispute:
                    dispute_opened_at = getattr(dispute, 'created_at', None)
                    dispute_resolved_at = getattr(dispute, 'resolved_at', None)
                    if dispute_opened_at:
                        timestamp_info += f"\n🔥 Disputed: {format_timestamp_short(dispute_opened_at)}"
                    if dispute_resolved_at:
                        timestamp_info += f"\n🏁 Resolved: {format_timestamp_short(dispute_resolved_at)}"
                
            elif status == "cancelled":
                cancelled_at = getattr(escrow, 'completed_at', None) or getattr(escrow, 'updated_at', None)
                # Only show cancelled timestamp if it differs from created
                if cancelled_at and created_at:
                    # Fix timezone awareness mismatch before subtraction
                    cancelled_at_aware = cancelled_at.replace(tzinfo=timezone.utc) if cancelled_at.tzinfo is None else cancelled_at
                    created_at_aware = created_at.replace(tzinfo=timezone.utc) if created_at.tzinfo is None else created_at
                    time_diff = (cancelled_at_aware - created_at_aware).total_seconds()
                    if time_diff > 300:  # More than 5 minutes difference
                        timestamp_info = f"\n❌ {format_timestamp_short(cancelled_at)}"
                    else:
                        # Quick cancellation - just show single timestamp
                        timestamp_info = f"\n❌ {format_timestamp_short(cancelled_at)}"
                else:
                    timestamp_info = f"\n❌ {format_timestamp_short(cancelled_at)}"
                
            elif status == "payment_confirmed":
                # Show comprehensive timeline for payment_confirmed status
                timestamp_info = ""
                
                # 1. PRIORITY: Show when seller needs to accept by - DYNAMIC TIME REMAINING
                payment_confirmed_at = getattr(escrow, 'payment_confirmed_at', None)
                expires_at = getattr(escrow, 'expires_at', None)
                
                # Calculate seller acceptance deadline - ALWAYS recalculate from payment_confirmed_at
                if payment_confirmed_at:
                    from config import Config as AppConfig
                    seller_timeout_minutes = getattr(AppConfig, 'SELLER_RESPONSE_TIMEOUT_MINUTES', 1440)  # 24 hours
                    calculated_expires = payment_confirmed_at + timedelta(minutes=seller_timeout_minutes)
                    if not expires_at or calculated_expires > expires_at:
                        expires_at = calculated_expires
                
                if expires_at:
                    current_time = datetime.now(timezone.utc)
                    expires_at_aware = expires_at.replace(tzinfo=timezone.utc) if expires_at.tzinfo is None else expires_at
                    time_remaining = expires_at_aware - current_time
                    
                    if time_remaining.total_seconds() > 0:
                        total_minutes = int(time_remaining.total_seconds() / 60)
                        hours = total_minutes // 60
                        minutes = total_minutes % 60
                        
                        if hours > 0 and minutes > 0:
                            timestamp_info = f"\n⏰ Seller has {hours}h {minutes}m left to accept"
                        elif hours > 0:
                            timestamp_info = f"\n⏰ Seller has {hours}h left to accept"
                        else:
                            timestamp_info = f"\n⏰ Seller has {minutes}m left to accept"
                    else:
                        timestamp_info = f"\n⏰ Acceptance deadline expired"
                else:
                    # Fallback to static message if no time info available
                    timestamp_info = f"\n⏰ Seller has 24h to accept"
                
                # 2. Show payment confirmation timestamp
                if payment_confirmed_at:
                    timestamp_info += f"\n💳 Paid: {format_timestamp_short(payment_confirmed_at)}"
                
                # 3. Show delivery deadline with full timestamp (not just date)
                delivery_deadline = getattr(escrow, 'delivery_deadline', None)
                if delivery_deadline:
                    timestamp_info += f"\n📦 Delivery by: {format_timestamp_short(delivery_deadline)}"
                
                # 4. Show when trade was created for complete timeline
                if created_at:
                    timestamp_info += f"\n📝 Created: {format_timestamp_short(created_at)}"
                
            elif status == "active":
                # Show delivery deadline and when trade started
                delivery_deadline = getattr(escrow, 'delivery_deadline', None)
                delivered_at = getattr(escrow, 'delivered_at', None)
                
                # Show delivered timestamp if item has been marked as delivered
                if delivered_at:
                    timestamp_info = f"\n📦 Delivered: {format_timestamp_short(delivered_at)}"
                elif delivery_deadline:
                    timestamp_info = f"\n📦 Delivery by: {format_timestamp_short(delivery_deadline)}"
                
                payment_confirmed_at = getattr(escrow, 'payment_confirmed_at', None)
                if payment_confirmed_at:
                    timestamp_info += f"\n✅ Paid: {format_timestamp_short(payment_confirmed_at)}"
                
                # Add seller accepted timestamp
                seller_accepted_at = getattr(escrow, 'seller_accepted_at', None)
                if seller_accepted_at:
                    timestamp_info += f"\n🤝 Accepted: {format_timestamp_short(seller_accepted_at)}"
                
                # Add started timestamp (creation time)
                started_at = getattr(escrow, 'created_at', None)
                if started_at:
                    timestamp_info += f"\n📝 Created: {format_timestamp_short(started_at)}"
        
            elif status in ["payment_pending", "pending_deposit", "created"]:
                # Show payment deadline with time remaining
                expires_at = getattr(escrow, 'expires_at', None)
                if not expires_at and created_at:
                    payment_timeout_minutes = getattr(AppConfig, 'PAYMENT_TIMEOUT_MINUTES', 15)
                    expires_at = created_at + timedelta(minutes=payment_timeout_minutes)
            
                if expires_at:
                    current_time = datetime.now(timezone.utc)
                    expires_at_aware = expires_at.replace(tzinfo=timezone.utc) if expires_at.tzinfo is None else expires_at
                    time_remaining = expires_at_aware - current_time
                
                    if time_remaining.total_seconds() > 0:
                        minutes_left = int(time_remaining.total_seconds() / 60)
                        if minutes_left < 60:
                            timestamp_info = f"\n⏰ Pay by: {format_timestamp_short(expires_at)} ({minutes_left} min left)"
                        else:
                            hours_left = minutes_left / 60
                            timestamp_info = f"\n⏰ Pay by: {format_timestamp_short(expires_at)} ({hours_left:.1f}h left)"
                    else:
                        timestamp_info = f"\n⏰ Payment expired: {format_timestamp_short(expires_at)}"
        
            elif status == "expired":
                expired_at = getattr(escrow, 'updated_at', None) or getattr(escrow, 'expires_at', None)
                if expired_at:
                    timestamp_info = f"\n⏰ Expired: {format_timestamp_short(expired_at)}"
        
            elif status == "disputed":
                # Show comprehensive dispute information
                timestamp_info = ""
                
                # Use dispute queried at the top level
                if dispute:
                    # Dispute opened time
                    dispute_opened_at = getattr(dispute, 'created_at', None)
                    if dispute_opened_at:
                        timestamp_info = f"\n🔥 Disputed: {format_timestamp_short(dispute_opened_at)}"
                    
                    # Dispute status
                    dispute_status = getattr(dispute, 'status', 'open')
                    if dispute_status == 'under_review':
                        timestamp_info += f"\n⚖️ Status: Under Admin Review"
                    elif dispute_status == 'resolved':
                        timestamp_info += f"\n✅ Status: Resolved"
                        
                        # Show resolution time if resolved
                        dispute_resolved_at = getattr(dispute, 'resolved_at', None)
                        if dispute_resolved_at:
                            timestamp_info += f"\n🏁 Resolved: {format_timestamp_short(dispute_resolved_at)}"
                        
                        # Show resolution details if available
                        resolution = getattr(dispute, 'resolution', None)
                        if resolution:
                            # Truncate long resolutions
                            resolution_display = resolution[:80] + "..." if len(resolution) > 80 else resolution
                            timestamp_info += f"\n📋 Decision: {resolution_display}"
                    else:
                        timestamp_info += f"\n⚖️ Status: Open"
                else:
                    # Fallback if no dispute record found
                    updated_at = getattr(escrow, 'updated_at', None)
                    if updated_at:
                        timestamp_info = f"\n🔥 Disputed: {format_timestamp_short(updated_at)}"
        
            elif status == "refunded":
                refunded_at = getattr(escrow, 'completed_at', None) or getattr(escrow, 'updated_at', None)
                if refunded_at:
                    timestamp_info = f"\n💸 {format_timestamp_short(refunded_at)}"
                
                # Show dispute history if trade was disputed before refund
                if dispute:
                    dispute_opened_at = getattr(dispute, 'created_at', None)
                    dispute_resolved_at = getattr(dispute, 'resolved_at', None)
                    resolution = getattr(dispute, 'resolution', None)
                    
                    if dispute_opened_at:
                        timestamp_info += f"\n🔥 Disputed: {format_timestamp_short(dispute_opened_at)}"
                    if dispute_resolved_at:
                        timestamp_info += f"\n🏁 Resolved: {format_timestamp_short(dispute_resolved_at)}"
                    if resolution:
                        resolution_display = resolution[:80] + "..." if len(resolution) > 80 else resolution
                        timestamp_info += f"\n📋 Decision: {resolution_display}"
        
            # Add refund info ONLY if already flagged as refunded (no extra query)
            refund_info = ""
            if is_refunded and status in ["cancelled", "refunded", "expired"]:
                if status == "expired":
                    refund_info = "\n💸 Refunded"
                else:
                    refund_info = "\n💸 Refunded"
        
            # Get fee information - handle None values
            fee_split_option = getattr(escrow, 'fee_split_option', 'buyer_pays')
            fee_paid_by = fee_split_option  # Use fee_split_option (fee_paid_by column doesn't exist)
        
            # Safely get fee amounts with None handling
            buyer_fee_raw = getattr(escrow, 'buyer_fee_amount', None)
            seller_fee_raw = getattr(escrow, 'seller_fee_amount', None)
            fee_amount_raw = getattr(escrow, 'fee_amount', None)
        
            # Convert to float safely
            buyer_fee = Decimal(str(buyer_fee_raw)) if buyer_fee_raw is not None else Decimal("0.0")
            seller_fee = Decimal(str(seller_fee_raw)) if seller_fee_raw is not None else Decimal("0.0")
        
            # If individual fees are 0, use total fee_amount
            if buyer_fee == 0 and seller_fee == 0 and fee_amount_raw is not None:
                total_fee = Decimal(str(fee_amount_raw))
                # Determine individual fees based on fee_paid_by
                if fee_paid_by == 'buyer' or fee_paid_by == 'buyer_pays':
                    buyer_fee = total_fee
                    seller_fee = 0
                elif fee_paid_by == 'seller' or fee_paid_by == 'seller_pays':
                    buyer_fee = 0
                    seller_fee = total_fee
                elif fee_paid_by == 'split':
                    buyer_fee = total_fee / 2
                    seller_fee = total_fee / 2
            else:
                total_fee = buyer_fee + seller_fee
        
            # Check for first trade free promotion (buyer fee waived)
            is_first_trade_free = (user_role == "buyer" and buyer_fee == 0 and seller_fee > 0)
        
            # Format fee display - simplified
            fee_info = ""
            if status not in ["cancelled", "expired", "refunded", "completed"]:  # Only show fees for active/pending trades
                if is_first_trade_free:
                    fee_info = f"Fee: ${total_fee:.2f} USD (FREE for you!)\n"
                elif fee_split_option == 'seller_pays':
                    if user_role == "seller":
                        fee_info = f"Fee: ${total_fee:.2f} USD\n"
                    else:
                        fee_info = f"Fee: ${total_fee:.2f} USD\n"
                elif fee_split_option == 'buyer_pays':
                    if user_role == "buyer":
                        fee_info = f"Fee: ${total_fee:.2f} USD\n"
                    else:
                        fee_info = f"Fee: ${total_fee:.2f} USD\n"
                elif fee_split_option == 'split':
                    fee_info = f"Fee: ${total_fee:.2f} USD\n"
        
            # Get payment method information
            payment_method = getattr(escrow, 'payment_method', None)
            payment_info = ""
            if payment_method:
                if payment_method.startswith('crypto_'):
                    crypto_currency = payment_method.replace('crypto_', '')
                    payment_info = f"💰 <b>Payment:</b> {crypto_currency} (Crypto)"
                elif payment_method == 'ngn':
                    payment_info = f"💰 <b>Payment:</b> Nigerian Naira (NGN)"
                elif payment_method == 'wallet':
                    payment_info = f"💰 <b>Payment:</b> Wallet Balance"
                else:
                    payment_info = f"💰 <b>Payment:</b> {payment_method.upper()}"
        
            # Ratings removed to improve performance - view separately via rating button
            ratings_info = ""

            # FIXED: Ensure description and delivery details are properly displayed
            display_description = description if description and description.strip() and description != "No description" else "Description not provided"
        
            # Add delivery information display only for relevant statuses
            delivery_info = ""
            if status in ["active", "payment_confirmed"]:
                delivery_deadline = getattr(escrow, 'delivery_deadline', None)
                if delivery_deadline:
                    try:
                        delivery_str = delivery_deadline.strftime("%b %d, %Y")
                        delivery_info = f"\n📦 <b>Delivery:</b> {delivery_str}"
                    except (AttributeError, ValueError):
                        pass

            # Format payment method in shorter way
            payment_short = ""
            if payment_method:
                if payment_method.startswith('crypto_'):
                    crypto_currency = payment_method.replace('crypto_', '')
                    payment_short = f" ({crypto_currency})"
            
            # Build concise status-specific details
            # Simplify the display based on status
            if status in ["cancelled", "expired", "refunded"]:
                # Minimal info for completed/cancelled trades
                details_text = f"""<b>#{trade_id}</b> {status_display}

    <b>${amount:.2f} → {format_username_html(counterparty_name, include_link=False)}{payment_short}</b>
    {fee_info}{refund_info}{timestamp_info}

    📝 {display_description}{ratings_info}
    """
            elif status == "completed":
                # Show completion info
                details_text = f"""<b>#{trade_id}</b> {status_display}

    <b>${amount:.2f} → {format_username_html(counterparty_name, include_link=False)}{payment_short}</b>
    <b>Role:</b> {user_role.title()}
    {timestamp_info}

    📝 {display_description}{ratings_info}
    """
            elif status == "disputed":
                # Show dispute info
                details_text = f"""<b>#{trade_id}</b> {status_display}

    <b>${amount:.2f} → {format_username_html(counterparty_name, include_link=False)}{payment_short}</b>
    <b>Role:</b> {user_role.title()}
    {timestamp_info}

    📝 {display_description}
    ⚠️ Admin reviewing...{ratings_info}
    """
            else:
                # Default format for active/pending trades
                details_text = f"""<b>#{trade_id}</b> {status_display}

    <b>${amount:.2f} → {format_username_html(counterparty_name, include_link=False)}{payment_short}</b>
    {fee_info}
    <b>Role:</b> {user_role.title()}
    {timestamp_info}

    📝 {display_description}{delivery_info}{ratings_info}
    """

            # Build action buttons based on status and role
            keyboard_buttons = []
        
            # Debug logging for Release Funds button visibility
            logger.info(f"🔍 Trade view debug - Trade ID: {trade_id}, Status: {status}, User Role: {user_role}, User ID: {user.id}, Buyer ID: {getattr(escrow, 'buyer_id', None)}, Seller ID: {getattr(escrow, 'seller_id', None)}")
        
            # Actions based on trade status
            if status == "active":
                if user_role == "seller":
                    # Only show "Mark Delivered" if not already delivered
                    if not getattr(escrow, 'delivered_at', None):
                        keyboard_buttons.append([
                            InlineKeyboardButton("✅ Mark Delivered", callback_data=f"mark_delivered_{escrow.id}")
                        ])
                elif user_role == "buyer":
                    # Buyer can always release funds during active trade (once they receive item)
                    # No need to wait for seller to mark as delivered
                    keyboard_buttons.append([
                        InlineKeyboardButton("✅ Release Funds", callback_data=f"release_funds_{escrow.id}")
                    ])
            
                keyboard_buttons.append([
                    InlineKeyboardButton("💬 Chat", callback_data=f"trade_chat_open:{escrow.id}"),
                    InlineKeyboardButton("⚠️ Report Issue", callback_data=f"dispute_trade:{escrow.id}")
                ])
        
            # Chat available for all trade statuses (active, payment_pending, completed, etc.)
            elif status in ["payment_pending", "payment_confirmed", "completed", "disputed"]:
                # SELLER ACCEPT/DECLINE BUTTONS: Add for sellers when trade is payment_confirmed (awaiting seller acceptance)
                if status == "payment_confirmed" and user_role == "seller":
                    keyboard_buttons.append([
                        InlineKeyboardButton("✅ Accept Trade", callback_data=f"accept_trade:{escrow.utid or escrow.escrow_id}"),  # type: ignore
                        InlineKeyboardButton("❌ Decline Trade", callback_data=f"decline_trade:{escrow.utid or escrow.escrow_id}")  # type: ignore
                    ])
            
                # BUYER CANCEL BUTTON: Add for buyers when trade is payment_confirmed (seller hasn't accepted)
                if status == "payment_confirmed" and user_role == "buyer":
                    keyboard_buttons.append([
                        InlineKeyboardButton("❌ Cancel Trade", callback_data=f"buyer_cancel_{escrow.escrow_id}")
                    ])
            
                # Add appropriate buttons based on status
                if status == "completed":
                    # Check if user has already rated this trade
                    from models import Rating
                    rating_stmt = select(Rating).where(
                        Rating.escrow_id == escrow.id,
                        Rating.rater_id == user.id
                    )
                    rating_result = await session.execute(rating_stmt)
                    existing_rating = rating_result.scalar_one_or_none()
                    
                    # If no rating exists, show rating button
                    if not existing_rating:
                        # Get counterparty username for rating button label
                        counterparty_username = None
                        if user_role == "buyer" and getattr(escrow, 'seller_id', None):
                            seller_stmt = select(User).where(User.id == escrow.seller_id)
                            seller_result = await session.execute(seller_stmt)
                            seller_user = seller_result.scalar_one_or_none()
                            if seller_user:
                                counterparty_username = f"@{seller_user.username}" if seller_user.username else seller_user.first_name
                        elif user_role == "seller" and getattr(escrow, 'buyer_id', None):
                            buyer_stmt = select(User).where(User.id == escrow.buyer_id)
                            buyer_result = await session.execute(buyer_stmt)
                            buyer_user = buyer_result.scalar_one_or_none()
                            if buyer_user:
                                counterparty_username = f"@{buyer_user.username}" if buyer_user.username else buyer_user.first_name
                        
                        # Show rating button with counterparty username
                        rating_label = f"⭐ Rate {counterparty_username}" if counterparty_username else "⭐ Rate Trade"
                        keyboard_buttons.append([
                            InlineKeyboardButton(rating_label, callback_data=f"rate_escrow_{escrow.id}")
                        ])
                    
                    # Navigation buttons
                    keyboard_buttons.append([
                        InlineKeyboardButton("📋 My Trades", callback_data="trades_messages_hub"),
                        InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")
                    ])
                elif status in ["payment_confirmed", "payment_pending"]:
                    # Pre-active trades - NO CHAT until seller accepts
                    # Chat only becomes available after trade is active
                
                    # CRITICAL FIX: Add PAY BUTTON for payment_pending escrows
                    if status == "payment_pending" and user_role == "buyer":
                        # Check if escrow payment timeout has expired (similar to exchange rate lock expiry)
                        from datetime import datetime, timedelta
                        from config import Config as AppConfig  # Use function-level import to avoid conflicts
                    
                        payment_timeout_minutes = AppConfig.PAYMENT_TIMEOUT_MINUTES
                        created_at = getattr(escrow, 'created_at', None)
                    
                        # Calculate if payment window is still valid
                        if created_at:
                            payment_expires_at = created_at + timedelta(minutes=payment_timeout_minutes)
                            from datetime import timezone
                            current_time = datetime.now(timezone.utc)
                            # Make payment_expires_at timezone-aware if it isn't
                            payment_expires_at_aware = payment_expires_at.replace(tzinfo=timezone.utc) if payment_expires_at.tzinfo is None else payment_expires_at
                            payment_window_valid = current_time < payment_expires_at_aware
                        else:
                            payment_window_valid = True  # Allow payment if no created_at timestamp
                    
                        if payment_window_valid:
                            # Show pay button for valid payment window
                            keyboard_buttons.append([
                                InlineKeyboardButton("💳 Pay Now", callback_data=f"pay_escrow_{escrow.id}")
                            ])
                            # Also show cancel option
                            keyboard_buttons.append([
                                InlineKeyboardButton("❌ Cancel Trade", callback_data=f"buyer_cancel_{escrow.escrow_id}")
                            ])
                        else:
                            # Payment window expired - show create new trade option
                            keyboard_buttons.append([
                                InlineKeyboardButton("🔄 Create New Trade", callback_data="menu_create"),
                                InlineKeyboardButton("❌ Cancel Trade", callback_data=f"buyer_cancel_{escrow.escrow_id}")
                            ])
                elif status == "disputed":
                    # Check if dispute is resolved - add rating options if so
                    # Dispute model already imported above
                    dispute_stmt = select(Dispute).where(Dispute.escrow_id == escrow.id)
                    dispute_result = await session.execute(dispute_stmt)
                    dispute = dispute_result.scalar_one_or_none()
                
                    if dispute and dispute.status in ['resolved', 'closed']:
                        # Resolved disputes get rating options only (no chat)
                    
                        # Add rating buttons for resolved disputes
                        if user_role == "buyer":
                            keyboard_buttons.append([
                                InlineKeyboardButton("⭐ Rate Seller", callback_data=f"rate_seller_{escrow.id}")
                            ])
                        elif user_role == "seller":
                            keyboard_buttons.append([
                                InlineKeyboardButton("⭐ Rate Buyer", callback_data=f"rate_buyer_{escrow.id}")
                            ])
                    else:
                        # Active disputed trades - route to dispute chat interface
                        if dispute:
                            keyboard_buttons.append([
                                InlineKeyboardButton("💬 Dispute Chat", callback_data=f"view_dispute:{dispute.id}")
                            ])
                        else:
                            # Fallback if no dispute found (shouldn't happen)
                            keyboard_buttons.append([
                                InlineKeyboardButton("💬 Chat", callback_data=f"trade_chat_open:{escrow.id}")
                            ])

            # Add navigation button for non-completed trades (completed trades have their own navigation)
            if status != "completed":
                keyboard_buttons.append([
                    InlineKeyboardButton("🔙 My Trades", callback_data="trades_messages_hub")
                ])

            # CRITICAL FIX: Delete and resend instead of edit to avoid Telegram rate limiting
            # When clicking "Back to Trade" from cancellation message, editing too quickly
            # causes Telegram to silently ignore the edit
            try:
                # Delete the old message
                await query.message.delete()  # type: ignore
                # Send new message with trade details
                await query.message.chat.send_message(  # type: ignore
                    details_text,
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup(keyboard_buttons)
                )
                logger.info(f"✅ Trade view sent as new message (deleted old) for escrow {escrow.id}")
            except Exception as delete_error:
                logger.warning(f"Failed to delete/resend, falling back to edit: {delete_error}")
                # Fallback to edit if delete fails
                await safe_edit_message_text(
                    query,
                    details_text,
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup(keyboard_buttons)
                )
    except Exception as e:
        logger.error(f"Error in view_trade handler: {e}")
        await safe_edit_message_text(query, "❌ Error loading trade details. Please try again.")

    return CONV_END


async def handle_mark_delivered(update: TelegramUpdate, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle mark_delivered callback for sellers"""
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "✅ Marking as delivered...")

    if not update.effective_user:
        return CONV_END

    # Extract escrow ID from callback data (format: "mark_delivered_123")
    callback_data = query.data if query else ""
    if not callback_data.startswith('mark_delivered_'):  # type: ignore
        await query.edit_message_text("❌ Invalid delivery confirmation request.")  # type: ignore
        return CONV_END

    try:
        escrow_id = callback_data.split('_')[2]  # type: ignore
        escrow_id = int(escrow_id)
        logger.info(f"✅ mark_delivered parsing successful: callback='{callback_data}', escrow_id={escrow_id}")
    except (IndexError, ValueError) as e:
        # ENHANCED: Use correlated error handling  
        from utils.enhanced_error_handler import CorrelatedErrorHandler
        correlation_id = await CorrelatedErrorHandler.handle_error_with_correlation(
            query_or_update=query,
            user_message="❌ Invalid trade ID format.",
            backend_error=f"mark_delivered parsing failed: callback='{callback_data}', error={str(e)}",
            handler_name="mark_delivered",
            callback_data=callback_data
        )
        logger.error(f"❌ [CORR:{correlation_id[:8]}] mark_delivered parsing failed: callback='{callback_data}', error={e}")
        return CONV_END

    # INSTANT FEEDBACK: Show processing message immediately
    from utils.callback_utils import safe_edit_message_text
    from telegram import InlineKeyboardMarkup
    
    processing_message = (
        f"⏳ *Processing Delivery Confirmation...*\n\n"
        f"Please wait while we notify the buyer and update the trade status..."
    )
    
    await safe_edit_message_text(
        query,
        processing_message,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([])  # Remove buttons during processing
    )
    
    logger.info(f"✅ Seller UI immediately updated with processing message for delivery confirmation")

    async with async_managed_session() as session:
        try:
            # Get the escrow
            escrow_stmt = select(Escrow).where(Escrow.id == escrow_id)
            escrow_result = await session.execute(escrow_stmt)
            escrow = escrow_result.scalar_one_or_none()
            
            if not escrow:
                await query.edit_message_text("❌ Trade not found.")  # type: ignore
                return CONV_END

            # Check user permissions (only seller can mark delivered)
            user_stmt = select(User).where(User.telegram_id == update.effective_user.id)
            user_result = await session.execute(user_stmt)
            user = user_result.scalar_one_or_none()
            
            if not user or getattr(escrow, 'seller_id', None) != user.id:
                await query.edit_message_text("❌ Only the seller can mark items as delivered.")  # type: ignore
                return CONV_END

            # Check if trade is in active status
            if getattr(escrow, 'status', '') != 'active':
                await query.edit_message_text("❌ Only active trades can be marked as delivered.")  # type: ignore
                return CONV_END

            # Extract ALL needed data BEFORE commit to avoid session issues
            buyer_id = escrow.buyer_id  # type: ignore
            escrow_id_str = str(escrow.escrow_id)  # type: ignore
            escrow_amount = escrow.amount if escrow.amount else Decimal('0')  # type: ignore
            currency = str(escrow.currency or "USD")  # type: ignore
            payment_method = str(escrow.payment_method or "N/A")  # type: ignore
            description = str(escrow.description or "N/A")  # type: ignore
            
            # Extract seller data BEFORE commit
            seller_name = str(user.first_name or user.username or "Seller")  # type: ignore
            seller_email = str(user.email) if user.email else None  # type: ignore
            seller_verified = bool(user.is_verified) if hasattr(user, 'is_verified') else False  # type: ignore
            
            # Get buyer info and extract data BEFORE commit
            buyer_stmt = select(User).where(User.id == buyer_id)
            buyer_result = await session.execute(buyer_stmt)
            buyer = buyer_result.scalar_one_or_none()
            
            # Extract buyer data BEFORE commit
            buyer_email = None
            buyer_name = "Buyer"
            buyer_verified = False
            if buyer:
                buyer_email = str(buyer.email) if buyer.email else None  # type: ignore
                buyer_name = str(buyer.first_name or buyer.username or "Buyer")  # type: ignore
                buyer_verified = bool(buyer.is_verified) if hasattr(buyer, 'is_verified') else False  # type: ignore
            
            # Mark as delivered but keep status as active (buyer still needs to release funds)
            # DO NOT change status to COMPLETED - that happens after buyer releases funds
            escrow.delivered_at = datetime.now(timezone.utc)  # type: ignore
            
            await session.commit()

            # Send notifications using extracted data
            from services.consolidated_notification_service import consolidated_notification_service as NotificationService
            await NotificationService.send_delivery_notification(
                buyer_id,  # type: ignore[arg-type]
                escrow_id_str,
                seller_name,
                escrow_amount
            )
            
            # Send email notification to buyer about delivery confirmation
            if buyer_email and buyer_verified:
                try:
                    from services.email import EmailService
                    email_service = EmailService()
                    await email_service.send_trade_notification(
                        buyer_email,
                        buyer_name,
                        escrow_id_str,
                        "delivery_confirmed",
                        {
                            "amount": escrow_amount,
                            "currency": currency,
                            "status": "delivered",
                            "seller": seller_name,
                            "payment_method": payment_method,
                            "description": description,
                        },
                    )
                    logger.info(f"Delivery notification email sent to buyer {buyer_email}")
                except Exception as e:
                    logger.error(f"Failed to send delivery notification email to buyer: {e}")
            
            # Send email notification to seller about successful delivery marking
            if seller_email and seller_verified:
                try:
                    from services.email import EmailService
                    email_service = EmailService()
                    await email_service.send_trade_notification(
                        seller_email,
                        seller_name,
                        escrow_id_str,
                        "delivery_marked",
                        {
                            "amount": escrow_amount,
                            "currency": currency,
                            "status": "delivered",
                            "buyer": buyer_name,
                            "payment_method": payment_method,
                            "description": description,
                        },
                    )
                    logger.info(f"Delivery marked notification email sent to seller {seller_email}")
                except Exception as e:
                    logger.error(f"Failed to send delivery marked notification email to seller: {e}")
            
            # Send Telegram group notification for item delivered
            try:
                delivery_data = {
                    'escrow_id': escrow_id_str,
                    'seller_info': seller_name,
                    'buyer_info': buyer_name,
                    'amount': float(escrow_amount),
                    'delivered_at': datetime.now(timezone.utc)
                }
                admin_notif_service = AdminTradeNotificationService()
                asyncio.create_task(admin_notif_service.send_group_notification_item_delivered(delivery_data))
                logger.info(f"📤 Queued group notification for item delivered: {escrow_id_str}")
            except Exception as notif_err:
                logger.error(f"❌ Failed to queue item delivered group notification: {notif_err}")

            await query.edit_message_text(  # type: ignore
                f"✅ Delivery Confirmed\n\n"
                f"Trade #{escrow_id_str[-6:]} has been marked as delivered.\n"
                f"The buyer has been notified to release the funds.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📋 My Trades", callback_data="trades_messages_hub")],
                    [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
                ])
            )

        except Exception as e:
            logger.error(f"Error in mark_delivered handler: {e}")
            await query.edit_message_text("❌ Error processing delivery confirmation. Please try again.")  # type: ignore

    return CONV_END


async def handle_release_funds(update: TelegramUpdate, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle release_funds callback for buyers - shows confirmation first"""
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query)  # Acknowledge click
        await query.edit_message_text("⏳ Loading release confirmation...")  # Instant visual feedback

    if not update.effective_user:
        return CONV_END

    # Extract escrow ID from callback data (format: "release_funds_123" or "release_funds_ES1016256NPX")
    callback_data = query.data if query else ""
    if not callback_data or not callback_data.startswith('release_funds_'):  # type: ignore
        await query.edit_message_text("❌ Invalid fund release request.")  # type: ignore
        return CONV_END

    async with async_managed_session() as session:
        try:
            # Extract ID part from callback
            parts = callback_data.split('_', 2) if callback_data else []
            id_part = parts[2] if len(parts) >= 3 else ""
            
            # Determine if ID is numeric or alphanumeric (escrow_id format)
            if id_part.isdigit():
                # Format: "release_funds_2" (numeric ID)
                escrow_id = int(id_part)
                logger.info(f"✅ release_funds numeric ID: {escrow_id}")
            else:
                # Format: "release_funds_ES1016256NPX" (alphanumeric escrow_id)
                # Query by escrow_id field to get numeric id
                escrow_lookup_stmt = select(Escrow).where(Escrow.escrow_id == id_part)
                escrow_lookup_result = await session.execute(escrow_lookup_stmt)
                escrow_lookup = escrow_lookup_result.scalar_one_or_none()
                
                if not escrow_lookup:
                    await query.edit_message_text("❌ Trade not found.")  # type: ignore
                    return CONV_END
                
                escrow_id = escrow_lookup.id
                logger.info(f"ID_MAPPING: Found escrow by escrow_id {id_part} -> numeric ID {escrow_id}")
            
            # Get the escrow
            escrow_stmt = select(Escrow).where(Escrow.id == escrow_id)
            escrow_result = await session.execute(escrow_stmt)
            escrow = escrow_result.scalar_one_or_none()
            
            if not escrow:
                await query.edit_message_text("❌ Trade not found.")  # type: ignore
                return CONV_END

            # Check user permissions (only buyer can release funds)
            user_stmt = select(User).where(User.telegram_id == update.effective_user.id)
            user_result = await session.execute(user_stmt)
            user = user_result.scalar_one_or_none()
            
            if not user or getattr(escrow, 'buyer_id', None) != user.id:
                await query.edit_message_text("❌ Only the buyer can release funds.")  # type: ignore
                return CONV_END

            # Check if trade is in active status (or marked as delivered but not yet released)
            current_status = getattr(escrow, 'status', '')
            if current_status not in ['active']:
                # If already completed, show appropriate message
                if current_status == 'completed':
                    await query.edit_message_text("ℹ️ Funds have already been released for this trade.")  # type: ignore
                else:
                    await query.edit_message_text(f"❌ Cannot release funds for trade with status: {current_status}")  # type: ignore
                return CONV_END

            # FEATURE: Add double confirmation for releasing funds (similar to trade cancellation)
            # Show confirmation dialog with trade details
            trade_amt = Decimal(str(getattr(escrow, 'amount', 0) or 0))
            
            # Get total platform fee (not just seller's portion)
            total_platform_fee_raw = getattr(escrow, 'fee_amount', None)
            total_platform_fee = Decimal(str(total_platform_fee_raw)) if total_platform_fee_raw is not None else Decimal("0.0")
            
            # Get seller's fee amount to calculate net received
            seller_fee_amt_raw = getattr(escrow, 'seller_fee_amount', None)
            seller_fee_amt = Decimal(str(seller_fee_amt_raw)) if seller_fee_amt_raw is not None else Decimal("0.0")
            seller_received = trade_amt - seller_fee_amt
            
            seller_stmt = select(User).where(User.id == escrow.seller_id) if escrow.seller_id else None  # type: ignore
            seller = None
            if seller_stmt is not None:
                seller_result = await session.execute(seller_stmt)
                seller = seller_result.scalar_one_or_none()
            seller_name = seller.username or seller.first_name or "Seller" if seller else "Seller"  # type: ignore
            
            confirmation_text = (
                f"⚠️ <b>Confirm Fund Release</b>\n\n"
                f"Are you sure you want to release funds to <b>{seller_name}</b>?\n\n"
                f"📋 <b>Trade ID:</b> #{getattr(escrow, 'utid', None) or escrow.escrow_id}\n"  # type: ignore
                f"💰 <b>Amount:</b> ${trade_amt:.2f}\n"
                f"💵 <b>Seller Receives:</b> ${seller_received:.2f}\n"
                f"💳 <b>Platform Fee:</b> ${total_platform_fee:.2f}\n\n"
                f"⚠️ <b>This action cannot be undone!</b>"
            )
            
            keyboard = [
                [
                    InlineKeyboardButton("✅ Yes, Release Funds", callback_data=f"confirm_release_{escrow_id}"),
                    InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_release_{escrow_id}")
                ]
            ]
            
            await query.edit_message_text(  # type: ignore
                confirmation_text,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            
            return CONV_END

        except Exception as e:
            logger.error(f"Error in release_funds handler: {e}")
            await query.edit_message_text("❌ Error processing fund release. Please try again.")  # type: ignore
            return CONV_END


async def handle_cancel_release_funds(update: TelegramUpdate, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle cancellation of fund release confirmation - shows feedback before returning to trade view"""
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query)

    if not update.effective_user:
        return CONV_END

    # Extract escrow ID from callback data (format: "cancel_release_123")
    callback_data = query.data if query else ""
    if not callback_data.startswith('cancel_release_'):  # type: ignore
        await query.edit_message_text("❌ Invalid cancellation.")  # type: ignore
        return CONV_END

    try:
        escrow_id = callback_data.split('_')[2]  # type: ignore
        escrow_id = int(escrow_id)
        logger.info(f"✅ cancel_release parsing successful: callback='{callback_data}', escrow_id={escrow_id}")
    except (IndexError, ValueError) as e:
        logger.error(f"❌ cancel_release parsing failed: callback='{callback_data}', error={e}")
        await query.edit_message_text("❌ Invalid cancellation request.")  # type: ignore
        return CONV_END

    try:
        # Show toast notification for immediate feedback
        await safe_answer_callback_query(query, "✅ Fund release cancelled", show_alert=False)  # type: ignore
        
        async with async_managed_session() as session:
            # Get escrow details for confirmation message
            stmt = select(Escrow).where(Escrow.id == escrow_id)  # type: ignore
            result = await session.execute(stmt)
            escrow = result.scalar_one_or_none()

            if not escrow:
                await query.edit_message_text("❌ Trade not found.")  # type: ignore
                return CONV_END

            # Show simple confirmation and return to trade view button
            trade_id = getattr(escrow, 'utid', None) or escrow.escrow_id  # type: ignore
            
            cancellation_text = (
                f"✅ Fund release cancelled\n\n"
                f"You did not release funds for trade #{trade_id}.\n"
                f"The trade remains active."
            )
            
            keyboard = [
                [InlineKeyboardButton("⬅️ Back to Trade", callback_data=f"view_trade_{escrow_id}")]
            ]
            
            await query.edit_message_text(  # type: ignore
                cancellation_text,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            
            return CONV_END

    except Exception as e:
        logger.error(f"Error in cancel_release handler: {e}")
        await query.edit_message_text("❌ Error processing cancellation. Please try again.")  # type: ignore
        return CONV_END


async def handle_confirm_release_funds(update: TelegramUpdate, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle confirmed release_funds callback after double confirmation"""
    # Import required services at function level
    from services.crypto import CryptoServiceAtomic
    from models import EscrowHolding, Transaction
    from services.escrow_fund_manager import EscrowFundManager
    
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query)

    if not update.effective_user:
        return CONV_END

    # Extract escrow ID from callback data (format: "confirm_release_123")
    callback_data = query.data if query else ""
    if not callback_data.startswith('confirm_release_'):  # type: ignore
        await query.edit_message_text("❌ Invalid fund release confirmation.")  # type: ignore
        return CONV_END

    try:
        escrow_id = callback_data.split('_')[2]  # type: ignore
        escrow_id = int(escrow_id)
        logger.info(f"✅ confirm_release parsing successful: callback='{callback_data}', escrow_id={escrow_id}")
    except (IndexError, ValueError) as e:
        logger.error(f"❌ confirm_release parsing failed: callback='{callback_data}', error={e}")
        await query.edit_message_text("❌ Invalid trade ID format.")  # type: ignore
        return CONV_END

    # INSTANT FEEDBACK: Show processing message immediately for critical financial operation
    from utils.callback_utils import safe_edit_message_text
    from telegram import InlineKeyboardMarkup
    
    processing_message = (
        f"⏳ *Processing Fund Release...*\n\n"
        f"Please wait while we:\n"
        f"• Transfer funds to seller\n"
        f"• Complete the trade\n"
        f"• Update records\n\n"
        f"This may take a few seconds..."
    )
    
    await safe_edit_message_text(
        query,
        processing_message,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([])  # Remove buttons during processing
    )
    
    logger.info(f"✅ Buyer UI immediately updated with processing message for fund release")

    async with async_managed_session() as session:
        try:
            # Get the escrow
            escrow_stmt = select(Escrow).where(Escrow.id == escrow_id)
            escrow_result = await session.execute(escrow_stmt)
            escrow = escrow_result.scalar_one_or_none()
            
            if not escrow:
                await query.edit_message_text("❌ Trade not found.")  # type: ignore
                return CONV_END

            # Check user permissions (only buyer can release funds)
            user_stmt = select(User).where(User.telegram_id == update.effective_user.id)
            user_result = await session.execute(user_stmt)
            user = user_result.scalar_one_or_none()
            
            if not user or getattr(escrow, 'buyer_id', None) != user.id:
                await query.edit_message_text("❌ Only the buyer can release funds.")  # type: ignore
                return CONV_END

            # Check if trade is in active status
            if getattr(escrow, 'status', '') != 'active':
                if getattr(escrow, 'status', '') == 'completed':
                    await query.edit_message_text(  # type: ignore
                        "ℹ️ Already Released\n\n"
                        "This trade has already been completed and funds released."
                    )
                else:
                    await query.edit_message_text("❌ Only active trades can have funds released.")  # type: ignore
                return CONV_END

            # CRITICAL: Check if funds were already released (prevent duplicate releases)
            # Check for existing release transaction
            existing_release_stmt = select(Transaction).where(
                Transaction.escrow_id == escrow.id,
                Transaction.transaction_type == "release",
                Transaction.status == "completed"
            )
            existing_release_result = await session.execute(existing_release_stmt)
            existing_release = existing_release_result.scalar_one_or_none()
            
            if existing_release:
                # Funds were already released but status wasn't updated - fix it now
                logger.warning(f"⚠️ Funds already released for {escrow.escrow_id} - fixing status")
                try:
                    EscrowStateValidator.validate_and_transition(
                        escrow,
                        EscrowStatus.COMPLETED,
                        escrow.escrow_id,  # type: ignore
                        force=False
                    )
                except StateTransitionError as e:
                    logger.warning(f"⚠️ FORCED_COMPLETION: {e} - completing anyway for fund release consistency")
                    escrow.status = EscrowStatus.COMPLETED.value  # type: ignore
                escrow.completed_at = escrow.completed_at or datetime.now(timezone.utc)  # type: ignore
                escrow.released_at = escrow.released_at or datetime.now(timezone.utc)  # type: ignore
                await session.commit()
                
                # Invalidate cached escrow data (trade completed, balance changed)
                invalidate_all_escrow_caches(context)
            
                await query.edit_message_text(  # type: ignore
                    "ℹ️ Already Released\n\n"
                    f"Funds for trade #{escrow.escrow_id} were already released to the seller.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("📋 My Trades", callback_data="trades_messages_hub")],
                        [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
                    ])
                )
                return CONV_END
            
            # Release held escrow funds to seller
            if getattr(escrow, 'seller_id', None):
                try:
                    # Get the escrow holding record
                    holding_stmt = select(EscrowHolding).where(
                        EscrowHolding.escrow_id == escrow.escrow_id
                    ).with_for_update()
                    holding_result = await session.execute(holding_stmt)
                    holding = holding_result.scalar_one_or_none()
                    
                    if not holding or holding.status != "active":  # type: ignore
                        logger.error(f"❌ No active escrow holding found for {escrow.escrow_id}")
                        await query.edit_message_text("❌ Error releasing funds. Please contact support.")  # type: ignore
                        return CONV_END
                    
                    # Calculate release amount (escrow amount minus seller fees)
                    escrow_amount = Decimal(str(escrow.amount))  # type: ignore
                    seller_fee = Decimal(str(escrow.seller_fee_amount)) if escrow.seller_fee_amount else Decimal("0.0")
                    release_amount = escrow_amount - seller_fee  # Deduct seller's fee from release
                    
                    logger.info(f"💰 Escrow {escrow.escrow_id}: amount=${escrow_amount}, seller_fee=${seller_fee}, release_amount=${release_amount}")
                    
                    # Credit seller's wallet (after deducting their fee)
                    seller_success = await CryptoServiceAtomic.credit_user_wallet_atomic(
                        user_id=escrow.seller_id,  # type: ignore
                        amount=release_amount,  # FIX: Credit release_amount, not full escrow_amount
                        currency="USD",
                        escrow_id=escrow.id,  # type: ignore
                        transaction_type="escrow_release",
                        description=f"✅ Released • Escrow #{escrow.escrow_id} (Fee: ${seller_fee})",
                        session=session
                    )
                    
                    if not seller_success:
                        logger.error(f"❌ Failed to credit seller wallet for {escrow.escrow_id}")
                        await query.edit_message_text("❌ Error releasing funds. Please contact support.")  # type: ignore
                        return CONV_END
                    
                    # Mark holding as released
                    holding.status = "released"  # type: ignore
                    holding.released_at = datetime.now(timezone.utc)  # type: ignore
                    
                    # CRITICAL FIX: Extract all data BEFORE commit to avoid lazy loading errors
                    escrow_id_str = escrow.escrow_id
                    buyer_id = escrow.buyer_id
                    seller_id = escrow.seller_id
                    escrow_amount_dec = Decimal(str(escrow.amount))
                    escrow_currency = escrow.currency
                    escrow_seller_fee = Decimal(str(escrow.seller_fee_amount)) if escrow.seller_fee_amount else Decimal("0.0")  # type: ignore[truthy-bool]
                    
                    release_success = True
                    if release_success:
                        # Update escrow status in same transaction
                        escrow.status = EscrowStatus.COMPLETED.value  # type: ignore
                        escrow.completed_at = datetime.now(timezone.utc)  # type: ignore
                        escrow.released_at = datetime.now(timezone.utc)  # type: ignore
                        await session.commit()
                        
                        # Invalidate cached escrow data (trade completed, balance changed)
                        invalidate_all_escrow_caches(context)
                        
                        logger.info(f"✅ Escrow {escrow_id_str} atomically completed with funds released to seller {seller_id}")
                        
                        # Send Telegram group notification for funds released
                        try:
                            # Get buyer and seller names
                            buyer_name = "Unknown"
                            seller_name = "Unknown"
                            if buyer_id:
                                buyer_result = await session.execute(select(User).where(User.id == buyer_id))
                                buyer_user = buyer_result.scalar_one_or_none()
                                if buyer_user:
                                    buyer_name = f"@{buyer_user.username}" if buyer_user.username else buyer_user.first_name
                            if seller_id:
                                seller_result = await session.execute(select(User).where(User.id == seller_id))
                                seller_user = seller_result.scalar_one_or_none()
                                if seller_user:
                                    seller_name = f"@{seller_user.username}" if seller_user.username else seller_user.first_name
                            
                            total_platform_fee = Decimal(str(escrow.fee_amount)) if escrow.fee_amount else Decimal("0.0")
                            seller_receives = escrow_amount_dec - escrow_seller_fee
                            
                            release_data = {
                                'escrow_id': escrow_id_str,
                                'seller_info': seller_name,
                                'buyer_info': buyer_name,
                                'amount': float(escrow_amount_dec),
                                'platform_fee': float(total_platform_fee),
                                'seller_receives': float(seller_receives),
                                'released_at': datetime.now(timezone.utc)
                            }
                            admin_notif_service = AdminTradeNotificationService()
                            asyncio.create_task(admin_notif_service.send_group_notification_funds_released(release_data))
                            # Broadcast to all registered groups
                            from services.group_event_service import group_event_service
                            asyncio.create_task(group_event_service.broadcast_escrow_completed(release_data))
                            logger.info(f"📤 Queued group notification for funds released: {escrow_id_str}")
                        except Exception as notif_err:
                            logger.error(f"❌ Failed to queue funds released group notification: {notif_err}")
                    
                        # Update user stats for both buyer and seller
                        try:
                            from services.user_stats_service import UserStatsService
                            await UserStatsService.update_both_user_stats(buyer_id, seller_id, session)  # type: ignore
                            logger.info(f"✅ Updated stats for buyer {buyer_id} and seller {seller_id}")
                        except Exception as e:
                            logger.error(f"❌ Failed to update user stats after trade completion: {e}")
                    
                        # ===== PHASE 3B: MILESTONE TRACKING & RECEIPT GENERATION =====
                        try:
                            from services.milestone_tracking_service import MilestoneTrackingService
                            from services.receipt_generation_service import ReceiptGenerationService
                        
                            # Check milestones for both buyer and seller
                            trigger_context = {
                                "event_type": "escrow_completed",
                                "escrow_id": escrow_id_str,
                                "amount": escrow_amount_dec,
                                "currency": escrow_currency
                            }
                        
                            # Check buyer milestones
                            buyer_achievements = MilestoneTrackingService.check_user_milestones(
                                buyer_id, trigger_context  # type: ignore
                            )
                            if buyer_achievements:
                                logger.info(f"🎉 Buyer {buyer_id} achieved {len(buyer_achievements)} new milestones")
                        
                            # Check seller milestones  
                            seller_achievements = MilestoneTrackingService.check_user_milestones(
                                seller_id, trigger_context  # type: ignore
                            )
                            if seller_achievements:
                                logger.info(f"🎉 Seller {seller_id} achieved {len(seller_achievements)} new milestones")
                        
                            # Generate branded receipts for both parties
                            buyer_receipt = ReceiptGenerationService.generate_escrow_completion_receipt(
                                escrow_id_str, buyer_id  # type: ignore
                            )
                            seller_receipt = ReceiptGenerationService.generate_escrow_completion_receipt(
                                escrow_id_str, seller_id  # type: ignore
                            )
                        
                            # Store achievement and receipt data for later notification
                            context.user_data['buyer_achievements'] = buyer_achievements  # type: ignore
                            context.user_data['seller_achievements'] = seller_achievements  # type: ignore
                            context.user_data['buyer_receipt'] = buyer_receipt  # type: ignore
                            context.user_data['seller_receipt'] = seller_receipt  # type: ignore
                        
                            logger.info(f"✅ Phase 3B integration complete for escrow {escrow_id_str}")
                        
                        except Exception as e:
                            logger.error(f"❌ Phase 3B milestone/receipt generation failed for {escrow_id_str}: {e}")
                            # Don't fail the transaction if milestone/receipt generation fails
                        # ===== END PHASE 3B INTEGRATION =====
                    else:
                        # Check if it was a duplicate attempt
                        holding_stmt = select(EscrowHolding).where(
                            EscrowHolding.escrow_id == escrow_id_str
                        )
                        holding_result = await session.execute(holding_stmt)
                        holding = holding_result.scalar_one_or_none()
                    
                        if holding and holding.status == "released":  # type: ignore
                            # Was already released, just update escrow status
                            escrow.status = EscrowStatus.COMPLETED.value  # type: ignore
                            escrow.completed_at = escrow.completed_at or datetime.now(timezone.utc)  # type: ignore
                            escrow.released_at = escrow.released_at or datetime.now(timezone.utc)  # type: ignore
                            await session.commit()
                        
                            # Update user stats for both buyer and seller (for previously completed trade)
                            try:
                                from services.user_stats_service import UserStatsService
                                await UserStatsService.update_both_user_stats(buyer_id, seller_id, session)  # type: ignore
                                logger.info(f"✅ Updated stats for buyer {buyer_id} and seller {seller_id} (delayed update)")
                            except Exception as e:
                                logger.error(f"❌ Failed to update user stats for previously completed trade: {e}")
                        
                            await query.edit_message_text(  # type: ignore
                                "ℹ️ Already Released\n\n"
                                "Funds were already released for this trade.",
                                parse_mode="Markdown"
                            )
                        else:
                            logger.error(f"❌ Failed to release escrow funds for {escrow_id_str}")
                            await query.edit_message_text("❌ Error releasing funds. Please contact support.")  # type: ignore
                    
                        return CONV_END
                    
                except Exception as release_error:
                    logger.error(f"❌ Critical error releasing funds for escrow {escrow_id_str}: {release_error}")  # type: ignore[possibly-undefined]
                    await query.edit_message_text("❌ Error releasing funds. Please contact support.")  # type: ignore
                    return CONV_END
            else:
                # Fallback: If no seller_id, still mark as completed but log warning
                # Extract data before commit (fallback case)
                escrow_id_str_fb = escrow.escrow_id
                buyer_id_fb = escrow.buyer_id
                seller_id_fb = escrow.seller_id
                
                logger.warning(f"No seller ID found for escrow {escrow_id_str_fb} - marking completed without fund release")
                escrow.status = EscrowStatus.COMPLETED.value  # type: ignore
                escrow.completed_at = datetime.now(timezone.utc)  # type: ignore
        
                await session.commit()
            
                # Update user stats for both buyer and seller (fallback case)
                try:
                    from services.user_stats_service import UserStatsService
                    if buyer_id_fb and seller_id_fb:  # type: ignore
                        await UserStatsService.update_both_user_stats(buyer_id_fb, seller_id_fb, session)  # type: ignore
                        logger.info(f"✅ Updated stats for buyer {buyer_id_fb} and seller {seller_id_fb} (fallback)")
                except Exception as e:
                    logger.error(f"❌ Failed to update user stats in fallback case: {e}")

            # ===== COMMON PATH: NOTIFICATIONS (runs for BOTH success and fallback) =====
            # Select correct variables based on which path was taken
            if 'escrow_id_str' in locals():
                # Main success path variables
                final_escrow_id = escrow_id_str  # type: ignore[possibly-undefined]
                final_buyer_id = buyer_id  # type: ignore[possibly-undefined]
                final_seller_id = seller_id  # type: ignore[possibly-undefined]
                final_amount = escrow_amount_dec  # type: ignore[possibly-undefined]
                final_currency = escrow_currency  # type: ignore[possibly-undefined]
                final_seller_fee = escrow_seller_fee  # type: ignore[possibly-undefined]
            else:
                # Fallback path variables
                final_escrow_id = escrow_id_str_fb  # type: ignore[possibly-undefined]
                final_buyer_id = buyer_id_fb  # type: ignore[possibly-undefined]
                final_seller_id = seller_id_fb  # type: ignore[possibly-undefined]
                final_amount = Decimal(str(escrow.amount))  # type: ignore
                final_currency = escrow.currency or 'USD'  # type: ignore
                final_seller_fee = Decimal(str(escrow.seller_fee_amount)) if escrow.seller_fee_amount else Decimal("0.0")  # type: ignore
            
            # Send notifications
            from services.consolidated_notification_service import consolidated_notification_service as NotificationService
            if final_seller_id:  # type: ignore
                await NotificationService.send_funds_released_notification(
                    seller_id=final_seller_id,  # type: ignore
                    escrow_id=final_escrow_id,  # type: ignore
                    amount=final_amount,  # type: ignore
                    escrow_numeric_id=escrow.id if escrow else None  # type: ignore
                )
            
            # Send comprehensive post-completion notifications to both parties
            try:
                from services.post_completion_notification_service import notify_escrow_completion
                
                # Get buyer and seller email addresses
                buyer_email = user.email if user and user.is_verified else None  # type: ignore
                seller_stmt = select(User).where(User.id == final_seller_id) if final_seller_id else None  # type: ignore
                seller = None
                if seller_stmt is not None:
                    seller_result = await session.execute(seller_stmt)
                    seller = seller_result.scalar_one_or_none()
                seller_email = seller.email if seller and seller.is_verified else None  # type: ignore
                
                notification_results = await notify_escrow_completion(
                    escrow_id=final_escrow_id,  # type: ignore
                    completion_type='released',
                    amount=final_amount,  # type: ignore
                    buyer_id=final_buyer_id,  # type: ignore
                    seller_id=final_seller_id,  # type: ignore
                    buyer_email=buyer_email,  # type: ignore
                    seller_email=seller_email  # type: ignore
                )
                
                logger.info(f"✅ Post-completion notifications sent for {final_escrow_id}: {notification_results}")
            except Exception as e:
                logger.error(f"❌ Failed to send post-completion notifications for {final_escrow_id}: {e}")
            
            # Send admin notification about escrow completion
            try:
                from services.admin_trade_notifications import AdminTradeNotificationService
                admin_service = AdminTradeNotificationService()
                
                await admin_service.notify_escrow_completed({
                    'escrow_id': final_escrow_id,
                    'amount': final_amount,  # type: ignore
                    'buyer_info': user.first_name or user.username or f"User {user.id}",  # type: ignore
                    'seller_info': escrow.seller_contact_display or (seller.first_name if seller else None) or (seller.username if seller else None) or f"User {seller.id}" if seller else "Unknown",  # type: ignore
                    'currency': final_currency,  # type: ignore
                    'resolution_type': 'released',
                    'completed_at': datetime.now(timezone.utc)
                })
                
                logger.info(f"✅ Admin notified of escrow completion: {final_escrow_id}")
            except Exception as e:
                logger.error(f"❌ Failed to send admin completion notification for {final_escrow_id}: {e}")
            
            # Show completion message with fee details (using extracted variables)
            try:
                trade_amt = final_amount
                seller_fee_amt = final_seller_fee
                seller_received = trade_amt - seller_fee_amt
            
                fee_info = ""
                if seller_fee_amt > 0:
                    fee_info = f"💳 Seller Fee: ${seller_fee_amt:.2f} USD\n"
            
                await query.edit_message_text(  # type: ignore
                    f"✅ Funds Released Successfully!\n\n"
                    f"💰 Trade Amount: ${trade_amt:.2f} USD\n"
                    f"{fee_info}"
                    f"💵 Seller Received: ${seller_received:.2f} USD\n"
                    f"📋 Trade: #{final_escrow_id}\n\n"
                    f"The trade is now complete. The seller has received the funds.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("📋 My Trades", callback_data="trades_messages_hub")],
                        [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
                    ])
                )

            except Exception as e:
                logger.error(f"Error in release_funds handler: {e}")
                await query.edit_message_text("❌ Error processing fund release. Please try again.")  # type: ignore

        except Exception as e:
            logger.error(f"Critical error in confirm_release_funds: {e}")
            if query:
                await query.edit_message_text("❌ Error processing fund release. Please contact support.")  # type: ignore
    
    return CONV_END



# ===== TRADE HELPERS FUNCTIONS (Consolidated from trade_helpers.py) =====

async def handle_trade_pagination(update: TelegramUpdate, context: ContextTypes.DEFAULT_TYPE) -> int:  # type: ignore
    """Handle trade list pagination"""
    query = update.callback_query
    if not query:
        return  # type: ignore

    # SINGLE CALLBACK ANSWER: Pagination
    await safe_answer_callback_query(query, "📄")

    try:
        # Extract page number
        if query.data and ":" in query.data:
            page = int(query.data.split(":")[1])
        else:
            page = 1

        # Store page in context safely
        if context.user_data is not None:
            context.user_data["trade_page"] = page

        # Redirect to unified trades & messages interface
        from handlers.messages_hub import show_trades_messages_hub
        await show_trades_messages_hub(update, context)

    except Exception as e:
        logger.error(f"Error in trade pagination: {e}")
        await safe_edit_message_text(query, "❌ Error loading page. Please try again.")


async def handle_trade_filter(update: TelegramUpdate, context: ContextTypes.DEFAULT_TYPE) -> int:  # type: ignore
    """Handle trade list filtering options"""
    query = update.callback_query
    if not query:
        return  # type: ignore

    await safe_answer_callback_query(query, "🔍")

    filter_text = """🔍 Filter My Trades

Choose how to filter your trades:"""

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📋 All Trades", callback_data="filter_apply:all"),
                InlineKeyboardButton(
                    "🟢 Active Only", callback_data="filter_apply:active"
                ),
            ],
            [
                InlineKeyboardButton(
                    "✅ Completed", callback_data="filter_apply:completed"
                ),
                InlineKeyboardButton(
                    "🔴 Cancelled", callback_data="filter_apply:cancelled"
                ),
            ],
            [
                InlineKeyboardButton("👤 As Buyer", callback_data="filter_apply:buyer"),
                InlineKeyboardButton(
                    "👥 As Seller", callback_data="filter_apply:seller"
                ),
            ],
            [InlineKeyboardButton("⬅️ Back to Trades", callback_data="menu_escrows")],
        ]
    )

    await safe_edit_message_text(query, filter_text, reply_markup=keyboard)


async def handle_filter_apply(update: TelegramUpdate, context: ContextTypes.DEFAULT_TYPE) -> int:  # type: ignore
    """Apply trade filter and redirect to trade list"""
    query = update.callback_query
    if not query:
        return  # type: ignore

    await safe_answer_callback_query(query, "✅")

    try:
        if query.data and ":" in query.data:
            filter_type = query.data.split(":")[1]
        else:
            filter_type = "all"

        # Store filter in context (for future enhancement)
        if context.user_data is not None:
            context.user_data["trade_filter"] = filter_type
            context.user_data["trade_page"] = 1  # Reset to page 1

        # Redirect back to unified trades & messages interface
        from handlers.messages_hub import show_trades_messages_hub
        await show_trades_messages_hub(update, context)

    except Exception as e:
        logger.error(f"Error applying trade filter: {e}")
        await safe_edit_message_text(query, "❌ Error applying filter. Please try again.")


async def handle_buyer_cancel_trade(update: TelegramUpdate, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle buyer cancelling trade when seller hasn't accepted (payment_confirmed status)"""
    query = update.callback_query
    if not query:
        return ConversationHandler.END
    
    # Set context flag for cancel action to provide better feedback if user clicks "No, Keep Trade"
    if context.user_data is not None:
        context.user_data['last_action'] = 'cancel_confirmation'
    
    await safe_answer_callback_query(query, "❌ Cancelling trade...")
    
    # Extract escrow ID from callback data (format: "buyer_cancel_ES123ABC")
    callback_data = query.data if query else ""
    if not callback_data.startswith('buyer_cancel_'):  # type: ignore
        await query.edit_message_text("❌ Invalid cancel request.")
        return ConversationHandler.END

    try:
        escrow_string_id = callback_data.split('_', 2)[2]  # buyer_cancel_ES123ABC -> ES123ABC  # type: ignore
    except (IndexError, ValueError):
        await query.edit_message_text("❌ Invalid trade ID format.")
        return ConversationHandler.END

    async with async_managed_session() as session:
        try:
            # Get the escrow by escrow_id string
            escrow_stmt = select(Escrow).where(Escrow.escrow_id == escrow_string_id)
            escrow_result = await session.execute(escrow_stmt)
            escrow = escrow_result.scalar_one_or_none()
            
            if not escrow:
                await query.edit_message_text("❌ Trade not found.")
                return ConversationHandler.END

            # Verify user is the buyer
            user_stmt = select(User).where(User.telegram_id == update.effective_user.id)  # type: ignore
            user_result = await session.execute(user_stmt)
            user = user_result.scalar_one_or_none()
            
            if not user or getattr(escrow, 'buyer_id', None) != user.id:
                await query.edit_message_text("❌ Only the buyer can cancel this trade.")
                return ConversationHandler.END

            # Verify trade is in correct status for buyer cancellation
            # Buyers can cancel BEFORE payment (payment_pending) OR AFTER payment but BEFORE seller accepts (payment_confirmed)
            if escrow.status not in ["payment_pending", "payment_confirmed"]:
                await query.edit_message_text(
                    f"❌ Cannot cancel trade in {escrow.status} status.\n"
                    f"You can only cancel before payment or after payment but before seller accepts."
                )
                return ConversationHandler.END

        # Check if payment has actually been made
            is_payment_made = escrow.status == "payment_confirmed"
            amount = Decimal(str(getattr(escrow, 'amount', 0) or 0))
            
            if not is_payment_made:
                # PAYMENT PENDING - No payment made, simple cancellation without refund
                confirmation_text = f"""❌ Cancel Trade #{escrow.escrow_id}

No payment has been made yet.
This will simply cancel the trade.

Are you sure you want to cancel?"""
            else:
                # PAYMENT CONFIRMED - Payment made, show refund information
                fee_amount_raw = getattr(escrow, 'fee_amount', None)
                fee_amount = Decimal(str(fee_amount_raw)) if fee_amount_raw is not None else Decimal("0.0")
                fee_split_option = getattr(escrow, 'fee_split_option', 'buyer_pays')
                
                # Calculate refund amount with precise fee split logic
                from utils.fee_calculator import FeeCalculator
                
                # Convert to Decimal for precise calculations
                amount_decimal = Decimal(str(amount))
                total_fee_decimal = Decimal(str(fee_amount))
                
                # Calculate fee split to determine what buyer paid
                buyer_fee_decimal, seller_fee_decimal = FeeCalculator._calculate_fee_split(
                    total_fee_decimal, fee_split_option
                )
                
                # Calculate refund based on what buyer actually paid
                if fee_split_option == "seller_pays":
                    # Buyer didn't pay any fee, refund only trade amount
                    total_refund_decimal = amount_decimal
                    refund_text = f"{format_money(total_refund_decimal, 'USD')} will be refunded"
                elif fee_split_option == "buyer_pays":
                    # Buyer paid the full fee, refund trade amount + full fee
                    total_refund_decimal = safe_add(amount_decimal, total_fee_decimal)
                    refund_text = f"{format_money(total_refund_decimal, 'USD')} will be refunded ({format_money(amount_decimal, 'USD')} + {format_money(total_fee_decimal, 'USD')} fee)"
                elif fee_split_option == "split":
                    # Buyer paid their portion, refund trade amount + buyer's fee portion
                    total_refund_decimal = safe_add(amount_decimal, buyer_fee_decimal)
                    refund_text = f"{format_money(total_refund_decimal, 'USD')} will be refunded ({format_money(amount_decimal, 'USD')} + {format_money(buyer_fee_decimal, 'USD')} fee)"
                else:  # default case for legacy escrows
                    # Use 5% default fee rate for consistency
                    default_fee_decimal = calculate_percentage(amount_decimal, Decimal('5'))
                    total_refund_decimal = safe_add(amount_decimal, default_fee_decimal)
                    refund_text = f"{format_money(total_refund_decimal, 'USD')} will be refunded ({format_money(amount_decimal, 'USD')} + {format_money(default_fee_decimal, 'USD')} fee)"
                
                confirmation_text = f"""❌ Cancel Trade #{escrow.escrow_id}

{refund_text} to your wallet.

Are you sure you want to cancel?"""

            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Yes, Cancel Trade", callback_data=f"confirm_buyer_cancel_{escrow.escrow_id}"),
                    InlineKeyboardButton("❌ No, Keep Trade", callback_data=f"keep_trade_{escrow.id}")
                ]
            ])

            await query.edit_message_text(
                confirmation_text,
                reply_markup=keyboard
            )

        except Exception as e:
            logger.error(f"Error in handle_buyer_cancel_trade: {e}")
            await query.edit_message_text("❌ Error processing cancellation. Please try again.")

        return ConversationHandler.END


async def handle_seller_accept_trade(update: TelegramUpdate, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle seller accepting a trade from the view trade interface"""
    query = update.callback_query
    if not query:
        return ConversationHandler.END
    
    await safe_answer_callback_query(query, "✅ Accepting trade...")
    
    # Extract escrow ID from callback data (format: "accept_trade:ES123ABC")
    callback_data = query.data if query else ""
    if not callback_data.startswith('accept_trade:'):  # type: ignore
        await query.edit_message_text("❌ Invalid accept request.")
        return ConversationHandler.END

    try:
        escrow_string_id = callback_data.split(':', 1)[1]  # accept_trade:ES123ABC -> ES123ABC  # type: ignore
    except (IndexError, ValueError):
        await query.edit_message_text("❌ Invalid trade ID format.")
        return ConversationHandler.END

    # INSTANT FEEDBACK: Show processing message immediately to seller
    from utils.callback_utils import safe_edit_message_text
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    
    processing_message = (
        f"⏳ *Processing Trade Acceptance...*\n\n"
        f"Trade: #{escrow_string_id}\n\n"
        f"Please wait while we activate your trade..."
    )
    
    await safe_edit_message_text(
        query,
        processing_message,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([])  # Remove buttons during processing
    )
    
    logger.info(f"✅ Seller UI immediately updated with processing message for trade {escrow_string_id}")

    # Use atomic transaction for acceptance processing
    from utils.atomic_transactions import atomic_transaction
    
    try:
        with atomic_transaction() as session:
            # Get the escrow
            escrow = session.query(Escrow).filter(Escrow.escrow_id == escrow_string_id).first()
            if not escrow:
                await query.edit_message_text("❌ Trade not found.")
                return ConversationHandler.END

            # Verify user is the seller
            stmt = select(User).where(User.telegram_id == update.effective_user.id)  # type: ignore
            result = session.execute(stmt)
            user = result.scalar_one_or_none()
            if not user:
                await query.edit_message_text("❌ User not found.")
                return ConversationHandler.END
                
            # Check if user is authorized seller
            is_seller = (
                (getattr(escrow, 'seller_id', None) == user.id) or
                (getattr(escrow, 'seller_contact_type', None) == 'username' and getattr(escrow, 'seller_contact_value', None) == user.username and not escrow.seller_id) or  # type: ignore
                (getattr(escrow, 'seller_contact_type', None) == 'email' and getattr(escrow, 'seller_contact_value', None) == user.email and not escrow.seller_id)  # type: ignore
            )
            
            if not is_seller:
                await query.edit_message_text("❌ Unauthorized acceptance.")
                return ConversationHandler.END

            # Double-check status - can only accept payment_confirmed trades
            if escrow.status != "payment_confirmed":  # type: ignore
                await query.edit_message_text("❌ Trade status changed. Cannot accept.")
                return ConversationHandler.END

            # SECURITY FIX: Validate state transition before acceptance to prevent DISPUTED→ACTIVE
            from utils.escrow_state_validator import EscrowStateValidator
            
            validator = EscrowStateValidator()
            current_status = str(escrow.status)  # Explicit cast to str for type safety
            if not validator.is_valid_transition(current_status, EscrowStatus.ACTIVE.value):
                logger.error(
                    f"🚫 SELLER_ACCEPT_BLOCKED: Invalid transition {current_status}→ACTIVE for trade {escrow_string_id}"
                )
                await query.edit_message_text(
                    f"❌ Trade cannot be accepted at this time.\n\n"
                    f"Current status: {current_status}\n\n"
                    f"Please contact support if you believe this is an error."
                )
                return ConversationHandler.END

            # Accept the trade
            escrow.seller_id = user.id  # type: ignore[assignment]  # Direct assignment - SQLAlchemy Column
            escrow.status = EscrowStatus.ACTIVE.value  # Use .value for database compatibility  # type: ignore
            escrow.seller_accepted_at = datetime.now(timezone.utc)  # type: ignore
            # Note: updated_at handled by SQLAlchemy onupdate
            
            # CRITICAL: Cache user IDs before session closes to avoid detached instance errors
            buyer_id = int(escrow.buyer_id)  # type: ignore[arg-type]
            seller_id = int(user.id)  # type: ignore[arg-type]
            escrow_id_str = str(escrow.escrow_id)
            escrow_amount = Decimal(str(escrow.amount))
            escrow_internal_id = int(escrow.id)  # type: ignore[arg-type]
            
            # Commit all changes
            session.commit()
            
            logger.info(f"✅ Trade {escrow_id_str} accepted by seller {seller_id} - database updated successfully")
        
        # SUCCESS MESSAGE (after session closes)
        from utils.callback_utils import safe_edit_message_text
        
        # Add timestamp to ensure message is unique and forces UI update
        acceptance_time = datetime.now(timezone.utc).strftime("%H:%M:%S")
        
        success_message = (
            f"🎉 Trade Accepted!\n\n"
            f"#{escrow_id_str} • ${escrow_amount:.2f}\n\n"
            f"✅ Trade is now active\n"
            f"💬 You can now chat with the buyer\n"
            f"📦 Please deliver as promised\n\n"
            f"The buyer has been notified.\n"
            f"_Accepted at {acceptance_time} UTC_"
        )
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("💬 Open Trade Chat", callback_data=f"trade_chat_open:{escrow_internal_id}")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
        ])
        
        edit_success = await safe_edit_message_text(
            query,
            success_message,
            parse_mode="Markdown",
            reply_markup=keyboard
        )
        
        if edit_success:
            logger.info(f"✅ Seller UI updated for trade {escrow_id_str} acceptance")
        else:
            logger.warning(f"⚠️ Failed to update seller UI for trade {escrow_id_str} - sending fallback message")
            # Send new message as fallback if edit fails
            try:
                from telegram import Message
                if query.message and isinstance(query.message, Message) and hasattr(query.message, 'reply_text'):
                    await query.message.reply_text(
                        success_message,
                        parse_mode="Markdown",
                        reply_markup=keyboard
                    )
                    logger.info(f"✅ Sent fallback message to seller for trade {escrow_id_str}")
                else:
                    logger.error(f"❌ Cannot send fallback - query.message not available for trade {escrow_id_str}")
            except Exception as fallback_error:
                logger.error(f"❌ Fallback message also failed for trade {escrow_id_str}: {fallback_error}")
        
        # SEND BUYER NOTIFICATION: Single notification with trade chat, details, and menu buttons
        try:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            
            buyer_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("💬 Open Trade Chat", callback_data=f"trade_chat_open:{escrow_internal_id}")],
                [InlineKeyboardButton("📦 View Trade Details", callback_data=f"view_trade_{escrow_internal_id}")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
            ])
            
            # Send a fresh message to buyer to force UI update with new buttons
            buyer_telegram_message = f"""🎉 *Trade Accepted!*

The seller has accepted your trade:
*#{escrow_id_str}* • *${escrow_amount:.2f}*

✅ Trade is now *active*
💬 You can now chat with the seller
📦 Waiting for delivery

🛡️ *You control release* • Only release after receiving item

_Accepted at {acceptance_time} UTC_"""
            
            await context.bot.send_message(
                chat_id=buyer_id,
                text=buyer_telegram_message,
                parse_mode="Markdown",
                reply_markup=buyer_keyboard
            )
            logger.info(f"✅ Buyer notification sent with 3 action buttons for trade {escrow_id_str}")
        except Exception as buyer_error:
            logger.error(f"❌ Failed to send buyer notification for trade {escrow_id_str}: {buyer_error}")
        
        # Send comprehensive trade acceptance notifications (email to BOTH buyer and seller)
        try:
            from services.trade_acceptance_notification_service import trade_acceptance_notifications
            
            notification_results = await trade_acceptance_notifications.notify_trade_acceptance(
                escrow_id=escrow_id_str,
                buyer_id=buyer_id,
                seller_id=seller_id,
                amount=escrow_amount,
                currency="USD"
            )
            
            success_count = sum(1 for success in notification_results.values() if success)
            total_count = len(notification_results)
            logger.info(f"✅ Trade acceptance notifications for {escrow_id_str}: {success_count}/{total_count} sent successfully")
            
            if success_count < total_count:
                failed_notifications = [k for k, v in notification_results.items() if not v]
                logger.warning(f"⚠️ Failed notifications for {escrow_id_str}: {', '.join(failed_notifications)}")
                
        except Exception as notification_error:
            logger.error(f"❌ Failed to send comprehensive trade acceptance notifications for {escrow_id_str}: {notification_error}")
        
        # SEND SELLER NOTIFICATION: Email-only audit trail (seller already saw confirmation in UI)
        try:
            from services.consolidated_notification_service import (
                ConsolidatedNotificationService,
                NotificationRequest,
                NotificationCategory,
                NotificationPriority,
                NotificationChannel
            )
            
            notification_service = ConsolidatedNotificationService()
            await notification_service.initialize()
            
            seller_email_request = NotificationRequest(
                user_id=seller_id,
                category=NotificationCategory.ESCROW_UPDATES,
                priority=NotificationPriority.NORMAL,
                title="✅ Trade Accepted - Email Confirmation",
                message=f"""Trade Accepted - Email Confirmation

#{escrow_id_str} • ${escrow_amount:.2f}

✅ You have accepted this trade
📧 Trade is now active
📦 Please deliver as promised

This is an email confirmation for your records.""",
                channels=[NotificationChannel.EMAIL],
                broadcast_mode=False,
                template_data={
                    "escrow_id": escrow_id_str,
                    "amount": decimal_to_string(escrow_amount, precision=2),
                    "status": "active",
                    "event_type": "seller_accept_confirmation"
                },
                idempotency_key=f"escrow_{escrow_id_str}_seller_accept_email"
            )
            
            seller_result = await notification_service.send_notification(seller_email_request)
            logger.info(f"✅ Seller email audit sent to user {seller_id} for accepted trade {escrow_id_str}")
        except Exception as seller_email_error:
            logger.error(f"❌ Failed to send seller email for trade {escrow_id_str}: {seller_email_error}")
        
        return ConversationHandler.END
            
    except Exception as e:
        logger.error(f"Error accepting trade {escrow_string_id}: {e}")
        await query.edit_message_text("❌ Error accepting trade. Please try again.")
        return ConversationHandler.END


async def handle_seller_decline_trade(update: TelegramUpdate, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle seller declining a trade from the view trade interface - with confirmation"""
    logger.info(f"🔴 DECLINE_HANDLER_CALLED: User {update.effective_user.id if update.effective_user else 'unknown'}")
    query = update.callback_query
    if not query:
        logger.error("❌ DECLINE_HANDLER: No callback query found")
        return ConversationHandler.END
    
    logger.info(f"🔴 DECLINE_HANDLER: callback_data = {query.data}")
    await safe_answer_callback_query(query, "⚠️ Decline confirmation...")
    
    # Extract escrow ID from callback data (format: "decline_trade:ES123ABC")
    callback_data = query.data if query else ""
    if not callback_data.startswith('decline_trade:'):  # type: ignore
        await query.edit_message_text("❌ Invalid decline request.")
        return ConversationHandler.END

    try:
        escrow_string_id = callback_data.split(':', 1)[1]  # decline_trade:ES123ABC -> ES123ABC  # type: ignore
    except (IndexError, ValueError):
        await query.edit_message_text("❌ Invalid trade ID format.")
        return ConversationHandler.END

    # Show confirmation dialog first
    async with async_managed_session() as session:
        try:
            escrow_stmt = select(Escrow).where(Escrow.escrow_id == escrow_string_id)
            escrow_result = await session.execute(escrow_stmt)
            escrow = escrow_result.scalar_one_or_none()
            
            if not escrow:
                await query.edit_message_text("❌ Trade not found.")
                return ConversationHandler.END
                
            trade_amount = Decimal(str(getattr(escrow, 'amount', 0)))
            buyer_fee = Decimal(str(getattr(escrow, 'buyer_fee_amount', 0)))
            total_refund = trade_amount + buyer_fee
            
            await query.edit_message_text(
                f"⚠️ Decline Trade #{escrow.escrow_id}?\n\n"
                f"💰 Escrow: ${trade_amount:.2f}\n"
                f"💳 Buyer Fee: ${buyer_fee:.2f}\n"
                f"💵 Total Refund: ${total_refund:.2f} USD\n\n"
                f"⚠️ This will refund the buyer and cannot be undone.\n\n"
                f"Confirm decline?",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("✅ Yes, Decline Trade", callback_data=f"confirm_seller_decline_{escrow_string_id}"),
                    ],
                    [
                        InlineKeyboardButton("❌ Cancel", callback_data=f"view_trade_{escrow.id}"),
                    ]
                ])
            )
            return ConversationHandler.END
            
        except Exception as e:
            logger.error(f"Error showing decline confirmation for trade {escrow_string_id}: {e}")
            await query.edit_message_text("❌ Error loading trade details.")
            return ConversationHandler.END

async def handle_confirm_seller_decline_trade(update: TelegramUpdate, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle confirmed seller decline after double confirmation"""
    query = update.callback_query
    if not query:
        return ConversationHandler.END
    
    await safe_answer_callback_query(query, "💰 Processing decline...")
    
    # Extract escrow ID from callback data (format: "confirm_seller_decline_ES123ABC")
    callback_data = query.data if query else ""
    if not callback_data.startswith('confirm_seller_decline_'):  # type: ignore
        await query.edit_message_text("❌ Invalid confirmation.")
        return ConversationHandler.END

    try:
        escrow_string_id = callback_data.split('_', 3)[3]  # confirm_seller_decline_ES123ABC -> ES123ABC  # type: ignore
    except (IndexError, ValueError):
        await query.edit_message_text("❌ Invalid trade ID format.")
        return ConversationHandler.END

    # Use async session for decline processing
    from database import async_managed_session
    
    try:
        async with async_managed_session() as session:
            # Get the escrow
            escrow_stmt = select(Escrow).where(Escrow.escrow_id == escrow_string_id)
            escrow_result = await session.execute(escrow_stmt)
            escrow = escrow_result.scalar_one_or_none()
            if not escrow:
                await query.edit_message_text("❌ Trade not found.")
                return ConversationHandler.END

            # Verify user is the seller
            stmt = select(User).where(User.telegram_id == update.effective_user.id)  # type: ignore
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()
            if not user:
                await query.edit_message_text("❌ User not found.")
                return ConversationHandler.END
                
            # Check if user is authorized seller
            is_seller = (
                (getattr(escrow, 'seller_id', None) == user.id) or
                (getattr(escrow, 'seller_contact_type', None) == 'username' and getattr(escrow, 'seller_contact_value', None) == user.username and not escrow.seller_id) or  # type: ignore
                (getattr(escrow, 'seller_contact_type', None) == 'email' and getattr(escrow, 'seller_contact_value', None) == user.email and not escrow.seller_id)  # type: ignore
            )
            
            if not is_seller:
                await query.edit_message_text("❌ Unauthorized decline.")
                return ConversationHandler.END

            # Double-check status - can only decline payment_confirmed trades
            if escrow.status != "payment_confirmed":  # type: ignore
                await query.edit_message_text("❌ Trade status changed. Cannot decline.")
                return ConversationHandler.END

            # CRITICAL FIX: Process refund when seller declines payment_confirmed trade
            # Handle both wallet payments (EscrowHolding) and crypto payments (RefundService)
            
            # First, check for wallet payment (EscrowHolding)
            holding_stmt = select(EscrowHolding).where(
                EscrowHolding.escrow_id == escrow.escrow_id,
                EscrowHolding.status == "active"
            )
            holding_result = await session.execute(holding_stmt)
            holding = holding_result.scalar_one_or_none()
            
            if holding:
                # WALLET PAYMENT: Release funds back to buyer's wallet
                # FAIR REFUND POLICY: Include buyer fee if seller never accepted
                refund_amount = holding.amount_held
                
                if escrow.seller_accepted_at is None:
                    # Seller never accepted: Full refund including buyer fee
                    buyer_fee = Decimal(str(escrow.buyer_fee_amount or 0))
                    refund_amount = refund_amount + buyer_fee
                    logger.info(f"💰 FAIR_REFUND: Seller never accepted {escrow.escrow_id}, refunding escrow (${holding.amount_held}) + buyer fee (${buyer_fee}) = ${refund_amount}")
                
                # Credit buyer's wallet with refund
                refund_success = await CryptoServiceAtomic.credit_user_wallet_atomic(
                    user_id=escrow.buyer_id,  # type: ignore
                    amount=Decimal(str(refund_amount)),  # type: ignore
                    currency="USD",
                    transaction_type="escrow_refund",
                    description=f"Trade refund #{escrow.escrow_id}: Seller declined",
                    escrow_id=escrow.id,  # type: ignore
                    session=session  # type: ignore
                )
                
                if refund_success:
                    # Mark holding as released/refunded
                    holding.status = "refunded"  # type: ignore
                    holding.released_at = datetime.now(timezone.utc)  # type: ignore
                    holding.released_to_user_id = escrow.buyer_id
                    
                    logger.info(f"✅ Wallet refund: ${refund_amount:.2f} to buyer {escrow.buyer_id} for declined trade {escrow.escrow_id}")
                else:
                    await session.rollback()
                    await query.edit_message_text("❌ Failed to process wallet refund. Please contact support.")
                    return ConversationHandler.END
            else:
                # CRYPTO PAYMENT: Use RefundService for proper refund including buyer fee
                from services.refund_service import RefundService
                
                # Validate refund eligibility
                validation_result = await RefundService.validate_refund_eligibility(
                    escrow=escrow,
                    session=session,
                    cancellation_reason="seller_declined"
                )
                
                if validation_result["eligible"]:
                    # Issue refund to buyer's wallet
                    refund_result = await RefundService.calculate_and_issue_refund_if_eligible(
                        escrow=escrow,
                        session=session,
                        cancellation_reason="seller_declined",
                        skip_validation=True  # Already validated above
                    )
                    
                    if not refund_result["success"]:
                        await session.rollback()
                        await query.edit_message_text(f"❌ Failed to process refund: {refund_result.get('message', 'Unknown error')}")
                        return ConversationHandler.END
                    
                    refund_amount = refund_result.get("amount_refunded", 0)
                    logger.info(
                        f"✅ Crypto refund: ${refund_amount:.2f} (escrow + buyer_fee) to buyer {escrow.buyer_id} "
                        f"for declined trade {escrow.escrow_id}"
                    )
                else:
                    # No refund needed (already refunded, no payment, etc.)
                    logger.warning(
                        f"⚠️ No refund issued for declined trade {escrow.escrow_id}: {validation_result.get('reason', 'Unknown')}"
                    )

            # Update escrow status to cancelled (to match listing filters)
            escrow.status = "cancelled"  # type: ignore
            # Note: updated_at handled by SQLAlchemy onupdate
            
            # CRITICAL: Cache user IDs and refund amount before session closes
            buyer_id = int(escrow.buyer_id)  # type: ignore[arg-type]
            seller_id = int(user.id)  # type: ignore[arg-type]
            escrow_id_str = str(escrow.escrow_id)
            escrow_amount = Decimal(str(escrow.amount))
            seller_name = user.first_name or user.username or "Seller"  # type: ignore
            
            # Cache actual refund amount (may include buyer fee if refunded)
            actual_refund_amount = refund_amount if 'refund_amount' in locals() else escrow_amount
            
            # Commit all changes
            await session.commit()
            
            logger.info(f"✅ Seller {seller_id} successfully declined trade {escrow_id_str}")
        
        # SUCCESS MESSAGE (after session closes)
        await query.edit_message_text(
            f"✅ Trade #{escrow_id_str} Declined\n\n"
            f"Buyer refunded automatically.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="main_menu")]
            ])
        )
        
        # SEND NOTIFICATIONS using ConsolidatedNotificationService
        from services.consolidated_notification_service import (
            ConsolidatedNotificationService,
            NotificationRequest,
            NotificationCategory,
            NotificationPriority,
            NotificationChannel
        )
        
        notification_service = ConsolidatedNotificationService()
        await notification_service.initialize()
        
        # Send notification to BUYER with actual refunded amount
        buyer_request = NotificationRequest(
            user_id=buyer_id,
            category=NotificationCategory.ESCROW_UPDATES,
            priority=NotificationPriority.HIGH,
            title="📥 Trade Declined",
            message=f"""📥 Trade #{escrow_id_str} Declined

Seller: {seller_name}
💰 Refunded: ${actual_refund_amount:.2f} to your wallet.

You can create a new trade anytime!""",
            template_data={
                "escrow_id": escrow_id_str,
                "amount": decimal_to_string(actual_refund_amount, precision=2),
                "seller_name": seller_name,
                "status": "declined"
            },
            broadcast_mode=True  # CRITICAL: Dual-channel delivery (Telegram + Email)
        )
        
        buyer_result = await notification_service.send_notification(buyer_request)
        logger.info(f"✅ Buyer notification sent to user {buyer_id} via {len(buyer_result)} channels for declined trade {escrow_id_str}")
        
        # Email-only audit trail for seller
        seller_email_request = NotificationRequest(
            user_id=seller_id,
            category=NotificationCategory.ESCROW_UPDATES,
            priority=NotificationPriority.NORMAL,
            title="✅ Trade Declined - Email Confirmation",
            message=f"""Trade Declined - Email Confirmation

#{escrow_id_str} • ${escrow_amount:.2f}

✅ You have declined this trade
💰 Buyer has been refunded automatically
📧 Trade has been cancelled

This is an email confirmation for your records.""",
            channels=[NotificationChannel.EMAIL],
            broadcast_mode=False,
            template_data={
                "escrow_id": escrow_id_str,
                "amount": decimal_to_string(escrow_amount, precision=2),
                "status": "declined",
                "event_type": "seller_decline_confirmation"
            },
            idempotency_key=f"escrow_{escrow_id_str}_seller_decline_email"
        )
        
        seller_result = await notification_service.send_notification(seller_email_request)
        logger.info(f"✅ Seller email audit sent to user {seller_id} for declined trade {escrow_id_str}")
        
        return ConversationHandler.END
            
    except Exception as e:
        logger.error(f"Error processing seller decline for trade {escrow_string_id}: {e}")
        await query.edit_message_text(
            "❌ An error occurred while processing your decline. Please try again or contact support."
        )
        return ConversationHandler.END

async def handle_keep_trade(update: TelegramUpdate, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle when buyer chooses to keep the trade (cancellation aborted)"""
    query = update.callback_query
    if not query:
        return ConversationHandler.END
    
    await safe_answer_callback_query(query)
    
    from handlers.messages_hub import show_active_trades
    return await show_active_trades(update, context)


async def handle_buyer_cancel_confirmed(update: TelegramUpdate, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Process confirmed buyer cancellation with automatic wallet refund"""
    query = update.callback_query
    if not query:
        return ConversationHandler.END
    
    await safe_answer_callback_query(query, "💰 Processing refund...")
    
    # Extract escrow ID from callback data (format: "confirm_buyer_cancel_ES123ABC")
    callback_data = query.data if query else ""
    if not callback_data.startswith('confirm_buyer_cancel_'):  # type: ignore
        await query.edit_message_text("❌ Invalid confirmation request.")
        return ConversationHandler.END

    try:
        escrow_string_id = callback_data.split('_', 3)[3]  # confirm_buyer_cancel_ES123ABC -> ES123ABC  # type: ignore
    except (IndexError, ValueError):
        await query.edit_message_text("❌ Invalid trade ID format.")
        return ConversationHandler.END

    # Use atomic transaction for refund
    from utils.atomic_transactions import atomic_transaction
    
    try:
        with atomic_transaction() as session:
            # Get the escrow
            escrow = session.query(Escrow).filter(Escrow.escrow_id == escrow_string_id).first()
            if not escrow:
                await query.edit_message_text("❌ Trade not found.")
                return ConversationHandler.END

            # Verify user is the buyer
            stmt = select(User).where(User.telegram_id == update.effective_user.id)  # type: ignore
            result = session.execute(stmt)
            user = result.scalar_one_or_none()
            if not user or getattr(escrow, 'buyer_id', None) != user.id:
                await query.edit_message_text("❌ Unauthorized cancellation.")
                return ConversationHandler.END

            # Double-check status - allow both payment_pending and payment_confirmed
            if escrow.status not in ["payment_pending", "payment_confirmed"]:
                await query.edit_message_text("❌ Trade status changed. Cannot cancel.")
                return ConversationHandler.END

            # Store original status before changing it
            original_status = escrow.status
            
            # Process refund to wallet - but only if payment was made (payment_confirmed)
            if original_status == "payment_confirmed":
                # Process refund to wallet based on who paid the fee
                from utils.fee_policy_messages import FeePolicyMessages
                trade_amount = Decimal(str(getattr(escrow, 'amount', 0)))
                
                # Calculate refund based on fee_split_option with proper precision
                fee_split_option = getattr(escrow, 'fee_split_option', 'buyer_pays')
                fee_amount_raw = getattr(escrow, 'fee_amount', None)
                fee_amount = Decimal(str(fee_amount_raw)) if fee_amount_raw is not None else Decimal("0.0")
                
                # Use FeeCalculator for consistent fee splitting logic
                from utils.fee_calculator import FeeCalculator
                
                # Convert to Decimal for precise calculations
                trade_amount_decimal = Decimal(str(trade_amount))
                total_fee_decimal = Decimal(str(fee_amount))
                
                # Calculate proper fee split using the same logic as fee collection
                buyer_fee_decimal, seller_fee_decimal = FeeCalculator._calculate_fee_split(
                    total_fee_decimal, fee_split_option
                )
                
                # Refund only what the buyer actually paid
                if fee_split_option == 'seller_pays':
                    # Buyer didn't pay any fee, refund only trade amount
                    amount_decimal = trade_amount_decimal
                elif fee_split_option == 'buyer_pays':
                    # Buyer paid the full fee, refund trade amount + full fee
                    amount_decimal = trade_amount_decimal + total_fee_decimal
                elif fee_split_option == 'split':
                    # Buyer paid their portion of the fee, refund trade amount + buyer's fee portion
                    amount_decimal = trade_amount_decimal + buyer_fee_decimal
                else:
                    # Default: assume buyer pays (backward compatibility)
                    # Use 5% default fee rate for legacy escrows without fee_split_option
                    default_fee_decimal = (trade_amount_decimal * Decimal('0.05')).quantize(
                        Decimal('0.01'), rounding=ROUND_HALF_UP
                    )
                    amount_decimal = trade_amount_decimal + default_fee_decimal
                
                # Convert back to float for wallet operations
                amount = Decimal(str(amount_decimal))
                
                # Log fee breakdown for debugging
                logger.info(f"Refund calculation - Trade: ${trade_amount}, Total Fee: ${fee_amount}, "
                           f"Split Option: {fee_split_option}, Buyer Fee: ${Decimal(str(buyer_fee_decimal))}, "
                           f"Seller Fee: ${Decimal(str(seller_fee_decimal))}, Total Refund: ${amount}")
                
                # Add refund to user's USD wallet
                from models import Wallet
                
                # Get or create USD wallet for user
                usd_wallet = session.query(Wallet).filter(
                    Wallet.user_id == user.id,
                    Wallet.currency == "USD"
                ).first()
                
                if not usd_wallet:
                    # Create USD wallet if it doesn't exist
                    usd_wallet = Wallet(
                        user_id=user.id,
                        currency="USD",
                        balance=Decimal("0.00")
                    )
                    session.add(usd_wallet)
                    session.flush()  # Get ID
                else:
                    # Refresh wallet to get latest balance from database
                    session.refresh(usd_wallet)
                
                # Add refund to wallet balance (use available_balance, not legacy balance field)
                current_balance = Decimal(str(Decimal(str(usd_wallet.available_balance or 0))))  # type: ignore
                amount_decimal = Decimal(str(amount))
                new_balance = current_balance + amount_decimal
                usd_wallet.available_balance = new_balance  # type: ignore
            else:
                # payment_pending status - no payment made yet, no refund needed
                trade_amount = Decimal(str(getattr(escrow, 'amount', 0)))
                amount = 0  # No refund for unpaid trades
                new_balance = None  # Will be handled in success message
            
            # Update escrow status to cancelled
            escrow.status = "cancelled"  # type: ignore
            # Note: updated_at handled by SQLAlchemy onupdate
            
            # Create transaction record for the refund - only if there was a refund
            if original_status == "payment_confirmed" and amount > 0:
                from models import Transaction, TransactionType, TransactionStatus
                from utils.helpers import generate_transaction_id
                refund_transaction = Transaction(
                    transaction_id=UniversalIDGenerator.generate_transaction_id(),
                    user_id=user.id,
                    escrow_id=escrow.id,
                    transaction_type=TransactionType.ESCROW_REFUND.value,
                    amount=amount,
                    currency="USD",
                    status=TransactionStatus.CONFIRMED.value,
                    description=f"Refund for cancelled trade #{escrow.escrow_id}",
                    created_at=datetime.now(timezone.utc)
                )
                session.add(refund_transaction)
            
            # Commit all changes
            session.commit()
            
            # Success message with appropriate information based on payment status
            if original_status == "payment_confirmed" and amount > 0:
                # Payment was made - show refund information
                from utils.fee_policy_messages import FeePolicyMessages
                actual_fee_refunded = amount - trade_amount
                refund_info = FeePolicyMessages.get_cancellation_refund_info(
                    Decimal(str(trade_amount)), 
                    Decimal(str(actual_fee_refunded))
                )
                success_text = f"""{refund_info}

New Wallet Balance: ${new_balance:.2f} USD"""
            else:
                # No payment was made - simple cancellation message
                success_text = f"""✅ Trade Cancelled

Your trade has been cancelled.

No payment was made, so no refund needed."""

            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("💰 View Wallet", callback_data="wallet_menu")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
            ])

            await query.edit_message_text(
                success_text,
                parse_mode="Markdown",
                reply_markup=keyboard
            )

            # Notify seller if they exist
            if escrow.seller_id:  # type: ignore
                from services.consolidated_notification_service import consolidated_notification_service
                await consolidated_notification_service.send_escrow_cancelled(escrow, "buyer_cancelled")  # type: ignore

    except Exception as e:
        logger.error(f"Error in handle_buyer_cancel_confirmed: {e}")
        await query.edit_message_text(
            "❌ Refund Failed\n\nThere was an error processing your refund. "
            "Please contact support for assistance.\n\nYour trade status has not been changed."
        )

    return ConversationHandler.END


async def _notify_registered_seller_new_escrow(
    seller_id: int,
    seller_username: str,
    seller_email: str,
    escrow_id: str,
    buyer_name: str,
    amount: Decimal,
    currency: str
) -> None:
    """Notify already-registered seller about new escrow created for them"""
    try:
        from services.consolidated_notification_service import (
            consolidated_notification_service,
            NotificationRequest,
            NotificationChannel,
            NotificationPriority,
            NotificationCategory
        )
        
        message = f"""📦 <b>New Escrow Created!</b>

A buyer has created a new escrow with you as the seller:

<b>Escrow Details:</b>
  • ID: {escrow_id}
  • Amount: {amount} {currency}
  • Buyer: {format_username_html(buyer_name)}

You can accept or decline this escrow from the main menu.

Use /start to view your escrows."""
        
        notification = NotificationRequest(
            user_id=seller_id,
            category=NotificationCategory.ESCROW_UPDATES,
            priority=NotificationPriority.HIGH,
            title=f"New {amount} {currency} escrow from {format_username_html(buyer_name, include_link=False)}",
            message=message,
            channels=[NotificationChannel.TELEGRAM, NotificationChannel.EMAIL],  # Telegram → Email fallback
            template_data={
                'escrow_id': escrow_id,
                'buyer_name': buyer_name,
                'amount': str(amount),
                'currency': currency,
                'event_type': 'registered_seller_new_escrow'
            },
            idempotency_key=f"escrow_{escrow_id}_registered_seller_new_escrow"
        )
        
        await consolidated_notification_service.send_notification(notification)
        logger.info(f"✅ Notified registered seller {seller_id} (@{seller_username}) about new escrow {escrow_id}")
        
        # Send Telegram group notification for seller notified
        try:
            notification_data = {
                'escrow_id': escrow_id,
                'seller_info': f"@{seller_username}" if seller_username else "Seller",
                'notification_channel': 'Telegram',
                'amount': float(amount),
                'notified_at': datetime.now(timezone.utc)
            }
            admin_notif_service = AdminTradeNotificationService()
            asyncio.create_task(admin_notif_service.send_group_notification_seller_notified(notification_data))
            logger.info(f"📤 Queued group notification for seller notified: {escrow_id}")
        except Exception as notif_err:
            logger.error(f"❌ Failed to queue seller notified group notification: {notif_err}")
        
    except Exception as e:
        logger.error(f"Error notifying registered seller {seller_id} about escrow {escrow_id}: {e}")
