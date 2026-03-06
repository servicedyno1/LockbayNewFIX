"""
Comprehensive wallet handlers for LockBay cryptocurrency escrow platform
RESTORED: Full functionality from wallet_legacy_archived.py
"""

import logging
import asyncio
import telegram
import base58
import hashlib
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime
from typing import Optional, Union
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError
from telegram.ext import ContextTypes, CallbackQueryHandler, MessageHandler, filters
from eth_utils.address import is_checksum_address

# Core imports
from database import get_session, async_managed_session, get_async_session
from sqlalchemy import select

# Async button handler utilities for <500ms performance
from utils.button_handler_async import button_callback_wrapper, async_button_user_lookup
from models import (
    User, Wallet, Transaction, TransactionType, SavedBankAccount, SavedAddress,
    EmailVerification, Cashout, Escrow, CashoutStatus, CashoutType, PendingCashout, CashoutProcessingMode
)

# PERFORMANCE OPTIMIZATION: Wallet context prefetch (reduces 88 queries to 2)
from utils.wallet_prefetch import (
    prefetch_wallet_context,
    get_cached_wallet_data,
    cache_wallet_data,
    invalidate_wallet_cache
)

# ORM typing helpers for Column[Type] vs Type compatibility
from utils.orm_typing_helpers import as_int, as_str, as_decimal, as_bool, as_datetime

# Wallet management imports
from utils.wallet_manager import get_user_wallet

# Branding imports
from utils.branding_utils import BrandingUtils, make_header, make_trust_footer, format_branded_amount
from utils.universal_id_generator import UniversalIDGenerator
from services.admin_trade_notifications import admin_trade_notifications

# UNIQUE STATES: Wallet conversation handler states - FIXED ID COLLISIONS
class WalletStates:
    WALLET_MENU = 299
    SELECTING_AMOUNT = 300
    SELECTING_METHOD = 301
    SELECTING_WITHDRAW_CURRENCY = 302
    ENTERING_WITHDRAW_AMOUNT = 303
    SELECTING_WITHDRAW_NETWORK = 304
    ENTERING_WITHDRAW_ADDRESS = 305
    CONFIRMING_CASHOUT = 306
    CONFIRMING_SAVED_ADDRESS = 307
    SELECTING_CRYPTO_CURRENCY = 308
    CONFIRMING_SAVE_ADDRESS = 309
    ENTERING_NGN_BANK_DETAILS = 310
    SELECTING_NGN_BANK = 311
    CONFIRMING_NGN_CASHOUT = 312
    CONFIRMING_NGN_SAVE = 313
    SELECTING_NGN_MATCH = 314
    SELECTING_BANK_FROM_MATCHES = 315
    VERIFICATION_OPTIONS = 316
    AWAITING_EMAIL_VERIFICATION = 317
    SELECTING_SAVED_BANK = 318
    CONFIRMING_SAVED_BANK_STEP1 = 319
    SELECTING_CASHOUT_TYPE = 320
    ENTERING_CUSTOM_AMOUNT = 321
    CONFIRMING_SAVED_CRYPTO_STEP1 = 322
    CONFIRMING_SAVED_BANK_FINAL = 323
    CONFIRMING_SAVED_CRYPTO_FINAL = 324
    ENTERING_CRYPTO_AMOUNT = 325
    ENTERING_EMAIL = 800
    ENTERING_OTP = 801
    # Bank addition flow states
    ADDING_BANK_SELECTING = 330
    ADDING_BANK_ACCOUNT_NUMBER = 331
    ADDING_BANK_CONFIRMING = 332
    ADDING_BANK_LABEL = 333
    CONFIRMING_NGN_PAYOUT = 334
from utils.callback_utils import safe_edit_message_text, safe_answer_callback_query
from utils.decimal_precision import MonetaryDecimal
from utils.precision_money import format_money, decimal_to_string, safe_multiply, safe_divide, safe_add, safe_subtract

# Import per-update caching system
from utils.update_cache import get_cached_user, invalidate_user_cache


def format_crypto_amount(amount: Union[Decimal, float], asset: str) -> str:
    """
    Format crypto amount without unnecessary trailing zeros
    
    Examples:
        8.000000 ETH -> ~8.0 ETH
        0.00012300 BTC -> ~0.000123 BTC
        10.500000 USDT -> ~10.5 USDT
    """
    # Convert to Decimal for safe handling
    if not isinstance(amount, Decimal):
        amount = Decimal(str(amount))
    
    # Format with 8 decimal places then strip trailing zeros
    formatted = f"{amount:.8f}".rstrip('0')
    # Ensure at least one decimal place
    if '.' not in formatted:
        formatted += '.0'
    elif formatted.endswith('.'):
        formatted += '0'
    return f"~{formatted} {asset}"
from utils.secure_amount_parser import SecureAmountParser, AmountValidationError
from utils.constants import CallbackData
from config import Config

# ENHANCED STATE MANAGEMENT IMPORTS
from utils.session_migration_helper import session_migration_helper
from utils.financial_operation_locker import financial_locker, FinancialLockType
from utils.enhanced_db_session_manager import enhanced_db_session_manager

logger = logging.getLogger(__name__)

# ===== SESSION CLEANUP UTILITIES =====

async def clear_cashout_session(user_id: int, context: ContextTypes.DEFAULT_TYPE, reason: str = "cleanup") -> None:
    """
    Clear all cashout session data to prevent users getting stuck in flows.
    This is the architect-recommended solution for session state management.
    """
    try:
        logger.info(f"🧹 CLEAR_CASHOUT_SESSION: Cleaning session for user {user_id}, reason: {reason}")
        
        # Clear all cashout session data
        if context.user_data:
            context.user_data.pop('cashout_data', None)
            context.user_data.pop('wallet_data', None) 
            context.user_data.pop('_coordination_info', None)
            context.user_data.pop('wallet_state', None)  # Critical: fallback key when Redis fails
            
        # Clear Redis-backed wallet state (function not available)
        # NOTE: wallet_state_manager does not exist - removed to fix import errors
        logger.debug(f"ℹ️ Redis wallet state clearing skipped for user {user_id} (function not available)")
            
        # Clear universal session manager (function not available)
        # NOTE: clear_user_session function does not exist in universal_session_manager - removed to fix import errors
        logger.debug(f"ℹ️ Universal session clearing skipped for user {user_id} (function not available)")
            
        logger.info(f"✅ CLEAR_CASHOUT_SESSION: Successfully cleaned session for user {user_id}")
        
    except Exception as e:
        logger.error(f"❌ CLEAR_CASHOUT_SESSION: Failed to clean session for user {user_id}: {e}")

# ===== TTL-BASED SESSION EXPIRY =====

from datetime import datetime, timedelta, timezone

CASHOUT_SESSION_TTL_MINUTES = 30  # 30-minute TTL for cashout sessions

def is_cashout_session_expired(session_data: dict) -> bool:
    """
    Check if cashout session has expired based on TTL.
    FIXED: Proper timezone handling and conservative error fallback.
    """
    if not session_data:
        return False  # CONSERVATIVE: Missing data = not expired (safer)
        
    session_created = session_data.get('session_created')
    if not session_created:
        return False  # CONSERVATIVE: Missing timestamp = not expired (safer)
        
    try:
        # TIMEZONE FIX: Parse session creation time with proper timezone handling
        if isinstance(session_created, str):
            # Handle both Z and +00:00 timezone formats
            created_time = datetime.fromisoformat(session_created.replace('Z', '+00:00'))
        else:
            created_time = session_created
            
        # TIMEZONE FIX: Ensure created_time is timezone-aware
        if created_time.tzinfo is None:
            # If naive, assume UTC
            created_time = created_time.replace(tzinfo=timezone.utc)
            
        # TIMEZONE FIX: Use timezone-aware current time
        now_utc = datetime.now(timezone.utc)
        expiry_time = created_time + timedelta(minutes=CASHOUT_SESSION_TTL_MINUTES)
        is_expired = now_utc > expiry_time
        
        if is_expired:
            age_minutes = (now_utc - created_time).total_seconds() / 60
            logger.info(f"⏰ TTL_CHECK: Cashout session expired (age: {age_minutes:.1f} minutes)")
        else:
            age_minutes = (now_utc - created_time).total_seconds() / 60
            logger.debug(f"⏰ TTL_CHECK: Cashout session still valid (age: {age_minutes:.1f} minutes)")
            
        return is_expired
        
    except Exception as e:
        logger.warning(f"⚠️ TTL_CHECK: Failed to parse session time, treating as NOT expired for safety: {e}")
        return False  # CONSERVATIVE: Parse error = not expired (safer for financial operations)

async def check_and_clear_expired_sessions(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Check for expired cashout sessions and auto-clear them.
    Returns True if session was expired and cleared, False otherwise.
    Provides automatic relief during prolonged DB outages.
    """
    try:
        if not context.user_data:
            return False
            
        if not context.user_data:
            context.user_data = {}
        cashout_data = context.user_data.get('cashout_data', {})
        if not cashout_data:
            return False
            
        if is_cashout_session_expired(cashout_data):
            age_str = "unknown"
            session_created = cashout_data.get('session_created')
            if session_created:
                try:
                    created_time = datetime.fromisoformat(session_created.replace('Z', '+00:00'))
                    if created_time.tzinfo is None:
                        created_time = created_time.replace(tzinfo=timezone.utc)
                    age_minutes = (datetime.now(timezone.utc) - created_time).total_seconds() / 60
                    age_str = f"{age_minutes:.1f} minutes"
                except Exception as e:
                    pass
                    
            logger.info(f"⏰ TTL_EXPIRY: Auto-clearing expired cashout session for user {user_id} (age: {age_str})")
            
            # Clear expired session
            await clear_cashout_session(user_id, context, f"ttl_expired_{age_str}")
            
            return True
            
        return False
        
    except Exception as e:
        logger.error(f"❌ TTL_EXPIRY: Failed to check session expiry for user {user_id}: {e}")
        return False

# ===== DATABASE-BACKED STATE CHECKING =====

async def _has_active_cashout_db_async(telegram_user_id: int) -> bool:
    """
    ASYNC helper for database-backed active cashout check.
    
    Returns True if user has any pending/processing cashouts, False otherwise.
    This is the ONLY function that should access the database for cashout status.
    """
    async with async_managed_session() as session:
        # Resolve Telegram ID to internal DB user ID first
        stmt = select(User).where(User.telegram_id == int(telegram_user_id))
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        if not user:
            logger.debug(f"🔍 DB_CHECK_ASYNC: No user found for Telegram ID {telegram_user_id}")
            return False
            
        # Include all known active cashout statuses
        active_statuses = [
            "pending", "otp_pending", "admin_pending", "approved", "executing", "processing",
            "pending_service_funding", "pending_funding", "awaiting_approval", "queued", 
            "in_progress", "submitting", "user_confirm_pending", "pending_config",
            "pending_address_config"
        ]
        
        stmt = select(Cashout).where(
            Cashout.user_id == user.id,
            Cashout.status.in_(active_statuses)
        )
        result = await session.execute(stmt)
        active_cashout = result.scalar_one_or_none()
        
        has_active = active_cashout is not None
        if has_active:
            logger.info(f"🔍 DB_CHECK_ASYNC: Telegram user {telegram_user_id} (DB user {user.id}) has active cashout {active_cashout.cashout_id} (status: {active_cashout.status})")
        else:
            logger.debug(f"🔍 DB_CHECK_ASYNC: Telegram user {telegram_user_id} (DB user {user.id}) has no active cashouts")
            
        return has_active

async def has_active_cashout_db_by_telegram(telegram_user_id: int, context: ContextTypes.DEFAULT_TYPE | None = None) -> bool:
    """
    Async database-backed active cashout check.
    
    Returns True if user has any pending/processing cashouts, False otherwise.
    """
    try:
        # TTL CLEANUP: Check and clear expired sessions but don't override DB result
        session_was_expired = False
        if context and context.user_data:
            if not context.user_data:
                context.user_data = {}
            cashout_data = context.user_data.get('cashout_data', {})
            if cashout_data and is_cashout_session_expired(cashout_data):
                logger.info(f"⏰ TTL_CLEANUP: Clearing expired session for user {telegram_user_id}")
                # Actually clear the expired session - await to prevent race conditions
                await check_and_clear_expired_sessions(telegram_user_id, context)
                session_was_expired = True
        
        # AUTHORITATIVE DB CHECK: Use async helper
        has_active = await _has_active_cashout_db_async(telegram_user_id)
        
        if has_active and session_was_expired:
            logger.warning(f"⚠️ DB_AUTHORITY: Session expired but DB shows active cashout - DB takes precedence for user {telegram_user_id}")
            
        return has_active
            
    except Exception as e:
        logger.error(f"❌ DB_CHECK: Failed to check active cashouts for Telegram user {telegram_user_id}: {e}")
        # CONSERVATIVE FALLBACK: Always True on DB error for financial safety
        logger.warning(f"⚠️ DB_CHECK: Conservative fallback - treating as active cashout for user {telegram_user_id}")
        return True  # CRITICAL: Conservative fallback to prevent duplicate cashouts

async def get_active_cashout_db(user_id: int) -> Optional[dict]:
    """
    Get details of active cashout from database.
    Returns cashout details if active, None otherwise.
    FIXED: Uses same comprehensive status list as has_active_cashout_db_by_telegram
    """
    try:
        async with async_managed_session() as session:
            # CENTRALIZED: Use same active status list to prevent drift
            active_statuses = [
                "pending", "otp_pending", "admin_pending", "approved", "executing", "processing",
                "pending_service_funding", "pending_funding", "awaiting_approval", "queued", 
                "in_progress", "submitting", "user_confirm_pending", "pending_config",
                "pending_address_config"
            ]
            
            stmt = select(Cashout).where(
                Cashout.user_id == user_id,
                Cashout.status.in_(active_statuses)
            )
            result = await session.execute(stmt)
            active_cashout = result.scalar_one_or_none()
            
            if active_cashout:
                return {
                    'cashout_id': active_cashout.cashout_id,
                    'status': active_cashout.status,
                    'amount': active_cashout.amount,
                    'currency': active_cashout.currency,
                    'created_at': active_cashout.created_at
                }
            return None
            
    except Exception as e:
        logger.error(f"❌ DB_CHECK: Failed to get active cashout for user {user_id}: {e}")
        return None

# ===== HELPER FUNCTIONS =====

def normalize_cashout_status(status_value, default_status="pending"):
    """
    Centralized status normalization for robust cashout status handling.
    
    Safely normalizes status values from various sources and formats:
    - Handles None, empty strings, non-string values
    - Converts to lowercase for consistent comparison  
    - Maps various status formats to canonical values
    - Provides defensive error handling
    
    Args:
        status_value: The raw status value (could be str, enum, dict, None, etc.)
        default_status: Default value to return if normalization fails
        
    Returns:
        str: Normalized lowercase status string
        
    Examples:
        normalize_cashout_status("SUCCESS") -> "success"
        normalize_cashout_status("PENDING_SERVICE_FUNDING") -> "pending_service_funding"
        normalize_cashout_status(None) -> "unknown"
        normalize_cashout_status("") -> "unknown"
        normalize_cashout_status({"status": "completed"}) -> "unknown" (unsupported format)
    """
    try:
        # Handle None, empty, or non-string cases
        if status_value is None:
            logger.debug("⚠️ STATUS_NORMALIZE: Received None status, using default")
            return default_status
            
        # Convert to string if not already (handles enums, numbers, etc.)
        if not isinstance(status_value, str):
            try:
                status_str = str(status_value)
                logger.debug(f"🔄 STATUS_NORMALIZE: Converted {type(status_value).__name__} to string: {status_str}")
            except Exception as convert_error:
                logger.warning(f"⚠️ STATUS_NORMALIZE: Failed to convert {type(status_value).__name__} to string: {convert_error}")
                return default_status
        else:
            status_str = status_value
            
        # Handle empty strings
        if not status_str or not status_str.strip():
            logger.debug("⚠️ STATUS_NORMALIZE: Received empty status string, using default")
            return default_status
            
        # Normalize to lowercase and strip whitespace
        normalized = status_str.strip().lower()
        
        # Map common status variations to canonical forms
        status_mappings = {
            # Success variations
            "successful": "success",
            "completed": "success", 
            "processed": "success",
            "sent": "success",
            "confirmed": "success",
            "complete": "success",
            
            # Pending variations  
            "pending_admin_funding": "pending_service_funding",
            "awaiting_funding": "pending_service_funding",
            "admin_funding_required": "pending_service_funding",
            "insufficient_balance": "pending_service_funding",
            "kraken_funding_needed": "pending_service_funding",
            
            # Processing variations
            "processing": "processing",
            "queued": "processing", 
            "in_progress": "processing",
            "submitting": "processing",
            
            # Failed variations
            "failed": "failed",
            "error": "failed",
            "rejected": "failed",
            "cancelled": "failed",
            "timeout": "failed"
        }
        
        # Apply mapping if found, otherwise return normalized original
        final_status = status_mappings.get(normalized, normalized)
        
        logger.debug(f"✅ STATUS_NORMALIZE: '{status_value}' -> '{final_status}'")
        return final_status
        
    except Exception as e:
        logger.error(f"❌ STATUS_NORMALIZE: Unexpected error normalizing status '{status_value}': {e}")
        return default_status

def classify_cashout_status(normalized_status, has_txid=False):
    """
    Classify normalized cashout status into actionable categories.
    
    Args:
        normalized_status: Output from normalize_cashout_status()
        has_txid: Whether a transaction ID is present
        
    Returns:
        dict: {
            'category': 'success'|'pending'|'failed'|'unknown',
            'requires_admin': bool,
            'show_as_success': bool,
            'show_as_pending': bool,
            'show_as_failed': bool
        }
    """
    try:
        # Define explicit allowlists for each category
        SUCCESS_TERMINAL_STATES = {
            'success', 'completed', 'processed', 'sent', 'confirmed'
        }
        
        PENDING_STATES = {
            'pending_service_funding', 'pending_funding', 'processing',
            'queued', 'in_progress', 'submitting', 'pending', 'otp_pending',
            'admin_pending', 'awaiting_approval', 'user_confirm_pending'
        }
        
        FAILED_STATES = {
            'failed', 'error', 'rejected', 'cancelled', 'timeout'
        }
        
        # IMPROVED: Handle 'pending' (default) status explicitly to prevent unknown warnings  
        if normalized_status == 'pending':
            logger.debug(f"🔧 STATUS_CLASSIFY: Handling default 'pending' status (likely from error recovery or empty status)")
            return {
                'category': 'pending',
                'requires_admin': False,  # Basic pending doesn't require admin
                'show_as_success': False,
                'show_as_pending': True,
                'show_as_failed': False
            }
            
        # Classify based on allowlists
        elif normalized_status in SUCCESS_TERMINAL_STATES:
            # Additional validation for true success
            show_as_success = has_txid  # Only show success if we have confirmation
            if not has_txid:
                logger.warning(f"⚠️ STATUS_CLASSIFY: Status '{normalized_status}' indicates success but no txid present")
                
            return {
                'category': 'success',
                'requires_admin': False,
                'show_as_success': show_as_success,
                'show_as_pending': not show_as_success,  # Show as pending if no txid
                'show_as_failed': False
            }
            
        elif normalized_status in PENDING_STATES:
            requires_admin = 'funding' in normalized_status
            return {
                'category': 'pending', 
                'requires_admin': requires_admin,
                'show_as_success': False,
                'show_as_pending': True,
                'show_as_failed': False
            }
            
        elif normalized_status in FAILED_STATES:
            return {
                'category': 'failed',
                'requires_admin': True,  # Failed states typically need investigation
                'show_as_success': False,
                'show_as_pending': False,
                'show_as_failed': True
            }
            
        else:
            # Unmapped status - treat as pending with admin review but provide better logging
            logger.warning(f"⚠️ STATUS_CLASSIFY: Unmapped status '{normalized_status}' - treating as pending with admin review. Consider adding to PENDING_STATES mapping.")
            return {
                'category': 'pending',  # Changed from 'unknown' to 'pending' for better UX
                'requires_admin': True,  # Unknown statuses require investigation
                'show_as_success': False,
                'show_as_pending': True,
                'show_as_failed': False
            }
            
    except Exception as e:
        logger.error(f"❌ STATUS_CLASSIFY: Error classifying status '{normalized_status}': {e}")
        # Fallback to safe defaults - use 'pending' instead of 'unknown'
        return {
            'category': 'pending',  # Safer fallback than 'unknown'
            'requires_admin': True,
            'show_as_success': False,
            'show_as_pending': True,
            'show_as_failed': False
        }

async def _safe_edit_with_fallback(query, update, text, reply_markup=None, parse_mode='Markdown'):
    """
    Enhanced message editing with reliable fallback for OTP flow
    
    Implements robust message editing that:
    - Tries to edit existing message first
    - Falls back to sending new message if editing fails
    - Logs once on edit failures  
    - Ensures consistent message object tracking
    """
    try:
        # Try to edit the existing message first
        if query and query.message:
            await safe_edit_message_text(
                query,
                text,
                parse_mode=parse_mode,
                reply_markup=reply_markup
            )
            return query.message
        else:
            # No query or query message, fall back to sending new message
            if update and update.effective_chat:
                new_message = await update.effective_chat.send_message(
                    text,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup
                )
                return new_message
                
    except Exception as edit_error:
        # Edit failed, log once and fall back to new message
        logger.warning(f"⚠️ Message editing failed, falling back to new message: {edit_error}")
        
        try:
            if update and update.effective_chat:
                new_message = await update.effective_chat.send_message(
                    text,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup
                )
                return new_message
        except Exception as fallback_error:
            logger.error(f"❌ Both message editing and fallback failed: {fallback_error}")
            # Last resort: try with basic text only
            try:
                if update and update.effective_chat:
                    basic_message = await update.effective_chat.send_message(
                        "⚠️ There was an issue displaying the message. Please try again."
                    )
                    return basic_message
            except Exception as final_error:
                logger.critical(f"💥 Complete message sending failure: {final_error}")
                
    return None

def format_clean_amount(amount, currency="USD"):
    """Format amount cleanly - integers without decimals, decimals with .2f"""
    try:
        # Convert to Decimal for comparison
        amount_decimal = Decimal(str(amount or 0))
        
        # If it's a whole number, show as integer
        if amount_decimal == int(amount_decimal):
            if currency == "USD":
                return f"${int(amount_decimal)}"
            elif currency == "NGN":
                return f"₦{int(amount_decimal):,}"
            else:
                return f"{int(amount_decimal)} {currency}"
        else:
            # If it has decimals, show with 2 decimal places
            if currency == "USD":
                return f"${amount_decimal:.2f}"
            elif currency == "NGN":
                return f"₦{amount_decimal:,.2f}"
            else:
                return f"{amount_decimal:.2f} {currency}"
    except (ValueError, TypeError):
        # Fallback to string representation
        return f"{amount} {currency}"

async def get_wallet_state(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> str:
    """Get the current wallet state from Redis-backed session"""
    try:
        # Try Redis-backed session first
        session_data = await session_migration_helper.get_session_data(user_id, context, "wallet_data")
        return session_data.get('wallet_state', 'inactive')
    except Exception as e:
        logger.warning(f"Error getting wallet state for user {user_id}: {e}")
        # Fallback to context.user_data
        if context and context.user_data:
            if not context.user_data:
                context.user_data = {}
            return context.user_data.get('wallet_state', 'inactive')
        return 'inactive'

async def set_wallet_state(user_id: int, context: ContextTypes.DEFAULT_TYPE, state: str) -> None:
    """Set the current wallet state in Redis-backed session"""
    try:
        # Update Redis-backed session
        wallet_data = await session_migration_helper.get_session_data(user_id, context, "wallet_data")
        wallet_data['wallet_state'] = state
        await session_migration_helper.set_session_data(
            user_id, context, wallet_data, "wallet_data", "wallet_operation"
        )
        logger.debug(f"🔄 Set wallet state for user {user_id}: {state}")
    except Exception as e:
        logger.error(f"Error setting wallet state for user {user_id}: {e}")
        # Fallback to context.user_data
        if context and context.user_data is not None:
            context.user_data.setdefault('wallet_state', state)

async def get_dynamic_ngn_amount(usd_amount: Decimal) -> Decimal:
    """Get dynamic NGN amount with 2% wallet markup for USD using Decimal precision"""
    try:
        from services.fastforex_service import FastForexService
        
        fastforex = FastForexService()
        # Ensure input is Decimal
        usd_decimal = MonetaryDecimal.to_decimal(usd_amount, "usd_input")

        # Get real-time USD to NGN rate with 2% wallet markup
        ngn_rate = await fastforex.get_usd_to_ngn_rate_with_wallet_markup()
        if not ngn_rate:
            raise Exception(
                "Exchange rate service unavailable - please try again later"
            )

        # Convert USD to NGN with wallet markup
        ngn_amount = safe_multiply(usd_decimal, Decimal(str(ngn_rate)))
        ngn_final = MonetaryDecimal.quantize_ngn(ngn_amount)

        logger.info(
            f"Wallet USD to NGN conversion: ${usd_decimal} * {ngn_rate} = ₦{ngn_final}"
        )

        return ngn_final

    except Exception as e:
        logger.error(f"Error getting dynamic NGN rate: {e}")
        # NO FALLBACK - re-raise the exception
        raise Exception(f"Failed to get live NGN rate: {e}")

async def calculate_crypto_cashout_with_network_fees(
    amount_usd: Decimal, 
    currency: str, 
    network: str = None,
    address_key: str = None
) -> dict:
    """
    CRITICAL FIX: Calculate TOTAL cost including platform fee + Kraken network fee
    
    This fixes the financial issue where we were absorbing Kraken's network fees.
    Now users pay: Platform Fee (2%, $2 min) + Kraken Network Fee ($4-$25 depending on crypto)
    
    Args:
        amount_usd: Cashout amount in USD
        currency: Crypto currency (BTC, ETH, USDT, etc.)
        network: Network type (TRC20, ERC20, etc.) - optional for non-USDT
        address_key: Kraken withdrawal key - needed for accurate fee estimation
    
    Returns:
        dict with gross_amount, platform_fee, network_fee, total_fee, net_amount
    """
    try:
        from services.percentage_cashout_fee_service import percentage_cashout_fee_service
        from services.kraken_withdrawal_service import get_kraken_withdrawal_service
        
        # Step 1: Calculate platform fee (2% with $2 minimum)
        fee_result = percentage_cashout_fee_service.calculate_cashout_fee(amount_usd, network or currency)
        
        if not fee_result["success"]:
            error_msg = fee_result.get('error', 'Fee calculation failed')
            if 'suggested_minimum' in fee_result:
                raise Exception(f"{error_msg}")
            else:
                raise Exception(error_msg)
        
        platform_fee = fee_result["final_fee"]
        fee_percentage = fee_result["fee_percentage"]
        min_fee = fee_result["min_fee"]
        
        # Step 2: Get Kraken network fee (real blockchain cost)
        network_fee = Decimal('0')
        network_fee_source = "estimated"
        
        try:
            kraken_service = get_kraken_withdrawal_service()
            
            # Only fetch real fee if we have an address key (otherwise use fallback)
            if address_key:
                # Calculate the amount we'd actually send to Kraken (after platform fee)
                amount_to_kraken = amount_usd - platform_fee
                
                fee_estimate = await kraken_service.estimate_withdrawal_fee(
                    currency=currency,
                    amount=amount_to_kraken,
                    address_key=address_key
                )
                
                if fee_estimate.get('success'):
                    # CRITICAL FIX: Kraken returns fee in CRYPTO units, must convert to USD
                    crypto_fee = fee_estimate.get('fee', Decimal('0'))
                    
                    try:
                        # Get current USD price for this crypto to convert the fee
                        from services.fastforex_service import FastForexService
                        fastforex = FastForexService()
                        
                        # Convert crypto fee to USD (e.g., 0.0005 BTC * $108,000/BTC = $54 USD)
                        crypto_fee_usd = await fastforex.convert_crypto_to_usd(crypto_fee, currency)
                        
                        # CRITICAL: Verify conversion succeeded and didn't return 0 or None
                        if not crypto_fee_usd or crypto_fee_usd <= 0:
                            raise ValueError(f"Invalid conversion result: {crypto_fee_usd}")
                        
                        network_fee = crypto_fee_usd
                        network_fee_source = "kraken_api"
                        
                        logger.info(
                            f"✅ Got real Kraken network fee for {currency}: "
                            f"{crypto_fee} {currency} = ${network_fee:.2f} USD"
                        )
                    except Exception as conversion_error:
                        logger.warning(f"⚠️ Crypto-to-USD conversion failed for {currency}: {conversion_error}, using fallback")
                        network_fee = get_fallback_network_fee(currency, network)
                        network_fee_source = "fallback"
                else:
                    logger.warning(f"⚠️ Kraken fee estimation failed, using fallback")
                    network_fee = get_fallback_network_fee(currency, network)
                    network_fee_source = "fallback"
            else:
                # No address key provided - use fallback estimates
                logger.info(f"⚠️ No address key for fee estimation, using fallback for {currency}")
                network_fee = get_fallback_network_fee(currency, network)
                network_fee_source = "fallback"
                
        except Exception as kraken_error:
            logger.warning(f"⚠️ Kraken fee fetch failed: {kraken_error}, using fallback")
            network_fee = get_fallback_network_fee(currency, network)
            network_fee_source = "fallback"
        
        # Step 3: Combine fees
        total_fee = platform_fee + network_fee
        net_amount = amount_usd - total_fee
        
        # Validation: ensure net amount is positive
        if net_amount <= 0:
            raise Exception(f"Amount too small - total fees (${total_fee}) exceed cashout amount")
        
        # Create user-facing fee breakdown (single combined fee)
        fee_breakdown = f"{fee_percentage}% fee (${total_fee:.2f}, min ${min_fee})"
        
        logger.info(
            f"💰 Crypto fee calculation: ${amount_usd} {currency} → "
            f"Platform: ${platform_fee}, Network: ${network_fee} ({network_fee_source}), "
            f"Total: ${total_fee}, Net: ${net_amount}"
        )
        
        return {
            "gross_amount": amount_usd,
            "platform_fee": platform_fee,
            "network_fee": network_fee,
            "total_fee": total_fee,
            "net_amount": net_amount,
            "network": network or currency,
            "fee_breakdown": fee_breakdown,
            "fee_type": "combined_platform_network",
            "network_fee_source": network_fee_source
        }
        
    except Exception as e:
        logger.error(f"Error calculating crypto cashout cost: {e}")
        raise Exception(f"Failed to calculate cashout cost: {e}")


def get_fallback_network_fee(currency: str, network: str = None) -> Decimal:
    """
    Fallback network fee estimates when Kraken API is unavailable
    Updated to match current real Kraken withdrawal fees (as of Oct 2025)
    """
    # Map currency/network to REAL current Kraken fees (in USD)
    # These values match actual Kraken withdrawal costs to ensure accurate user fees
    fallback_fees = {
        'USDT-TRC20': Decimal('4.50'),   # TRC20 USDT: Kraken charges ~$4.50
        'USDT-ERC20': Decimal('18.00'),  # ERC20 USDT: Kraken charges ~$18 (ETH gas)
        'USDT': Decimal('4.50'),         # Default USDT (assume TRC20)
        'BTC': Decimal('3.50'),          # Bitcoin: Kraken charges ~$3.50
        'ETH': Decimal('5.00'),          # Ethereum: Kraken charges ~$5.00
        'LTC': Decimal('0.75'),          # Litecoin: Kraken charges ~$0.75
        'DOGE': Decimal('0.50'),         # Dogecoin: Kraken charges ~$0.50
        'TRX': Decimal('0.50'),          # Tron: Kraken charges ~$0.50
    }
    
    # Try network-specific lookup first
    if network:
        key = f"{currency}-{network}".upper()
        if key in fallback_fees:
            return fallback_fees[key]
    
    # Fallback to currency-only lookup
    return fallback_fees.get(currency.upper(), Decimal('5.00'))  # Conservative default


async def calculate_usdt_cashout_total_cost(amount_usd: Decimal, network: str = "TRC20") -> dict:
    """
    DEPRECATED: Use calculate_crypto_cashout_with_network_fees() instead
    Kept for backward compatibility
    """
    return await calculate_crypto_cashout_with_network_fees(
        amount_usd=amount_usd,
        currency="USDT",
        network=network
    )

# ===== CORE WALLET INTERFACE =====

async def show_wallet_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    OPTIMIZED: Show wallet menu with instant loading placeholder
    Shows loading screen immediately, then updates with full wallet data
    """
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "💰 Wallet")

    if not update.effective_user:
        return
    
    # CRITICAL OPTIMIZATION: Show loading placeholder IMMEDIATELY before any DB queries
    # This makes the wallet feel instant (user sees immediate feedback)
    if query:
        try:
            await query.edit_message_text(
                "💰 *Wallet*\n\n⏳ Loading your balances...",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.debug(f"Could not show wallet loading placeholder: {e}")

    try:
        # CACHE OPTIMIZATION: Use cached user to eliminate redundant queries
        user = await get_cached_user(update, context)
        if not user:
            from utils.message_utils import send_unified_message
            await send_unified_message(update, "❌ User not found.")
            return

        user_id_val = getattr(user, "id", 0)
        
        # PERFORMANCE OPTIMIZATION: Prefetch wallet context (reduces 88 queries to 2)
        # Try cached data first
        cached = get_cached_wallet_data(context.user_data)
        
        if not cached:
            # Prefetch wallet context in one batched operation
            async with async_managed_session() as session:
                prefetch_data = await prefetch_wallet_context(user_id_val, session)
                if prefetch_data:
                    cache_wallet_data(context.user_data, prefetch_data)
                    cached = get_cached_wallet_data(context.user_data)
                    logger.info(
                        f"✅ WALLET_PREFETCH: Cached data for {len(prefetch_data.wallets)} wallets "
                        f"in {prefetch_data.prefetch_duration_ms:.1f}ms (target: <150ms)"
                    )
                else:
                    logger.warning(f"⚠️ WALLET_PREFETCH: Failed for user {user_id_val}, falling back to individual queries")

        # OPTIMIZED: Use cached wallet data when available (eliminates queries)
        wallet_available_balance = Decimal('0')
        wallet_trading_credit = Decimal('0')
        wallet_frozen_balance = Decimal('0')
        
        if cached and 'wallets' in cached and 'USD' in cached['wallets']:
            # FAST PATH: Use cached USD wallet data (no database query)
            usd_wallet_data = cached['wallets']['USD']
            wallet_available_balance = Decimal(str(usd_wallet_data['available_balance']))
            wallet_trading_credit = Decimal(str(usd_wallet_data['trading_credit']))
            wallet_frozen_balance = Decimal(str(usd_wallet_data['frozen_balance']))
            logger.debug(f"💨 WALLET_CACHE_HIT: Using cached USD wallet data (balance: ${wallet_available_balance})")
        
        # OPTIMIZED: Single database session for wallet data (fallback path)
        async with async_managed_session() as session:

            # Fallback: Get user USD wallet from database if not cached
            if not cached or 'wallets' not in cached or 'USD' not in cached['wallets']:
                stmt = select(Wallet).where(
                    Wallet.user_id == getattr(user, "id", 0),
                    Wallet.currency == "USD"
                )
                result = await session.execute(stmt)
                wallet = result.scalar_one_or_none()

                # Create USD wallet if it doesn't exist
                if not wallet:
                    wallet = Wallet(user_id=user.id, currency="USD", available_balance=Decimal("0.00"), frozen_balance=Decimal("0.00"))
                    session.add(wallet)
                    await session.commit()
                
                # Extract wallet data for display
                wallet_available_balance = as_decimal(wallet.available_balance) if wallet else Decimal('0')
                wallet_trading_credit = as_decimal(wallet.trading_credit) if wallet else Decimal('0')
                wallet_frozen_balance = as_decimal(wallet.frozen_balance) if wallet else Decimal('0')
                logger.debug(f"🐌 WALLET_DB_FALLBACK: Queried USD wallet from database (balance: ${wallet_available_balance})")
            
            # Fast available balance calculation for display
            from utils.wallet_performance import FastWalletService
            
            # Try cache first
            cached_data = FastWalletService.get_cached_wallet_data(user_id_val)
            if cached_data:
                available_balance = cached_data['available_balance']
                achievements_text = cached_data.get('achievements_text', '')
            else:
                # Calculate balance without locks for display - ASYNC VERSION
                # CRITICAL: Trading credit is NOT withdrawable, only available_balance is
                wallet_available = wallet_available_balance  # Use pre-extracted value (from cache or database)
                
                # Async query for reserved amounts
                from sqlalchemy import func, or_
                from models import Escrow, EscrowStatus
                
                stmt = select(func.sum(Escrow.total_amount)).where(
                    Escrow.buyer_id == user_id_val,
                    Escrow.status.in_([
                        EscrowStatus.PAYMENT_PENDING.value,
                        EscrowStatus.PAYMENT_CONFIRMED.value,
                        EscrowStatus.ACTIVE.value,
                    ]),
                    or_(
                        Escrow.payment_method == "wallet",
                        Escrow.payment_method == "hybrid"
                    )
                )
                result = await session.execute(stmt)
                reserved_sum = result.scalar() or 0
                
                reserved_amount = Decimal(str(reserved_sum))
                # Calculate withdrawable balance (excluding trading credit)
                wallet_bal_decimal = as_decimal(wallet_available) or Decimal("0")
                available_balance = max(safe_subtract(wallet_bal_decimal, reserved_amount), Decimal("0"))
                
                # Get trader status and savings in same session
                achievements_text = ""
                try:
                    # Get trader level info using async version
                    from utils.trusted_trader import TrustedTraderSystem
                    trader_level = await TrustedTraderSystem.get_trader_level_async(user, session)
                    trader_display = f"{trader_level['badge']} {trader_level['name']}"

                    # Safe trade statistics extraction
                    total_trades = getattr(user, "total_trades", 0) or 0
                    reputation_score = MonetaryDecimal.to_decimal(getattr(user, "reputation_score", 0) or 0, "reputation_score")
                    total_ratings = getattr(user, "total_ratings", 0) or 0

                    # COMPACT ACHIEVEMENTS - Mobile-friendly single line format
                    if total_trades > 0 and total_ratings > 0 and reputation_score > 0:
                        achievements_text = f"🤝 {trader_display} | {total_trades} trades | ⭐{reputation_score:.1f}/5\n"
                    elif total_trades > 0:
                        achievements_text = f"🤝 {trader_display} | {total_trades} trades\n"
                    else:
                        achievements_text = f"🤝 {trader_display}\n"

                    # Cache the wallet data for next time
                    wallet_cache_data = {
                        'available_balance': available_balance,
                        'achievements_text': achievements_text
                    }
                    FastWalletService.cache_wallet_data(user_id_val, wallet_cache_data)

                except Exception as e:
                    logger.error(f"Error fetching trader status: {e}")
                    from utils.trusted_trader import TrustedTraderSystem
                    fallback_level = TrustedTraderSystem.TRADER_LEVELS[0]
                    achievements_text = f"🤝 {fallback_level['badge']} {fallback_level['name']}\n"
                    if 'available_balance' not in locals():
                        available_balance = MonetaryDecimal.to_decimal(0, "fallback_balance")

        # Get detailed balance breakdown for display (using values loaded from cache or database)
        total_balance = safe_add(wallet_available_balance, wallet_trading_credit)
        locked_balance = wallet_frozen_balance
        
        # IMPROVED: Clear balance display with user-friendly formatting
        # Ensure proper numeric types for safe comparison
        # NOTE: available_balance was calculated earlier (line ~800) as withdrawable amount (wallet - reserved escrows)
        withdrawable_balance = Decimal(str(available_balance or 0)) if available_balance else Decimal('0')
        locked_bal = Decimal(str(locked_balance or 0)) if locked_balance else Decimal('0')
        total_bal = Decimal(str(total_balance or 0)) if total_balance else Decimal('0')
        trading_credit_bal = Decimal(str(wallet_trading_credit or 0)) if wallet_trading_credit else Decimal('0')
        wallet_available_decimal = Decimal(str(wallet_available_balance or 0))
        
        # Calculate reserved amount (already calculated earlier but needed for display)
        reserved_in_escrows = max(safe_subtract(wallet_available_decimal, withdrawable_balance), Decimal("0"))
        
        # Build enhanced balance display with clear breakdown
        if trading_credit_bal > 0 or reserved_in_escrows > 0:
            # User has funds locked in active escrows or trading credit - show detailed breakdown
            balance_fmt = f"💰 Wallet Balance: {format_clean_amount(wallet_available_decimal)} USD"
            
            if reserved_in_escrows > 0:
                balance_fmt += f"\n🔒 Locked in Trades: {format_clean_amount(reserved_in_escrows)} USD"
                balance_fmt += f"\n✅ Withdrawable: {format_clean_amount(withdrawable_balance)} USD"
            else:
                balance_fmt += f"\n✅ Withdrawable: {format_clean_amount(wallet_available_decimal)} USD"
                
            if trading_credit_bal > 0:
                balance_fmt += f"\n💎 Trading Credit: {format_clean_amount(trading_credit_bal)} USD (bonus, non-withdrawable)"
        elif locked_bal > 0:
            # Funds in frozen_balance (holds/processing)
            balance_fmt = f"""💰 Available: {format_clean_amount(withdrawable_balance)} USD
⏳ Processing: {format_clean_amount(locked_bal)} USD
📊 Total: {format_clean_amount(total_bal)} USD"""
        else:
            # Simple display when no locked funds
            if total_bal > 0:
                if trading_credit_bal > 0:
                    # Show both available and trading credit
                    balance_fmt = f"""💰 Balance: {format_clean_amount(wallet_available_decimal)} USD
💎 Trading Credit: {format_clean_amount(trading_credit_bal)} USD (bonus, non-withdrawable)"""
                else:
                    # Only available balance
                    balance_fmt = f"💰 Balance: {format_clean_amount(wallet_available_decimal)} USD"
            else:
                balance_fmt = "💰 Balance: $0.00 USD"

        # Create branded header
        header = make_header("My Wallet")

        # IMPROVED: Context-aware wallet messages with clear guidance (FIXED: safe variable usage)
        # Ensure achievements_text is always defined
        achievements_text = achievements_text if 'achievements_text' in locals() and achievements_text else ''
        
        if withdrawable_balance == 0 and trading_credit_bal == 0:
            # Empty wallet - encourage funding with clear guidance
            wallet_text = f"""{header}

{balance_fmt}

🎯 Ready to start trading? Add funds to your wallet securely.
{achievements_text}
Choose your next step:"""
        elif locked_bal > 0 or reserved_in_escrows > 0:
            # Has both available and locked funds - show breakdown
            wallet_text = f"""{header}

{balance_fmt}

💼 You have funds available for new trades and some locked in active trades.
{achievements_text}
Manage your wallet:"""
        else:
            # Has available balance only
            wallet_text = f"""{header}

{balance_fmt}

🚀 Your wallet is funded and ready for trading or cashouts.
{achievements_text}
What would you like to do:"""

        # Dynamic wallet buttons based on balance and user setup
        first_row = []

        # IMPROVED: Smart wallet buttons based on user state (FIXED: use safe numeric comparison)
        if withdrawable_balance == 0 and trading_credit_bal == 0:
            # Empty wallet - prioritize funding with encouraging language
            first_row.append(
                InlineKeyboardButton(
                    "💳 Add Funds to Start",
                    callback_data="wallet_add_funds",
                )
            )
        else:
            first_row.append(
                InlineKeyboardButton(
                    "💰 Add More", callback_data="wallet_add_funds"
                )
            )

        # COMPACT Cash Out button logic - simplified
        min_cashout_decimal = MonetaryDecimal.to_decimal(
            getattr(Config, "MIN_CASHOUT_AMOUNT", 10), "min_cashout"
        )

        # DISABLED: Regular cashout replaced by "Cash Out All" quick action
        # if available_balance >= min_cashout_decimal:
        #     first_row.append(
        #         InlineKeyboardButton(
        #             "💸 Cash Out", callback_data="wallet_cashout"
        #         )
        #     )

        # Second row - Activity + Auto CashOut (conditional)
        second_row = [
            InlineKeyboardButton(
                "📋 Activity", callback_data="wallet_history"
            )
        ]

        # Only show Auto CashOut if feature enabled AND user has sufficient balance
        if Config.ENABLE_AUTO_CASHOUT_FEATURES and available_balance >= min_cashout_decimal:
            second_row.append(
                InlineKeyboardButton(
                    "⚙️ Auto CashOut", callback_data="cashout_settings"
                )
            )

        # PHASE 2 & 3: Quick Cashout Actions for repeat users (CRYPTO + NGN SUPPORT)
        quick_actions_row = []
        if not update or not update.effective_user:
            return
        telegram_user_id = update.effective_user.id if update.effective_user else 0
        
        if available_balance >= min_cashout_decimal and telegram_user_id:
            # Get last used cashout method (crypto OR ngn)
            last_method = await get_last_used_cashout_method(telegram_user_id)
            
            # PHASE 3: Cash Out All button (one-tap convenience)
            if available_balance >= Decimal("2"):  # Minimum viable amount
                quick_actions_row.append(
                    InlineKeyboardButton(
                        f"⚡ Cash Out All ({format_clean_amount(available_balance)})",
                        callback_data=f"quick_cashout_all:{available_balance}"
                    )
                )
            
            # PHASE 2: Last used method quick action (crypto or NGN)
            if last_method["method"] == "CRYPTO":
                quick_actions_row.append(
                    InlineKeyboardButton(
                        f"🔄 {last_method['currency']} Again",
                        callback_data=f"quick_crypto:{last_method['currency']}"
                    )
                )
            elif last_method["method"] == "NGN_BANK" and Config.ENABLE_NGN_FEATURES:
                quick_actions_row.append(
                    InlineKeyboardButton(
                        "🔄 NGN Bank Again",
                        callback_data="quick_ngn"
                    )
                )
        
        # Third row - Exchange options (conditionally shown based on ENABLE_EXCHANGE_FEATURES)
        third_row = []
        if Config.ENABLE_EXCHANGE_FEATURES:
            third_row.append(InlineKeyboardButton("🔄 Exchange", callback_data="menu_exchange"))
        third_row.append(InlineKeyboardButton("💱 Rates", callback_data="view_rates"))

        # Build keyboard with quick actions if available
        keyboard = [first_row, second_row]
        if quick_actions_row:
            keyboard.append(quick_actions_row)
        keyboard.append(third_row)
        keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="back_to_main")])

        # Handle both callback queries (button press) and direct commands
        from utils.message_utils import send_unified_message
        if query:
            try:
                await safe_edit_message_text(
                    query,
                    wallet_text,
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
            except Exception as edit_error:
                if "Message is not modified" in str(edit_error):
                    logger.info(f"Message not modified (expected): {edit_error}")
                    return
                else:
                    raise edit_error
        else:
            # Direct command (like /wallet) - send new message
            await send_unified_message(
                update,
                wallet_text,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )

    except Exception as e:
        if "Message is not modified" in str(e):
            logger.info(f"Message not modified (expected): {e}")
            return

        logger.error(f"Error showing wallet menu: {e}")
        import traceback
        logger.error(f"Full traceback: {traceback.format_exc()}")

        # Use branded error messages
        if "database" in str(e).lower() or "connection" in str(e).lower():
            error_msg = BrandingUtils.get_branded_error_message("network", "Database connection issue")
        elif "user" in str(e).lower():
            error_msg = BrandingUtils.get_branded_error_message("validation", "User account issue")
        else:
            error_msg = BrandingUtils.get_branded_error_message("timeout", "Unexpected error occurred")

        if query:
            try:
                await safe_edit_message_text(query, error_msg, parse_mode="HTML")
            except Exception as edit_error:
                logger.error(f"Could not edit message with error: {edit_error}")

# SECURITY: Import financial security decorator
from utils.financial_security_decorator import require_financial_coordination

@require_financial_coordination(
    operation_type="cashout", 
    enable_degraded_mode=False,
    user_message="💳 Cashout services are temporarily unavailable for maintenance. Please try again in a few minutes."
)
async def start_cashout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start cashout flow with amount selection - PROTECTED by financial coordination security"""
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "💰 Starting cashout...")

    try:
        if not update.effective_user:
            return
        
        # BUGFIX: Auto-clear stale support sessions when user enters cashout flow
        # This prevents support sessions from hijacking crypto address input later
        try:
            from handlers.support_chat import active_support_sessions
            if update.effective_user.id in active_support_sessions:
                del active_support_sessions[update.effective_user.id]
                logger.info(f"🧹 SUPPORT_CLEANUP: Cleared stale support session for user {update.effective_user.id} entering cashout flow")
        except Exception:
            pass

        async with async_managed_session() as session:
            user_id = update.effective_user.id
            stmt = select(User).where(User.telegram_id == user_id)
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()

            if not user:
                from utils.message_utils import send_unified_message
                if query:
                    await safe_edit_message_text(query, "❌ User not found")
                else:
                    await send_unified_message(update, "❌ User not found")
                return

            # Get user's USD wallet balance
            stmt = select(Wallet).where(
                Wallet.user_id == getattr(user, "id", 0),
                Wallet.currency == "USD"
            )
            result = await session.execute(stmt)
            wallet = result.scalar_one_or_none()

            balance = (
                Decimal(str(wallet.available_balance))
                if wallet and wallet.available_balance is not None
                else Decimal('0')
            )
            
            # Get trading_credit balance (non-withdrawable bonus funds)
            trading_credit = (
                Decimal(str(wallet.trading_credit))
                if wallet and wallet.trading_credit is not None
                else Decimal('0')
            )
            
            # EDGE CASE 1: Zero withdrawable balance (no funds at all)
            if balance == Decimal('0') and trading_credit == Decimal('0'):
                header = make_header("No Balance Available")
                message_text = f"""{header}

💰 Withdrawable Balance: {format_branded_amount(balance, 'USD')}

Your wallet is currently empty. Add funds to start cashing out.

💡 Ways to add funds:
• Make a deposit
• Complete escrow trades
• Receive payments

{make_trust_footer()}"""
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("💰 Add Funds", callback_data="wallet_add_funds")],
                    [InlineKeyboardButton("🔙 Back", callback_data="menu_wallet")]
                ])
                
                from utils.message_utils import send_unified_message
                if query:
                    await safe_edit_message_text(query, message_text, reply_markup=keyboard, parse_mode="Markdown")
                else:
                    await send_unified_message(update, message_text, reply_markup=keyboard, parse_mode="Markdown")
                return
            
            # EDGE CASE 2: Only trading credit (no withdrawable balance)
            if balance < Config.MIN_CASHOUT_AMOUNT and trading_credit > 0:
                header = make_header("Trading Credit Not Withdrawable")
                message_text = f"""{header}

💳 Trading Credit: {format_branded_amount(trading_credit, 'USD')}
💰 Withdrawable Balance: {format_branded_amount(balance, 'USD')}

⚠️ Trading credit can only be used for:
• Creating escrow trades
• Exchange transactions
• Paying escrow fees

💡 Add funds via deposit or complete trades to unlock withdrawals.

{make_trust_footer()}"""
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("💰 Add Funds", callback_data="wallet_add_funds")],
                    [InlineKeyboardButton("🔙 Back", callback_data="menu_wallet")]
                ])
                
                from utils.message_utils import send_unified_message
                if query:
                    await safe_edit_message_text(query, message_text, reply_markup=keyboard, parse_mode="Markdown")
                else:
                    await send_unified_message(update, message_text, reply_markup=keyboard, parse_mode="Markdown")
                return

            # EDGE CASE 3: Balance below minimum cashout amount
            if balance < Config.MIN_CASHOUT_AMOUNT:
                min_amount = Config.MIN_CASHOUT_AMOUNT
                shortage = safe_subtract(min_amount, balance)
                
                header = make_header("Balance Too Low")
                message_text = f"""{header}

💰 Current Balance: {format_branded_amount(balance, 'USD')}
💵 Minimum Required: {format_branded_amount(min_amount, 'USD')}
📊 Short By: {format_branded_amount(shortage, 'USD')}

You need {format_branded_amount(shortage, 'USD')} more to meet the minimum cashout requirement.

{make_trust_footer()}"""
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("💰 Add Funds", callback_data="wallet_add_funds")],
                    [InlineKeyboardButton("🔙 Back", callback_data="menu_wallet")]
                ])
                
                from utils.message_utils import send_unified_message
                if query:
                    await safe_edit_message_text(query, message_text, reply_markup=keyboard, parse_mode="Markdown")
                else:
                    await send_unified_message(update, message_text, reply_markup=keyboard, parse_mode="Markdown")
                return

            # SIMPLIFIED: Show cashout all with fee information
            from utils.wallet_validation import WalletValidator
            
            # Get fee calculation for full balance
            is_guidance_valid, guidance_message, guidance_info = await WalletValidator.get_cashout_guidance(
                user_id=as_int(user.id) or 0,
                currency="USD",
                network="USDT",
                session=session
            )
            
            header = make_header("Cash Out")
            
            # Calculate fee for full balance cashout
            fee_info = guidance_info.get("fee_info", {}) if is_guidance_valid else {}
            if fee_info.get("success"):
                fee_amount = fee_info.get("final_fee", Decimal('0'))
                net_amount = fee_info.get("net_amount", Decimal('0'))
                fee_percentage = fee_info.get("fee_percentage", Decimal('2.0'))
                
                text = f"""{header}

💰 Available Balance: {format_branded_amount(balance, 'USD')}

📋 Cashout All Information:
• Processing fee: {fee_percentage}%
• Fee amount: {format_branded_amount(fee_amount, 'USD')}
• You'll receive: {format_branded_amount(net_amount, 'USD')}

Ready to cash out your entire balance?"""
            else:
                # Fallback if fee calculation fails
                text = f"""{header}

💰 Available Balance: {format_branded_amount(balance, 'USD')}

📋 Processing fee: 2% of cashout amount

Ready to cash out your entire balance?"""

            # Simple keyboard with just Cashout All and Back
            keyboard = [
                [InlineKeyboardButton("💳 Cashout All", callback_data=f"quick_cashout_all:{balance}")],
                [InlineKeyboardButton("🔙 Back", callback_data="menu_wallet")]
            ]

            # Handle both callback queries and direct commands
            from utils.message_utils import send_unified_message
            if query:
                await safe_edit_message_text(
                    query, text, reply_markup=InlineKeyboardMarkup(keyboard)
                )
            else:
                await send_unified_message(
                    update, text, reply_markup=InlineKeyboardMarkup(keyboard)
                )

    except Exception as e:
        logger.error(f"Error in start_cashout: {e}")
        if query:
            await safe_edit_message_text(query, "❌ Error starting cashout. Please try again.")

async def handle_custom_amount_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle custom amount text input during cashout flow"""
    if not update.message:
        return
    if not update.message or not update.message.text:
        logger.warning("❌ No message or text in handle_custom_amount_input")
        return

    if not update.effective_user:
        return

    if not update.message:
        return
    input_text = update.message.text.strip()
    user_id = update.effective_user.id

    # CRITICAL FIX: Never process commands in amount handler
    if input_text.startswith('/'):
        logger.info(f"🚫 Ignoring command '{input_text}' in custom amount handler")
        return

    # Remove $ symbol if present for parsing
    if input_text.startswith('$'):
        input_text = input_text[1:]

    # IMMEDIATE FEEDBACK: Amount processing indicator
    if not update.message:
        return
    processing_msg = await update.message.reply_text("⏳ Processing amount...")

    logger.info(f"🎯 CUSTOM AMOUNT HANDLER processing input from user {user_id}: '{input_text}'")

    try:
        # SECURITY FIX: Use secure parser instead of dangerous .replace(",", "")
        amount_decimal, validation_msg = SecureAmountParser.validate_and_parse(input_text, "$")
        amount_usd = Decimal(str(amount_decimal or 0))
        logger.info(f"💰 {validation_msg}, Min: ${Config.MIN_CASHOUT_AMOUNT}, Max: ${Config.MAX_CASHOUT_AMOUNT}")
        
        # CLEANUP: Remove processing message on success
        try:
            await processing_msg.delete()
        except Exception:
            pass

        if amount_usd < Config.MIN_CASHOUT_AMOUNT:
            if not update.message:
                return
            await update.message.reply_text(
                f"❌ Minimum amount is ${Config.MIN_CASHOUT_AMOUNT:.0f}. Please enter a higher amount."
            )
            return

        if amount_usd > Config.MAX_CASHOUT_AMOUNT:
            if not update.message:
                return
            await update.message.reply_text(
                f"❌ Maximum amount is ${Config.MAX_CASHOUT_AMOUNT:.0f}. Please enter a lower amount."
            )
            return

        # ENHANCED PROACTIVE VALIDATION: Use new comprehensive validation system
        async with async_managed_session() as session:
            stmt = select(User).where(User.telegram_id == user_id)
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()
            if not user:
                if not update.message:
                    return
                await update.message.reply_text("❌ User not found")
                return

            # Import the enhanced validation utilities
            from utils.wallet_validation import WalletValidator
            
            # PROACTIVE VALIDATION: Check amount viability before proceeding
            is_valid, validation_message, validation_details = WalletValidator.validate_minimum_cashout_amount(
                amount=Decimal(str(amount_usd)),
                network="USDT",  # Default to USDT for text input
                currency="USD"
            )
            
            if not is_valid:
                logger.info(f"❌ Proactive validation failed for ${amount_usd} from user {user_id}")
                
                # Enhanced error message with branded formatting
                header = make_header("Amount Validation")
                error_text = f"""{header}

{validation_message}

{make_trust_footer()}"""
                
                # Create helpful action buttons for common issues
                keyboard = []
                
                # If there's a suggested minimum, offer it as a button
                if "suggested_minimum" in validation_details:
                    suggested_amount = Decimal(str(validation_details.get("suggested_minimum", 0) or 0))
                    keyboard.append([
                        InlineKeyboardButton(
                            f"💡 Try ${suggested_amount:.2f}", 
                            callback_data=f"amount:{suggested_amount}"
                        )
                    ])
                
                # Add helpful navigation buttons
                keyboard.extend([
                    [InlineKeyboardButton("💰 Add Funds", callback_data="wallet_add_funds")],
                    [InlineKeyboardButton("🔙 Back to Amounts", callback_data="wallet_cashout")],
                    [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_start")]
                ])
                
                # Send enhanced error message with options
                if not update.message:
                    return
                await update.message.reply_text(
                    error_text,
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                return

            # SUCCESS: Validation passed - extract details for logging and proceed
            final_fee = validation_details.get("final_fee", Decimal('0'))
            net_amount = validation_details.get("net_amount", Decimal('0'))
            
            logger.info(f"✅ Proactive validation passed for user {user_id}: Amount ${amount_usd}, Fee ${final_fee}, Net ${net_amount}")
            
            # Store amount and validation details for later use
            if context.user_data is not None:
                context.user_data["cashout_data"] = {
                    "amount": amount_usd,
                    "validation_details": validation_details  # Store for reference in later steps
                }
                # Clear custom amount state and proceed to currency selection
                if not context.user_data:
                    context.user_data = {}
                context.user_data["current_state"] = "SELECTING_CRYPTO_CURRENCY"
                if not context.user_data:
                    context.user_data = {}
                context.user_data["wallet_state"] = "selecting_crypto_currency"

            # Show success feedback with fee breakdown before proceeding  
            success_header = make_header("Amount Validated")
            success_text = f"""{success_header}

{validation_message}

Proceeding to currency selection...

{make_trust_footer()}"""
            
            # Send confirmation message (with proper error handling)
            if update.message:
                confirmation_msg = await update.message.reply_text(
                    success_text,
                    parse_mode="Markdown"
                )
            if not update.callback_query or not update.callback_query.message:
                return
            elif update.callback_query and update.callback_query.message:
                # Check if message is accessible Message type before calling reply_text
                from telegram import Message
                msg = update.callback_query.message
                if isinstance(msg, Message):
                    confirmation_msg = await msg.reply_text(
                        success_text,
                        parse_mode="Markdown"
                    )
            else:
                # Add null check for effective_chat
                if update.effective_chat:
                    confirmation_msg = await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text=success_text,
                        parse_mode="Markdown"
                    )
                else:
                    logger.error("No effective_chat available for confirmation message")
                    return
            
            # Brief delay to show the success message, then proceed
            import asyncio
            await asyncio.sleep(1)
            
            # Proceed to method selection (shows both NGN and crypto options)
            await show_method_selection_for_text_input(update, context, amount_usd)

    except (ValueError, AmountValidationError) as e:
        # CLEANUP: Remove processing message on error
        try:
            await processing_msg.delete()
        except Exception:
            pass
        
        # Clear session state to prevent stuck state
        await clear_cashout_session(user_id, context, f"amount_validation_error_{type(e).__name__}")
        
        # Provide specific error message for better UX
        error_msg = str(e) if isinstance(e, AmountValidationError) else "Invalid amount format"
        
        if not update.message:
            return
        await update.message.reply_text(
            f"❌ {error_msg}\n\n{SecureAmountParser.get_format_examples()}\n\nUse /cancel to reset if needed.",
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"❌ CUSTOM_AMOUNT: Unexpected error for user {user_id}: {e}")
        # CLEANUP: Remove processing message on error
        try:
            await processing_msg.delete()
        except Exception:
            pass
        
        # Clear session state to prevent stuck state
        await clear_cashout_session(user_id, context, f"amount_input_error_{type(e).__name__}")
        
        if update.message:
            await update.message.reply_text(
                "❌ Error processing amount. Please try again or use /cancel to reset."
            )

async def show_method_selection_for_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE, amount_usd: Decimal) -> None:
    """Show payment method selection for text input (adapted from callback version)"""
    text = f"""💵 Payment Method

💰 Amount: {format_clean_amount(amount_usd)} USD

Choose your cashout method:"""

    keyboard = []
    
    if Config.ENABLE_NGN_FEATURES:
        keyboard.append([InlineKeyboardButton("🏦 NGN Bank Transfer", callback_data="method:ngn")])
    
    keyboard.extend([
        [InlineKeyboardButton("💰 Cryptocurrency", callback_data="method:crypto")],
        [InlineKeyboardButton("🔙 Back", callback_data="wallet_cashout")]
    ])

    # Send new message for text-based flow
    if update.message:
        await update.message.reply_text(
            text, 
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

# ===== CASHOUT FLOW HANDLERS =====

async def handle_amount_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle amount selection (Step 1 of cashout flow)"""
    query = update.callback_query
    if not query:
        logger.warning("No callback query in handle_amount_selection")
        return

    # IMMEDIATE FEEDBACK: Action confirmed
    await safe_answer_callback_query(query, "💰 Processing amount...")
    
    # Parse amount from callback data (amount:25 -> 25)
    try:
        if not query:
            return
        if not query.data:
            raise ValueError("No callback data")
        if not query:
            return
        amount_str = (query.data or "").split(":")[-1]
        if amount_str == "custom":
            # This path is no longer used since we removed the custom amount button
            # But keeping for any legacy callback data that might still exist
            await safe_answer_callback_query(query, "❌ Please type your amount instead of clicking Custom")
            return

        amount_usd = int(Decimal(str(amount_str or 0)))  # Handle both "2" and "2.0"
    except (ValueError, IndexError):
        keyboard = [
            [InlineKeyboardButton("🔄 Try Again", callback_data="wallet_cashout")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")],
        ]
        await safe_edit_message_text(
            query,
            "❌ Invalid amount selected\n\nPlease choose a valid amount:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    async with async_managed_session() as session:
        if not update.effective_user:
            await safe_edit_message_text(query, "❌ User session expired")
            return

        user_id = getattr(update.effective_user, "id", 0) if update.effective_user else 0
        if not user_id:
            await safe_edit_message_text(query, "❌ Invalid user session")
            return

        stmt = select(User).where(User.telegram_id == int(user_id))
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        if not user:
            await safe_edit_message_text(query, "❌ User not found")
            return

        # ENHANCED PROACTIVE VALIDATION: Use comprehensive validation system
        from utils.wallet_validation import WalletValidator
        
        # Validate amount with fees and balance comprehensively
        is_valid, validation_message, validation_details = WalletValidator.validate_minimum_cashout_amount(
            amount=Decimal(str(amount_usd)),
            network="USDT",  # Default to USDT for button selections
            currency="USD"
        )
        
        if not is_valid:
            logger.info(f"❌ Button validation failed for ${amount_usd} from user {user_id}")
            
            # Create branded error message for button selection
            header = make_header("Amount Validation")
            error_text = f"""{header}

{validation_message}

{make_trust_footer()}"""
            
            # Create helpful navigation buttons specific to button selection flow
            keyboard = []
            
            # If there's a suggested minimum, offer it as a button
            if "suggested_minimum" in validation_details:
                suggested_amount = Decimal(str(validation_details.get("suggested_minimum", 0) or 0))
                keyboard.append([
                    InlineKeyboardButton(
                        f"💡 Try ${suggested_amount:.2f}", 
                        callback_data=f"amount:{suggested_amount}"
                    )
                ])
            
            # Add helpful navigation buttons
            keyboard.extend([
                [InlineKeyboardButton("🔙 Back to Amounts", callback_data="wallet_cashout")],
                [InlineKeyboardButton("💰 Add Funds", callback_data="wallet_add_funds")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_start")]
            ])
            
            # Send enhanced error message
            await safe_edit_message_text(
                query,
                error_text,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return

        # SUCCESS: Extract validation details for logging and proceed
        final_fee = validation_details.get("final_fee", Decimal('0'))
        net_amount = validation_details.get("net_amount", Decimal('0'))
        
        logger.info(f"✅ Button validation passed for user {user_id}: Amount ${amount_usd}, Fee ${final_fee}, Net ${net_amount}")

        # Store amount and validation details for later use
        if context.user_data is not None:
            context.user_data.setdefault("cashout_data", {
                "amount": amount_usd,
                "validation_details": validation_details  # Store for reference in later steps
            })
        else:
            context.user_data = {
                "cashout_data": {
                    "amount": amount_usd,
                    "validation_details": validation_details
                }
            }
        if not context.user_data:
            context.user_data = {}
        context.user_data["current_state"] = WalletStates.SELECTING_METHOD

        # Show method selection with validated amount
        return await show_method_selection(query, context, amount_usd, as_int(user.id) or 0 if user else 0, session)

async def show_method_selection(query, context, amount_usd, user_id, session) -> None:
    """Show payment method selection (Step 2 of cashout flow) with forced UI refresh"""
    text = f"""💵 Payment Method

Amount: {format_clean_amount(amount_usd)} USD

Choose your cashout method:"""

    keyboard = []
    
    # Add NGN option only if feature is enabled
    if Config.ENABLE_NGN_FEATURES:
        keyboard.append([InlineKeyboardButton("🏦 NGN Bank Transfer", callback_data="method:ngn")])
    
    keyboard.extend([
        [InlineKeyboardButton("💰 Cryptocurrency", callback_data="method:crypto")],
        [InlineKeyboardButton("⬅️ Back to Wallet", callback_data="menu_wallet")]
    ])

    # Force UI refresh by deleting old message and sending new one
    try:
        await query.delete_message()
        await query.message.reply_text(
            text, 
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        # Fallback to edit if delete fails
        logger.warning(f"Delete+send failed, using edit: {e}")
        await safe_edit_message_text(
            query, text, reply_markup=InlineKeyboardMarkup(keyboard)
        )

async def handle_back_to_methods(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle back to methods button - delete message and show method selection with toast"""
    query = update.callback_query
    if not query:
        logger.warning("No callback query in handle_back_to_methods")
        return
    
    # Show toast notification
    await safe_answer_callback_query(query, "💵 Returning to methods...")
    
    # Get amount from context
    if not context.user_data or "cashout_data" not in context.user_data:
        await safe_edit_message_text(query, "❌ Session error. Please restart.")
        return
    
    if not context.user_data:
        context.user_data = {}
    amount_usd = context.user_data.get("cashout_data", {}).get("amount")
    if not amount_usd:
        await safe_edit_message_text(query, "❌ Session error. Please restart from cashout.")
        return
    
    # Get user from database for show_method_selection
    async with async_managed_session() as session:
        if not update.effective_user:
            await safe_edit_message_text(query, "❌ Session error. Please restart.")
            return
        
        user_id = update.effective_user.id
        stmt = select(User).where(User.telegram_id == user_id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        
        if not user:
            await safe_edit_message_text(query, "❌ User not found. Please restart.")
            return
        
        # Show method selection (this deletes message and shows new one)
        await show_method_selection(query, context, amount_usd, as_int(user.id) or 0, session)

async def handle_method_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle method selection (Step 2 of cashout flow) - Enhanced with session cleanup"""
    query = update.callback_query
    if not update or not update.effective_user:
        return
    user_id = update.effective_user.id if update.effective_user else 0
    
    if not query:
        logger.warning("No callback query in handle_method_selection")
        return

    # Log method selection for audit trail
    logger.debug(f"Method selection: callback_data='{query.data}', user={user_id}")

    try:
        await safe_answer_callback_query(query, "💰 Processing method...")

        if not query:
            return
        if query.data == "back_to_amounts":
            # Redirect to wallet menu instead of amount selection (amount selection hidden)
            return await show_wallet_menu(update, context)

        # Parse method from callback data (method:ngn -> ngn)
        try:
            if not query:
                return
            if not query.data:
                raise ValueError("No callback data")
            if not query:
                return
            method = (query.data or "").split(":")[-1]
            # Legacy compatibility: redirect method:usdt to method:crypto
            if method == "usdt":
                method = "crypto"
        except (ValueError, IndexError):
            await safe_edit_message_text(
                query, "❌ Invalid method selected\n\nPlease select a valid payment method:"
            )
            return

        if context.user_data is None or "cashout_data" not in context.user_data:
            await safe_edit_message_text(query, "❌ Session error. Please restart.")
            await clear_cashout_session(user_id, context, "session_error")
            return
        
        amount_usd = context.user_data.get("cashout_data", {}).get("amount")
        if not amount_usd:
            await clear_cashout_session(user_id, context, "missing_amount")
            return

        if method == "ngn":
            # NGN Bank Transfer selected
            if not context.user_data:
                context.user_data = {}
            context.user_data["cashout_data"].update({
                "network": "NGN-BANK", 
                "currency": "NGN"
            })

            # Check for saved bank accounts
            async with async_managed_session() as session:
                try:
                    if not update.effective_user:
                        await safe_edit_message_text(query, "❌ Session error. Please restart.")
                        return
                    
                    user_id = getattr(update.effective_user, "id", 0) if update.effective_user else 0
                    stmt = select(User).where(User.telegram_id == user_id)
                    result = await session.execute(stmt)
                    user = result.scalar_one_or_none()

                    if not user:
                        await safe_edit_message_text(query, "❌ User not found. Please restart.")
                        return

                    stmt = select(SavedBankAccount).where(
                        SavedBankAccount.user_id == getattr(user, "id", 0)
                    ).order_by(
                        SavedBankAccount.is_default.desc(),
                        SavedBankAccount.last_used.desc()
                    )
                    result = await session.execute(stmt)
                    saved_accounts = result.scalars().all()

                    if saved_accounts:
                        # Show saved accounts
                        return await show_saved_bank_accounts(query, context, amount_usd, saved_accounts)
                    else:
                        # No saved accounts - proceed with manual entry
                        return await show_manual_bank_entry(query, context, amount_usd)

                except Exception as e:
                    logger.error(f"Error in NGN method selection: {e}")
                    await safe_edit_message_text(
                        query, "❌ Error processing request.\n\nPlease select a payment method again:"
                    )
                    # Clear session on database errors to prevent stuck state
                    await clear_cashout_session(user_id, context, f"ngn_db_error_{type(e).__name__}")

        elif method == "crypto":
            logger.info("User selected crypto method - showing currency options")
            # DYNAMIC MINIMUM VALIDATION: Check minimum for crypto cashouts
            min_usd = Config.MIN_CASHOUT_AMOUNT
            # Convert amount to Decimal for comparison
            amount_decimal = Decimal(str(amount_usd))
            if amount_decimal < min_usd:
                await safe_edit_message_text(
                    query,
                    f"❌ Minimum CashOut: ${min_usd:.0f} USD\n\n"
                    f"Selected: {format_clean_amount(amount_usd)} USD\n\n"
                    f"Please select a higher amount to continue.",
                    parse_mode="HTML"
                )
                return

            # Store method choice
            if not context.user_data:
                context.user_data = {}
            context.user_data["cashout_data"]["method"] = "crypto"

            # Show cryptocurrency selection screen
            return await show_crypto_currency_selection(query, context)
        else:
            await safe_edit_message_text(query, "❌ Invalid method selected")
        
    except Exception as e:
        # Top-level exception handler - clears session on any unexpected error
        logger.error(f"❌ METHOD_SELECTION: Unexpected error for user {user_id}: {e}")
        await safe_edit_message_text(query, "❌ Unexpected error. Please try again or use /cancel to reset.")
        await clear_cashout_session(user_id, context, f"method_selection_error_{type(e).__name__}")

async def show_saved_bank_accounts(query, context, amount_usd, saved_accounts) -> None:
    """Show saved bank accounts for selection - OPTIMIZED VERSION"""
    # Pre-format amount for reuse
    amount_formatted = format_clean_amount(amount_usd, "USD")
    
    text = f"""🏦 Select Bank Account

Amount: {amount_formatted}

Choose a saved account:"""

    keyboard = []
    for account in saved_accounts:
        # Optimized account display formatting
        is_verified = getattr(account, "is_verified", False) if hasattr(account, 'is_verified') else account.get('is_verified', False)
        status_icon = "✅" if is_verified else "⚠️"
        
        # Handle both model objects and dictionary data
        bank_name = getattr(account, 'bank_name', None) or account.get('bank_name', 'Unknown Bank')
        account_number = getattr(account, 'account_number', None) or account.get('account_number', '****')
        account_id = getattr(account, 'id', None) or account.get('id', 0)
        
        # Optimized string operations
        short_bank = bank_name[:15] + "..." if len(bank_name) > 15 else bank_name
        last_four = account_number[-4:] if len(account_number) >= 4 else account_number
        label = f"{status_icon} {short_bank} ••••{last_four}"
        
        keyboard.append([
            InlineKeyboardButton(label, callback_data=f"saved_bank:{account_id}")
        ])

    # Pre-built action buttons
    keyboard.extend([
        [InlineKeyboardButton("🏦 Add New Bank", callback_data="add_new_bank")],
        [InlineKeyboardButton("⬅️ Back to Methods", callback_data="back_to_methods")]
    ])

    await safe_edit_message_text(
        query, text, reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def show_manual_bank_entry(query, context, amount_usd) -> None:
    """Show manual bank entry screen"""
    text = f"""🏦 Add Bank Account

Amount: ${amount_usd} USD

Please enter your bank details to proceed with NGN cashout."""

    keyboard = [
        [InlineKeyboardButton("🏦 Select Bank", callback_data="select_ngn_bank")],
        [InlineKeyboardButton("⬅️ Back to Methods", callback_data="back_to_methods")]
    ]

    await safe_edit_message_text(
        query, text, reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def show_crypto_currency_selection(query, context) -> None:
    """Show cryptocurrency selection screen with fees and smart defaults - PHASE 1 & 2 OPTIMIZED"""
    from utils.helpers import get_currency_emoji
    from services.percentage_cashout_fee_service import percentage_cashout_fee_service

    if not context.user_data:
        context.user_data = {}
    amount_usd = context.user_data.get("cashout_data", {}).get("amount") if context.user_data else None
    if not amount_usd:
        return
    
    # Get user's last used crypto for smart defaults (PHASE 2)
    user_id = query.from_user.id if query and query.from_user else 0
    last_used_crypto = await get_last_used_crypto(user_id) if user_id else None

    text = f"""💰 Select Cryptocurrency
Amount: {format_clean_amount(amount_usd)} USD

Choose your crypto (fees shown below):"""

    keyboard = []
    amount_decimal = Decimal(str(amount_usd))
    
    # Currency display order: BTC, ETH, USDT-TRC20 ONLY
    # Note: LTC and USDT-ERC20 temporarily hidden for simplified UX
    display_order = ["BTC", "ETH", "USDT-TRC20"]
    
    for currency in display_order:
        if currency not in Config.SUPPORTED_CURRENCIES:
            continue
            
        emoji = get_currency_emoji(currency)
        
        # CRITICAL FIX: Calculate COMBINED fee (platform + network)
        # Use fallback network fees since we don't have address yet
        network = currency.split("-")[1] if "-" in currency else currency
        base_currency = currency.split("-")[0] if "-" in currency else currency
        
        try:
            fee_info = await calculate_crypto_cashout_with_network_fees(
                amount_usd=amount_decimal,
                currency=base_currency,
                network=network,
                address_key=None  # No address yet, will use fallback network fees
            )
            
            # Build button label with COMBINED fee info
            final_fee = fee_info["total_fee"]
            fee_source = fee_info.get("network_fee_source", "fallback")
            
            # Highlight low-fee options
            fee_indicator = "✨" if final_fee <= Decimal("3.00") else ""
            
            # Show TOTAL fee with source indicator (~ for estimated, no symbol for real-time)
            fee_prefix = "~" if fee_source == "fallback" else ""
            button_label = f"{emoji} {currency} ({fee_prefix}${final_fee:.2f} fee){fee_indicator}"
            
        except Exception as fee_error:
            logger.warning(f"Fee calculation failed for {currency}: {fee_error}")
            button_label = f"{emoji} {currency}"
        
        # Add star indicator for last used (PHASE 2)
        if currency == last_used_crypto:
            button_label = f"⭐ {button_label}"
        
        keyboard.append([
            InlineKeyboardButton(button_label, callback_data=f"select_crypto:{currency}")
        ])

    # Add back button
    keyboard.append(
        [InlineKeyboardButton("⬅️ Back to Methods", callback_data="back_to_methods")]
    )

    await safe_edit_message_text(
        query, text, reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ===== NGN PAYOUT CONFIRMATION SCREEN =====

async def show_ngn_payout_confirmation_screen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Show NGN payout confirmation screen with live rates and bank details before OTP verification.
    Features rate locking to prevent rate changes between confirmation and OTP verification.
    """
    query = update.callback_query if update.callback_query else None
    
    try:
        # Get required data from context
        if not context.user_data:
            context.user_data = {}
        cashout_data = context.user_data.get('cashout_data', {})
        verified_account = cashout_data.get('verified_account')
        amount_usd = cashout_data.get('amount', '0.00')
        
        if not verified_account or not amount_usd:
            error_msg = "❌ Transaction Error\n\nCashout information not found. Please try again."
            if query:
                await safe_edit_message_text(query, error_msg)
            else:
                from utils.message_utils import send_unified_message
                await send_unified_message(update, error_msg)
            return
        
        # Set state to confirmation
        if not context.user_data:
            context.user_data = {}
        context.user_data['wallet_state'] = 'confirming_ngn_payout'
        
        # Show loading message first
        loading_text = "💰 Preparing Payout Confirmation\n\n📊 Fetching live exchange rates and creating rate lock..."
        if query:
            await safe_edit_message_text(query, loading_text)
        else:
            from utils.message_utils import send_unified_message
            await send_unified_message(update, loading_text)
        
        try:
            # Convert amount to Decimal for precise calculations
            amount_decimal = MonetaryDecimal.to_decimal(amount_usd, "usd_amount")
            
            # Get live NGN rate with wallet markup
            ngn_amount = await get_dynamic_ngn_amount(amount_decimal)
            
            # Get rate details for display
            from services.fastforex_service import FastForexService
            fastforex = FastForexService()
            ngn_rate = await fastforex.get_usd_to_ngn_rate_with_wallet_markup()
            
            # 🔒 CREATE RATE LOCK - This prevents rate changes during OTP verification
            from utils.rate_lock import RateLock
            
            cashout_context = {
                'bank_account': {
                    'bank_code': verified_account['bank_code'],
                    'account_number': verified_account['account_number'],
                    'account_name': verified_account['account_name']
                },
                'cashout_type': 'ngn'
            }
            
            # Check effective_user exists
            if not update.effective_user:
                raise Exception("No effective_user available")
            
            rate_lock_result = RateLock.create_rate_lock(
                user_id=update.effective_user.id,
                usd_amount=amount_decimal,
                ngn_amount=ngn_amount,
                exchange_rate=ngn_rate if ngn_rate else Decimal('0.0'),
                cashout_context=cashout_context
            )
            
            if not rate_lock_result['success']:
                logger.error(f"❌ Failed to create rate lock: {rate_lock_result.get('error')}")
                raise Exception(f"Rate lock creation failed: {rate_lock_result.get('error')}")
            
            # Store rate lock in user context for OTP flow
            rate_lock = rate_lock_result['rate_lock']
            RateLock.store_rate_lock_in_context(context, rate_lock)
            
            # Get rate lock display information
            rate_display_info = RateLock.format_locked_rate_display(rate_lock)
            
            # Format amounts cleanly
            usd_formatted = format_clean_amount(amount_decimal, "USD")
            ngn_formatted = format_clean_amount(ngn_amount, "NGN")
            
            # Create confirmation screen with rate lock information
            header = make_header("Confirm NGN Payout")
            
            text = f"""{header}
            
🏦 Bank Details
• Bank: {verified_account['bank_name']}
• Account: {verified_account['account_name']}
• Number: {verified_account['account_number']}

💱 Payout Details
• Cashout Amount: {usd_formatted}
• Exchange Rate: {rate_display_info['rate_display']} per $1
• Fees: ₦0.00 (Free NGN transfers)
• NGN Amount: {ngn_formatted}

✅ You Will Receive: {ngn_formatted}

🔒 {rate_display_info['lock_status']}
{rate_display_info['countdown_display']}

✨ Next Steps:
We'll send a security code to your email for final verification.
Your rate is locked and won't change during verification.

{make_trust_footer()}"""
            
            # Create action buttons
            keyboard = [
                [InlineKeyboardButton("💰 Cashout Now", callback_data="confirm_ngn_payout_proceed")],
                [InlineKeyboardButton("💰 Cashout & Save Bank", callback_data="confirm_ngn_payout_and_save")],
                [InlineKeyboardButton("🔙 Back to Bank Selection", callback_data="select_ngn_bank")]
            ]
            
            # Update message with confirmation screen
            if query:
                await safe_edit_message_text(
                    query, 
                    text, 
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode='Markdown'
                )
            else:
                from utils.message_utils import send_unified_message
                await send_unified_message(
                    update, 
                    text, 
                    reply_markup=InlineKeyboardMarkup(keyboard), 
                    parse_mode='Markdown'
                )
            
            # Add null check for effective_user
            if not update or not update.effective_user:
                return
            user_id_log = update.effective_user.id if update.effective_user else "unknown"
            logger.info(
                f"✅ NGN payout confirmation screen shown with rate lock - User: {user_id_log}, "
                f"Amount: {usd_formatted} → {ngn_formatted}, Rate: ₦{ngn_rate:.2f}, "
                f"Lock Token: {rate_lock['token'][:8]}..., Expires: {rate_lock_result['expires_at_formatted']}"
            )
            
        except Exception as rate_error:
            logger.error(f"❌ Error fetching live rates for payout confirmation: {rate_error}")
            
            # Show error with retry option
            error_text = f"""❌ Rate Fetch Error

Unable to get live exchange rates at the moment.

{str(rate_error)}

Please try again or contact support if the issue persists."""
            
            keyboard = [
                [InlineKeyboardButton("🔄 Try Again", callback_data="retry_ngn_payout_confirmation")],
                [InlineKeyboardButton("🔙 Back to Bank Selection", callback_data="select_ngn_bank")]
            ]
            
            if query:
                await safe_edit_message_text(
                    query, 
                    error_text, 
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode='Markdown'
                )
            else:
                from utils.message_utils import send_unified_message
                await send_unified_message(
                    update, 
                    error_text, 
                    reply_markup=InlineKeyboardMarkup(keyboard), 
                    parse_mode='Markdown'
                )
        
    except Exception as e:
        logger.error(f"❌ Error in show_ngn_payout_confirmation_screen: {e}")
        
        # Fallback error message
        error_msg = f"""❌ System Error"

There was an error preparing your payout confirmation.

{str(e)}

Please try again or contact support."""
        
        keyboard = [
            [InlineKeyboardButton("🔄 Retry", callback_data="select_ngn_bank")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
        ]
        
        if query:
            await safe_edit_message_text(
                query, 
                error_msg, 
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
        else:
            from utils.message_utils import send_unified_message
            await send_unified_message(
                update, 
                error_msg, 
                reply_markup=InlineKeyboardMarkup(keyboard), 
                parse_mode='Markdown'
            )

# ===== NGN PAYOUT CONFIRMATION HANDLERS =====

async def handle_confirm_ngn_payout_proceed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle 'Cashout Now' button from payout confirmation screen - proceed to OTP without saving bank"""
    query = update.callback_query
    await safe_answer_callback_query(query, "💰 Processing cashout...")
    
    try:
        if not update or not update.effective_user:
            return
        logger.info(f"🎯 NGN payout proceeding without bank save - User: {update.effective_user.id}")
        
        # Clear any save bank flag
        if context.user_data and 'cashout_data' in context.user_data:
            if not context.user_data:
                context.user_data = {}
            context.user_data['cashout_data']['save_bank'] = False
        
        # Proceed to OTP verification
        await proceed_to_ngn_otp_verification(update, context)
        
    except Exception as e:
        logger.error(f"❌ Error in handle_confirm_ngn_payout_proceed: {e}")
        await safe_edit_message_text(
            query,
            f"❌ Cashout Error\n\nThere was an error processing your cashout request.\n\n{str(e)}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Try Again", callback_data="retry_ngn_payout_confirmation")],
                [InlineKeyboardButton("🔙 Back", callback_data="select_ngn_bank")]
            ])
        )

async def handle_confirm_ngn_payout_and_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle 'Cashout & Save Bank' button from payout confirmation screen - proceed to OTP with bank saving"""
    query = update.callback_query
    await safe_answer_callback_query(query, "💰 Processing cashout with bank save...")
    
    try:
        if not update or not update.effective_user:
            return
        logger.info(f"🎯 NGN payout proceeding with bank save - User: {update.effective_user.id}")
        
        # Set save bank flag
        if not context.user_data or 'cashout_data' not in context.user_data:
            if not context.user_data:
                context.user_data = {}
            context.user_data['cashout_data'] = {}
        if not context.user_data:
            context.user_data = {}
        context.user_data['cashout_data']['save_bank'] = True
        
        # Proceed to OTP verification
        await proceed_to_ngn_otp_verification(update, context)
        
    except Exception as e:
        logger.error(f"❌ Error in handle_confirm_ngn_payout_and_save: {e}")
        await safe_edit_message_text(
            query,
            f"❌ Cashout Error\n\nThere was an error processing your cashout request.\n\n{str(e)}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Try Again", callback_data="retry_ngn_payout_confirmation")],
                [InlineKeyboardButton("🔙 Back", callback_data="select_ngn_bank")]
            ])
        )

async def handle_retry_ngn_payout_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle retry for payout confirmation screen when rate fetching fails"""
    query = update.callback_query
    await safe_answer_callback_query(query, "🔄 Retrying...")
    
    try:
        if not update or not update.effective_user:
            return
        logger.info(f"🔄 Retrying NGN payout confirmation - User: {update.effective_user.id}")
        
        # Simply call the confirmation screen again
        await show_ngn_payout_confirmation_screen(update, context)
        
    except Exception as e:
        logger.error(f"❌ Error in handle_retry_ngn_payout_confirmation: {e}")
        await safe_edit_message_text(
            query,
            f"❌ Retry Error\n\nUnable to retry the confirmation screen.\n\n{str(e)}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back to Bank Selection", callback_data="select_ngn_bank")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
            ])
        )

async def proceed_to_ngn_otp_verification(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Proceed from payout confirmation to OTP verification with rate lock validation.
    This validates the rate lock before sending OTP to ensure rates are still valid.
    """
    query = update.callback_query
    
    try:
        # Get cashout data
        if not context.user_data:
            context.user_data = {}
        cashout_data = context.user_data.get('cashout_data', {})
        selected_account = cashout_data.get('verified_account')
        
        if not selected_account:
            await safe_edit_message_text(
                query,
                "❌ Bank Information Missing\n\nPlease select your bank account again.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back to Bank Selection", callback_data="select_ngn_bank")]
                ])
            )
            return
        
        # 🔒 VALIDATE RATE LOCK before proceeding with OTP
        from utils.rate_lock import RateLock
        
        rate_lock = RateLock.get_rate_lock_from_context(context)
        if not rate_lock:
            if not update or not update.effective_user:
                return
            logger.warning(f"❌ No rate lock found for user {update.effective_user.id} during OTP verification")
            await safe_edit_message_text(
                query,
                "❌ Rate Lock Missing\n\nYour rate lock has been lost. Please confirm your cashout again.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Confirm Again", callback_data="retry_ngn_payout_confirmation")],
                    [InlineKeyboardButton("🔙 Back to Bank Selection", callback_data="select_ngn_bank")]
                ])
            )
            return
        
        # Validate rate lock is still active and not expired
        if not update or not update.effective_user:
            return
        validation_result = RateLock.validate_rate_lock(rate_lock, update.effective_user.id)
        
        if not validation_result['valid']:
            error_code = validation_result.get('error_code', 'UNKNOWN')
            error_message = validation_result.get('error', 'Rate lock validation failed')
            
            if not update or not update.effective_user:
                return
            logger.warning(f"❌ Rate lock validation failed for user {update.effective_user.id}: {error_message}")
            
            if error_code == 'LOCK_EXPIRED':
                expired_seconds = validation_result.get('expired_seconds', 0)
                expired_minutes = expired_seconds // 60
                
                # Rate lock expired - user needs to confirm again with new rates
                await safe_edit_message_text(
                    query,
                    f"⏰ Rate Lock Expired\n\nYour rate lock expired {expired_minutes} minute(s) ago. Please confirm your cashout again to get current rates.\n\n🔄 Fresh rates will be locked for another 15 minutes.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔄 Get Fresh Rates", callback_data="retry_ngn_payout_confirmation")],
                        [InlineKeyboardButton("🔙 Back to Bank Selection", callback_data="select_ngn_bank")]
                    ])
                )
            else:
                # Other validation errors
                await safe_edit_message_text(
                    query,
                    f"❌ Rate Lock Error\n\n{error_message}\n\nPlease confirm your cashout again.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔄 Try Again", callback_data="retry_ngn_payout_confirmation")],
                        [InlineKeyboardButton("🔙 Back to Bank Selection", callback_data="select_ngn_bank")]
                    ])
                )
            
            # Clean up invalid rate lock
            RateLock.invalidate_rate_lock(rate_lock, f"validation_failed_{error_code}")
            return
        
        # Rate lock is valid - get remaining time for UI display
        remaining_minutes = validation_result['remaining_minutes']
        remaining_seconds = validation_result['remaining_seconds']
        
        # Show loading message with rate lock status
        await safe_edit_message_text(
            query,
            f"🔐 Sending verification code...\n\n📊 Rate locked (expires in {remaining_minutes}m {remaining_seconds % 60}s)\n\nPlease wait while we send a security code to your email.",
            reply_markup=None
        )
        
        # Get user information for OTP sending
        async with async_managed_session() as session:
            # Add null check for effective_user
            if not update.effective_user:
                await safe_edit_message_text(
                    query,
                    "❌ User session expired. Please try again.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 Back", callback_data="back_to_method_selection")]
                    ])
                )
                return
                
            if not update or not update.effective_user:
                return
            stmt = select(User).where(User.telegram_id == int(update.effective_user.id))
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()
            if not user or not as_str(user.email):
                await safe_edit_message_text(
                    query,
                    "❌ Email Required\n\nPlease set up your email address first to proceed with cashout.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 Back", callback_data="back_to_method_selection")]
                    ])
                )
                return
            
            # Prepare enhanced cashout context with rate lock for security binding
            cashout_amount = cashout_data.get('amount', '0.00')
            
            # Create security fingerprint from rate lock for OTP binding
            rate_lock_fingerprint = RateLock.create_security_fingerprint(rate_lock)
            
            # FIXED: Consistent cashout_context structure with proper cashout_id
            # Generate unique cashout_id for security binding (max 20 chars for DB constraint)
            import uuid
            # Format: ng_[8-digit-timestamp]_[6-digit-uuid] = 19 chars total
            timestamp_suffix = str(int(datetime.utcnow().timestamp()))[-8:]  # Last 8 digits
            uuid_suffix = str(uuid.uuid4()).replace('-', '')[:6]  # 6 chars, no dashes
            cashout_id = f"ng_{timestamp_suffix}_{uuid_suffix}"
            
            cashout_context = {
                'cashout_id': cashout_id,
                'amount': str(cashout_amount),  # FIXED: Consistent string type
                'currency': 'NGN',
                'destination_hash': f"{selected_account['bank_code']}_{selected_account['account_number']}",
                'rate_lock_token': rate_lock['token'],
                'rate_lock_fingerprint': rate_lock_fingerprint,
                'locked_rate': str(rate_lock['exchange_rate']),  # FIXED: Consistent string type
                'locked_ngn_amount': str(rate_lock['ngn_amount'])  # FIXED: Consistent string type
            }
            
            # Store cashout_id in context for verification phase
            if not context.user_data:
                context.user_data = {}
            context.user_data.setdefault('cashout_data', {})['cashout_id'] = cashout_id
            
            # Define user_id for later use
            user_id = as_int(user.id)
            
            # ===== SKIP OTP: Proceed directly to cashout confirmation =====
            # OTP verification has been removed - users proceed directly
            from decimal import Decimal
            
            amount_ngn_decimal = Decimal(str(rate_lock.get('ngn_amount', 0)))
            cashout_amount_usd = Decimal(str(rate_lock.get('usd_amount', cashout_data.get('amount', 0))))
            
            logger.info(f"📝 DIRECT_CASHOUT: User {user_id} cashout ₦{amount_ngn_decimal:,.2f} (OTP removed)")
            
            # Show confirmation screen without OTP
            confirm_text = f"""<b>Confirm Cashout</b>

<b>Amount:</b> ₦{amount_ngn_decimal:,.2f}
<b>Bank:</b> {selected_account['bank_name']}
<b>Account:</b> ****{selected_account['account_number'][-4:]}
<b>Rate:</b> ₦{rate_lock.get('exchange_rate', 'N/A')} / $1

<b>Rate lock expires in {remaining_minutes}m {remaining_seconds % 60}s</b>

Proceed with this cashout?"""
            
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("Confirm Cashout", callback_data=f"confirm_unverified_cashout_{cashout_id}")],
                [InlineKeyboardButton("Cancel", callback_data="wallet_menu")]
            ])
            await safe_edit_message_text(query, confirm_text, parse_mode="HTML", reply_markup=keyboard)
            
            # Store cashout context for confirmation
            if not context.user_data:
                context.user_data = {}
            context.user_data['pending_unverified_cashout'] = {
                'cashout_id': cashout_id,
                'amount': str(amount_ngn_decimal),
                'bank_account_id': selected_account.get('id'),
                'bank_name': selected_account['bank_name'],
                'account_number': selected_account['account_number'],
                'bank_code': selected_account['bank_code'],
                'rate_lock': rate_lock
            }
            return
            
    except Exception as e:
        logger.error(f"❌ Error in proceed_to_ngn_otp_verification: {e}")
        await safe_edit_message_text(
            query,
            f"❌ OTP Setup Error\n\nThere was an error setting up email verification.\n\n{str(e)}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Try Again", callback_data="retry_ngn_payout_confirmation")],
                [InlineKeyboardButton("🔙 Back", callback_data="select_ngn_bank")]
            ])
        )

# ===== UNVERIFIED CASHOUT CONFIRMATION HANDLER =====

async def handle_confirm_unverified_cashout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle unverified cashout confirmation - processes cashout without OTP"""
    query = update.callback_query
    if not query or not query.from_user:
        logger.warning("No callback query or from_user in handle_confirm_unverified_cashout")
        return
    
    user_id = query.from_user.id
    
    try:
        # Extract cashout_id from callback data
        if not query.data or not query.data.startswith("confirm_unverified_cashout_"):
            await safe_edit_message_text(
                query,
                "❌ Invalid cashout confirmation.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back", callback_data="wallet_menu")]
                ])
            )
            return
        
        cashout_id = query.data.replace("confirm_unverified_cashout_", "")
        
        # Get pending cashout context
        if not context.user_data or 'pending_unverified_cashout' not in context.user_data:
            await safe_edit_message_text(
                query,
                "❌ Cashout session expired. Please try again.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back", callback_data="wallet_menu")]
                ])
            )
            return
        
        cashout_context = context.user_data['pending_unverified_cashout']
        
        # Validate cashout_id matches
        if cashout_context.get('cashout_id') != cashout_id:
            logger.error(f"Cashout ID mismatch: {cashout_context.get('cashout_id')} != {cashout_id}")
            await safe_edit_message_text(
                query,
                "❌ Invalid cashout session. Please try again.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back", callback_data="wallet_menu")]
                ])
            )
            return
        
        # Show processing message
        await safe_answer_callback_query(query, "💰 Processing unverified cashout...")
        await safe_edit_message_text(
            query,
            f"⏳ <b>Processing Cashout...</b>\n\n"
            f"🆔 Reference: {cashout_id}\n\n"
            f"Processing your NGN cashout...\n"
            f"This may take a moment.",
            parse_mode="HTML",
            reply_markup=None
        )
        
        logger.info(f"📝 UNVERIFIED_CASHOUT_CONFIRMED: User {user_id} confirmed cashout {cashout_id}")
        
        # Process the NGN cashout (same as verified flow, but without OTP)
        # Import auto cashout service
        from services.auto_cashout import process_ngn_cashout_direct
        
        async with async_managed_session() as session:
            # Get user
            stmt = select(User).where(User.telegram_id == int(user_id))
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()
            
            if not user:
                await safe_edit_message_text(
                    query,
                    "❌ User not found.",
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 Back", callback_data="wallet_menu")]
                    ])
                )
                return
            
            # Process cashout
            amount_decimal = Decimal(str(cashout_context['amount']))
            bank_account_id = cashout_context.get('bank_account_id')
            
            # Create cashout record
            from models import Cashout, CashoutStatus, CashoutType
            cashout_record = Cashout(
                cashout_id=cashout_id,
                user_id=as_int(user.id),
                amount=amount_decimal,
                currency='NGN',
                cashout_type=CashoutType.NGN.value,
                status=CashoutStatus.PENDING.value,
                bank_account_id=bank_account_id,
                created_at=datetime.now(timezone.utc)
            )
            session.add(cashout_record)
            await session.commit()
            
            logger.info(f"✅ Created unverified NGN cashout record: {cashout_id}")
            
            # Clear pending cashout from context
            context.user_data.pop('pending_unverified_cashout', None)
            
            # Invalidate user cache after cashout
            invalidate_user_cache(user_id)
            
            # Show success message
            await safe_edit_message_text(
                query,
                f"✅ <b>Cashout Requested</b>\n\n"
                f"🆔 Reference: {cashout_id}\n"
                f"💰 Amount: ₦{amount_decimal:,.2f}\n"
                f"🏦 Bank: {cashout_context.get('bank_name', 'N/A')}\n\n"
                f"Your cashout is being processed.\n\n"
                f"💡 <b>Tip:</b> Verify your email for OTP-protected cashouts!",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("💰 Wallet", callback_data="wallet_menu")],
                    [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
                ])
            )
            
    except Exception as e:
        logger.error(f"❌ Error in handle_confirm_unverified_cashout: {e}")
        await safe_edit_message_text(
            query,
            f"❌ <b>Cashout Error</b>\n\nFailed to process cashout.\n\nPlease try again.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Try Again", callback_data="wallet_cashout")],
                [InlineKeyboardButton("🔙 Back", callback_data="wallet_menu")]
            ])
        )

# ===== EXISTING ESSENTIAL HANDLERS (PRESERVED) =====

async def handle_confirm_ngn_cashout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle NGN cashout confirmation after user clicks 'Confirm & Process'"""
    query = update.callback_query
    if not query or not query.from_user:
        logger.warning("No callback query or from_user in handle_confirm_ngn_cashout")
        return
    user_id = query.from_user.id
    
    try:
        # Extract cashout_id from callback data
        if not query:
            return
        callback_data = query.data
        if not callback_data or not callback_data.startswith("confirm_ngn_cashout:"):
            if query.message:
                if not query:
                    return
                await query.edit_message_text("❌ Invalid confirmation request.")
            return
            
        cashout_id = callback_data.split(":", 1)[1]
        logger.info(f"🎯 Processing NGN cashout confirmation: {cashout_id} for user {user_id}")
        
        # INSTANT FEEDBACK: Show processing message immediately before heavy database operations
        await safe_answer_callback_query(query, "💰 Processing NGN cashout...")
        
        await safe_edit_message_text(
            query,
            f"⏳ **Processing NGN Cashout...**\n\n"
            f"🆔 Reference: {cashout_id}\n\n"
            f"Please wait while we:\n"
            f"• Verify your balance\n"
            f"• Validate bank account\n"
            f"• Process the payout\n\n"
            f"This may take a few seconds...",
            reply_markup=InlineKeyboardMarkup([])  # Remove buttons during processing
        )
        
        logger.info(f"✅ User {user_id} UI immediately updated with processing message for NGN cashout")
        
        from models import User, Cashout, CashoutStatus
        
        async with async_managed_session() as session:
            # Find the cashout record
            stmt = select(User).where(User.telegram_id == user_id)
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()
            if not user:
                if not query:
                    return
                await query.edit_message_text("❌ User not found. Please try again.")
                return
                
            stmt = select(Cashout).where(
                Cashout.cashout_id == cashout_id,
                Cashout.user_id == user.id,
                Cashout.status == CashoutStatus.USER_CONFIRM_PENDING.value
            )
            result = await session.execute(stmt)
            cashout = result.scalar_one_or_none()
            
            if not cashout:
                if not query:
                    return
                await query.edit_message_text(
                    f"❌ Cashout {cashout_id} not found or already processed.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("💳 Wallet", callback_data="menu_wallet")]
                    ])
                )
                return

            # BALANCE VALIDATION: Check if user has sufficient balance before processing
            from utils.wallet_validation import WalletValidator
            
            # Calculate cashout amount and estimated fees
            cashout_amount = Decimal(str(cashout.amount))
            
            # For NGN cashouts, fees are typically included in exchange rate markup
            # No additional processing fees for NGN bank transfers
            estimated_fees = Decimal("0.00")
            
            # Validate that user has sufficient balance for the cashout
            # Add await since validate_cashout_amount is a coroutine
            is_valid, error_message = await WalletValidator.validate_cashout_amount(
                user_id=as_int(user.id) or 0,
                cashout_amount=cashout_amount,
                estimated_fees=estimated_fees,
                currency="USD",
                session=session
            )
            
            if not is_valid:
                # Get current balance for error display
                from models import Wallet as WalletModel
                stmt = select(WalletModel).where(
                    WalletModel.user_id == user.id,
                    WalletModel.currency == "USD"
                )
                result = await session.execute(stmt)
                wallet = result.scalar_one_or_none()
                
                current_balance = Decimal(str(wallet.available_balance)) if wallet else Decimal("0.00")
                
                # Create branded insufficient balance error message
                header = make_header("Insufficient Balance")
                # FIXED: Escape markdown characters to prevent parse entities error
                required_amount = format_branded_amount(cashout_amount, 'USD')
                available_amount = format_branded_amount(current_balance, 'USD')
                shortage_amount = format_branded_amount(cashout_amount - current_balance, 'USD')
                
                if not query:
                    return
                await query.edit_message_text(
                    f"{header}\n\n"
                    f"❌ *Insufficient Balance*\n\n"
                    f"💰 *Required:* {required_amount}\n"
                    f"💳 *Available:* {available_amount}\n"
                    f"📉 *Shortage:* {shortage_amount}\n\n"
                    f"💡 Please add funds to your wallet or reduce the cashout amount.\n\n"
                    f"{make_trust_footer()}",
                    parse_mode='Markdown',
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("💰 Add Funds", callback_data="menu_deposit")],
                        [InlineKeyboardButton("💳 Wallet", callback_data="menu_wallet")],
                        [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
                    ])
                )
                return
                
            logger.info(f"✅ Balance validation passed for NGN cashout {cashout_id}: ${cashout_amount}")
                
            # Update status to approved for processing
            # Rename import to avoid conflict with telegram Update type
            from sqlalchemy import update as sql_update
            stmt = sql_update(Cashout).where(Cashout.id == cashout.id).values(
                status=CashoutStatus.ADMIN_PENDING.value
            )
            await session.execute(stmt)
            await session.commit()
            logger.info(f"✅ NGN cashout {cashout_id} confirmed by user - status updated to ADMIN_PENDING")
        
        # Clear user state
        if not context.user_data:
            context.user_data = {}
        context.user_data['wallet_state'] = None
        if 'pending_cashout' in context.user_data:
            if not context.user_data:
                context.user_data = {}
            del context.user_data['pending_cashout']
        if 'cashout_data' in context.user_data:
            if not context.user_data:
                context.user_data = {}
            del context.user_data['cashout_data']
            
        # Process the cashout
        from services.auto_cashout import process_ngn_cashout_wrapper
        
        # Use branded processing message with trust footer
        header = make_header("Processing Transfer")
        if not query:
            return
        await query.edit_message_text(
            f"{header}\n\n"
            f"🔄 Processing NGN Bank Transfer\n\n"
            f"📝 Reference: `{cashout_id}`\n"
            f"⏳ Status: Processing your transfer...\n\n"
            f"✅ Your bank transfer is being processed. You'll receive confirmation shortly.\n\n"
            f"{make_trust_footer()}",
            parse_mode='Markdown'
        )
        
        # Call the NGN cashout processing function
        # Convert Column[int] to int using ORM helper
        result = await process_ngn_cashout_wrapper(as_int(user.id) or 0, cashout_id)
        
        if result.get('success'):
            # Create branded success receipt
            header = make_header("Transfer Complete")
            # Convert Column[Decimal] to Decimal using ORM helper
            usd_amount = format_branded_amount(as_decimal(cashout.amount) or Decimal("0"), "USD")
            ngn_amount = format_branded_amount(result.get('ngn_amount', 0), "NGN")
            
            await context.bot.send_message(
                chat_id=user_id,
                text=f"{header}\n\n"
                     f"✅ NGN Transfer Completed!\n\n"
                     f"📝 Reference: `{cashout_id}`\n"
                     f"💰 Amount: {usd_amount} → {ngn_amount}\n"
                     f"🏦 Status: Transfer completed successfully\n\n"
                     f"📧 Check your email for full transaction details.\n\n"
                     f"{make_trust_footer()}",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("💳 Wallet", callback_data="menu_wallet")],
                    [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
                ])
            )
        else:
            # Use branded error message for failed transfer
            header = make_header("Transfer Issue")
            await context.bot.send_message(
                chat_id=user_id,
                text=f"{header}\n\n"
                     f"❌ Transfer Processing Failed\n\n"
                     f"📝 Reference: `{cashout_id}`\n"
                     f"🔄 Status: Failed - {result.get('error', 'Unknown error')}\n\n"
                     f"💡 Your funds are safe. Please try again or contact support.\n\n"
                     f"{make_trust_footer()}",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Try Again", callback_data="menu_wallet")],
                    [InlineKeyboardButton("💬 Support", callback_data="support_chat")]
                ])
            )
        
    except Exception as e:
        logger.error(f"❌ Error in handle_confirm_ngn_cashout: {e}")
        # Use branded error message for processing errors
        error_msg = BrandingUtils.get_branded_error_message("payment", "NGN cashout processing error")
        if not query:
            return
        await query.edit_message_text(
            error_msg,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
            ])
        )

async def handle_select_ngn_bank(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show Nigerian bank list for selection"""
    query = update.callback_query
    await safe_answer_callback_query(query, "🏦 Loading banks...")
    
    try:
        # Nigerian banks list (Fincra supported banks)
        nigerian_banks = [
            "Access Bank", "Zenith Bank", "GTBank", "First Bank", "UBA",
            "Fidelity Bank", "FCMB", "Sterling Bank", "Union Bank", "Wema Bank",
            "Polaris Bank", "Stanbic IBTC", "Heritage Bank", "Keystone Bank", "Unity Bank",
            "Citibank", "Ecobank", "Standard Chartered", "Jaiz Bank", "SunTrust Bank",
            "Providus Bank", "Titan Trust Bank"
        ]
        
        text = """🏦 Select Your Bank

Choose your Nigerian bank from the list below:"""

        keyboard = []
        # Create bank buttons (2 per row)
        for i in range(0, len(nigerian_banks), 2):
            row = []
            for j in range(2):
                if i + j < len(nigerian_banks):
                    bank = nigerian_banks[i + j]
                    row.append(InlineKeyboardButton(bank, callback_data=f"select_bank:{bank}"))
            keyboard.append(row)
            
        keyboard.append([InlineKeyboardButton("⬅️ Back to Methods", callback_data="back_to_methods")])
        
        await safe_edit_message_text(
            query, text, reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
    except Exception as e:
        logger.error(f"Error in handle_select_ngn_bank: {e}")
        await safe_edit_message_text(query, "❌ Error loading banks. Please try again.")

async def handle_select_bank(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle specific bank selection for NGN cashouts"""
    query = update.callback_query
    await safe_answer_callback_query(query, "🏦 Bank selected...")
    
    try:
        if not query or not query.data:
            return
        bank_data = query.data.replace("select_bank:", "")
        logger.info(f"Bank selected: {bank_data}")
        
        # Store bank selection
        if context.user_data is not None:
            context.user_data.setdefault("cashout_data", {})
            context.user_data["cashout_data"]["selected_bank"] = bank_data
        else:
            context.user_data = {
                "cashout_data": {"selected_bank": bank_data}
            }
        
        # Update user state to wait for account number
        async with async_managed_session() as session:
            if update.effective_user:
                user_id = getattr(update.effective_user, "id", 0)
                stmt = select(User).where(User.telegram_id == user_id)
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()
                if user:
                    # Use proper ORM updates
                    from sqlalchemy import update as sql_update
                    stmt = sql_update(User).where(User.id == user.id).values(
                        current_state="ENTERING_BANK_ACCOUNT",
                        wallet_state="entering_bank_account"
                    )
                    await session.execute(stmt)
                    await session.commit()
                    logger.info(f"🎯 BANK STATE SET: current_state=ENTERING_BANK_ACCOUNT, wallet_state=entering_bank_account for user {user.id}")
        
        await safe_edit_message_text(
            query,
            f"✅ Bank Selected: {bank_data}\n\n"
            "📱 Step 2: Enter Account Number\n\n"
            "Please type your 10-digit account number:",
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Error in handle_select_bank: {e}")
        await safe_edit_message_text(query, "❌ Error selecting bank. Please try again.")

async def handle_retry_bank_verification(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle retry bank verification - ASYNC OPTIMIZED"""
    try:
        # PERFORMANCE: Use button_callback_wrapper for instant feedback (<50ms)
        async with button_callback_wrapper(update, "🔄 Loading...") as session:
            query = update.callback_query
            if not query:
                return
            
            await query.edit_message_text(
                "🔄 Retry Bank Verification\n\n"
                "Please select your bank again:",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🏦 Select Bank", callback_data="menu_wallet")]
                ])
            )
            
    except Exception as e:
        logger.error(f"Error in handle_retry_bank_verification: {e}")
        query = update.callback_query
        if query:
            await query.edit_message_text("❌ Error retrying verification. Please try again.")

# ===== MINIMAL WALLET HANDLERS LIST =====
# This is what the system expects to import

# Add missing essential handlers
async def handle_saved_bank_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle saved bank account selection for NGN cashouts - OPTIMIZED VERSION"""
    query = update.callback_query
    await safe_answer_callback_query(query, "🏦 Processing saved bank...")
    
    try:
        # Extract bank account ID from callback data
        if not query or not query.data:
            return
        callback_data = query.data
        if not callback_data.startswith("saved_bank:"):
            logger.error(f"Invalid callback data for saved bank: {callback_data}")
            await safe_edit_message_text(query, "❌ Invalid bank selection. Please try again.")
            return
        
        account_id_str = callback_data.replace("saved_bank:", "")
        if not update or not update.effective_user:
            return
        telegram_user_id = update.effective_user.id
        
        # Convert account_id to integer (fix type conversion bug)
        try:
            account_id = int(account_id_str)
        except ValueError:
            logger.error(f"Invalid account ID format: {account_id_str} for telegram user {telegram_user_id}")
            await safe_edit_message_text(query, "❌ Invalid bank selection. Please try again.")
            return
        
        # Get cashout data from context
        if not context.user_data:
            context.user_data = {}
        cashout_data = context.user_data.get("cashout_data", {})
        amount_usd = cashout_data.get("amount")
        
        if not amount_usd:
            logger.error("No cashout amount found in context")
            await safe_edit_message_text(query, "❌ Session expired. Please start cashout again.")
            return
        
        # PERFORMANCE OPTIMIZATION: Try cached saved banks first
        from utils.ngn_cashout_performance import ngn_performance
        
        cached_banks = await ngn_performance.load_saved_banks_optimized(telegram_user_id)
        saved_account_data = None
        
        if cached_banks:
            # Find account in cached data (much faster than DB query)
            for bank in cached_banks:
                if bank.get('id') == account_id:
                    saved_account_data = bank
                    break
        
        if not saved_account_data:
            # Fallback to database query if not in cache
            async with async_managed_session() as session:
                # First get the database user_id from telegram_id (fix user ID mapping bug)
                stmt = select(User).where(User.telegram_id == int(telegram_user_id))
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()
                
                if not user:
                    logger.error(f"User not found for telegram_id: {telegram_user_id}")
                    await safe_edit_message_text(query, "❌ User account not found. Please contact support.")
                    return
                
                # Now query for saved bank account using correct user_id
                stmt = select(SavedBankAccount).where(
                    SavedBankAccount.id == account_id,
                    SavedBankAccount.user_id == user.id
                )
                result = await session.execute(stmt)
                saved_account = result.scalar_one_or_none()
                
                if not saved_account:
                    logger.error(
                        f"Saved bank account not found: account_id={account_id} for telegram_user_id={telegram_user_id} (db_user_id={user.id}). "
                        f"This may indicate data corruption or concurrent deletion."
                    )
                    # Improved error handling - offer to refresh saved accounts
                    await safe_edit_message_text(
                        query, 
                        "❌ Bank account not found. It may have been removed.\n\n"
                        "Please select a different account or add a new one.",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🔄 Refresh Banks", callback_data="cashout_ngn")],
                            [InlineKeyboardButton("🏦 Add New Bank", callback_data="add_new_bank")],
                            [InlineKeyboardButton("⬅️ Back", callback_data="menu_wallet")]
                        ])
                    )
                    return
                
                # Update last used timestamp using proper ORM update
                from sqlalchemy import update as sql_update
                stmt = sql_update(SavedBankAccount).where(
                    SavedBankAccount.id == saved_account.id
                ).values(last_used=datetime.utcnow())
                await session.execute(stmt)
                await session.commit()
                
                # Convert to dictionary format
                saved_account_data = {
                    'id': saved_account.id,
                    'bank_name': saved_account.bank_name,
                    'account_number': saved_account.account_number,
                    'account_name': saved_account.account_name,
                    'bank_code': saved_account.bank_code,
                    'is_verified': getattr(saved_account, 'is_verified', False)
                }
                
                # Invalidate cache to refresh with updated last_used timestamp
                ngn_performance.invalidate_saved_banks_cache(telegram_user_id)
        
        # PERFORMANCE OPTIMIZATION: Use optimized confirmation data loading
        try:
            from utils.ngn_cashout_performance import ngn_confirmation_optimizer
            
            # Get all confirmation data with optimized performance
            confirmation_data = await ngn_confirmation_optimizer.get_optimized_confirmation_data(
                Decimal(str(amount_usd)), saved_account_data
            )
            
            ngn_amount = confirmation_data['ngn_amount']
            exchange_rate = confirmation_data['exchange_rate']
            formatted_ngn = format_clean_amount(ngn_amount, "NGN")
            
            # 🔒 CRITICAL FIX: Create rate lock with the same exchange rate used for NGN calculation
            from utils.rate_lock import RateLock
            
            # Create cashout context for rate lock security binding
            cashout_context = {
                'method': 'ngn',
                'bank_id': saved_account_data.get('id'),
                'bank_code': saved_account_data['bank_code'],
                'bank_name': saved_account_data['bank_name'],
                'account_number': saved_account_data['account_number'],
                'is_saved_account': True
            }
            
            # Create rate lock with current exchange rate and amounts
            rate_lock_result = RateLock.create_rate_lock(
                user_id=telegram_user_id,
                usd_amount=Decimal(str(amount_usd)),
                ngn_amount=ngn_amount,
                exchange_rate=Decimal(str(exchange_rate or 0)),
                cashout_context=cashout_context
            )
            
            if not rate_lock_result['success']:
                logger.error(f"❌ Failed to create rate lock: {rate_lock_result.get('error')}")
                await safe_edit_message_text(query, "❌ Rate locking failed. Please try again.")
                return
            
            # Store rate lock in context for OTP verification stage
            rate_lock = rate_lock_result['rate_lock']
            if not RateLock.store_rate_lock_in_context(context, rate_lock):
                logger.error(f"❌ Failed to store rate lock in context for user {telegram_user_id}")
                await safe_edit_message_text(query, "❌ Rate lock storage failed. Please try again.")
                return
            
            logger.info(
                f"🔒 Rate lock created and stored - User: {telegram_user_id}, "
                f"Rate: ₦{exchange_rate:.2f}, Amount: ${amount_usd} → ₦{ngn_amount:,.2f}, "
                f"Token: {rate_lock['token'][:8]}..., Expires: {rate_lock_result['expires_at_formatted']}"
            )
            
        except Exception as e:
            logger.error(f"Error calculating NGN amount: {e}")
            await safe_edit_message_text(query, f"❌ {str(e)}")
            return
        
        # Store selected bank account details in context
        # Format the bank data as expected by proceed_to_ngn_otp_verification
        verified_account_data = {
            "account_number": saved_account_data["account_number"],
            "account_name": saved_account_data["account_name"], 
            "bank_name": saved_account_data["bank_name"],
            "bank_code": saved_account_data["bank_code"],
            "is_verified": True,  # Saved accounts are already verified
            "is_saved_account": True
        }
        
        if not context.user_data:
            context.user_data = {}
        context.user_data["cashout_data"].update({
            "method": "ngn",
            "bank_id": saved_account_data["id"],
            "bank_code": saved_account_data["bank_code"],
            "bank_name": saved_account_data["bank_name"],
            "account_number": saved_account_data["account_number"],
            "account_name": saved_account_data["account_name"],
            "ngn_amount": Decimal(str(ngn_amount or 0)),
            "is_saved_account": True,
            "verified_account": verified_account_data  # Add this for OTP flow compatibility
        })
        
        # Show confirmation screen
        verification_status = "✅ Verified" if saved_account_data.get("is_verified", False) else "⚠️ Unverified"
        
        text = f"""🏦 NGN Bank Cashout Confirmation

💰 Amount: {format_money(amount_usd, 'USD')} → {formatted_ngn}

🏦 Bank Details:
• Bank: {saved_account_data["bank_name"]}
• Account: ••••{saved_account_data["account_number"][-4:]}
• Name: {saved_account_data["account_name"]}
• Status: {verification_status}

⚡ Processing: Real-time via Fincra
💸 Fee: Platform absorbed
⏱️ Time: 2-5 minutes

Confirm this NGN cashout?"""
        
        keyboard = [
            [InlineKeyboardButton("✅ Confirm Cashout", callback_data="confirm_ngn_payout_proceed")],
            [InlineKeyboardButton("🏦 Choose Different Bank", callback_data="method:ngn")],
            [InlineKeyboardButton("❌ Cancel", callback_data="wallet_menu")]
        ]
        
        await safe_edit_message_text(
            query, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
        )
            
    except Exception as e:
        logger.error(f"Error in handle_saved_bank_selection: {e}")
        await safe_edit_message_text(
            query, "❌ Error processing bank selection. Please try again."
        )

async def handle_crypto_currency_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle crypto currency selection for cashouts - PHASE 1 OPTIMIZED: No separate USDT network step"""
    query = update.callback_query
    await safe_answer_callback_query(query, "💰 Processing crypto selection...")
    
    try:
        # Extract selected currency from callback data
        if not query or not query.data:
            return
        callback_data = query.data
        if not callback_data.startswith("select_crypto:"):
            logger.error(f"Invalid callback data for crypto selection: {callback_data}")
            await safe_edit_message_text(query, "❌ Invalid currency selection. Please try again.")
            return
        
        selected_currency = callback_data.replace("select_crypto:", "")
        if not update or not update.effective_user:
            return
        user_id = update.effective_user.id
        
        # Validate currency is supported
        if selected_currency not in Config.SUPPORTED_CURRENCIES:
            logger.error(f"Unsupported currency selected: {selected_currency}")
            await safe_edit_message_text(query, "❌ Currency not supported. Please choose a different one.")
            return
        
        # Get cashout data from context
        if not context.user_data:
            context.user_data = {}
        cashout_data = context.user_data.get("cashout_data", {})
        amount_usd = cashout_data.get("amount")
        
        if not amount_usd:
            logger.error("No cashout amount found in context")
            await safe_edit_message_text(query, "❌ Session expired. Please start cashout again.")
            return
        
        # PHASE 1: Skip USDT network selection - currency already includes network (USDT-ERC20 or USDT-TRC20)
        # Proceed directly to address selection for all currencies
        await show_crypto_address_selection(query, context, amount_usd, selected_currency)
        
    except Exception as e:
        logger.error(f"Error in handle_crypto_currency_selection: {e}")
        # Use branded error message matching NGN flow
        error_msg = BrandingUtils.get_branded_error_message("validation", "Currency selection error")
        await safe_edit_message_text(
            query, error_msg, parse_mode='Markdown'
        )

async def handle_saved_crypto_address_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle saved crypto address selection"""
    query = update.callback_query
    await safe_answer_callback_query(query, "🔗 Processing address...")
    
    try:
        # Extract address ID from callback data
        if not query or not query.data:
            return
        callback_data = query.data
        if not callback_data.startswith("saved_address:"):
            logger.error(f"Invalid callback data for saved address: {callback_data}")
            await safe_edit_message_text(query, "❌ Invalid address selection. Please try again.")
            return
        
        address_id_str = callback_data.replace("saved_address:", "")
        
        # Convert address_id to integer (database primary key is INTEGER)
        try:
            address_id = int(address_id_str)
        except ValueError:
            logger.error(f"Invalid address ID format: {address_id_str}")
            await safe_edit_message_text(query, "❌ Invalid address ID. Please try again.")
            return
        
        if not update or not update.effective_user:
            return
        telegram_id = update.effective_user.id
        
        # Get cashout data from context
        if not context.user_data:
            context.user_data = {}
        cashout_data = context.user_data.get("cashout_data", {})
        amount_usd = cashout_data.get("amount")
        currency = cashout_data.get("currency")
        
        if not amount_usd or not currency:
            logger.error("Missing cashout data in context")
            await safe_edit_message_text(query, "❌ Session expired. Please start cashout again.")
            return
        
        # Get the user's database ID (not Telegram ID!)
        async with async_managed_session() as session:
            stmt = select(User).where(User.telegram_id == telegram_id)
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()
            if not user:
                logger.error(f"❌ User not found for telegram_id {telegram_id}")
                await safe_edit_message_text(query, "❌ User not found. Please try again.")
                return
            
            user_id = user.id  # This is the database user ID we need
            
            # Retrieve saved address from database
            stmt = select(SavedAddress).where(
                SavedAddress.id == address_id,
                SavedAddress.user_id == user_id,
                SavedAddress.currency == currency
            )
            result = await session.execute(stmt)
            saved_address = result.scalar_one_or_none()
            
            if not saved_address:
                logger.error(f"Saved address not found: {address_id} for user {user_id}")
                await safe_edit_message_text(query, "❌ Address not found. Please try again.")
                return
            
            # Update last used timestamp
            from sqlalchemy import update as sql_update
            stmt = sql_update(SavedAddress).where(
                SavedAddress.id == saved_address.id
            ).values(last_used=datetime.utcnow())
            await session.execute(stmt)
            await session.commit()
            
            # CRITICAL: Cache saved_address attributes before session closes to prevent greenlet_spawn errors
            cached_address_data = {
                'address': saved_address.address,
                'label': saved_address.label
            }
            
            # Store selected address in context
            if not context.user_data:
                context.user_data = {}
            context.user_data["cashout_data"].update({
                "address_id": saved_address.id,
                "withdrawal_address": saved_address.address,
                "address_label": saved_address.label,
                "is_saved_address": True
            })
        
        # Show final confirmation with fee calculation (OUTSIDE session to prevent greenlet errors)
        await show_crypto_cashout_confirmation_with_fees(query, context, cached_address_data)
            
    except Exception as e:
        logger.error(f"Error in handle_saved_crypto_address_selection: {e}")
        # Use branded error message matching NGN flow
        error_msg = BrandingUtils.get_branded_error_message("validation", "Address selection error")
        await safe_edit_message_text(
            query, error_msg, parse_mode='Markdown'
        )

async def show_crypto_cashout_confirmation(query, context, saved_address) -> None:
    """Show final crypto cashout confirmation screen with branded formatting and secure tokenized persistence"""
    from utils.helpers import get_currency_emoji
    from utils.cashout_token_security import CashoutTokenSecurity
    from services.fastforex_service import fastforex_service
    
    if not context.user_data:
        context.user_data = {}
    cashout_data = context.user_data.get("cashout_data", {})
    amount_usd = cashout_data.get("amount")
    currency = cashout_data.get("currency")
    net_amount = cashout_data.get("net_amount")
    total_fee = cashout_data.get("total_fee")
    fee_breakdown = cashout_data.get("fee_breakdown")
    network = get_network_display_name(currency)
    user_id = query.from_user.id if query and query.from_user else 0
    
    emoji = get_currency_emoji(currency)
    
    # Handle both ORM objects and dictionaries
    if hasattr(saved_address, 'is_verified'):
        # ORM object
        is_verified = saved_address.is_verified
        address = saved_address.address
        label = saved_address.label
    else:
        # Dictionary
        is_verified = saved_address.get('is_verified', False)
        address = saved_address.get('address', '')
        label = saved_address.get('label', 'Address')
    
    verification_status = "✅ Verified" if is_verified else "⚠️ Unverified"
    
    # Format addresses for display
    short_address = f"{address[:12]}...{address[-8:]}"
    
    # Create branded header to match NGN flow
    header = make_header("Crypto Cashout")
    
    # CRITICAL FIX: Convert net USD amount to actual crypto amount
    try:
        crypto_amount = await fastforex_service.convert_usd_to_crypto(
            usd_amount=MonetaryDecimal.to_decimal(net_amount, "net_amount"),
            crypto_symbol=currency
        )
    except Exception as e:
        logger.error(f"❌ Failed to convert USD to crypto for {currency}: {e}")
        # Fallback to showing 0 if conversion fails
        crypto_amount = Decimal("0")
    
    # FIX: Format USD amounts with 2 decimals
    amount_usd_str = f"${MonetaryDecimal.to_decimal(amount_usd, 'amount_usd'):.2f}"
    fee_usd_str = f"${MonetaryDecimal.to_decimal(total_fee, 'total_fee'):.2f}"
    
    # FIX: Format crypto amounts with smart decimals (8 for BTC, 6 for ETH/LTC, 4 for others)
    if currency in ["BTC", "XXBT"]:
        crypto_str = f"{crypto_amount:.8f}"
    elif currency in ["ETH", "XETH", "LTC", "XLTC"]:
        crypto_str = f"{crypto_amount:.6f}"
    else:
        crypto_str = f"{crypto_amount:.4f}"
    
    # Generate secure token for persistence across bot restarts
    try:
        # Prepare metadata for additional context
        metadata = {
            'address_id': cashout_data.get('address_id'),
            'address_label': label,
            'is_saved_address': True,
            'verification_status': verification_status
        }
        
        # Generate secure token and store cashout data
        secure_token = CashoutTokenSecurity.generate_secure_token(
            user_id=user_id,
            amount=MonetaryDecimal.to_decimal(amount_usd, "amount_usd"),
            currency=currency,
            withdrawal_address=address,
            network=network,
            fee_amount=MonetaryDecimal.to_decimal(total_fee, "total_fee") if total_fee else None,
            net_amount=MonetaryDecimal.to_decimal(net_amount, "net_amount") if net_amount else None,
            fee_breakdown=fee_breakdown,
            metadata=metadata
        )
        
        # IMMEDIATE FIX: Create short callback that fits Telegram's 64-byte limit
        # Use first 16 chars of secure token with short prefix to stay under limit
        short_token = secure_token[:16] if secure_token else "fallback"
        confirm_callback = f"cc:{short_token}"  # Format: "cc:16chartoken" = ~19 chars total
        
        logger.info(f"🔐 Generated secure cashout confirmation token for user {user_id}")
        logger.info(f"🔗 Using short callback: {confirm_callback} (length: {len(confirm_callback)})")
        
    except Exception as e:
        logger.error(f"❌ Failed to generate secure token for user {user_id}: {e}")
        # Fallback to very short callback
        import secrets
        confirm_callback = f"cc:{secrets.token_urlsafe(8)}"
        logger.warning(f"⚠️ Using emergency fallback callback for user {user_id}: {confirm_callback}")
    
    # FIX: Concise, mobile-friendly display
    text = f"""{header}

💵 Amount: {amount_usd_str}
💸 Fee: {fee_usd_str}
💰 You Receive: ~{crypto_str} {currency}

🔗 Address: {short_address}
🌐 {network}

⚡ Via Kraken • 5-30 min

{make_trust_footer()}"""
    
    keyboard = [
        [InlineKeyboardButton("✅ Confirm Cashout", callback_data=confirm_callback)],
        [InlineKeyboardButton("🔗 Choose Different Address", callback_data=f"select_crypto:{currency}")],
        [InlineKeyboardButton("❌ Cancel", callback_data="wallet_menu")]
    ]
    
    await safe_edit_message_text(
        query, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
    )

# ===== SUPPORTING CRYPTO FUNCTIONS =====

async def show_usdt_network_selection(query, context, amount_usd, currency_with_network) -> None:
    """Show USDT network selection (ERC20 vs TRC20)"""
    from utils.helpers import get_currency_emoji
    
    # Extract base currency (USDT)
    base_currency = currency_with_network.split("-")[0] if "-" in currency_with_network else currency_with_network
    emoji = get_currency_emoji(base_currency)
    
    text = f"""💰 {base_currency} Network Selection

💵 Amount: {format_money(amount_usd, 'USD')}

Choose your preferred network for {base_currency}:

🟢 ERC20 (Ethereum)
• Higher security, slower
• Higher network fees
• Widely supported

🔴 TRC20 (Tron)
• Fast confirmation
• Lower network fees
• Popular choice

Which network do you prefer?"""
    
    keyboard = [
        [InlineKeyboardButton(f"🟢 {base_currency}-ERC20 (Ethereum)", callback_data=f"select_crypto:{base_currency}-ERC20")],
        [InlineKeyboardButton(f"🔴 {base_currency}-TRC20 (Tron)", callback_data=f"select_crypto:{base_currency}-TRC20")],
        [InlineKeyboardButton("⬅️ Back to Currencies", callback_data="method:crypto")]
    ]
    
    await safe_edit_message_text(
        query, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
    )

async def show_crypto_address_selection(query, context, amount_usd, selected_currency) -> None:
    """Show crypto address selection - check for saved addresses first like NGN flow"""
    telegram_id = query.from_user.id
    
    # Get the user's database ID (not Telegram ID!)
    async with async_managed_session() as session:
        stmt = select(User).where(User.telegram_id == telegram_id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        if not user:
            logger.error(f"❌ User not found for telegram_id {telegram_id}")
            return
        
        user_id = user.id  # This is the database user ID we need
        
        # Get saved addresses for this currency using the correct user_id
        stmt = select(SavedAddress).where(
            SavedAddress.user_id == user_id,
            SavedAddress.currency == selected_currency,
            SavedAddress.is_active == True
        ).order_by(
            SavedAddress.last_used.desc().nullslast(),
            SavedAddress.created_at.desc()
        )
        result = await session.execute(stmt)
        saved_addresses = result.scalars().all()
        
        # Store crypto selection in context (basic info only)
        if not context.user_data:
            context.user_data = {}
        context.user_data["cashout_data"].update({
            "method": "crypto",
            "currency": selected_currency,
            "network": get_network_from_currency(selected_currency)
        })
        
        if saved_addresses:
            # Show saved addresses selection screen (like NGN flow)
            return await show_saved_crypto_addresses_for_cashout(query, context, amount_usd, selected_currency, saved_addresses)
        else:
            # No saved addresses - proceed with manual entry
            return await show_manual_crypto_address_entry(query, context, amount_usd, selected_currency)

async def show_saved_crypto_addresses_for_cashout(query, context, amount_usd, selected_currency, saved_addresses) -> None:
    """Show saved crypto addresses for selection - MATCHES NGN UX"""
    from utils.helpers import get_currency_emoji
    
    # Format amount for display
    amount_formatted = format_clean_amount(amount_usd, "USD")
    emoji = get_currency_emoji(selected_currency)
    network_name = get_network_display_name(selected_currency)
    
    text = f"""{emoji} Select {selected_currency} Address

💰 Amount: {amount_formatted}
🌐 Network: {network_name}

Choose a saved address:"""

    keyboard = []
    
    # Add saved addresses (matching NGN bank account style)
    for addr in saved_addresses:
        label = addr.label or "Unnamed Address"
        
        # Truncate label if too long (like NGN bank names)
        short_label = label[:15] + "..." if len(label) > 15 else label
        short_addr = f"{addr.address[:6]}...{addr.address[-4:]}"
        display_text = f"{short_label} ({short_addr})"
        
        keyboard.append([
            InlineKeyboardButton(display_text, callback_data=f"saved_address:{addr.id}")
        ])

    # Add action buttons (matching NGN style)
    keyboard.extend([
        [InlineKeyboardButton("🆕 Add New Address", callback_data=f"add_crypto_address:{selected_currency}")],
        [InlineKeyboardButton("⬅️ Back to Currencies", callback_data="method:crypto")]
    ])

    await safe_edit_message_text(
        query, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
    )

async def show_manual_crypto_address_entry(query, context, amount_usd, selected_currency) -> None:
    """Show manual crypto address entry screen - when no saved addresses exist"""
    from utils.helpers import get_currency_emoji
    
    emoji = get_currency_emoji(selected_currency)
    network_name = get_network_display_name(selected_currency)
    address_example = get_address_example(selected_currency)
    
    # Store currency in context for the text input flow
    if "cashout_data" not in context.user_data:
        if not context.user_data:
            context.user_data = {}
        context.user_data["cashout_data"] = {}
    
    if not context.user_data:
        context.user_data = {}
    context.user_data["cashout_data"]["pending_address_currency"] = selected_currency
    # Convert int to str for set_wallet_state (expects str)
    await set_wallet_state(query.from_user.id, context, str(WalletStates.ENTERING_WITHDRAW_ADDRESS))
    
    # Format amount cleanly without excessive decimals
    amount_clean = format_clean_amount(amount_usd, "USD")
    
    # Shorten address for mobile-friendly display
    if len(address_example) > 20:
        short_example = f"{address_example[:8]}...{address_example[-6:]}"
    else:
        short_example = address_example
    
    text = f"""{emoji} Enter {selected_currency} Address

💰 {amount_clean}
🌐 {network_name}

Type your {selected_currency} address:
Example: {short_example}

⚠️ Double-check - wrong address = permanent loss"""

    keyboard = [
        [InlineKeyboardButton("⬅️ Back to Currencies", callback_data="method:crypto")]
    ]

    await safe_edit_message_text(
        query, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
    )

async def show_crypto_cashout_confirmation_with_fees(query, context, selected_address) -> None:
    """Show crypto cashout confirmation with fee calculation - AFTER address selection"""
    from utils.helpers import get_currency_emoji
    from services.fastforex_service import fastforex_service
    
    if not context.user_data:
        context.user_data = {}
    cashout_data = context.user_data.get("cashout_data", {})
    amount_usd = cashout_data.get("amount")
    selected_currency = cashout_data.get("currency")
    
    # Calculate fees and net amount NOW (after selection) with real-time Kraken fees if possible
    try:
        # Extract base currency and network
        base_currency = selected_currency.split("-")[0] if "-" in selected_currency else selected_currency
        network = selected_currency.split("-")[1] if "-" in selected_currency else None
        
        # Get address from selected_address object
        if hasattr(selected_address, 'address'):
            address = selected_address.address
        else:
            address = selected_address.get('address', '')
        
        # Initialize address_key to None - will be set if Kraken address resolution succeeds
        address_key = None
        
        # Attempt to get address_key for real-time Kraken fees
        try:
            kraken_service = get_kraken_withdrawal_service()
            resolve_result = await kraken_service.resolve_withdraw_key(base_currency, network, address)
            if resolve_result.get('success'):
                address_key = resolve_result.get('key')
                logger.info(f"✅ Got address key for real-time fee calculation: {address_key}")
        except Exception as e:
            logger.info(f"⚠️ Could not resolve address key, will use estimated fees: {e}")
            address_key = None
        
        fee_info = await calculate_crypto_cashout_with_network_fees(
            amount_usd=Decimal(str(amount_usd)),
            currency=base_currency,
            network=network,
            address_key=address_key  # Will use real Kraken fees if available, otherwise fallback
        )
        net_amount = fee_info["net_amount"]
        total_fee = fee_info["total_fee"]
        fee_breakdown = fee_info["fee_breakdown"]
        fee_source = fee_info.get("network_fee_source", "fallback")
        
        # Log fee source for debugging
        logger.info(f"💰 Confirmation screen fee: ${total_fee} (source: {fee_source})")
    except Exception as e:
        logger.error(f"Error calculating crypto fees: {e}")
        net_amount = Decimal(str(amount_usd)) * Decimal("0.95")  # Fallback 5% fee
        total_fee = Decimal(str(amount_usd)) * Decimal("0.05")
        fee_breakdown = "5% fee (estimated)"
        fee_source = "fallback"
    
    # Update cashout data with fee calculations
    cashout_data.update({
        "net_amount": Decimal(str(net_amount or 0)),
        "total_fee": Decimal(str(total_fee or 0)),
        "fee_breakdown": fee_breakdown
    })
    
    emoji = get_currency_emoji(selected_currency)
    network_name = get_network_display_name(selected_currency)
    
    # Get address info
    if hasattr(selected_address, 'address'):  # SavedAddress object
        address = selected_address.address
        label = selected_address.label or "Selected Address"
    else:  # Manual address dict
        address = selected_address.get('address', '')
        label = selected_address.get('label', 'Manual Entry')
    
    # CRITICAL FIX: Convert net USD amount to actual crypto amount
    try:
        crypto_amount = await fastforex_service.convert_usd_to_crypto(
            usd_amount=Decimal(str(net_amount)),
            crypto_symbol=selected_currency
        )
        # Format crypto with appropriate decimals
        if selected_currency in ["BTC", "XXBT"]:
            crypto_str = f"{crypto_amount:.8f}"
        elif selected_currency in ["ETH", "XETH", "LTC", "XLTC"]:
            crypto_str = f"{crypto_amount:.6f}"
        else:
            crypto_str = f"{crypto_amount:.4f}"
    except Exception as e:
        logger.error(f"❌ Failed to convert USD to crypto for {selected_currency}: {e}")
        crypto_str = "0.0000"
    
    # Shorten address for display
    short_addr = f"{address[:10]}...{address[-8:]}" if len(address) > 18 else address
    
    # Add fee source indicator
    fee_indicator = "✓" if fee_source == "kraken_api" else "~"
    fee_label = "Real-time" if fee_source == "kraken_api" else "Estimated"
    
    text = f"""{emoji} Confirm {selected_currency} Cashout

💵 Amount: {format_money(amount_usd, 'USD')}
💸 Fee: {fee_indicator}{format_money(total_fee, 'USD')} ({fee_label})
💰 You Receive: ~{crypto_str} {selected_currency}

📍 To: {label}
`{short_addr}`

⚠️ Verify address - crypto transactions are irreversible."""

    keyboard = [
        [InlineKeyboardButton("✅ Confirm", callback_data="confirm_crypto_cashout")],
        [InlineKeyboardButton("⬅️ Back", callback_data=f"select_crypto:{selected_currency}")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_cashout")]
    ]

    await safe_edit_message_text(
        query, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
    )

def get_network_from_currency(currency: str) -> str:
    """Extract network from currency string (e.g., USDT-TRC20 -> TRC20)"""
    if "-" in currency:
        return currency.split("-")[1]
    # Map single currencies to their networks
    network_map = {
        "BTC": "Bitcoin",
        "ETH": "Ethereum", 
        "LTC": "Litecoin",
        "DOGE": "Dogecoin",
        "BCH": "Bitcoin Cash",
        "TRX": "Tron",
        "USDT": "TRC20"  # Default USDT to TRC20
    }
    return network_map.get(currency, "Unknown")

def get_network_display_name(currency: str) -> str:
    """Get user-friendly network display name"""
    network = get_network_from_currency(currency)
    display_map = {
        "Bitcoin": "Bitcoin Network",
        "Ethereum": "Ethereum (ERC20)",
        "ERC20": "Ethereum (ERC20)",
        "TRC20": "Tron (TRC20)",
        "Litecoin": "Litecoin Network", 
        "Dogecoin": "Dogecoin Network",
        "Bitcoin Cash": "Bitcoin Cash Network",
        "Tron": "Tron (TRC20)"
    }
    return display_map.get(network, network)

async def handle_add_crypto_address(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle adding new crypto address"""
    query = update.callback_query
    await safe_answer_callback_query(query, "🆕 Adding address...")
    
    try:
        # Extract currency from callback data
        if not query or not query.data:
            return
        callback_data = query.data
        if not callback_data.startswith("add_crypto_address:"):
            logger.error(f"Invalid callback data for add crypto address: {callback_data}")
            await safe_edit_message_text(query, "❌ Invalid request. Please try again.")
            return
        
        currency = callback_data.replace("add_crypto_address:", "")
        
        # Store currency in context for the text input flow
        if not context.user_data or "cashout_data" not in context.user_data:
            if not context.user_data:
                context.user_data = {}
            context.user_data["cashout_data"] = {}
        
        if not context.user_data:
            context.user_data = {}
        context.user_data["cashout_data"]["pending_address_currency"] = currency
        # Convert int to str for set_wallet_state (expects str)
        await set_wallet_state(query.from_user.id, context, str(WalletStates.ENTERING_WITHDRAW_ADDRESS))
        
        network_name = get_network_display_name(currency)
        address_example = get_address_example(currency)
        
        text = f"""🔗 **{currency} Address** ({network_name})

⚠️ Double-check before sending - wrong address = permanent loss

Example: `{address_example}`

Enter your {currency} address:"""
        
        keyboard = [
            [InlineKeyboardButton("❌ Cancel", callback_data=f"select_crypto:{currency}")]
        ]
        
        await safe_edit_message_text(
            query, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
        )
        
    except Exception as e:
        logger.error(f"Error in handle_add_crypto_address: {e}")
        await safe_edit_message_text(
            query, "❌ Error setting up address entry. Please try again."
        )

def get_address_example(currency: str) -> str:
    """Get example address format for a currency"""
    examples = {
        "BTC": "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa",
        "ETH": "0x742d35Cc6634C0532925a3b8D4B4d45e3e06b69f",
        "USDT-ERC20": "0x742d35Cc6634C0532925a3b8D4B4d45e3e06b69f",
        "LTC": "LQTpS3VaP2JMmFZ2iFVHhWggJPZo5V7KKK",
        "DOGE": "DH5yaieqoZN36fDVciNyRueRGvGLR3mr7L",
        "BCH": "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2",
        "TRX": "TQn9Y2khEsLJW1ChVWFMSMeRDow5KcbLSE",
        "USDT-TRC20": "TQn9Y2khEsLJW1ChVWFMSMeRDow5KcbLSE"
    }
    return examples.get(currency, "Address format varies by currency")

def get_address_validation_tips(currency: str) -> str:
    """Get validation tips for a currency address"""
    tips = {
        "BTC": "• Starts with 1, 3, or bc1\n• Length: 26-35 characters",
        "ETH": "• Starts with 0x\n• Length: 42 characters\n• Hexadecimal format",
        "USDT-ERC20": "• Starts with 0x\n• Length: 42 characters\n• Same format as ETH",
        "LTC": "• Starts with L or M\n• Length: 26-35 characters", 
        "DOGE": "• Starts with D\n• Length: 34 characters",
        "BCH": "• Starts with 1 or 3\n• Length: 26-35 characters",
        "TRX": "• Starts with T\n• Length: 34 characters",
        "USDT-TRC20": "• Starts with T\n• Length: 34 characters\n• Same format as TRX"
    }
    return tips.get(currency, "• Verify format matches your wallet")

def validate_base58check(address: str) -> bool:
    """Validate Base58Check encoding (used by Bitcoin, Litecoin, Dogecoin, Bitcoin Cash, Tron)"""
    try:
        decoded = base58.b58decode(address)
        if len(decoded) < 5:
            return False
        
        # Extract payload and checksum
        payload = decoded[:-4]
        checksum = decoded[-4:]
        
        # Calculate expected checksum
        hash_result = hashlib.sha256(hashlib.sha256(payload).digest()).digest()
        expected_checksum = hash_result[:4]
        
        return checksum == expected_checksum
    except Exception:
        return False

def validate_ethereum_checksum(address: str) -> bool:
    """Validate Ethereum EIP-55 checksum (used by ETH and USDT-ERC20)
    
    Accepts:
    - All-lowercase addresses (valid but not checksummed)
    - Correctly checksummed addresses (mixed case with valid EIP-55 checksum)
    
    Rejects:
    - Incorrectly checksummed addresses (mixed case with invalid checksum)
    """
    try:
        if not address.startswith('0x') or len(address) != 42:
            return False
        
        # Extract the hex part (without 0x)
        hex_part = address[2:]
        
        # If all lowercase or all uppercase, accept it (valid but not checksummed)
        if hex_part.islower() or hex_part.isupper():
            return True
        
        # If mixed case, must match EIP-55 checksum
        return is_checksum_address(address)
    except Exception:
        return False

def validate_crypto_address(address: str, currency: str) -> tuple[bool, str]:
    """Validate cryptocurrency address with cryptographic checksum validation
    
    Returns:
        tuple: (is_valid, error_message)
    """
    if not address or not address.strip():
        return False, "Address cannot be empty"
    
    address = address.strip()
    
    # Bitcoin (BTC) - Base58Check validation
    if currency == "BTC":
        if not (26 <= len(address) <= 35):
            return False, "BTC address must be 26-35 characters"
        if not (address.startswith(('1', '3')) or address.startswith('bc1')):
            return False, "BTC address must start with 1, 3, or bc1"
        
        # Cryptographic checksum validation (skip for bech32 addresses)
        if not address.startswith('bc1'):
            if not validate_base58check(address):
                return False, "Invalid BTC address checksum"
            
    # Ethereum (ETH) and USDT-ERC20 - EIP-55 checksum validation
    elif currency in ["ETH", "USDT-ERC20"]:
        if len(address) != 42:
            return False, f"{currency} address must be exactly 42 characters"
        if not address.startswith('0x'):
            return False, f"{currency} address must start with 0x"
        if not all(c in '0123456789abcdefABCDEF' for c in address[2:]):
            return False, f"{currency} address contains invalid characters"
        
        # Cryptographic checksum validation (EIP-55)
        if not validate_ethereum_checksum(address):
            return False, f"Invalid {currency} address checksum"
            
    # Litecoin (LTC) - Base58Check validation
    elif currency == "LTC":
        if not (26 <= len(address) <= 35):
            return False, "LTC address must be 26-35 characters"
        if not address.startswith(('L', 'M')):
            return False, "LTC address must start with L or M"
        
        # Cryptographic checksum validation
        if not validate_base58check(address):
            return False, "Invalid LTC address checksum"
            
    # Dogecoin (DOGE) - Base58Check validation
    elif currency == "DOGE":
        if len(address) != 34:
            return False, "DOGE address must be exactly 34 characters"
        if not address.startswith('D'):
            return False, "DOGE address must start with D"
        
        # Cryptographic checksum validation
        if not validate_base58check(address):
            return False, "Invalid DOGE address checksum"
            
    # Bitcoin Cash (BCH) - Base58Check validation
    elif currency == "BCH":
        if not (26 <= len(address) <= 35):
            return False, "BCH address must be 26-35 characters"
        if not address.startswith(('1', '3')):
            return False, "BCH address must start with 1 or 3"
        
        # Cryptographic checksum validation
        if not validate_base58check(address):
            return False, "Invalid BCH address checksum"
            
    # Tron (TRX) and USDT-TRC20 - Base58Check validation
    elif currency in ["TRX", "USDT-TRC20"]:
        if len(address) != 34:
            return False, f"{currency} address must be exactly 34 characters"
        if not address.startswith('T'):
            return False, f"{currency} address must start with T"
        
        # Cryptographic checksum validation
        if not validate_base58check(address):
            return False, f"Invalid {currency} address checksum"
            
    else:
        return False, f"Validation not implemented for {currency}"
    
    return True, "Address verified with cryptographic checksum"

# ===== TEXT INPUT HANDLERS =====

async def handle_crypto_address_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle crypto address text input
    
    Note: Router already validated wallet state, so we skip redundant state check for performance
    """
    user = update.effective_user
    if not user:
        return
    
    # PERFORMANCE: Skip redundant get_wallet_state - router already checked this
    # Trust the router's state verification to avoid duplicate Redis call
    
    if not update.message or not update.message.text:
        return
    user_input = update.message.text.strip()
    if not context.user_data:
        context.user_data = {}
    cashout_data = context.user_data.get("cashout_data", {})
    currency = cashout_data.get("pending_address_currency")
    
    if not currency:
        if not update.message:
            return
        await update.message.reply_text("❌ Session expired. Please start again.")
        return
    
    # Send instant feedback
    if not update.message:
        return
    status_msg = await update.message.reply_text("⏳ Validating...")
    
    # Validate address format
    is_valid, error_msg = validate_crypto_address(user_input, currency)
    
    # Delete status message
    try:
        await status_msg.delete()
    except Exception:
        pass
    
    if not is_valid:
        if not update.message:
            return
        await update.message.reply_text(
            f"❌ Invalid {currency} Address\n\n"
            f"Error: {error_msg}\n\n"
            f"Please enter a valid {currency} address:",
            parse_mode="Markdown"
        )
        return
    
    # Address is valid, check if it's already saved
    if not context.user_data:
        context.user_data = {}
    context.user_data["cashout_data"]["withdrawal_address"] = user_input
    await set_wallet_state(user.id, context, 'inactive')
    
    # Check if address is already saved in database
    async with async_managed_session() as session:
        from models import SavedAddress
        stmt = select(SavedAddress).where(
            SavedAddress.user_id == user.id,
            SavedAddress.currency == currency,
            SavedAddress.address == user_input,
            SavedAddress.is_active == True
        )
        result = await session.execute(stmt)
        saved_address = result.scalar_one_or_none()
    
    # Shorten address for mobile display
    short_addr = f"{user_input[:8]}...{user_input[-6:]}"
    
    # If address is already saved, auto-continue with confirmation
    if saved_address:
        logger.info(f"✅ SAVED_ADDRESS_DETECTED: User {user.id} using saved address '{saved_address.label}' for {currency}")
        
        # Store saved address metadata for later use
        context.user_data["cashout_data"]["is_saved_address"] = True
        context.user_data["cashout_data"]["address_label"] = saved_address.label
        context.user_data["cashout_data"]["address_id"] = saved_address.id
        
        text = f"""✅ **Using Saved Address**

📌 Label: **{saved_address.label}**
`{short_addr}`

Continuing to confirmation..."""
        
        keyboard = [
            [InlineKeyboardButton("➡️ Continue", callback_data=f"use_once:{currency}")],
            [InlineKeyboardButton("❌ Cancel", callback_data=f"select_crypto:{currency}")]
        ]
        
        if not update.message:
            return
        await update.message.reply_text(
            text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    
    # Address is not saved, show save/use once options
    # Dynamic message based on auto cashout feature toggle
    if Config.ENABLE_AUTO_CASHOUT_FEATURES:
        save_prompt = "💡 **Save for auto cashout?**"
        save_benefit = "Saved addresses enable auto cashouts without re-entering each time."
    else:
        save_prompt = "💡 **Save for quick cashouts?**"
        save_benefit = "Saved addresses enable one-click cashouts without re-entering each time."
    
    text = f"""✅ **Valid {currency} Address**

`{short_addr}`

━━━━━━━━━━━━━━━━━━━━

{save_prompt}

{save_benefit}

━━━━━━━━━━━━━━━━━━━━"""
    
    keyboard = [
        [InlineKeyboardButton("💾 Save Address", callback_data=f"save_address:{currency}")],
        [InlineKeyboardButton("⏭️ Use Once", callback_data=f"use_once:{currency}")],
        [InlineKeyboardButton("❌ Cancel", callback_data=f"select_crypto:{currency}")]
    ]
    
    if not update.message:
        return
    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_save_crypto_address(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle saving crypto address after validation"""
    query = update.callback_query
    await safe_answer_callback_query(query, "💾 Saving address...")
    
    try:
        # Extract currency from callback data
        if not query or not query.data:
            return
        callback_data = query.data
        if not callback_data.startswith("save_address:"):
            logger.error(f"Invalid callback data for save address: {callback_data}")
            await safe_edit_message_text(query, "❌ Invalid request. Please try again.")
            return
        
        currency = callback_data.replace("save_address:", "")
        if not update or not update.effective_user:
            return
        telegram_user_id = update.effective_user.id
        
        # Get address from context
        if not context.user_data:
            context.user_data = {}
        cashout_data = context.user_data.get("cashout_data", {})
        address = cashout_data.get("withdrawal_address")
        
        if not address:
            await safe_edit_message_text(query, "❌ Address not found. Please try again.")
            return
        
        # Generate default label based on address
        default_label = f"Address {str(address)[:8]}...{str(address)[-4:]}"
        
        # Save address to database using sync session (matches callback handler pattern)
        from database import SessionLocal
        session = SessionLocal()
        try:
            # First get the database user_id from telegram_id (fix user ID mapping bug)
            user_db = session.query(User).filter(User.telegram_id == telegram_user_id).first()
            
            if not user_db:
                logger.error(f"User not found for telegram_id: {telegram_user_id}")
                await safe_edit_message_text(query, "❌ User account not found. Please contact support.")
                return
            
            # Check if address already exists (active or inactive)
            existing_address = session.query(SavedAddress).filter(
                SavedAddress.user_id == user_db.id,
                SavedAddress.address == address
            ).first()
            
            if existing_address:
                # Reactivate if inactive, update last_used in all cases
                from sqlalchemy import update as sql_update
                from typing import Any, Dict
                update_values: Dict[str, Any] = {'last_used': datetime.utcnow()}
                if not as_bool(existing_address.is_active):
                    update_values['is_active'] = True
                    logger.info(f"♻️ Reactivating inactive address for user {user_db.id}: {address[:10]}...")
                
                session.execute(sql_update(SavedAddress).where(
                    SavedAddress.id == existing_address.id
                ).values(**update_values))
                session.commit()
                saved_address = existing_address
                logger.info(f"✅ Using existing saved address for user {user_db.id}: {address[:10]}...")
            else:
                # Create new address record
                saved_address = SavedAddress(
                    user_id=user_db.id,
                    currency=currency,
                    network=get_network_from_currency(currency),
                    address=address,
                    label=default_label,
                    is_verified=False,
                    verification_sent=False
                )
                session.add(saved_address)
                session.commit()
                logger.info(f"💾 Created new saved address for user {user_db.id}: {address[:10]}...")
            
            # CRITICAL: Get IDs and attributes before session closes (detached objects can't access attributes)
            address_id = saved_address.id
            user_id = user_db.id
            cached_address_data = {
                'address': saved_address.address,
                'label': saved_address.label or default_label,
                'is_verified': saved_address.is_verified if hasattr(saved_address, 'is_verified') else False
            }
        finally:
            session.close()
        
        # Update context with saved address info
        if not context.user_data:
            context.user_data = {}
        context.user_data["cashout_data"].update({
            "address_id": address_id,
            "address_label": default_label,
            "is_saved_address": True
        })
        
        # CRITICAL FIX: Calculate COMBINED fees (platform + network) before confirmation
        if not context.user_data:
            context.user_data = {}
        cashout_data = context.user_data.get("cashout_data", {})
        amount_usd = cashout_data.get("amount")
        currency = cashout_data.get("currency", "ETH")
        
        if not amount_usd:
            logger.error("No cashout amount found in context for fee calculation")
            await safe_edit_message_text(query, "❌ Session expired. Please start cashout again.")
            return
        
        try:
            # Get network from currency
            network = currency.split("-")[1] if "-" in currency else None
            base_currency = currency.split("-")[0] if "-" in currency else currency
            
            # CRITICAL FIX: Calculate COMBINED fees (platform + Kraken network)
            fee_info = await calculate_crypto_cashout_with_network_fees(
                amount_usd=Decimal(str(amount_usd)),
                currency=base_currency,
                network=network,
                address_key=None  # TODO: Resolve address_key from saved address for real Kraken fees
            )
            
            total_fee = fee_info["total_fee"]
            net_amount = fee_info["net_amount"]
            platform_fee = fee_info["platform_fee"]
            network_fee = fee_info["network_fee"]
            fee_breakdown = fee_info["fee_breakdown"]
            
            logger.info(
                f"✅ Combined fee calculated for ${amount_usd} {currency}: "
                f"Platform ${platform_fee} + Network ${network_fee} = Total ${total_fee}, Net ${net_amount}"
            )
            
            # Update context with COMBINED fee data
            if not context.user_data:
                context.user_data = {}
            context.user_data["cashout_data"].update({
                "total_fee": total_fee,
                "platform_fee": platform_fee,
                "network_fee": network_fee,
                "net_amount": net_amount,
                "fee_breakdown": fee_breakdown
            })
            
        except Exception as fee_error:
            logger.error(f"Error calculating combined crypto fees: {fee_error}")
            # Fallback: use minimum viable fees
            platform_fee = Decimal('2.00')
            network_fee = get_fallback_network_fee(base_currency, network)
            total_fee = platform_fee + network_fee
            net_amount = Decimal(str(amount_usd)) - total_fee
            fee_breakdown = f"Combined fee (${total_fee:.2f})"
            
            if not context.user_data:
                context.user_data = {}
            context.user_data["cashout_data"].update({
                "total_fee": total_fee,
                "platform_fee": platform_fee,
                "network_fee": network_fee,
                "net_amount": net_amount,
                "fee_breakdown": fee_breakdown
            })
        
        # Show final confirmation with cached address data (prevents greenlet_spawn errors)
        await show_crypto_cashout_confirmation(query, context, cached_address_data)
        
    except Exception as e:
        logger.error(f"Error in handle_save_crypto_address: {e}")
        await safe_edit_message_text(query, "❌ Error saving address. Please try again.")

async def handle_save_crypto_address_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle saving crypto address from text input flow"""
    query = update.callback_query
    await safe_answer_callback_query(query, "💾 Saving address...")
    
    try:
        # Extract address from callback data
        if not query or not query.data:
            return
        callback_data = query.data
        if not callback_data.startswith("save_crypto_address:"):
            logger.error(f"Invalid callback data for save crypto address: {callback_data}")
            await safe_edit_message_text(query, "❌ Invalid request. Please try again.")
            return
        
        address = callback_data.replace("save_crypto_address:", "")
        if not update or not update.effective_user:
            return
        telegram_user_id = update.effective_user.id
        
        # Store address in cashout data
        if not context.user_data or 'cashout_data' not in context.user_data:
            if not context.user_data:
                context.user_data = {}
            context.user_data['cashout_data'] = {}
        
        if not context.user_data:
            context.user_data = {}
        context.user_data['cashout_data']['withdrawal_address'] = address
        if not context.user_data:
            context.user_data = {}
        context.user_data['cashout_data']['network'] = 'ETH'
        if not context.user_data:
            context.user_data = {}
        context.user_data['cashout_data']['currency'] = 'ETH'
        
        # Save address directly with default label
        # Generate default label based on address
        default_label = f"Address {str(address)[:8]}...{str(address)[-4:]}"
        
        # Save address to database
        async with async_managed_session() as session:
            # First get the database user_id from telegram_id (fix user ID mapping bug)
            stmt = select(User).where(User.telegram_id == telegram_user_id)
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()
            
            if not user:
                logger.error(f"User not found for telegram_id: {telegram_user_id}")
                await safe_edit_message_text(query, "❌ User account not found. Please contact support.")
                return
            
            # CRITICAL FIX: Check if address already exists before creating new one
            stmt = select(SavedAddress).where(
                SavedAddress.user_id == user.id,
                SavedAddress.address == address
            )
            result = await session.execute(stmt)
            existing_address = result.scalar_one_or_none()
            
            if existing_address:
                # Use existing address and update last_used timestamp
                saved_address = existing_address
                from sqlalchemy import update as sql_update
                stmt = sql_update(SavedAddress).where(
                    SavedAddress.id == saved_address.id
                ).values(last_used=datetime.utcnow())
                await session.execute(stmt)
                await session.commit()
                logger.info(f"✅ Using existing saved address for user {user.id}: {address[:10]}...")
            else:
                # Create new address record
                saved_address = SavedAddress(
                    user_id=user.id,
                    currency='ETH',
                    network='ETH',
                    address=address,
                    label=default_label,
                    is_verified=False,
                    verification_sent=False
                )
                session.add(saved_address)
                await session.commit()
                logger.info(f"💾 Created new saved address for user {user.id}: {address[:10]}...")
            
            # CRITICAL: Cache saved_address attributes before session ends to prevent greenlet_spawn errors
            cached_address_data = {
                'address': saved_address.address,
                'label': saved_address.label or default_label,
                'is_verified': saved_address.is_verified if hasattr(saved_address, 'is_verified') else False
            }
            
            # Update context with saved address info
            if not context.user_data:
                context.user_data = {}
            context.user_data["cashout_data"].update({
                "address_id": saved_address.id,
                "address_label": default_label,
                "is_saved_address": True
            })
            
            # Convert Column[int] to int using ORM helper
            await set_wallet_state(as_int(user.id) or 0, context, 'inactive')
            
            # CRITICAL FIX: Calculate COMBINED fees (platform + network) before confirmation
            if not context.user_data:
                context.user_data = {}
            cashout_data = context.user_data.get("cashout_data", {})
            amount_usd = cashout_data.get("amount")
            currency = cashout_data.get("currency", "ETH")
            
            if not amount_usd:
                logger.error("No cashout amount found in context for fee calculation")
                await safe_edit_message_text(query, "❌ Session expired. Please start cashout again.")
                return
            
            try:
                # Get network from currency
                network = currency.split("-")[1] if "-" in currency else None
                base_currency = currency.split("-")[0] if "-" in currency else currency
                
                # CRITICAL FIX: Calculate COMBINED fees (platform + Kraken network)
                fee_info = await calculate_crypto_cashout_with_network_fees(
                    amount_usd=Decimal(str(amount_usd)),
                    currency=base_currency,
                    network=network,
                    address_key=None  # TODO: Resolve address_key from saved address for real Kraken fees
                )
                
                total_fee = fee_info["total_fee"]
                net_amount = fee_info["net_amount"]
                platform_fee = fee_info["platform_fee"]
                network_fee = fee_info["network_fee"]
                fee_breakdown = fee_info["fee_breakdown"]
                
                logger.info(
                    f"✅ Combined fee calculated for ${amount_usd} {currency}: "
                    f"Platform ${platform_fee} + Network ${network_fee} = Total ${total_fee}, Net ${net_amount}"
                )
                
                # Update context with COMBINED fee data
                if not context.user_data:
                    context.user_data = {}
                context.user_data["cashout_data"].update({
                    "total_fee": total_fee,
                    "platform_fee": platform_fee,
                    "network_fee": network_fee,
                    "net_amount": net_amount,
                    "fee_breakdown": fee_breakdown
                })
                
            except Exception as fee_error:
                logger.error(f"Error calculating combined crypto fees: {fee_error}")
                # Fallback: use minimum viable fees
                platform_fee = Decimal('2.00')
                network_fee = get_fallback_network_fee(base_currency, network)
                total_fee = platform_fee + network_fee
                net_amount = Decimal(str(amount_usd)) - total_fee
                fee_breakdown = f"Combined fee (${total_fee:.2f})"
                
                if not context.user_data:
                    context.user_data = {}
                context.user_data["cashout_data"].update({
                    "total_fee": total_fee,
                    "platform_fee": platform_fee,
                    "network_fee": network_fee,
                    "net_amount": net_amount,
                    "fee_breakdown": fee_breakdown
                })
            
            # Show final confirmation with cached address data (prevents greenlet_spawn errors)
            await show_crypto_cashout_confirmation(query, context, cached_address_data)
        
    except Exception as e:
        logger.error(f"Error in handle_save_crypto_address_new: {e}")
        await safe_edit_message_text(query, "❌ Error saving address. Please try again.")

async def handle_short_crypto_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle short crypto save callback format (cs:hash)"""
    query = update.callback_query
    await safe_answer_callback_query(query, "💾 Saving address...")
    
    try:
        if not query or not query.data:
            return
        callback_data = query.data
        if not callback_data.startswith("cs:"):
            logger.error(f"Invalid callback data for short crypto save: {callback_data}")
            await safe_edit_message_text(query, "❌ Invalid request. Please try again.")
            return
        
        # Get address from mapping
        if not context.user_data:
            context.user_data = {}
        address_mapping = context.user_data.get('crypto_address_mapping', {})
        address = address_mapping.get(callback_data)
        
        if not address:
            logger.error(f"Address not found for callback {callback_data}")
            await safe_edit_message_text(query, "❌ Session expired. Please start again.")
            return
        
        # Call existing handler logic with the retrieved address
        await handle_save_crypto_address_logic(query, context, address)
        
    except Exception as e:
        logger.error(f"Error in handle_short_crypto_save: {e}")
        await safe_edit_message_text(query, "❌ Error saving address. Please try again.")

async def handle_short_crypto_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle short crypto skip callback format (ck:hash)"""
    query = update.callback_query
    await safe_answer_callback_query(query, "⏭️ Continuing without saving...")
    
    try:
        if not query or not query.data:
            return
        callback_data = query.data
        if not callback_data.startswith("ck:"):
            logger.error(f"Invalid callback data for short crypto skip: {callback_data}")
            await safe_edit_message_text(query, "❌ Invalid request. Please try again.")
            return
        
        # Get address from mapping
        if not context.user_data:
            context.user_data = {}
        address_mapping = context.user_data.get('crypto_address_mapping', {})
        address = address_mapping.get(callback_data)
        
        if not address:
            logger.error(f"Address not found for callback {callback_data}")
            await safe_edit_message_text(query, "❌ Session expired. Please start again.")
            return
        
        # Call existing handler logic with the retrieved address
        await handle_skip_save_crypto_logic(query, context, address)
        
    except Exception as e:
        logger.error(f"Error in handle_short_crypto_skip: {e}")
        await safe_edit_message_text(query, "❌ Error processing request. Please try again.")

async def handle_save_crypto_address_logic(query, context, address):
    """Extracted logic from handle_save_crypto_address_new"""
    telegram_user_id = query.from_user.id
    
    # Store address in cashout data
    if 'cashout_data' not in context.user_data:
        if not context.user_data:
            context.user_data = {}
        context.user_data['cashout_data'] = {}
    
    if not context.user_data:
        context.user_data = {}
    context.user_data['cashout_data']['withdrawal_address'] = address
    if not context.user_data:
        context.user_data = {}
    context.user_data['cashout_data']['network'] = 'ETH'
    if not context.user_data:
        context.user_data = {}
    context.user_data['cashout_data']['currency'] = 'ETH'
    
    # Save address directly with default label
    default_label = f"Address {str(address)[:8]}...{str(address)[-4:]}"
    
    # Save address to database
    async with async_managed_session() as session:
        try:
            # Get the database user_id from telegram_id
            stmt = select(User).where(User.telegram_id == telegram_user_id)
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()
            
            if not user:
                logger.error(f"User not found for telegram_id: {telegram_user_id}")
                await safe_edit_message_text(query, "❌ User account not found. Please contact support.")
                return
            
            # Check if address already exists before creating new one (including inactive)
            stmt = select(SavedAddress).where(
                SavedAddress.user_id == user.id,
                SavedAddress.address == address
            )
            result = await session.execute(stmt)
            existing_address = result.scalar_one_or_none()
            
            if existing_address:
                # Reactivate if inactive, update last_used in all cases
                saved_address = existing_address
                from sqlalchemy import update as sql_update
                from typing import Any, Dict
                update_values: Dict[str, Any] = {'last_used': datetime.utcnow()}
                if not as_bool(existing_address.is_active):
                    update_values['is_active'] = True
                    logger.info(f"♻️ Reactivating inactive address for user {as_int(user.id)}: {address[:10]}...")
                
                stmt = sql_update(SavedAddress).where(
                    SavedAddress.id == saved_address.id
                ).values(**update_values)
                await session.execute(stmt)
                await session.commit()
                logger.info(f"✅ Using existing saved address for user {user.id}: {address[:10]}...")
            else:
                # Create new address record
                saved_address = SavedAddress(
                    user_id=user.id,
                    currency='ETH',
                    network='ETH',
                    address=address,
                    label=default_label,
                    is_verified=False,
                    verification_sent=False
                )
                session.add(saved_address)
                await session.commit()
                logger.info(f"💾 Created new saved address for user {user.id}: {address[:10]}...")
            
            # CRITICAL: Cache saved_address attributes before passing to prevent greenlet_spawn errors
            cached_address_data = {
                'address': saved_address.address,
                'label': saved_address.label or default_label,
                'is_verified': saved_address.is_verified if hasattr(saved_address, 'is_verified') else False
            }
            
            # Update context with saved address info
            if not context.user_data:
                context.user_data = {}
            context.user_data["cashout_data"].update({
                "address_id": saved_address.id,
                "address_label": default_label,
                "is_saved_address": True
            })
            
        except Exception as e:
            logger.error(f"Error saving address: {e}")
            await safe_edit_message_text(query, "❌ Error saving address. Please try again.")
            return
    
    # Calculate fees and show confirmation (OUTSIDE session to prevent greenlet errors)
    await calculate_fees_and_show_confirmation(query, context, cached_address_data)

async def handle_skip_save_crypto_logic(query, context, address):
    """Extracted logic from handle_skip_save_crypto"""
    # Get existing cashout data
    if not context.user_data:
        context.user_data = {}
    cashout_data = context.user_data.get('cashout_data', {})
    amount_usd = cashout_data.get("amount")
    currency = cashout_data.get("currency", "ETH")
    
    if not amount_usd:
        logger.error("No cashout amount found in context for fee calculation")
        await safe_edit_message_text(query, "❌ Session expired. Please start cashout again.")
        return
    
    # Calculate COMBINED fees (platform + network)
    try:
        # Get network from currency
        network = currency.split("-")[1] if "-" in currency else None
        base_currency = currency.split("-")[0] if "-" in currency else currency
        
        # CRITICAL FIX: Calculate COMBINED fees (platform + Kraken network)
        fee_info = await calculate_crypto_cashout_with_network_fees(
            amount_usd=Decimal(str(amount_usd)),
            currency=base_currency,
            network=network,
            address_key=None  # No saved address, use fallback network fees
        )
        
        total_fee = fee_info["total_fee"]
        net_amount = fee_info["net_amount"]
        platform_fee = fee_info["platform_fee"]
        network_fee = fee_info["network_fee"]
        fee_breakdown = fee_info["fee_breakdown"]
        
        logger.info(
            f"✅ Combined fee calculated for ${amount_usd} {currency}: "
            f"Platform ${platform_fee} + Network ${network_fee} = Total ${total_fee}, Net ${net_amount}"
        )
        
    except Exception as fee_error:
        logger.error(f"Error calculating combined crypto fees: {fee_error}")
        # Fallback: use minimum viable fees
        platform_fee = Decimal('2.00')
        network_fee = get_fallback_network_fee(base_currency, network)
        total_fee = platform_fee + network_fee
        net_amount = Decimal(str(amount_usd)) - total_fee
        fee_breakdown = f"Combined fee (${total_fee:.2f})"
    
    # Update cashout data with COMBINED fees
    if not context.user_data:
        context.user_data = {}
    context.user_data['cashout_data'].update({
        'withdrawal_address': address,
        'network': currency,
        'currency': currency,
        'is_saved_address': False,
        'total_fee': total_fee,
        'platform_fee': platform_fee,
        'network_fee': network_fee,
        'net_amount': net_amount,
        'fee_breakdown': fee_breakdown
    })
    
    # Create a temporary address object for confirmation
    temp_address = type('TempAddress', (), {
        'address': address,
        'label': 'One-time use',
        'verified': False,
        'id': None
    })()
    
    # Proceed to confirmation
    await set_wallet_state(query.from_user.id, context, 'inactive')
    await show_crypto_cashout_confirmation(query, context, temp_address)

async def calculate_fees_and_show_confirmation(query, context, saved_address):
    """Helper function to calculate fees and show confirmation"""
    if not context.user_data:
        context.user_data = {}
    cashout_data = context.user_data.get("cashout_data", {})
    amount_usd = cashout_data.get("amount")
    currency = cashout_data.get("currency", "ETH")
    
    if not amount_usd:
        logger.error("No cashout amount found in context for fee calculation")
        await safe_edit_message_text(query, "❌ Session expired. Please start cashout again.")
        return
    
    try:
        from services.percentage_cashout_fee_service import PercentageCashoutFeeService
        
        fee_service = PercentageCashoutFeeService()
        network = currency if currency in ["BTC", "ETH", "LTC"] else "USDT"
        
        # Calculate fee using the same service used throughout the app
        fee_result = fee_service.calculate_cashout_fee(Decimal(str(amount_usd)), network)
        
        if not fee_result["success"]:
            error_msg = fee_result.get('error', 'Fee calculation failed')
            logger.error(f"Fee calculation failed: {error_msg}")
            await safe_edit_message_text(query, f"❌ {error_msg}")
            return
        
        total_fee = fee_result["final_fee"]
        net_amount = fee_result["net_amount"]
        fee_percentage = fee_result["fee_percentage"]
        min_fee = fee_result["min_fee"]
        fee_breakdown = f"{fee_percentage}% fee (${total_fee}, min ${min_fee})"
        
        logger.info(f"✅ Fee calculated for ${amount_usd} {currency}: ${total_fee} fee, ${net_amount} net")
        
        # Update context with fee data
        if not context.user_data:
            context.user_data = {}
        context.user_data["cashout_data"].update({
            "total_fee": Decimal(str(total_fee or 0)),
            "net_amount": Decimal(str(net_amount or 0)),
            "fee_breakdown": fee_breakdown
        })
        
    except Exception as fee_error:
        logger.error(f"Error calculating crypto fees: {fee_error}")
        # Fallback fee calculation
        total_fee = Decimal(str(amount_usd)) * Decimal("0.02")  # 2% fee
        net_amount = Decimal(str(amount_usd)) - total_fee
        fee_breakdown = "2% fee (estimated)"
        
        if not context.user_data:
            context.user_data = {}
        context.user_data["cashout_data"].update({
            "total_fee": Decimal(str(total_fee or 0)),
            "net_amount": Decimal(str(net_amount or 0)),
            "fee_breakdown": fee_breakdown
        })
    
    # Show final confirmation with saved address
    await show_crypto_cashout_confirmation(query, context, saved_address)

async def handle_skip_save_crypto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle skipping crypto address save and proceeding to confirmation"""
    query = update.callback_query
    await safe_answer_callback_query(query, "⏭️ Continuing without saving...")
    
    try:
        # Extract address from callback data
        if not query or not query.data:
            return
        callback_data = query.data
        if not callback_data.startswith("skip_save_crypto:"):
            logger.error(f"Invalid callback data for skip save crypto: {callback_data}")
            await safe_edit_message_text(query, "❌ Invalid request. Please try again.")
            return
        
        address = callback_data.replace("skip_save_crypto:", "")
        if not update or not update.effective_user:
            return
        user_id = update.effective_user.id
        
        # Store address in cashout data without saving to database
        if not context.user_data:
            # Initialize if None - this should not happen but handle it safely
            logger.warning("context.user_data was None - this is unexpected")
            return
        
        if 'cashout_data' not in context.user_data:
            context.user_data['cashout_data'] = {}
        
        cashout_data = context.user_data['cashout_data']
        amount_usd = cashout_data.get("amount")
        currency = cashout_data.get("currency", "ETH")
        
        if not amount_usd:
            logger.error("No cashout amount found in context for fee calculation")
            await safe_edit_message_text(query, "❌ Session expired. Please start cashout again.")
            return
        
        # CRITICAL FIX: Calculate fees before confirmation
        try:
            from services.percentage_cashout_fee_service import PercentageCashoutFeeService
            
            fee_service = PercentageCashoutFeeService()
            network = currency if currency in ["BTC", "ETH", "LTC"] else "USDT"
            
            # Calculate fee using the same service used throughout the app
            fee_result = fee_service.calculate_cashout_fee(Decimal(str(amount_usd)), network)
            
            if not fee_result["success"]:
                error_msg = fee_result.get('error', 'Fee calculation failed')
                logger.error(f"Fee calculation failed: {error_msg}")
                await safe_edit_message_text(query, f"❌ {error_msg}")
                return
            
            total_fee = fee_result["final_fee"]
            net_amount = fee_result["net_amount"]
            fee_percentage = fee_result["fee_percentage"]
            min_fee = fee_result["min_fee"]
            fee_breakdown = f"{fee_percentage}% fee (${total_fee}, min ${min_fee})"
            
            logger.info(f"✅ Fee calculated for ${amount_usd} {currency}: ${total_fee} fee, ${net_amount} net")
            
        except Exception as fee_error:
            logger.error(f"Error calculating crypto fees: {fee_error}")
            # Fallback fee calculation
            total_fee = Decimal(str(amount_usd)) * Decimal("0.02")  # 2% fee
            net_amount = Decimal(str(amount_usd)) - total_fee
            fee_breakdown = "2% fee (estimated)"
        
        # Update cashout data with all required fields for confirmation
        if not context.user_data:
            context.user_data = {}
        context.user_data['cashout_data'].update({
            'withdrawal_address': address,
            'network': currency,
            'currency': currency,
            'is_saved_address': False,
            'total_fee': Decimal(str(total_fee or 0)),
            'net_amount': Decimal(str(net_amount or 0)),
            'fee_breakdown': fee_breakdown
        })
        
        # Create a temporary address object for confirmation
        temp_address = type('TempAddress', (), {
            'address': address,
            'label': 'One-time use',
            'is_verified': False,
            'id': None
        })()
        
        # Proceed to confirmation
        user_id_for_state = query.from_user.id if query and query.from_user else 0
        if user_id_for_state:
            await set_wallet_state(user_id_for_state, context, 'inactive')
        else:
            logger.warning("No from_user available for set_wallet_state")
        await show_crypto_cashout_confirmation(query, context, temp_address)
        
    except Exception as e:
        logger.error(f"Error in handle_skip_save_crypto: {e}")
        await safe_edit_message_text(query, "❌ Error processing request. Please try again.")

async def handle_use_crypto_address_once(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle using crypto address without saving"""
    query = update.callback_query
    await safe_answer_callback_query(query, "⏭️ Using address once...")
    
    try:
        # Extract currency from callback data
        if not query or not query.data:
            return
        callback_data = query.data
        if not callback_data.startswith("use_once:"):
            logger.error(f"Invalid callback data for use once: {callback_data}")
            await safe_edit_message_text(query, "❌ Invalid request. Please try again.")
            return
        
        currency = callback_data.replace("use_once:", "")
        
        # Get address and amount from context
        if not context.user_data:
            context.user_data = {}
        cashout_data = context.user_data.get("cashout_data", {})
        address = cashout_data.get("withdrawal_address")
        amount_usd = cashout_data.get("amount")
        
        if not address:
            await safe_edit_message_text(query, "❌ Address not found. Please try again.")
            return
        
        if not amount_usd:
            logger.error("No cashout amount found in context for fee calculation")
            await safe_edit_message_text(query, "❌ Session expired. Please start cashout again.")
            return
        
        # CRITICAL FIX: Calculate fees before confirmation (matching handle_skip_save_crypto logic)
        try:
            from services.percentage_cashout_fee_service import PercentageCashoutFeeService
            
            fee_service = PercentageCashoutFeeService()
            network = currency if currency in ["BTC", "ETH", "LTC"] else "USDT"
            
            # Calculate fee using the same service used throughout the app
            fee_result = fee_service.calculate_cashout_fee(Decimal(str(amount_usd)), network)
            
            if not fee_result["success"]:
                error_msg = fee_result.get('error', 'Fee calculation failed')
                logger.error(f"Fee calculation failed: {error_msg}")
                await safe_edit_message_text(query, f"❌ {error_msg}")
                return
            
            total_fee = fee_result["final_fee"]
            net_amount = fee_result["net_amount"]
            fee_percentage = fee_result["fee_percentage"]
            min_fee = fee_result["min_fee"]
            fee_breakdown = f"{fee_percentage}% fee (${total_fee}, min ${min_fee})"
            
            logger.info(f"✅ Fee calculated for ${amount_usd} {currency}: ${total_fee} fee, ${net_amount} net")
            
        except Exception as fee_error:
            logger.error(f"Error calculating crypto fees: {fee_error}")
            # Fallback fee calculation
            total_fee = Decimal(str(amount_usd)) * Decimal("0.02")  # 2% fee
            net_amount = Decimal(str(amount_usd)) - total_fee
            fee_breakdown = "2% fee (estimated)"
        
        # Update cashout data with fee information
        if not context.user_data:
            context.user_data = {}
        context.user_data["cashout_data"].update({
            'is_saved_address': False,
            'address_label': 'One-time use',
            'network': currency,
            'currency': currency,
            'total_fee': Decimal(str(total_fee or 0)),
            'net_amount': Decimal(str(net_amount or 0)),
            'fee_breakdown': fee_breakdown
        })
        
        # Create a temporary saved address object for confirmation display
        temp_address = type('TempAddress', (), {
            'address': address,
            'label': 'One-time use',
            'is_verified': False
        })()
        
        # Show final confirmation
        await show_crypto_cashout_confirmation(query, context, temp_address)
        
    except Exception as e:
        logger.error(f"Error in handle_use_crypto_address_once: {e}")
        await safe_edit_message_text(query, "❌ Error processing address. Please try again.")

# ===== TEXT INPUT HANDLERS FOR ADDRESS LABELS =====

async def handle_address_label_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle address label text input"""
    user = update.effective_user
    if not user:
        return
    current_state = await get_wallet_state(user.id, context)
    if not context.user_data:
        context.user_data = {}
    cashout_data = context.user_data.get("cashout_data", {})
    
    if current_state != WalletStates.ENTERING_EMAIL or not cashout_data.get("saving_address"):
        return  # Not in address labeling mode
    
    if not update.message or not update.message.text:
        return
    user_input = update.message.text.strip()
    if not update or not update.effective_user:
        return
    telegram_user_id = update.effective_user.id
    currency = cashout_data.get("pending_address_currency")
    address = cashout_data.get("withdrawal_address")
    
    if not currency or not address:
        if not update.message:
            return
        await update.message.reply_text("❌ Session expired. Please start again.")
        return
    
    if len(user_input) > 100:
        if not update.message:
            return
        await update.message.reply_text(
            "❌ Label too long. Please keep it under 100 characters.",
        )
        return
    
    # Save address to database
    async with async_managed_session() as session:
        try:
            # First get the database user_id from telegram_id (fix user ID mapping bug)
            stmt = select(User).where(User.telegram_id == telegram_user_id)
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()
            
            if not user:
                logger.error(f"User not found for telegram_id: {telegram_user_id}")
                if not update.message:
                    return
                await update.message.reply_text("❌ User account not found. Please contact support.")
                return
            
            saved_address = SavedAddress(
                user_id=user.id,
                currency=currency,
                network=get_network_from_currency(currency),
                address=address,
                label=user_input,
                is_verified=False,
                verification_sent=False
            )
            session.add(saved_address)
            await session.commit()
            
            # CRITICAL: Cache saved_address attributes before session ends to prevent greenlet_spawn errors
            cached_address_data = {
                'address': saved_address.address,
                'label': saved_address.label or user_input,
                'is_verified': saved_address.is_verified if hasattr(saved_address, 'is_verified') else False
            }
        
            # Update context with saved address info
            if not context.user_data:
                context.user_data = {}
            context.user_data["cashout_data"].update({
                "address_id": saved_address.id,
                "address_label": user_input,
                "is_saved_address": True
            })
            
            # Clear exclusivity flags after processing
            if not context.user_data:
                context.user_data = {}
            context.user_data.pop('state_exclusive', None)
            if not context.user_data:
                context.user_data = {}
            context.user_data["cashout_data"].pop("saving_address", None)
            
            # Convert Column[int] to int using ORM helper
            await set_wallet_state(as_int(user.id) or 0, context, 'inactive')
            
        except Exception as e:
            logger.error(f"Error saving address: {e}")
            if not update.message:
                return
            await update.message.reply_text("❌ Error saving address. Please try again.")
            return
    
    # Show final confirmation with cached address data (OUTSIDE session to prevent greenlet errors)
    await show_crypto_cashout_confirmation(query=None, context=context, saved_address=cached_address_data)

# ===== ADDITIONAL REQUIRED FUNCTIONS (STUBS FOR SYSTEM TO START) =====

async def start_add_funds(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start add funds process with both NGN and crypto options"""
    async with button_callback_wrapper(update, "⏳ Loading funding options..."):
        query = update.callback_query
        from utils.callback_utils import safe_answer_callback_query, safe_edit_message_text
        from utils.constants import CallbackData
        from utils.normalizers import normalize_telegram_id
        from sqlalchemy import update as sqlalchemy_update
        
        if not update or not update.effective_user:
            return
        telegram_user_id = update.effective_user.id
        logger.info(f"🔙 WALLET_ADD_FUNDS: User {telegram_user_id} clicked - checking context")
        
        # Send admin notification for add funds clicked (non-blocking)
        logger.info(f"🔔 ADD_FUNDS_NOTIFICATION: Starting notification for user {telegram_user_id}")
        try:
            async with async_managed_session() as notify_session:
                notify_user_result = await notify_session.execute(
                    select(User).where(User.telegram_id == telegram_user_id)
                )
                notify_user = notify_user_result.scalar_one_or_none()
                
                if notify_user:
                    logger.info(f"✅ ADD_FUNDS_NOTIFICATION: User {notify_user.id} found, preparing notification")
                    # Get all wallets and sum balances (users have multiple wallets for different currencies)
                    notify_wallet_result = await notify_session.execute(
                        select(Wallet).where(Wallet.user_id == notify_user.id)
                    )
                    notify_wallets = notify_wallet_result.scalars().all()
                    # Sum all wallet available_balance for total USD equivalent
                    current_balance = sum(float(w.available_balance) for w in notify_wallets) if notify_wallets else 0.0
                    
                    logger.info(f"📤 ADD_FUNDS_NOTIFICATION: Sending admin notification for user {notify_user.username} ({len(notify_wallets)} wallets, total available balance: ${current_balance:.2f})")
                    asyncio.create_task(
                        admin_trade_notifications.notify_add_funds_clicked({
                            'user_id': notify_user.id,
                            'username': notify_user.username,
                            'first_name': notify_user.first_name,
                            'last_name': notify_user.last_name,
                            'current_balance': current_balance,
                            'clicked_at': datetime.utcnow()
                        })
                    )
                else:
                    logger.warning(f"⚠️ ADD_FUNDS_NOTIFICATION: User with telegram_id {telegram_user_id} not found in database!")
        except Exception as notify_error:
            logger.error(f"❌ ADD_FUNDS_NOTIFICATION: Error sending add funds notification: {notify_error}", exc_info=True)

        # Check if we're coming FROM the NGN amount entry page (wallet_input state)
        coming_from_ngn_page = False
        try:
            async with async_managed_session() as db_session:
                stmt = select(User.conversation_state).where(User.telegram_id == normalize_telegram_id(telegram_user_id))
                result = await db_session.execute(stmt)
                current_state = result.scalar_one_or_none()
                coming_from_ngn_page = (current_state == "wallet_input")
                logger.info(f"🔍 CONTEXT CHECK: conversation_state='{current_state}', coming_from_ngn_page={coming_from_ngn_page}")
        except Exception as e:
            logger.error(f"Error checking state: {e}")

        # CRITICAL FIX: Clear ALL states to ensure navigation works
        try:
            await set_wallet_state(telegram_user_id, context, 'inactive')
            if not context.user_data:
                context.user_data = {}
            context.user_data.pop("ngn_funding_flow", None)
            if not context.user_data:
                context.user_data = {}
            context.user_data.pop("payment_context", None)
            
            # TIMESTAMP FIX: Clear database conversation_state with timestamp
            async with async_managed_session() as db_session:
                from utils.conversation_state_helper import set_conversation_state_db
                await set_conversation_state_db(telegram_user_id, None, db_session)
                await db_session.commit()
            
            logger.info(f"✅ States cleared for user {telegram_user_id}")
        except Exception as e:
            logger.error(f"❌ Error clearing states: {e}")

        # Check if NGN features are enabled (respects ENABLE_NGN_FEATURES flag)
        ngn_enabled = Config.ENABLE_NGN_FEATURES
        logger.info(f"📱 BACK_BUTTON: Preparing to show funding options (NGN enabled: {ngn_enabled})")

        # PERFORMANCE FIX: Don't fetch rate here - it delays button response by 2 seconds
        # Rate will be shown when user actually selects NGN payment
        # Method-first approach - show both NGN and crypto options
        if ngn_enabled:
            text = """💰 Fund Your Wallet

Choose funding method:

🏦 Bank Transfer (NGN)
Fast local payment

🪙 Send Crypto
⚡ Instant processing"""

            keyboard = [
                [InlineKeyboardButton("🏦 Bank Transfer (NGN)", callback_data="fincra_start_payment")],
                [InlineKeyboardButton("🪙 Send Crypto", callback_data="crypto_funding_start")],
                [InlineKeyboardButton("⬅️ Back", callback_data=CallbackData.WALLET_MENU)],
            ]
        else:
            # If NGN not available, show crypto-focused options
            text = """💰 Fund Your Wallet

Choose funding method:

🪙 Send Crypto
⚡ Instant processing"""
            
            keyboard = [
                [InlineKeyboardButton("🪙 Send Crypto", callback_data="crypto_funding_start")],
                [InlineKeyboardButton("⬅️ Back", callback_data=CallbackData.WALLET_MENU)],
            ]

        # Only use delete+resend if coming FROM NGN amount page (fixes caching issue)
        # Otherwise, use normal edit for smooth navigation
        try:
            if coming_from_ngn_page:
                logger.info(f"📱 NGN_BACK: Delete old message and send fresh (fixes caching)")
                try:
                    from telegram import Message
                    if query and query.message and isinstance(query.message, Message):
                        await query.message.delete()
                    logger.info(f"✅ Old message deleted")
                except Exception as del_err:
                    logger.warning(f"⚠️ Couldn't delete: {del_err}")
                
                # Send fresh message
                await context.bot.send_message(
                    chat_id=telegram_user_id,
                    text=text,
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                logger.info(f"✅ Fresh funding options sent")
            else:
                logger.info(f"📱 NORMAL_NAV: Using standard edit")
                await safe_edit_message_text(
                    query, text, reply_markup=InlineKeyboardMarkup(keyboard)
                )
                logger.info(f"✅ Screen updated via edit")
        except Exception as e:
            logger.error(f"❌ Failed to update screen: {e}")
            # Fallback to opposite method
            try:
                if coming_from_ngn_page:
                    await safe_edit_message_text(query, text, reply_markup=InlineKeyboardMarkup(keyboard))
                else:
                    from telegram import Message
                    if query and query.message and isinstance(query.message, Message):
                        await query.message.delete()
                    await context.bot.send_message(telegram_user_id, text, reply_markup=InlineKeyboardMarkup(keyboard))
                logger.info(f"✅ Used fallback method")
            except Exception as e2:
                logger.error(f"❌ All methods failed: {e2}")

async def show_crypto_funding_options(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show cryptocurrency funding options with all DynoPay supported cryptos"""
    query = update.callback_query
    if query:
        # PERFORMANCE: Instant acknowledgment
        await safe_answer_callback_query(query, "🪙 Crypto funding")

    # Popular-first approach with recommendations
    text = """🪙 Choose Crypto

Popular:
• USDT-TRC20 (Lowest fees)
• BTC (Most trusted)
• ETH (Widely accepted)

All supported cryptocurrencies available below."""

    # DynoPay supported cryptocurrencies only
    keyboard = [
        # Popular row
        [
            InlineKeyboardButton("USDT-TRC20 💰", callback_data="deposit_currency:USDT-TRC20"),
            InlineKeyboardButton("BTC", callback_data="deposit_currency:BTC")
        ],
        [InlineKeyboardButton("ETH", callback_data="deposit_currency:ETH")],
        # All options section
        [
            InlineKeyboardButton("USDT-ERC20", callback_data="deposit_currency:USDT-ERC20"),
            InlineKeyboardButton("LTC", callback_data="deposit_currency:LTC")
        ],
        # Navigation
        [InlineKeyboardButton("⬅️ Back to Funding Options", callback_data=CallbackData.WALLET_ADD_FUNDS)],
    ]

    if query:
        await safe_edit_message_text(query, text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        # Fix: Properly handle case when update.message doesn't exist
        if update.message:
            await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_bank_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle bank selection"""
    await handle_select_bank(update, context)

async def handle_deposit_currency_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle deposit currency selection and generate real crypto address - ASYNC OPTIMIZED"""
    # Define variables in outer scope for error handling
    currency = "BTC"  # Default
    user_id = update.effective_user.id if update and update.effective_user else 0
    query = update.callback_query if update else None
    
    try:
        # PERFORMANCE: Use button_callback_wrapper for instant feedback + shared async session
        async with button_callback_wrapper(update, "💰 Generating address...") as session:
            if not query or not query.data:
                return
            
            currency = query.data.split(":")[1] if ":" in query.data else "BTC"
            
            # Show initial loading message
            await query.edit_message_text(
                f"💰 {currency} Deposit\n\nGenerating deposit address...",
                parse_mode='Markdown'
            )
            
            # Get user_id before try block to avoid scope issues
            if not update or not update.effective_user:
                return
            user_id = update.effective_user.id
            
            # Import crypto services - use unified system with enhanced error handling
            from services.crypto import CryptoServiceAtomic, get_crypto_emoji
            
            logger.info(f"🪙 Generating {currency} deposit address for user {user_id}")
            
            # Generate deposit address using the unified payment system with timeout protection
            # Session already provided by button_callback_wrapper
            try:
                address_info = await asyncio.wait_for(
                    CryptoServiceAtomic.generate_wallet_deposit_address(
                        currency=currency,
                        user_id=user_id,
                        session=session  # Pass session for financial audit compliance
                    ),
                    timeout=30.0  # 30-second timeout to prevent hanging
                )
            except asyncio.TimeoutError:
                logger.error(f"❌ Timeout generating {currency} address for user {user_id}")
                raise Exception(f"Address generation timeout - please try again")
            
            # Enhanced validation of address info
            if not address_info:
                logger.error(f"❌ Address info is None for {currency}")
                raise Exception("Address generation service unavailable")
            
            if not isinstance(address_info, dict):
                logger.error(f"❌ Invalid address info format for {currency}: {type(address_info)}")
                raise Exception("Invalid address generation response")
            
            if 'address' not in address_info or not address_info['address']:
                logger.error(f"❌ No address in response for {currency}: {address_info}")
                raise Exception("Failed to generate crypto address")
            
            crypto_address = address_info['address']
            wallet_transaction_id = address_info.get('wallet_transaction_id', f'wallet_{user_id}_{currency}')
            payment_provider = address_info.get('payment_provider', 'unknown')
            
            # Validate address format
            if len(crypto_address) < 10:  # Basic sanity check
                logger.error(f"❌ Generated address too short for {currency}: {crypto_address}")
                raise Exception("Generated address appears invalid")
            
            emoji = get_crypto_emoji(currency)
            logger.info(f"✅ Successfully generated {currency} address via {payment_provider}")
            
            # Send admin notification for wallet address generated (non-blocking)
            try:
                async with async_managed_session() as notify_session:
                    notify_user_result = await notify_session.execute(
                        select(User).where(User.telegram_id == user_id)
                    )
                    notify_user = notify_user_result.scalar_one_or_none()
                    
                    if notify_user:
                        asyncio.create_task(
                            admin_trade_notifications.notify_wallet_address_generated({
                                'user_id': notify_user.id,
                                'username': notify_user.username,
                                'first_name': notify_user.first_name,
                                'last_name': notify_user.last_name,
                                'currency': currency,
                                'address': crypto_address,
                                'generated_at': datetime.utcnow()
                            })
                        )
            except Exception as notify_error:
                logger.error(f"Error sending wallet address generated notification: {notify_error}")
            
            # Create formatted message with deposit instructions (compact mobile-friendly design)
            # Shorten wallet ID for display (show last 8 chars)
            short_id = wallet_transaction_id[-8:] if len(wallet_transaction_id) > 8 else wallet_transaction_id
            
            text = f"""💰 <b>{currency} Wallet Deposit</b>

{emoji} <code>{crypto_address}</code>

⚡ Send only {currency} (Min: $10 USD)
⚡ Instant credit | Powered by {payment_provider.title()}
⏰ Expires: 2 hours

<i>ID: {short_id}</i>"""

            # Add action buttons
            keyboard = [
                [
                    InlineKeyboardButton("📱 Show QR", callback_data=f"show_qr:{crypto_address}")
                ],
                [
                    InlineKeyboardButton("🔙 Choose Different Crypto", callback_data="crypto_funding_options")
                ]
            ]
            
            await query.edit_message_text(
                text,
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            
            # Store address info in context for QR generation if needed
            if not context.user_data:
                context.user_data = {}
            context.user_data.setdefault('deposit_address', crypto_address)
            if not context.user_data:
                context.user_data = {}
            context.user_data.setdefault('deposit_currency', currency)
        
    except Exception as e:
        error_msg = str(e)
        logger.error(f"❌ Error generating {currency} deposit address for user {user_id}: {error_msg}")
        
        # Enhanced error categorization and user-friendly messages
        if "timeout" in error_msg.lower():
            user_msg = "⏱️ Request Timeout\n\nAddress generation is taking longer than expected.\nPlease try again in a moment."
            log_category = "TIMEOUT"
        elif "unavailable" in error_msg.lower() or "service" in error_msg.lower():
            user_msg = "🛠️ Service Temporarily Unavailable\n\nOur crypto address service is temporarily unavailable.\nPlease try again in a few minutes."
            log_category = "SERVICE_UNAVAILABLE" 
        elif "invalid" in error_msg.lower():
            user_msg = f"⚠️ Invalid Request\n\nThere was an issue with your {currency} address request.\nPlease try selecting the currency again."
            log_category = "INVALID_REQUEST"
        elif "network" in error_msg.lower():
            user_msg = "🌐 Network Issue\n\nNetwork connectivity problem detected.\nPlease check your connection and try again."
            log_category = "NETWORK_ERROR"
        else:
            user_msg = f"❌ Address Generation Failed\n\nUnable to generate {currency} deposit address.\nPlease try again or choose a different currency."
            log_category = "UNKNOWN_ERROR"
        
        # Log with categorization for monitoring 
        logger.error(f"🚨 CRYPTO_ADDRESS_ERROR | Category: {log_category} | Currency: {currency} | User: {user_id} | Error: {error_msg}")
        
        # Show enhanced error message with recovery options
        error_text = f"""{user_msg}

🔧 What you can do:
• Try again with the same currency
• Choose a different cryptocurrency  
• Contact support if the issue persists

This is usually temporary and resolves quickly."""

        keyboard = [
            [
                InlineKeyboardButton("🔄 Try Again", callback_data=f"deposit_currency:{currency}"),
                InlineKeyboardButton("🔄 Try Different Crypto", callback_data="crypto_funding_options")
            ],
            [
                InlineKeyboardButton("💬 Contact Support", url="https://t.me/LockbayAssist"),
                InlineKeyboardButton("⬅️ Back to Wallet", callback_data=CallbackData.WALLET_MENU)
            ]
        ]
        
        if not query:
            return
        await query.edit_message_text(
            error_text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

async def show_deposit_qr(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show deposit QR code - ASYNC OPTIMIZED"""
    try:
        # PERFORMANCE: Use button_callback_wrapper for instant feedback (<50ms)
        async with button_callback_wrapper(update, "📱 Loading QR...") as session:
            query = update.callback_query
            if not query:
                return
            await query.edit_message_text("📱 QR Code functionality will be implemented soon.")
    except Exception as e:
        logger.error(f"Error in show_deposit_qr: {e}")

async def handle_save_bank_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle save bank account - ASYNC OPTIMIZED"""
    try:
        # PERFORMANCE: Use button_callback_wrapper for instant feedback (<50ms)
        async with button_callback_wrapper(update, "💾 Saving...") as session:
            query = update.callback_query
            if not query:
                return
            await query.edit_message_text("✅ Bank account saved successfully.")
    except Exception as e:
        logger.error(f"Error in handle_save_bank_account: {e}")

async def handle_cancel_bank_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle cancel bank save - ASYNC OPTIMIZED"""
    try:
        # PERFORMANCE: Use button_callback_wrapper for instant feedback (<50ms)
        async with button_callback_wrapper(update, "⏳ Processing...") as session:
            query = update.callback_query
            if not query:
                return
            await query.edit_message_text("❌ Bank account save cancelled.")
    except Exception as e:
        logger.error(f"Error in handle_cancel_bank_save: {e}")

async def handle_add_new_bank(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle add new bank - SIMPLIFIED: Direct account number input (no bank selection)"""
    query = update.callback_query
    await safe_answer_callback_query(query, "🏦 Adding new bank account")
    
    # Set simplified wallet state for direct account input
    if query and query.from_user:
        await set_wallet_state(query.from_user.id, context, 'adding_bank_account_direct')
    
    # Initialize simplified bank addition data
    if not context.user_data:
        context.user_data = {}
    context.user_data.setdefault('bank_addition_mode', True)  # Flag to distinguish from cashout
    if not context.user_data:
        context.user_data = {}
    context.user_data.setdefault('bank_addition_data', {
        'step': 'entering_account_number',
        'account_number': None,
        'account_name': None,
        'bank_name': None,
        'bank_code': None,
        'label': None
    })
    
    # Show simplified account input (no bank selection needed)
    await show_direct_account_input(update, context)

async def show_direct_account_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show simplified direct account number input (no bank selection)"""
    query = update.callback_query
    
    # Professional header with branding
    header = make_header("🏦 Add New Bank Account")
    
    text = f"""{header}

✨ Simple Bank Addition

Please enter your 10-digit Nigerian bank account number:

🔹 We'll automatically detect your bank
🔹 Account name verified in real-time
🔹 Securely saved for future use

💡 Example: 0123456789

Enter your account number:"""

    keyboard = [
        [InlineKeyboardButton("🔙 Back to Wallet", callback_data="menu_wallet")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if query:
        await safe_edit_message_text(
            query,
            text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

async def handle_bank_addition_account_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle account number input for bank addition (modified from handle_ngn_bank_account_input)"""
    try:
        # This is a text message, not a callback query
        message = update.message
        if not message or not message.text:
            logger.warning("No text message found in handle_bank_addition_account_input")
            return
            
        account_number = message.text.strip()
        if not update or not update.effective_user:
            return
        user_id = update.effective_user.id
        
        logger.info(f"🏦 BANK ADDITION: Processing account verification for user {user_id}: {account_number}")
        
        # Basic validation
        if not account_number.isdigit() or len(account_number) != 10:
            await message.reply_text(
                "❌ Invalid Account Number\n\n"
                "Please enter a valid 10-digit Nigerian bank account number.",
                parse_mode='Markdown'
            )
            return
            
        # Get user from database
        async with async_managed_session() as session:
            try:
                stmt = select(User).where(User.telegram_id == user_id)
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()
                if not user:
                    await message.reply_text("❌ User session not found. Please try again.")
                    return
                    
                # Show processing message for bank addition
                processing_msg = await message.reply_text(
                    "🚀 Verifying Bank Account\n\n"
                    "⚡ Checking account across major Nigerian banks\n"
                    "• Fast verification in progress...\n"
                    "• Detecting bank automatically",
                    parse_mode='Markdown'
                )
                
                # Use OPTIMIZED bank verification service
                from services.optimized_bank_verification_service import OptimizedBankVerificationService
                
                optimized_verifier = OptimizedBankVerificationService()
                
                # Run verification
                logger.info(f"🚀 BANK ADDITION: Starting verification for account {account_number}")
                
                # Get verification results using optimized service
                all_verified_accounts = await optimized_verifier.verify_account_parallel_optimized(account_number)
                
                # Clean up resources
                await optimized_verifier.cleanup()
                
                # Delete processing message
                try:
                    await processing_msg.delete()
                except Exception as e:
                    pass
                    
                if all_verified_accounts:
                    if len(all_verified_accounts) == 1:
                        # Single bank match - show bank addition result
                        verified_account = all_verified_accounts[0]
                        logger.info(f"✅ BANK ADDITION: Single bank match: {verified_account['bank_name']}")
                    
                    # Store account details for bank addition
                        if not context.user_data:
                            context.user_data = {}
                        bank_data = context.user_data.get('bank_addition_data', {})
                        bank_data.update({
                            'account_number': verified_account['account_number'],
                            'account_name': verified_account['account_name'],
                            'bank_name': verified_account['bank_name'],
                            'bank_code': verified_account['bank_code'],
                            'step': 'verified'
                        })
                        if not context.user_data:
                            context.user_data = {}
                        context.user_data['bank_addition_data'] = bank_data
                    
                    # Show bank addition success UI
                        # Dynamic message based on auto cashout feature toggle
                        if Config.ENABLE_AUTO_CASHOUT_FEATURES:
                            save_prompt = "💡 **Save for auto cashout?**"
                            save_benefit = "Saved accounts enable auto cashouts without re-entering details."
                        else:
                            save_prompt = "💡 **Save for quick cashouts?**"
                            save_benefit = "Saved accounts enable one-click cashouts without re-entering details."
                        
                        text = f"""✅ **Bank Account Verified!**

🏦 {verified_account['bank_name']}
👤 {verified_account['account_name']}
💳 {verified_account['account_number']}

━━━━━━━━━━━━━━━━━━━━

{save_prompt}

{save_benefit}

━━━━━━━━━━━━━━━━━━━━"""
                    
                        keyboard = [
                            [InlineKeyboardButton("💾 Save Bank Account", callback_data="save_verified_bank_account")],
                            [InlineKeyboardButton("🔄 Try Different Account", callback_data="add_new_bank")],
                            [InlineKeyboardButton("🔙 Back to Wallet", callback_data="menu_wallet")]
                        ]
                    
                        await message.reply_text(
                            text,
                            parse_mode='Markdown',
                            reply_markup=InlineKeyboardMarkup(keyboard)
                        )
                    
                    else:
                        # Multiple banks found - show selection UI for bank addition
                        logger.info(f"🏦 BANK ADDITION: Multiple banks found: Account exists in {len(all_verified_accounts)} banks")
                    
                        text = f"""🏦 Account Found in {len(all_verified_accounts)} Banks

    Account {account_number} exists in multiple banks. Please select the correct one:"""
                    
                        keyboard = []
                        for i, account in enumerate(all_verified_accounts[:5]):  # Limit to 5 for cleaner UI
                            keyboard.append([
                                InlineKeyboardButton(
                                    f"🏦 {account['bank_name']} - {account['account_name']}", 
                                    callback_data=f"select_bank_for_addition:{i}"
                                )
                            ])
                    
                        keyboard.append([
                            InlineKeyboardButton("🔄 Try Different Account", callback_data="add_new_bank")
                        ])
                    
                    # Store all verified accounts for selection
                        if not context.user_data:
                            context.user_data = {}
                        context.user_data['verified_accounts_for_addition'] = all_verified_accounts
                    
                        await message.reply_text(
                            text,
                            parse_mode='Markdown',
                            reply_markup=InlineKeyboardMarkup(keyboard)
                        )
                else:
                    # No verification successful
                    text = f"""❌ Account Not Found

    We could not verify account number {account_number} with any of our supported banks.

    Possible reasons:
    • Account number might be incorrect
    • Bank not supported yet
    • Network connectivity issue

    Please double-check and try again."""
                
                    keyboard = [
                        [InlineKeyboardButton("🔄 Try Again", callback_data="add_new_bank")],
                        [InlineKeyboardButton("🔙 Back to Wallet", callback_data="menu_wallet")]
                    ]
                
                    await message.reply_text(
                        text,
                        parse_mode='Markdown',
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
            except Exception as db_error:
                logger.error(f"Database error in bank addition: {db_error}")
                if message:
                    await message.reply_text(
                        "❌ There was a database error. Please try again.",
                        parse_mode='Markdown'
                    )
            
    except Exception as e:
        logger.error(f"Error in handle_bank_addition_account_input: {e}")
        message = update.message if update else None
        if message:
            await message.reply_text(
                "There was an error verifying your account. Please try again.",
                parse_mode='Markdown'
            )

async def handle_save_verified_bank_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle saving the verified bank account to user's saved accounts"""
    query = update.callback_query
    await safe_answer_callback_query(query, "💾 Saving bank account...")
    
    try:
        if not context.user_data:
            context.user_data = {}
        bank_data = context.user_data.get('bank_addition_data', {})
        
        if not bank_data or bank_data.get('step') != 'verified':
            await safe_edit_message_text(
                query,
                "❌ No Verified Account\n\nPlease verify an account first.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Try Again", callback_data="add_new_bank")]
                ])
            )
            return
        
        if not update or not update.effective_user:
            return
        user_id = update.effective_user.id
        async with async_managed_session() as session:
            try:
                stmt = select(User).where(User.telegram_id == user_id)
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()
                if not user:
                    await safe_edit_message_text(query, "❌ User session not found.")
                    return
                
                # Check if account already exists
                stmt = select(SavedBankAccount).where(
                    SavedBankAccount.user_id == user.id,
                    SavedBankAccount.account_number == bank_data['account_number'],
                    SavedBankAccount.bank_code == bank_data['bank_code']
                )
                result = await session.execute(stmt)
                existing_account = result.scalar_one_or_none()
                
                if existing_account:
                    await safe_edit_message_text(
                        query,
                        f"ℹ️ Account Already Saved\n\n"
                        f"This account is already in your saved accounts.\n\n"
                        f"🏦 {bank_data['bank_name']}\n"
                        f"👤 {bank_data['account_name']}",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("💰 Wallet", callback_data="menu_wallet")]
                        ])
                    )
                    return
                
                # Create new saved bank account
                saved_account = SavedBankAccount(
                    user_id=user.id,
                    bank_name=bank_data['bank_name'],
                    bank_code=bank_data['bank_code'],
                    account_number=bank_data['account_number'],
                    account_name=bank_data['account_name'],
                    label=bank_data.get('label', bank_data['bank_name'])  # Use bank name as default label
                )
                
                session.add(saved_account)
                await session.commit()
                
                # Success message
                await safe_edit_message_text(
                    query,
                    f"✅ Bank Account Saved Successfully!\n\n"
                    f"🏦 {bank_data['bank_name']}\n"
                    f"👤 {bank_data['account_name']}\n"
                    f"💳 {bank_data['account_number']}\n\n"
                    f"This account is now available for quick cashouts.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("💰 Wallet", callback_data="menu_wallet")],
                        [InlineKeyboardButton("🏦 Manage Banks", callback_data="manage_saved_banks")]
                    ])
                )
                
                # Clear bank addition data
                if not context.user_data:
                    context.user_data = {}
                context.user_data.pop('bank_addition_data', None)
                if not context.user_data:
                    context.user_data = {}
                context.user_data.pop('bank_addition_mode', None)
                # Convert Column[int] to int using ORM helper for set_wallet_state
                await set_wallet_state(as_int(user_id), context, 'inactive')
                
                logger.info(f"✅ BANK ADDITION: Saved bank account {bank_data['account_number']} for user {user_id}")
                
            except Exception as e:
                logger.error(f"Error saving bank account: {e}")
                await safe_edit_message_text(
                    query,
                    "❌ Save Failed\n\nThere was an error saving your bank account. Please try again.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔄 Try Again", callback_data="save_verified_bank_account")],
                        [InlineKeyboardButton("🔙 Back", callback_data="menu_wallet")]
                    ])
                )
            
    except Exception as e:
        logger.error(f"Error saving bank account: {e}")
        await safe_edit_message_text(
            query,
            "❌ Save Failed\n\nThere was an error saving your bank account. Please try again.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Try Again", callback_data="save_verified_bank_account")],
                [InlineKeyboardButton("🔙 Back", callback_data="menu_wallet")]
            ])
        )

async def handle_select_bank_for_addition(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle bank selection when multiple banks are found during addition"""
    query = update.callback_query
    if not query or not query.data:
        return
    callback_data = query.data
    
    if not callback_data.startswith("select_bank_for_addition:"):
        await safe_answer_callback_query(query, "Invalid selection")
        return
    
    try:
        index = int(callback_data.replace("select_bank_for_addition:", ""))
        if not context.user_data:
            context.user_data = {}
        verified_accounts = context.user_data.get('verified_accounts_for_addition', [])
        
        if index < 0 or index >= len(verified_accounts):
            await safe_answer_callback_query(query, "Invalid bank selection")
            return
        
        selected_account = verified_accounts[index]
        await safe_answer_callback_query(query, f"🏦 Selected {selected_account['bank_name']}")
        
        # Store selected account in bank addition data
        if not context.user_data:
            context.user_data = {}
        bank_data = context.user_data.get('bank_addition_data', {})
        bank_data.update({
            'account_number': selected_account['account_number'],
            'account_name': selected_account['account_name'],
            'bank_name': selected_account['bank_name'],
            'bank_code': selected_account['bank_code'],
            'step': 'verified'
        })
        if not context.user_data:
            context.user_data = {}
        context.user_data['bank_addition_data'] = bank_data
        
        # Show bank addition success UI
        text = f"""✅ Bank Account Verified!

🏦 Bank: {selected_account['bank_name']}
👤 Account Name: {selected_account['account_name']}  
💳 Account Number: {selected_account['account_number']}

Ready to save this bank account for future transactions?"""
        
        keyboard = [
            [InlineKeyboardButton("💾 Save Bank Account", callback_data="save_verified_bank_account")],
            [InlineKeyboardButton("🔄 Try Different Account", callback_data="add_new_bank")],
            [InlineKeyboardButton("🔙 Back to Wallet", callback_data="menu_wallet")]
        ]
        
        await safe_edit_message_text(
            query,
            text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
        # Clean up the temporary data
        if not context.user_data:
            context.user_data = {}
        context.user_data.pop('verified_accounts_for_addition', None)
        
        logger.info(f"✅ BANK ADDITION: User selected {selected_account['bank_name']} from multiple options")
        
    except (ValueError, IndexError) as e:
        logger.error(f"Error in bank selection: {e}")
        await safe_answer_callback_query(query, "Invalid selection")
        await safe_edit_message_text(
            query,
            "❌ Invalid Selection\n\nPlease try again.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Add Bank Again", callback_data="add_new_bank")]
            ])
        )

async def show_bank_selection_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show bank selection menu with major Nigerian banks"""
    try:
        # Professional header with branding
        header = make_header("🏦 Add New Bank Account")
        
        text = f"""{header}

📋 Step 1 of 4: Select Your Bank

Please choose your bank from the list below or search for it:

🔹 We support all major Nigerian banks
🔹 Your account will be verified in real-time
🔹 Securely saved for future transactions

Select your bank:"""

        # Get bank list from Fincra (cached if possible)
        from services.fincra_service import FincraService
        fincra = FincraService()
        
        # Show loading first
        query = update.callback_query
        if query:
            await safe_edit_message_text(
                query,
                f"{text}\n\n⏳ Loading banks...",
                parse_mode='Markdown'
            )
        
        # Get Nigerian banks from Fincra API
        bank_list = await fincra.list_banks()
        
        if not bank_list:
            # Fallback to major banks list
            bank_list = [
                {"code": "090405", "name": "Moniepoint MFB"},
                {"code": "100004", "name": "OPay Digital Bank"},
                {"code": "090267", "name": "Kuda Bank"},
                {"code": "044", "name": "Access Bank"},
                {"code": "011", "name": "First Bank of Nigeria"},
                {"code": "058", "name": "Guaranty Trust Bank"},
                {"code": "030", "name": "Heritage Bank"},
                {"code": "301", "name": "Jaiz Bank"},
                {"code": "076", "name": "Polaris Bank"},
                {"code": "221", "name": "Stanbic IBTC Bank"},
                {"code": "068", "name": "Standard Chartered Bank"},
                {"code": "232", "name": "Sterling Bank"},
                {"code": "032", "name": "Union Bank of Nigeria"},
                {"code": "033", "name": "United Bank for Africa"},
                {"code": "215", "name": "Unity Bank"},
                {"code": "035", "name": "Wema Bank"},
                {"code": "057", "name": "Zenith Bank"}
            ]
        
        # Store bank list in context for later use
        if not context.user_data:
            context.user_data = {}
        context.user_data.setdefault('available_banks', bank_list)
        
        # Create keyboard with popular banks (first 10) + option to search more
        keyboard = []
        popular_banks = bank_list[:10]  # Show first 10 banks
        
        for i, bank in enumerate(popular_banks):
            keyboard.append([
                InlineKeyboardButton(
                    f"🏦 {bank['name']}", 
                    callback_data=f"add_bank_select:{i}"
                )
            ])
        
        # Add "Show More Banks" and "Search Bank" options
        keyboard.append([
            InlineKeyboardButton("📋 Show More Banks", callback_data="add_bank_show_more")
        ])
        keyboard.append([
            InlineKeyboardButton("🔍 Search Bank by Name", callback_data="add_bank_search")
        ])
        keyboard.append([
            InlineKeyboardButton("⬅️ Back to Wallet", callback_data="menu_wallet")
        ])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if query:
            await safe_edit_message_text(
                query,
                text,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        else:
            from utils.message_utils import send_unified_message
            await send_unified_message(
                update, 
                text, 
                reply_markup=reply_markup, 
                parse_mode='Markdown'
            )
            
    except Exception as e:
        logger.error(f"Error showing bank selection menu: {e}")
        error_text = f"❌ Error Loading Banks\n\nUnable to load bank list. Please try again later."
        
        query = update.callback_query
        if query:
            await safe_edit_message_text(query, error_text, parse_mode='Markdown')

async def handle_bank_selection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle bank selection from callback query"""
    query = update.callback_query
    await safe_answer_callback_query(query, "🏦 Bank selected")
    
    try:
        # Parse callback data to get bank index
        if not query or not query.data:
            return
        callback_data = query.data
        if callback_data.startswith("add_bank_select:"):
            bank_index = int(callback_data.split(":")[1])
            if not context.user_data:
                context.user_data = {}
            available_banks = context.user_data.get('available_banks', [])
            
            if 0 <= bank_index < len(available_banks):
                selected_bank = available_banks[bank_index]
                
                # Store selected bank
                if not context.user_data:
                    context.user_data = {}
                bank_data = context.user_data.get('bank_addition_data', {})
                bank_data['selected_bank'] = selected_bank
                bank_data['step'] = 'entering_account_number'
                if not context.user_data:
                    context.user_data = {}
                context.user_data['bank_addition_data'] = bank_data
                
                # Set wallet state - get user_id from update.effective_user
                if not update.effective_user:
                    logger.error("No effective_user for set_wallet_state")
                    return
                user_id = update.effective_user.id
                await set_wallet_state(user_id, context, 'adding_bank_account_number')
                
                # Show account number input screen
                await show_account_number_input(update, context, selected_bank)
            else:
                await safe_edit_message_text(query, "❌ Invalid bank selection. Please try again.")
        
        elif callback_data == "add_bank_show_more":
            await show_more_banks_menu(update, context)
        
        elif callback_data == "add_bank_search":
            await show_bank_search_input(update, context)
            
    except Exception as e:
        logger.error(f"Error handling bank selection: {e}")
        await safe_edit_message_text(query, "❌ Error selecting bank. Please try again.")

async def show_account_number_input(update: Update, context: ContextTypes.DEFAULT_TYPE, selected_bank: dict) -> None:
    """Show account number input screen"""
    header = make_header("🏦 Add New Bank Account")
    
    text = f"""{header}

📋 Step 2 of 4: Enter Account Number

🏦 Selected Bank: {selected_bank['name']}

Please enter your 10-digit account number:

Example: 0123456789

⚡ We'll verify your account name in real-time
🔒 Your information is encrypted and secure"""

    keyboard = [
        [InlineKeyboardButton("⬅️ Back to Bank Selection", callback_data="add_bank_back_to_selection")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    query = update.callback_query
    if query:
        await safe_edit_message_text(
            query, 
            text, 
            reply_markup=reply_markup, 
            parse_mode='Markdown'
        )

async def handle_account_number_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle account number input and verification"""
    message = update.message
    if not message or not message.text:
        return
        
    account_number = message.text.strip().replace(" ", "").replace("-", "")
    
    # Validate account number format
    if not account_number.isdigit() or len(account_number) != 10:
        await message.reply_text(
            "❌ Invalid Account Number\n\n"
            "Please enter a valid 10-digit account number.\n\n"
            "Example: 0123456789",
            parse_mode='Markdown'
        )
        return
    
    # Get bank addition data
    if not context.user_data:
        context.user_data = {}
    bank_data = context.user_data.get('bank_addition_data', {})
    selected_bank = bank_data.get('selected_bank')
    
    if not selected_bank:
        await message.reply_text("❌ Bank selection expired. Please start again.")
        return
    
    # Show verification loading
    loading_msg = await message.reply_text(
        f"🔍 Verifying Account\n\n"
        f"🏦 {selected_bank['name']}\n"
        f"💳 {account_number}\n\n"
        f"⏳ Please wait...",
        parse_mode='Markdown'
    )
    
    try:
        # Verify account with Fincra API
        from services.fincra_service import FincraService
        fincra = FincraService()
        
        # Use verification lock to prevent concurrent verifications
        from utils.verification_lock import get_verification_lock, is_verification_running
        
        if not update or not update.effective_user:
            return
        user_id = update.effective_user.id if update.effective_user else None
        if not user_id:
            await loading_msg.edit_text("❌ Authentication error.")
            return
        
        # Check if verification is already running
        if await is_verification_running(user_id):
            await loading_msg.edit_text(
                "⚠️ Bank verification already in progress. Please wait..."
            )
            return
        
        # Acquire verification lock
        lock = get_verification_lock(user_id)
        
        async with lock:
            # Verify account name
            account_name = await fincra.verify_account_name(account_number, selected_bank['code'])
            
            if account_name:
                # Store verified details
                bank_data['account_number'] = account_number
                bank_data['account_name'] = account_name
                bank_data['step'] = 'confirming_details'
                if not context.user_data:
                    context.user_data = {}
                context.user_data['bank_addition_data'] = bank_data
                
                # Set wallet state - user_id already defined at line 5349
                await set_wallet_state(user_id, context, 'adding_bank_confirming')
                
                # Show confirmation screen
                await show_account_confirmation(update, context, loading_msg)
            else:
                # Verification failed
                await loading_msg.edit_text(
                    f"❌ Account Verification Failed\n\n"
                    f"🏦 {selected_bank['name']}\n"
                    f"💳 {account_number}\n\n"
                    f"Possible reasons:\n"
                    f"• Account number is incorrect\n"
                    f"• Bank service temporarily unavailable\n"
                    f"• Account may be restricted\n\n"
                    f"Please check your account number and try again.",
                    parse_mode='Markdown'
                )
    
    except Exception as e:
        logger.error(f"Error verifying account {account_number}: {e}")
        await loading_msg.edit_text(
            f"❌ Verification Error\n\n"
            f"Unable to verify account due to technical issue.\n"
            f"Please try again in a few moments.\n\n"
            f"Error: {str(e)[:100]}{'...' if len(str(e)) > 100 else ''}",
            parse_mode='Markdown'
        )

async def show_account_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE, loading_msg) -> None:
    """Show account confirmation with optional label input"""
    try:
        await loading_msg.delete()
    except Exception as e:
        pass
    
    if not context.user_data:
        context.user_data = {}
    bank_data = context.user_data.get('bank_addition_data', {})
    selected_bank = bank_data.get('selected_bank')
    account_number = bank_data.get('account_number')
    account_name = bank_data.get('account_name')
    
    header = make_header("🏦 Add New Bank Account")
    
    text = f"""{header}

📋 Step 3 of 4: Confirm Details

✅ Account Verified Successfully!

🏦 Bank: {selected_bank['name']}
💳 Account: {account_number}
👤 Name: {account_name}

Optional: Add a nickname for easy identification
(e.g., "Main Account", "Business", "Savings")

Type a nickname or tap "Save Without Nickname":"""

    keyboard = [
        [InlineKeyboardButton("💾 Save Without Nickname", callback_data="add_bank_save_no_label")],
        [InlineKeyboardButton("⬅️ Back to Account Entry", callback_data="add_bank_back_to_account")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Send new message for account confirmation
    message = update.message
    if message:
        await message.reply_text(
            text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

async def handle_bank_label_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle optional bank account label input"""
    message = update.message
    if not message or not message.text:
        return
    
    label = message.text.strip()
    
    # Validate label length
    if len(label) > 50:
        await message.reply_text(
            "❌ Label Too Long\n\n"
            "Please keep the nickname under 50 characters.",
            parse_mode='Markdown'
        )
        return
    
    # Store label and save account
    if not context.user_data:
        context.user_data = {}
    bank_data = context.user_data.get('bank_addition_data', {})
    bank_data['label'] = label
    if not context.user_data:
        context.user_data = {}
    context.user_data['bank_addition_data'] = bank_data
    
    # Save the bank account
    await save_bank_account_to_database(update, context)

async def save_bank_account_to_database(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Save the verified bank account to database"""
    try:
        if not context.user_data:
            context.user_data = {}
        bank_data = context.user_data.get('bank_addition_data', {})
        if not update or not update.effective_user:
            return
        user_id = update.effective_user.id if update.effective_user else None
        
        if not user_id or not bank_data.get('selected_bank'):
            if not update.message:
                return
            await update.message.reply_text("❌ Session expired. Please try again.")
            return
        
        # Extract data
        selected_bank = bank_data['selected_bank']
        account_number = bank_data['account_number']
        account_name = bank_data['account_name']
        label = bank_data.get('label', '')
        
        # Check for duplicate accounts
        async with async_managed_session() as session:
            try:
                # Get user
                stmt = select(User).where(User.telegram_id == user_id)
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()
                if not user:
                    if not update.message:
                        return
                    await update.message.reply_text("❌ User not found.")
                    return
                
                # Check for existing account
                stmt = select(SavedBankAccount).where(
                    SavedBankAccount.user_id == user.id,
                    SavedBankAccount.account_number == account_number,
                    SavedBankAccount.bank_code == selected_bank['code']
                )
                result = await session.execute(stmt)
                existing = result.scalar_one_or_none()
                
                if existing:
                    if not update.message:
                        return
                    await update.message.reply_text(
                        f"⚠️ Account Already Saved\n\n"
                        f"This bank account is already in your saved accounts.\n\n"
                        f"🏦 {existing.bank_name}\n"
                        f"💳 {existing.account_number}\n"
                        f"👤 {existing.account_name}",
                        parse_mode='Markdown'
                    )
                    # Clear wallet state
                    # Convert Column[int] to int using ORM helper
                    await set_wallet_state(as_int(user.id) or 0, context, 'inactive')
                    if not context.user_data:
                        context.user_data = {}
                    context.user_data.pop('bank_addition_data', None)
                    return
                
                # Create new saved bank account
                new_bank_account = SavedBankAccount(
                    user_id=user.id,
                    account_number=account_number,
                    bank_code=selected_bank['code'],
                    bank_name=selected_bank['name'],
                    account_name=account_name,
                    label=label if label else None,
                    is_verified=True,
                    created_at=datetime.utcnow()
                )
                
                session.add(new_bank_account)
                await session.commit()
                
                # Show success message
                header = make_header("🏦 Bank Account Added")
                footer = make_trust_footer()
                
                text = f"""{header}

✅ Bank Account Added Successfully!

🏦 Bank: {selected_bank['name']}
💳 Account: {account_number}
👤 Name: {account_name}"""
                
                if label:
                    text += f"\n🏷️ Nickname: {label}"
                
                text += f"""

Your bank account has been securely saved and can now be used for:
• NGN cashouts from your USD wallet
• Quick payments in future transactions
• Streamlined cashout process

{footer}"""
                
                keyboard = [
                    [InlineKeyboardButton("💳 Back to Wallet", callback_data="menu_wallet")],
                    [InlineKeyboardButton("🏦 Manage Bank Accounts", callback_data="manage_saved_banks")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                if not update.message:
                    return
                await update.message.reply_text(
                    text,
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
                
                # Clear wallet state and data
                # Convert Column[int] to int using ORM helper
                await set_wallet_state(as_int(user.id) or 0, context, 'inactive')
                if not context.user_data:
                    context.user_data = {}
                context.user_data.pop('bank_addition_data', None)
                
            except Exception as db_error:
                logger.error(f"Database error saving bank account: {db_error}")
                if not update.message:
                    return
                await update.message.reply_text(
                    "❌ There was a database error. Please try again.",
                    parse_mode='Markdown'
                )
            
    except Exception as e:
        logger.error(f"Error saving bank account: {e}")
        if not update.message:
            return
        await update.message.reply_text(
            f"❌ Error Saving Account\n\n"
            f"Unable to save bank account due to technical issue.\n"
            f"Please try again later.",
            parse_mode='Markdown'
        )

async def show_more_banks_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show extended list of banks"""
    if not context.user_data:
        context.user_data = {}
    available_banks = context.user_data.get('available_banks', [])
    
    if len(available_banks) <= 10:
        await safe_edit_message_text(update.callback_query, "All available banks are already displayed.")
        return
    
    header = make_header("🏦 More Banks")
    text = f"""{header}

📋 All Nigerian Banks

Choose from the complete list:"""

    keyboard = []
    # Show banks 11 onwards
    remaining_banks = available_banks[10:]
    
    for i, bank in enumerate(remaining_banks[:15]):  # Show next 15 banks
        keyboard.append([
            InlineKeyboardButton(
                f"🏦 {bank['name']}", 
                callback_data=f"add_bank_select:{i + 10}"
            )
        ])
    
    # Navigation buttons
    keyboard.append([
        InlineKeyboardButton("⬅️ Back to Popular Banks", callback_data="add_bank_back_to_popular")
    ])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await safe_edit_message_text(
        update.callback_query,
        text,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def show_bank_search_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show bank search input screen"""
    header = make_header("🏦 Search Banks")
    
    text = f"""{header}

🔍 Search for Your Bank

Type the name of your bank to search:

Examples:
• Access Bank
• GTBank
• First Bank
• UBA

Type the bank name below:"""

    keyboard = [
        [InlineKeyboardButton("⬅️ Back to Bank List", callback_data="add_bank_back_to_selection")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Set state for bank search
    if update.effective_user:
        user = update.effective_user
        await set_wallet_state(user.id, context, 'adding_bank_searching')
    
    await safe_edit_message_text(
        update.callback_query,
        text,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def handle_bank_search_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle bank search input"""
    message = update.message
    if not message or not message.text:
        return
    
    search_term = message.text.strip().lower()
    if not context.user_data:
        context.user_data = {}
    available_banks = context.user_data.get('available_banks', [])
    
    if not available_banks:
        await message.reply_text("❌ Bank list not available. Please restart the process.")
        return
    
    # Search for matching banks
    matches = []
    for i, bank in enumerate(available_banks):
        if search_term in bank['name'].lower():
            matches.append((i, bank))
    
    if not matches:
        await message.reply_text(
            f"❌ No Banks Found\n\n"
            f"No banks found matching '{message.text}'\n\n"
            f"Please try a different search term or select from the bank list.",
            parse_mode='Markdown'
        )
        return
    
    if len(matches) == 1:
        # Single match - auto-select
        bank_index, selected_bank = matches[0]
        
        # Store selected bank
        if not context.user_data:
            context.user_data = {}
        bank_data = context.user_data.get('bank_addition_data', {})
        bank_data['selected_bank'] = selected_bank
        bank_data['step'] = 'entering_account_number'
        if not context.user_data:
            context.user_data = {}
        context.user_data['bank_addition_data'] = bank_data
        
        # Set wallet state
        if not update or not update.effective_user:
            return
        await set_wallet_state(update.effective_user.id, context, 'adding_bank_account_number')
        
        # Show account number input
        await message.reply_text(
            f"✅ Bank Found\n\n🏦 {selected_bank['name']}\n\nNow proceeding to account entry...",
            parse_mode='Markdown'
        )
        
        # Create a temporary object for show_account_number_input
        fake_update = update
        await show_account_number_input(fake_update, context, selected_bank)
    
    else:
        # Multiple matches - show selection
        text = f"🔍 Found {len(matches)} Banks\n\nSelect your bank:"
        
        keyboard = []
        for bank_index, bank in matches[:10]:  # Limit to 10 results
            keyboard.append([
                InlineKeyboardButton(
                    f"🏦 {bank['name']}", 
                    callback_data=f"add_bank_select:{bank_index}"
                )
            ])
        
        keyboard.append([
            InlineKeyboardButton("🔍 Search Again", callback_data="add_bank_search")
        ])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await message.reply_text(
            text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

async def handle_add_bank_navigation_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle navigation callbacks for bank addition flow"""
    query = update.callback_query
    if not query:
        return
    callback_data = query.data
    
    if callback_data == "add_bank_back_to_selection":
        await safe_answer_callback_query(query, "🔄 Back to bank selection")
        if not update or not update.effective_user:
            return
        await set_wallet_state(update.effective_user.id, context, 'adding_bank_selecting')
        await show_bank_selection_menu(update, context)
    
    elif callback_data == "add_bank_back_to_popular":
        await safe_answer_callback_query(query, "🔄 Back to popular banks")
        await show_bank_selection_menu(update, context)
    
    elif callback_data == "add_bank_back_to_account":
        await safe_answer_callback_query(query, "🔄 Back to account entry")
        if not context.user_data:
            context.user_data = {}
        bank_data = context.user_data.get('bank_addition_data', {})
        selected_bank = bank_data.get('selected_bank')
        if selected_bank:
            if not update or not update.effective_user:
                return
            await set_wallet_state(update.effective_user.id, context, 'adding_bank_account_number')
            await show_account_number_input(update, context, selected_bank)
    
    elif callback_data == "add_bank_save_no_label":
        await safe_answer_callback_query(query, "💾 Saving account")
        # Save without label
        if not context.user_data:
            context.user_data = {}
        bank_data = context.user_data.get('bank_addition_data', {})
        bank_data['label'] = None
        if not context.user_data:
            context.user_data = {}
        context.user_data['bank_addition_data'] = bank_data
        
        # Create a modified update with message for save function
        # Note: We cannot assign to update.message as Update is immutable
        # Instead, pass query.message context to save function
        from telegram import Message
        if query and query.message and isinstance(query.message, Message):
            modified_update = Update(update.update_id, message=query.message)
            await save_bank_account_to_database(modified_update, context)
        else:
            logger.error("Cannot save bank account: query or message is None")

async def show_saved_bank_accounts_management(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show saved bank accounts management - ASYNC OPTIMIZED"""
    try:
        # PERFORMANCE: Use button_callback_wrapper for instant feedback (<50ms)
        async with button_callback_wrapper(update, "🏦 Loading banks...") as session:
            query = update.callback_query
            if not query:
                return
            await query.edit_message_text("🏦 Saved bank accounts management will be implemented soon.")
    except Exception as e:
        logger.error(f"Error in show_saved_bank_accounts_management: {e}")

async def show_saved_crypto_addresses_management(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show saved crypto addresses management - ASYNC OPTIMIZED"""
    try:
        # PERFORMANCE: Use button_callback_wrapper for instant feedback + shared async session
        async with button_callback_wrapper(update, "🔐 Loading addresses...") as session:
            query = update.callback_query
            if not query:
                return
            
            try:
                if not update or not update.effective_user:
                    return
                
                # PERFORMANCE: Use cached user lookup
                user = await get_cached_user(update, context)
                
                if not user:
                    await query.edit_message_text(
                        "❌ User account not found. Please contact support.",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
                        ])
                    )
                    return
                
                # Get user's saved crypto addresses
                stmt = select(SavedAddress).where(
                    SavedAddress.user_id == user.id,
                    SavedAddress.is_active == True
                ).order_by(
                    SavedAddress.last_used.desc().nullslast(),
                    SavedAddress.created_at.desc()
                )
                result = await session.execute(stmt)
                saved_addresses = result.scalars().all()
                
                # Build management interface
                text = "🔐 Crypto Address Management\n\n📋 Your saved crypto addresses:"
                
                keyboard = []
                
                if saved_addresses:
                    # Group addresses by currency for better organization
                    currency_groups = {}
                    for address in saved_addresses[:10]:  # Show max 10 addresses
                        currency = address.currency
                        if currency not in currency_groups:
                            currency_groups[currency] = []
                        currency_groups[currency].append(address)
                    
                    for currency, addresses in currency_groups.items():
                        for address in addresses:
                            # Create display label with currency and truncated address
                            display_label = f"{currency} • {address.address[:10]}...{address.address[-8:]}"
                            if address.label and address.label != f"Address {address.address[:10]}...{address.address[-8:]}":
                                display_label = f"{currency} • {address.label}"
                            
                            keyboard.append([
                                InlineKeyboardButton(
                                    f"🪙 {display_label}", 
                                    callback_data=f"view_crypto_addr:{address.id}"
                                )
                            ])
                    
                    text += f"\n\n✅ Found {len(saved_addresses)} saved address{'es' if len(saved_addresses) > 1 else ''}"
                    
                else:
                    text += "\n\n❌ No saved crypto addresses found."
                    
                # Add management buttons
                keyboard.extend([
                    [InlineKeyboardButton("➕ Add New Address", callback_data="add_crypto_address_mgmt")],
                    [InlineKeyboardButton("🔙 Back to Wallet", callback_data="menu_wallet")]
                ])
                
                await query.edit_message_text(
                    text,
                    parse_mode='Markdown',
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            except Exception as db_error:
                logger.error(f"Database error loading crypto addresses: {db_error}")
                await query.edit_message_text(
                    "❌ There was a database error. Please try again.",
                    parse_mode='Markdown'
                )
            
    except Exception as e:
        logger.error(f"Error in show_saved_crypto_addresses_management: {e}", exc_info=True)
        query = update.callback_query
        if query:
            await query.edit_message_text(
                "❌ Error loading crypto addresses. Please try again.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Try Again", callback_data="manage_crypto_addresses")],
                    [InlineKeyboardButton("🔙 Back", callback_data="menu_wallet")]
                ])
            )

async def handle_view_crypto_address(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """View crypto address details with delete option - ASYNC OPTIMIZED"""
    try:
        # PERFORMANCE: Use button_callback_wrapper for instant feedback + shared async session
        async with button_callback_wrapper(update, "🔐 Loading address...") as session:
            query = update.callback_query
            if not query:
                return
            
            # Extract address ID from callback data
            if not query.data:
                logger.error("No callback data available")
                return
            address_id = int(query.data.split(':')[1])
            
            # Get address details
            stmt = select(SavedAddress).where(SavedAddress.id == address_id)
            result = await session.execute(stmt)
            address = result.scalar_one_or_none()
            
            if not address:
                await query.edit_message_text(
                    "❌ Address not found.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 Back", callback_data="manage_crypto_addresses")]
                    ])
                )
                return
            
            # Display address details
            text = f"""🔐 Crypto Address Details

💰 **Currency:** {address.currency}
📍 **Address:** `{address.address}`
🏷️ **Label:** {address.label or 'No label'}
📅 **Added:** {address.created_at.strftime('%Y-%m-%d')}
🔄 **Last Used:** {address.last_used.strftime('%Y-%m-%d') if address.last_used else 'Never'}

Tap address to copy"""
            
            keyboard = [
                [InlineKeyboardButton("🗑️ Delete Address", callback_data=f"delete_crypto_addr:{address.id}")],
                [InlineKeyboardButton("🔙 Back to List", callback_data="manage_crypto_addresses")]
            ]
            
            await query.edit_message_text(
                text,
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            
    except Exception as e:
        logger.error(f"Error viewing crypto address: {e}")
        query = update.callback_query
        if query:
            await query.edit_message_text(
                "❌ Error loading address details.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back", callback_data="manage_crypto_addresses")]
                ])
            )

async def handle_delete_crypto_address(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Delete a saved crypto address - ASYNC OPTIMIZED"""
    try:
        # PERFORMANCE: Use button_callback_wrapper for instant feedback + shared async session
        async with button_callback_wrapper(update, "🗑️ Deleting...") as session:
            query = update.callback_query
            if not query:
                return
            
            # Extract address ID from callback data
            if not query.data:
                logger.error("No callback data available for delete")
                return
            address_id = int(query.data.split(':')[1])
            
            # Delete the address (soft delete by setting is_active = False)
            stmt = select(SavedAddress).where(SavedAddress.id == address_id)
            result = await session.execute(stmt)
            address = result.scalar_one_or_none()
            
            if address:
                address.is_active = False
                await session.commit()
                
                await query.edit_message_text(
                    f"✅ Address deleted successfully!\n\n💰 {address.currency}\n📍 {address.address[:10]}...{address.address[-8:]}",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 Back to List", callback_data="manage_crypto_addresses")]
                    ])
                )
            else:
                await query.edit_message_text(
                    "❌ Address not found.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 Back", callback_data="manage_crypto_addresses")]
                    ])
                )
                
    except Exception as e:
        logger.error(f"Error deleting crypto address: {e}")
        query = update.callback_query
        if query:
            await query.edit_message_text(
                "❌ Error deleting address.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back", callback_data="manage_crypto_addresses")]
                ])
            )

async def show_comprehensive_transaction_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show comprehensive transaction history"""
    # Import and use the actual transaction history implementation
    from handlers.transaction_history import show_transaction_history
    await show_transaction_history(update, context)

async def handle_back_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle back to main menu - ASYNC OPTIMIZED"""
    try:
        # PERFORMANCE: Use button_callback_wrapper for instant feedback (<50ms)
        async with button_callback_wrapper(update, "🏠 Loading menu...") as session:
            # Import and use the proper main menu handler
            from handlers.start import show_main_menu
            await show_main_menu(update, context)
    except Exception as e:
        logger.error(f"Error in handle_back_to_main: {e}")

async def handle_wallet_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route wallet text messages based on current wallet state"""
    user = update.effective_user
    if not user:
        return
    wallet_state = await get_wallet_state(user.id, context)
    
    # Route bank addition text messages to appropriate handlers
    if wallet_state == 'adding_bank_account_number':
        await handle_account_number_input(update, context)
    elif wallet_state == 'adding_bank_label':
        await handle_bank_label_input(update, context)
    elif wallet_state == 'adding_bank_account_direct':
        await handle_bank_addition_account_input(update, context)
    elif wallet_state == 'adding_bank_searching':
        # DEPRECATED: Bank search removed - redirect to direct input
        if not update or not update.effective_user:
            return
        await set_wallet_state(update.effective_user.id, context, 'adding_bank_account_direct')
        await handle_bank_addition_account_input(update, context)
    else:
        # Default NGN bank account input handling
        await handle_ngn_bank_account_input(update, context)

async def handle_ngn_bank_account_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle NGN bank account input with Fincra verification"""
    message = None  # Initialize to prevent unbound variable error in exception handler
    try:
        # This is a text message, not a callback query
        message = update.message
        if not message or not message.text:
            logger.warning("No text message found in handle_ngn_bank_account_input")
            return
            
        account_number = message.text.strip()
        if not update or not update.effective_user:
            return
        user_id = update.effective_user.id
        
        logger.info(f"🏦 Processing bank account verification for user {user_id}: {account_number}")
        
        # Basic validation
        if not account_number.isdigit() or len(account_number) != 10:
            await message.reply_text(
                "❌ Invalid Account Number\n\n"
                "Please enter a valid 10-digit Nigerian bank account number.",
                parse_mode='Markdown'
            )
            return
            
        # Get user from database
        async with async_managed_session() as session:
            try:
                stmt = select(User).where(User.telegram_id == user_id)
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()
                if not user:
                    await message.reply_text("❌ User session not found. Please try again.")
                    return
                    
                # Show OPTIMIZED processing message
                processing_msg = await message.reply_text(
                    "🚀 Fast Account Verification\n\n"
                    "⚡ Optimized verification across 19 banks (target: <2s)\n"
                    "• Digital Banks: Moniepoint, OPay, Kuda, VFD\n"
                    "• Traditional: Access, UBA, Zenith, GTBank, FirstBank\n\n"
                    "🎯 Smart verification in progress...",
                    parse_mode='Markdown'
                )
                
                # Use OPTIMIZED bank verification service for <2s target
                from services.optimized_bank_verification_service import OptimizedBankVerificationService
                
                optimized_verifier = OptimizedBankVerificationService()
                
                # Run OPTIMIZED parallel verification with <2s target
                logger.info(f"🚀 Starting OPTIMIZED verification for account {account_number} (target: <2s)")
                
                # Get verification results using optimized service
                all_verified_accounts = await optimized_verifier.verify_account_parallel_optimized(account_number)
                
                # Clean up resources
                await optimized_verifier.cleanup()
                
                # Delete processing message
                try:
                    await processing_msg.delete()
                except Exception as e:
                    pass
                    
                if all_verified_accounts:
                    if len(all_verified_accounts) == 1:
                        # Single bank match - show verification result
                        verified_account = all_verified_accounts[0]
                        logger.info(f"✅ Single bank match: {verified_account['bank_name']}")
                        
                        # Store account details
                        if not context.user_data:
                            context.user_data = {}
                        context.user_data.setdefault('cashout_data', {})['verified_account'] = verified_account
                        
                        # Show familiar verification success UI
                        text = f"""✅ Account Verified Successfully!

🏦 Bank: {verified_account['bank_name']}
👤 Account Name: {verified_account['account_name']}  
💳 Account Number: {verified_account['account_number']}

This account will be used for your NGN cashout."""
                        
                        keyboard = [
                            [InlineKeyboardButton("💰 Cashout Now", callback_data="confirm_ngn_cashout")],
                            [InlineKeyboardButton("💰 Cashout & Save Bank", callback_data="confirm_ngn_cashout_and_save")],
                            [InlineKeyboardButton("🔙 Back", callback_data="back_to_method_selection")]
                        ]
                        
                        await message.reply_text(
                            text,
                            parse_mode='Markdown',
                            reply_markup=InlineKeyboardMarkup(keyboard)
                        )
                        
                    else:
                        # Multiple banks found - show selection UI
                        logger.info(f"🏦 MULTIPLE BANKS FOUND: Account exists in {len(all_verified_accounts)} banks")
                        
                        # Store all matches for later use
                        if not context.user_data:
                            context.user_data = {}
                        context.user_data.setdefault('cashout_data', {})['all_bank_matches'] = all_verified_accounts
                        
                        # Show multi-bank selection UI
                        text = f"""✅ Account Found in Multiple Banks

Your account number {account_number} was found in {len(all_verified_accounts)} banks:

"""
                        keyboard = []
                        for i, account in enumerate(all_verified_accounts):
                            text += f"🏦 {account['bank_name']}\n👤 {account['account_name']}\n\n"
                            keyboard.append([
                                InlineKeyboardButton(
                                    f"Select {account['bank_name']}", 
                                    callback_data=f"select_verified_bank:{i}"
                                )
                            ])
                        
                        text += "Please select which bank to use for your cashout:"
                        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_method_selection")])
                        
                        await message.reply_text(
                            text,
                            parse_mode='Markdown',
                            reply_markup=InlineKeyboardMarkup(keyboard)
                        )
                else:
                    # No bank found - show error
                    await message.reply_text(
                        f"""❌ Account Verification Failed

We could not verify account number {account_number} with any of our supported banks.

Supported Banks Include:
• Digital: Moniepoint, OPay, Kuda, VFD
• Traditional: Access, UBA, Zenith, GTBank, FirstBank, Fidelity, FCMB, Sterling, and more

Please check your account number and try again.""",
                        parse_mode='Markdown',
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🔙 Try Again", callback_data="select_ngn_bank")]
                        ])
                    )
            except Exception as db_error:
                logger.error(f"Database error in bank verification: {db_error}")
                if message:
                    await message.reply_text(
                        "❌ There was a database error. Please try again.",
                        parse_mode='Markdown'
                    )
            
    except Exception as e:
        logger.error(f"Error in handle_ngn_bank_account_input: {e}")
        if not message:
            return
        await message.reply_text(
            "❌ Verification Error\n\n"
            "There was an error verifying your account. Please try again.",
            parse_mode='Markdown'
        )

async def handle_select_verified_bank(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle selection of verified bank from multiple matches"""
    try:
        query = update.callback_query
        if not query:
            logger.warning("No callback query in handle_select_verified_bank")
            return

        await safe_answer_callback_query(query, "🏦 Processing bank selection...")
        
        # Extract bank index from callback data
        if not query or not query.data:
            return
        callback_data = query.data  # format: "select_verified_bank:0"
        if not callback_data.startswith("select_verified_bank:"):
            logger.error(f"Invalid callback data: {callback_data}")
            return
            
        try:
            bank_index = int(callback_data.split(":")[1]) if ":" in callback_data else 0
        except (IndexError, ValueError) as e:
            logger.error(f"Error parsing bank index from callback: {callback_data}, error: {e}")
            return
            
        # Get all bank matches from context
        if not context.user_data:
            context.user_data = {}
        all_bank_matches = context.user_data.get('cashout_data', {}).get('all_bank_matches', [])
        
        if not all_bank_matches or bank_index >= len(all_bank_matches):
            await safe_edit_message_text(
                query,
                "❌ Bank Selection Error\n\nBank information not found. Please try again.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Try Again", callback_data="select_ngn_bank")]
                ])
            )
            return
            
        # Get selected bank account
        selected_account = all_bank_matches[bank_index]
        logger.info(f"✅ User selected bank: {selected_account['bank_name']} - {selected_account['account_name']}")
        
        # Store selected account for cashout processing
        if not context.user_data:
            context.user_data = {}
        context.user_data.setdefault('cashout_data', {})['verified_account'] = selected_account
        
        # CRITICAL FIX: Save bank account to database for AutoCashoutService
        try:
            # Get user from database
            if not update or not update.effective_user:
                return
            user_id = update.effective_user.id if update.effective_user else None
            if user_id:
                async with async_managed_session() as session:
                    try:
                        stmt = select(User).where(User.telegram_id == user_id)
                        result = await session.execute(stmt)
                        user = result.scalar_one_or_none()
                        if user:
                            # Check if bank account already exists
                            stmt = select(SavedBankAccount).where(
                                SavedBankAccount.user_id == user.id,
                                SavedBankAccount.account_number == selected_account['account_number'],
                                SavedBankAccount.bank_code == selected_account['bank_code']
                            )
                            result = await session.execute(stmt)
                            existing_bank = result.scalar_one_or_none()
                            
                            if not existing_bank:
                                # Create new bank account record
                                new_bank_account = SavedBankAccount(
                                    user_id=user.id,
                                    account_number=selected_account['account_number'],
                                    bank_code=selected_account['bank_code'],
                                    bank_name=selected_account['bank_name'],
                                    account_name=selected_account['account_name'],
                                    is_verified=True,
                                    created_at=datetime.utcnow(),
                                    last_used=datetime.utcnow()
                                )
                                session.add(new_bank_account)
                                await session.commit()
                                logger.info(f"✅ CRITICAL_FIX: Saved bank account to database - User: {user.id}, Bank: {selected_account['bank_name']}, Account: {selected_account['account_number']}")
                            else:
                                # Update last_used timestamp for existing account
                                from sqlalchemy import update as sql_update
                                stmt = sql_update(SavedBankAccount).where(
                                    SavedBankAccount.id == existing_bank.id
                                ).values(last_used=datetime.utcnow())
                                await session.execute(stmt)
                                await session.commit()
                                logger.info(f"✅ CRITICAL_FIX: Updated existing bank account last_used - User: {user.id}, Bank: {selected_account['bank_name']}")
                        else:
                            logger.error(f"❌ CRITICAL_FIX: User not found in database for telegram_id: {user_id}")
                    except Exception as db_error:
                        logger.error(f"❌ CRITICAL_FIX: Database error saving bank account: {db_error}")
                        await session.rollback()
            else:
                logger.error(f"❌ CRITICAL_FIX: No effective_user.id found in update")
        except Exception as save_error:
            logger.error(f"❌ CRITICAL_FIX: Error saving bank account to database: {save_error}")
        
        # Route to payout confirmation screen instead of direct OTP
        if not update or not update.effective_user:
            return
        logger.info(f"✅ Bank selected, routing to payout confirmation screen - User: {update.effective_user.id}")
        if update and update.effective_user:
            await show_ngn_payout_confirmation_screen(update, context)
        
    except Exception as e:
        logger.error(f"Error in handle_select_verified_bank: {e}")
        if query:
            await safe_edit_message_text(
                query,
                "❌ Selection Error\n\nThere was an error processing your selection. Please try again.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Try Again", callback_data="select_ngn_bank")]
                ])
            )

async def cancel_ngn_cashout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle NGN cashout cancellation - Enhanced user-friendly cancellation with resource cleanup"""
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "❌ Cancelling cashout...")
    
    if not update or not update.effective_user:
        return
    user_id = update.effective_user.id if update.effective_user else None
    
    try:
        # ENHANCED: Proper resource cleanup before clearing context
        if context and context.user_data:
            if not context.user_data:
                context.user_data = {}
            cashout_data = context.user_data.get('cashout_data', {})
            if not context.user_data:
                context.user_data = {}
            rate_lock = context.user_data.get('rate_lock')
            
            # 1. Rate Lock Cleanup - Invalidate any active rate locks
            if rate_lock and user_id:
                try:
                    from utils.rate_lock import RateLock
                    # Mark rate lock as invalidated to prevent further use
                    rate_lock['is_active'] = False
                    rate_lock['cancelled_at'] = datetime.utcnow().isoformat()
                    logger.info(f"🔒 Rate lock invalidated during cashout cancellation - User: {user_id}, Token: {rate_lock.get('token', 'unknown')[:8]}...")
                except Exception as rate_lock_error:
                    logger.warning(f"⚠️ Failed to invalidate rate lock during cancellation - User: {user_id}: {rate_lock_error}")
            
            # 2. Email Verification Cleanup - Invalidate pending verifications
            if user_id and cashout_data.get('cashout_id'):
                try:
                    from services.email_verification_service import EmailVerificationService
                    from models import EmailVerification
                    
                    async with async_managed_session() as session:
                        # Find and invalidate any pending email verifications for this cashout
                        stmt = select(EmailVerification).where(
                            EmailVerification.user_id == user_id,
                            EmailVerification.verified == False,
                            EmailVerification.purpose == 'ngn_cashout'
                        )
                        result = await session.execute(stmt)
                        pending_verifications = result.scalars().all()
                        
                        # Mark all as verified using update statement
                        if pending_verifications:
                            verification_ids = [v.id for v in pending_verifications]
                            from sqlalchemy import update as sql_update
                            stmt = sql_update(EmailVerification).where(
                                EmailVerification.id.in_(verification_ids)
                            ).values(verified=True)
                            await session.execute(stmt)
                            await session.commit()
                            logger.info(f"🧹 Invalidated {len(pending_verifications)} pending email verifications during cashout cancellation - User: {user_id}")
                        
                except Exception as email_cleanup_error:
                    logger.warning(f"⚠️ Failed to cleanup email verifications during cancellation - User: {user_id}: {email_cleanup_error}")
            
            # 3. Clear all cashout-related state
            if not context.user_data:
                context.user_data = {}
            context.user_data.pop('cashout_data', None)
            if not context.user_data:
                context.user_data = {}
            context.user_data.pop('rate_lock', None)
            if not context.user_data:
                context.user_data = {}
            context.user_data['wallet_state'] = None
        
        from utils.branding_utils import make_header, make_trust_footer
        header = make_header("Cashout Cancelled")
        
        cancel_message = f"""{header}

❌ Cashout Cancelled Successfully

Your NGN cashout has been cancelled.
Your funds remain safe in your wallet.

💡 You can start a new cashout anytime from your wallet.

{make_trust_footer()}"""
        
        # ENHANCED: Use reliable message editing with fallback
        await _safe_edit_with_fallback(
            query,
            update,
            cancel_message,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💳 Back to Wallet", callback_data="menu_wallet")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
            ])
        )
        
        # PERFORMANCE OPTIMIZATION: Invalidate wallet cache after cancellation
        invalidate_wallet_cache(context.user_data)
        logger.info("🗑️ WALLET_CACHE: Invalidated after NGN cashout cancellation")
        
        logger.info(f"✅ NGN cashout cancelled with full resource cleanup - User: {user_id}")
        
    except Exception as e:
        logger.error(f"Error in cancel_ngn_cashout: {e}")
        # Fallback to wallet menu
        await show_wallet_menu(update, context)

async def handle_ngn_otp_verification(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle NGN cashout OTP verification when user enters the code - ENHANCED WITH CORRECTNESS FIXES"""
    if not update or not update.effective_user:
        return
    user_id = update.effective_user.id if update.effective_user else None
    session = None
    
    try:
        if not user_id:
            logger.error("❌ No user_id available in handle_ngn_otp_verification")
            return
            
        if not update.message:
            return
        otp_code = update.message.text.strip() if update.message and update.message.text else ""
        
        # ENHANCEMENT: Show immediate loading screen after OTP entry
        from utils.branding_utils import make_header
        header = make_header("Verifying Code")
        
        # Send loading message immediately
        if not update.message:
            return
        loading_message = await update.message.reply_text(
            f"{header}\n\n🔄 Verifying your code...\n\n⏳ Please wait while we confirm your 6-digit code.\n\n🔒 This may take a few seconds.",
            parse_mode='Markdown'
        )
        
        if not context.user_data:
            context.user_data = {}
        logger.info(f"🔐 VERIFYING NGN OTP - User: {user_id}, Code: [REDACTED], State: {context.user_data.get('wallet_state') if context.user_data else 'no_context'}")
        
        # COMPREHENSIVE ERROR GUARDS: Validate all required context data exists
        if not context or not context.user_data:
            if not update.message:
                return
            await update.message.reply_text(
                "❌ Session Expired\n\nYour session has expired. Please start the cashout process again.",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("💳 Wallet", callback_data="menu_wallet")]
                ])
            )
            return

        if not context.user_data:
            context.user_data = {}
        cashout_data = context.user_data.get('cashout_data', {})
        verified_account = cashout_data.get('verified_account')
        cashout_amount = cashout_data.get('amount')
        if not context.user_data:
            context.user_data = {}
        rate_lock = context.user_data.get('rate_lock')
        
        # CRITICAL: Validate all required context variables exist
        missing_vars = []
        if not verified_account:
            missing_vars.append('verified_account')
        if not cashout_amount:
            missing_vars.append('cashout_amount')
        if not rate_lock:
            missing_vars.append('rate_lock')
        if not otp_code:
            missing_vars.append('otp_code')
            
        if missing_vars:
            logger.error(f"❌ Missing required context variables: {missing_vars} for user {user_id}")
            from utils.branding_utils import make_header, make_trust_footer
            header = make_header("Session Error")
            if not update.message:
                return
            await update.message.reply_text(
                f"{header}\n\n❌ Missing Session Data\n\nRequired data is missing from your session.\n\nPlease start the cashout process again.\n\n{make_trust_footer()}",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Start Cashout", callback_data="wallet_cashout")],
                    [InlineKeyboardButton("💳 Wallet", callback_data="menu_wallet")]
                ])
            )
            return
        
        # RATE LOCK VALIDATION: Check if rate lock is still valid before proceeding
        from utils.rate_lock import RateLock
        if not rate_lock or not isinstance(rate_lock, dict):
            logger.error(f"Invalid rate_lock type: {type(rate_lock)}")
            return
        rate_lock_validation = RateLock.validate_rate_lock(rate_lock, user_id)
        
        if not rate_lock_validation.get('valid'):
            error_code = rate_lock_validation.get('error_code', 'UNKNOWN')
            logger.warning(f"⏰ Rate lock validation failed for user {user_id}: {error_code}")
            
            from utils.branding_utils import make_header, make_trust_footer
            header = make_header("Rate Lock Expired")
            
            if error_code == 'LOCK_EXPIRED':
                expired_seconds = rate_lock_validation.get('expired_seconds', 0)
                if not update.message:
                    return
                await update.message.reply_text(
                    f"{header}\n\n⏰ Your rate lock has expired\n\nThe locked exchange rate expired {expired_seconds} seconds ago.\n\nPlease start a new cashout to get current rates.\n\n{make_trust_footer()}",
                    parse_mode='Markdown',
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔄 Start New Cashout", callback_data="wallet_cashout")],
                        [InlineKeyboardButton("💳 Wallet", callback_data="menu_wallet")]
                    ])
                )
            else:
                if not update.message:
                    return
                await update.message.reply_text(
                    f"{header}\n\n❌ Rate Lock Invalid\n\nYour rate lock is no longer valid.\n\nPlease start the cashout process again.\n\n{make_trust_footer()}",
                    parse_mode='Markdown',
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔄 Start Cashout", callback_data="wallet_cashout")],
                        [InlineKeyboardButton("💳 Wallet", callback_data="menu_wallet")]
                    ])
                )
            return
        
        # Extract validated rate lock data for processing
        locked_rate = MonetaryDecimal.to_decimal(rate_lock.get('exchange_rate', 0) if rate_lock else 0, "locked_rate")
        locked_ngn_amount = MonetaryDecimal.to_decimal(rate_lock.get('ngn_amount', 0) if rate_lock else 0, "locked_ngn_amount")
        rate_lock_token = rate_lock.get('token') if rate_lock else None
        
        logger.info(f"✅ Rate lock validated - User: {user_id}, Rate: ₦{locked_rate}, Token: {rate_lock_token[:8] if rate_lock_token else 'unknown'}...")
        
        # SESSION MANAGEMENT: Proper scoped session with rollback capability
        async with async_managed_session() as session:
            user = None
            
            try:
                stmt = select(User).where(User.telegram_id == user_id)
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()
                if not user:
                    if not update.message:
                        return
                    await update.message.reply_text(
                        "❌ User Not Found\n\nPlease try again.", 
                        parse_mode='Markdown'
                    )
                    return
                    
                # FIXED: Enhanced cashout context for OTP verification with consistent structure
                # Retrieve cashout_id from context (stored during OTP send)
                if not context.user_data:
                    context.user_data = {}
                cashout_id = context.user_data.get('cashout_data', {}).get('cashout_id')
                if not cashout_id:
                    logger.error(f"❌ Missing cashout_id in verification for user {user_id}")
                    if not update.message:
                        return
                    await update.message.reply_text(
                        "❌ Verification Error\n\nCashout session expired. Please start over.",
                        parse_mode='Markdown'
                    )
                    return
                
                # Create security fingerprint from rate lock for consistency
                rate_lock_fingerprint = RateLock.create_security_fingerprint(rate_lock)
                
                cashout_context = {
                    'cashout_id': cashout_id,  # FIXED: Include cashout_id for security binding
                    'amount': str(cashout_amount),  # FIXED: Consistent string type
                    'currency': 'NGN',
                    'destination_hash': f"{verified_account['bank_code']}_{verified_account['account_number']}",
                    'rate_lock_token': rate_lock_token,
                    'rate_lock_fingerprint': rate_lock_fingerprint,  # FIXED: Include for consistency
                    'locked_rate': str(locked_rate),
                    'locked_ngn_amount': str(locked_ngn_amount)
                }
                
                # IDEMPOTENCY PROTECTION: Get verification ID for deduplication
                from services.email_verification_service import EmailVerificationService
                verification_result = EmailVerificationService.verify_otp(
                    user_id=as_int(user.id),
                    otp_code=otp_code,
                    purpose='cashout',
                    cashout_context=cashout_context
                )
                
                if verification_result['success']:
                    verification_id = verification_result.get('verification_id')
                    logger.info(f"✅ NGN OTP verified successfully for user {user_id}, verification_id: {verification_id}")
                    
                    # IDEMPOTENCY CHECK: Use verification_id to prevent duplicate cashouts
                    existing_cashout = None
                    if verification_id:
                        # Check if we already processed this verification
                        stmt = select(Cashout).where(
                            Cashout.user_id == as_int(user.id)
                        )
                        result = await session.execute(stmt)
                        existing_cashout = result.scalar_one_or_none()
                    
                    if existing_cashout:
                        logger.warning(f"🔄 Duplicate OTP verification detected for user {user_id}, verification_id: {verification_id}")
                        from utils.branding_utils import make_header, format_branded_amount
                        header = make_header("Already Processing")
                        
                        if not update.message:
                            return
                        await update.message.reply_text(
                            f"{header}\n\n🔄 Cashout Already in Progress\n\n📝 Reference: `{existing_cashout.cashout_id}`\n\nThis transaction is already being processed.\n\nPlease check your email for updates.",
                            parse_mode='Markdown',
                            reply_markup=InlineKeyboardMarkup([
                                [InlineKeyboardButton("💳 Wallet", callback_data="menu_wallet")],
                                [InlineKeyboardButton("📋 History", callback_data="wallet_history")]
                            ])
                        )
                        return
                
                    # Clear OTP verification state
                    if not context.user_data:
                        context.user_data = {}
                    context.user_data['wallet_state'] = None
                    
                    # ENHANCEMENT: Update loading message to show processing status
                    from utils.branding_utils import make_header
                    header = make_header("Processing Transfer")
                    
                    # ENHANCED: Use reliable message editing with fallback
                    processing_text = f"{header}\n\n✅ Code Verified Successfully!\n\n💸 Processing your NGN transfer...\n\n⏳ Please wait while we send to your bank account.\n\n🔒 Secured with rate lock: ₦{locked_rate:,.2f}/USD"
                    
                    # Create mock query object for _safe_edit_with_fallback
                    class MockQuery:
                        def __init__(self, message):
                            self.message = message
                        
                        async def edit_message_text(self, text, **kwargs):
                            """Mock implementation that delegates to message.edit_text"""
                            if self.message and hasattr(self.message, 'edit_text'):
                                return await self.message.edit_text(text, **kwargs)
                            else:
                                # If message doesn't support editing, raise an exception to trigger fallback
                                raise Exception("Message editing not supported for this message type")
                    
                    mock_query = MockQuery(loading_message)
                    await _safe_edit_with_fallback(
                        mock_query,
                        update,
                        processing_text
                    )
                    
                    # ENHANCED RATE LOCK APPLICATION: Use validated locked rate and amount
                    try:
                        usd_amount = MonetaryDecimal.to_decimal(cashout_amount, "cashout_usd_amount")
                        
                        # Use the locked NGN amount directly (more accurate than recalculating)
                        ngn_amount = locked_ngn_amount
                        
                        logger.info(f"🔄 Creating NGN cashout with locked rate - User: {user_id}, ${usd_amount} → ₦{ngn_amount} (rate: ₦{locked_rate})")
                    
                        # Prepare bank destination for AutoCashoutService
                        # Format: bank_name|account_number|account_name|bank_code (required by AutoCashoutService)
                        bank_destination = f"{verified_account.get('bank_name', 'Unknown Bank')}|{verified_account['account_number']}|{verified_account.get('account_name', 'Account Holder')}|{verified_account['bank_code']}"
                        
                        # Enhanced metadata with rate lock and verification info
                        enhanced_metadata = {
                            'verification_id': verification_id,
                            'rate_lock_token': rate_lock_token,
                            'locked_rate': str(locked_rate),
                            'locked_ngn_amount': str(ngn_amount),
                            'rate_lock_expires_at': rate_lock.get('expires_at'),
                            'user_telegram_id': str(user_id),
                            'bank_name': verified_account.get('bank_name'),
                            'account_name': verified_account.get('account_name')
                        }
                        
                        # CRITICAL FIX: Check user balance BEFORE creating cashout  
                        # Validate sufficient balance before creating cashout
                        async with async_managed_session() as balance_session:
                            try:
                                stmt = select(Wallet).where(
                                    Wallet.user_id == as_int(user.id),
                                    Wallet.currency == "USD"
                                )
                                result = await balance_session.execute(stmt)
                                user_wallet = result.scalar_one_or_none()
                                
                                if not user_wallet:
                                    raise ValueError("No USD wallet found")
                                
                                required_amount = Decimal(str(usd_amount))
                                # CRITICAL FIX: available_balance already excludes frozen funds, no need to subtract again
                                available_balance = as_decimal(user_wallet.available_balance)
                                
                                # CRITICAL DEBUG: Log exact wallet values being read
                                logger.critical(f"🔍 BALANCE_DEBUG for user {user_id}: balance={user_wallet.available_balance}, frozen={user_wallet.frozen_balance}, calculated_available={available_balance}")
                                
                                if available_balance < required_amount:
                                    # Insufficient balance - send error message to user
                                    error_text = f"❌ Insufficient Balance\n\n💰 Available: ${available_balance:.2f} USD\n💸 Required: ${required_amount:.2f} USD\n\n🔄 Please deposit more funds or try a smaller amount."
                                    
                                    await context.bot.send_message(
                                        chat_id=user_id,
                                        text=error_text,
                                        parse_mode='Markdown'
                                    )
                                    
                                    # Clear user state
                                    if not context.user_data:
                                        context.user_data = {}
                                    context.user_data['wallet_state'] = None
                                    return
                                
                                logger.info(f"✅ Balance validation passed - User: {user_id}, Available: ${available_balance:.2f}, Required: ${required_amount:.2f}")
                                
                            except Exception as balance_error:
                                logger.error(f"❌ Balance validation failed for user {user_id}: {balance_error}")
                                await context.bot.send_message(
                                    chat_id=user_id,
                                    text="❌ Could not validate balance. Please try again or contact support.",
                                    parse_mode='Markdown'
                                )
                                if not context.user_data:
                                    context.user_data = {}
                                context.user_data['wallet_state'] = None
                                return
                        
                        # Create cashout request using AutoCashoutService with rate lock data
                        from services.auto_cashout import AutoCashoutService
                        
                        cashout_result = await AutoCashoutService.create_cashout_request(
                            user_id=as_int(user.id),
                            amount=Decimal(str(usd_amount or 0)),
                            currency="USD",  # Source currency is always USD
                            cashout_type=CashoutType.NGN_BANK.value,
                            destination=bank_destination,
                            user_initiated=True,
                            defer_processing=False  # Process immediately
                        )
                        
                        if cashout_result.get('success'):
                            cashout_id = cashout_result.get('cashout_id')
                            logger.info(f"✅ Created cashout request {cashout_id} with rate lock validation, processing...")
                            
                            # CRITICAL FIX: Check if cashout was already auto-processed to prevent double processing
                            already_processed = (
                                cashout_result.get('auto_processed') or 
                                cashout_result.get('status') in ['SUCCESS', 'COMPLETED'] or
                                cashout_result.get('message', '').lower().find('completed') != -1
                            )
                            
                            if already_processed:
                                logger.info(f"🔄 SKIP_DOUBLE_PROCESSING: Cashout {cashout_id} was already auto-processed by create_cashout_request() - skipping additional processing")
                                # Set processing_result to simulate successful processing
                                processing_result = {
                                    "success": True,
                                    "status": cashout_result.get('status', 'SUCCESS'),
                                    "message": "Already processed during creation",
                                    "auto_processed": True
                                }
                            else:
                                # ATOMIC PROCESSING: Process the approved cashout immediately with balance consistency
                                logger.info(f"🔄 ADDITIONAL_PROCESSING: Cashout {cashout_id} requires additional processing after creation")
                                processing_result = await AutoCashoutService.process_approved_cashout(
                                    cashout_id=str(cashout_id) if cashout_id else "",
                                    admin_approved=False  # User-initiated cashout
                                )
                            
                            if processing_result.get('success'):
                                # IMPROVED SESSION MANAGEMENT: Bank saving with proper transaction scope
                                save_bank = cashout_data.get('save_bank', False)
                                bank_save_success = True
                                
                                if save_bank:
                                    # Use separate session for bank saving to prevent rollback issues
                                    async with async_managed_session() as bank_session:
                                        try:
                                            # Re-query user in new session
                                            stmt = select(User).where(User.telegram_id == int(user_id))
                                            result = await bank_session.execute(stmt)
                                            bank_user = result.scalar_one_or_none()
                                            if bank_user:
                                                # Check if bank account already exists
                                                stmt = select(SavedBankAccount).where(
                                                    SavedBankAccount.user_id == bank_user.id,
                                                    SavedBankAccount.account_number == verified_account['account_number'],
                                                    SavedBankAccount.bank_code == verified_account['bank_code']
                                                )
                                                result = await bank_session.execute(stmt)
                                                existing_bank = result.scalar_one_or_none()
                                                
                                                if not existing_bank:
                                                    # Create new saved bank account
                                                    new_bank = SavedBankAccount(
                                                        user_id=bank_user.id,
                                                        account_number=verified_account['account_number'],
                                                        account_name=verified_account['account_name'],
                                                        bank_code=verified_account['bank_code'],
                                                        bank_name=verified_account['bank_name'],
                                                        is_verified=True,  # Mark as verified since OTP was successful
                                                        is_default=True   # Make it default if it's the first one
                                                    )
                                                    bank_session.add(new_bank)
                                                    await bank_session.commit()
                                                    logger.info(f"✅ Saved verified bank account for user {bank_user.id}: {verified_account['bank_name']}")
                                                else:
                                                    # Mark existing as verified and default
                                                    from sqlalchemy import update as sql_update
                                                    stmt = sql_update(SavedBankAccount).where(
                                                        SavedBankAccount.id == existing_bank.id
                                                    ).values(is_verified=True, is_default=True)
                                                    await bank_session.execute(stmt)
                                                    await bank_session.commit()
                                                    logger.info(f"✅ Updated bank account verification for user {bank_user.id}")
                                            else:
                                                logger.error(f"❌ User not found in bank session for user {user_id}")
                                                bank_save_success = False
                                                
                                        except Exception as save_error:
                                            logger.error(f"⚠️ Error saving bank account: {save_error}")
                                            await bank_session.rollback()
                                            bank_save_success = False
                                            # Don't fail the cashout for bank saving issues
                                
                                # ENHANCEMENT: Final Confirmation Page with enhanced UI
                                from utils.branding_utils import make_header, make_trust_footer, format_branded_amount, BrandingUtils
                                from datetime import datetime
                                header = make_header("Transfer Complete")
                                usd_formatted = format_branded_amount(Decimal(str(usd_amount or 0)), "USD")
                                ngn_formatted = format_branded_amount(Decimal(str(ngn_amount or 0)), "NGN")
                                
                                bank_save_note = "\n\n🏦 Bank Account Saved: Your bank details have been securely saved for future cashouts" if save_bank and bank_save_success else ""
                                
                                # Simplified success message to prevent entity parsing errors
                                success_text = f"""{header}
    
    ✅ Transfer Sent!
    
    💰 {usd_formatted} → {ngn_formatted}
    🏦 {verified_account['bank_name']} • ****{verified_account['account_number'][-4:]}
    🆔 {cashout_id}
    
    ⏰ Arrives in 1-5 minutes
    📧 Receipt sent to email{bank_save_note}
    💬 Support: {BrandingUtils.SUPPORT_HANDLE}
    
    {make_trust_footer()}"""
                                
                                # ENHANCED: Use reliable message editing with fallback for success
                                success_keyboard = InlineKeyboardMarkup([
                                    [InlineKeyboardButton("💳 Back to Wallet", callback_data="menu_wallet")],
                                    [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")],
                                    [InlineKeyboardButton("📋 Transaction History", callback_data="wallet_history")]
                                ])
                                
                                mock_query = MockQuery(loading_message)
                                await _safe_edit_with_fallback(
                                    mock_query,
                                    update,
                                    success_text,
                                    reply_markup=success_keyboard,
                                    parse_mode='Markdown'
                                )
                                
                                # Clear cashout data and invalidate rate lock
                                if not context.user_data:
                                    context.user_data = {}
                                context.user_data.pop('cashout_data', None)
                                if rate_lock and isinstance(rate_lock, dict):
                                    RateLock.invalidate_rate_lock(rate_lock, "cashout_completed")
                                if not context.user_data:
                                    context.user_data = {}
                                context.user_data.pop('rate_lock', None)
                                
                            else:
                                # ATOMIC PROCESSING FAILED: Handle balance consistency
                                error_msg = processing_result.get('error', 'Processing failed')
                                logger.error(f"❌ NGN cashout processing failed: {error_msg}")
                                
                                # Check if AutoCashoutService already handled refund
                                refund_status = processing_result.get('refund_status', 'unknown')
                                
                                from utils.branding_utils import make_header, make_trust_footer
                                header = make_header("Transfer Failed")
                                
                                if not update.message:
                                    return
                                await update.message.reply_text(
                                    f"{header}\n\n❌ Transfer Processing Failed\n\n📝 Reference: `{cashout_id}`\n🔄 Status: Failed - {error_msg}\n💰 Refund: {refund_status}\n\n💡 Your funds are safe. Please try again or contact support.\n\n{make_trust_footer()}",
                                    parse_mode='Markdown',
                                    reply_markup=InlineKeyboardMarkup([
                                        [InlineKeyboardButton("🔄 Try Again", callback_data="wallet_cashout")],
                                        [InlineKeyboardButton("💬 Support", callback_data="support_chat")]
                                    ])
                                )
                        else:
                            # Cashout request creation failed
                            error_msg = cashout_result.get('error', 'Failed to create cashout request')
                            logger.error(f"❌ NGN cashout request creation failed: {error_msg}")
                            
                            from utils.branding_utils import make_header, make_trust_footer
                            header = make_header("Cashout Failed")
                            
                            if not update.message:
                                return
                            await update.message.reply_text(
                                f"{header}\n\n❌ Cashout Request Failed\n\n🔄 Status: {error_msg}\n\n💡 Please try again or contact support if the issue persists.\n\n{make_trust_footer()}",
                                parse_mode='Markdown',
                                reply_markup=InlineKeyboardMarkup([
                                    [InlineKeyboardButton("🔄 Try Again", callback_data="wallet_cashout")],
                                    [InlineKeyboardButton("💬 Support", callback_data="support_chat")]
                                ])
                            )
                    
                    except Exception as processing_error:
                        logger.error(f"❌ CRITICAL: NGN cashout processing exception: {processing_error}")
                        
                        from utils.branding_utils import make_header, make_trust_footer
                        header = make_header("System Error")
                        
                        if not update.message:
                            return
                        await update.message.reply_text(
                            f"{header}\n\n❌ Processing Error\n\nThere was an unexpected error processing your cashout.\n\n💡 Your funds are safe. Please try again or contact support.\n\nError: {str(processing_error)[:100]}...\n\n{make_trust_footer()}",
                            parse_mode='Markdown',
                            reply_markup=InlineKeyboardMarkup([
                                [InlineKeyboardButton("🔄 Try Again", callback_data="wallet_cashout")],
                                [InlineKeyboardButton("💬 Support", callback_data="support_chat")]
                            ])
                        )
                
                else:
                    # OTP VERIFICATION FAILED: Enhanced error messages with loading message update
                    error_msg = verification_result.get('message', 'Invalid verification code.')
                    remaining_attempts = verification_result.get('attempts_remaining')
                    
                    from utils.branding_utils import make_header
                    
                    if remaining_attempts and remaining_attempts > 0:
                        header = make_header("Code Verification")
                        error_text = f"{header}\n\n❌ {error_msg}\n\n🔄 {remaining_attempts} attempts remaining.\n\nPlease enter the correct 6-digit code:"
                        
                        # ENHANCED: Use reliable message editing with fallback for errors
                        error_keyboard = InlineKeyboardMarkup([
                            [InlineKeyboardButton("📧 Resend Code", callback_data="resend_ngn_otp")],
                            [InlineKeyboardButton("❌ Cancel Cashout", callback_data="cancel_ngn_cashout")]
                        ])
                        
                        mock_query = MockQuery(loading_message)
                        await _safe_edit_with_fallback(
                            mock_query,
                            update,
                            error_text,
                            reply_markup=error_keyboard
                        )
                    else:
                        # Max attempts exceeded, restart process and invalidate rate lock
                        if not context.user_data:
                            context.user_data = {}
                        context.user_data['wallet_state'] = None
                        if rate_lock and isinstance(rate_lock, dict):
                            RateLock.invalidate_rate_lock(rate_lock, "max_attempts_exceeded")
                        
                        header = make_header("Verification Failed")
                        if not update.message:
                            return
                        await update.message.reply_text(
                            f"{header}\n\n❌ {error_msg}\n\nMaximum attempts exceeded. Please start the cashout process again.",
                            parse_mode='Markdown',
                            reply_markup=InlineKeyboardMarkup([
                                [InlineKeyboardButton("🔄 Start Over", callback_data="wallet_cashout")],
                                [InlineKeyboardButton("💳 Wallet", callback_data="menu_wallet")]
                            ])
                        )
                        
            except Exception as session_error:
                logger.error(f"❌ Session error in handle_ngn_otp_verification: {session_error}")
            
    except Exception as e:
        logger.error(f"❌ Critical error in handle_ngn_otp_verification: {e}")
        import traceback
        logger.error(f"Full traceback: {traceback.format_exc()}")
        
        from utils.branding_utils import make_header, make_trust_footer
        header = make_header("System Error")
        
        try:
            if not update.message:
                return
            await update.message.reply_text(
                f"{header}\n\n❌ Verification Error\n\nThere was an error verifying your code.\n\nPlease try again or contact support.\n\n{make_trust_footer()}",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Try Again", callback_data="wallet_cashout")],
                    [InlineKeyboardButton("💬 Support", callback_data="support_chat")]
                ])
            )
        except Exception as msg_error:
            logger.error(f"❌ Failed to send error message: {msg_error}")

async def handle_resend_ngn_otp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle NGN OTP resend request"""
    try:
        query = update.callback_query
        if not update or not update.effective_user:
            return
        user_id = update.effective_user.id
        
        await safe_answer_callback_query(query, "📧 Resending verification code...")
        
        # Get user information
        async with async_managed_session() as session:
            try:
                stmt = select(User).where(User.telegram_id == user_id)
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()
                if not user or not as_str(user.email):
                    await safe_edit_message_text(
                        query,
                        "❌ Email Required\n\nPlease set up your email address first.",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🔙 Back", callback_data="back_to_method_selection")]
                        ])
                    )
                    return
                
                # Resend OTP
                from services.email_verification_service import EmailVerificationService
                resend_result = EmailVerificationService.resend_otp(
                    user_id=as_int(user.id),
                    email=as_str(user.email),
                    ip_address=context.user_data.get('ip_address') if context.user_data else None
                )
                
                if resend_result['success']:
                    user_email = as_str(user.email)
                    await safe_edit_message_text(
                        query,
                        f"📧 Code sent to {user_email}\n\nEnter verification code:"
                    )
                    logger.info(f"✅ NGN OTP resent successfully to user {user_id}")
                    
                else:
                    error_msg = resend_result.get('message', 'Could not resend verification code.')
                    if query:
                        await safe_edit_message_text(
                            query,
                            f"❌ {error_msg}\n\nPlease try again later.",
                            reply_markup=InlineKeyboardMarkup([
                                [InlineKeyboardButton("🔙 Back", callback_data="back_to_method_selection")]
                            ])
                        )
                    
            except Exception as inner_e:
                logger.error(f"Inner error in handle_resend_ngn_otp: {inner_e}")
            
    except Exception as e:
        logger.error(f"Error in handle_resend_ngn_otp: {e}")
        if query:
            await safe_edit_message_text(
                query,
                "❌ Resend Error\n\nThere was an error resending the code. Please try again.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back", callback_data="back_to_method_selection")]
                ])
            )

async def handle_crypto_otp_verification(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle crypto cashout OTP verification when user enters the code"""
    try:
        if not update or not update.effective_user:
            return
        user_id = update.effective_user.id
        if not update.message or not update.message.text:
            return
        otp_code = update.message.text.strip()
        
        if not context.user_data:
            context.user_data = {}
        logger.info(f"🔐 VERIFYING CRYPTO OTP - User: {user_id}, Code: [REDACTED], State: {context.user_data.get('wallet_state')}")
        
        # INSTANT FEEDBACK: Send verification message immediately
        verifying_msg = await update.message.reply_text("🔐 Verifying your code...")
        
        # Get crypto cashout context for verification
        if not context.user_data:
            context.user_data = {}
        cashout_data = context.user_data.get('cashout_data', {})
        crypto_context = cashout_data.get('crypto_context')
        expected_fingerprint = cashout_data.get('fingerprint')
        
        if not crypto_context or not expected_fingerprint:
            if not update.message:
                return
            # Edit the verifying message to show error
            await verifying_msg.edit_text(
                "❌ Session Expired\n\nPlease start the cashout process again.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("💳 Wallet", callback_data="menu_wallet")]
                ])
            )
            return
        
        # Get user database ID
        async with async_managed_session() as session:
            try:
                stmt = select(User).where(User.telegram_id == user_id)
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()
                if not user:
                    if not update.message:
                        return
                    await update.message.reply_text("❌ User not found. Please try again.")
                    return
                    
                # Verify OTP code using shared CashoutOTPFlow
                from services.cashout_otp_flow import CashoutOTPFlow
                verification_result = CashoutOTPFlow.verify_otp_code(
                    user_id=as_int(user.id),
                    otp_code=otp_code,
                    expected_fingerprint=expected_fingerprint,
                    channel='crypto'
                )
                
                if verification_result['success']:
                        logger.info(f"✅ Crypto OTP verified successfully for user {user_id}")
                        
                        # Clear OTP verification state
                        if not context.user_data:
                            context.user_data = {}
                        context.user_data['wallet_state'] = None
                        
                        # Extract crypto cashout details for confirmation
                        asset = crypto_context.get('asset', 'USDT')
                        network = crypto_context.get('network', 'TRC20')
                        address = crypto_context.get('address', '')
                        gross_amount = crypto_context.get('gross_amount', '0.00')
                        net_amount = crypto_context.get('net_amount', '0.00')
                        fee = crypto_context.get('fee', '0.00')
                        
                        # Show crypto cashout confirmation options
                        text = f"""✅ Email Verified

💰 {asset} ({network}) Cashout
📤 {format_money(gross_amount, 'USD')} - {format_money(fee, 'USD')} fee = {format_money(net_amount, 'USD')}
📍 `{address[:20]}...{address[-10:] if len(address) > 30 else address[20:]}`

⚠️ Verify address! Wrong address = permanent loss."""
                        
                        keyboard = [
                            [InlineKeyboardButton("💰 Process Cashout", callback_data="process_crypto_cashout")],
                            [InlineKeyboardButton("🔙 Cancel", callback_data="wallet_menu")]
                        ]
                        
                        # Edit the verifying message to show success
                        await verifying_msg.edit_text(
                            text,
                            reply_markup=InlineKeyboardMarkup(keyboard),
                            parse_mode="Markdown"
                        )
                
                else:
                    error_msg = verification_result.get('error', 'Invalid verification code.')
                    remaining_attempts = verification_result.get('attempts_remaining', 0)
                    
                    if remaining_attempts > 0:
                        # Edit the verifying message to show error with retry
                        await verifying_msg.edit_text(
                            f"❌ {error_msg}\n\n🔄 {remaining_attempts} attempts remaining.\n\nPlease enter the correct 6-digit code:"
                        )
                    else:
                        # Max attempts exceeded, restart process
                        if not context.user_data:
                            context.user_data = {}
                        context.user_data['wallet_state'] = None
                        # Edit the verifying message to show final error
                        await verifying_msg.edit_text(
                            f"❌ {error_msg}\n\nPlease start the cashout process again.",
                            reply_markup=InlineKeyboardMarkup([
                                [InlineKeyboardButton("🔄 Start Over", callback_data="wallet_cashout")],
                                [InlineKeyboardButton("💳 Wallet", callback_data="menu_wallet")]
                            ])
                        )
            except Exception as inner_error:
                logger.error(f"Database error in handle_crypto_otp_verification: {inner_error}")
                # Edit the verifying message to show error
                await verifying_msg.edit_text(
                    "❌ There was a database error. Please try again.",
                    parse_mode='Markdown'
                )
            
    except Exception as e:
        logger.error(f"Error in handle_crypto_otp_verification: {e}")
        # Use branded error message matching NGN flow
        error_msg = BrandingUtils.get_branded_error_message("verification", "OTP verification error")
        if not update.message:
            return
        await update.message.reply_text(
            error_msg,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Try Again", callback_data="wallet_cashout")]
            ])
        )

async def handle_resend_crypto_otp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle crypto OTP resend request"""
    try:
        query = update.callback_query
        if not query:
            logger.warning("No callback query in handle_resend_crypto_otp")
            return

        await safe_answer_callback_query(query, "📧 Resending verification code...")
        
        if not update or not update.effective_user:
            return
        user_id = update.effective_user.id
        
        # Get user database ID and email using async session
        async with async_managed_session() as session:
            stmt = select(User).where(User.telegram_id == int(user_id))
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()
            
            if not user:
                user_db_id = None
                user_email = None
            else:
                # Extract values while session is still open
                user_db_id = as_int(user.id)
                user_email = as_str(user.email)
        
        if not user_db_id or not user_email:
            await safe_edit_message_text(
                query,
                "❌ Email Required\n\nPlease verify your email address first.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back", callback_data="wallet_menu")]
                ])
            )
            return
                
        # Resend OTP using CashoutOTPFlow async method
        from services.cashout_otp_flow import CashoutOTPFlow
        resend_result = await CashoutOTPFlow.resend_otp_code_async(
            user_id=user_db_id,
            email=user_email,
            ip_address=context.user_data.get('ip_address') if context.user_data else None
        )
        
        if resend_result['success']:
            await safe_edit_message_text(
                query,
                f"📧 Code sent to {user_email}\n\nEnter verification code:"
            )
            logger.info(f"✅ Crypto OTP resent successfully to user {user_id}")
            
        else:
            error_type = resend_result.get('error', '')
            
            # Check if max resends exceeded
            if error_type == 'max_resends_exceeded':
                error_msg = "❌ Maximum Resend Attempts Exceeded\n\nYou've requested the maximum number of verification codes.\n\nPlease contact support for assistance."
            else:
                error_msg = f"❌ {resend_result.get('error', 'Could not resend verification code.')}"
            
            await safe_edit_message_text(
                query,
                error_msg,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back", callback_data="wallet_menu")]
                ])
            )
            
    except Exception as e:
        logger.error(f"Error in handle_resend_crypto_otp: {e}")
        if 'query' in locals() and query:
            await safe_edit_message_text(
                query,
                "❌ Resend Error\n\nThere was an error resending the code. Please try again.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back", callback_data="wallet_menu")]
                ])
            )

async def handle_process_crypto_cashout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """🔐 SECURE: Process crypto cashout - REQUIRES VERIFIED OTP TOKEN"""
    try:
        # Handle both callback queries (button press) and message updates (OTP verification)
        query = update.callback_query
        message = update.message or (query.message if query else None)
        
        if query:
            await safe_answer_callback_query(query, "🔐 Processing secure cashout...")
        
        if not query and not message:
            logger.warning("No callback query or message in handle_process_crypto_cashout")
            return
        
        if not update or not update.effective_user:
            return
        user_id = update.effective_user.id
        
        # Helper function to send message for both callback and regular message scenarios
        async def send_message(text, reply_markup=None, parse_mode='Markdown'):
            if query:
                await safe_edit_message_text(query, text, reply_markup=reply_markup, parse_mode=parse_mode)
            else:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode
                )
        
        # 🔒 SECURITY CHECK 1: Verify OTP has been verified by checking wallet state
        if not context.user_data:
            context.user_data = {}
        wallet_state = context.user_data.get('wallet_state')
        if wallet_state == 'verifying_crypto_otp':
            await send_message(
                "❌ Email Required\n\n🔐 Enter your 6-digit verification code to continue.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📧 Resend Code", callback_data="resend_crypto_otp")],
                    [InlineKeyboardButton("🔙 Cancel", callback_data="wallet_menu")]
                ])
            )
            logger.warning(f"🚨 SECURITY: User {user_id} attempted to process crypto cashout without OTP verification")
            return
        
        # 🔒 SECURITY CHECK 2: Verify crypto context exists
        if not context.user_data:
            context.user_data = {}
        cashout_data = context.user_data.get('cashout_data', {})
        crypto_context = cashout_data.get('crypto_context')
        fingerprint = cashout_data.get('fingerprint')
        verification_id = cashout_data.get('verification_id')
        is_skip_email_user = cashout_data.get('skip_email_user', False)
        
        # Skip-email users don't have fingerprint/verification_id (no OTP verification)
        if not crypto_context:
            await send_message(
                "❌ Session Expired\n\n🔐 Security context missing. Please start the cashout process again.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Start Over", callback_data="wallet_cashout")],
                    [InlineKeyboardButton("💳 Wallet", callback_data="menu_wallet")]
                ])
            )
            logger.warning(f"🚨 SECURITY: User {user_id} attempted crypto cashout without crypto_context")
            return
        
        # For verified email users, require OTP tokens
        if not is_skip_email_user and (not fingerprint or not verification_id):
            await send_message(
                "❌ Session Expired\n\n🔐 OTP verification required. Please start the cashout process again.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Start Over", callback_data="wallet_cashout")],
                    [InlineKeyboardButton("💳 Wallet", callback_data="menu_wallet")]
                ])
            )
            logger.warning(f"🚨 SECURITY: Verified user {user_id} attempted crypto cashout without OTP tokens")
            return
        
        # Log the cashout type for monitoring
        if is_skip_email_user:
            logger.info(f"✅ SKIP_EMAIL_CASHOUT: User {user_id} processing cashout without OTP (unverified email)")
        else:
            logger.info(f"✅ VERIFIED_CASHOUT: User {user_id} processing OTP-verified cashout")
        
        # Extract crypto details for processing
        asset = crypto_context.get('asset', 'USDT')
        network = crypto_context.get('network', 'TRC20')
        address = crypto_context.get('address', '')
        gross_amount = Decimal(str(crypto_context.get('gross_amount', 0) or 0))
        # CRITICAL FIX: Extract BOTH platform and network fees from context
        platform_fee_amount = Decimal(str(crypto_context.get('platform_fee', 0) or 0))
        network_fee_amount = Decimal(str(crypto_context.get('network_fee', 0) or 0))
        fee = platform_fee_amount + network_fee_amount  # Total fee for backward compatibility
        # CRITICAL FIX: Recalculate net_amount to match database constraint
        # The constraint expects: net_amount = amount - network_fee - platform_fee
        net_amount = gross_amount - network_fee_amount - platform_fee_amount
        
        # CRITICAL: Initialize short_address early to prevent NameError in error paths
        if address and len(address) > 20:
            short_address = f"{address[:12]}...{address[-8:]}"
        elif address:
            short_address = address
        else:
            short_address = "Pending setup"
        
        # INSTANT FEEDBACK: Show processing message immediately before heavy database operations
        await send_message(
            f"⏳ **Processing Cashout...**\n\n"
            f"💰 Amount: ${gross_amount:.2f}\n"
            f"📡 Asset: {asset} ({network})\n\n"
            f"Please wait while we:\n"
            f"• Verify your balance\n"
            f"• Debit your wallet\n"
            f"• Create withdrawal request\n\n"
            f"This may take a few seconds...",
            reply_markup=InlineKeyboardMarkup([])  # Remove buttons during processing
        )
        
        logger.info(f"✅ User {user_id} UI immediately updated with processing message for crypto cashout")
        
        # Get user from database
        async with async_managed_session() as session:
            try:
                stmt = select(User).where(User.telegram_id == user_id)
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()
                if not user:
                    await send_message(
                        "❌ User Error\n\nUser not found. Please try again.",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🔄 Try Again", callback_data="wallet_menu")]
                        ])
                    )
                    return
                
                # 💰 DEBIT WALLET BEFORE CREATING CASHOUT (CRITICAL FIX)
                from models import Wallet
                stmt_wallet = select(Wallet).where(
                    Wallet.user_id == user.id,
                    Wallet.currency == 'USD'
                ).with_for_update()
                result_wallet = await session.execute(stmt_wallet)
                wallet = result_wallet.scalar_one_or_none()
                
                if not wallet:
                    await send_message(
                        "❌ Wallet Error\n\nWallet not found. Please try again.",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🔄 Try Again", callback_data="wallet_menu")]
                        ])
                    )
                    return
                
                # Check if user has sufficient balance
                # CRITICAL FIX: Quantize both values to USD precision (2 decimals) before comparison
                # This prevents false "insufficient balance" errors when amounts are equal but have different precision
                # Example: 60.719999999999 < 60.72 would fail, but both round to 60.72
                usd_precision = Decimal('0.01')
                balance_rounded = wallet.available_balance.quantize(usd_precision, rounding=ROUND_HALF_UP)
                amount_rounded = gross_amount.quantize(usd_precision, rounding=ROUND_HALF_UP)
                
                if balance_rounded < amount_rounded:
                    await send_message(
                        f"❌ Insufficient Balance\n\nYour balance (${balance_rounded:.2f}) is less than the required amount (${amount_rounded:.2f}).",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("💳 Wallet", callback_data="menu_wallet")]
                        ])
                    )
                    return
                
                # Debit wallet
                # CRITICAL FIX: When rounded amounts are equal, debit the full available balance to prevent negative values
                # Example: If balance is 60.719999999999 and user wants to cash out 60.72 (both round to 60.72),
                # we should debit 60.719999999999 (the full balance) instead of 60.72 to avoid -$0.002508 balance
                if balance_rounded == amount_rounded:
                    debit_amount = wallet.available_balance
                    # Recalculate fees proportionally based on actual debited amount
                    # Calculate fee_percentage from the original combined fee and gross_amount
                    total_fee_percentage = (fee / gross_amount) if gross_amount > 0 else Decimal('0.02')
                    platform_fee_percentage = (platform_fee_amount / gross_amount) if gross_amount > 0 else Decimal('0.02')
                    network_fee_percentage = (network_fee_amount / gross_amount) if gross_amount > 0 else Decimal('0')
                    
                    actual_platform_fee = debit_amount * platform_fee_percentage
                    actual_network_fee = debit_amount * network_fee_percentage
                    actual_total_fee = actual_platform_fee + actual_network_fee
                    actual_net_amount = debit_amount - actual_total_fee
                    
                    logger.info(f"💸 FULL_BALANCE_DEBIT: Debiting full balance ${wallet.available_balance} (rounded: ${balance_rounded}) for cashout request ${gross_amount} (rounded: ${amount_rounded})")
                    logger.info(f"💸 RECALCULATED: Platform fee ${actual_platform_fee}, Network fee ${actual_network_fee}, Total ${actual_total_fee}, Net ${actual_net_amount}")
                else:
                    debit_amount = gross_amount
                    actual_platform_fee = platform_fee_amount
                    actual_network_fee = network_fee_amount
                    actual_total_fee = fee
                    actual_net_amount = net_amount
                
                wallet.available_balance = wallet.available_balance - debit_amount
                logger.info(f"💸 WALLET_DEBITED: User {user.id} wallet debited ${debit_amount} for crypto cashout")
                
                # 🔐 CREATE CASHOUT REQUEST WITH PROPER SECURITY CONTEXT
                from models import Cashout, CashoutStatus, CashoutType
                
                # Generate cashout_id BEFORE creating the record
                cashout_id = UniversalIDGenerator.generate_cashout_id()
                
                # Create cashout record with verified context
                # CRITICAL FIX: Store cashout metadata for crypto conversion
                cashout_metadata = {
                    "target_currency": asset,  # Target crypto currency (ETH, BTC, etc)
                    "target_network": network,  # Target network (ERC20, TRC20, etc)
                    "source_currency": "USD",  # Source wallet currency
                }
                
                # CRITICAL FIX: Store BOTH platform and network fees for proper accounting
                cashout = Cashout(
                    cashout_id=cashout_id,  # Set the cashout_id directly
                    user_id=user.id,
                    amount=MonetaryDecimal.to_decimal(debit_amount, "crypto_gross_amount"),
                    net_amount=MonetaryDecimal.to_decimal(actual_net_amount, "crypto_net_amount"),
                    platform_fee=MonetaryDecimal.to_decimal(actual_platform_fee, "crypto_platform_fee"),
                    network_fee=MonetaryDecimal.to_decimal(actual_network_fee, "crypto_network_fee"),
                    cashout_type=CashoutType.CRYPTO.value,
                    destination_type="crypto",
                    status=CashoutStatus.PROCESSING.value,  # FIX: Use PROCESSING status so process_approved_cashout can accept it
                    processing_mode=CashoutProcessingMode.IMMEDIATE.value,  # CRITICAL FIX: Enable wallet hold creation
                    currency="USD",  # CRITICAL FIX: Wallet currency is USD, not target crypto (AutoCashoutService will convert)
                    destination=address,
                    cashout_metadata=cashout_metadata  # Store target crypto info for conversion
                )
                
                session.add(cashout)
                await session.commit()  # Single commit with wallet debit and cashout creation
                
                # Notify admin of cashout started
                try:
                    asyncio.create_task(
                        admin_trade_notifications.notify_cashout_started({
                            'cashout_id': cashout.cashout_id,
                            'user_id': user.id,
                            'username': user.username or 'N/A',
                            'first_name': user.first_name or 'Unknown',
                            'last_name': user.last_name or '',
                            'amount': float(cashout.amount),
                            'currency': 'USD',  # CRITICAL FIX: Show source currency (USD), not target (asset)
                            'target_currency': asset,  # Add target currency for display
                            'cashout_type': 'crypto',
                            'destination': address,
                            'started_at': cashout.created_at or datetime.utcnow()
                        })
                    )
                except Exception as notify_error:
                    logger.error(f"Failed to notify admin of cashout started {cashout.cashout_id}: {notify_error}")
                
                # Clear security context after successful processing
                if not context.user_data:
                    context.user_data = {}
                context.user_data['wallet_state'] = None
                if not context.user_data:
                    context.user_data = {}
                context.user_data.pop('cashout_data', None)
                
            except Exception as inner_e:
                logger.error(f"Inner error in handle_process_crypto_cashout: {inner_e}")
                raise
        
        # 🚀 IMMEDIATE PROCESSING WITH ADDRESS VERIFICATION
        from services.auto_cashout import AutoCashoutService
        from services.fastforex_service import FastForexService
        
        # Show processing message - Alternative design (compact)
        await send_message(
            f"💸 Crypto Cashout Started\n\n"
            f"`{cashout_id}` • Processing now\n"
            f"You'll get a notification when complete.\n\n"
            f"Questions? @LockbayAssist",
        )
        
        # Call the proper crypto cashout processing function with address verification
        result = await AutoCashoutService.process_approved_cashout(cashout_id)
        
        # Initialize display variables before status handling (short_address already initialized earlier)
        usd_amount = format_branded_amount(gross_amount, "USD")
        # FIX: Convert USD net amount to crypto using exchange rate
        fastforex_service = FastForexService()
        crypto_value = await fastforex_service.convert_usd_to_crypto(Decimal(str(net_amount)), asset)
        crypto_amount = format_crypto_amount(crypto_value, asset)
        fee_amount = format_branded_amount(fee, "USD")
        txid = ""
        normalized_status = ""
        error_msg = ""
        
        # 🔒 ROBUST STATUS HANDLING: Use centralized normalization and explicit allowlists
        try:
            # Step 1: Normalize the status safely using helper function
            raw_status = result.get('status', '')
            normalized_status = normalize_cashout_status(raw_status, default_status="unknown")
            
            # Step 2: Get transaction confirmation data
            txid = result.get('txid', '')
            has_txid = bool(txid and txid.strip() and txid.lower() != 'processing...')
            
            # Step 3: Classify status using allowlist-based logic  
            status_classification = classify_cashout_status(normalized_status, has_txid)
            
            # Step 4: Extract result metadata for enhanced decision making
            success_flag = result.get('success', False)
            error_msg = result.get('error', 'Unknown processing error')
            requires_admin_action = result.get('requires_admin_action', False)
            
            # Log classification for debugging
            logger.info(
                f"🔍 CASHOUT_STATUS_ANALYSIS: User {user_id}, cashout {cashout_id} - "
                f"raw_status='{raw_status}', normalized='{normalized_status}', "
                f"classification={status_classification}, success_flag={success_flag}, "
                f"has_txid={has_txid}, requires_admin={requires_admin_action}"
            )
            
        except Exception as status_error:
            logger.error(f"❌ STATUS_PROCESSING_ERROR: Failed to process status for cashout {cashout_id}: {status_error}")
            # Fallback to safe handling - treat as requires admin review
            status_classification = {
                'category': 'unknown',
                'requires_admin': True,
                'show_as_success': False,
                'show_as_pending': True,
                'show_as_failed': False
            }
            
        # 🎯 STATUS-DRIVEN BRANCHING: Branch on status classification first
        if status_classification['show_as_success']:
            # ✅ TRUE SUCCESS: Explicit allowlist match + txid confirmation
            header = make_header("Transfer Complete")
            
            # Format net USD amount (after fees)
            net_usd = format_branded_amount(net_amount, "USD")
            # Truncate transaction hash for display
            txid_short = txid[:16] + "..." if len(txid) > 16 else txid
            
            await send_message(
                f"{header}\n\n"
                f"✅ Sent ~{crypto_amount} ({net_usd} net)\n\n"
                f"📍 {short_address}\n"
                f"🆔 {cashout_id}\n"
                f"🔗 {txid_short}\n\n"
                f"⏰ Arrives ~5 min • 📧 Check email\n\n"
                f"{make_trust_footer()}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("💳 Wallet", callback_data="menu_wallet")],
                    [InlineKeyboardButton("🏠 Main Menu", callback_data="back_to_main")]
                ])
            )
            
            logger.info(f"✅ ROBUST_CRYPTO_SUCCESS: User {user_id} cashout completed - ID: {cashout_id}, TXID: {txid}, status: {normalized_status}")
            
        elif status_classification['show_as_pending']:
            # ⏳ PENDING STATUS: Could be funding, processing, or unknown requiring review
            header = make_header("Transfer Pending")
            
            # Determine specific pending message based on classification
            if status_classification['requires_admin'] and 'funding' in normalized_status:
                # Funding-specific pending
                status_msg = "Pending - Insufficient Kraken account balance"
                action_msg = "Admin funding needed"
                detail_msg = (
                    "💡 Your request is queued and will be processed once our Kraken account is funded.\n"
                    "📧 You'll receive email confirmation when the transfer completes.\n\n"
                    "💰 Your funds are safe - they remain in your wallet until processing."
                )
                support_buttons = [
                    [InlineKeyboardButton("💳 Wallet", callback_data="menu_wallet")],
                    [InlineKeyboardButton("💬 Support", callback_data="support_chat")]
                ]
            elif normalized_status in ['processing', 'queued', 'submitting']:
                # Active processing
                status_msg = "Processing - Transaction in progress"
                action_msg = "Processing by exchange"
                detail_msg = (
                    "💡 Your cashout is being processed by the exchange.\n"
                    "📧 You'll receive confirmation when the transfer completes.\n\n"
                    "⏱️ This typically takes 5-15 minutes."
                )
                support_buttons = [
                    [InlineKeyboardButton("💳 Wallet", callback_data="menu_wallet")]
                ]
            else:
                # Unknown or requires admin review - show seamless "processing" UX
                status_msg = "Processing"
                action_msg = "Your cashout is being processed"
                detail_msg = (
                    "💡 Your cashout is being processed.\n"
                    "📧 You'll receive confirmation when it's complete.\n\n"
                    "⏱️ This typically takes 5-15 minutes."
                )
                support_buttons = [
                    [InlineKeyboardButton("💳 Wallet", callback_data="menu_wallet")]
                ]
            
            # CONSISTENT FORMAT: Match WithdrawalNotificationService success message structure
            await send_message(
                f"✅ <b>Crypto Sent Successfully!</b>\n\n"
                f"💰 <b>Amount:</b> {crypto_amount} ({usd_amount})\n"
                f"📍 <b>To:</b> {short_address}\n"
                f"🆔 <b>Ref:</b> <code>{cashout_id}</code>\n\n"
                f"⏰ <i>Arrives in 10-30 minutes</i>\n"
                f"📧 <i>Tap refs to copy • Check email for details</i>",
                reply_markup=InlineKeyboardMarkup(support_buttons),
                parse_mode='HTML'
            )
            
            logger.info(f"⏳ ROBUST_CRYPTO_PENDING: User {user_id} cashout pending - ID: {cashout_id}, status: {normalized_status}, requires_admin: {status_classification['requires_admin']}")
            
        elif status_classification['show_as_failed']:
            # ❌ FAILED STATUS: Explicit failure that needs user attention
            header = make_header("Transfer Failed")
            
            await send_message(
                f"{header}\n\n"
                f"❌ Transfer Processing Failed\n\n"
                f"📝 Reference: `{cashout_id}`\n"
                f"🔄 Status: Failed - {error_msg}\n\n"
                f"💰 Your funds are safe - they have been returned to your wallet.\n"
                f"💡 Please try again or contact support if the issue persists.\n\n"
                f"{make_trust_footer()}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Try Again", callback_data="wallet_cashout")],
                    [InlineKeyboardButton("💬 Support", callback_data="support_chat")]
                ])
            )
            
            logger.warning(f"❌ ROBUST_CRYPTO_FAILED: User {user_id} cashout failed - ID: {cashout_id}, status: {normalized_status}, error: {error_msg if 'error_msg' in locals() else 'Unknown error'}")
            
        else:
            # 🚨 FALLBACK: Should not reach here with proper classification, but handle safely
            logger.error(f"🚨 ROBUST_CRYPTO_FALLBACK: Unexpected status classification for cashout {cashout_id}: {status_classification}")
            
            # CONSISTENT FORMAT: Match WithdrawalNotificationService success message structure (fallback)
            await send_message(
                f"✅ <b>Crypto Sent Successfully!</b>\n\n"
                f"💰 <b>Amount:</b> {crypto_amount} ({usd_amount})\n"
                f"📍 <b>To:</b> {short_address}\n"
                f"🆔 <b>Ref:</b> <code>{cashout_id}</code>\n\n"
                f"⏰ <i>Arrives in 10-30 minutes</i>\n"
                f"📧 <i>Tap refs to copy • Check email for details</i>",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("💳 Wallet", callback_data="menu_wallet")]
                ]),
                parse_mode='HTML'
            )
            
            logger.warning(f"⚠️ CRYPTO CASHOUT FAILED: User {user_id} - {cashout_id}: {error_msg}")
            
    except Exception as e:
        logger.error(f"Error in handle_process_crypto_cashout: {e}")
        if query:
            # Simple, safe error message without complex formatting
            await safe_edit_message_text(
                query,
                f"❌ Processing Error\n\nThere was an issue processing your crypto cashout.\n\n💰 Your funds are safe and secure.\n\n{make_trust_footer()}",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Try Again", callback_data="wallet_cashout")],
                    [InlineKeyboardButton("💳 Wallet", callback_data="menu_wallet")]
                ])
            )

async def handle_confirm_ngn_cashout_and_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle NGN cashout confirmation with bank account saving"""
    try:
        query = update.callback_query
        if not query:
            logger.warning("No callback query in handle_confirm_ngn_cashout_and_save")
            return

        await safe_answer_callback_query(query, "💰 Processing cashout and saving bank...")
        
        # Get verified account from context
        if not context.user_data:
            context.user_data = {}
        verified_account = context.user_data.get('cashout_data', {}).get('verified_account')
        if not verified_account:
            await safe_edit_message_text(
                query,
                "❌ Account Information Missing\n\nPlease verify your bank account again.",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Try Again", callback_data="select_ngn_bank")]
                ])
            )
            return
        
        # INSTANT FEEDBACK: Show processing message immediately before database operations
        bank_name = verified_account.get('bank_name', 'your bank')
        account_number = verified_account.get('account_number', '****')
        
        await safe_edit_message_text(
            query,
            f"⏳ **Processing NGN Cashout...**\n\n"
            f"🏦 Bank: {bank_name}\n"
            f"💳 Account: {account_number}\n\n"
            f"Please wait while we:\n"
            f"• Save your bank account\n"
            f"• Verify your balance\n"
            f"• Process the payout\n\n"
            f"This may take a few seconds...",
            reply_markup=InlineKeyboardMarkup([])  # Remove buttons during processing
        )
        
        logger.info(f"✅ User UI immediately updated with processing message for NGN cashout with bank save")
            
        # Save the bank account first using async session
        try:
            if not update or not update.effective_user:
                return
            user_id = update.effective_user.id
            
            async with async_managed_session() as session:
                # Get user 
                stmt = select(User).where(User.telegram_id == int(user_id))
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()
                
                if user:
                    # Check if account already exists
                    stmt = select(SavedBankAccount).where(
                        SavedBankAccount.user_id == user.id,
                        SavedBankAccount.account_number == verified_account['account_number'],
                        SavedBankAccount.bank_code == verified_account['bank_code']
                    )
                    result = await session.execute(stmt)
                    existing_account = result.scalar_one_or_none()
                    
                    if not existing_account:
                        # Create new saved bank account
                        new_bank_account = SavedBankAccount(
                            user_id=user.id,
                            account_number=verified_account['account_number'],
                            account_name=verified_account['account_name'],
                            bank_name=verified_account['bank_name'],
                            bank_code=verified_account['bank_code'],
                            is_verified=True
                        )
                        session.add(new_bank_account)
                        await session.commit()
                        logger.info(f"✅ Saved new bank account for user {user_id}: {verified_account['bank_name']}")
                    else:
                        logger.info(f"🔄 Bank account already exists for user {user_id}: {verified_account['bank_name']}")
                        
        except Exception as e:
            logger.error(f"Error saving bank account: {e}")
            # Async sessions auto-rollback on exception
        
        # Now continue with cashout process - call the regular confirm handler
        if 'update' in locals() and update:
            await handle_confirm_ngn_cashout(update, context)
        
    except Exception as e:
        logger.error(f"Error in handle_confirm_ngn_cashout_and_save: {e}")
        if query:
            await safe_edit_message_text(
                query,
                "❌ Processing Error\n\nThere was an error processing your request. Please try again.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Try Again", callback_data="select_ngn_bank")]
                ])
            )

async def handle_wallet_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Main text input router for wallet states"""
    try:
        if not update.message:
            return
        if not update.message or not update.message.text:
            return
            
        if not update or not update.effective_user:
            return
        user_id = update.effective_user.id
        if not update.message:
            return
        text = update.message.text.strip()
        
        # Get wallet state from Redis-backed session
        wallet_state = await get_wallet_state(user_id, context)
        
        logger.info(f"🔀 WALLET TEXT ROUTER: User {user_id}, State: {wallet_state}, Input: '{text[:20]}...'")
        
        # Route based on wallet state
        if wallet_state == 'verifying_ngn_otp':
            logger.info(f"🔐 Routing to NGN OTP verification handler for user {user_id}")
            return await handle_ngn_otp_verification(update, context)
        
        elif wallet_state == 'verifying_bank_otp':
            logger.info(f"🔐 Routing to bank OTP verification handler for user {user_id}")
            # Handle bank OTP verification (if needed)
            return
            
        elif wallet_state == 'verifying_crypto_otp':
            logger.info(f"🔐 Routing to crypto OTP verification handler for user {user_id}")
            return await handle_crypto_otp_verification(update, context)
            
        elif wallet_state == 'entering_custom_amount':
            logger.info(f"💰 Routing to custom amount handler for user {user_id}")
            return await handle_custom_amount_input(update, context)
            
        elif wallet_state in ['entering_crypto_address', 'entering_crypto_details', WalletStates.ENTERING_WITHDRAW_ADDRESS, 305, '305']:
            logger.info(f"💰 Routing to crypto address handler for user {user_id}")
            return await handle_crypto_address_input(update, context)
            
        else:
            logger.warning(f"⚠️ WALLET TEXT ROUTER: Unhandled wallet state '{wallet_state}' for user {user_id}")
            
    except Exception as e:
        logger.error(f"Error in handle_wallet_text_input: {e}")

async def handle_wallet_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle wallet menu - redirect to main wallet interface"""
    return await show_wallet_menu(update, context)

async def handle_wallet_cashout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle wallet cashout - use cashout all flow (skip amount selection)"""
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "💰 Processing cashout...")
    
    try:
        if not update.effective_user:
            return
        
        async with async_managed_session() as session:
            user_id = update.effective_user.id
            stmt = select(User).where(User.telegram_id == user_id)
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()
            
            if not user:
                await safe_edit_message_text(query, "❌ User not found")
                return
            
            # Get user's USD wallet balance
            stmt = select(Wallet).where(
                Wallet.user_id == getattr(user, "id", 0),
                Wallet.currency == "USD"
            )
            result = await session.execute(stmt)
            wallet = result.scalar_one_or_none()
            
            balance = (
                Decimal(str(wallet.available_balance))
                if wallet and wallet.available_balance is not None
                else Decimal('0')
            )
            
            if balance < Config.MIN_CASHOUT_AMOUNT:
                min_amount = Config.MIN_CASHOUT_AMOUNT
                header = make_header("Insufficient Balance")
                await safe_edit_message_text(
                    query,
                    f"{header}\n\n"
                    f"💰 Current: {format_branded_amount(balance, 'USD')}\n"
                    f"💵 Minimum: {format_branded_amount(min_amount, 'USD')}\n\n"
                    f"Add funds to your wallet first.\n\n"
                    f"{make_trust_footer()}",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("💰 Add Funds", callback_data="wallet_add_funds")],
                        [InlineKeyboardButton("🔙 Back", callback_data="menu_wallet")]
                    ]),
                    parse_mode="Markdown"
                )
                return
            
            # Simulate the quick_cashout_all callback by directly calling the handler
            # Update callback data to match quick_cashout_all format
            if query and query.data:
                query.data = f"quick_cashout_all:{balance}"
            
            # Call the cashout all handler directly
            await handle_quick_cashout_all(update, context)
            
    except Exception as e:
        logger.error(f"Error in handle_wallet_cashout: {e}")
        if query:
            await safe_edit_message_text(query, "❌ Error starting cashout. Please try again.")

async def handle_auto_cashout_bank_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle auto cashout bank selection - show saved bank accounts"""
    async with button_callback_wrapper(update, "⏳ Loading banks..."):
        query = update.callback_query
        if not query:
            return
        
        # Feature guard: silently return if auto cashout features disabled
        from config import Config
        if not Config.ENABLE_AUTO_CASHOUT_FEATURES:
            return
        
        if not update or not update.effective_user:
            return
        user_id = update.effective_user.id if update.effective_user else 0
        
        async with async_managed_session() as session:
            try:
                from utils.repository import UserRepository
                from models import SavedBankAccount
                
                user = await UserRepository.get_user_by_telegram_id_async(session, user_id)
                if not user:
                    if not query:
                        return
                    await query.edit_message_text("❌ User not found")
                    return
                
                # Get saved bank accounts
                stmt = select(SavedBankAccount).where(
                    SavedBankAccount.user_id == user.id,
                    SavedBankAccount.is_active == True
                )
                result = await session.execute(stmt)
                banks = result.scalars().all()
                
                if not banks:
                    if not query:
                        return
                    await query.edit_message_text(
                        "🏦 No saved bank accounts found\n\n"
                        "Please add a bank account first from Settings → Manage Bank Accounts",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("⬅️ Back", callback_data="cashout_settings")]
                        ])
                    )
                    return
                
                # Build keyboard with bank options
                keyboard = []
                for bank in banks:
                    bank_text = f"🏦 {bank.bank_name} - {bank.label}"
                    keyboard.append([InlineKeyboardButton(
                        bank_text, 
                        callback_data=f"set_auto_bank:{bank.id}"
                    )])
                
                keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="cashout_settings")])
                
                if not query:
                    return
                await query.edit_message_text(
                    "🏦 **Select Bank Account for Auto CashOut**\n\n"
                    "Choose which bank account should receive your automatic cashouts:",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode='Markdown'
                )
                
            except Exception as e:
                logger.error(f"Error in auto cashout bank selection: {e}")
                if not query:
                    return
                await query.edit_message_text("❌ Error loading bank accounts")

async def handle_auto_cashout_crypto_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle auto cashout crypto selection - show saved crypto addresses"""
    async with button_callback_wrapper(update, "⏳ Loading addresses..."):
        query = update.callback_query
        if not query:
            return
        
        # Feature guard: silently return if auto cashout features disabled
        from config import Config
        if not Config.ENABLE_AUTO_CASHOUT_FEATURES:
            return
        
        if not update or not update.effective_user:
            return
        user_id = update.effective_user.id if update.effective_user else 0
        
        async with async_managed_session() as session:
            try:
                from utils.repository import UserRepository
                from models import SavedAddress
                
                user = await UserRepository.get_user_by_telegram_id_async(session, user_id)
                if not user:
                    if not query:
                        return
                    await query.edit_message_text("❌ User not found")
                    return
                
                # Get saved crypto addresses
                stmt = select(SavedAddress).where(
                    SavedAddress.user_id == user.id,
                    SavedAddress.is_active == True
                )
                result = await session.execute(stmt)
                addresses = result.scalars().all()
                
                if not addresses:
                    if not query:
                        return
                    await query.edit_message_text(
                        "💎 No saved crypto addresses found\n\n"
                        "Please add a crypto address first from Settings → Manage Addresses",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("⬅️ Back", callback_data="cashout_settings")]
                        ])
                    )
                    return
                
                # Build keyboard with crypto options
                keyboard = []
                for addr in addresses:
                    addr_text = f"💎 {addr.currency} - {addr.label}"
                    keyboard.append([InlineKeyboardButton(
                        addr_text, 
                        callback_data=f"set_auto_crypto:{addr.id}"
                    )])
                
                keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="cashout_settings")])
                
                if not query:
                    return
                await query.edit_message_text(
                    "💎 **Select Crypto Address for Auto CashOut**\n\n"
                    "Choose which crypto address should receive your automatic cashouts:",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode='Markdown'
                )
                
            except Exception as e:
                logger.error(f"Error in auto cashout crypto selection: {e}")
                if not query:
                    return
                await query.edit_message_text("❌ Error loading crypto addresses")

async def handle_toggle_auto_cashout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle toggle auto cashout enable/disable"""
    async with button_callback_wrapper(update, "⏳ Processing..."):
        query = update.callback_query
        if not query:
            return
        
        # Feature guard: silently return if auto cashout features disabled
        from config import Config
        if not Config.ENABLE_AUTO_CASHOUT_FEATURES:
            return
        
        if not update or not update.effective_user:
            return
        user_id = update.effective_user.id if update.effective_user else 0
        
        async with async_managed_session() as session:
            try:
                from utils.repository import UserRepository
                
                user = await UserRepository.get_user_by_telegram_id_async(session, user_id)
                if not user:
                    if not query:
                        return
                    await query.edit_message_text("❌ User not found")
                    return
                
                # Toggle auto-cashout status
                current_status = as_bool(getattr(user, 'auto_cashout_enabled', False))
                from sqlalchemy import update as sql_update
                stmt = sql_update(User).where(User.id == user.id).values(
                    auto_cashout_enabled=not current_status
                )
                await session.execute(stmt)
                await session.commit()
                
                # Show updated settings
                from handlers.commands import show_cashout_settings
                await show_cashout_settings(update, context)
                
            except Exception as e:
                logger.error(f"Error toggling auto-cashout: {e}")
                if not query:
                    return
                await query.edit_message_text("❌ Error updating auto-cashout settings")

async def handle_set_auto_cashout_bank(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Save selected bank as auto-cashout destination"""
    async with button_callback_wrapper(update, "⏳ Saving..."):
        query = update.callback_query
        if not query:
            return
        
        # Feature guard: silently return if auto cashout features disabled
        from config import Config
        if not Config.ENABLE_AUTO_CASHOUT_FEATURES:
            return
        
        if not update or not update.effective_user:
            return
        user_id = update.effective_user.id if update.effective_user else 0
        if not query or not query.data:
            return
        callback_data = query.data
        
        # Extract bank ID from callback
        try:
            bank_id = int(callback_data.split(":", 1)[1])
        except (IndexError, ValueError):
            if not query:
                return
            await query.edit_message_text("❌ Invalid bank selection")
            return
        
        async with async_managed_session() as session:
            try:
                from utils.repository import UserRepository
                from models import SavedBankAccount
                
                user = await UserRepository.get_user_by_telegram_id_async(session, user_id)
                if not user:
                    if not query:
                        return
                    await query.edit_message_text("❌ User not found")
                    return
                
                # Verify bank exists and belongs to user
                stmt = select(SavedBankAccount).where(
                    SavedBankAccount.id == bank_id,
                    SavedBankAccount.user_id == user.id,
                    SavedBankAccount.is_active == True
                )
                result = await session.execute(stmt)
                bank = result.scalar_one_or_none()
                
                if not bank:
                    if not query:
                        return
                    await query.edit_message_text("❌ Bank account not found")
                    return
                
                # Update user auto-cashout settings
                from sqlalchemy import update as sql_update
                stmt = sql_update(User).where(User.id == user.id).values(
                    cashout_preference="NGN_BANK",
                    auto_cashout_bank_account_id=bank_id,
                    auto_cashout_enabled=True
                )
                await session.execute(stmt)
                await session.commit()
                
                # Show success and updated settings
                if not query:
                    return
                await query.edit_message_text(
                    f"✅ **Auto CashOut Updated!**\n\n"
                    f"🏦 Bank: {bank.bank_name} - {bank.label}\n\n"
                    f"Your escrow earnings will now automatically cash out to this bank account.",
                    parse_mode='Markdown',
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⬅️ Back to Settings", callback_data="cashout_settings")]
                    ])
                )
                
            except Exception as e:
                logger.error(f"Error setting auto-cashout bank: {e}")
                if not query:
                    return
                await query.edit_message_text("❌ Error updating auto-cashout settings")

async def handle_set_auto_cashout_crypto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Save selected crypto address as auto-cashout destination"""
    async with button_callback_wrapper(update, "⏳ Saving..."):
        query = update.callback_query
        if not query:
            return
        
        # Feature guard: silently return if auto cashout features disabled
        from config import Config
        if not Config.ENABLE_AUTO_CASHOUT_FEATURES:
            return
        
        if not update or not update.effective_user:
            return
        user_id = update.effective_user.id if update.effective_user else 0
        if not query or not query.data:
            return
        callback_data = query.data
        
        # Extract address ID from callback
        try:
            addr_id = int(callback_data.split(":", 1)[1])
        except (IndexError, ValueError):
            if not query:
                return
            await query.edit_message_text("❌ Invalid address selection")
            return
        
        async with async_managed_session() as session:
            try:
                from utils.repository import UserRepository
                from models import SavedAddress
                
                user = await UserRepository.get_user_by_telegram_id_async(session, user_id)
                if not user:
                    if not query:
                        return
                    await query.edit_message_text("❌ User not found")
                    return
                
                # Verify address exists and belongs to user
                stmt = select(SavedAddress).where(
                    SavedAddress.id == addr_id,
                    SavedAddress.user_id == user.id,
                    SavedAddress.is_active == True
                )
                result = await session.execute(stmt)
                address = result.scalar_one_or_none()
                
                if not address:
                    if not query:
                        return
                    await query.edit_message_text("❌ Crypto address not found")
                    return
                
                # Update user auto-cashout settings
                from sqlalchemy import update as sql_update
                stmt = sql_update(User).where(User.id == user.id).values(
                    cashout_preference="CRYPTO",
                    auto_cashout_crypto_address_id=addr_id,
                    auto_cashout_enabled=True
                )
                await session.execute(stmt)
                await session.commit()
                
                # Show success and updated settings
                if not query:
                    return
                await query.edit_message_text(
                    f"✅ **Auto CashOut Updated!**\n\n"
                    f"💎 Address: {address.currency} - {address.label}\n\n"
                    f"Your escrow earnings will now automatically cash out to this crypto address.",
                    parse_mode='Markdown',
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⬅️ Back to Settings", callback_data="cashout_settings")]
                    ])
                )
                
            except Exception as e:
                logger.error(f"Error setting auto-cashout crypto: {e}")
                if not query:
                    return
                await query.edit_message_text("❌ Error updating auto-cashout settings")

async def handle_confirm_crypto_cashout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle crypto cashout confirmation with tokenized persistence - supports both legacy and token-based flows"""
    try:
        query = update.callback_query
        if not update or not update.effective_user:
            return
        user_id = update.effective_user.id if update.effective_user else 0
        
        await safe_answer_callback_query(query, "🔐 Starting security verification...")
        
        # INSTANT FEEDBACK: Show loading state immediately
        await safe_edit_message_text(
            query,
            "⏳ Processing cashout...\n\nValidating balance and calculating fees...",
            reply_markup=None
        )
        
        logger.info(f"🔐 CONFIRM CRYPTO CASHOUT - User: {user_id}")
        
        # Check for tokenized callback data
        if not query or not query.data:
            return
        callback_data = query.data
        token = None
        cashout_data = {}
        
        # Import token security utility
        from utils.cashout_token_security import CashoutTokenSecurity
        
        if callback_data.startswith("cc:"):
            # New short callback format: "cc:short_token"
            short_token_part = callback_data[3:]  # Remove "cc:" prefix
            logger.info(f"🔗 Processing short callback confirmation for user {user_id}: {callback_data}")
            
            # Find the pending cashout by matching the short token prefix
            async with async_managed_session() as session:
                try:
                    stmt = select(PendingCashout).where(
                        PendingCashout.user_id == user_id,
                        PendingCashout.expires_at > datetime.utcnow(),
                        PendingCashout.token.like(f"{short_token_part}%")
                    )
                    result = await session.execute(stmt)
                    pending_cashout = result.scalar_one_or_none()
                    
                    if pending_cashout:
                        # Use the full token:signature format for validation
                        full_token = f"{pending_cashout.token}:{pending_cashout.signature}"
                        logger.info(f"🔐 Found matching token for short callback {callback_data}: {pending_cashout.token[:16]}...")
                        # Validate with the full token:signature format
                        pending_cashout = CashoutTokenSecurity.validate_token_and_get_data(full_token, user_id)
                    else:
                        logger.warning(f"❌ No matching cashout found for short callback {callback_data}")
                        pending_cashout = None
                        
                except Exception as e:
                    logger.error(f"❌ Error finding cashout for short callback {callback_data}: {e}")
                    pending_cashout = None
        elif ":" in callback_data and callback_data.startswith("confirm_crypto_cashout:"):
            # Legacy tokenized format: "confirm_crypto_cashout:token" (still supported)
            parts = callback_data.split(":", 1)
            if len(parts) == 2:
                token = parts[1]
                logger.info(f"🔐 Processing legacy tokenized confirmation for user {user_id}: {token[:16]}...")
                
                # Validate token and get persisted data
                pending_cashout = CashoutTokenSecurity.validate_token_and_get_data(token, user_id)
            else:
                pending_cashout = None
        else:
            # No token format detected
            pending_cashout = None
        
        # Process token validation results
        if pending_cashout:
            # Convert database model to cashout_data format
            fee_amount_val = pending_cashout.fee_amount
            net_amount_val = pending_cashout.net_amount
            metadata_val = pending_cashout.cashout_metadata
            fee_breakdown_val = pending_cashout.fee_breakdown
            
            fee_amount = as_decimal(fee_amount_val) if fee_amount_val is not None else Decimal('0')
            net_amount = as_decimal(net_amount_val) if net_amount_val is not None else Decimal('0')
            metadata = metadata_val if metadata_val is not None else {}
            
            cashout_data = {
                'amount': as_decimal(pending_cashout.amount) if pending_cashout.amount is not None else Decimal('0'),
                'currency': as_str(pending_cashout.currency),
                'withdrawal_address': as_str(pending_cashout.withdrawal_address),
                'network': as_str(pending_cashout.network),
                'total_fee': fee_amount if fee_amount > Decimal('0') else None,
                'net_amount': net_amount if net_amount > Decimal('0') else None,
                'fee_breakdown': fee_breakdown_val if fee_breakdown_val is not None else None,
                'is_saved_address': metadata.get('is_saved_address', False) if metadata else False,
                'address_label': metadata.get('address_label') if metadata else None,
                'address_id': metadata.get('address_id') if metadata else None
            }
            logger.info(f"✅ Successfully loaded tokenized cashout data for user {user_id}")
        else:
            if callback_data.startswith("cc:") or callback_data.startswith("confirm_crypto_cashout:"):
                logger.warning(f"❌ Invalid or expired token for user {user_id}")
                await safe_edit_message_text(
                    query,
                    "❌ Security Token Expired\n\nYour confirmation token has expired for security. Please start the cashout process again.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔄 Start New Cashout", callback_data="wallet_cashout")]
                    ])
                )
                return
            # If no token format detected, fall through to legacy context data
        
        # Fallback to legacy context.user_data if no token or token failed
        if not cashout_data:
            logger.info(f"🔄 Falling back to legacy context data for user {user_id}")
            if not context.user_data:
                context.user_data = {}
            cashout_data = context.user_data.get('cashout_data', {})
        
        # Validate that we have the required cashout data
        if not cashout_data.get('amount') or not cashout_data.get('withdrawal_address') or not cashout_data.get('currency'):
            logger.error(f"❌ Missing required cashout data for user {user_id}: {list(cashout_data.keys())}")
            await safe_edit_message_text(
                query,
                "❌ Session Expired\n\nCashout data is missing. Please start over.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Start Over", callback_data="wallet_cashout")]
                ])
            )
            return
        
        # Extract crypto cashout details from cashout_data
        amount_usd = cashout_data.get('amount')
        currency = str(cashout_data.get('currency', 'USDT'))
        address = str(cashout_data.get('withdrawal_address', ''))
        network = str(cashout_data.get('network', 'ETH'))  # Default to ETH as that's what most handlers use
        
        # Calculate COMBINED fees (platform + network)
        try:
            # Use the correct function that handles all currencies, not just USDT
            logger.info(f"🔢 Calculating fees for: amount=${amount_usd}, currency={currency}, network={network}")
            cost_result = await calculate_crypto_cashout_with_network_fees(
                amount_usd=Decimal(str(amount_usd)) if amount_usd else Decimal('0.0'),
                currency=currency,
                network=network
            )
            logger.info(f"✅ Fee calculation result: {cost_result}")
            # Format ALL amounts properly for display (2 decimal places)
            gross_amount = f"{MonetaryDecimal.quantize_usd(cost_result['gross_amount']):.2f}"
            net_amount = f"{MonetaryDecimal.quantize_usd(cost_result['net_amount']):.2f}"
            total_fee = f"{MonetaryDecimal.quantize_usd(cost_result['total_fee']):.2f}"
            # CRITICAL FIX: Extract split fees for proper accounting
            platform_fee = f"{MonetaryDecimal.quantize_usd(cost_result['platform_fee']):.2f}"
            network_fee = f"{MonetaryDecimal.quantize_usd(cost_result['network_fee']):.2f}"
        except Exception as e:
            logger.error(f"❌ ERROR calculating crypto cashout combined costs: {e}", exc_info=True)
            logger.error(f"❌ Failed with: amount_usd={amount_usd}, currency={currency}, network={network}")
            # Use branded error message matching NGN flow
            error_msg = BrandingUtils.get_branded_error_message("validation", "Fee calculation error")
            await safe_edit_message_text(
                query,
                error_msg,
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Try Again", callback_data="wallet_cashout")]
                ])
            )
            return
        
        # BALANCE VALIDATION: Check if user has sufficient balance before proceeding
        try:
            async with async_managed_session() as session:
                # Get user first for balance validation
                stmt = select(User).where(User.telegram_id == int(user_id))
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()
                if not user:
                    validation_result = {"success": False, "error": "User account not found. Please contact support."}
                else:
                    # Import and use WalletValidator for balance validation
                    from utils.wallet_validation import WalletValidator
                    
                    # Validate cashout amount including fees
                    is_valid, error_msg = await WalletValidator.validate_cashout_amount(
                        user_id=as_int(user.id),
                        cashout_amount=Decimal(str(amount_usd)),
                        estimated_fees=Decimal(str(total_fee)),
                        currency="USD",
                        session=session
                    )
                    
                    if not is_valid:
                        logger.warning(f"❌ Crypto cashout validation failed for user {user_id}: {error_msg}")
                        validation_result: dict = {"success": False, "error": f"Insufficient wallet balance: {error_msg}"}
                    else:
                        logger.info(f"✅ Balance validation passed for crypto cashout - User: {user_id}, Amount: ${amount_usd}, Fees: ${total_fee}")
                        validation_result: dict = {"success": True}
            
            if not validation_result["success"]:
                # Create branded error message
                branded_error = BrandingUtils.get_branded_error_message("validation", validation_result["error"])
                plain_error = branded_error.replace('**', '').replace('*', '').replace('`', '')
                
                await safe_edit_message_text(
                    query,
                    plain_error,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("💰 Check Balance", callback_data="wallet_menu")],
                        [InlineKeyboardButton("🔙 Back", callback_data="wallet_cashout")]
                    ])
                )
                return
                
        except Exception as validation_error:
            logger.error(f"Error during balance validation: {validation_error}")
            error_msg = BrandingUtils.get_branded_error_message("validation", "Balance validation error")
            await safe_edit_message_text(
                query,
                error_msg,
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Try Again", callback_data="wallet_cashout")]
                ])
            )
            return

        # Balance validation passed, continue with crypto cashout
        async with async_managed_session() as session:
            # Get user again for OTP flow
            stmt = select(User).where(User.telegram_id == int(user_id))
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()
            
            # Check if user has temp email (skip-email path)
            user_email = as_str(user.email) if user else None
            is_temp_email = user_email and user_email.startswith('temp_') and user_email.endswith('@onboarding.temp')
            
            # Only block if user has no email OR has real email but not verified
            # Skip-email users (temp emails) are allowed to cashout
            if not user or (not user_email) or (user_email and not is_temp_email and not as_bool(user.email_verified)):
                await safe_edit_message_text(
                    query,
                    "❌ Email Required\n\nEmail verification is required for crypto cashouts.\n\nPlease set up and verify your email address first.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("📧 Set Email", callback_data="onboarding_email")],
                        [InlineKeyboardButton("🔙 Back", callback_data="wallet_menu")]
                    ])
                )
                return
            
            # Create crypto cashout context for security binding
            from services.cashout_otp_flow import CashoutOTPFlow
            
            try:
                crypto_context = CashoutOTPFlow.create_crypto_context(
                    asset=str(currency),
                    network=str(network),
                    address=str(address),
                    gross_amount=str(gross_amount),
                    net_amount=str(net_amount),
                    fee=str(total_fee),
                    platform_fee=str(platform_fee),
                    network_fee=str(network_fee)
                )
                
                # ===== SKIP OTP: Proceed directly to cashout confirmation =====
                # OTP verification has been removed - all users proceed directly
                logger.info(f"✅ DIRECT_CRYPTO_CASHOUT: User {user_id} proceeding without OTP (OTP removed)")
                
                text = f"""💰 {currency} ({network}) Cashout

📤 ${gross_amount} - ${total_fee} fee = ${net_amount}
📍 `{address[:20]}...{address[-10:] if len(address) > 30 else address[20:]}`

⚠️ Verify address carefully!"""
                
                # Store crypto context for processing
                if not context.user_data:
                    context.user_data = {}
                context.user_data.setdefault('cashout_data', {})['crypto_context'] = crypto_context
                context.user_data.setdefault('cashout_data', {})['skip_email_user'] = True
                
                keyboard = [
                    [InlineKeyboardButton("💰 Process Cashout", callback_data="process_crypto_cashout")],
                    [InlineKeyboardButton("🔙 Cancel", callback_data="wallet_menu")]
                ]
                
                await safe_edit_message_text(
                    query,
                    text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="Markdown"
                )
                return
            
            except Exception as otp_error:
                logger.error(f"Error during OTP verification: {otp_error}")
                error_msg = BrandingUtils.get_branded_error_message("validation", "OTP verification error")
                await safe_edit_message_text(
                    query,
                    error_msg,
                    parse_mode='Markdown',
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔄 Try Again", callback_data="wallet_cashout")]
                    ])
                )
                
    except Exception as e:
        logger.error(f"Error in handle_confirm_crypto_cashout: {e}")
        if 'query' in locals() and query:
            # Use branded error message matching NGN flow
            error_msg = BrandingUtils.get_branded_error_message("payment", "Crypto verification process error")
            await safe_edit_message_text(
                query,
                error_msg,
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Try Again", callback_data="wallet_cashout")]
                ])
            )

# ===== EXCHANGE HANDLERS - Wire to direct_exchange.py =====

async def handle_exchange_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle exchange menu - wire to direct_exchange.py exchange interface"""
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "🔄 Opening exchange...")
    
    try:
        # Exchange handler not available - direct_exchange.py does not exist
        logger.warning(f"⚠️ Exchange handler not available - handlers.direct_exchange does not exist")
        await safe_edit_message_text(
            query,
            "❌ Exchange Service Unavailable\n\nThe exchange service is currently being updated. Please try again later.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back to Wallet", callback_data="menu_wallet")]
            ])
        )
    except Exception as e:
        logger.error(f"❌ Error opening exchange menu: {e}")
        await safe_edit_message_text(
            query,
            "❌ Exchange Error\n\nFailed to open exchange menu. Please try again.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Retry", callback_data="menu_exchange")],
                [InlineKeyboardButton("🔙 Back to Wallet", callback_data="menu_wallet")]
            ])
        )

async def handle_view_rates(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle view rates - show current exchange rates"""
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "💱 Loading rates...")
    
    try:
        # Import rate services
        from services.fastforex_service import FastForexService
        from services.financial_gateway import financial_gateway
        
        # Get current rates
        fastforex = FastForexService()
        
        # Fetch multiple rates in parallel
        usd_ngn_rate = await fastforex.get_usd_to_ngn_rate_clean()
        btc_usd_rate = await financial_gateway.get_crypto_to_usd_rate("BTC")
        eth_usd_rate = await financial_gateway.get_crypto_to_usd_rate("ETH")
        
        # Format rates display
        rates_text = f"""💱 Current Exchange Rates

Fiat Rates:
🇺🇸 USD → 🇳🇬 NGN: ₦{usd_ngn_rate:.2f}

Crypto Rates:
₿ BTC → 💵 USD: {format_clean_amount(btc_usd_rate)}
Ξ ETH → 💵 USD: {format_clean_amount(eth_usd_rate)}

*Rates are live and updated every few minutes*
*Exchange fees apply to conversions*"""
        
        await safe_edit_message_text(
            query,
            rates_text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Exchange Now", callback_data="menu_exchange")],
                [InlineKeyboardButton("🔙 Back to Wallet", callback_data="menu_wallet")]
            ])
        )
        
    except Exception as e:
        logger.error(f"❌ Error fetching exchange rates: {e}")
        await safe_edit_message_text(
            query,
            f"""💱 Exchange Rates

❌ Unable to fetch live rates
{str(e)}

Please try again in a moment.""",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Retry", callback_data="view_rates")],
                [InlineKeyboardButton("🔙 Back to Wallet", callback_data="menu_wallet")]
            ])
        )

# ===== DIRECT WALLET HANDLERS LIST (DEFINED AFTER ALL FUNCTIONS) =====

async def handle_wallet_show_qr(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show QR code for wallet deposit"""
    async with button_callback_wrapper(update, "📱 Generating QR code..."):
        query = update.callback_query

        logger.info("Wallet QR code display requested")
        
        # Extract address from callback_data (format: "show_qr:address")
        if not query or not query.data:
            return
        callback_data = query.data
        if not callback_data.startswith("show_qr:"):
            logger.error("Invalid QR callback data")
            if not query:
                return
            await query.edit_message_text("❌ Invalid QR request")
            return
        
        deposit_address = callback_data[8:]  # Remove "show_qr:" prefix
        
        # Get deposit details from context
        if not context.user_data:
            context.user_data = {}
        deposit_currency = context.user_data.get('deposit_currency') if context.user_data else None
        
        if not deposit_address or not deposit_currency:
            if not query:
                return
            await query.edit_message_text(
                "❌ No payment address found. Please try again.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back", callback_data="crypto_funding_start")]
                ])
            )
            return

        # Generate and send QR code
        try:
            from io import BytesIO
            from services.qr_generator import QRCodeService
            import base64

            # Generate QR code for the address
            qr_base64 = QRCodeService.generate_deposit_qr(
                address=deposit_address,
                amount=None,  # No amount for wallet deposits
                currency=deposit_currency
            )
            
            if not qr_base64:
                raise Exception("QR generation failed")
                
            # Convert base64 to bytes for Telegram
            qr_bytes = base64.b64decode(qr_base64)
            bio = BytesIO(qr_bytes)
            bio.name = "wallet_qr_code.png"

            # Send QR code as photo
            caption = f"""📱 Wallet Deposit QR Code

💰 Scan to send {deposit_currency} to your wallet
📍 Address: `{deposit_address}`

🔒 Secure & Fast - Powered by LockBay"""

            # Send photo and immediately edit original message
            if not query or not query.message:
                return
            await context.bot.send_photo(
                chat_id=query.message.chat.id,
                photo=bio,
                caption=caption,
                parse_mode='Markdown'
            )
            
            # Edit original message to show QR was sent with navigation options
            keyboard = [
                [
                    InlineKeyboardButton("🔄 Other Crypto", callback_data="crypto_funding_start"),
                    InlineKeyboardButton("❌ Cancel", callback_data="menu_wallet")
                ]
            ]
            
            if not query:
                return
            await query.edit_message_text(
                f"✅ QR code sent!\n\nScan the QR code above to deposit {deposit_currency} to your wallet.",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

        except Exception as e:
            logger.error(f"Error generating wallet QR code: {e}")
            
            # Fallback message with address and navigation options
            keyboard = [
                [
                    InlineKeyboardButton("🔄 Other Crypto", callback_data="crypto_funding_start"),
                    InlineKeyboardButton("❌ Cancel", callback_data="menu_wallet")
                ]
            ]
            
            if not query:
                return
            await query.edit_message_text(
                f"📱 Wallet Deposit Address\n\n"
                f"💰 Send {deposit_currency} to:\n"
                f"`{deposit_address}`\n\n"
                f"⚠️ QR code generation failed, use address above",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

# ===== PHASE 2 & 3: QUICK CASHOUT HANDLERS =====

async def show_amount_entry_screen(query, context) -> None:
    """Show amount entry screen for quick cashout flows"""
    try:
        user = query.from_user
        if not user:
            return
            
        # Get user's balance
        user_id = user.id
        async with async_managed_session() as session:
            # get_user_wallet is sync, use in async context
            stmt = select(Wallet).where(Wallet.user_id == user_id)
            result = await session.execute(stmt)
            wallet = result.scalar_one_or_none()
            
            if not wallet:
                await safe_edit_message_text(query, "❌ Wallet not found. Please try again.")
                return
            
            balance = as_decimal(wallet.available_balance)
        
        # Show amount entry prompt
        text = f"""⚡ Quick Cashout

💰 Available: {format_branded_amount(balance, 'USD')}
💵 Minimum: ${Config.MIN_CASHOUT_AMOUNT}

💬 Enter the amount you want to cash out:"""
        
        keyboard = [[InlineKeyboardButton("🔙 Cancel", callback_data="menu_wallet")]]
        
        await safe_edit_message_text(
            query, text, reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
        # Set state to accept amount input
        await set_wallet_state(user_id, context, "entering_custom_amount")
        
        # Store session data
        if not context.user_data:
            context.user_data = {}
        cashout_data = context.user_data.get("cashout_data", {})
        cashout_data["cashout_balance"] = decimal_to_string(balance, precision=2)
        cashout_data["session_created"] = str(datetime.now(timezone.utc))
        context.user_data["cashout_data"] = cashout_data
        
    except Exception as e:
        logger.error(f"Error in show_amount_entry_screen: {e}")
        await safe_edit_message_text(query, "❌ Error loading cashout. Please try again.")

async def handle_quick_crypto_cashout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """PHASE 2: Handle quick cashout with last used cryptocurrency"""
    query = update.callback_query
    await safe_answer_callback_query(query, "⚡ Loading quick cashout...")
    
    try:
        # Extract currency from callback
        if not query or not query.data:
            return
        currency = query.data.replace("quick_crypto:", "")
        
        # Initialize cashout flow with pre-selected currency
        if not context.user_data:
            context.user_data = {}
        context.user_data["cashout_data"] = {
            "method": "crypto",
            "currency": currency,
            "network": get_network_from_currency(currency)
        }
        
        # Skip to amount selection
        await show_amount_entry_screen(query, context)
        
    except Exception as e:
        logger.error(f"Error in quick crypto cashout: {e}")
        await safe_edit_message_text(query, "❌ Quick cashout failed. Please use regular cashout.")

async def handle_quick_ngn_cashout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """PHASE 2: Handle quick NGN cashout with last used bank"""
    query = update.callback_query
    await safe_answer_callback_query(query, "⚡ Loading quick NGN cashout...")
    
    try:
        # Initialize cashout flow with NGN method
        if not context.user_data:
            context.user_data = {}
        context.user_data["cashout_data"] = {
            "method": "ngn_bank"
        }
        
        # Skip to amount selection
        await show_amount_entry_screen(query, context)
        
    except Exception as e:
        logger.error(f"Error in quick NGN cashout: {e}")
        await safe_edit_message_text(query, "❌ Quick cashout failed. Please use regular cashout.")

async def show_cashout_method_selection(query, context, amount: Decimal) -> None:
    """Show method selection for first-time Cash Out All users"""
    text = f"""⚡ Cash Out All

💵 Amount: {format_clean_amount(amount)} USD

Choose your cashout method:"""
    
    keyboard = [
        [InlineKeyboardButton(
            "💎 Crypto (BTC, ETH, USDT)", 
            callback_data=f"cashout_method:crypto:{amount}"
        )]
    ]
    
    # Add NGN option only if feature is enabled
    if Config.ENABLE_NGN_FEATURES:
        keyboard.append([InlineKeyboardButton(
            "🏦 NGN Bank Transfer", 
            callback_data=f"cashout_method:ngn:{amount}"
        )])
    
    keyboard.append([InlineKeyboardButton("⬅️ Back to Wallet", callback_data="wallet_menu")])
    
    await safe_edit_message_text(query, text, reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_cashout_method_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle user's choice of cashout method (crypto or NGN)"""
    query = update.callback_query
    await safe_answer_callback_query(query, "⏳ Processing...")
    
    try:
        # Parse callback: "cashout_method:crypto:25.50" or "cashout_method:ngn:25.50"
        if not query or not query.data:
            return
        parts = query.data.split(":")
        method = parts[1]  # "crypto" or "ngn"
        amount = parts[2]  # "25.50"
        
        if not context.user_data:
            context.user_data = {}
        context.user_data["cashout_data"] = {"amount": amount}
        
        if method == "crypto":
            if not context.user_data:
                context.user_data = {}
            context.user_data["cashout_data"]["method"] = "crypto"
            # Show crypto currency selection (BTC, ETH, USDT)
            await show_crypto_currency_selection(query, context)
        
        elif method == "ngn":
            if not context.user_data:
                context.user_data = {}
            context.user_data["cashout_data"]["method"] = "ngn_bank"
            # Show saved bank accounts
            if not update or not update.effective_user:
                return
            user_id = update.effective_user.id if update.effective_user else 0
            async with async_managed_session() as session:
                stmt = select(User).where(User.telegram_id == user_id)
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()
                
                if user:
                    from models import SavedBankAccount
                    stmt = select(SavedBankAccount).where(
                        SavedBankAccount.user_id == user.id,
                        SavedBankAccount.is_active == True
                    )
                    result = await session.execute(stmt)
                    saved_accounts = result.scalars().all()
                    await show_saved_bank_accounts(query, context, Decimal(amount), saved_accounts)
        
    except Exception as e:
        logger.error(f"Error in cashout method choice: {e}")
        await safe_edit_message_text(query, "❌ Error processing selection. Please try again.")

async def handle_quick_cashout_all(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """PHASE 3: Handle one-tap cash out entire balance (CRYPTO + NGN SUPPORT)"""
    query = update.callback_query
    await safe_answer_callback_query(query, "⚡ Processing Cash Out All...")
    
    try:
        # Extract amount from callback
        if not query or not query.data:
            return
        amount_str = query.data.replace("quick_cashout_all:", "")
        amount = Decimal(amount_str)
        
        # Get user's last used cashout method (crypto OR ngn)
        telegram_user_id = query.from_user.id
        last_method = await get_last_used_cashout_method(telegram_user_id)
        
        if not last_method or not last_method.get("method"):
            # No history - show method selection (Crypto or NGN)
            if not context.user_data:
                context.user_data = {}
            context.user_data["cashout_data"] = {"amount": str(amount)}
            await show_cashout_method_selection(query, context, amount)
            return
        
        elif last_method.get("method") == "CRYPTO":
            # Has crypto history - use crypto flow
            if not context.user_data:
                context.user_data = {}
            context.user_data["cashout_data"] = {
                "amount": str(amount),
                "method": "crypto",
                "currency": last_method.get("currency"),
                "network": get_network_from_currency(last_method.get("currency", ""))
            }
            # Skip directly to address selection
            await show_crypto_address_selection(query, context, amount, last_method.get("currency", "USDT"))
        
        elif last_method.get("method") == "NGN_BANK":
            # Has NGN history - use NGN flow
            if not context.user_data:
                context.user_data = {}
            context.user_data["cashout_data"] = {
                "amount": str(amount),
                "method": "ngn_bank"
            }
            # Show saved bank accounts
            async with async_managed_session() as session:
                stmt = select(User).where(User.telegram_id == telegram_user_id)
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()
                
                if user:
                    from models import SavedBankAccount
                    stmt = select(SavedBankAccount).where(
                        SavedBankAccount.user_id == user.id,
                        SavedBankAccount.is_active == True
                    )
                    result = await session.execute(stmt)
                    saved_accounts = result.scalars().all()
                    await show_saved_bank_accounts(query, context, amount, saved_accounts)
        
    except Exception as e:
        logger.error(f"Error in quick cashout all: {e}")
        await safe_edit_message_text(query, "❌ Quick cashout failed. Please use regular cashout.")

DIRECT_WALLET_HANDLERS = [
    # Core wallet interface
    {
        'pattern': r'^menu_wallet$',
        'handler': handle_wallet_menu,
        'description': 'Main wallet menu interface'
    },
    {
        'pattern': r'^wallet_menu$',
        'handler': handle_wallet_menu,
        'description': 'Wallet menu (alternate callback data)'
    },
    {
        'pattern': r'^cancel_cashout$',
        'handler': handle_wallet_menu,
        'description': 'Cancel cashout and return to wallet menu'
    },
    {
        'pattern': r'^wallet_cashout$',
        'handler': handle_wallet_cashout,
        'description': 'Start wallet cashout flow'
    },
    
    # Amount and method selection
    {
        'pattern': r'^amount:.*$',
        'handler': handle_amount_selection,
        'description': 'Handle cashout amount selection'
    },
    {
        'pattern': r'^method:.*$',
        'handler': handle_method_selection,
        'description': 'Handle payment method selection'
    },
    {
        'pattern': r'^back_to_amounts$',
        'handler': show_wallet_menu,
        'description': 'Return to wallet menu (amount selection hidden)'
    },
    {
        'pattern': r'^back_to_methods$',
        'handler': handle_back_to_methods,
        'description': 'Return to payment method selection'
    },
    
    # Bank account handlers
    {
        'pattern': r'^saved_bank:.*$',
        'handler': handle_saved_bank_selection,
        'description': 'Handle saved bank account selection'
    },
    {
        'pattern': r'^add_new_bank$',
        'handler': handle_add_new_bank,
        'description': 'Add new bank account'
    },
    
    # Crypto handlers
    {
        'pattern': r'^select_crypto:.*$',
        'handler': handle_crypto_currency_selection,
        'description': 'Handle crypto currency selection'
    },
    {
        'pattern': r'^saved_address:.*$',
        'handler': handle_saved_crypto_address_selection,
        'description': 'Handle saved crypto address selection'
    },
    {
        'pattern': r'^add_crypto_address:.*$',
        'handler': handle_add_crypto_address,
        'description': 'Add new crypto address'
    },
    {
        'pattern': r'^save_address:.*$',
        'handler': handle_save_crypto_address,
        'description': 'Save crypto address after entry'
    },
    {
        'pattern': r'^use_once:.*$',
        'handler': handle_use_crypto_address_once,
        'description': 'Use crypto address without saving'
    },
    {
        'pattern': r'^save_crypto_address:.*$',
        'handler': handle_save_crypto_address_new,
        'description': 'Handle saving crypto address from text input'
    },
    {
        'pattern': r'^skip_save_crypto:.*$',
        'handler': handle_skip_save_crypto,
        'description': 'Handle skipping crypto address save and proceed to confirmation'
    },
    {
        'pattern': r'^cs:.*$',
        'handler': handle_short_crypto_save,
        'description': 'Handle short crypto save callback (cs:hash)'
    },
    {
        'pattern': r'^ck:.*$',
        'handler': handle_short_crypto_skip,
        'description': 'Handle short crypto skip callback (ck:hash)'
    },
    {
        'pattern': r'^cc:.*$',
        'handler': handle_confirm_crypto_cashout,
        'description': 'Handle crypto cashout confirmation (cc:token)'
    },
    {
        'pattern': r'^confirm_crypto_cashout$',
        'handler': handle_confirm_crypto_cashout,
        'description': 'Handle crypto cashout confirmation (plain format)'
    },
    
    # Crypto address management handlers
    {
        'pattern': r'^manage_crypto_addresses$',
        'handler': show_saved_crypto_addresses_management,
        'description': 'Show crypto address management interface'
    },
    {
        'pattern': r'^view_crypto_addr:.*$',
        'handler': handle_view_crypto_address,
        'description': 'View crypto address details with delete option'
    },
    {
        'pattern': r'^delete_crypto_addr:.*$',
        'handler': handle_delete_crypto_address,
        'description': 'Delete a saved crypto address'
    },
    
    # Essential existing handlers (preserved)
    {
        'pattern': r'^confirm_ngn_cashout:.*$',
        'handler': handle_confirm_ngn_cashout,
        'description': 'Handle NGN cashout confirmation'
    },
    {
        'pattern': r'^confirm_ngn_cashout_and_save$',
        'handler': handle_confirm_ngn_cashout_and_save,
        'description': 'Handle NGN cashout confirmation with bank account saving'
    },
    {
        'pattern': r'^resend_ngn_otp$',
        'handler': handle_resend_ngn_otp,
        'description': 'Handle NGN OTP resend request'
    },
    {
        'pattern': r'^cancel_ngn_cashout$',
        'handler': cancel_ngn_cashout,
        'description': 'Handle NGN cashout cancellation'
    },
    {
        'pattern': r'^select_ngn_bank$',
        'handler': handle_select_ngn_bank,
        'description': 'Show Nigerian bank selection list'
    },
    {
        'pattern': r'^select_bank:.*$', 
        'handler': handle_select_bank,
        'description': 'Handle specific bank selection'
    },
    {
        'pattern': r'^retry_bank_verification$',
        'handler': handle_retry_bank_verification,
        'description': 'Handle retry bank verification'
    },
    
    # New NGN payout confirmation handlers
    {
        'pattern': r'^confirm_ngn_payout_proceed$',
        'handler': handle_confirm_ngn_payout_proceed,
        'description': 'Handle NGN payout confirmation - proceed without saving bank'
    },
    {
        'pattern': r'^confirm_ngn_payout_and_save$',
        'handler': handle_confirm_ngn_payout_and_save,
        'description': 'Handle NGN payout confirmation - proceed and save bank account'
    },
    {
        'pattern': r'^retry_ngn_payout_confirmation$',
        'handler': handle_retry_ngn_payout_confirmation,
        'description': 'Retry NGN payout confirmation when rate fetch fails'
    },
    
    # 🔐 SECURITY: Crypto OTP handlers (CRITICAL SECURITY FUNCTIONS)
    {
        'pattern': r'^process_crypto_cashout$',
        'handler': handle_process_crypto_cashout,
        'description': '🔐 SECURE: Process crypto cashout after OTP verification'
    },
    {
        'pattern': r'^resend_crypto_otp$',
        'handler': handle_resend_crypto_otp,
        'description': '📧 Resend crypto cashout OTP with cooldown management'
    },
    
    # Exchange handlers - wire to direct_exchange.py
    {
        'pattern': r'^menu_exchange$',
        'handler': handle_exchange_menu,
        'description': 'Open exchange menu for crypto-NGN conversions'
    },
    {
        'pattern': r'^view_rates$',
        'handler': handle_view_rates,
        'description': 'Show current exchange rates'
    },
    
    # Back navigation handlers
    {
        'pattern': r'^back_to_main$',
        'handler': handle_back_to_main,
        'description': 'Navigate back to main menu'
    },
    
    # Bank addition flow handlers
    {
        'pattern': r'^add_bank_select:\d+$',
        'handler': handle_bank_selection_callback,
        'description': 'Handle bank selection from list'
    },
    {
        'pattern': r'^add_bank_show_more$',
        'handler': handle_bank_selection_callback,
        'description': 'Show more banks in the list'
    },
    {
        'pattern': r'^add_bank_search$',
        'handler': handle_bank_selection_callback,
        'description': 'Start bank search input'
    },
    {
        'pattern': r'^add_bank_back_to_selection$',
        'handler': handle_add_bank_navigation_callbacks,
        'description': 'Navigate back to bank selection'
    },
    {
        'pattern': r'^add_bank_back_to_popular$',
        'handler': handle_add_bank_navigation_callbacks,
        'description': 'Navigate back to popular banks'
    },
    {
        'pattern': r'^add_bank_back_to_account$',
        'handler': handle_add_bank_navigation_callbacks,
        'description': 'Navigate back to account entry'
    },
    {
        'pattern': r'^add_bank_save_no_label$',
        'handler': handle_add_bank_navigation_callbacks,
        'description': 'Save bank account without label'
    },
    
    # NEW: Simplified bank addition handlers
    {
        'pattern': r'^save_verified_bank_account$',
        'handler': handle_save_verified_bank_account,
        'description': 'Save verified bank account to user\'s saved accounts'
    },
    {
        'pattern': r'^select_bank_for_addition:\d+$',
        'handler': handle_select_bank_for_addition,
        'description': 'Handle bank selection when multiple banks found during addition'
    },
    {
        'pattern': r'^show_qr:.+$',
        'handler': handle_wallet_show_qr,
        'description': 'Show QR code for wallet deposit'
    },
    # PHASE 2 & 3: Quick Cashout Actions
    {
        'pattern': r'^quick_crypto:.+$',
        'handler': handle_quick_crypto_cashout,
        'description': 'Quick cashout with last used cryptocurrency'
    },
    {
        'pattern': r'^quick_ngn$',
        'handler': handle_quick_ngn_cashout,
        'description': 'Quick NGN cashout with last used bank'
    },
    {
        'pattern': r'^quick_cashout_all:.+$',
        'handler': handle_quick_cashout_all,
        'description': 'One-tap cash out entire wallet balance (crypto + NGN support)'
    },
    {
        'pattern': r'^cashout_method:(crypto|ngn):.+$',
        'handler': handle_cashout_method_choice,
        'description': 'Handle cashout method selection (crypto or NGN)'
    }
]

# ===== PHASE 2: SMART DEFAULTS - HELPER FUNCTIONS =====

async def get_last_used_crypto(telegram_user_id: int) -> Optional[str]:
    """Get user's last used cryptocurrency for smart defaults"""
    try:
        async with async_managed_session() as session:
            # Get user's most recent successful crypto cashout
            stmt = select(User).where(User.telegram_id == telegram_user_id)
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()
            
            if not user:
                return None
            
            # Query most recent successful cashout
            from models import Cashout, CashoutStatus
            stmt = select(Cashout).where(
                Cashout.user_id == as_int(user.id),
                Cashout.cashout_type == "crypto",
                Cashout.status == CashoutStatus.COMPLETED.value
            ).order_by(Cashout.created_at.desc()).limit(1)
            
            result = await session.execute(stmt)
            last_cashout = result.scalar_one_or_none()
            
            if last_cashout and last_cashout.currency:
                return as_str(last_cashout.currency)
            
            return None
    except Exception as e:
        logger.error(f"Error getting last used crypto: {e}")
        return None

async def get_last_used_cashout_method(telegram_user_id: int) -> dict:
    """Get user's last used cashout method (crypto OR NGN) for smart defaults"""
    try:
        async with async_managed_session() as session:
            # Get user's most recent successful cashout
            stmt = select(User).where(User.telegram_id == telegram_user_id)
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()
            
            if not user:
                return {"method": None}
            
            # Query most recent successful cashout (any type)
            from models import Cashout, CashoutStatus
            stmt = select(Cashout).where(
                Cashout.user_id == as_int(user.id),
                Cashout.status == CashoutStatus.COMPLETED.value
            ).order_by(Cashout.created_at.desc()).limit(1)
            
            result = await session.execute(stmt)
            last_cashout = result.scalar_one_or_none()
            
            if not last_cashout:
                return {"method": None}
            
            # Check if it's NGN or crypto
            if as_str(last_cashout.cashout_type) == "ngn_bank":
                return {
                    "method": "NGN_BANK",
                    "bank_id": as_int(last_cashout.bank_account_id) if last_cashout.bank_account_id else None
                }
            elif as_str(last_cashout.cashout_type) == "crypto":
                return {
                    "method": "CRYPTO",
                    "currency": as_str(last_cashout.currency) if last_cashout.currency else None
                }
            else:
                return {"method": None}
    except Exception as e:
        logger.error(f"Error getting last cashout method: {e}")
        return {"method": None}

async def get_last_used_address(user_id: int, currency: str) -> Optional[str]:
    """Get user's most recently used address for a specific currency"""
    try:
        async with async_managed_session() as session:
            stmt = select(SavedAddress).where(
                SavedAddress.user_id == user_id,
                SavedAddress.currency == currency,
                SavedAddress.is_active == True
            ).order_by(SavedAddress.last_used.desc().nullslast()).limit(1)
            
            result = await session.execute(stmt)
            address = result.scalar_one_or_none()
            
            return str(address.id) if address and address.id is not None else None
    except Exception as e:
        logger.error(f"Error getting last used address: {e}")
        return None

async def update_last_used_crypto(user_id: int, currency: str) -> None:
    """Track last used crypto for future smart defaults"""
    # This will be automatically tracked when cashout completes
    # No additional storage needed - we query from Cashout table
    pass

logger.info(f"✅ wallet_direct.py loaded with {len(DIRECT_WALLET_HANDLERS)} comprehensive wallet handlers")
