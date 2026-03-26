"""
Complete Direct Exchange Handler for instant CRYPTO-NGN-USD conversions
Includes conversation flows, payment processing, and automatic settlement
"""

import logging
from utils.universal_session_manager import (
    universal_session_manager, SessionType
)
import json
import decimal
import asyncio
from decimal import Decimal
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    CallbackQueryHandler,
)
from utils.callback_utils import safe_edit_message_text, safe_answer_callback_query
from database import async_managed_session
from models import (
    User,
    ExchangeOrder,
    SavedAddress,
    SavedBankAccount,
    ExchangeStatus,
)
from utils.normalizers import normalize_telegram_id
from services.financial_gateway import financial_gateway
from services.fincra_service import fincra_service
from services.payment_processor_manager import payment_manager, PaymentProvider
from services.saved_destination_cache import SavedDestinationCache

# PRECISION MONEY UTILITIES - for accurate monetary formatting
from utils.precision_money import (
    format_money,
    decimal_to_string,
    safe_multiply
)

# UNIFIED TRANSACTION SYSTEM INTEGRATION
from services.unified_transaction_service import (
    UnifiedTransactionService, TransactionRequest, UnifiedTransactionType, 
    UnifiedTransactionPriority
)
from services.conditional_otp_service import ConditionalOTPService
from services.dual_write_adapter import DualWriteConfig, DualWriteMode, DualWriteStrategy

# Mobile verification service removed - using Telegram ID binding
# DirectExchangeService will be implemented inline
from config import Config
from sqlalchemy import func, select

logger = logging.getLogger(__name__)

# Use existing user cache for performance optimization  
from utils.user_cache import user_cache

# PERFORMANCE OPTIMIZATION: Exchange context prefetch (reduces 57 queries to 2)
from utils.exchange_prefetch import (
    prefetch_exchange_context,
    get_cached_exchange_data,
    cache_exchange_data,
    invalidate_exchange_cache
)


# SECURITY: Exchange state validation
from utils.exchange_state_validator import ExchangeStateValidator

# OPTIMIZATION: Centralized state validation helper with safety locks
async def ensure_exchange_state(context: ContextTypes.DEFAULT_TYPE) -> dict:
    """Centralized exchange state initialization and validation with safety checks"""
    # SAFETY: Validate context exists and is accessible
    if not hasattr(context, 'user_data') or context.user_data is None:
        logger.warning("Context.user_data is None, initializing new context")
        if not context.user_data:
            pass  # user_data exists but empty
        else:
            context.user_data.clear()  # Clear existing data
    
    # SAFETY: Thread-safe state initialization
    try:
        if "exchange_data" not in context.user_data:
            context.user_data["exchange_data"] = {}
        return context.user_data["exchange_data"]
    except Exception as e:
        logger.error(f"Error accessing exchange_data: {e}")
        # Fallback: Create new clean state
        context.user_data = {"exchange_data": {}}
        return context.user_data["exchange_data"]

# RESILIENCE: Enhanced user cache with retry logic
async def get_user_with_retry(telegram_id: str, max_retries: int = 3) -> Optional[Dict[str, Any]]:
    """Get user from cache with retry logic for transient failures"""
    for attempt in range(max_retries):
        try:
            cached_user = user_cache.get(telegram_id)
            if cached_user is not None:
                logger.debug(f"Cache hit for user {telegram_id} on attempt {attempt + 1}")
                return cached_user
            
            # Cache miss - try database with retry using async session
            async with async_managed_session() as session:
                stmt = select(User).where(User.telegram_id == telegram_id)
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()
                
                if user:
                    # Cache the valid user
                    user_cache.set(telegram_id, user)
                    logger.debug(f"User {telegram_id} cached successfully on attempt {attempt + 1}")
                    return user_cache.get(telegram_id)  # Return cached version
                else:
                    logger.debug(f"User {telegram_id} not found in database")
                    return None
                
        except Exception as e:
            logger.warning(f"User cache retry attempt {attempt + 1} failed: {e}")
            if attempt == max_retries - 1:
                logger.error(f"All {max_retries} user cache attempts failed for {telegram_id}")
                return None
            await asyncio.sleep(0.1 * (attempt + 1))  # Exponential backoff
    
    return None

# RESILIENCE: Enhanced rate fetching with comprehensive error handling
async def fetch_rates_with_resilience(crypto: str, max_retries: int = 2) -> tuple[Optional[Decimal], Optional[Decimal]]:
    """Fetch crypto and NGN rates with comprehensive error handling and fallbacks"""
    for attempt in range(max_retries):
        try:
            # OPTIMIZED: Parallel rate fetching with timeout
            crypto_rate_task = asyncio.wait_for(
                financial_gateway.get_crypto_to_usd_rate(crypto), 
                timeout=10.0  # 10 second timeout
            )
            ngn_rate_task = asyncio.wait_for(
                financial_gateway.get_usd_to_ngn_rate(), 
                timeout=10.0  # 10 second timeout
            )
            
            # Fetch both rates in parallel with comprehensive exception handling
            results = await asyncio.gather(
                crypto_rate_task, ngn_rate_task, return_exceptions=True
            )
            
            crypto_usd_rate, ngn_usd_rate = results
            
            # Handle individual exceptions
            if isinstance(crypto_usd_rate, Exception):
                logger.warning(f"Crypto rate fetch failed on attempt {attempt + 1}: {crypto_usd_rate}")
                crypto_usd_rate = None
            
            if isinstance(ngn_usd_rate, Exception):
                logger.warning(f"NGN rate fetch failed on attempt {attempt + 1}: {ngn_usd_rate}")
                ngn_usd_rate = None
            
            # Success case: both rates retrieved
            if crypto_usd_rate is not None and ngn_usd_rate is not None:
                logger.debug(f"Rates fetched successfully on attempt {attempt + 1}")
                return crypto_usd_rate, ngn_usd_rate
            
            # Partial success: log and retry if not final attempt
            if attempt < max_retries - 1:
                logger.warning(f"Partial rate fetch failure, retrying attempt {attempt + 2}")
                await asyncio.sleep(0.5 * (attempt + 1))  # Exponential backoff
            
        except asyncio.TimeoutError:
            logger.warning(f"Rate fetch timeout on attempt {attempt + 1}")
            if attempt < max_retries - 1:
                await asyncio.sleep(1.0 * (attempt + 1))  # Longer delay for timeouts
        except Exception as e:
            logger.error(f"Unexpected error in rate fetching attempt {attempt + 1}: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(0.5 * (attempt + 1))
    
    logger.error(f"Failed to fetch rates after {max_retries} attempts")
    return None, None

# Custom security exception for financial operations
class SecurityError(Exception):
    """Raised when financial security validation fails"""
    pass

# Exchange conversation states (integer constants)
# REMOVED: State constants no longer needed for pure direct handlers
# Pure direct handlers manage state through context.user_data only

class ExchangeHandler:
    """Complete handler for direct currency exchange operations"""

    @staticmethod
    async def start_exchange(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Entry point for exchange operations - OPTIMIZED"""
        logger.critical(f"🎯 START_EXCHANGE called for user {update.effective_user.id if update.effective_user else 'unknown'}")
        logger.critical(f"📊 Callback data: {update.callback_query.data if update.callback_query else 'None'}")
        
        query = update.callback_query
        if query:
            # IMMEDIATE FEEDBACK: Exchange start
            await safe_answer_callback_query(query, "💱 Starting exchange")
            logger.critical(f"✅ Answered callback query for user {update.effective_user.id}")

        # Check if exchange features are enabled
        if not Config.ENABLE_EXCHANGE_FEATURES:
            text = "⚠️ Exchange features are currently unavailable. Please try again later."
            if query:
                await safe_edit_message_text(query, text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="main_menu")]]))
            return

        # OPTIMIZED: Use cached user validation to reduce DB hits
        user_id = update.effective_user.id if update.effective_user else 0
        telegram_id = str(user_id)
        
        # RESILIENT: Use enhanced user validation with retry logic
        try:
            cached_user = await get_user_with_retry(telegram_id)
            
            if cached_user is None:
                # BLOCK: Unregistered users cannot access exchange
                logger.debug(f"Unregistered user {user_id} attempted exchange access")

                error_text = """❌ Registration Required

🚫 Complete registration first.

Use /start to create account."""

                error_keyboard = InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "🏠 Register Now", callback_data="main_menu"
                            )
                        ]
                    ]
                )

                # FIXED: Use unified message handling to prevent UI duplication
                from utils.message_utils import send_unified_message
                await send_unified_message(
                    update, error_text, reply_markup=error_keyboard
                )
                return
        except Exception as e:
            logger.error(f"Critical error in user validation: {e}")
            # SAFETY: Graceful degradation with system recovery message
            error_text = """⚠️ System Recovery

A temporary error occurred, but we've safely restored your session.

Returning to main menu..."""
            
            # FIXED: Use unified message handling to prevent UI duplication
            from utils.message_utils import send_unified_message
            await send_unified_message(update, error_text)
            
            # Clean state and return to main menu
            if hasattr(context, 'user_data') and context.user_data:
                context.user_data.clear()
            
            from handlers.start import show_main_menu
            await show_main_menu(update, context)
            return
        # else: User is cached and valid, proceed

        # PRODUCTION FIX: Clear any conflicting conversation states
        if context.user_data and "escrow_data" in context.user_data:
            logger.info("🔄 Clearing active escrow conversation for exchange start")
            context.user_data.pop("escrow_data", None)
            
        # RESILIENT: Use enhanced state initialization with safety checks
        try:
            exchange_data = await ensure_exchange_state(context)
            exchange_data.clear()  # Clear any existing data
        except Exception as e:
            logger.error(f"Error initializing exchange state: {e}")
            # SAFETY: Create clean state as fallback
            context.user_data = {"exchange_data": {}}
            exchange_data = context.user_data["exchange_data"]
        
        # PERFORMANCE OPTIMIZATION: Prefetch exchange context (reduces 57 queries to 2)
        cached_data = get_cached_exchange_data(context.user_data)
        if not cached_data:
            # Prefetch user + wallets + saved destinations in 2 batched queries
            async with async_managed_session() as session:
                # Get user_id from cached_user (may be dict or object)
                user_db_id = cached_user.get('id') if isinstance(cached_user, dict) else cached_user.id
                prefetch_data = await prefetch_exchange_context(user_db_id, session)
                if prefetch_data:
                    cache_exchange_data(context.user_data, prefetch_data)
                    logger.info(f"⚡ EXCHANGE_PREFETCH: Cached context in {prefetch_data.prefetch_duration_ms:.1f}ms for user {user_db_id}")
                else:
                    logger.warning(f"⚠️ EXCHANGE_PREFETCH: Failed to cache context for user {user_db_id}")
        else:
            logger.debug(f"✅ EXCHANGE_CACHE_HIT: Using cached exchange data for user {telegram_id}")
            
        # Create exchange session in universal manager
        exchange_session_id = f"exchange_{telegram_id}_{datetime.now().timestamp()}"
        universal_session_manager.create_session(
            user_id=telegram_id,
            session_type=SessionType.DIRECT_EXCHANGE,
            session_id=exchange_session_id,
            metadata={'start_time': datetime.now().isoformat()}
        )
        
        # Mark exchange as the active conversation (legacy support)
        context.user_data["active_conversation"] = "exchange"
        context.user_data["exchange_session_id"] = exchange_session_id

        text = f"""🔄 Quick Exchange

💱 Crypto ⟷ Cash ({Config.AVERAGE_PROCESSING_TIME_MINUTES} min)

Choose type:"""

        keyboard = []
        
        if Config.ENABLE_NGN_FEATURES:
            keyboard.append([
                InlineKeyboardButton(
                    "💱 Sell Crypto", callback_data="exchange_crypto_to_ngn"
                ),
                InlineKeyboardButton(
                    "💰 Buy Crypto", callback_data="exchange_ngn_to_crypto"
                ),
            ])
        
        keyboard.extend([
            [
                InlineKeyboardButton("📊 History", callback_data="exchange_history"),
                InlineKeyboardButton("❓ Help", callback_data="exchange_help"),
            ],
            [InlineKeyboardButton("⬅️ Back", callback_data="main_menu")],
        ])

        reply_markup = InlineKeyboardMarkup(keyboard)

        # OPTIMIZED: Remove redundant error handling - let safe_edit handle "not modified"
        try:
            if query:
                await safe_edit_message_text(query, text, reply_markup=reply_markup)
            elif update.message:
                await update.message.reply_text(text, reply_markup=reply_markup)
            logger.debug(f"Exchange menu displayed for user {telegram_id}")
        except Exception as e:
            logger.error(f"Error in start_exchange: {e}")
            if query:
                await safe_answer_callback_query(query, "❌ Error loading exchange")

        # DIRECT HANDLER: Set state in user_data instead of returning conversation state
        context.user_data["exchange_state"] = "selecting_type"
        context.user_data["active_conversation"] = "exchange"

    @staticmethod
    async def select_exchange_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle exchange type selection with enhanced state validation"""
        query = update.callback_query
        if query:
            # OPTIMIZED: Single callback answer only
            await safe_answer_callback_query(query, "💱 Exchange action")
            logger.debug(f"Exchange button clicked: {query.data}")

        # CRITICAL FIX: Enhanced state validation to prevent frozen buttons
        try:
            # Check if we have valid context
            if not context.user_data:
                logger.warning("No user_data context found, initializing fresh state")
            
            # Validate exchange conversation is properly active
            active_conv = context.user_data.get("active_conversation")
            if active_conv != "exchange":
                logger.warning(f"Exchange button clicked but active_conversation is '{active_conv}', reinitializing")
                # Reinitialize exchange state
                context.user_data["active_conversation"] = "exchange"
                
                # Create fresh exchange session
                telegram_id = update.effective_user.id if update.effective_user else 0
                exchange_session_id = f"exchange_{telegram_id}_{datetime.now().timestamp()}"
                universal_session_manager.create_session(
                    user_id=telegram_id,
                    session_type=SessionType.DIRECT_EXCHANGE,
                    session_id=exchange_session_id,
                    metadata={'start_time': datetime.now().isoformat(), 'recovery': True}
                )
                context.user_data["exchange_session_id"] = exchange_session_id
                logger.info("✅ Exchange state recovered from frozen button issue")
            
            exchange_data = await ensure_exchange_state(context)
            
        except Exception as e:
            logger.error(f"Error accessing exchange state: {e}")
            # SAFETY: Return to main menu on state corruption
            if query:
                await safe_answer_callback_query(query, "⚠️ Session restored")
            
            from handlers.start import show_main_menu
            await show_main_menu(update, context)
            return

        if query and query.data == "exchange_crypto_to_ngn":
            logger.info("Processing crypto to NGN exchange")
            exchange_data["type"] = "crypto_to_ngn"
            return await ExchangeHandler.show_crypto_selection(
                update, context, "sell"
            )
        elif query and query.data == "exchange_ngn_to_crypto":
            logger.info("Processing NGN to crypto exchange")
            exchange_data["type"] = "ngn_to_crypto"
            return await ExchangeHandler.show_crypto_selection(
                update, context, "buy"
            )
        elif query and query.data == "exchange_history":
            logger.info("Showing exchange history")
            return await ExchangeHandler.show_exchange_history(update, context)
        elif query and query.data == "exchange_help":
            logger.info("Showing exchange help")
            return await ExchangeHandler.show_exchange_help(update, context)
        elif query and query.data == "main_menu":
            logger.info("Returning to main menu")
            # CRITICAL: Clean exchange state before routing to main menu
            if context.user_data and "exchange_data" in context.user_data:
                del context.user_data["exchange_data"]
                logger.info("Cleaned exchange_data state - routing to main menu")
            from handlers.start import show_main_menu

            await show_main_menu(update, context)
            return  # CRITICAL: End this conversation
        else:
            logger.warning(
                f"Unhandled exchange callback data: {query.data if query else 'None'}"
            )

        return

    @staticmethod
    async def show_crypto_selection(
        update: Update, context: ContextTypes.DEFAULT_TYPE, action="sell"
    ) -> int:
        """Show cryptocurrency selection menu using standardized UI components"""
        from utils.crypto_ui_components import CryptoUIComponents

        query = update.callback_query

        action_text = "sell" if action == "sell" else "buy"
        opposite_action = "NGN" if action == "sell" else "crypto"

        text = f"""🪙 {action_text.title()} Crypto → {opposite_action}
💰 Live rates • ⚡ 5-15min settlement"""

        # Use standardized crypto selection keyboard with compact layout
        reply_markup = CryptoUIComponents.get_crypto_selection_keyboard(
            callback_prefix="exchange_select_crypto:",
            layout="compact",  # 2-column layout for exchange interface
            back_callback="exchange_back",
        )

        if query:
            await safe_edit_message_text(query, text, reply_markup=reply_markup)

        return

    @staticmethod
    async def select_crypto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle cryptocurrency selection"""
        query = update.callback_query
        if query:
            # IMMEDIATE FEEDBACK: Show crypto name to confirm selection
            crypto = query.data.split(":")[1] if query.data and ":" in query.data else ""
            if "exchange_sell_" in query.data or "exchange_buy_" in query.data:
                # Handle buy/sell patterns like exchange_sell_btc_ngn, exchange_buy_btc_ngn
                parts = query.data.split("_")
                if len(parts) >= 3:
                    operation = parts[1]  # "sell" or "buy"
                    crypto = parts[2].upper()  # "btc" -> "BTC"
                    to_currency = parts[3].upper() if len(parts) > 3 else "NGN"  # "ngn" -> "NGN"
                    
                    feedback_text = f"✅ {operation.title()} {crypto} → {to_currency}"
                else:
                    feedback_text = "Processing..."
            else:
                feedback_text = f"✅ {crypto} selected" if crypto else "Processing..."
            await safe_answer_callback_query(query, feedback_text)

        # Handle standard crypto selection
        if query and query.data and query.data.startswith("exchange_select_crypto:"):
            crypto = query.data.split(":")[1]
            if context.user_data and "exchange_data" in context.user_data:
                context.user_data["exchange_data"]["crypto"] = crypto

            return await ExchangeHandler.ask_amount(update, context)
            
        # Handle buy/sell patterns like exchange_sell_btc_ngn, exchange_buy_btc_ngn
        elif query and query.data and ("exchange_sell_" in query.data or "exchange_buy_" in query.data):
            parts = query.data.split("_")
            if len(parts) >= 3:
                operation = parts[1]  # "sell" or "buy"
                crypto = parts[2].upper()  # "btc" -> "BTC"
                to_currency = parts[3].upper() if len(parts) > 3 else "NGN"  # "ngn" -> "NGN"
                
                # Initialize exchange state with proper currency information
                if not context.user_data:
                    context.user_data = {}
                if "exchange_data" not in context.user_data:
                    context.user_data["exchange_data"] = {}
                
                exchange_data = context.user_data["exchange_data"]
                
                if operation == "sell":
                    # Selling crypto for NGN
                    exchange_data["from_currency"] = crypto
                    exchange_data["to_currency"] = to_currency
                    exchange_data["source_currency"] = crypto
                    exchange_data["target_currency"] = to_currency
                    exchange_data["crypto"] = crypto
                    exchange_data["type"] = "sell"
                elif operation == "buy":
                    # Buying crypto with NGN
                    exchange_data["from_currency"] = to_currency  # NGN
                    exchange_data["to_currency"] = crypto
                    exchange_data["source_currency"] = to_currency
                    exchange_data["target_currency"] = crypto
                    exchange_data["crypto"] = crypto
                    exchange_data["type"] = "buy"
                
                return await ExchangeHandler.ask_amount(update, context)
                
        elif query and query.data == "exchange_back":
            return await ExchangeHandler.start_exchange(update, context)

        return

    @staticmethod
    async def ask_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Ask user for exchange amount with live rates"""
        query = update.callback_query

        # ENHANCED CONTEXT RECOVERY: Attempt to restore session data
        if not context.user_data or "exchange_data" not in context.user_data:
            user_id = update.effective_user.id if update.effective_user else "unknown"
            logger.warning(
                f"🔧 EXCHANGE CONTEXT RECOVERY: Empty context detected for user {user_id}"
            )

            # Initialize context if missing
            context.user_data["exchange_data"] = {}

            # Graceful recovery with helpful messaging
            if query:
                await query.edit_message_text(
                    "⚠️ *Session Timeout - Let's Continue*\n\nYour session expired, but we'll help you restart quickly.\n\n💡 *This keeps your data secure*",
                    reply_markup=InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    "🔄 Quick Exchange", callback_data="start_exchange"
                                )
                            ],
                            [
                                InlineKeyboardButton(
                                    "🏠 Main Menu", callback_data="main_menu"
                                )
                            ],
                        ]
                    ),
                )
            return

        exchange_data = context.user_data["exchange_data"]
        crypto = exchange_data.get("crypto")
        exchange_type = exchange_data.get("type")

        if not crypto or not exchange_type:
            logger.warning(
                f"🔧 EXCHANGE RECOVERY: Missing crypto ({crypto}) or type ({exchange_type}) data"
            )
            if query:
                await query.edit_message_text(
                    "⚠️ *Session Data Missing*\n\nSome exchange details were lost. Let's restart with a secure session.\n\n🔒 *Your data stays protected*",
                    reply_markup=InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    "🔄 New Secure Session",
                                    callback_data="direct_exchange",
                                )
                            ],
                            [
                                InlineKeyboardButton(
                                    "🏠 Main Menu", callback_data="main_menu"
                                )
                            ],
                        ]
                    ),
                )
            return

        # Fetch live rates with optimization to prevent duplicate API calls
        rate_info = None
        try:
            # OPTIMIZATION: Cache USD-NGN rate to prevent duplicate calls within same operation
            import time
            cached_usd_ngn = context.user_data.get('cached_exchange_usd_ngn_rate')
            if cached_usd_ngn and (time.time() - cached_usd_ngn.get('fetched_at', 0)) < 300:  # 5 min cache
                clean_usd_ngn_rate = cached_usd_ngn['rate']
                logger.info("🚀 Using cached USD-NGN rate to prevent duplicate API call")
            else:
                # Fresh rate fetch
                clean_usd_ngn_rate = await financial_gateway.get_usd_to_ngn_rate_clean()
                if clean_usd_ngn_rate:
                    context.user_data['cached_exchange_usd_ngn_rate'] = {
                        'rate': clean_usd_ngn_rate,
                        'fetched_at': time.time()
                    }
                    logger.info("🔄 Cached fresh USD-NGN rate for exchange operation")
                    
            if exchange_type == "crypto_to_ngn":
                # RESILIENT: Use enhanced rate fetching with retry logic
                crypto_usd_rate, _ = await fetch_rates_with_resilience(crypto)

                if crypto_usd_rate and clean_usd_ngn_rate:
                    # SELLING CRYPTO: Market rate - configurable markup % = Display rate
                    market_rate_per_crypto = crypto_usd_rate * clean_usd_ngn_rate
                    markup_percentage = (
                        Config.EXCHANGE_MARKUP_PERCENTAGE / 100
                    )  # Convert to decimal
                    markup_amount = market_rate_per_crypto * markup_percentage
                    display_rate = market_rate_per_crypto - markup_amount

                    rate_info = f"₦{decimal_to_string(display_rate, precision=0)} per {crypto}"
                    logger.info(
                        f"SELL {crypto}: Market {format_money(market_rate_per_crypto, 'NGN')} - Markup({Config.EXCHANGE_MARKUP_PERCENTAGE}%) {format_money(markup_amount, 'NGN')} = Display {format_money(display_rate, 'NGN')}"
                    )
                else:
                    rate_info = "Rate temporarily unavailable"
            else:
                # NGN to crypto - show how much crypto per NGN
                # RESILIENT: Use enhanced rate fetching with retry logic
                crypto_usd_rate, _ = await fetch_rates_with_resilience(crypto)
                # OPTIMIZATION: Use cached rate instead of duplicate API call
                # clean_usd_ngn_rate already fetched above and cached

                if crypto_usd_rate and clean_usd_ngn_rate:
                    # BUYING CRYPTO: Market rate + configurable markup % = Display rate
                    market_rate_per_crypto = crypto_usd_rate * clean_usd_ngn_rate
                    markup_percentage = (
                        Config.EXCHANGE_MARKUP_PERCENTAGE / 100
                    )  # Convert to decimal
                    markup_amount = market_rate_per_crypto * markup_percentage
                    display_rate = market_rate_per_crypto + markup_amount

                    # Show consistent format: ₦X per crypto (what you pay for 1 crypto)
                    rate_info = f"₦{decimal_to_string(display_rate, precision=0)} per {crypto}"
                    logger.info(
                        f"BUY {crypto}: Market {format_money(market_rate_per_crypto, 'NGN')} + Markup({Config.EXCHANGE_MARKUP_PERCENTAGE}%) {format_money(markup_amount, 'NGN')} = Display {format_money(display_rate, 'NGN')}"
                    )
                else:
                    rate_info = "Rate temporarily unavailable"
        except Exception as e:
            logger.error(f"Error fetching live rates: {e}")
            # Clear cache on error to ensure fresh rates on retry
            if 'cached_exchange_usd_ngn_rate' in context.user_data:
                del context.user_data['cached_exchange_usd_ngn_rate']
                logger.info("🗑️ Cleared USD-NGN rate cache due to error")
            # Try to get emergency fallback rate for better UX
            try:
                # OPTIMIZATION: Use cached rate first before making another API call
                cached_usd_ngn = context.user_data.get('cached_exchange_usd_ngn_rate')
                if cached_usd_ngn:
                    emergency_rate = cached_usd_ngn['rate']
                    logger.info("🚀 Using cached USD-NGN rate for emergency fallback")
                else:
                    # Last resort: make fresh API call if no cache available
                    emergency_rate = await financial_gateway.get_usd_to_ngn_rate_clean()
                    logger.info(f"Fresh emergency rate fetch: {emergency_rate}")
                    
                if emergency_rate and emergency_rate > 0:
                    rate_info = f"⚠️ Using fallback rate: {format_money(emergency_rate, 'NGN')} per USD"
                    logger.info(f"Emergency rate fallback successful: {emergency_rate}")
                else:
                    rate_info = "Rate temporarily unavailable - please try again"
            except Exception as fallback_error:
                logger.error(f"Emergency rate fallback failed: {fallback_error}")
                rate_info = "Rate temporarily unavailable - please try again"

        # Create dynamic examples based on selected crypto
        if crypto.startswith("BTC"):
            pass
        elif crypto.startswith("ETH"):
            pass
        elif crypto.startswith("USDT"):
            pass
        elif crypto in ["LTC", "DOGE", "XMR"]:
            pass
        elif crypto == "TRX":
            pass
        else:
            pass

        if exchange_type == "crypto_to_ngn":
            # COMPACT EXCHANGE INTERFACE - Simplified for mobile UX
            if rate_info and rate_info != "Rate temporarily unavailable":
                try:
                    # Extract and format rate compactly
                    rate_str = (
                        rate_info.replace("₦", "").replace(",", "").split(" per ")[0]
                    )
                    numeric_rate = Decimal(str(rate_str or 0))

                    # Ultra-compact rate display
                    if numeric_rate >= 1_000_000:
                        rate_display = f"₦{decimal_to_string(numeric_rate / Decimal('1000000'), precision=1)}M"
                    elif numeric_rate >= 1_000:
                        rate_display = f"₦{decimal_to_string(numeric_rate / Decimal('1000'), precision=0)}K"
                    else:
                        rate_display = f"₦{decimal_to_string(numeric_rate, precision=0)}"

                    # Single example for clarity (avoid confusion)
                    if crypto.startswith("BTC"):
                        example = 0.01
                    elif crypto.startswith("ETH"):
                        example = 0.1
                    elif crypto.startswith("USDT"):
                        example = 500
                    elif crypto in ["LTC", "DOGE", "XMR"]:
                        example = 5
                    elif crypto == "TRX":
                        example = 5000
                    else:
                        example = 1

                    example_ngn = numeric_rate * example
                    if example_ngn >= 1_000_000:
                        example_display = f"₦{decimal_to_string(example_ngn / Decimal('1000000'), precision=1)}M"
                    elif example_ngn >= 1_000:
                        example_display = f"₦{decimal_to_string(example_ngn / Decimal('1000'), precision=0)}K"
                    else:
                        example_display = f"₦{decimal_to_string(example_ngn, precision=0)}"

                    text = f"""💱 Sell {crypto} → NGN
{rate_display} per {crypto} • Ex: {example}→{example_display}

Enter {crypto} amount:"""
                except Exception:
                    text = f"""💱 Sell {crypto} → NGN
{rate_info}

Enter {crypto} amount:"""
            else:
                text = f"""💱 Sell {crypto} → NGN
{rate_info}

Enter amount:"""
        else:
            # COMPACT NGN to crypto format
            text = f"""💰 Buy {crypto} ← NGN
{rate_info}

Enter NGN amount:"""

        keyboard = [
            [
                InlineKeyboardButton(
                    "⬅️ Back to Crypto Selection", callback_data="exchange_back_crypto"
                )
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        if query:
            await safe_edit_message_text(query, text, reply_markup=reply_markup)

        # DIRECT HANDLER: Set state for text message routing
        context.user_data["exchange_state"] = "entering_amount"
        
        return

    @staticmethod
    async def process_amount_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Process amount input - alias for process_amount for test compatibility"""
        return await ExchangeHandler.process_amount(update, context)

    @staticmethod
    async def process_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Process amount input and show rate calculation"""
        message = update.message
        if not message or not message.text:
            if message:
                await message.reply_text("❌ Please enter a valid amount.")
            return

        # CRITICAL: Session state validation to prevent routing bugs
        if not context.user_data or "exchange_data" not in context.user_data:
            logger.error("🚨 CRITICAL BUG FIXED: Exchange session state lost - user input being routed to wrong handler")
            await message.reply_text(
                "⚠️ Session Expired\n\nYour exchange session has expired. Let's start fresh.\n\n🔄 Starting new exchange...",
                parse_mode="HTML"
            )
            # Auto-recover by restarting exchange flow (use class reference)
            return await ExchangeHandler.start_exchange(update, context)

        try:
            # IMMEDIATE FEEDBACK: Show processing indicator
            processing_msg = await message.reply_text("⏳ Processing amount...")
            
            # SECURITY: Enhanced input validation and sanitization
            amount_text = message.text.strip()

            # Validate and sanitize amount input
            try:
                amount_decimal = Decimal(amount_text)
                
                # DYNAMIC MINIMUM VALIDATION: Calculate real minimum based on $5 USD requirement
                min_amount_usd = Config.MIN_EXCHANGE_AMOUNT_USD
                exchange_type = context.user_data["exchange_data"].get("type")
                
                if exchange_type == "crypto_to_ngn":
                    crypto = context.user_data["exchange_data"].get("crypto")
                    from services.financial_gateway import financial_gateway
                    crypto_usd_rate = await financial_gateway.get_crypto_to_usd_rate(crypto)
                    
                    if crypto_usd_rate:
                        # Calculate dynamic minimum crypto amount needed to meet $5 USD
                        min_crypto_needed = min_amount_usd / Decimal(str(crypto_usd_rate))
                        
                        if amount_decimal < min_crypto_needed:
                            # Delete processing message and show error
                            try:
                                await processing_msg.delete()
                            except Exception:
                                pass
                            # Format crypto amounts to avoid scientific notation
                            from utils.decimal_precision import MonetaryDecimal
                            amount_display = MonetaryDecimal.format_crypto(amount_decimal, crypto)
                            min_display = MonetaryDecimal.format_crypto(min_crypto_needed, crypto)
                            
                            usd_value = safe_multiply(amount_decimal, Decimal(str(crypto_usd_rate)), precision=2)
                            await message.reply_text(
                                f"❌ *Minimum Transaction: {format_money(min_amount_usd, 'USD')}*\n\n"
                                f"Your amount: {amount_display} = {format_money(usd_value, 'USD')}\n\n"
                                f"Required minimum: {min_display} (≈ {format_money(min_amount_usd, 'USD')})",
                                parse_mode="HTML"
                            )
                            return
                else:
                    # Basic minimum for non-crypto inputs (keep simple 0.01 check for NGN inputs)
                    if amount_decimal < Decimal("0.01"):
                        try:
                            await processing_msg.delete()
                        except Exception:
                            pass
                        await message.reply_text(
                            "❌ Amount too small. Minimum amount is 0.01"
                        )
                        return
                
                # Check maximum amount (applies to all exchange types)
                if amount_decimal > Decimal("1000000.0"):
                    # Delete processing message and show error
                    try:
                        await processing_msg.delete()
                    except Exception:
                        pass
                    await message.reply_text(
                        "❌ Amount too large. Maximum amount is 1,000,000"
                    )
                    return
                amount = amount_decimal
            except (ValueError, decimal.InvalidOperation):
                # Delete processing message and show error
                try:
                    await processing_msg.delete()
                except Exception:
                    pass
                await message.reply_text(
                    "❌ Invalid amount. Please enter a valid number.\n\n"
                    "💡 Examples of valid amounts:\n"
                    "• 0.06\n"
                    "• 1.5\n"
                    "• 100\n\n"
                    "🚫 Don't use:\n"
                    "• Commas (1,000)\n"
                    "• Currency symbols ($, ₦)\n"
                    "• Letters or spaces"
                )
                return

            if amount <= 0:
                raise ValueError("Amount must be positive")

            # Check minimum exchange amount ($5 USD equivalent)
            min_amount_usd = Config.MIN_EXCHANGE_AMOUNT_USD

            # For crypto-to-NGN, validate crypto amount in USD equivalent
            # For NGN-to-crypto, validate NGN amount converted to USD equivalent
            if not context.user_data or "exchange_data" not in context.user_data:
                await message.reply_text(
                    "❌ Session expired. Please restart with /exchange"
                )
                return
            exchange_type = context.user_data["exchange_data"].get("type")

            if exchange_type == "crypto_to_ngn":
                # Calculate USD equivalent of crypto amount
                if not context.user_data or "exchange_data" not in context.user_data:
                    await message.reply_text(
                        "❌ Session expired. Please restart with /exchange"
                    )
                    return
                crypto = context.user_data["exchange_data"].get("crypto")
                # Rate fetching now handled by financial_gateway
                from services.financial_gateway import financial_gateway

                crypto_usd_rate = await financial_gateway.get_crypto_to_usd_rate(crypto)
                if crypto_usd_rate:
                    usd_equivalent = Decimal(str(amount)) * Decimal(
                        str(crypto_usd_rate)
                    )
                    # Fix floating point precision: add small tolerance (1 cent)
                    if usd_equivalent < (
                        Decimal(str(min_amount_usd)) - Decimal("0.01")
                    ):
                        # Format crypto amounts to avoid scientific notation
                        from utils.decimal_precision import MonetaryDecimal
                        amount_display = MonetaryDecimal.format_crypto(amount, crypto)
                        min_needed = min_amount_usd/crypto_usd_rate
                        min_display = MonetaryDecimal.format_crypto(min_needed, crypto)
                        
                        await message.reply_text(
                            f"❌ *Minimum Transaction: {format_money(min_amount_usd, 'USD')}*\n\n"
                            f"Your {crypto} amount ({amount_display}) = {format_money(usd_equivalent, 'USD')}\n\n"
                            f"Please enter at least {min_display}",
                        )
                        return
            elif exchange_type == "ngn_to_crypto":
                # Calculate USD equivalent of NGN amount
                # Rate fetching now handled by financial_gateway
                from services.financial_gateway import financial_gateway

                usd_ngn_rate = await financial_gateway.get_usd_to_ngn_rate_clean()
                if usd_ngn_rate:
                    usd_equivalent = Decimal(str(amount)) / Decimal(str(usd_ngn_rate))
                    # Fix floating point precision: add small tolerance (1 cent)
                    if usd_equivalent < (
                        Decimal(str(min_amount_usd)) - Decimal("0.01")
                    ):
                        min_ngn_amount = safe_multiply(min_amount_usd, usd_ngn_rate, precision=2)
                        await message.reply_text(
                            f"❌ *Minimum Transaction: {format_money(min_amount_usd, 'USD')}*\n\n"
                            f"Your NGN amount ({format_money(amount, 'NGN')}) = {format_money(usd_equivalent, 'USD')}\n\n"
                            f"Please enter at least {format_money(min_ngn_amount, 'NGN')}",
                        )
                        return

            # Context recovery for amount processing
            if not context.user_data or "exchange_data" not in context.user_data:
                if update.message:
                    await update.message.reply_text(
                        "❌ *Session Expired*\n\nYour exchange session has expired. Please start over with /exchange",
                    )
                return

            exchange_data = context.user_data["exchange_data"]
            crypto = exchange_data.get("crypto")
            exchange_type = exchange_data.get("type")

            if not crypto or not exchange_type:
                if update.message:
                    await update.message.reply_text(
                        "❌ *Session Data Lost*\n\nPlease start your exchange over with /exchange",
                    )
                return

            # Calculate rates WITH RATE LOCK for price protection
            if exchange_type == "crypto_to_ngn":
                # Get internal database user ID from Telegram ID
                if not update.effective_user or not update.effective_user.id:
                    if message:
                        await message.reply_text(
                            "❌ Authentication error. Please restart with /start"
                        )
                    return
                telegram_user_id = update.effective_user.id
                async with async_managed_session() as session:
                    from models import User

                    stmt = select(User).where(User.telegram_id == normalize_telegram_id(telegram_user_id))
                    result = await session.execute(stmt)
                    db_user = result.scalar_one_or_none()
                    
                    if not db_user:
                        if message:
                            await message.reply_text(
                                "❌ User not found. Please restart with /start"
                            )
                        return

                # RESILIENT: Use enhanced rate fetching with comprehensive error handling
                crypto_usd_rate, ngn_usd_rate = await fetch_rates_with_resilience(crypto)
                
                # Handle rate fetch failures with user-friendly messaging
                if not crypto_usd_rate or not ngn_usd_rate:
                    error_msg = f"❌ Unable to get current {crypto} rates. Please try again in a moment."
                    if message:
                        await message.reply_text(error_msg)
                    return

                usd_amount = Decimal(str(amount)) * crypto_usd_rate
                final_ngn_amount = usd_amount * ngn_usd_rate

                # Generate real order ID
                import uuid

                order_id = f"CRYPTO_NGN_{uuid.uuid4().hex[:8].upper()}"

                rate_info = {
                    "final_ngn_amount": Decimal(str(final_ngn_amount or 0)),
                    "order_id": order_id,
                    "crypto_usd_rate": Decimal(str(crypto_usd_rate or 0)),
                    "ngn_usd_rate": Decimal(str(ngn_usd_rate or 0)),
                    "effective_rate": Decimal(str(final_ngn_amount or 0))
                    / Decimal(str(amount or 1)),  # NGN per crypto unit
                    "exchange_markup_percentage": Config.EXCHANGE_MARKUP_PERCENTAGE,
                    "processing_fee": Decimal("0.0"),  # No separate processing fees - markup only
                }

                exchange_data["amount"] = amount
                exchange_data["rate_info"] = rate_info
                exchange_data["order_id"] = rate_info["order_id"]

                # Fix Config scope issue
                rate_lock_duration = Config.RATE_LOCK_DURATION_MINUTES
                text = f"""💱 Sell {amount} {crypto} → ₦{rate_info['final_ngn_amount']:,.0f}
⏰ Rate protected for {rate_lock_duration} minutes

Next: Bank account details"""

                return await ExchangeHandler.ask_bank_details(
                    update, context, text
                )

            else:  # ngn_to_crypto
                # Get internal database user ID from Telegram ID
                if not update.effective_user or not update.effective_user.id:
                    if message:
                        await message.reply_text(
                            "❌ Authentication error. Please restart with /start"
                        )
                    return
                telegram_user_id = update.effective_user.id
                async with async_managed_session() as session:
                    from models import User

                    stmt = select(User).where(User.telegram_id == normalize_telegram_id(telegram_user_id))
                    result = await session.execute(stmt)
                    db_user = result.scalar_one_or_none()
                    
                    if not db_user:
                        if message:
                            await message.reply_text(
                                "❌ User not found. Please restart with /start"
                            )
                        return

                ngn_amount = amount
                # RESILIENT: Use enhanced rate calculation with comprehensive error handling
                from services.financial_gateway import financial_gateway

                crypto_usd_rate, ngn_usd_rate = await fetch_rates_with_resilience(crypto)

                if not crypto_usd_rate or not ngn_usd_rate:
                    if message:
                        await message.reply_text(
                            "❌ Unable to get current rates. Please try again."
                        )
                    return

                usd_amount = Decimal(str(ngn_amount)) / ngn_usd_rate
                crypto_amount = usd_amount / crypto_usd_rate

                # Generate real order ID
                import uuid

                order_id = f"NGN_CRYPTO_{uuid.uuid4().hex[:8].upper()}"

                rate_info = {
                    "crypto_amount": Decimal(str(crypto_amount or 0)),
                    "order_id": order_id,
                    "crypto_usd_rate": Decimal(str(crypto_usd_rate or 0)),
                    "ngn_usd_rate": Decimal(str(ngn_usd_rate or 0)),
                    "effective_rate": Decimal(str(amount or 0))
                    / Decimal(str(crypto_amount or 1)),  # NGN per crypto unit
                    "exchange_markup_percentage": Config.EXCHANGE_MARKUP_PERCENTAGE,
                    "processing_fee": Decimal("0.0"),  # No separate processing fees - markup only
                }

                exchange_data["amount"] = amount
                exchange_data["rate_info"] = rate_info
                exchange_data["order_id"] = rate_info["order_id"]

                # Fix Config scope issue
                rate_lock_duration = Config.RATE_LOCK_DURATION_MINUTES
                text = f"""💰 Send Payment

📤 {format_money(amount, 'NGN')} via bank transfer
📥 {decimal_to_string(rate_info['crypto_amount'], precision=6)} {crypto}
⏰ {rate_lock_duration}min

Next: {crypto} wallet address"""

                return await ExchangeHandler.ask_wallet_address(
                    update, context, text
                )

        except ValueError:
            if message:
                await message.reply_text(
                    "❌ Invalid amount. Please enter a valid number."
                )
            return

    @staticmethod
    async def ask_bank_details(
        update: Update, context: ContextTypes.DEFAULT_TYPE, quote_text: str
    ) -> int:
        """Ask for bank account details - check for saved account first"""
        async with async_managed_session() as session:
            if not update.effective_user or not update.effective_user.id:
                message = update.message
                if message:
                    await message.reply_text("❌ User authentication error.")
                return

            query = update.callback_query  # Fix undefined query variable
            if not update.effective_user:
                if query:
                    await query.edit_message_text("❌ Authentication error.")
                return

            user_id = str(update.effective_user.id)
            stmt = select(User).where(User.telegram_id == user_id)
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()

            if not user:
                message = update.message
                if message:
                    await message.reply_text("❌ User not found.")
                return

            # Check if user has saved bank accounts using cached method
            saved_banks = await SavedDestinationCache.load_bank_accounts_optimized(int(user_id))
            has_saved_bank = len(saved_banks) > 0

            if has_saved_bank:
                # Show saved account with options
                return await ExchangeHandler.show_saved_bank_account(
                    update, context, quote_text, user
                )
            else:
                # No saved account - go to bank verification flow
                return await ExchangeHandler.start_bank_verification(
                    update, context, quote_text
                )

    @staticmethod
    async def show_saved_bank_account(
        update: Update, context: ContextTypes.DEFAULT_TYPE, quote_text: str, user
    ) -> int:
        """Show saved bank accounts with selection options - Consistent with wallet cashout UX"""
        async with async_managed_session():
            # Get saved bank accounts using cached method - ordered by last used
            saved_banks = await SavedDestinationCache.load_bank_accounts_optimized(int(user.telegram_id))

            if saved_banks:
                # Clean, consistent format matching wallet cashout
                text = f"""{quote_text}

🏦 Select bank account:"""

                keyboard = []

                # Add saved banks as buttons with validation - consistent format with wallet cashout
                for bank in saved_banks:
                    # ENHANCED: Validate saved bank account before displaying
                    from services.destination_validation_service import (
                        DestinationValidationService,
                    )

                    try:
                        validation = (
                            DestinationValidationService.validate_saved_bank_account(
                                bank
                            )
                        )

                        if not validation.get("valid", False):
                            # Skip invalid bank accounts and log the issue
                            logger.warning(
                                f"Skipping invalid saved bank account {bank['id']} for user: {validation.get('errors', [])}"
                            )
                            continue
                    except Exception as validation_error:
                        logger.error(
                            f"Validation error for saved bank account {bank.get('id', 'unknown')}: {validation_error}"
                        )
                        continue

                    label = bank.get("label") or bank.get("bank_name", "Bank Account")
                    is_default = bank.get("is_default", False)
                    account_number = bank.get("account_number", "")

                    default_mark = "⭐ " if is_default else ""
                    if len(account_number) >= 4:
                        masked_account = f"••••{account_number[-4:]}"
                    else:
                        masked_account = "••••"

                    keyboard.append(
                        [
                            InlineKeyboardButton(
                                f"🏦 {default_mark}{label} ({masked_account})",
                                callback_data=f"exchange_use_bank:{bank['id']}",
                            )
                        ]
                    )

                # Add option for new bank account - consistent wording
                keyboard.append(
                    [
                        InlineKeyboardButton(
                            "➕ Add New Bank Account",
                            callback_data="exchange_different_bank",
                        )
                    ]
                )

                keyboard.append(
                    [
                        InlineKeyboardButton(
                            "⬅️ Back to Amount", callback_data="exchange_back_amount"
                        )
                    ]
                )

            else:
                # No saved bank accounts - go directly to verification
                return await ExchangeHandler.start_bank_verification(
                    update, context, quote_text
                )

            reply_markup = InlineKeyboardMarkup(keyboard)
            if update.message:
                await update.message.reply_text(text, reply_markup=reply_markup)
            return

    @staticmethod
    async def start_bank_verification(
        update: Update, context: ContextTypes.DEFAULT_TYPE, quote_text: str
    ) -> int:
        """Start bank account verification process"""
        text = f"""{quote_text}

🏦 Enter your 10-digit bank account number:
(We'll auto-detect your bank and verify your details)

Example: 0123456789"""

        keyboard = [
            [
                InlineKeyboardButton(
                    "⬅️ Back to Amount", callback_data="exchange_back_amount"
                )
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        if update.message:
            await update.message.reply_text(text, reply_markup=reply_markup)

        # Set context for bank verification with null safety
        if context.user_data is not None:
            context.user_data["exchange_bank_mode"] = "verification"
        return

    @staticmethod
    async def ask_wallet_address(
        update: Update, context: ContextTypes.DEFAULT_TYPE, quote_text: str
    ) -> int:
        """Ask for crypto wallet address for crypto payout - Consistent with wallet cashout UX"""
        # Add null safety for context data access
        if not context.user_data or "exchange_data" not in context.user_data:
            return
        crypto = context.user_data["exchange_data"]["crypto"]

        # Get saved crypto addresses for this currency
        async with async_managed_session() as session:
            query = update.callback_query  # Fix undefined query variable
            if not update.effective_user:
                if query:
                    await query.edit_message_text("❌ Authentication error.")
                return

            user_id = str(update.effective_user.id)
            stmt = select(User).where(User.telegram_id == user_id)
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()

            saved_addresses = []
            if user:
                # Use cached method to load saved addresses
                saved_addresses = await SavedDestinationCache.load_crypto_addresses_optimized(
                    int(user_id), currency=crypto
                )

        # Currency emoji mapping
        currency_emoji = {
            "BTC": "₿",
            "ETH": "Ξ",
            "LTC": "Ł",
            "DOGE": "Ð",
            "TRX": "🏛️",
            "XMR": "ⓧ",
            "USDT-TRC20": "💰",
            "USDT-ERC20": "💰",
        }.get(crypto, "💰")

        keyboard = []

        if saved_addresses:
            # Show saved addresses first - consistent with wallet cashout format
            text = f"""{quote_text}

{currency_emoji} Select {crypto} address:"""

            # Add saved addresses as buttons with validation - consistent format
            for addr in saved_addresses:
                # ENHANCED: Validate saved address before displaying
                from services.destination_validation_service import (
                    DestinationValidationService,
                )

                validation = DestinationValidationService.validate_saved_address(addr)

                if not validation["valid"]:
                    # Skip invalid addresses and log the issue
                    logger.warning(
                        f"Skipping invalid saved address {addr['id']} for user: {validation['errors']}"
                    )
                    continue

                label = addr.get("label") or f"{crypto} Wallet"
                is_default = False
                address_value = addr.get("address", "")

                default_mark = "⭐ " if is_default else ""
                if len(address_value) >= 12:
                    masked = f"{address_value[:6]}...{address_value[-6:]}"
                else:
                    masked = "••••"

                keyboard.append(
                    [
                        InlineKeyboardButton(
                            f"{currency_emoji} {default_mark}{label} ({masked})",
                            callback_data=f"exchange_use_address:{addr['id']}",
                        )
                    ]
                )

            # Add option for new address
            keyboard.append(
                [
                    InlineKeyboardButton(
                        "➕ Add New Address", callback_data="exchange_new_address"
                    )
                ]
            )

        else:
            # No saved addresses - show manual entry prompt
            text = f"""{quote_text}

🔗 {currency_emoji} {crypto} Wallet Address

📝 Enter your {crypto} wallet address:

⚠️ <i>Double-check your address - wrong addresses result in permanent loss.</i>"""

        keyboard.append(
            [
                InlineKeyboardButton(
                    "⬅️ Back to Amount", callback_data="exchange_back_amount"
                )
            ]
        )
        reply_markup = InlineKeyboardMarkup(keyboard)

        if update.message:
            await update.message.reply_text(text, reply_markup=reply_markup)
        return

    @staticmethod
    async def show_manual_address_entry(
        update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """Show manual crypto address entry form"""
        query = update.callback_query
        # Add null safety for context data access
        if not context.user_data or "exchange_data" not in context.user_data:
            if query:
                await query.edit_message_text("❌ Session expired. Please restart.")
            return
        crypto = context.user_data["exchange_data"]["crypto"]

        # Remove duplicate check - already checked above
        exchange_data = context.user_data["exchange_data"]
        quote_text = f"""💱 Buy {exchange_data.get('amount')} {crypto} → ${exchange_data.get('rate_info', {}).get('final_usd_amount', 0):,.2f}
⏰ Quote valid {Config.RATE_LOCK_DURATION_MINUTES} minutes

Next: {crypto} wallet address"""

        {
            "BTC": "₿",
            "ETH": "Ξ",
            "LTC": "Ł",
            "DOGE": "Ð",
            "TRX": "🏛️",
            "XMR": "ⓧ",
            "USDT-TRC20": "💰",
            "USDT-ERC20": "💰",
        }.get(crypto, "💰")

        from utils.crypto_ui_components import CryptoUIComponents

        # Use standardized address input text
        address_text = CryptoUIComponents.generate_address_input_text(
            currency=crypto, action="Enter"
        )

        text = f"""{quote_text}

{address_text}"""

        # Use standardized navigation keyboard
        keyboard = [
            [
                InlineKeyboardButton(
                    "⬅️ Back to Addresses", callback_data="exchange_back_to_addresses"
                )
            ],
            [
                InlineKeyboardButton(
                    "⬅️ Back to Amount", callback_data="exchange_back_amount"
                )
            ],
        ]

        reply_markup = InlineKeyboardMarkup(keyboard)
        if query:
            await safe_edit_message_text(query, text, reply_markup=reply_markup)
        return

    @staticmethod
    async def use_specific_saved_bank(
        update: Update, context: ContextTypes.DEFAULT_TYPE, bank_id: int
    ) -> int:
        """Use a specific saved bank account"""
        query = update.callback_query

        if not update.effective_user or not update.effective_user.id:
            if query:
                await query.edit_message_text("❌ Authentication error.")
            return

        user_id = str(update.effective_user.id)
        
        # OPTIMIZATION: Use cached exchange data instead of database query
        cached_data = get_cached_exchange_data(context.user_data)
        if cached_data:
            # Use prefetched saved banks from cache (eliminates 2 queries)
            saved_banks = cached_data.get('saved_bank_accounts', [])
            saved_bank = next((bank for bank in saved_banks if bank.id == bank_id), None)
            user_db_id = cached_data['user_id']
        else:
            # Fallback to database query if cache miss
            async with async_managed_session() as session:
                stmt = select(User).where(User.telegram_id == user_id)
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()
                
                if not user:
                    if query:
                        await query.edit_message_text("❌ User not found.")
                    return
                user_db_id = user.id
                
                # Get saved banks
                saved_banks = await SavedDestinationCache.load_bank_accounts_optimized(int(user_id))
                saved_bank = next((bank for bank in saved_banks if bank['id'] == bank_id), None)

        if not saved_bank:
            if query:
                await query.edit_message_text("❌ Saved bank account not found.")
            return

        # Update last used timestamp in database
        async with async_managed_session() as session:
            saved_bank_stmt = select(SavedBankAccount).where(SavedBankAccount.id == bank_id)
            saved_bank_result = await session.execute(saved_bank_stmt)
            saved_bank_obj = saved_bank_result.scalar_one_or_none()
            if saved_bank_obj:
                saved_bank_obj.last_used = datetime.utcnow()
                await session.commit()
                # Invalidate caches after update
                SavedDestinationCache.invalidate_bank_accounts_cache(user_db_id)
                invalidate_exchange_cache(context.user_data)

            # Store bank details from saved account (handle both dict and dataclass)
            if isinstance(saved_bank, dict):
                bank_details = {
                    "bank_name": saved_bank['bank_name'],
                    "account_number": saved_bank['account_number'],
                    "account_name": saved_bank['account_name'],
                    "bank_code": saved_bank['bank_code'],
                }
            else:
                # Dataclass from prefetch cache
                bank_details = {
                    "bank_name": saved_bank.bank_name,
                    "account_number": saved_bank.account_number,
                    "account_name": saved_bank.account_name,
                    "bank_code": saved_bank.bank_code,
                }
            # Add null safety for context data access
            if context.user_data and "exchange_data" in context.user_data:
                context.user_data["exchange_data"]["bank_details"] = bank_details

            # Proceed to confirmation
            return await ExchangeHandler.confirm_exchange_order(update, context)

    @staticmethod
    async def handle_wallet_selection(
        update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """Handle crypto wallet address selection callbacks"""
        query = update.callback_query

        if query:
            # OPTIMIZED: Remove duplicate callback answer
            # Callback already answered in calling function
            
            if query.data and query.data.startswith("exchange_use_address:"):
                # Use specific saved crypto address
                address_id = int(query.data.split(":")[1])
                return await ExchangeHandler.use_saved_crypto_address(
                    update, context, address_id
                )
            elif query.data == "exchange_new_address":
                # Show manual address entry
                return await ExchangeHandler.show_manual_address_entry(
                    update, context
                )
            elif query.data == "exchange_back_to_addresses":
                # Go back to address selection with null safety
                if not context.user_data or "exchange_data" not in context.user_data:
                    if query:
                        await query.edit_message_text("❌ Session expired. Please restart.")
                    return
                exchange_data = context.user_data["exchange_data"]
                quote_text = f"""💰 Buy {exchange_data.get('amount')} {exchange_data.get('crypto')} → ${exchange_data.get('rate_info', {}).get('final_usd_amount', 0):,.2f}
⏰ Quote valid {Config.RATE_LOCK_DURATION_MINUTES} minutes

Next: {exchange_data.get('crypto')} wallet address"""
                return await ExchangeHandler.ask_wallet_address(
                    update, context, quote_text
                )
            elif query.data == "exchange_back_amount":
                return await ExchangeHandler.ask_amount(update, context)

        # Handle text input for new address
        if update.message and update.message.text:
            wallet_address = update.message.text.strip()
            # Add null safety for context data access
            if not context.user_data or "exchange_data" not in context.user_data:
                await update.message.reply_text("❌ Session expired. Please restart.")
                return
            crypto = context.user_data["exchange_data"]["crypto"]

            # Validate address format using the existing address detector
            from utils.address_detector import (
                detect_network_from_address,
                format_address_error,
            )

            detected_currency, is_valid = detect_network_from_address(wallet_address)

            if not is_valid or detected_currency != crypto:
                error_msg = format_address_error(wallet_address, detected_currency)
                await update.message.reply_text(error_msg)
                return

            # Store wallet address with null safety
            if context.user_data and "exchange_data" in context.user_data:
                context.user_data["exchange_data"]["wallet_address"] = wallet_address

            # Save address for future use
            await ExchangeHandler.save_new_crypto_address(
                update, context, wallet_address, crypto
            )

            # Proceed to confirmation
            return await ExchangeHandler.confirm_exchange_order(update, context)

        return

    @staticmethod
    async def use_saved_crypto_address(
        update: Update, context: ContextTypes.DEFAULT_TYPE, address_id: int
    ) -> int:
        """Use a specific saved crypto address"""
        query = update.callback_query

        if not update.effective_user:
            if query:
                await query.edit_message_text("❌ Authentication error.")
            return

        user_id = str(update.effective_user.id)
        crypto = context.user_data.get("exchange_data", {}).get("crypto", "BTC") if context.user_data else "BTC"
        
        # OPTIMIZATION: Use cached exchange data instead of database query
        cached_data = get_cached_exchange_data(context.user_data)
        if cached_data:
            # Use prefetched saved addresses from cache (eliminates 2 queries)
            saved_addresses = [addr for addr in cached_data.get('saved_crypto_addresses', []) if addr.currency == crypto]
            saved_address = next((addr for addr in saved_addresses if addr.id == address_id), None)
            user_db_id = cached_data['user_id']
        else:
            # Fallback to database query if cache miss
            async with async_managed_session() as session:
                stmt = select(User).where(User.telegram_id == user_id)
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()
                
                if not user:
                    if query:
                        await query.edit_message_text("❌ User not found.")
                    return
                user_db_id = user.id
                
                # Get saved addresses
                saved_addresses = await SavedDestinationCache.load_crypto_addresses_optimized(
                    int(user_id), currency=crypto
                )
                saved_address = next((addr for addr in saved_addresses if addr['id'] == address_id), None)

        if not saved_address:
            if query:
                await query.edit_message_text("❌ Saved address not found.")
            return

        # Update last used timestamp in database
        async with async_managed_session() as session:
            saved_address_stmt = select(SavedAddress).where(SavedAddress.id == address_id)
            saved_address_result = await session.execute(saved_address_stmt)
            saved_address_obj = saved_address_result.scalar_one_or_none()
            if saved_address_obj:
                saved_address_obj.last_used = datetime.utcnow()
                await session.commit()
                # Invalidate caches after update
                SavedDestinationCache.invalidate_crypto_addresses_cache(user_db_id)
                invalidate_exchange_cache(context.user_data)

        # Store wallet address with null safety (handle both dict and dataclass)
        if context.user_data and "exchange_data" in context.user_data:
            if isinstance(saved_address, dict):
                context.user_data["exchange_data"]["wallet_address"] = saved_address['address']
                display_label = saved_address['label']
                display_address = saved_address['address']
            else:
                # Dataclass from prefetch cache
                context.user_data["exchange_data"]["wallet_address"] = saved_address.address
                display_label = saved_address.label
                display_address = saved_address.address

        # Show confirmation message
        await safe_edit_message_text(
            query,
            f"✅ Using {display_label}: `{display_address[:10]}...{display_address[-8:]}`\n\n"
            f"Proceeding to order confirmation...",
        )

        # Proceed to confirmation
        return await ExchangeHandler.confirm_exchange_order(update, context)

    @staticmethod
    async def save_new_crypto_address(
        update: Update, context: ContextTypes.DEFAULT_TYPE, address: str, currency: str
    ) -> int:
        """Save a new crypto address for future use"""
        async with async_managed_session() as session:
            query = update.callback_query  # Fix undefined query variable
            if not update.effective_user:
                if query:
                    await query.edit_message_text("❌ Authentication error.")
                return

            user_id = str(update.effective_user.id)
            stmt = select(User).where(User.telegram_id == user_id)
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()

            if not user:
                return

            # Check if address already exists
            existing_stmt = select(SavedAddress).where(
                SavedAddress.user_id == user.id,
                SavedAddress.address == address,
                SavedAddress.currency == currency
            )
            existing_result = await session.execute(existing_stmt)
            existing = existing_result.scalar_one_or_none()

            if not existing:
                # Create new saved address
                from utils.address_detector import get_network_info

                currency_info = get_network_info(currency)

                saved_address = SavedAddress(
                    user_id=user.id,
                    address=address,
                    currency=currency,
                    network=currency_info["name"],
                    label=f"{currency} Wallet",
                    last_used=datetime.utcnow(),
                )
                session.add(saved_address)
                await session.commit()
                # Invalidate cache after saving new address
                SavedDestinationCache.invalidate_crypto_addresses_cache(user.id)
                logger.info(
                    f"Saved new {currency} address for user {user.id}: {address[:10]}..."
                )
        
        return

    @staticmethod
    async def save_new_bank_account(
        update: Update, context: ContextTypes.DEFAULT_TYPE, bank_details: dict
    ) -> int:
        """Save a new bank account for future use"""
        query = update.callback_query  # Define query variable
        async with async_managed_session() as session:
            if not update.effective_user:
                if query:
                    await query.edit_message_text("❌ Authentication error.")
                return

            user_id = str(update.effective_user.id)
            stmt = select(User).where(User.telegram_id == user_id)
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()

            if not user:
                return

            # Check if bank account already exists
            existing_stmt = select(SavedBankAccount).where(
                SavedBankAccount.user_id == user.id,
                SavedBankAccount.account_number == bank_details["account_number"],
                SavedBankAccount.bank_code == bank_details.get("bank_code", ""),
            )
            existing_result = await session.execute(existing_stmt)
            existing = existing_result.scalar_one_or_none()

            if not existing:
                # Create new saved bank account
                saved_bank = SavedBankAccount(
                    user_id=user.id,
                    account_number=bank_details["account_number"],
                    bank_code=bank_details.get("bank_code", ""),
                    bank_name=bank_details["bank_name"],
                    account_name=bank_details["account_name"],
                    last_used=datetime.utcnow(),
                )
                session.add(saved_bank)
                await session.commit()
                # Invalidate cache after saving new bank account
                SavedDestinationCache.invalidate_bank_accounts_cache(user.id)
                logger.info(
                    f"Saved new bank account for user {user.id}: {bank_details['bank_name']} {bank_details['account_number'][-4:]}"
                )
        
        return

    @staticmethod
    async def handle_bank_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle bank account selection callbacks"""
        query = update.callback_query
        if query:
            # IMMEDIATE FEEDBACK: Show specific action feedback
            if query.data and query.data.startswith("exchange_use_bank:"):
                await safe_answer_callback_query(query, "🏦 Bank selected")
            elif query.data == "exchange_different_bank":
                await safe_answer_callback_query(query, "🆕 Adding new bank")
            else:
                # OPTIMIZED: Remove duplicate callback answer
                pass  # Callback already answered in calling function

        if query and query.data == "exchange_use_saved_bank":
            # Use legacy saved bank account
            return await ExchangeHandler.use_saved_bank_account(update, context)
        elif query and query.data and query.data.startswith("exchange_use_bank:"):
            # Use specific saved bank account
            bank_id = int(query.data.split(":")[1])
            return await ExchangeHandler.use_specific_saved_bank(
                update, context, bank_id
            )
        elif query and query.data == "exchange_different_bank":
            # Start bank verification for new account
            exchange_data = context.user_data.get("exchange_data", {}) if context.user_data else {}
            quote_text = f"""💱 Sell {exchange_data.get('amount')} {exchange_data.get('crypto')} → ₦{exchange_data.get('rate_info', {}).get('final_ngn_amount', 0):,.0f}
⏰ Quote valid {Config.RATE_LOCK_DURATION_MINUTES} minutes

Next: Bank account details"""
            return await ExchangeHandler.start_bank_verification_from_callback(
                update, context, quote_text
            )

        return

    @staticmethod
    async def use_saved_bank_account(
        update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """Use the user's saved bank account"""
        query = update.callback_query

        async with async_managed_session() as session:
            try:
                # FINANCIAL CRITICAL: Strict user authentication for bank operations
                if not update.effective_user:
                    logger.error("SECURITY ALERT: Bank account access attempted without authenticated user")
                    if query:
                        await query.edit_message_text("❌ ⚠️ SECURITY ERROR: Authentication required for financial operations.")
                    raise SecurityError("Bank account access attempted without authenticated user")

                user_id = str(update.effective_user.id)
                stmt = select(User).where(User.telegram_id == user_id)
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()

                # Get user's saved bank account using SavedBankAccount model
                from models import SavedBankAccount

                saved_bank_stmt = select(SavedBankAccount).where(
                    SavedBankAccount.user_id == (user.id if user else 0)
                )
                saved_bank_result = await session.execute(saved_bank_stmt)
                saved_bank = saved_bank_result.scalar_one_or_none()

                # FINANCIAL CRITICAL: Validate user and bank account existence for money operations
                if not user:
                    logger.error(f"SECURITY ALERT: User not found in database for bank operation - Telegram ID: {user_id}")
                    if query:
                        await query.edit_message_text("❌ ⚠️ SECURITY ERROR: User verification failed.")
                    raise SecurityError(f"User not found for bank operation: {user_id}")
                
                if not saved_bank:
                    logger.warning(f"Bank account not found for user {user_id} - legitimate scenario")
                    if query:
                        await query.edit_message_text("❌ Saved bank account not found. Please add a bank account first.")
                    return

                # Store bank details from saved account
                bank_details = {
                    "bank_name": saved_bank.bank_name,
                    "account_number": saved_bank.account_number,
                    "account_name": saved_bank.account_name,
                    "bank_code": saved_bank.bank_code,
                }
                
                # FINANCIAL CRITICAL: Strict validation for bank details storage
                if not context.user_data or "exchange_data" not in context.user_data:
                    logger.error(f"SECURITY ALERT: Bank details storage attempted without valid session for user {user_id}")
                    raise SecurityError("Bank details storage attempted without valid financial context")
                
                # Validate bank details before storage
                required_fields = ["bank_name", "account_number", "account_name", "bank_code"]
                missing_fields = [field for field in required_fields if not str(bank_details.get(field, "")).strip()]
                
                if missing_fields:
                    logger.error(f"SECURITY ALERT: Incomplete bank details for user {user_id}: {missing_fields}")
                    raise ValueError(f"Incomplete bank details: {missing_fields}")
                
                context.user_data["exchange_data"]["bank_details"] = bank_details
                logger.info(f"SECURITY: Bank details stored for user {user_id}: {bank_details['bank_name']} ending {bank_details['account_number'][-4:]}")

                # Proceed to confirmation
                return await ExchangeHandler.confirm_exchange_order(update, context)

            except (SecurityError, ValueError) as e:
                # Financial security errors - log and terminate
                user_id = update.effective_user.id if update.effective_user else "unknown"
                logger.error(f"FINANCIAL SECURITY ERROR in bank account operation for user {user_id}: {e}")
                if query:
                    await query.edit_message_text("❌ ⚠️ Security Error: Bank operation terminated for your protection.")
                return
            except Exception as e:
                # Unexpected errors in bank operations
                user_id = update.effective_user.id if update.effective_user else "unknown"
                logger.error(f"CRITICAL: Unexpected error in bank operation for user {user_id}: {e}")
                if query:
                    await query.edit_message_text("❌ Critical system error. Please contact support if this persists.")
                return

    @staticmethod
    async def start_bank_verification_from_callback(
        update: Update, context: ContextTypes.DEFAULT_TYPE, quote_text: str
    ) -> int:
        """Start bank verification from callback query"""
        query = update.callback_query

        text = f"""{quote_text}

🏦 Enter your 10-digit bank account number:
(We'll auto-detect your bank and verify your details)

Example: 0123456789"""

        keyboard = [
            [
                InlineKeyboardButton(
                    "⬅️ Back to Amount", callback_data="exchange_back_amount"
                )
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        if query:
            await query.edit_message_text(text, reply_markup=reply_markup)

        # Set context for bank verification with null safety
        if context.user_data:
            context.user_data["exchange_bank_mode"] = "verification"
        return

    @staticmethod
    async def process_bank_verification(
        update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """Process bank account number OR bank selection using NEW CONCURRENT VERIFICATION"""
        message = update.message
        if not message or not message.text:
            if message:
                await message.reply_text("❌ Please provide input.")
            return

        input_text = message.text.strip()

        # Check if user is in bank selection mode (has pending matches)
        if (
            context.user_data
            and context.user_data.get("bank_matches")
            and context.user_data.get("exchange_bank_mode") == "selecting"
        ):
            # Handle bank selection (1, 2, 3...)
            return await ExchangeHandler.handle_bank_selection_text(
                update, context
            )

        # Otherwise, treat as account number entry
        account_number = input_text.replace(" ", "")

        # Validate account number format
        if not account_number.isdigit() or len(account_number) != 10:
            await message.reply_text("❌ Please enter a valid 10-digit account number.")
            return

        # THREAD-SAFE LOCK: Use same unified lock system as wallet handler
        from utils.verification_lock import (
            get_verification_lock,
            is_verification_running,
        )

        user_id = update.effective_user.id if update.effective_user else None
        if not user_id:
            await message.reply_text("❌ Authentication error.")
            return

        # Check if verification is already running (unified check)
        if await is_verification_running(user_id):
            logger.info(
                f"🔒 EXCHANGE BLOCKED: Verification already running for user {user_id}"
            )
            await message.reply_text(
                "⚠️ Bank verification already in progress. Please wait..."
            )
            return

        # Acquire the unified lock for this user
        lock = get_verification_lock(user_id)

        # Show verification loading
        loading_msg = await message.reply_text("🔍 Verifying account...")

        try:
            # Use Fincra bank verification service for direct exchange
            async with lock:
                from services.fincra_service import FincraService
                fincra_service = FincraService()

                # TOP 20 NIGERIAN BANKS: Same as wallet handler for consistency
                # Order: Digital banks first (higher success rates), then major traditional banks by assets
                major_banks = [
                    # === DIGITAL BANKS & FINTECHS (Higher Success Rate) ===
                    {"code": "090405", "name": "Moniepoint MFB"},  # #1 Digital bank
                    {"code": "100004", "name": "OPay Digital Bank"},  # $2B valuation super app
                    {"code": "100002", "name": "PalmPay"},  # 30M+ users Mobile Money Operator
                    {"code": "090267", "name": "Kuda Bank"},  # Leading digital bank, 5M+ users
                    {"code": "100012", "name": "Paga"},  # Established mobile money platform
                    {"code": "100022", "name": "VFD Microfinance Bank"},  # Growing digital platform
                    {"code": "090270", "name": "AB Microfinance Bank"},  # Popular digital services
                    
                    # === TIER 1 TRADITIONAL BANKS (By Assets & Market Share) ===
                    {"code": "044", "name": "Access Bank"},  # #1 by assets ₦32.57T
                    {"code": "033", "name": "United Bank For Africa"},  # #2 by assets ₦25.37T
                    {"code": "057", "name": "Zenith Bank"},  # #3 by assets ₦24.28T
                    {"code": "011", "name": "First Bank"},  # #4 by assets ₦16.90T
                    {"code": "058", "name": "Guaranty Trust Bank"},  # #5 by assets
                    
                    # === TIER 2 MAJOR BANKS (Significant Market Presence) ===
                    {"code": "070", "name": "Fidelity Bank"},  # Strong SME focus
                    {"code": "221", "name": "Stanbic IBTC Bank"},  # Investment banking leader
                    {"code": "214", "name": "First City Monument Bank"},  # Customer-focused
                    {"code": "032", "name": "Union Bank of Nigeria"},  # Century-old institution
                    {"code": "035", "name": "Wema Bank"},  # Significant growth
                    
                    # === SPECIALIZED & INTERNATIONAL BANKS ===
                    {"code": "050", "name": "Ecobank Nigeria"},  # Pan-African presence
                    {"code": "232", "name": "Sterling Bank"},  # Consistent growth
                    {"code": "030", "name": "Heritage Bank"},  # Regional strength
                    {"code": "082", "name": "Keystone Bank"},  # Solid market position
                    {"code": "068", "name": "Standard Chartered Bank"},  # International banking
                ]
                verified_account = None
                
                # Try to verify with major banks first
                for bank in major_banks:
                    try:
                        verified_name = await fincra_service.verify_account_name(
                            account_number=account_number, 
                            bank_code=bank.get('code', '')
                        )
                        if verified_name:
                            verified_account = {
                                'account_number': account_number,
                                'account_name': verified_name,
                                'bank_name': bank.get('name', 'Unknown Bank'),
                                'bank_code': bank.get('code', ''),
                                'status': 'verified'
                            }
                            break
                    except Exception as e:
                        logger.debug(f"Bank verification failed for {bank.get('name', 'Unknown')}: {e}")
                        continue

                # Simulate the old verification_result format
                if verified_account:
                    verification_result = type('VerificationResult', (), {
                        'status': 'single_match',
                        'data': verified_account
                    })()
                else:
                    verification_result = type('VerificationResult', (), {
                        'status': 'no_matches',
                        'data': None
                    })()

                if verification_result.status in ["service_unavailable", "invalid_format", "verification_in_progress"]:
                    pass  # Handle this case
                elif verification_result.status == "single_match":
                    match = verification_result.data
                    bank_code = match.get("bank_code", "")
                    bank_name = match.get("bank_name", "Unknown Bank")
                    account_name = match.get("account_name", "Unknown Account")

                    # Store bank details
                    bank_data = {
                        "account_number": account_number,
                        "bank_code": bank_code,
                        "bank_name": bank_name,
                        "account_name": account_name,
                    }

                    # Continue with single match confirmation
                    return await ExchangeHandler.confirm_bank_verification_new(
                        update, context, bank_data, loading_msg
                    )

                logger.info(
                    f"✨ DIRECT EXCHANGE VERIFICATION: Status {verification_result.status} for user {user_id}"
                )

                # Handle verification result (UNIFIED FORMAT)
                if verification_result.status == "single_match":
                    match = verification_result.data or {}
                    bank_code = match.get("bankCode", "")
                    bank_name = match.get("bankName", "Unknown Bank")
                    account_name = match.get("accountName", "Unknown Account")

                    # Store bank details
                    bank_data = {
                        "account_number": account_number,
                        "bank_code": bank_code,
                        "bank_name": bank_name,
                        "account_name": account_name,
                    }

                    # Continue with single match confirmation
                    return await ExchangeHandler.confirm_bank_verification_new(
                        update, context, bank_data, loading_msg
                    )

                elif verification_result.status == "multiple_matches":
                    # Multiple matches - let user choose with BUTTON interface
                    matches = verification_result.matches or []
                    # Store matches for button callbacks
                    if context.user_data:
                        context.user_data["bank_matches"] = matches
                    account_name = matches[0].get("account_name", "Account Holder") if matches else "Account Holder"
                    return await ExchangeHandler.show_smart_bank_selection(
                        update, context, matches, account_name, loading_msg
                    )

                elif verification_result.status == "no_matches":
                    await loading_msg.edit_text(
                        "❌ Account not found in any bank. Please check your account number and try again."
                    )
                    return

                elif verification_result.status in [
                    "service_unavailable",
                    "invalid_format",
                    "verification_in_progress",
                ]:
                    await loading_msg.edit_text(
                        "❌ Bank verification service temporarily unavailable. Please try again."
                    )
                    return
                else:
                    await loading_msg.edit_text(
                        "❌ Verification failed. Please try again."
                    )
                    return

        except Exception as e:
            logger.error(f"Error in bank verification: {e}")
            await loading_msg.edit_text("❌ Verification failed. Please try again.")
            return

    @staticmethod
    async def confirm_bank_verification_new(
        update: Update, context: ContextTypes.DEFAULT_TYPE, bank_data: dict, loading_msg
    ) -> int:
        """Handle single bank verification match with NEW concurrent system format"""
        try:
            await loading_msg.delete()
        except Exception:
            pass

        # Store bank details in exchange context
        exchange_data = context.user_data.get("exchange_data", {}) if context.user_data else {}

        # Extract bank details from new concurrent verification format
        bank_details = {
            "account_number": bank_data.get("account_number"),
            "bank_code": bank_data.get("bank_code"),
            "bank_name": bank_data.get("bank_name"),
            "account_name": bank_data.get("account_name"),
            "verified": True,
            # Track if multiple names were found during verification
            "multiple_names_found": bank_data.get("multiple_names_found", False),
        }

        # Store in exchange context with null safety
        if context.user_data is not None:
            exchange_data["bank_details"] = bank_details
            context.user_data["exchange_data"] = exchange_data

        # Verification handled by unified service - no global flags needed
        user_id = update.effective_user.id if update.effective_user else "unknown"
        logger.info(f"✅ Exchange bank verification completed for user {user_id}")

        # CRITICAL FIX: Always prompt to save bank details before confirming order
        return await ExchangeHandler.prompt_save_bank_details(update, context)

    @staticmethod
    async def show_bank_selection_new(
        update: Update, context: ContextTypes.DEFAULT_TYPE, matches: list, loading_msg
    ) -> int:
        """Handle multiple bank matches with NEW concurrent system format and TEXT INPUT support"""
        try:
            await loading_msg.delete()
        except Exception:
            pass

        if not matches:
            if update.message:
                await update.message.reply_text(
                    "❌ No bank matches found. Please try again."
                )
            return

        # Store matches for selection with null safety
        if context.user_data is not None:
            context.user_data["bank_matches"] = matches
            context.user_data["exchange_bank_mode"] = "selecting"

        # Build selection text with numbered options
        text = "🏦 Multiple Banks Found\n\n"
        text += "Please choose the correct bank:\n\n"

        for i, match in enumerate(matches, 1):
            account_name = match.get("account_name", "Unknown")
            bank_name = match.get("bank_name", "Unknown Bank")
            text += f"{i}. {bank_name}\n"
            text += f"   👤 {account_name}\n\n"

        text += "Type the number (1, 2, 3...) to select:"

        # Add back button
        keyboard = [
            [
                InlineKeyboardButton(
                    "⬅️ Back to Amount", callback_data="exchange_back_amount"
                )
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        if update.message:
            await update.message.reply_text(text, reply_markup=reply_markup)
        return

    @staticmethod
    async def handle_bank_selection_text(
        update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """Handle text input for bank selection (1, 2, 3, etc.)"""
        if not update.message or not update.message.text:
            return

        text = update.message.text.strip()
        matches = context.user_data.get("bank_matches", []) if context.user_data else []

        if not matches:
            if update.message:
                await update.message.reply_text(
                    "❌ No bank options available. Please start over."
                )
            return

        try:
            selection = int(text) - 1  # Convert to 0-based index
            if 0 <= selection < len(matches):
                # Valid selection
                selected_match = matches[selection]
                
                # Check if multiple different names were found
                unique_names = set(match.get("account_name", "Unknown") for match in matches)
                selected_match["multiple_names_found"] = len(unique_names) > 1

                # Clear the matches from context with null safety
                if context.user_data:
                    context.user_data.pop("bank_matches", None)

                # Process the selection
                return await ExchangeHandler.confirm_bank_verification_new(
                    update, context, selected_match, None
                )
            else:
                await update.message.reply_text(
                    f"❌ Please enter a number between 1 and {len(matches)}."
                )
                return

        except ValueError:
            await update.message.reply_text(
                "❌ Please enter a valid number (1, 2, 3...)."
            )
            return

    @staticmethod
    async def confirm_bank_verification(
        update: Update, context: ContextTypes.DEFAULT_TYPE, match, loading_msg
    ) -> int:
        """Confirm single bank match"""
        text = f"""✅ Account Verified!

👤 {match['account_name']}
🏦 {match['bank_name']}
💳 {match['account_number']}"""

        keyboard = [
            [
                InlineKeyboardButton(
                    "✅ Save & Continue",
                    callback_data=f"exchange_confirm_bank:{match['account_number']}:{match['bank_code']}:{match['bank_name']}:{match['account_name']}",
                )
            ],
            [
                InlineKeyboardButton(
                    "🔄 Try Different Number", callback_data="exchange_retry_bank"
                )
            ],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await loading_msg.edit_text(text, reply_markup=reply_markup)
        return

    @staticmethod
    async def show_smart_bank_selection(
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        matches,
        account_name,
        loading_msg,
    ) -> int:
        """Show Option 1 UX - Smart bank selection with priorities"""
        # Create bank selection UI inline (SmartBankUI not needed)
        if not matches:
            text = "❌ No bank matches found."
            reply_markup = InlineKeyboardMarkup([[]])
        else:
            # Check if there are different account names for the same account number
            unique_names = set(match.get("account_name", "Unknown") for match in matches)
            has_different_names = len(unique_names) > 1
            
            if has_different_names:
                text = "🏦 Multiple Account Names Found\n\n⚠️ The same account number returned different names. Please select the correct one:\n\n"
            else:
                text = f"🏦 Select Bank for {account_name}\n\n"
                
            keyboard = []
            for i, match in enumerate(matches[:5]):  # Limit to 5 options
                bank_name = match.get("bank_name", "Unknown Bank")
                match_account_name = match.get("account_name", "Unknown")
                
                if has_different_names:
                    # Show both bank name and account name when different names exist
                    button_text = f"{i+1}. {bank_name}\n👤 {match_account_name}"
                else:
                    # Show only bank name when names are consistent
                    button_text = f"{i+1}. {bank_name}"
                    
                keyboard.append(
                    [
                        InlineKeyboardButton(
                            button_text,
                            callback_data=f"exchange_select_bank:{i}",
                        )
                    ]
                )
            keyboard.append(
                [
                    InlineKeyboardButton(
                        "🔄 Try Different Account", callback_data="exchange_retry_bank"
                    )
                ]
            )
            reply_markup = InlineKeyboardMarkup(keyboard)

        await loading_msg.edit_text(text, parse_mode="HTML", reply_markup=reply_markup)
        return

    @staticmethod
    async def show_bank_matches(
        update: Update, context: ContextTypes.DEFAULT_TYPE, matches, loading_msg
    ) -> int:
        """Legacy method - replaced by show_smart_bank_selection"""
        # Redirect to smart selection for consistent UX
        account_name = (
            matches[0].get("account_name", "Unknown") if matches else "Unknown"
        )
        return await ExchangeHandler.show_smart_bank_selection(
            update, context, matches, account_name, loading_msg
        )

    @staticmethod
    async def handle_bank_verification_callback(
        update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """Handle bank verification callback actions"""
        query = update.callback_query

        if query and query.data and query.data.startswith("exchange_select_bank:"):
            # Handle bank selection button (format: exchange_select_bank:0, exchange_select_bank:1, etc.)
            await safe_answer_callback_query(query, "🏦 Processing bank selection...")
            
            try:
                bank_index = int(query.data.split(":")[1])
                # Get stored bank matches
                matches = context.user_data.get("bank_matches", []) if context.user_data else []
                
                if 0 <= bank_index < len(matches):
                    selected_match = matches[bank_index]
                    
                    # Check if multiple different names were found
                    unique_names = set(match.get("account_name", "Unknown") for match in matches)
                    multiple_names_found = len(unique_names) > 1
                    
                    # Store in context for further processing
                    if context.user_data and "exchange_data" not in context.user_data:
                        context.user_data["exchange_data"] = {}
                    
                    # REVERT: Use actual field names from bank verification response
                    bank_details = {
                        "account_number": selected_match.get("accountNumber", ""),  # Correct camelCase
                        "bank_code": selected_match.get("bankCode", ""),           # Correct camelCase
                        "bank_name": selected_match.get("bank_name", ""),
                        "account_name": selected_match.get("account_name", ""),
                        "multiple_names_found": multiple_names_found,
                    }
                    
                    if context.user_data:
                        context.user_data["exchange_data"]["bank_details"] = bank_details
                    
                    # Continue to save bank prompt
                    return await ExchangeHandler.prompt_save_bank_details(update, context)
                else:
                    await query.edit_message_text("❌ Invalid bank selection. Please try again.")
                    return
                    
            except (ValueError, IndexError):
                await query.edit_message_text("❌ Error processing bank selection. Please try again.")
                return
        
        elif (
            query
            and query.data
            and query.data.startswith("exchange_confirm_bank:")
        ):
            # Handle legacy confirm bank callback (detailed format)
            await safe_answer_callback_query(query, "✅ Confirming bank details...")
            
            parts = query.data.split(":", 1)[1].split(":")
            if len(parts) >= 4:
                account_number, bank_code, bank_name, account_name = (
                    parts[0],
                    parts[1],
                    parts[2],
                    ":".join(parts[3:]),
                )

                # Store bank details
                bank_details = {
                    "bank_name": bank_name,
                    "account_number": account_number,
                    "account_name": account_name,
                    "bank_code": bank_code,
                }
                # Add null safety for context data access
                if context.user_data and "exchange_data" in context.user_data:
                    context.user_data["exchange_data"]["bank_details"] = bank_details

                # NEW: Prompt user to save bank details for future use
                return await ExchangeHandler.prompt_save_bank_details(update, context)

        elif query and query.data == "exchange_retry_bank":
            # Go back to account number entry with null safety
            await safe_answer_callback_query(query, "🔄 Restarting bank verification...")
            
            exchange_data = context.user_data.get("exchange_data", {}) if context.user_data else {}
            quote_text = f"""💱 Sell {exchange_data.get('amount')} {exchange_data.get('crypto')} → ₦{exchange_data.get('rate_info', {}).get('final_ngn_amount', 0):,.0f}
⏰ Quote valid {Config.RATE_LOCK_DURATION_MINUTES} minutes

Next: Bank account details"""
            return await ExchangeHandler.start_bank_verification_from_callback(
                update, context, quote_text
            )

        return

    @staticmethod
    async def prompt_save_bank_details(
        update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """Prompt user to save bank details for future use"""
        if not context.user_data or "exchange_data" not in context.user_data:
            query = update.callback_query
            if query:
                await query.edit_message_text("❌ Session expired. Please restart.")
            return
        
        exchange_data = context.user_data["exchange_data"]
        bank_details = exchange_data.get("bank_details", {})
        bank_name = bank_details.get("bank_name", "Unknown Bank")
        account_number = bank_details.get("account_number", "")
        account_name = bank_details.get("account_name", "")
        
        # Show bank details preview with save options
        masked_account = f"***{account_number[-4:]}" if len(account_number) >= 4 else account_number
        text = f"""✅ Bank Account Verified

{bank_name}
Account: {masked_account}
Name: {account_name}

💾 Save this bank account for future use?
This will make your next exchanges faster and easier."""
        
        keyboard = [
            [
                InlineKeyboardButton(
                    "💾 Save & Continue", callback_data="exchange_save_bank"
                )
            ],
            [
                InlineKeyboardButton(
                    "➡️ Just Continue", callback_data="exchange_skip_bank_save"
                )
            ],
            [
                InlineKeyboardButton(
                    "✏️ Edit Bank Details", callback_data="exchange_retry_bank"
                )
            ]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # CRITICAL FIX: Always send the save prompt regardless of update type
        query = update.callback_query
        if query:
            await query.edit_message_text(
                text, parse_mode="HTML", reply_markup=reply_markup
            )
        elif update.message:
            await update.message.reply_text(
                text, parse_mode="HTML", reply_markup=reply_markup
            )
        else:
            # Fallback for edge cases
            await update.effective_chat.send_message(
                text, parse_mode="HTML", reply_markup=reply_markup
            )
        
        return
    
    @staticmethod
    async def handle_save_bank_prompt(
        update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """Handle save bank prompt response"""
        query = update.callback_query
        
        if not context.user_data or "exchange_data" not in context.user_data:
            if query:
                await query.edit_message_text("❌ Session expired. Please restart.")
            return
        
        if query and query.data == "exchange_save_bank":
            # Save bank details and continue
            exchange_data = context.user_data["exchange_data"]
            bank_details = exchange_data.get("bank_details", {})
            await ExchangeHandler.save_user_bank_details(update, bank_details)
            return await ExchangeHandler.confirm_exchange_order(update, context)
        
        elif query and query.data == "exchange_skip_bank_save":
            # Just continue without saving
            return await ExchangeHandler.confirm_exchange_order(update, context)
        
        elif query and query.data == "exchange_retry_bank":
            # Go back to bank entry - this callback is already handled in handle_bank_verification_callback
            exchange_data = context.user_data.get("exchange_data", {}) if context.user_data else {}
            quote_text = f"""💱 Sell {exchange_data.get('amount')} {exchange_data.get('crypto')} → ₦{exchange_data.get('rate_info', {}).get('final_ngn_amount', 0):,.0f}
⏰ Quote valid {Config.RATE_LOCK_DURATION_MINUTES} minutes

Next: Bank account details"""
            return await ExchangeHandler.start_bank_verification_from_callback(
                update, context, quote_text
            )
        
        return

    @staticmethod
    async def save_user_bank_details(update: Update, bank_details) -> bool:
        """Save bank details as user's default and to SavedBankAccount"""
        query = update.callback_query  # Define query variable
        async with async_managed_session() as session:
            if not update.effective_user:
                logger.error("No effective user for saving bank details")
                return False

            user_id = str(update.effective_user.id)
            stmt = select(User).where(User.telegram_id == user_id)
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()

            if user:
                # NOTE: User model doesn't have default bank fields, only save to SavedBankAccount table

                # Also save to SavedBankAccount table
                existing_stmt = select(SavedBankAccount).where(
                    SavedBankAccount.user_id == user.id,
                    SavedBankAccount.account_number == bank_details["account_number"],
                    SavedBankAccount.bank_code == bank_details.get("bank_code", ""),
                )
                existing_result = await session.execute(existing_stmt)
                existing = existing_result.scalar_one_or_none()

                if not existing:
                    saved_bank = SavedBankAccount(
                        user_id=user.id,
                        account_number=bank_details["account_number"],
                        bank_code=bank_details.get("bank_code", ""),
                        bank_name=bank_details["bank_name"],
                        account_name=bank_details["account_name"],
                        is_verified=True,
                        last_used=func.now(),
                    )
                    session.add(saved_bank)
                    logger.info(
                        f"Created new SavedBankAccount for user {user_id}: {bank_details['bank_name']}"
                    )
                else:
                    # Update existing record - fix SQLAlchemy column assignments using update query
                    from sqlalchemy import update as sqlalchemy_update

                    await session.execute(
                        sqlalchemy_update(SavedBankAccount)
                        .where(SavedBankAccount.id == existing.id)
                        .values(last_used=func.now())
                    )
                    # Note: is_default field doesn't exist in SavedBankAccount model
                    logger.info(
                        f"Updated existing SavedBankAccount for user {user_id}: {bank_details['bank_name']}"
                    )

                await session.commit()
                logger.info(
                    f"Saved bank details for user {user_id}: {bank_details['bank_name']} {bank_details['account_number'][-4:]}"
                )
                
                # Show success feedback
                if query:
                    await safe_answer_callback_query(query, f"✅ {bank_details['bank_name']} account saved!")
                
                return True
        
        return False

    @staticmethod
    async def process_wallet_address(
        update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """Process crypto wallet address"""
        message = update.message
        if not message or not message.text:
            if message:
                await message.reply_text("❌ Please provide your wallet address.")
            return

        wallet_address = message.text.strip()

        # Basic validation
        if len(wallet_address) < 20:
            if message:
                await message.reply_text(
                    "❌ That address seems too short.\n\nPlease enter your complete wallet address (usually 25-45 characters long)."
                )
            return

        # FINANCIAL CRITICAL: Strict validation for wallet address storage
        if not context.user_data or "exchange_data" not in context.user_data:
            logger.error(f"SECURITY: Wallet address storage attempted without valid session for user {update.effective_user.id if update.effective_user else 'unknown'}")
            if message:
                await message.reply_text("❌ ⚠️ Security Error: Invalid session for financial operation. Please restart.")
            raise ValueError("Financial operation attempted without valid context")
        
        context.user_data["exchange_data"]["wallet_address"] = wallet_address
        logger.info(f"SECURITY: Wallet address stored for user {update.effective_user.id if update.effective_user else 'unknown'}: {wallet_address[:10]}...{wallet_address[-6:]}")

        # NEW: Prompt user to save address for future use
        return await ExchangeHandler.prompt_save_address(update, context)

    @staticmethod
    async def prompt_save_address(
        update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """Prompt user to save wallet address for future use"""
        if not context.user_data or "exchange_data" not in context.user_data:
            if update.message:
                await update.message.reply_text("❌ Session expired. Please restart.")
            return
        
        exchange_data = context.user_data["exchange_data"]
        crypto = exchange_data.get("crypto", "crypto")
        wallet_address = exchange_data.get("wallet_address", "")
        
        # Show address preview with save options
        text = f"""✅ Address Verified

{crypto} Address:
<code>{wallet_address}</code>

💾 Save this address for future use?
This will make your next exchanges faster and easier."""
        
        keyboard = [
            [
                InlineKeyboardButton(
                    "💾 Save & Continue", callback_data="exchange_save_address"
                )
            ],
            [
                InlineKeyboardButton(
                    "➡️ Just Continue", callback_data="exchange_skip_save"
                )
            ],
            [
                InlineKeyboardButton(
                    "✏️ Edit Address", callback_data="exchange_new_address"
                )
            ]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if update.message:
            await update.message.reply_text(
                text, parse_mode="HTML", reply_markup=reply_markup
            )
        
        return
    
    @staticmethod
    async def handle_save_address_prompt(
        update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """Handle save address prompt response"""
        query = update.callback_query
        
        if not context.user_data or "exchange_data" not in context.user_data:
            if query:
                await query.edit_message_text("❌ Session expired. Please restart.")
            return
        
        if query and query.data == "exchange_save_address":
            # Save address and continue
            await ExchangeHandler.save_wallet_address(update, context)
            return await ExchangeHandler.confirm_exchange_order(update, context)
        
        elif query and query.data == "exchange_skip_save":
            # Just continue without saving
            return await ExchangeHandler.confirm_exchange_order(update, context)
        
        elif query and query.data == "exchange_new_address":
            # Go back to address entry
            return await ExchangeHandler.show_manual_address_entry(update, context)
        
        return
    
    @staticmethod
    async def save_wallet_address(
        update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> bool:
        """Save wallet address to user's saved addresses"""
        try:
            if not update.effective_user:
                logger.error("No effective user for saving address")
                return False
            
            if not context.user_data or "exchange_data" not in context.user_data:
                logger.error("No exchange data for saving address")
                return False
            
            exchange_data = context.user_data["exchange_data"]
            crypto = exchange_data.get("crypto")
            wallet_address = exchange_data.get("wallet_address")
            
            if not crypto or not wallet_address:
                logger.error(f"Missing crypto ({crypto}) or address ({wallet_address})")
                return False
            
            async with async_managed_session() as session:
                # Get user
                stmt = select(User).where(User.telegram_id == normalize_telegram_id(update.effective_user.id))
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()
                
                if not user:
                    logger.error(f"User not found for telegram_id {update.effective_user.id}")
                    return False
                
                # Check if address already exists
                from models import SavedAddress
                stmt = select(SavedAddress).where(
                    SavedAddress.user_id == user.id,
                    SavedAddress.currency == crypto,
                    SavedAddress.address == wallet_address
                )
                result = await session.execute(stmt)
                existing = result.scalar_one_or_none()
                
                if existing:
                    # Update last used timestamp
                    existing.last_used = datetime.utcnow()
                    await session.commit()
                    logger.info(f"Updated existing {crypto} address for user {user.id}")
                else:
                    # Create new saved address
                    saved_address = SavedAddress(
                        user_id=user.id,
                        currency=crypto,
                        network=crypto,  # For now, use crypto as network
                        address=wallet_address,
                        label=f"My {crypto} Wallet",
                        last_used=datetime.utcnow()
                    )
                    session.add(saved_address)
                    await session.commit()
                    logger.info(f"Saved new {crypto} address for user {user.id}")
                
                # Show success feedback
                query = update.callback_query
                if query:
                    await safe_answer_callback_query(query, f"✅ {crypto} address saved!")
                
                return True
                
        except Exception as e:
            logger.error(f"Error saving wallet address: {e}")
            return False
    
    @staticmethod
    async def confirm_exchange_order(
        update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """Show final confirmation for exchange order"""
        # FINANCIAL CRITICAL: Strict validation for exchange order confirmation
        if not context.user_data or "exchange_data" not in context.user_data:
            user_id = update.effective_user.id if update.effective_user else "unknown"
            logger.error(f"SECURITY ALERT: Exchange order confirmation attempted without valid session - User: {user_id}")
            
            query = update.callback_query
            if query:
                await query.edit_message_text(
                    "❌ ⚠️ SECURITY ERROR\n\nFinancial operation attempted without valid session. For your security, this transaction has been terminated.",
                    reply_markup=InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    "🔄 Start New Exchange",
                                    callback_data="direct_exchange",
                                )
                            ]
                        ]
                    ),
                )
            elif update.message:
                await update.message.reply_text(
                    "❌ ⚠️ SECURITY ERROR\n\nFinancial operation attempted without valid session.",
                )
            raise SecurityError(f"Exchange confirmation attempted without valid context for user {user_id}")

        # FINANCIAL CRITICAL: Validate all required exchange data
        exchange_data = context.user_data["exchange_data"]
        
        # Strict validation of critical financial data
        required_fields = ["crypto", "type", "rate_info", "amount"]
        missing_fields = [field for field in required_fields if not exchange_data.get(field)]
        
        if missing_fields:
            user_id = update.effective_user.id if update.effective_user else "unknown"
            logger.error(f"SECURITY ALERT: Missing critical financial data for user {user_id}: {missing_fields}")
            raise ValueError(f"Critical financial data missing: {missing_fields}")
        
        crypto = exchange_data["crypto"]
        exchange_type = exchange_data["type"]
        rate_info = exchange_data["rate_info"]
        
        # Validate financial amounts
        if not isinstance(exchange_data.get("amount"), (int, float)) or exchange_data["amount"] <= 0:
            logger.error(f"SECURITY ALERT: Invalid amount for user {update.effective_user.id if update.effective_user else 'unknown'}: {exchange_data.get('amount')}")
            raise ValueError(f"Invalid financial amount: {exchange_data.get('amount')}")

        if exchange_type == "crypto_to_ngn":
            # FINANCIAL CRITICAL: Validate bank details for money transfer
            if "bank_details" not in exchange_data or not exchange_data["bank_details"]:
                user_id = update.effective_user.id if update.effective_user else "unknown"
                logger.error(f"SECURITY ALERT: Missing bank details for crypto-to-NGN exchange - User: {user_id}")
                raise ValueError("Bank details required for crypto-to-NGN exchange")
            
            bank_details = exchange_data["bank_details"]
            
            # Validate required bank fields
            required_bank_fields = ["account_number", "bank_code", "account_name", "bank_name"]
            missing_bank_fields = [field for field in required_bank_fields if not bank_details.get(field)]
            
            if missing_bank_fields:
                user_id = update.effective_user.id if update.effective_user else "unknown"
                logger.error(f"SECURITY ALERT: Incomplete bank details for user {user_id}: {missing_bank_fields}")
                raise ValueError(f"Incomplete bank details: {missing_bank_fields}")

            # Mask account number to show first and last 4 digits
            account_number = bank_details.get("account_number", "N/A")
            if account_number != "N/A" and len(account_number) >= 8:
                masked_account = f"{account_number[:4]}••••{account_number[-4:]}"
            elif account_number != "N/A" and len(account_number) >= 4:
                masked_account = f"••••{account_number[-4:]}"
            else:
                masked_account = account_number

            # Format crypto amount properly to avoid scientific notation
            from utils.decimal_precision import MonetaryDecimal
            formatted_crypto_amount = MonetaryDecimal.quantize_crypto(exchange_data['amount'])
            
            # Check if multiple names were found during verification
            multiple_names_warning = ""
            if bank_details.get('multiple_names_found', False):
                multiple_names_warning = "\n⚠️ Multiple names found - verify before proceeding"
            
            text = f"""✅ Review Exchange

📤 {formatted_crypto_amount} {crypto}
📥 ₦{rate_info['final_ngn_amount']:,.2f}

🏦 {bank_details.get('bank_name', 'N/A')}
`{masked_account}`
{bank_details.get('account_name', 'N/A')}{multiple_names_warning}

⏰ Rate locked {Config.CRYPTO_EXCHANGE_RATE_LOCK_MINUTES}min"""

        else:  # ngn_to_crypto
            # FINANCIAL CRITICAL: Validate wallet address for crypto transfer
            if "wallet_address" not in exchange_data or not exchange_data["wallet_address"]:
                user_id = update.effective_user.id if update.effective_user else "unknown"
                logger.error(f"SECURITY ALERT: Missing wallet address for NGN-to-crypto exchange - User: {user_id}")
                raise ValueError("Wallet address required for NGN-to-crypto exchange")
            
            wallet_address = exchange_data["wallet_address"]
            
            # Validate wallet address format
            if len(wallet_address) < 20 or len(wallet_address) > 100:
                user_id = update.effective_user.id if update.effective_user else "unknown"
                logger.error(f"SECURITY ALERT: Invalid wallet address length for user {user_id}: {len(wallet_address)} chars")
                raise ValueError(f"Invalid wallet address format: {len(wallet_address)} characters")
            # Format crypto amount properly to avoid scientific notation
            from utils.decimal_precision import MonetaryDecimal
            formatted_crypto_amount = MonetaryDecimal.quantize_crypto(rate_info['crypto_amount'])
            
            text = f"""✅ Review Exchange

📤 ₦{exchange_data['amount']:,.2f}
📥 {formatted_crypto_amount} {crypto}

🔐 Wallet Address:
`{wallet_address}`

⏰ Rate locked {Config.CRYPTO_EXCHANGE_RATE_LOCK_MINUTES}min"""

        # Removed address checking - no switching allowed after confirmation

        # PHASE 1: Enhanced UX - Add switching options before confirmation
        keyboard = []
        
        # Check if user has multiple bank accounts for smart button display
        user_has_multiple_banks = False
        user = None
        
        try:
            from models import User, SavedBankAccount
            
            async with async_managed_session() as session:
                stmt = select(User).where(User.telegram_id == int(update.effective_user.id if update.effective_user else 0))
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()
                
                if user and exchange_type == "crypto_to_ngn":
                    # Get bank account count and current bank ID
                    stmt = select(SavedBankAccount).where(SavedBankAccount.user_id == user.id)
                    result = await session.execute(stmt)
                    saved_banks = result.scalars().all()
                    user_has_multiple_banks = len(saved_banks) > 1
                    
                    # Extract current bank ID from exchange data if available
                    bank_details = exchange_data.get("bank_details", {})
                    if isinstance(bank_details, dict) and "bank_account_id" in bank_details:
                        bank_details["bank_account_id"]
                    
        except Exception as e:
            logger.error(f"Error checking user banks: {e}")
        
        # SIMPLIFIED UX: Only show switching options for sell crypto (crypto_to_ngn)
        # Remove switching complexity from buy crypto (ngn_to_crypto) for streamlined experience
        switch_row = []
        
        if exchange_type == "crypto_to_ngn":
            # Crypto to NGN: Show crypto and bank switching options
            switch_row.append(InlineKeyboardButton("🔄 Switch Crypto", callback_data="exchange_crypto_switch_pre"))
            
            if user_has_multiple_banks:
                switch_row.append(InlineKeyboardButton("🔄 Switch Bank", callback_data="exchange_bank_switch_pre"))
            else:
                switch_row.append(InlineKeyboardButton("🏦 Change Bank", callback_data="exchange_add_bank_pre"))
        # else: NGN to Crypto - No switching options for streamlined buy experience
        
        # Show switching options only for sell crypto
        if switch_row:
            keyboard.append(switch_row)
        
        # Add confirm order button
        keyboard.append([
            InlineKeyboardButton("✅ Confirm Order", callback_data="exchange_confirm_order")
        ])
        
        # Add cancel button  
        keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="exchange_cancel")])
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Handle both callback query and message updates
        if update.callback_query:
            await safe_edit_message_text(
                update.callback_query,
                text,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
        else:
            if update.message:
                await update.message.reply_text(text, reply_markup=reply_markup)
        return

    @staticmethod
    async def handle_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle order confirmation"""
        query = update.callback_query
        if query:
            # OPTIMIZED: Remove duplicate callback answer
            pass  # Callback already answered in calling function

        if query and query.data == "exchange_confirm_order":
            # CRITICAL FIX: Prevent duplicate processing with proper callback handling
            await safe_answer_callback_query(query, "🔄 Processing order...")
            
            # SAFETY: Check if order already being processed (prevent duplicate clicks)
            if hasattr(context, 'user_data') and context.user_data.get('processing_order'):
                logger.warning(f"Duplicate order confirmation attempt from user {update.effective_user.id}")
                return  # Stay in same state
            
            # Mark as processing
            if context.user_data:
                context.user_data['processing_order'] = True
            
            try:
                result = await ExchangeHandler.create_exchange_order(update, context)
                return result
            finally:
                # Clear processing flag
                if context.user_data:
                    context.user_data.pop('processing_order', None)
        elif query and query.data == "exchange_crypto_switch_pre":
            # PHASE 2: Pre-confirmation crypto switching
            return await ExchangeHandler.handle_pre_confirmation_crypto_switch(update, context)
        elif query and query.data == "exchange_bank_switch_pre":
            # PHASE 2: Pre-confirmation bank switching
            return await ExchangeHandler.handle_pre_confirmation_bank_switch(update, context)
        elif query and query.data == "exchange_add_bank_pre":
            # PHASE 2: Pre-confirmation add bank
            return await ExchangeHandler.handle_pre_confirmation_add_bank(update, context)
        elif query and query.data == "exchange_back_to_confirmation":
            # CONTEXT RECOVERY: Restore exchange context for confirmation
            if not context.user_data or "exchange_data" not in context.user_data:
                logger.warning("Recovering exchange context for back navigation")
                # Initialize basic exchange context to prevent security error
                context.user_data["exchange_data"] = {
                    "type": "crypto_to_ngn",  # Default to most common case
                    "crypto": "BTC",  # Will be updated if user has active session
                    "amount": "0.01",  # Minimal amount
                }
                context.user_data["exchange_state"] = "confirming_order"
            
            # Back to confirmation screen
            return await ExchangeHandler.confirm_exchange_order(update, context)
        elif query and query.data and query.data.startswith("exchange_pre_crypto:"):
            # Handle pre-confirmation crypto selection
            return await ExchangeHandler.handle_pre_confirmation_crypto_selection(update, context)
        elif query and query.data and query.data.startswith("exchange_pre_bank:"):
            # Handle pre-confirmation bank selection
            return await ExchangeHandler.handle_pre_confirmation_bank_selection(update, context)
        elif query and query.data == "exchange_pre_add_bank":
            # Handle pre-confirmation add bank
            return await ExchangeHandler.handle_pre_confirmation_add_bank(update, context)
        elif query and query.data == "exchange_wallet_switch_pre":
            # PHASE 2: Pre-confirmation wallet switching (NGN to crypto)
            return await ExchangeHandler.handle_pre_confirmation_wallet_switch(update, context)
        elif query and query.data == "exchange_add_wallet_pre":
            # PHASE 2: Pre-confirmation add wallet (NGN to crypto)
            return await ExchangeHandler.handle_pre_confirmation_add_wallet(update, context)
        elif query and query.data and query.data.startswith("exchange_pre_wallet:"):
            # Handle pre-confirmation wallet selection
            return await ExchangeHandler.handle_pre_confirmation_wallet_selection(update, context)
        elif query and query.data == "exchange_pre_add_wallet":
            # Handle pre-confirmation add wallet
            return await ExchangeHandler.handle_pre_confirmation_add_wallet(update, context)
        elif query and query.data == "exchange_edit_details":
            # Clear current data and restart the exchange flow to allow editing
            if context.user_data and "exchange_data" in context.user_data:
                context.user_data["exchange_data"].clear()
            return await ExchangeHandler.start_exchange(update, context)
        elif query and query.data == "exchange_cancel":
            # Simple cancellation handling
            logger.info("Exchange cancellation requested by user")
            
            # Clear exchange data
            if context.user_data and "exchange_data" in context.user_data:
                context.user_data.pop("exchange_data", None)
                logger.info("Cleared exchange data on cancellation")
            
            # Show cancellation confirmation
            message = "❌ Exchange cancelled.\n\nReturning to main menu..."
            
            if query:
                await query.edit_message_text(message)
            
            # Navigate to main menu
            from handlers.start import show_main_menu
            await show_main_menu(update, context)
            return

        return

    @staticmethod
    async def handle_pre_confirmation_crypto_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle crypto selection from pre-confirmation screen"""
        query = update.callback_query
        if not query or not query.data:
            return
            
        if query.data.startswith("exchange_pre_crypto:"):
            new_crypto = query.data.split(":")[1]
            await safe_answer_callback_query(query, f"✅ {new_crypto} selected")
            
            # Update exchange data with new crypto
            if context.user_data and "exchange_data" in context.user_data:
                old_crypto = context.user_data["exchange_data"].get("crypto", "")
                context.user_data["exchange_data"]["crypto"] = new_crypto
                
                # Re-calculate rates and amounts for new crypto
                exchange_data = context.user_data["exchange_data"]
                old_crypto_amount = exchange_data.get("amount", 0)
                exchange_type = exchange_data.get("type", "crypto_to_ngn")
                
                try:
                    from services.financial_gateway import financial_gateway
                    from decimal import Decimal, ROUND_HALF_UP
                    
                    if exchange_type == "crypto_to_ngn":
                        # CRITICAL FIX: Preserve USD value when switching cryptocurrencies
                        
                        # Step 1: Get original crypto USD rate to calculate current USD value
                        old_crypto_usd_rate = exchange_data.get("rate_info", {}).get("crypto_usd_rate")
                        if not old_crypto_usd_rate:
                            # Fallback: get current rate for old crypto
                            old_crypto_usd_rate = await financial_gateway.get_crypto_to_usd_rate(old_crypto)
                        
                        if not old_crypto_usd_rate:
                            raise ValueError(f"Cannot get USD rate for original crypto {old_crypto}")
                        
                        # Step 2: Calculate USD value of original crypto amount
                        usd_value = Decimal(str(old_crypto_amount)) * Decimal(str(old_crypto_usd_rate))
                        
                        # Step 3: Get new crypto rate and calculate equivalent amount
                        new_crypto_usd_rate = await financial_gateway.get_crypto_to_usd_rate(new_crypto)
                        
                        # OPTIMIZATION: Use cached USD-NGN rate to prevent duplicate API call
                        import time
                        cached_usd_ngn = context.user_data.get('cached_exchange_usd_ngn_rate')
                        if cached_usd_ngn and (time.time() - cached_usd_ngn.get('fetched_at', 0)) < 300:  # 5 min cache
                            clean_usd_ngn_rate = cached_usd_ngn['rate']
                            logger.info("🚀 Crypto switch: Using cached USD-NGN rate to prevent duplicate API call")
                        else:
                            clean_usd_ngn_rate = await financial_gateway.get_usd_to_ngn_rate_clean()
                            if clean_usd_ngn_rate:
                                context.user_data['cached_exchange_usd_ngn_rate'] = {
                                    'rate': clean_usd_ngn_rate,
                                    'fetched_at': time.time()
                                }
                                logger.info("🔄 Crypto switch: Cached fresh USD-NGN rate")
                        
                        if new_crypto_usd_rate and clean_usd_ngn_rate:
                            # Step 4: Calculate equivalent crypto amount that maintains USD value
                            new_crypto_amount = usd_value / Decimal(str(new_crypto_usd_rate))
                            new_crypto_amount = new_crypto_amount.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)
                            
                            # Step 5: Calculate NGN amount with new crypto
                            market_rate_per_crypto = new_crypto_usd_rate * clean_usd_ngn_rate
                            markup_percentage = Decimal(str(Config.EXCHANGE_MARKUP_PERCENTAGE)) / Decimal("100")
                            markup_amount = market_rate_per_crypto * markup_percentage
                            display_rate = market_rate_per_crypto - markup_amount
                            final_ngn_amount = new_crypto_amount * display_rate
                            
                            # Step 6: Update amount to new equivalent crypto amount
                            exchange_data["amount"] = new_crypto_amount
                            
                            # Step 7: Update rate info with correct values
                            rate_info = exchange_data.get("rate_info", {})
                            rate_info.update({
                                "crypto_usd_rate": Decimal(str(new_crypto_usd_rate or 0)),
                                "ngn_usd_rate": Decimal(str(clean_usd_ngn_rate or 0)),
                                "final_ngn_amount": final_ngn_amount,
                                "effective_rate": display_rate
                            })
                            exchange_data["rate_info"] = rate_info
                            
                            logger.info(f"FIXED: Crypto switched from {old_crypto} ({old_crypto_amount}) to {new_crypto} ({decimal_to_string(new_crypto_amount, precision=8)}) - USD value preserved: {format_money(usd_value, 'USD')}")
                        
                except Exception as e:
                    logger.error(f"Error recalculating rates for new crypto {new_crypto}: {e}")
                    if query:
                        await query.edit_message_text(f"❌ Error updating rates for {new_crypto}. Please try again.")
                    return
            
            # Return to confirmation screen with updated crypto
            return await ExchangeHandler.confirm_exchange_order(update, context)
            
        return

    @staticmethod
    async def handle_pre_confirmation_bank_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle bank selection from pre-confirmation screen"""
        query = update.callback_query
        if not query or not query.data:
            return
            
        if query.data.startswith("exchange_pre_bank:"):
            bank_id_str = query.data.split(":")[1]
            
            # Convert bank_id to integer for consistency with other handlers
            try:
                bank_id = int(bank_id_str)
            except ValueError:
                logger.error(f"Invalid bank ID format in exchange_pre_bank: {bank_id_str}")
                if query:
                    await query.edit_message_text("❌ Invalid bank selection. Please try again.")
                return
            
            async with async_managed_session() as session:
                try:
                    from models import SavedBankAccount
                    stmt = select(SavedBankAccount).where(SavedBankAccount.id == bank_id)
                    result = await session.execute(stmt)
                    bank_account = result.scalar_one_or_none()
                    
                    if not bank_account:
                        if query:
                            await query.edit_message_text("❌ Bank account not found.")
                        return
                        
                    await safe_answer_callback_query(query, f"✅ {bank_account.bank_name} selected")
                    
                    # Update exchange data with new bank
                    if context.user_data and "exchange_data" in context.user_data:
                        context.user_data["exchange_data"]["bank_details"] = {
                            "bank_account_id": bank_account.id,
                            "bank_name": bank_account.bank_name,
                            "account_number": bank_account.account_number,
                            "account_name": bank_account.account_name
                        }
                        
                        logger.info(f"Bank switched to {bank_account.bank_name} for pre-confirmation")
                    
                    # Return to confirmation screen with updated bank
                    return await ExchangeHandler.confirm_exchange_order(update, context)
                    
                except Exception as e:
                    logger.error(f"Error selecting bank {bank_id}: {e}")
                    if query:
                        await query.edit_message_text("❌ Error selecting bank. Please try again.")
                    return
                
        return

    @staticmethod
    async def handle_pre_confirmation_wallet_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle wallet selection from pre-confirmation screen"""
        query = update.callback_query
        if not query or not query.data:
            return
            
        if query.data.startswith("exchange_pre_wallet:"):
            wallet_id = query.data.split(":")[1]
            
            async with async_managed_session() as session:
                try:
                    from models import SavedAddress
                    stmt = select(SavedAddress).where(SavedAddress.id == int(wallet_id))
                    result = await session.execute(stmt)
                    wallet_address = result.scalar_one_or_none()
                    
                    if not wallet_address:
                        if query:
                            await query.edit_message_text("❌ Wallet address not found.")
                        return
                        
                    await safe_answer_callback_query(query, f"✅ {wallet_address.currency} wallet selected")
                    
                    # Update exchange data with new wallet
                    if context.user_data and "exchange_data" in context.user_data:
                        context.user_data["exchange_data"]["wallet_address"] = wallet_address.address
                        context.user_data["exchange_data"]["saved_address_id"] = wallet_address.id
                        
                        logger.info(f"Wallet switched to {wallet_address.address[:12]}... for pre-confirmation")
                    
                    # Return to confirmation screen with updated wallet
                    return await ExchangeHandler.confirm_exchange_order(update, context)
                    
                except Exception as e:
                    logger.error(f"Error selecting wallet {wallet_id}: {e}")
                    if query:
                        await query.edit_message_text("❌ Error selecting wallet. Please try again.")
                    return
                
        return

    @staticmethod
    async def create_exchange_order(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Create the exchange order in database"""
        query = update.callback_query
        user = update.effective_user

        # INSTANT FEEDBACK: Show processing message immediately
        if query:
            await safe_answer_callback_query(query, "✅ Creating exchange...")
            
            from utils.callback_utils import safe_edit_message_text
            from telegram import InlineKeyboardMarkup, InlineKeyboardButton
            
            processing_message = (
                "⏳ **Creating Your Exchange...**\n\n"
                "Please wait while we:\n"
                "• Verify your account\n"
                "• Create transaction records\n"
                "• Set up exchange order\n\n"
                "This may take a few seconds..."
            )
            
            await safe_edit_message_text(
                query,
                processing_message,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([])  # Remove buttons during processing
            )
            
            logger.info("✅ Exchange UI immediately updated with processing message")

        try:
            async with async_managed_session() as session:
                if not user:
                    logger.error("No effective user found in create_exchange_order")
                    if query:
                        await query.edit_message_text("❌ User authentication error.")
                    return
                    
                stmt = select(User).where(User.telegram_id == normalize_telegram_id(user.id))
                result = await session.execute(stmt)
                db_user = result.scalar_one_or_none()

                if not db_user:
                    if query:
                        await query.edit_message_text(
                            "❌ User not found. Please restart with /start"
                        )
                    return

            # CONTEXT PERSISTENCE FIX: Enhanced session validation
            if not context.user_data or "exchange_data" not in context.user_data:
                logger.warning(f"Context data lost for user {user.id if user else 'unknown'} during order creation")
                if query:
                    await query.edit_message_text(
                        "❌ *Session Expired*\n\nYour exchange session has expired. Please start over:",
                        parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup(
                            [
                                [
                                    InlineKeyboardButton(
                                        "🔄 Start New Exchange",
                                        callback_data="direct_exchange",
                                    )
                                ]
                            ]
                        ),
                    )
                return
            exchange_data = context.user_data["exchange_data"]

            # Create exchange order
            order_data = {
                "source_currency": (
                    exchange_data["crypto"]
                    if exchange_data["type"] == "crypto_to_ngn"
                    else "NGN"
                ),
                "source_amount": exchange_data["amount"],
                "target_currency": (
                    "NGN"
                    if exchange_data["type"] == "crypto_to_ngn"
                    else exchange_data["crypto"]
                ),
                "rate_info": exchange_data["rate_info"],
            }

            if exchange_data["type"] == "crypto_to_ngn":
                order_data["bank_account"] = json.dumps(exchange_data["bank_details"])
            else:
                order_data["wallet_address"] = exchange_data["wallet_address"]

            # UNIFIED TRANSACTION SYSTEM: Create unified transaction for exchange
            unified_service = UnifiedTransactionService(
                dual_write_config=DualWriteConfig(
                    mode=DualWriteMode.DUAL_WRITE,
                    strategy=DualWriteStrategy.UNIFIED_FIRST
                )
            )
            
            # Determine transaction type based on exchange type
            if exchange_data["type"] == "crypto_to_ngn":
                transaction_type = UnifiedTransactionType.EXCHANGE_SELL_CRYPTO
            else:
                transaction_type = UnifiedTransactionType.EXCHANGE_BUY_CRYPTO
            
            # Check OTP requirement (should be False for exchanges per ConditionalOTPService)
            requires_otp = ConditionalOTPService.requires_otp(transaction_type.value)
            logger.info(f"🔄 EXCHANGE OTP requirement: {requires_otp} (via ConditionalOTPService)")
            
            # Create unified transaction request
            transaction_request = TransactionRequest(
                transaction_type=transaction_type,
                user_id=db_user.id,
                amount=Decimal(str(exchange_data["amount"] or 0)),
                currency=exchange_data.get("source_currency", "USD"),
                priority=UnifiedTransactionPriority.NORMAL,
                exchange_rate=exchange_data.get("rate_info", {}).get("rate"),
                metadata={
                    "exchange_type": exchange_data["type"],
                    "source_currency": order_data["source_currency"],
                    "target_currency": order_data["target_currency"],
                    "rate_info": exchange_data["rate_info"]
                }
            )
            
            # Create unified transaction
            unified_result = await unified_service.create_transaction(transaction_request)
            
            if not unified_result.success:
                logger.error(f"Failed to create unified exchange transaction: {unified_result.error}")
                if query:
                    await query.edit_message_text(
                        "❌ <b>Exchange Transaction Failed</b>\n\n"
                        "Unable to create exchange transaction. Please try again.",
                        parse_mode='HTML'
                    )
                return
            
            unified_transaction_id = unified_result.transaction_id
            logger.info(f"✅ Unified exchange transaction created: {unified_transaction_id}")
            
            # Store unified transaction ID for later reference
            context.user_data["unified_transaction_id"] = unified_transaction_id
            exchange_data["unified_transaction_id"] = unified_transaction_id
            
            # Continue with existing ExchangeService for dual-write safety
            from services.exchange_service import ExchangeService

            exchange_service = ExchangeService()
            exchange_order = await exchange_service.create_exchange_order(
                user_id=int(getattr(db_user, 'id', 0)),  # FIXED: Safe attribute access for SQLAlchemy
                order_type=exchange_data["type"],
                **order_data,
            )
            
            # Link exchange order with unified transaction
            if exchange_order and hasattr(exchange_order, 'id'):
                logger.info(f"🔗 Linking exchange order {exchange_order.id} with unified transaction {unified_transaction_id}")

            # FINANCIAL CRITICAL: Validate exchange order creation
            if not exchange_order:
                user_id = update.effective_user.id if update.effective_user else "unknown"
                logger.error(f"SECURITY ALERT: Exchange order creation failed for user {user_id} - Data: {order_data}")
                if query:
                    await query.edit_message_text(
                        "❌ ⚠️ CRITICAL ERROR\n\nFailed to create exchange order. This incident has been logged for security review."
                    )
                raise RuntimeError(f"Exchange order creation failed for user {user_id}")

            # Show payment instructions
            return await ExchangeHandler.show_payment_instructions(
                update, context, exchange_order
            )

        except (SecurityError, ValueError, RuntimeError) as e:
            # Financial security errors - log and terminate
            user_id = update.effective_user.id if update.effective_user else "unknown"
            logger.error(f"FINANCIAL SECURITY ERROR for user {user_id}: {e}")
            if query:
                await query.edit_message_text("❌ ⚠️ Security Error: Transaction terminated for your protection.")
            return
        except Exception as e:
            # Unexpected errors in financial operations
            user_id = update.effective_user.id if update.effective_user else "unknown"
            logger.error(f"CRITICAL: Unexpected error in financial operation for user {user_id}: {e}")
            if query:
                await query.edit_message_text("❌ Critical system error. Please contact support if this persists.")
            return

    @staticmethod
    async def show_payment_instructions(
        update: Update, context: ContextTypes.DEFAULT_TYPE, exchange_order
    ) -> int:
        """Show payment instructions for the exchange order"""
        query = update.callback_query

        if exchange_order.order_type == "crypto_to_ngn":
            # Generate crypto address for deposit with retry logic
            try:
                crypto_currency = exchange_order.source_currency

                # Retry logic for address generation
                max_retries = 3
                address_info = None

                for attempt in range(max_retries):
                    try:
                        # Generate payment address using Payment Manager with DynoPay failover
                        base_webhook_url = Config.WEBHOOK_URL or Config.BLOCKBEE_CALLBACK_URL
                        metadata = {
                            'exchange_order_id': exchange_order.id,
                            'order_type': 'crypto_to_ngn',
                            'operation_type': 'exchange_deposit'
                        }
                        
                        # Get the primary provider to determine correct callback URL
                        primary_provider = payment_manager.primary_provider
                        
                        # Set callback URL based on the primary provider configuration
                        if primary_provider == PaymentProvider.DYNOPAY:
                            callback_url = f"{base_webhook_url}/dynopay/exchange"
                        else:
                            callback_url = f"{base_webhook_url}/blockbee/callback/{exchange_order.id}"
                        
                        address_result, provider_used = await payment_manager.create_payment_address(
                            currency=crypto_currency,
                            amount=Decimal(str(exchange_order.final_amount or 0)) / Decimal("1535.80"),  # Convert NGN to USD
                            callback_url=callback_url,
                            reference_id=str(exchange_order.id),
                            metadata=metadata
                        )
                        
                        # Convert to expected format
                        address_info = {
                            "address": address_result.get("address_in") or address_result.get("address"),
                            "qr_code": address_result.get("qr_code"),
                            "payment_provider": provider_used.value
                        }
                        
                        if provider_used != PaymentProvider.DYNOPAY:
                            logger.warning(f"⚠️ Using backup provider ({provider_used.value}) for exchange order {exchange_order.id}")
                        else:
                            logger.info(f"✅ Using primary provider ({provider_used.value}) for exchange order {exchange_order.id}")

                        if address_info and "address" in address_info:
                            break  # Success, exit retry loop

                    except Exception as e:
                        logger.warning(
                            f"Address generation attempt {attempt + 1} failed for order {exchange_order.id}: {e}"
                        )
                        if attempt < max_retries - 1:
                            import asyncio

                            await asyncio.sleep(min(2**attempt, 10))  # Exponential backoff
                            continue

                if address_info and "address" in address_info:
                    # Update order with payment address AND CRITICAL: change status to awaiting_deposit
                    # SESSION MANAGEMENT FIX: Use centralized session manager for safety
                    from utils.session_manager import SessionManager
                    
                    with SessionManager.get_locked_session() as session:
                        # Get fresh order object with row-level lock to prevent race conditions
                        db_order = session.query(ExchangeOrder).filter(
                            ExchangeOrder.id == exchange_order.id
                        ).with_for_update().first()  # Row-level lock prevents race conditions
                        
                        if db_order:
                            # FIX: Use proper status mapping and validation through StatusUpdateFacade
                            from services.legacy_status_mapper import LegacyStatusMapper, LegacySystemType
                            from utils.status_update_facade import StatusUpdateFacade
                            
                            current_legacy_status = getattr(db_order, 'status', None)
                            new_legacy_status = ExchangeStatus.AWAITING_DEPOSIT
                            
                            # Map legacy statuses to unified system for validation
                            try:
                                if current_legacy_status:
                                    current_unified_status = LegacyStatusMapper.map_to_unified(
                                        current_legacy_status, LegacySystemType.EXCHANGE
                                    )
                                else:
                                    current_unified_status = None
                                    
                                new_unified_status = LegacyStatusMapper.map_to_unified(
                                    new_legacy_status, LegacySystemType.EXCHANGE
                                )
                                
                                # Use StatusUpdateFacade for comprehensive validation and update
                                facade = StatusUpdateFacade()
                                success = facade.update_exchange_status(
                                    exchange_id=db_order.id,
                                    current_status=current_legacy_status,
                                    new_status=new_legacy_status,
                                    session=session,
                                    additional_updates={
                                        "crypto_address": address_info["address"]
                                    }
                                )
                                
                                if success:
                                    logger.info(
                                        f"✅ Order {exchange_order.id} status updated: {current_legacy_status} → {new_legacy_status.value} with address {address_info['address']}"
                                    )
                                    # Update local object attributes (StatusUpdateFacade already updated DB)
                                    setattr(exchange_order, 'crypto_address', address_info["address"])
                                    # Validate transition for local object sync (defensive programming)
                                    try:
                                        if hasattr(exchange_order, 'status'):
                                            local_current = ExchangeStatus(exchange_order.status)
                                            ExchangeStateValidator.validate_transition(
                                                local_current, new_legacy_status, exchange_order.id
                                            )
                                        setattr(exchange_order, 'status', new_legacy_status.value)
                                    except Exception as local_validation_error:
                                        logger.error(
                                            f"🚫 EXCHANGE_HANDLER_BLOCKED: Local object sync validation failed for {exchange_order.id}: {local_validation_error}"
                                        )
                                        # DB already updated by facade, just log the inconsistency
                                        setattr(exchange_order, 'status', new_legacy_status.value)
                                else:
                                    raise ValueError(f"StatusUpdateFacade failed to update order {db_order.id}")
                                    
                            except Exception as mapping_error:
                                logger.error(f"Status mapping/update error for order {exchange_order.id}: {mapping_error}")
                                # Fallback to direct update for backward compatibility
                                session.query(ExchangeOrder).filter(
                                    ExchangeOrder.id == db_order.id
                                ).update({
                                    "crypto_address": address_info["address"],
                                    "status": new_legacy_status.value
                                })
                                session.commit()
                                logger.warning(f"⚠️  Used fallback status update for order {exchange_order.id}")
                        else:
                            # ENHANCED ERROR HANDLING: More detailed diagnostics
                            current_status = session.query(ExchangeOrder.status).filter(
                                ExchangeOrder.id == exchange_order.id
                            ).scalar()
                            logger.error(
                                f"RACE CONDITION: Order {exchange_order.id} not found (current status: {current_status}) for address assignment. This indicates a database consistency issue."
                            )
                            # Attempt recovery by checking if order exists in any status
                            recovery_order = session.query(ExchangeOrder).filter(
                                ExchangeOrder.id == exchange_order.id
                            ).first()
                            if recovery_order:
                                logger.info(f"Order recovery: Found order {exchange_order.id} with status {recovery_order.status} - updating address anyway")
                                setattr(recovery_order, 'crypto_address', address_info["address"])
                                # Validate state transition for recovery path
                                try:
                                    current_status = ExchangeStatus(recovery_order.status)
                                    new_status = ExchangeStatus.AWAITING_DEPOSIT
                                    ExchangeStateValidator.validate_transition(
                                        current_status, new_status, exchange_order.id
                                    )
                                    setattr(recovery_order, 'status', new_status.value)
                                    session.commit()
                                except Exception as validation_error:
                                    logger.error(
                                        f"🚫 EXCHANGE_HANDLER_BLOCKED: Recovery path {current_status}→{new_status} for order {exchange_order.id}: {validation_error}"
                                    )
                                    # Still update address even if status validation fails
                                    session.commit()
                            else:
                                logger.critical(f"CRITICAL: Order {exchange_order.id} completely missing from database - data integrity compromised")

                    # Fix Config scope issue by extracting timeout value before f-string
                    timeout_minutes = Config.NGN_EXCHANGE_TIMEOUT_MINUTES

                    # Get bank details for display with null safety
                    exchange_data = context.user_data.get("exchange_data", {}) if context.user_data else {}
                    bank_details = exchange_data.get("bank_details", {})
                    bank_name = bank_details.get("bank_name", "Your Bank")
                    account_name = bank_details.get("account_name", "Your Account")
                    account_number = bank_details.get("account_number", "N/A")

                    # Mask account number for security
                    if account_number != "N/A" and len(account_number) >= 8:
                        masked_account = f"{account_number[:4]}••••{account_number[-4:]}"
                    else:
                        masked_account = account_number

                    # Calculate exchange rate for transparency
                    exchange_rate = Decimal(str(exchange_order.final_amount or 0)) / Decimal(
                        str(exchange_order.source_amount or 1)
                    )

                    # FIXED: Plain text format - no parsing issues
                    crypto_address = address_info['address']
                    final_amount_clean = int(exchange_order.final_amount)
                    
                    text = f"""💰 Send Payment

📤 {exchange_order.source_amount} {crypto_currency} to:
<code>{crypto_address}</code>

📥 ₦{final_amount_clean:,} to {masked_account}
⏰ {timeout_minutes}min • 📋 EX{exchange_order.id}

⚠️ Send only {crypto_currency} to this address
💡 Tap address to copy"""

                else:
                    # ERROR HANDLING ENHANCEMENT: All attempts failed - mark order for recovery
                    try:
                        async with async_managed_session() as session:
                            stmt = select(ExchangeOrder).where(ExchangeOrder.id == exchange_order.id)
                            result = await session.execute(stmt)
                            db_order = result.scalar_one_or_none()
                            if db_order:
                                # Validate state transition before updating
                                try:
                                    current_status = ExchangeStatus(db_order.status)
                                    new_status = ExchangeStatus.ADDRESS_GENERATION_FAILED
                                    ExchangeStateValidator.validate_transition(
                                        current_status, new_status, exchange_order.id
                                    )
                                    db_order.status = new_status.value
                                    await session.commit()
                                    logger.error(
                                        f"Order {exchange_order.id} marked as address_generation_failed - will retry via background job"
                                    )
                                except Exception as validation_error:
                                    logger.error(
                                        f"🚫 EXCHANGE_HANDLER_BLOCKED: {current_status}→{new_status} for order {exchange_order.id}: {validation_error}"
                                    )
                                    # Don't crash - order already exists in valid state
                    except Exception as recovery_error:
                        logger.critical(f"Failed to mark order {exchange_order.id} for recovery: {recovery_error}")

                    text = """❌ Address Generation Failed
                    
Payment address generation temporarily unavailable. 

🔄 Don't worry! Our system will automatically retry and send you the payment details within a few minutes.

You'll receive a notification once ready."""

            except Exception as e:
                logger.error(
                    f"Critical error in address generation for order {exchange_order.id}: {e}"
                )

                # Mark for recovery
                async with async_managed_session() as session:
                    stmt = select(ExchangeOrder).where(ExchangeOrder.id == exchange_order.id)
                    result = await session.execute(stmt)
                    db_order = result.scalar_one_or_none()
                    if db_order:
                        # Validate state transition before updating
                        try:
                            current_status = ExchangeStatus(db_order.status)
                            new_status = ExchangeStatus.AWAITING_DEPOSIT
                            ExchangeStateValidator.validate_transition(
                                current_status, new_status, exchange_order.id
                            )
                            db_order.status = new_status.value
                            await session.commit()
                        except Exception as validation_error:
                            logger.error(
                                f"🚫 EXCHANGE_HANDLER_BLOCKED: {current_status}→{new_status} for order {exchange_order.id}: {validation_error}"
                            )
                            # Don't crash - recovery will retry later

                text = """❌ Technical Issue
                
Temporary system issue generating payment address.

🔄 Auto-Recovery Active - You'll receive payment details automatically within a few minutes."""

        else:  # ngn_to_crypto
            # Generate virtual account for NGN payment
            try:
                # CRITICAL FIX: Refresh exchange_order to prevent SQLAlchemy detachment
                async with async_managed_session() as session:
                    stmt = select(ExchangeOrder).where(ExchangeOrder.id == exchange_order.id)
                    result = await session.execute(stmt)
                    fresh_order = result.scalar_one_or_none()
                    
                    if not fresh_order or not fresh_order.user:
                        logger.error(f"Cannot find order {exchange_order.id} or associated user")
                        raise ValueError(f"Order {exchange_order.id} not found or missing user")
                    
                    user_id = fresh_order.user.id
                    source_amount = fresh_order.source_amount
                    order_id = fresh_order.id
                
                # CRITICAL FIX: Correct parameter order for Fincra service
                # Validate parameters before calling Fincra
                if not isinstance(user_id, int) or user_id <= 0:
                    logger.error(f"Invalid user_id: {user_id} (type: {type(user_id)})")
                    raise ValueError(f"Invalid user_id: {user_id}")
                
                if not source_amount or source_amount <= 0:
                    logger.error(f"Invalid source_amount: {source_amount}")
                    raise ValueError(f"Invalid source_amount: {source_amount}")
                
                logger.info(f"Creating payment link: amount_ngn={source_amount}, user_id={user_id}, order_id={order_id}")
                
                # Parameters: amount_ngn, user_id, purpose, escrow_id
                payment_link_result = await fincra_service.create_payment_link(
                    Decimal(str(source_amount or 0)),  # amount (NGN) - use Decimal for precision
                    int(user_id),          # user_id (integer)
                    f"exchange_order_{order_id}",  # purpose
                    None,  # escrow_id (not used for direct exchanges)
                )

                if payment_link_result:
                    # CRITICAL FIX: Update status from created → rate_locked → awaiting_deposit for NGN orders
                    # FIXED: Proper session management for persisting status change with validation
                    async with async_managed_session() as session:
                        stmt = select(ExchangeOrder).where(ExchangeOrder.id == exchange_order.id)
                        result = await session.execute(stmt)
                        db_order = result.scalar_one_or_none()
                        if db_order and db_order.status == ExchangeStatus.CREATED.value:
                            # First transition: created → awaiting_deposit (with validation)
                            try:
                                current_status = ExchangeStatus(db_order.status)
                                new_status = ExchangeStatus.AWAITING_DEPOSIT
                                ExchangeStateValidator.validate_transition(
                                    current_status, new_status, exchange_order.id
                                )
                                db_order.status = new_status.value
                                await session.commit()
                                logger.info(
                                    f"NGN Order {exchange_order.id} status updated: created → awaiting_deposit"
                                )
                            except Exception as validation_error:
                                logger.error(
                                    f"🚫 EXCHANGE_HANDLER_BLOCKED: {current_status}→{new_status} for order {exchange_order.id}: {validation_error}"
                                )
                        elif db_order and db_order.status == ExchangeStatus.RATE_LOCKED.value:
                            # Legacy support: rate_locked → awaiting_deposit (with validation)
                            try:
                                current_status = ExchangeStatus(db_order.status)
                                new_status = ExchangeStatus.AWAITING_DEPOSIT
                                ExchangeStateValidator.validate_transition(
                                    current_status, new_status, exchange_order.id
                                )
                                db_order.status = new_status.value
                                await session.commit()
                                logger.info(
                                    f"NGN Order {exchange_order.id} status updated: rate_locked → awaiting_deposit"
                                )
                            except Exception as validation_error:
                                logger.error(
                                    f"🚫 EXCHANGE_HANDLER_BLOCKED: {current_status}→{new_status} for order {exchange_order.id}: {validation_error}"
                                )
                        else:
                            logger.warning(
                                f"NGN Order {exchange_order.id} not found or not in expected status (current: {db_order.status if db_order else 'not found'})"
                            )

                    # Fix Config scope issue by extracting timeout value before f-string
                    timeout_minutes = Config.NGN_EXCHANGE_TIMEOUT_MINUTES
                    payment_link = payment_link_result.get('payment_link', '')
                    text = f"""💰 Send Payment

📤 {format_money(source_amount, 'NGN')} via bank transfer
📥 {decimal_to_string(fresh_order.final_amount, precision=8)} {fresh_order.target_currency}
⏰ {timeout_minutes}min • 📋 EX{order_id}"""

                elif payment_link_result and not payment_link_result.get("payment_link"):
                    # Fincra returned response but no payment link
                    text = f"""⚠️ Payment Link Pending

Order ID: <code>{fresh_order.utid if fresh_order.utid else f'EX{order_id}'}</code>
Status: Generating payment link

Setting up secure payment link (1-2 min)

Order: {format_money(source_amount, 'NGN')} → {decimal_to_string(fresh_order.final_amount, precision=8)} {fresh_order.target_currency}"""

                else:
                    # Complete failure to create payment link
                    text = f"""❌ Payment Setup Failed

Order ID: `{exchange_order.utid if exchange_order.utid else f'EX{exchange_order.id}'}`
Status: Setup error

Unable to generate payment link. 
Try creating a new order or contact support.

Order: {format_money(exchange_order.source_amount, 'NGN')} → {decimal_to_string(exchange_order.final_amount, precision=8)} {exchange_order.target_currency}"""

            except Exception as e:
                logger.error(f"Error generating payment link: {e}")
                text = f"""❌ Payment Setup Error

Order ID: `{exchange_order.utid if exchange_order.utid else f'EX{exchange_order.id}'}`
Status: Technical error

Error: {str(e)[:100]}...

Try: New order or wait 5min

Order: {format_money(exchange_order.source_amount, 'NGN')} → {decimal_to_string(exchange_order.final_amount, precision=8)} {exchange_order.target_currency}"""

                # Log the full error for debugging
                logger.error(f"Full payment link creation error: {e}", exc_info=True)

        # Check bank accounts for switch options - ENHANCED: Always show bank options
        bank_count = 0
        async with async_managed_session() as session_check:
            if update.effective_user:
                user_id = update.effective_user.id
                stmt = select(User).where(User.telegram_id == normalize_telegram_id(user_id))
                result = await session_check.execute(stmt)
                user = result.scalar_one_or_none()
                if user:
                    from models import SavedBankAccount
                    from sqlalchemy import func
                    stmt = select(func.count()).select_from(SavedBankAccount).where(
                        SavedBankAccount.user_id == user.id
                    )
                    result = await session_check.execute(stmt)
                    bank_count = result.scalar()

        # Build clean payment confirmation keyboard - UX IMPROVEMENT: No switching options at payment stage
        keyboard = []
        
        # Add payment link button if available
        if 'payment_link_result' in locals() and payment_link_result and payment_link_result.get('payment_link'):
            from telegram import WebAppInfo
            keyboard.append([
                InlineKeyboardButton("💳 Pay via Bank Transfer", web_app=WebAppInfo(url=payment_link_result['payment_link']))
            ])
        
        keyboard.extend([
            [InlineKeyboardButton("📞 Get Help", callback_data="contact_support")],
            [InlineKeyboardButton("❌ Cancel Order", callback_data="exchange_cancel"), 
             InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")],
        ])
        reply_markup = InlineKeyboardMarkup(keyboard)

        if query:
            await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=reply_markup)
            
            # Generate and send compact QR code for crypto payments
            if "Send Payment" in text and "📤 Send" in text and "<code>" in text:
                try:
                    # Extract crypto address from the text (between code tags)
                    import re
                    address_match = re.search(r'<code>([^<]+)</code>', text)
                    if address_match:
                        crypto_address = address_match.group(1)
                        
                        # Extract currency and amount from text
                        amount_match = re.search(r'Send ([\d\.]+) (\w+)', text)
                        if amount_match:
                            amount = Decimal(str(amount_match.group(1) or 0))
                            currency = amount_match.group(2)
                            
                            # Generate QR code using existing service
                            from services.qr_generator import QRCodeService
                            qr_base64 = QRCodeService.generate_deposit_qr(
                                address=crypto_address,
                                amount=amount,
                                currency=currency
                            )
                            
                            if qr_base64:
                                # Convert base64 to BytesIO for sending
                                import base64
                                from io import BytesIO
                                
                                qr_bytes = base64.b64decode(qr_base64)
                                bio = BytesIO(qr_bytes)
                                bio.name = "payment_qr.png"
                                bio.seek(0)
                                
                                # Send compact QR code
                                caption = f"📱 Scan to Pay\n💰 {amount} {currency}"
                                
                                msg = getattr(query, 'message', None)
                                if msg and callable(getattr(msg, 'reply_photo', None)):
                                    await msg.reply_photo(
                                        photo=bio,
                                        caption=caption
                                    )
                            
                except Exception as qr_error:
                    logger.error(f"Error generating QR code: {qr_error}")
                    # Continue without QR if generation fails

        return

    @staticmethod
    async def handle_switch_crypto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle cryptocurrency switching from payment screen"""
        query = update.callback_query
        if not query:
            return
            
        await safe_answer_callback_query(query, "🔄")

        # Get current order ID from the payment screen context
        async with async_managed_session() as session:
            try:
                user_id = update.effective_user.id if update.effective_user else 0
                stmt = select(User).where(User.telegram_id == normalize_telegram_id(user_id))
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()
                if not user:
                    if query:
                        await query.edit_message_text("❌ User not found. Please restart.")
                    return

                # Find the most recent pending exchange order for this user
                stmt = select(ExchangeOrder).where(
                    ExchangeOrder.user_id == user.id,
                    ExchangeOrder.status.in_(["created", "awaiting_deposit"])
                ).order_by(ExchangeOrder.created_at.desc())
                result = await session.execute(stmt)
                current_order = result.scalar_one_or_none()

                if not current_order:
                    if query:
                        await query.edit_message_text(
                            "❌ No active order found to switch.\n\nPlease create a new exchange order.",
                            reply_markup=InlineKeyboardMarkup([
                                [InlineKeyboardButton("🔄 New Exchange", callback_data="direct_exchange")],
                                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
                            ])
                        )
                    return

                # Check if order has received payment
                if current_order.status not in ["created", "awaiting_deposit"]:
                    if query:
                        await query.edit_message_text(
                            f"❌ Cannot switch crypto after payment started.\n\nOrder: EX{current_order.id}\nStatus: {current_order.status}",
                            reply_markup=InlineKeyboardMarkup([
                                [InlineKeyboardButton("📞 Get Help", callback_data="contact_support")],
                                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
                            ])
                        )
                    return

                # Store current order info for switching
                # UNIFIED SWITCH CONTEXT - Standard format for all switching operations
                context.user_data["switch_context"] = {
                    "operation_type": "crypto_switch",
                    "current_order_id": current_order.id,
                    "bank_account_id": getattr(current_order, 'bank_account_id', None),
                    "source_amount": current_order.source_amount,
                    "current_crypto": current_order.source_currency,
                    "target_currency": "NGN",
                    "exchange_type": "crypto_to_ngn",
                    "context_id": f"crypto_switch_{current_order.id}"
                }

                # Show crypto selection for switching
                text = f"""🔄 Switch Cryptocurrency

Current: {current_order.source_currency} → ₦{int(Decimal(str(getattr(current_order, 'final_amount', 0) or 0))):,}
Amount: {current_order.source_amount}

Choose new crypto:"""

                from utils.crypto_ui_components import CryptoUIComponents
                reply_markup = CryptoUIComponents.get_crypto_selection_keyboard(
                    callback_prefix="exchange_switch_to:",
                    layout="compact",
                    back_callback="exchange_switch_cancel"
                )

                if query:
                    await query.edit_message_text(text, reply_markup=reply_markup)
                return

            except Exception as e:
                logger.error(f"Error handling crypto switch: {e}")
                if query:
                    await query.edit_message_text("❌ Error switching crypto. Please try again.")
                return

    @staticmethod
    async def handle_switch_bank(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle bank account switching from payment screen"""
        query = update.callback_query
        if not query:
            return
            
        await safe_answer_callback_query(query, "🏦")

        # Get current order ID from the payment screen context
        async with async_managed_session() as session:
            try:
                user_id = update.effective_user.id if update.effective_user else 0
                stmt = select(User).where(User.telegram_id == normalize_telegram_id(user_id))
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()
                if not user:
                    if query:
                        await query.edit_message_text("❌ User not found. Please restart.")
                    return

                # Find the most recent pending exchange order for this user
                stmt = select(ExchangeOrder).where(
                    ExchangeOrder.user_id == user.id,
                    ExchangeOrder.status.in_(["created", "awaiting_deposit"])
                ).order_by(ExchangeOrder.created_at.desc())
                result = await session.execute(stmt)
                current_order = result.scalar_one_or_none()

                if not current_order:
                    if query:
                        await query.edit_message_text("❌ No active order found.")
                    return

                # Security: Prevent switching if payment already received
                if getattr(current_order, 'payment_received', False):
                    if query:
                        await query.edit_message_text("❌ Cannot switch bank - payment already received.")
                    return

                # Get all saved bank accounts for this user
                from models import SavedBankAccount
                stmt = select(SavedBankAccount).where(
                    SavedBankAccount.user_id == user.id
                ).order_by(SavedBankAccount.created_at.desc())
                result = await session.execute(stmt)
                saved_banks = result.scalars().all()

                if len(saved_banks) == 0:
                    # No saved banks - redirect to add bank flow
                    if query:
                        await query.edit_message_text(
                            "🏦 Add Bank Account\n\n"
                            "You need to add a bank account to receive your NGN payment.\n\n"
                            "Click the button below to add your first bank account:",
                            reply_markup=InlineKeyboardMarkup([
                                [InlineKeyboardButton("➕ Add Bank Account", callback_data="add_bank_account")],
                                [InlineKeyboardButton("❌ Cancel", callback_data="exchange_bank_switch_cancel")]
                            ])
                        )
                    return
                elif len(saved_banks) == 1:
                    # One bank - allow changing it or adding new one
                    current_bank = saved_banks[0]
                    masked_account = f"{current_bank.account_number[:4]}••••{current_bank.account_number[-4:]}"
                    
                    if query:
                        await query.edit_message_text(
                            f"🏦 Bank Account Options\n\n"
                            f"Current Bank:\n"
                            f"{current_bank.bank_name}\n"
                            f"{masked_account} • {current_bank.account_name}\n\n"
                            f"Choose an option:",
                            reply_markup=InlineKeyboardMarkup([
                                [InlineKeyboardButton("➕ Add New Bank", callback_data="add_bank_account")],
                                [InlineKeyboardButton("✏️ Keep Current", callback_data="exchange_bank_switch_cancel")],
                                [InlineKeyboardButton("❌ Cancel", callback_data="exchange_bank_switch_cancel")]
                            ])
                        )
                    return

                # Store current order info for switching
                # UNIFIED SWITCH CONTEXT - Standard format for all switching operations
                context.user_data["switch_context"] = {
                    "operation_type": "bank_switch",
                    "current_order_id": current_order.id,
                    "current_bank_id": getattr(current_order, 'bank_account_id', None),
                    "source_amount": current_order.source_amount,
                    "current_crypto": current_order.source_currency,
                    "target_currency": "NGN",
                    "exchange_type": "crypto_to_ngn",
                    "context_id": f"bank_switch_{current_order.id}"
                }

                # Show bank selection for switching
                text = f"""🏦 Switch Bank Account

Current Order: {current_order.source_currency} → ₦{int(Decimal(str(getattr(current_order, 'final_amount', 0) or 0))):,}
Amount: {current_order.source_amount}

Choose new bank account:"""

                keyboard = []
                for bank in saved_banks:
                    # Skip current bank account - use switch context for consistency
                    if context.user_data["switch_context"]["current_bank_id"] == getattr(bank, 'id', None):
                        continue
                        
                    masked_account = f"{bank.account_number[:4]}••••{bank.account_number[-4:]}"
                    button_text = f"🏦 {bank.bank_name}\n{masked_account} • {bank.account_name}"
                    keyboard.append([
                        InlineKeyboardButton(
                            button_text, 
                            callback_data=f"exchange_bank_switch_{bank.id}"
                        )
                    ])

                keyboard.append([
                    InlineKeyboardButton("❌ Cancel", callback_data="exchange_bank_switch_cancel")
                ])

                reply_markup = InlineKeyboardMarkup(keyboard)
                if query:
                    await query.edit_message_text(text, reply_markup=reply_markup)
                return

            except Exception as e:
                logger.error(f"Error in handle_switch_bank: {e}")
                if query:
                    await query.edit_message_text("❌ Error processing bank switch. Please try again.")
                return

    @staticmethod
    async def handle_crypto_switch_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle new crypto selection for switching"""
        from models import User
        from datetime import datetime
        query = update.callback_query
        if not query or not query.data:
            return
            
        # IMMEDIATE FEEDBACK: Exchange switching action
        await safe_answer_callback_query(query, "🔄 Exchange switching")

        if query.data == "exchange_switch_cancel":
            # Return to payment screen
            if query:
                await query.edit_message_text("🔄 Returning to payment screen...")
            # Redirect back to the current order display
            # This will be handled by the payment processor
            return

        if not query.data.startswith("exchange_switch_to:"):
            return

        new_crypto = query.data.split(":")[1]
        # UNIFIED CONTEXT RETRIEVAL with enhanced error logging
        switch_context = context.user_data.get("switch_context") if context.user_data else None
        if not switch_context:
            logger.error(f"Crypto switch failed: No switch context found for user {update.effective_user.id if update.effective_user else 'unknown'}")
            if query:
                await query.edit_message_text("❌ Switch session expired. Please restart.")
            return
        
        # PHASE 3: Support both post-confirmation and pre-confirmation switching
        operation_type = switch_context.get("operation_type")
        if operation_type not in ["crypto_switch", "pre_confirmation_crypto_switch"]:
            logger.error(f"Crypto switch failed: Wrong operation type '{operation_type}' for user {update.effective_user.id if update.effective_user else 'unknown'}")
            if query:
                await query.edit_message_text("❌ Invalid switch context. Please restart.")
            return

        async with async_managed_session() as session:
            try:
                user_id = update.effective_user.id if update.effective_user else 0
                stmt = select(User).where(User.telegram_id == normalize_telegram_id(user_id))
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()
                
                # PHASE 3: Handle pre-confirmation vs post-confirmation switching
                if operation_type == "pre_confirmation_crypto_switch":
                    # Pre-confirmation: Update exchange data, no order to cancel
                    logger.info(f"Pre-confirmation crypto switch to {new_crypto} for user {user_id}")
                    current_order = None
                else:
                    # Post-confirmation: Cancel existing order
                    stmt = select(ExchangeOrder).where(ExchangeOrder.id == switch_context["current_order_id"])
                    result = await session.execute(stmt)
                    current_order = result.scalar_one_or_none()

                    if not current_order or current_order.status not in ["created", "awaiting_deposit"]:
                        if query:
                            await query.edit_message_text("❌ Cannot switch - order status changed.")
                        return

                    # Cancel current order - use proper SQLAlchemy assignment
                    from sqlalchemy import update as sql_update
                    await session.execute(
                        sql_update(ExchangeOrder).where(ExchangeOrder.id == current_order.id).values(
                            status=ExchangeStatus.CANCELLED.value
                        )
                    )
                    # OPTIMIZATION: Invalidate exchange cache on order cancellation
                    invalidate_exchange_cache(context.user_data)
                    logger.info(f"Cancelled order {current_order.id} for crypto switch")
                    
                    # Send admin notification about exchange cancellation
                    try:
                        from services.admin_trade_notifications import admin_trade_notifications
                        from models import User
                        
                        # Get user information
                        stmt = select(User).where(User.id == getattr(current_order, "user_id", 0))
                        result = await session.execute(stmt)
                        user_obj = result.scalar_one_or_none()
                        user_info = (
                            user_obj.username or user_obj.first_name or f"User_{user_obj.telegram_id}"
                            if user_obj else "Unknown User"
                        )
                        
                        exchange_cancellation_data = {
                            'exchange_id': str(getattr(current_order, "id", "Unknown")),
                            'amount': Decimal(str(getattr(current_order, "source_amount", 0) or 0)),
                            'from_currency': getattr(current_order, "source_currency", "Unknown"),
                            'to_currency': getattr(current_order, "target_currency", "Unknown"),
                            'exchange_type': getattr(current_order, "order_type", "Unknown"),
                            'user_info': user_info,
                            'cancellation_reason': 'Cancelled for crypto switch',
                            'cancelled_at': datetime.utcnow()
                        }
                        
                        # Send admin notification asynchronously
                        import asyncio
                        asyncio.create_task(
                            admin_trade_notifications.notify_exchange_cancelled(exchange_cancellation_data)
                        )
                        logger.info(f"Admin notification queued for exchange cancellation: {current_order.id}")
                        
                    except Exception as e:
                        logger.error(f"Failed to queue admin notification for exchange cancellation: {e}")

                # Calculate new amounts with the selected crypto
                from services.financial_gateway import financial_gateway
                source_amount = Decimal(str(switch_context["source_amount"] or 0))
                
                # Get real-time rates for new crypto
                crypto_usd_rate = await financial_gateway.get_crypto_to_usd_rate(new_crypto)
                clean_usd_ngn_rate = await financial_gateway.get_usd_to_ngn_rate_clean()

                if not crypto_usd_rate or not clean_usd_ngn_rate:
                    if query:
                        await query.edit_message_text(f"❌ Unable to get rates for {new_crypto}. Please try again.")
                    return

                # Calculate final NGN amount with markup
                market_rate_per_crypto = Decimal(str(crypto_usd_rate or 0)) * Decimal(str(clean_usd_ngn_rate or 0))
                markup_percentage = Decimal(str(Config.EXCHANGE_MARKUP_PERCENTAGE)) / Decimal("100")
                markup_amount = market_rate_per_crypto * markup_percentage
                display_rate = market_rate_per_crypto - markup_amount
                final_ngn_amount = source_amount * display_rate

                # PHASE 3: Handle pre-confirmation vs post-confirmation logic
                if operation_type == "pre_confirmation_crypto_switch":
                    # Pre-confirmation: Update exchange data and return to confirmation
                    exchange_data = context.user_data.get("exchange_data", {}) if context.user_data else {}
                    
                    # Update crypto in exchange data
                    exchange_data["crypto"] = new_crypto
                    
                    # Recalculate rate info with new crypto
                    rate_info = {
                        "crypto_amount": source_amount,
                        "crypto_usd_rate": crypto_usd_rate,
                        "usd_ngn_rate": clean_usd_ngn_rate,
                        "final_ngn_amount": final_ngn_amount,
                        "markup_percentage": Config.EXCHANGE_MARKUP_PERCENTAGE,
                        "exchange_rate": display_rate
                    }
                    exchange_data["rate_info"] = rate_info
                    
                    # Update context
                    if context.user_data:
                        context.user_data["exchange_data"] = exchange_data
                    
                    # Clear switch context
                    if context.user_data:
                        context.user_data.pop("switch_context", None)
                    
                    # Show success message and return to confirmation
                    text = f"""✅ Crypto Switched Successfully!

Updated to: {new_crypto}
New amount: ₦{final_ngn_amount:,.2f}

Returning to confirmation screen..."""

                    if query:
                        await query.edit_message_text(text)
                    
                    # Return to confirmation screen
                    import asyncio
                    await asyncio.sleep(1)  # Brief pause for user to see update
                    return await ExchangeHandler.confirm_exchange_order(update, context)
                    
                else:
                    # Post-confirmation: Create new order (existing logic)
                    from datetime import datetime, timedelta
                    
                    # Initialize variables to avoid unbound errors
                    text = ""
                    crypto_address = ""
                
                    new_order = ExchangeOrder(
                        user_id=getattr(user, 'id', 0),
                        order_type="crypto_to_ngn",
                        source_currency=new_crypto,
                        target_currency="NGN",
                        source_amount=source_amount,
                        target_amount=final_ngn_amount,
                        exchange_rate=display_rate,
                        markup_percentage=Config.EXCHANGE_MARKUP_PERCENTAGE,
                        fee_amount=0,
                        final_amount=final_ngn_amount,
                        expires_at=datetime.utcnow() + timedelta(minutes=Config.NGN_EXCHANGE_TIMEOUT_MINUTES),
                        created_at=datetime.utcnow(),
                        status=ExchangeStatus.CREATED.value
                    )
                    session.add(new_order)
                    await session.commit()
                    
                    # Send admin notification about new exchange creation
                    try:
                        from services.admin_trade_notifications import admin_trade_notifications
                        
                        user_info = (
                            user.username or user.first_name or f"User_{user.telegram_id}"
                            if user else "Unknown User"
                        )
                        
                        exchange_notification_data = {
                            'exchange_id': str(new_order.id),
                            'amount': source_amount,
                            'from_currency': new_crypto,
                            'to_currency': 'NGN',
                            'exchange_type': 'crypto_to_ngn',
                            'user_info': user_info,
                            'created_at': new_order.created_at or datetime.utcnow()
                        }
                        
                        # Send admin notification asynchronously
                        import asyncio
                        asyncio.create_task(
                            admin_trade_notifications.notify_exchange_created(exchange_notification_data)
                        )
                        logger.info(f"Admin notification queued for exchange creation: {new_order.id}")
                        
                    except Exception as e:
                        logger.error(f"Failed to queue admin notification for exchange creation: {e}")

                    old_crypto = current_order.source_currency if current_order else "previous crypto"
                    logger.info(f"Created new order {new_order.id} switching from {old_crypto} to {new_crypto}")

                    # Generate new payment address for new crypto with DynoPay failover
                    try:
                        # FINANCIAL SECURITY: Validate amount before API call
                        usd_amount = source_amount * Decimal(str(crypto_usd_rate or 0))
                        if usd_amount <= 0:
                            raise Exception(f"Invalid USD amount: {usd_amount}")
                        
                        logger.info(f"Creating payment address for {new_crypto} switch - Amount: {format_money(usd_amount, 'USD')}")
                        
                        # Generate unique exchange order ID
                        exchange_order_id = f"EX{new_order.id:06d}"
                        
                        # Use payment manager for failover support
                        base_webhook_url = Config.WEBHOOK_URL or Config.BLOCKBEE_CALLBACK_URL
                        metadata = {
                            'exchange_order_id': new_order.id,
                            'order_type': 'crypto_to_ngn',
                            'operation_type': 'exchange_switch'
                        }
                        
                        # Get the primary provider to determine correct callback URL
                        primary_provider = payment_manager.primary_provider
                        
                        # Set callback URL based on the primary provider configuration
                        if primary_provider == PaymentProvider.DYNOPAY:
                            callback_url = f"{base_webhook_url}/dynopay/exchange"
                        else:
                            callback_url = f"{base_webhook_url}/blockbee/callback/{exchange_order_id}"
                        
                        address_result, provider_used = await payment_manager.create_payment_address(
                            currency=new_crypto.lower(),
                            amount=usd_amount,
                            callback_url=callback_url,
                            reference_id=exchange_order_id,
                            metadata=metadata
                        )
                        
                        # Convert to expected format
                        address_info = {
                            "address": address_result.get("address_in") or address_result.get("address"),
                            "qr_code": address_result.get("qr_code"),
                            "payment_provider": provider_used.value
                        }
                        
                        if provider_used != PaymentProvider.DYNOPAY:
                            logger.warning(f"⚠️ Using backup provider ({provider_used.value}) for exchange switch {new_order.id}")
                        else:
                            logger.info(f"✅ Using primary provider ({provider_used.value}) for exchange switch {new_order.id}")

                        # FIXED: Check for correct field name - service returns "address" not "address_in"
                        if address_info and "address" in address_info:
                            # Update order with payment address and exchange ID
                            from sqlalchemy import update as sql_update
                            await session.execute(
                                sql_update(ExchangeOrder).where(ExchangeOrder.id == new_order.id).values(
                                    crypto_address=address_info["address"],  # FIXED: Use correct field name
                                    exchange_order_id=exchange_order_id,  # Save exchange ID for callback
                                    status=ExchangeStatus.AWAITING_DEPOSIT.value
                                )
                            )
                            await session.commit()

                            # Get bank account for display  
                            bank_account_id = switch_context.get("bank_account_id")
                            bank_account = None
                            if bank_account_id:
                                from models import SavedBankAccount
                                stmt_bank = select(SavedBankAccount).where(SavedBankAccount.id == bank_account_id)
                                result_bank = await session.execute(stmt_bank)
                                bank_account = result_bank.scalar_one_or_none()
                            masked_account = f"{bank_account.account_number[:4]}••••{bank_account.account_number[-4:]}" if bank_account else "Your bank"

                            # Show new payment details
                            crypto_address = address_info['address']  # FIXED: Use correct field name
                            final_amount_clean = int(Decimal(str(getattr(new_order, 'final_amount', 0) or 0)))
                            
                            text = f"""✅ Crypto Switched Successfully!

💰 Send Payment

📤 Send {new_order.source_amount} {new_crypto} to:
<code>{crypto_address}</code>

📥 You'll receive: ₦{final_amount_clean:,} to {masked_account}
⏰ Expires: 45 minutes
📋 Order: EX{new_order.id}

⚡ Automatic processing after 3 confirmations
⚠️ Send only {new_crypto} to this address

💡 Tap and hold the address above to copy it"""

                            # Check for multiple bank accounts for this user
                            user_has_multiple_banks = False
                            if update.effective_user:
                                stmt = select(User).where(User.telegram_id == normalize_telegram_id(update.effective_user.id))
                                result = await session.execute(stmt)
                                check_user = result.scalar_one_or_none()
                                if check_user:
                                    from models import SavedBankAccount
                                    from sqlalchemy import func
                                    stmt = select(func.count()).select_from(SavedBankAccount).where(
                                        SavedBankAccount.user_id == check_user.id
                                    )
                                    result = await session.execute(stmt)
                                    bank_count = result.scalar()
                                    user_has_multiple_banks = bank_count > 1

                            # Build keyboard with conditional bank switch button
                            keyboard = []
                            
                            # Switch options row
                            switch_row = []
                            switch_row.append(InlineKeyboardButton("🔄 Switch Crypto", callback_data="exchange_crypto_switch"))
                            if user_has_multiple_banks:
                                switch_row.append(InlineKeyboardButton("🏦 Switch Bank", callback_data="exchange_bank_switch"))
                            keyboard.append(switch_row)
                            
                            # Utility buttons
                            keyboard.extend([
                                [InlineKeyboardButton("📞 Get Help", callback_data="contact_support")],
                                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")],
                            ])
                            reply_markup = InlineKeyboardMarkup(keyboard)

                            # Display payment instructions with copyable address included
                            if query and 'text' in locals() and 'text' in locals():
                                await query.edit_message_text(text, parse_mode="HTML", reply_markup=reply_markup)
                                
                                # Generate and send compact QR code for crypto switching
                                if 'crypto_address' in locals() and crypto_address:
                                    try:
                                        # Generate QR code using existing service
                                        from services.qr_generator import QRCodeService
                                        qr_base64 = QRCodeService.generate_deposit_qr(
                                            address=crypto_address,
                                            amount=getattr(new_order, 'source_amount', None) or Decimal('0'),
                                            currency=new_crypto
                                        )
                                    
                                        if qr_base64:
                                            # Convert base64 to BytesIO for sending
                                            import base64
                                            from io import BytesIO
                                            
                                            qr_bytes = base64.b64decode(qr_base64)
                                            bio = BytesIO(qr_bytes)
                                            bio.name = "payment_qr.png"
                                            bio.seek(0)
                                            
                                            # Send compact QR code
                                            caption = f"📱 Scan to Pay\n💰 {getattr(new_order, 'source_amount', 0) or 0} {new_crypto}"
                                            
                                            msg = getattr(query, 'message', None)
                                            if msg and callable(getattr(msg, 'reply_photo', None)):
                                                await msg.reply_photo(
                                                    photo=bio,
                                                    caption=caption
                                                )
                                    except Exception as qr_error:
                                        logger.error(f"Error generating QR code for crypto switch: {qr_error}")
                                    # Continue without QR if generation fails
                            else:
                                raise Exception("Failed to generate payment address")

                    except Exception as e:
                        logger.error(f"Error generating address for switched crypto: {e}")
                        # Rollback new order
                        session.delete(new_order)
                        await session.commit()
                        
                        if query:
                            if query:
                                await query.edit_message_text(
                                f"❌ Error setting up {new_crypto} payment. Please try again.",
                                reply_markup=InlineKeyboardMarkup([
                                    [InlineKeyboardButton("🔄 Try Again", callback_data="exchange_crypto_switch")],
                                    [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
                                ])
                            )
                        return

            except Exception as e:
                logger.error(f"Error processing crypto switch: {e}")
                # ENHANCED: More specific error message with recovery options
                switch_context = context.user_data.get('switch_context', {}) if context.user_data else {}
                new_crypto_name = switch_context.get('new_crypto', 'new crypto')
                
                if query:
                    await query.edit_message_text(
                        f"❌ Error switching to {new_crypto_name}.\n\n"
                        "This might be a temporary service issue. Please try again or choose a different cryptocurrency.",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🔄 Try Again", callback_data="exchange_crypto_switch")],
                            [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
                        ])
                    )
                return
        
        # Default return in case none of the conditions are met
        return

    @staticmethod
    async def show_exchange_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Show user's comprehensive exchange history with real data"""
        from models import ExchangeOrder
        from sqlalchemy import desc
        from datetime import datetime

        if not update.effective_user:
            return

        user_id = update.effective_user.id

        async with async_managed_session() as session:
            try:
                # Get user from database
                from models import User

                stmt = select(User).where(User.telegram_id == normalize_telegram_id(user_id))
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()
                if not user:
                    text = "❌ You need to register first. Use /start to get started."
                    keyboard = [
                        [InlineKeyboardButton("⬅️ Back", callback_data="direct_exchange")]
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)

                    if update.callback_query:
                        await safe_edit_message_text(
                            update.callback_query,
                            text,
                            parse_mode="HTML",
                            reply_markup=reply_markup,
                        )
                    return

                # Get exchange orders for this user
                stmt = (
                    select(ExchangeOrder)
                    .where(ExchangeOrder.user_id == user.id)
                    .order_by(desc(ExchangeOrder.created_at))
                    .limit(20)
                )
                result = await session.execute(stmt)
                exchange_orders = result.scalars().all()

                if not exchange_orders:
                    text = "📊 Exchange History\n\n🔄 No exchange history found\n\nStart your first exchange to see your transaction history here!"
                    keyboard = [
                        [InlineKeyboardButton("⬅️ Back", callback_data="direct_exchange")]
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)

                    query = update.callback_query
                    if query:
                        await safe_edit_message_text(query, text, reply_markup=reply_markup)
                    return

                # Build comprehensive history report
                text = "📊 Exchange History\n\n"

                # Summary statistics with null safety
                total_orders = len(exchange_orders)
                completed_orders = len(
                    [o for o in exchange_orders if getattr(o, 'status', None) == ExchangeStatus.COMPLETED.value]
                )
                total_volume = sum(
                    [getattr(o, 'source_amount', None) or Decimal('0') for o in exchange_orders if getattr(o, 'source_amount', None) is not None]
                )

                text += "📈 Summary\n"
                text += f"• Total orders: {total_orders}\n"
                text += f"• Completed: {completed_orders}\n"
                if total_orders > 0:
                    success_rate = safe_multiply(Decimal(str(completed_orders)), Decimal('100'), precision=1) / Decimal(str(total_orders))
                    text += f"• Success rate: {decimal_to_string(success_rate, precision=1)}%\n"
                else:
                    text += "• Success rate: 0%\n"
                text += f"• Total volume: {format_money(total_volume, 'USD')}\n\n"

                # Recent transactions (last 10)
                text += f"🗓️ Recent Exchanges (Last {min(10, len(exchange_orders))})\n\n"

                for i, order in enumerate(exchange_orders[:10]):
                    # Format date with null safety
                    created_at = getattr(order, "created_at", None)
                    date_str = (
                        created_at.strftime("%m/%d %H:%M")
                        if created_at
                        else "Unknown"
                    )

                    # Format order type
                    order_type_display = {
                        "crypto_to_ngn": "💰➡️₦",
                        "ngn_to_crypto": "₦➡️💰",
                        "usd_to_ngn": "💵➡️₦",
                        "ngn_to_usd": "₦➡️💵",
                    }.get(getattr(order, "order_type", None) or "", "🔄")

                    # Format status with emoji - using actual database statuses
                    status_display = {
                        "completed": "✅",
                        "awaiting_deposit": "⏳",
                        "processing": "🔄",
                        "failed": "❌",
                        "cancelled": "🚫",
                        "expired": "⏰",
                        "rate_locked": "🔒",
                    }.get(getattr(order, 'status', None) or '', "❓")

                    # Format amounts with null safety
                    source_amount = getattr(order, 'source_amount', None)
                    target_amount = getattr(order, 'target_amount', None)
                    from_amount = source_amount or Decimal("0")
                    to_amount = target_amount or Decimal("0")
                    from_currency = getattr(order, 'source_currency', None) or "USD"
                    to_currency = getattr(order, 'target_currency', None) or "NGN"

                    text += f"{i+1}. {order_type_display} {status_display}\n"
                    text += f"   {decimal_to_string(from_amount, precision=2)} {from_currency} → {decimal_to_string(to_amount, precision=2)} {to_currency}\n"
                    text += f"   {date_str}\n\n"

                    # Prevent message from being too long
                    if len(text) > 3500:
                        text += "...📜 More in full export\n\n"
                        break

                # Time period analysis
                now = datetime.utcnow()
                week_ago = now - timedelta(days=7)
                month_ago = now - timedelta(days=30)

                recent_orders = [
                    o
                    for o in exchange_orders
                    if getattr(o, "created_at", None) is not None
                    and getattr(o, "created_at", datetime.min) >= week_ago
                ]
                monthly_orders = [
                    o
                    for o in exchange_orders
                    if getattr(o, "created_at", None) is not None
                    and getattr(o, "created_at", datetime.min) >= month_ago
                ]

                text += "📅 Activity\n"
                text += f"• Last 7 days: {len(recent_orders)} orders\n"
                text += f"• Last 30 days: {len(monthly_orders)} orders\n"

                # Most used exchange type
                if exchange_orders:
                    type_counts = {}
                    for order in exchange_orders:
                        order_type = getattr(order, 'order_type', None)
                        if order_type:
                            type_counts[order_type] = (
                                type_counts.get(order_type, 0) + 1
                            )

                    if type_counts:
                        most_used = max(type_counts, key=lambda x: type_counts.get(x, 0))
                    else:
                        most_used = None
                    most_used_display = {
                        "crypto_to_ngn": "Crypto → ₦ NGN",
                        "ngn_to_crypto": "₦ NGN → Crypto",
                        "usd_to_ngn": "USD → ₦ NGN",
                        "ngn_to_usd": "₦ NGN → USD",
                    }.get(most_used or '', most_used or 'Unknown')

                    if most_used:
                        text += (
                            f"• Most used: {most_used_display} ({type_counts[most_used]}x)\n"
                        )

                text += f"\n🔄 Updated: {datetime.utcnow().strftime('%H:%M:%S')}"

            except Exception as e:
                logger.error(f"Error in exchange history: {e}")
                text = f"❌ Error loading exchange history: {str(e)[:100]}..."

        keyboard = [
            [InlineKeyboardButton("📊 Refresh", callback_data="exchange_history")],
            [InlineKeyboardButton("⬅️ Back", callback_data="direct_exchange")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        if update.callback_query:
            await update.callback_query.edit_message_text(
                text, parse_mode="HTML", reply_markup=reply_markup
            )

        return

    @staticmethod
    async def show_exchange_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Show exchange help information"""
        text = f"""❓ *Quick Exchange*

Convert crypto ↔ NGN instantly

*💰 Fee:* 5% markup  
*⏱️ Speed:* {Config.AVERAGE_PROCESSING_TIME_MINUTES}-{getattr(Config, 'MAX_PROCESSING_TIME_MINUTES', 15)} minutes
*📱 Supported:* BTC, ETH, USDT, LTC, DOGE, TRX, XMR

*Need help?*
📧 {Config.SUPPORT_EMAIL}
💬 @lockbay_bot"""

        keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data="direct_exchange")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        if update.callback_query:
            await update.callback_query.edit_message_text(
                text, parse_mode="HTML", reply_markup=reply_markup
            )

        return

    @staticmethod
    async def cancel_exchange(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Cancel exchange conversation - ENHANCED with complete cleanup"""
        # CRITICAL: Complete state cleanup
        if context.user_data:
            # OPTIMIZATION: Invalidate exchange cache on cancellation
            invalidate_exchange_cache(context.user_data)
            context.user_data.pop("exchange_data", None)
            context.user_data.pop("exchange_session_id", None)
            context.user_data.pop("active_conversation", None)
            logger.info("✅ Complete exchange state cleanup on cancellation")
        
        # Clear universal sessions
        if update.effective_user:
            from utils.universal_session_manager import universal_session_manager, SessionType
            existing_sessions = universal_session_manager.get_user_session_ids(
                update.effective_user.id, SessionType.DIRECT_EXCHANGE
            )
            for session_id in existing_sessions:
                universal_session_manager.terminate_session(session_id, "exchange_cancelled")

        text = "❌ Exchange cancelled."
        message = update.message
        if message:
            await message.reply_text(text)
        
        # Return to main menu after cancellation
        from handlers.start import show_main_menu
        await show_main_menu(update, context)
        return

    @staticmethod
    async def handle_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle text input based on current exchange state"""
        if not context.user_data or context.user_data.get("active_conversation") != "exchange":
            return
        
        state = context.user_data.get("exchange_state")
        
        if state == "entering_amount":
            await ExchangeHandler.process_amount(update, context)
        elif state == "entering_bank":
            await ExchangeHandler.process_bank_verification(update, context)
        elif state == "entering_wallet":
            await ExchangeHandler.process_wallet_address(update, context)

    @staticmethod
    async def handle_main_menu_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle main menu navigation from exchange conversation - ENHANCED"""
        # CRITICAL: Complete state cleanup before routing to main menu
        if context.user_data:
            # OPTIMIZATION: Invalidate exchange cache on navigation away
            invalidate_exchange_cache(context.user_data)
            context.user_data.pop("exchange_data", None)
            context.user_data.pop("exchange_session_id", None)
            context.user_data.pop("active_conversation", None)
            logger.info("✅ Complete exchange state cleanup on main menu fallback")
        
        # Clear universal sessions
        if update.effective_user:
            from utils.universal_session_manager import universal_session_manager, SessionType
            existing_sessions = universal_session_manager.get_user_session_ids(
                update.effective_user.id, SessionType.DIRECT_EXCHANGE
            )
            for session_id in existing_sessions:
                universal_session_manager.terminate_session(session_id, "main_menu_navigation")
        
        from handlers.start import show_main_menu
        await show_main_menu(update, context)
        return

    @staticmethod
    async def handle_payment_processing_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Enhanced payment processing callbacks with improved UX"""
        query = update.callback_query
        if not query or not query.data:
            return
        
        await safe_answer_callback_query(query, "")
        
        if query.data == "exchange_cancel":
            # Simple cancellation during payment processing
            logger.info("Payment processing: Exchange cancellation requested by user")
            
            # Clear exchange data
            if context.user_data and "exchange_data" in context.user_data:
                # OPTIMIZATION: Invalidate exchange cache on payment cancellation
                invalidate_exchange_cache(context.user_data)
                context.user_data.pop("exchange_data", None)
                logger.info("Cleared exchange data on payment cancellation")
            
            # Show cancellation confirmation
            message = "❌ Exchange cancelled during payment processing.\n\nAny pending transactions will be refunded automatically.\n\nReturning to main menu..."
            
            await query.edit_message_text(message)
            
            # Navigate to main menu
            from handlers.start import show_main_menu
            await show_main_menu(update, context)
            return
            
        elif query.data == "main_menu":
            # Navigate to main menu without cancelling order
            from handlers.start import show_main_menu
            await show_main_menu(update, context)
            return
            
        elif query.data == "contact_support":
            # Handle support contact
            await query.edit_message_text(
                "📞 Need Help?\n\n"
                "Contact our support team:\n"
                f"📧 Email: {Config.SUPPORT_EMAIL}\n"
                "💬 Telegram: @lockbay_bot\n\n"
                "We typically respond within 30 minutes!",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
                ])
            )
            return
            
        # Default case - stay in processing state
        return

    @staticmethod
    async def handle_bank_switch_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle new bank account selection for switching"""
        query = update.callback_query
        if not query or not query.data:
            return
            
        await safe_answer_callback_query(query, "🏦")

        if query.data == "exchange_bank_switch_cancel":
            # PHASE 3: Check if this is pre-confirmation bank switching
            switch_context = context.user_data.get("switch_context") if context.user_data else None
            if switch_context and switch_context.get("operation_type") == "pre_confirmation_bank_switch":
                # Pre-confirmation: Return to confirmation screen
                if query:
                    await query.edit_message_text("🔄 Returning to confirmation screen...")
                return await ExchangeHandler.confirm_exchange_order(update, context)
            
            # Post-confirmation: Return to payment screen
            if switch_context and switch_context.get("current_order_id"):
                try:
                    async with async_managed_session() as session:
                        stmt = select(ExchangeOrder).where(ExchangeOrder.id == switch_context["current_order_id"])
                        result = await session.execute(stmt)
                        current_order = result.scalar_one_or_none()
                        if current_order:
                            # Return to payment instruction screen
                            await ExchangeHandler.show_payment_instructions(update, context, current_order)
                            return
                except Exception as e:
                    logger.error(f"Error returning to payment screen: {e}")
            
            # Fallback if we can't get order details
            if query:
                await query.edit_message_text(
                "✅ Keeping current bank account.\n\nUse the menu below to continue:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
                ])
            )
            return

        # Extract bank ID from callback data
        if not query.data.startswith("exchange_bank_switch_"):
            if query:
                await query.edit_message_text("❌ Invalid bank selection.")
            return

        try:
            bank_id = int(query.data.split("_")[-1])
        except (ValueError, IndexError):
            if query:
                await query.edit_message_text("❌ Invalid bank selection.")
            return

        # UNIFIED CONTEXT RETRIEVAL with enhanced error logging
        switch_context = context.user_data.get("switch_context") if context.user_data else None
        if not switch_context:
            logger.error(f"Bank switch failed: No switch context found for user {update.effective_user.id if update.effective_user else 'unknown'}")
            if query:
                await query.edit_message_text("❌ Switch session expired. Please restart.")
            return
            
        if switch_context.get("operation_type") != "bank_switch":
            logger.error(f"Bank switch failed: Wrong operation type '{switch_context.get('operation_type')}' expected 'bank_switch' for user {update.effective_user.id if update.effective_user else 'unknown'}")
            if query:
                await query.edit_message_text("❌ Invalid switch context. Please restart.")
            return

        async with async_managed_session() as session:
            try:
                user_id = update.effective_user.id if update.effective_user else 0
                stmt = select(User).where(User.telegram_id == normalize_telegram_id(user_id))
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()
                if not user:
                    if query:
                        await query.edit_message_text("❌ User not found.")
                    return

                # Verify the selected bank belongs to the user
                from models import SavedBankAccount
                stmt = select(SavedBankAccount).where(
                    SavedBankAccount.id == bank_id,
                    SavedBankAccount.user_id == user.id
                )
                result = await session.execute(stmt)
                selected_bank = result.scalar_one_or_none()

                if not selected_bank:
                    if query:
                        await query.edit_message_text("❌ Invalid bank account selection.")
                    return

                # Get current order
                stmt = select(ExchangeOrder).where(ExchangeOrder.id == switch_context["current_order_id"])
                result = await session.execute(stmt)
                current_order = result.scalar_one_or_none()

                if not current_order or current_order.status not in ["created", "awaiting_deposit"]:
                    if query:
                        await query.edit_message_text("❌ Cannot switch - order status changed.")
                    return

                # Security: Double-check payment not received
                if getattr(current_order, 'payment_received', False):
                    if query:
                        await query.edit_message_text("❌ Cannot switch bank - payment already received.")
                    return

                # Update order with new bank account details
                bank_details = {
                    "account_number": selected_bank.account_number,
                    "bank_code": selected_bank.bank_code,
                    "bank_name": selected_bank.bank_name,
                    "account_name": selected_bank.account_name,
                }

                # Update the order's bank information using SQLAlchemy proper assignment
                from sqlalchemy import update as sql_update
                await session.execute(
                    sql_update(ExchangeOrder).where(ExchangeOrder.id == current_order.id).values(
                        bank_account_details=json.dumps(bank_details)
                    )
                )
                await session.commit()

                logger.info(f"Switched bank account for order {current_order.id} to bank {bank_id}")

                # Show success message with updated payment details
                masked_account = f"{selected_bank.account_number[:4]}••••{selected_bank.account_number[-4:]}"
                
                success_text = f"""✅ Bank Account Switched!

📤 Pay: {current_order.source_amount} {current_order.source_currency}
📥 Receive: ₦{int(getattr(current_order, 'final_amount', None) or Decimal('0')):,}

🏦 New Bank Account:
{selected_bank.bank_name}
{masked_account} • {selected_bank.account_name}

Your crypto payment address remains the same. Continue with your payment using the same address."""

                # Return to payment screen with new bank details
                keyboard = [
                    [InlineKeyboardButton("📋 View Payment Details", callback_data="exchange_view_payment")],
                    [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)

                if query:
                    await query.edit_message_text(success_text, reply_markup=reply_markup)
                
                # Clear switch data
                if context.user_data:
                    context.user_data.pop("bank_switch_data", None)
                
                return

            except Exception as e:
                logger.error(f"Error in handle_bank_switch_selection: {e}")
                if query:
                    await query.edit_message_text("❌ Error switching bank account. Please try again.")
                return

    @staticmethod
    async def handle_switch_ngn_crypto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle crypto switching from NGN→Crypto payment screen"""
        query = update.callback_query
        if not query:
            return
            
        await safe_answer_callback_query(query, "🔄")

        # Get current exchange data
        if not context.user_data or "exchange_data" not in context.user_data:
            if query:
                await query.edit_message_text("❌ Session expired. Please restart.")
            return

        exchange_data = context.user_data["exchange_data"]
        current_crypto = exchange_data.get("crypto", "BTC")
        # FIXED: Get the NGN amount from rate_info or calculated final amount
        rate_info = exchange_data.get("rate_info", {})
        ngn_amount = rate_info.get("final_ngn_amount", exchange_data.get("final_amount", 0))
        
        # Fallback: If no NGN amount, try to calculate from crypto amount
        if not ngn_amount and exchange_data.get("amount"):
            try:
                from services.financial_gateway import financial_gateway
                crypto_amount = exchange_data.get("amount", 0)
                
                # Get current rates for conversion
                crypto_usd_rate = await financial_gateway.get_crypto_to_usd_rate(current_crypto)
                usd_ngn_rate = await financial_gateway.get_usd_to_ngn_rate_clean()
                
                if crypto_usd_rate and usd_ngn_rate:
                    # CRITICAL FIX: Calculate NGN amount based on exchange direction
                    usd_value = Decimal(str(crypto_amount or 0)) * Decimal(str(crypto_usd_rate or 0))
                    
                    # Check exchange type to apply correct calculation
                    exchange_type = exchange_data.get("type", "crypto_to_ngn")
                    
                    if exchange_type == "crypto_to_ngn":
                        # User is SELLING crypto - they get LESS NGN due to markup
                        markup_percentage = Decimal(str(getattr(Config, "EXCHANGE_MARKUP_PERCENTAGE", 5))) / Decimal("100")
                        markup_multiplier = Decimal("1") - markup_percentage  # User gets LESS
                        ngn_amount = usd_value * Decimal(str(usd_ngn_rate or 0)) * markup_multiplier
                        logger.info(f"SELLING: {crypto_amount} {current_crypto} → ₦{ngn_amount:,.2f} (after {markup_percentage*Decimal('100')}% platform fee)")
                    else:
                        # User is BUYING crypto - this is how much NGN they need to pay
                        ngn_amount = usd_value * Decimal(str(usd_ngn_rate or 0))
                        logger.info(f"BUYING: ₦{ngn_amount:,.2f} → {crypto_amount} {current_crypto}")
            except Exception as e:
                logger.error(f"Error calculating NGN amount: {e}")
                ngn_amount = 0

        # Get original exchange details for proper display - DEBUG ENHANCED
        original_exchange_type = exchange_data.get("type", "crypto_to_ngn")
                        
        # FALLBACK: Check alternative field names in case 'type' is stored differently
        if not original_exchange_type or original_exchange_type == "crypto_to_ngn":
            # Check for other possible field names
            alt_type = exchange_data.get("exchange_type") or exchange_data.get("direction") 
            if alt_type:
                original_exchange_type = alt_type
        crypto_amount = exchange_data.get("amount", 0)
        
        # Store current exchange data for switching
        # UNIFIED SWITCH CONTEXT - Standard format for all switching operations  
        context.user_data["switch_context"] = {
            "operation_type": "ngn_crypto_switch",
            "ngn_amount": ngn_amount,
            "current_crypto": current_crypto,
            "crypto_amount": crypto_amount,
            "wallet_address": exchange_data.get("wallet_address"),
            "target_currency": "NGN",
            "exchange_type": original_exchange_type,  # CRITICAL: Store exchange type for correct direction display
            "context_id": f"ngn_crypto_switch_{current_crypto}_{int(ngn_amount)}"
        }

        # FIXED: Show correct flow direction based on actual exchange type
        if original_exchange_type == "crypto_to_ngn":
            # User is selling crypto for NGN
            text = f"""🔄 Switch Cryptocurrency

Current Order: {crypto_amount} {current_crypto} → ₦{ngn_amount:,.2f}
Selling: {crypto_amount} {current_crypto}
Receiving: ₦{ngn_amount:,.2f}

Choose different crypto to sell:"""
        else:
            # User is buying crypto with NGN  
            text = f"""🔄 Switch Cryptocurrency

📤 Pay: ₦{ngn_amount:,.2f} via bank transfer
📥 Receive: {current_crypto}
Current Selection: {current_crypto}

Choose different crypto to buy:"""

        # Build crypto selection keyboard (TWO PER ROW for better mobile UX)
        cryptos = ["BTC", "ETH", "LTC", "DOGE", "TRX", "USDT-TRC20", "USDT-ERC20", "XMR"]
        keyboard = []
        
        # Build list of available cryptos (excluding current)
        available_cryptos = []
        for crypto in cryptos:
            if crypto != current_crypto:  # Skip current crypto
                emoji = {
                    "BTC": "₿", "ETH": "Ξ", "LTC": "Ł", "DOGE": "Ð",
                    "TRX": "🏛️", "XMR": "ⓧ",
                    "USDT-TRC20": "💰", "USDT-ERC20": "💰"
                }.get(crypto, "💰")
                
                available_cryptos.append({
                    "text": f"{emoji} {crypto}",
                    "callback": f"exchange_ngn_switch_{crypto}"
                })
        
        # Create keyboard with TWO buttons per row
        for i in range(0, len(available_cryptos), 2):
            row = []
            # Add first button
            row.append(InlineKeyboardButton(
                available_cryptos[i]["text"],
                callback_data=available_cryptos[i]["callback"]
            ))
            # Add second button if available
            if i + 1 < len(available_cryptos):
                row.append(InlineKeyboardButton(
                    available_cryptos[i + 1]["text"],
                    callback_data=available_cryptos[i + 1]["callback"]
                ))
            keyboard.append(row)

        keyboard.append([
            InlineKeyboardButton("❌ Cancel", callback_data="exchange_ngn_switch_cancel")
        ])

        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text, reply_markup=reply_markup)
        return

    @staticmethod
    async def handle_switch_ngn_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle wallet switching from NGN→Crypto payment screen"""
        query = update.callback_query
        if not query:
            return
            
        await safe_answer_callback_query(query, "💼")

        # Get current exchange data
        if not context.user_data or "exchange_data" not in context.user_data:
            if query:
                await query.edit_message_text("❌ Session expired. Please restart.")
            return

        exchange_data = context.user_data["exchange_data"]
        current_crypto = exchange_data.get("crypto", "BTC")
        current_wallet = exchange_data.get("wallet_address", "")

        async with async_managed_session() as session:
            try:
                user_id = update.effective_user.id if update.effective_user else 0
                stmt = select(User).where(User.telegram_id == normalize_telegram_id(user_id))
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()
                if not user:
                    if query:
                        await query.edit_message_text("❌ User not found. Please restart.")
                    return

                # Get all saved addresses for this crypto using cached method
                saved_addresses = await SavedDestinationCache.load_crypto_addresses_optimized(
                    user_id, currency=current_crypto
                )

                if len(saved_addresses) < 2:
                    if query:
                        await query.edit_message_text("❌ You need at least 2 saved wallet addresses to switch.")
                    return

                # Store switch data
                # UNIFIED SWITCH CONTEXT - Standard format for all switching operations
                context.user_data["switch_context"] = {
                    "operation_type": "wallet_switch",
                    "current_crypto": current_crypto,
                    "current_wallet": current_wallet,
                    "ngn_amount": exchange_data.get("amount", 0),
                    "target_currency": current_crypto,
                    "exchange_type": "ngn_to_crypto",
                    "context_id": f"wallet_switch_{current_crypto}_{current_wallet[:8]}"
                }

                # Show wallet selection
                text = f"""💼 Switch Wallet Address

📤 Pay: ₦{exchange_data.get('amount', 0):,.2f} via bank transfer
📥 Receive: {current_crypto}
Current Wallet: {current_wallet[:10]}...{current_wallet[-10:]}

Choose new wallet address:"""

                keyboard = []
                for addr in saved_addresses:
                    # Skip current address
                    addr_value = addr.get('address', '')
                    if addr_value == current_wallet:
                        continue
                        
                    # Format address display
                    addr_str = str(addr_value)
                    masked_addr = f"{addr_str[:10]}...{addr_str[-10:]}" if len(addr_str) > 20 else addr_str
                    label = addr.get('label') or "Unnamed Address"
                    keyboard.append([
                        InlineKeyboardButton(
                            f"💼 {label}\n{masked_addr}", 
                            callback_data=f"exchange_wallet_switch_{addr['id']}"
                        )
                    ])

                keyboard.append([
                    InlineKeyboardButton("❌ Cancel", callback_data="exchange_wallet_switch_cancel")
                ])

                reply_markup = InlineKeyboardMarkup(keyboard)
                if query:
                    await query.edit_message_text(text, reply_markup=reply_markup)
                return

            except Exception as e:
                logger.error(f"Error in handle_switch_ngn_wallet: {e}")
                if query:
                    await query.edit_message_text("❌ Error processing wallet switch. Please try again.")
                return

    @staticmethod
    async def handle_ngn_crypto_switch_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle new crypto selection for NGN switching"""
        query = update.callback_query
        if not query or not query.data:
            return
            
        # IMMEDIATE FEEDBACK: Exchange switching action
        await safe_answer_callback_query(query, "🔄 Exchange switching")

        if query.data == "exchange_ngn_switch_cancel":
            # Return to confirmation screen
            if query:
                await query.edit_message_text("🔄 Returning to confirmation screen...")
            return

        # Extract crypto from callback data
        if not query.data.startswith("exchange_ngn_switch_"):
            if query:
                await query.edit_message_text("❌ Invalid crypto selection.")
            return

        new_crypto = query.data.replace("exchange_ngn_switch_", "")
        
        # UNIFIED CONTEXT RETRIEVAL with enhanced error logging
        switch_context = context.user_data.get("switch_context") if context.user_data else None
        if not switch_context:
            logger.error(f"NGN crypto switch failed: No switch context found for user {update.effective_user.id if update.effective_user else 'unknown'}")
            if query:
                await query.edit_message_text("❌ Switch session expired. Please restart.")
            return
            
        # PHASE 3: Support both post-confirmation and pre-confirmation NGN crypto switching
        operation_type = switch_context.get("operation_type")
        if operation_type not in ["ngn_crypto_switch", "pre_confirmation_crypto_switch"]:
            logger.error(f"NGN crypto switch failed: Wrong operation type '{operation_type}' for user {update.effective_user.id if update.effective_user else 'unknown'}")
            if query:
                await query.edit_message_text("❌ Invalid switch context. Please restart.")
            return

        try:
            # Recalculate rates for new crypto
            from services.financial_gateway import financial_gateway
            
            ngn_amount = switch_context["ngn_amount"]
            
            # Get rates for new crypto
            crypto_usd_rate = await financial_gateway.get_crypto_to_usd_rate(new_crypto)
            clean_usd_ngn_rate = await financial_gateway.get_usd_to_ngn_rate_clean()
            
            if not crypto_usd_rate or not clean_usd_ngn_rate:
                if query:
                    await query.edit_message_text(f"❌ Unable to get rates for {new_crypto}. Please try again.")
                return

            # Calculate final crypto amount with markup - FIX TYPE CONVERSION ERROR
            from decimal import Decimal, ROUND_HALF_UP
            
            # Convert all values to Decimal for precise financial calculations
            ngn_amount_decimal = Decimal(str(ngn_amount))
            clean_usd_ngn_rate_decimal = Decimal(str(clean_usd_ngn_rate))
            crypto_usd_rate_decimal = Decimal(str(crypto_usd_rate))
            markup_percentage_decimal = Decimal(str(Config.EXCHANGE_MARKUP_PERCENTAGE)) / Decimal("100")
            
            # ENHANCED FIX: Robust exchange type detection with fallbacks
            exchange_type = switch_context.get("exchange_type", "")
            
            # Additional validation: check main exchange_data if switch_context is unclear
            if not exchange_type and context.user_data and "exchange_data" in context.user_data:
                exchange_type = context.user_data["exchange_data"].get("type", "")
            
            # Default to ngn_to_crypto for NGN crypto switching context
            if not exchange_type:
                exchange_type = "ngn_to_crypto"
                logger.warning("Exchange type not found in context, defaulting to ngn_to_crypto for NGN switching")
            
            logger.info(f"🔍 SWITCH DEBUG: exchange_type = '{exchange_type}' (validated)")
            logger.info(f"🔍 SWITCH DEBUG: ngn_amount = {ngn_amount}, new_crypto = {new_crypto}")
            
            if exchange_type == "crypto_to_ngn":
                # User is SELLING crypto for NGN - calculate crypto amount needed for NGN amount
                # ngn_amount is what user wants to receive, calculate crypto they need to send
                usd_equivalent = ngn_amount_decimal / clean_usd_ngn_rate_decimal
                usd_equivalent = usd_equivalent.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)
                
                # Apply markup - user needs to send MORE crypto to account for fees
                markup_multiplier = Decimal("1") + markup_percentage_decimal  # ADD markup for selling
                usd_with_markup = usd_equivalent * markup_multiplier
                usd_with_markup = usd_with_markup.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)
                
                final_crypto_amount = usd_with_markup / crypto_usd_rate_decimal
                final_crypto_amount = final_crypto_amount.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)
            else:
                # User is BUYING crypto with NGN - calculate crypto amount they receive for NGN amount
                usd_equivalent = ngn_amount_decimal / clean_usd_ngn_rate_decimal
                usd_equivalent = usd_equivalent.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)
                
                # FIX: Match main calculation logic - no markup deduction for NGN to crypto
                # Main flow (lines 778-779) doesn't apply markup deduction, so switching shouldn't either
                final_crypto_amount = usd_equivalent / crypto_usd_rate_decimal
                final_crypto_amount = final_crypto_amount.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)

            # PHASE 3: Handle pre-confirmation vs post-confirmation NGN crypto switching
            if operation_type == "pre_confirmation_crypto_switch":
                # Pre-confirmation: Update exchange data and return to confirmation
                exchange_data = context.user_data.get("exchange_data", {}) if context.user_data else {}
                
                # Update crypto in exchange data
                exchange_data["crypto"] = new_crypto
                
                # Reset wallet address for new crypto
                exchange_data["wallet_address"] = ""
                
                # Recalculate rate info with new crypto
                rate_info = {
                    "crypto_amount": final_crypto_amount,
                    "crypto_usd_rate": crypto_usd_rate_decimal,
                    "usd_ngn_rate": clean_usd_ngn_rate_decimal,
                    "final_crypto_amount": final_crypto_amount,
                    "markup_percentage": Config.EXCHANGE_MARKUP_PERCENTAGE
                }
                exchange_data["rate_info"] = rate_info
                
                # Update context
                if context.user_data:
                    context.user_data["exchange_data"] = exchange_data
                
                # Clear switch context
                if context.user_data:
                    context.user_data.pop("switch_context", None)
                
                # Show success message and note wallet address needs selection
                text = f"""✅ Crypto Switched Successfully!

Updated to: {new_crypto}
New amount: {decimal_to_string(final_crypto_amount, precision=8)} {new_crypto}

⚠️ Please select a wallet address for the new crypto to complete your order."""

                if query:
                    await query.edit_message_text(text)
                
                # Return to wallet selection since crypto changed
                import asyncio
                await asyncio.sleep(1)  # Brief pause
                return await ExchangeHandler.handle_wallet_selection(update, context)
                
            else:
                # Post-confirmation: Continue with existing logic
                # Update exchange data with new crypto
                if context.user_data and "exchange_data" in context.user_data:
                    context.user_data["exchange_data"]["crypto"] = new_crypto
                    if "rate_info" in context.user_data["exchange_data"]:
                        context.user_data["exchange_data"]["rate_info"]["crypto_amount"] = final_crypto_amount
                
                # Reset wallet address to force new selection
                if context.user_data and "exchange_data" in context.user_data:
                    context.user_data["exchange_data"]["wallet_address"] = ""

                # Show success and redirect to wallet selection
                logger.info(f"🔍 FINAL DISPLAY DEBUG: exchange_type = '{exchange_type}', condition result = {exchange_type == 'crypto_to_ngn'}")
                
                if exchange_type == "crypto_to_ngn":
                    # User is SELLING crypto for NGN - use "Send" for clarity
                    success_text = f"""✅ Crypto Switched to {new_crypto}!

📤 Send: {decimal_to_string(final_crypto_amount, precision=8)} {new_crypto}
📥 Receive: {format_money(ngn_amount, 'NGN')}

Now select a {new_crypto} wallet address to send your crypto from."""
                    logger.info("✅ SELLING PATH: Displaying Send crypto, Receive NGN")
                else:
                    # User is BUYING crypto with NGN - use "Pay" as they're purchasing  
                    success_text = f"""✅ Crypto Switched to {new_crypto}!

📤 Pay: {format_money(ngn_amount, 'NGN')}
📥 Receive: {decimal_to_string(final_crypto_amount, precision=8)} {new_crypto}

Now select a {new_crypto} wallet address to receive your crypto."""
                    logger.info("✅ BUYING PATH: Displaying Pay NGN, Receive crypto")

                keyboard = [
                    [InlineKeyboardButton("📍 Select Wallet Address", callback_data="exchange_back_to_addresses")],
                    [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)

                if query:
                    await query.edit_message_text(success_text, reply_markup=reply_markup)
                
                # Clear unified switch context
                if context.user_data:
                    context.user_data.pop("switch_context", None)
                
                return

        except Exception as e:
            logger.error(f"Error in handle_ngn_crypto_switch_selection: {e}")
            if query:
                await query.edit_message_text("❌ Error switching crypto. Please try again.")
            return

    @staticmethod
    async def handle_ngn_wallet_switch_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle new wallet selection for NGN switching"""
        query = update.callback_query
        if not query or not query.data:
            return
            
        await safe_answer_callback_query(query, "💼")

        if query.data == "exchange_wallet_switch_cancel":
            # PHASE 3: Check if this is pre-confirmation wallet switching
            switch_context = context.user_data.get("switch_context") if context.user_data else None
            if switch_context and switch_context.get("operation_type") == "pre_confirmation_wallet_switch":
                # Pre-confirmation: Return to confirmation screen
                if query:
                    await query.edit_message_text("🔄 Returning to confirmation screen...")
                return await ExchangeHandler.confirm_exchange_order(update, context)
            
            # Post-confirmation: Return to confirmation screen (existing logic)
            if query:
                await query.edit_message_text("🔄 Returning to confirmation screen...")
            return

        # Extract address ID from callback data
        if not query.data.startswith("exchange_wallet_switch_"):
            if query:
                await query.edit_message_text("❌ Invalid wallet selection.")
            return

        try:
            addr_id = int(query.data.split("_")[-1])
        except (ValueError, IndexError):
            if query:
                await query.edit_message_text("❌ Invalid wallet selection.")
            return

        # UNIFIED CONTEXT RETRIEVAL with enhanced error logging
        switch_context = context.user_data.get("switch_context") if context.user_data else None
        if not switch_context:
            logger.error(f"Wallet switch failed: No switch context found for user {update.effective_user.id if update.effective_user else 'unknown'}")
            if query:
                await query.edit_message_text("❌ Switch session expired. Please restart.")
            return
            
        if switch_context.get("operation_type") != "wallet_switch":
            logger.error(f"Wallet switch failed: Wrong operation type '{switch_context.get('operation_type')}' expected 'wallet_switch' for user {update.effective_user.id if update.effective_user else 'unknown'}")
            if query:
                await query.edit_message_text("❌ Invalid switch context. Please restart.")
            return

        async with async_managed_session() as session:
            try:
                user_id = update.effective_user.id if update.effective_user else 0
                stmt = select(User).where(User.telegram_id == normalize_telegram_id(user_id))
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()
                if not user:
                    if query:
                        await query.edit_message_text("❌ User not found.")
                    return

                # Verify the selected address belongs to the user using cached method
                crypto = switch_context["current_crypto"]
                saved_addresses = await SavedDestinationCache.load_crypto_addresses_optimized(
                    user_id, currency=crypto
                )
                selected_address = next((addr for addr in saved_addresses if addr['id'] == addr_id), None)

                if not selected_address:
                    if query:
                        await query.edit_message_text("❌ Invalid wallet address selection.")
                    return

                # Update exchange data with new wallet address
                if context.user_data and "exchange_data" in context.user_data:
                    context.user_data["exchange_data"]["wallet_address"] = selected_address['address']

                # Show success message
                ngn_amount = switch_context["ngn_amount"]
                f"{selected_address['address'][:10]}...{selected_address['address'][-10:]}"
                label = selected_address.get('label') or "Unnamed Address"
                
                success_text = f"""✅ Wallet Address Switched!

📤 Pay: ₦{ngn_amount:,.2f}
📥 Receive: {crypto}

💼 New Wallet Address:
{label}
`{selected_address['address']}`

You can now proceed to confirm your order."""

                # Return to confirmation screen
                keyboard = [
                    [InlineKeyboardButton("✅ Proceed to Confirm", callback_data="exchange_confirm_order")],
                    [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)

                if query:
                    await query.edit_message_text(success_text, parse_mode="HTML", reply_markup=reply_markup)
                
                # Clear unified switch context
                if context.user_data:
                    context.user_data.pop("switch_context", None)
                
                return

            except Exception as e:
                logger.error(f"Error in handle_ngn_wallet_switch_selection: {e}")
                if query:
                    await query.edit_message_text("❌ Error switching wallet address. Please try again.")
                return

    @staticmethod
    async def handle_pre_confirmation_crypto_switch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """PHASE 2: Handle crypto switching from confirmation screen"""
        query = update.callback_query
        # NOTE: Callback already answered in handle_confirmation() - don't answer again
        
        # Show crypto selection for pre-confirmation switching
        from utils.crypto_ui_components import CryptoUIComponents
        
        text = """🔄 Switch Cryptocurrency

💰 Select a different crypto for your exchange:
⚡ Live rates • 🔒 Secure switching"""
        
        # Use standardized crypto selection keyboard with pre-confirmation callback
        reply_markup = CryptoUIComponents.get_crypto_selection_keyboard(
            callback_prefix="exchange_pre_crypto:",
            layout="compact",
            back_callback="exchange_back_to_confirmation",
        )
        
        if query:
            await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=reply_markup)
        
        return

    @staticmethod
    async def handle_pre_confirmation_bank_switch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """PHASE 2: Handle bank switching from confirmation screen"""
        query = update.callback_query
        # NOTE: Callback already answered in handle_confirmation() - don't answer again
        
        async with async_managed_session() as session:
            try:
                if not update.effective_user:
                    if query:
                        await query.edit_message_text("❌ Authentication error.")
                    return
                    
                user_id = str(update.effective_user.id)
                stmt = select(User).where(User.telegram_id == user_id)
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()
                
                if not user:
                    if query:
                        await query.edit_message_text("❌ User not found. Please restart with /start")
                    return
                    
                # Get saved bank accounts using cached method
                saved_banks = await SavedDestinationCache.load_bank_accounts_optimized(int(user_id))
                
                text = """🔄 Switch Bank Account

🏦 Select a different bank account for your exchange:"""
                
                keyboard = []
                
                for bank in saved_banks:
                    bank_display = f"{bank['bank_name']} - {bank['account_number'][-4:]}"
                    keyboard.append([
                        InlineKeyboardButton(
                            f"🏦 {bank_display}",
                            callback_data=f"exchange_pre_bank:{bank['id']}"
                        )
                    ])
                
                # Add option to add new bank
                keyboard.append([
                    InlineKeyboardButton(
                        "➕ Add New Bank Account",
                        callback_data="exchange_pre_add_bank"
                    )
                ])
                
                # Add back button
                keyboard.append([
                    InlineKeyboardButton(
                        "⬅️ Back to Confirmation",
                        callback_data="exchange_back_to_confirmation"
                    )
                ])
                
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                if query:
                    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=reply_markup)
                
                return
                
            except Exception as e:
                logger.error(f"Error in handle_pre_confirmation_bank_switch: {e}")
                if query:
                    await query.edit_message_text("❌ Error loading bank accounts. Please try again.")
                return

    @staticmethod
    async def handle_pre_confirmation_add_bank(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """PHASE 2: Handle adding new bank from confirmation screen"""
        query = update.callback_query
        if query:
            await safe_answer_callback_query(query, "🏦 Add New Bank")
        
        # Set pre-confirmation context
            
        context.user_data["pre_confirmation_add_bank"] = True
        
        # Show bank account input screen
        text = """🏦 Add New Bank Account

💳 Enter your 10-digit bank account number:
(We'll auto-detect your bank and verify your details)

📝 Example: 0123456789"""
        
        keyboard = [
            [
                InlineKeyboardButton(
                    "⬅️ Back to Confirmation",
                    callback_data="exchange_back_to_confirmation"
                )
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if query:
            await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=reply_markup)
        
        return

    @staticmethod
    async def handle_pre_confirmation_wallet_switch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """PHASE 2: Handle wallet switching from confirmation screen (NGN to crypto)"""
        query = update.callback_query
        # NOTE: Callback already answered in handle_confirmation() - don't answer again
        
        async with async_managed_session() as session:
            try:
                if not update.effective_user:
                    if query:
                        await query.edit_message_text("❌ Authentication error.")
                    return
                    
                user_id = str(update.effective_user.id)
                stmt = select(User).where(User.telegram_id == user_id)
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()
                
                if not user:
                    if query:
                        await query.edit_message_text("❌ User not found. Please restart with /start")
                    return
                    
                # Get exchange data
                exchange_data = context.user_data.get("exchange_data", {}) if context.user_data else {}
                current_crypto = exchange_data.get("crypto", "BTC")
                
                # Get saved wallet addresses for this crypto using cached method
                saved_addresses = await SavedDestinationCache.load_crypto_addresses_optimized(
                    int(user_id), currency=current_crypto
                )
                
                text = f"""🔄 Switch {current_crypto} Wallet

💼 Select a different wallet address for your exchange:"""
                
                keyboard = []
                
                for addr in saved_addresses:
                    # Show truncated address for readability
                    addr_display = f"{addr['address'][:8]}...{addr['address'][-8:]}"
                    keyboard.append([
                        InlineKeyboardButton(
                            f"💼 {addr_display}",
                            callback_data=f"exchange_pre_wallet:{addr['id']}"
                        )
                    ])
                
                # Add option to add new wallet
                keyboard.append([
                    InlineKeyboardButton(
                        f"➕ Add New {current_crypto} Address",
                        callback_data="exchange_pre_add_wallet"
                    )
                ])
                
                # Add back button
                keyboard.append([
                    InlineKeyboardButton(
                        "⬅️ Back to Confirmation",
                        callback_data="exchange_back_to_confirmation"
                    )
                ])
                
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                if query:
                    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=reply_markup)
                
                return
                
            except Exception as e:
                logger.error(f"Error in handle_pre_confirmation_wallet_switch: {e}")
                if query:
                    await query.edit_message_text("❌ Error loading wallet addresses. Please try again.")
                return

    @staticmethod
    async def handle_pre_confirmation_add_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """PHASE 2: Handle adding new wallet from confirmation screen (NGN to crypto)"""
        query = update.callback_query
        if query:
            await safe_answer_callback_query(query, "📍 Add New Wallet")
        
        # Set pre-confirmation context
            
        exchange_data = context.user_data.get("exchange_data", {})
        current_crypto = exchange_data.get("crypto", "BTC")
        context.user_data["pre_confirmation_add_wallet"] = True
        
        # Show wallet address input screen
        from utils.crypto_ui_components import CryptoUIComponents
        address_text = CryptoUIComponents.generate_address_input_text(
            currency=current_crypto, action="Enter"
        )
        
        text = f"""📍 Add New {current_crypto} Wallet

{address_text}"""
        
        keyboard = [
            [
                InlineKeyboardButton(
                    "⬅️ Back to Confirmation",
                    callback_data="exchange_back_to_confirmation"
                )
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if query:
            await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=reply_markup)
        
        return

# CONVERTED TO DIRECT HANDLERS: No longer using ConversationHandler
# All exchange flows now use direct callback handlers with manual state management

def register_exchange_handlers(application):
    """Register all exchange-related direct handlers with access control"""
    from utils.user_access_control import require_feature_access
    
    # Apply access control to entry point handlers
    start_exchange_with_access = require_feature_access("exchange")(ExchangeHandler.start_exchange)
    
    # Entry point handlers with access control
    application.add_handler(CallbackQueryHandler(start_exchange_with_access, pattern="^start_exchange$"))
    application.add_handler(CallbackQueryHandler(start_exchange_with_access, pattern="^direct_exchange$"))
    
    # Apply access control to all main exchange handlers
    select_exchange_type_with_access = require_feature_access("exchange")(ExchangeHandler.select_exchange_type)
    select_crypto_with_access = require_feature_access("exchange")(ExchangeHandler.select_crypto)
    
    # Main exchange flow handlers with access control
    application.add_handler(CallbackQueryHandler(select_exchange_type_with_access, pattern="^exchange_(crypto_to_ngn|ngn_to_crypto|history|help)$"))
    
    # Crypto selection handlers with access control
    application.add_handler(CallbackQueryHandler(select_crypto_with_access, pattern="^exchange_select_crypto:"))
    application.add_handler(CallbackQueryHandler(select_crypto_with_access, pattern="^exchange_crypto:"))
    
    # Navigation handlers
    application.add_handler(CallbackQueryHandler(ExchangeHandler.show_crypto_selection, pattern="^exchange_back_crypto$"))
    application.add_handler(CallbackQueryHandler(ExchangeHandler.ask_amount, pattern="^exchange_back_amount$"))
    
    # Bank selection handlers  
    application.add_handler(CallbackQueryHandler(ExchangeHandler.handle_bank_selection, pattern="^exchange_(use_saved_bank|use_bank:|different_bank)"))
    application.add_handler(CallbackQueryHandler(ExchangeHandler.handle_bank_verification_callback, pattern="^exchange_(confirm_bank|select_bank|retry_bank)"))
    
    # Wallet selection handlers
    application.add_handler(CallbackQueryHandler(ExchangeHandler.handle_wallet_selection, pattern="^exchange_(use_address:|new_address|back_to_addresses)$"))
    
    # Order confirmation handlers
    application.add_handler(CallbackQueryHandler(ExchangeHandler.handle_confirmation, pattern="^exchange_(confirm_order|edit_|back_to_confirmation|add_wallet|cancel)$"))
    application.add_handler(CallbackQueryHandler(ExchangeHandler.handle_confirmation, pattern="^exchange_crypto_switch_pre$"))
    application.add_handler(CallbackQueryHandler(ExchangeHandler.handle_confirmation, pattern="^exchange_add_bank_pre$"))
    
    # Switching handlers
    application.add_handler(CallbackQueryHandler(ExchangeHandler.handle_crypto_switch_selection, pattern="^exchange_switch_"))
    application.add_handler(CallbackQueryHandler(ExchangeHandler.handle_pre_confirmation_crypto_selection, pattern="^exchange_pre_crypto:"))
    application.add_handler(CallbackQueryHandler(ExchangeHandler.handle_bank_switch_selection, pattern="^exchange_bank_switch_"))
    application.add_handler(CallbackQueryHandler(ExchangeHandler.handle_ngn_crypto_switch_selection, pattern="^exchange_ngn_switch_"))
    application.add_handler(CallbackQueryHandler(ExchangeHandler.handle_ngn_wallet_switch_selection, pattern="^exchange_wallet_switch_"))
    
    # Address and bank save handlers
    application.add_handler(CallbackQueryHandler(ExchangeHandler.handle_save_address_prompt, pattern="^exchange_(save_address|skip_save|new_address)$"))
    application.add_handler(CallbackQueryHandler(ExchangeHandler.handle_save_bank_prompt, pattern="^exchange_(save_bank|skip_bank_save|retry_bank)$"))
    
    # Payment processing handlers (contact_support removed to prevent conflicts)
    application.add_handler(CallbackQueryHandler(ExchangeHandler.handle_payment_processing_callbacks, pattern="^(main_menu|exchange_cancel)$"))
    
    # TEXT HANDLING REMOVED: Now handled by unified text router in main.py
    # ExchangeHandler.handle_text_input is registered with unified router instead

# Add text handler method to existing ExchangeHandler class above
# (This method should be added to the main ExchangeHandler class)

# Handler instance for callback registration  
direct_exchange_handler = ExchangeHandler()
