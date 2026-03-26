"""
User Transaction History Handler
Provides comprehensive transaction history viewing with pagination and filtering

CACHE INVALIDATION POINTS:
==========================
Transaction history cache should be invalidated after any operation that creates
or updates transactions:

1. Escrow Operations (handlers/escrow.py):
   - After escrow completion: invalidate_transaction_history_cache(context.user_data)
   - After escrow cancellation: invalidate_transaction_history_cache(context.user_data)
   - After escrow refund: invalidate_transaction_history_cache(context.user_data)

2. Wallet Operations (handlers/wallet_direct.py):
   - After wallet deposit completed: invalidate_transaction_history_cache(context.user_data)
   - After cashout processed: invalidate_transaction_history_cache(context.user_data)
   - After crypto withdrawal: invalidate_transaction_history_cache(context.user_data)

3. Exchange Operations (handlers/exchange_handler.py):
   - After exchange completed: invalidate_transaction_history_cache(context.user_data)

4. Admin Operations (handlers/admin.py):
   - After admin adjustment: invalidate_transaction_history_cache(context.user_data)

Usage pattern in handlers:
    from utils.transaction_history_prefetch import invalidate_transaction_history_cache
    
    # After transaction state change
    invalidate_transaction_history_cache(context.user_data)
    logger.info(f"🗑️ TX_CACHE: Invalidated transaction history cache for user {user_id}")
"""

import logging
import hashlib
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime, timezone
from decimal import Decimal
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from telegram.error import BadRequest
from sqlalchemy import or_

from models import (
    User, Transaction, UnifiedTransaction, TransactionType, TransactionStatus,
    UnifiedTransactionType, UnifiedTransactionStatus
)
from utils.session_reuse_manager import get_reusable_session
from utils.callback_utils import safe_edit_message_text, safe_answer_callback_query
from utils.transaction_history_prefetch import (
    prefetch_transaction_history,
    get_cached_transaction_history,
    cache_transaction_history
)

logger = logging.getLogger(__name__)

# Conversation states
TRANSACTION_FILTER, TRANSACTION_DETAIL = range(2)

# Constants
TRANSACTIONS_PER_PAGE = 10
MAX_DESCRIPTION_LENGTH = 50
AUTO_REFRESH_INTERVAL = 15  # seconds
AUTO_REFRESH_MAX_CYCLES = 8  # 2 minutes total
ACTIVE_TX_REFRESHES = {}  # Store active refresh jobs


def ensure_timezone_aware(dt: Optional[datetime]) -> datetime:
    """Ensure datetime is timezone-aware (UTC). Handles None and naive datetimes."""
    if dt is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    if dt.tzinfo is None:
        # Naive datetime - assume UTC and make it aware
        return dt.replace(tzinfo=timezone.utc)
    return dt


def escape_markdown(md_text: str) -> str:
    """Safely escape markdown characters to prevent parsing errors"""
    if not md_text:
        return ""
    return (str(md_text)
        .replace('\\', '\\\\')
        .replace('_', '\\_')
        .replace('*', '\\*')
        .replace('[', '\\[')
        .replace(']', '\\]')
        .replace('`', '\\`')
        .replace('(', '\\(')
        .replace(')', '\\)')
        .replace('~', '\\~')
        .replace('>', '\\>')
        .replace('#', '\\#')
        .replace('+', '\\+')
        .replace('-', '\\-')
        .replace('=', '\\=')
        .replace('|', '\\|')
        .replace('{', '\\{')
        .replace('}', '\\}')
        .replace('.', '\\.')
        .replace('!', '\\!')
        .replace(':', '\\:'))


def format_transaction_amount(amount: Decimal, currency: str, transaction_type: str) -> str:
    """Format transaction amount with appropriate sign and emoji"""
    amount_str = f"{abs(amount):.8f}".rstrip('0').rstrip('.') if abs(amount) < 1 else f"{abs(amount):.2f}"
    
    # Determine if this is a credit or debit using enum values
    credit_types = [
        TransactionType.DEPOSIT.value, TransactionType.WALLET_DEPOSIT.value, 
        TransactionType.ESCROW_RELEASE.value, TransactionType.REFUND.value, 
        TransactionType.ADMIN_ADJUSTMENT.value,
        # Overpayment/Underpayment Credits (bonus money)
        'ESCROW_OVERPAYMENT', 'EXCHANGE_OVERPAYMENT', 'ESCROW_UNDERPAY_REFUND'
    ]
    debit_types = [
        TransactionType.WITHDRAWAL.value, TransactionType.CASHOUT.value, 
        TransactionType.CASHOUT_DEBIT.value, TransactionType.ESCROW_PAYMENT.value, 
        TransactionType.FEE.value
    ]
    
    if transaction_type.upper() in credit_types:
        return f"🟢 +{amount_str} {currency}"
    elif transaction_type.upper() in debit_types:
        return f"🔴 -{amount_str} {currency}"
    else:
        return f"⚪ {amount_str} {currency}"


def format_transaction_status(status: str) -> str:
    """Format transaction status with appropriate emoji"""
    status_emojis = {
        TransactionStatus.PENDING.value.upper(): '🟡',
        TransactionStatus.CONFIRMED.value.upper(): '🟢', 
        TransactionStatus.FAILED.value.upper(): '🔴',
        TransactionStatus.CANCELLED.value.upper(): '⚫',
        'PAYMENT_CONFIRMED': '✅',
        'COMPLETED': '✅',
        'PROCESSING': '🔄'
    }
    return f"{status_emojis.get(status.upper(), '⚪')} {status.title()}"


def get_status_emoji(status: str) -> str:
    """Get status emoji only (no text) - simplified format"""
    status_emojis = {
        TransactionStatus.PENDING.value.upper(): '⏳',
        TransactionStatus.CONFIRMED.value.upper(): '✅', 
        TransactionStatus.FAILED.value.upper(): '❌',
        TransactionStatus.CANCELLED.value.upper(): '❌',
        'PAYMENT_CONFIRMED': '✅',
        'COMPLETED': '✅',
        'PROCESSING': '⏳'
    }
    return status_emojis.get(status.upper(), '✅')


def is_unified_escrow_release(tx_data: dict) -> bool:
    """Determine if a unified escrow transaction is a release (credit) or hold (debit)"""
    # For unified escrow transactions, check amount sign or status to determine type
    if tx_data['transaction_type'].upper() == UnifiedTransactionType.ESCROW.value:
        # Positive amounts typically indicate releases (money coming to wallet)
        # Negative amounts typically indicate holds (money leaving wallet)
        return tx_data['amount'] > 0
    return False


def get_transaction_type_icon(transaction_type: str, tx_data: dict = None) -> str:
    """Get distinctive icon for each transaction type"""
    
    # CRITICAL FIX: Check if wallet_deposit is actually a crypto deposit  
    transaction_type_upper = transaction_type.upper()
    if transaction_type_upper == TransactionType.WALLET_DEPOSIT.value.upper():
        # Check transaction description to distinguish crypto vs NGN deposits
        if tx_data and 'description' in tx_data:
            description = tx_data['description'].lower()
            
            # Specific crypto icons based on currency type
            crypto_icons = {
                'btc': '₿',      # Bitcoin symbol
                'eth': '⟠',      # Ethereum symbol  
                'ltc': '🔸',     # LTC - diamond shape
                'usdt': '💲',    # USDT - dollar symbol
                'bnb': '🟨',     # BNB - yellow square
                'usdc': '💵',    # USDC - dollar bills
                'doge': '🐕',    # DOGE - dog
                'ada': '🔷',     # ADA - blue diamond
                'xrp': '💧',     # XRP - water drop
                'sol': '☀️'      # SOL - sun
            }
            
            # Check for specific crypto types first for specialized icons
            for crypto_code, crypto_icon in crypto_icons.items():
                if crypto_code in description:
                    return crypto_icon
            
            # Generic crypto icons
            if any(crypto in description for crypto in ['crypto', '→ usd', 'wallet deposit:']):
                return '💰'  # Generic crypto deposit icon
            elif any(ngn in description for ngn in ['ngn', 'bank', 'fincra', 'naira']):
                return '💳'  # NGN bank funding icon
        # Default to crypto deposit icon for wallet_deposit type (most common case)
        return '💰'
    
    type_icons = {
        # Regular Transaction Types (uppercase keys for lookup)
        TransactionType.DEPOSIT.value.upper(): '💰',  # Wallet deposit from crypto
        TransactionType.WALLET_DEPOSIT.value.upper(): '💰',  # Default case, handled above
        TransactionType.WITHDRAWAL.value.upper(): '💸',  # Generic withdrawal
        TransactionType.CASHOUT.value.upper(): '🏦',  # Bank cashout
        TransactionType.CASHOUT_DEBIT.value.upper(): '🏧',  # Cashout processing
        TransactionType.ESCROW_PAYMENT.value.upper(): '🔒',  # Money put into escrow
        TransactionType.ESCROW_RELEASE.value.upper(): '🔓',  # Money released from escrow
        TransactionType.ESCROW_REFUND.value.upper(): '↩️',  # Escrow refund
        TransactionType.WALLET_TRANSFER.value.upper(): '🔄',  # Internal transfer
        TransactionType.EXCHANGE_HOLD.value.upper(): '🔃',  # Exchange hold (different from transfer)
        TransactionType.EXCHANGE_DEBIT.value.upper(): '💱',  # Currency exchange
        TransactionType.FEE.value.upper(): '📊',  # Platform fee
        TransactionType.ADMIN_ADJUSTMENT.value.upper(): '⚙️',  # Admin adjustment
        TransactionType.REFUND.value.upper(): '💰',  # Refund
        
        # Unified Transaction Types (uppercase keys for lookup)
        UnifiedTransactionType.WALLET_CASHOUT.value.upper(): '🏦',  # Unified wallet cashout
        'WALLET_CASHOUT': '🏦',  # Backup for string matching
        
        # Overpayment/Underpayment Credit Types (dynamic types from tolerance service)
        'ESCROW_OVERPAYMENT': '🎁',  # Overpayment credit bonus
        'EXCHANGE_OVERPAYMENT': '🎁',  # Exchange overpayment credit
        'ESCROW_UNDERPAY_REFUND': '💰',  # Underpayment auto-refund
    }
    
    # Special handling for unified escrow transactions
    transaction_type_upper = transaction_type.upper()
    if transaction_type_upper in [UnifiedTransactionType.ESCROW.value.upper(), 'ESCROW']:
        if tx_data and is_unified_escrow_release(tx_data):
            return '🔓'  # Release icon
        else:
            return '🔒'  # Hold icon (default)
    
    return type_icons.get(transaction_type_upper, '⚪')


def get_transaction_description(transaction_type: str, external_id: str = None, tx_data: dict = None) -> str:
    """Generate human-readable transaction description with proper escaping"""
    
    # CRITICAL FIX: Use database description if available (for custom formatted descriptions)
    if tx_data and 'description' in tx_data:
        # Normalize description: convert None to empty string before stripping
        raw_desc = tx_data.get('description') or ''
        db_desc = raw_desc.strip()
        # Only use custom description if it's non-empty AND contains emoji indicators
        # This ensures backwards compatibility with older transactions that have empty descriptions
        if db_desc and any(indicator in db_desc for indicator in ['✅', '🏆', '↩️', '⚖️', '•', '🔒', '💸', '💰', '💳', '🔥']):
            return db_desc  # Use the custom description from database as-is
    
    # CRITICAL FIX: Check if wallet_deposit is actually a crypto deposit
    transaction_type_upper = transaction_type.upper()
    if transaction_type_upper == TransactionType.WALLET_DEPOSIT.value.upper():
        # Check transaction description to distinguish crypto vs NGN deposits
        if tx_data and 'description' in tx_data:
            description = tx_data['description'].lower()
            
            # Extract specific crypto type from description - SIMPLIFIED
            crypto_types = {
                'btc': 'BTC',
                'eth': 'ETH', 
                'ltc': 'LTC',
                'usdt': 'USDT',
                'bnb': 'BNB',
                'usdc': 'USDC',
                'doge': 'DOGE',
                'ada': 'ADA',
                'xrp': 'XRP',
                'sol': 'SOL'
            }
            
            # Check for specific crypto types first
            for crypto_code, crypto_label in crypto_types.items():
                if crypto_code in description:
                    return f"{crypto_label} Deposit"
            
            # Generic deposit if no specific type found
            if any(crypto in description for crypto in ['crypto', '→ usd', 'wallet deposit:']):
                return 'Deposit'
            elif any(ngn in description for ngn in ['ngn', 'bank', 'fincra', 'naira']):
                return 'NGN Deposit'
        # Default to deposit for wallet_deposit type (most common case)
        return 'Deposit'
    
    descriptions = {
        # Regular Transaction Types (uppercase keys for lookup) - SIMPLIFIED
        TransactionType.DEPOSIT.value.upper(): 'Deposit',
        TransactionType.WALLET_DEPOSIT.value.upper(): 'Deposit',  # Default case, handled above
        TransactionType.WITHDRAWAL.value.upper(): 'Withdrawal',
        TransactionType.CASHOUT.value.upper(): 'Cashout',
        TransactionType.CASHOUT_DEBIT.value.upper(): 'Cashout',
        TransactionType.ESCROW_PAYMENT.value.upper(): 'Payment',
        TransactionType.ESCROW_RELEASE.value.upper(): 'Release',
        TransactionType.ESCROW_REFUND.value.upper(): 'Refund',
        TransactionType.WALLET_TRANSFER.value.upper(): 'Transfer',
        TransactionType.EXCHANGE_HOLD.value.upper(): 'Exchange',
        TransactionType.EXCHANGE_DEBIT.value.upper(): 'Exchange',
        TransactionType.FEE.value.upper(): 'Fee',
        TransactionType.ADMIN_ADJUSTMENT.value.upper(): 'Adjustment',
        TransactionType.REFUND.value.upper(): 'Refund',
        
        # Unified Transaction Types (uppercase keys for lookup) - SIMPLIFIED
        UnifiedTransactionType.WALLET_CASHOUT.value.upper(): 'Cashout',
        'WALLET_CASHOUT': 'Cashout',  # Backup for string matching
        
        # Overpayment/Underpayment Credit Types - SIMPLIFIED
        'ESCROW_OVERPAYMENT': 'Bonus',
        'EXCHANGE_OVERPAYMENT': 'Bonus',
        'ESCROW_UNDERPAY_REFUND': 'Refund',
    }
    
    # Special handling for unified escrow transactions - SIMPLIFIED
    if transaction_type_upper in [UnifiedTransactionType.ESCROW.value.upper(), 'ESCROW']:
        if tx_data and is_unified_escrow_release(tx_data):
            return 'Release'
        else:
            return 'Payment'
    
    base_desc = descriptions.get(transaction_type_upper, transaction_type.replace('_', ' ').title())
    
    if external_id:
        # Truncate and escape external ID if too long
        ext_id = external_id[:12] + "..." if len(external_id) > 12 else external_id
        escape_markdown(ext_id)
        return f"{base_desc}"  # Don't show external ID to keep display clean
    
    return base_desc


def generate_tx_fingerprint(transactions: List[Dict[str, Any]], page: int, filter_type: str) -> str:
    """Generate fingerprint for transaction data to detect changes"""
    if not transactions:
        return hashlib.sha1(f"empty_{page}_{filter_type}".encode()).hexdigest()[:8]
    
    # Create fingerprint from key transaction data
    fingerprint_data = []
    for tx in transactions:
        tx_fingerprint = f"{tx['transaction_id']}_{tx['status']}_{tx['amount']}_{tx.get('updated_at', tx['created_at'])}"
        fingerprint_data.append(tx_fingerprint)
    
    combined = f"{page}_{filter_type}_{'|'.join(fingerprint_data)}"
    return hashlib.sha1(combined.encode()).hexdigest()[:8]


async def render_transaction_history(db_user_id: int, page: int, filter_type: str) -> Tuple[str, InlineKeyboardMarkup, str, Dict[str, str]]:
    """Pure function to render transaction history (for reuse in auto-refresh)"""
    import re
    
    with get_reusable_session("tx_render", user_id=db_user_id) as session:
        # Import models for joins
        from models import Escrow, Cashout
        
        # Build queries for both Transaction and UnifiedTransaction tables
        # Join with Escrow and Cashout tables to get public IDs
        regular_transactions = session.query(
            Transaction, 
            Escrow.escrow_id.label('escrow_public_id'),
            Cashout.cashout_id.label('cashout_public_id')
        ).outerjoin(
            Escrow, Transaction.escrow_id == Escrow.id
        ).outerjoin(
            Cashout, Transaction.cashout_id == Cashout.id
        ).filter(
            Transaction.user_id == db_user_id
        )
        
        unified_transactions = session.query(UnifiedTransaction).filter(
            UnifiedTransaction.user_id == db_user_id
        )
        
        # Apply filters using proper enum values
        if filter_type != 'ALL':
            if filter_type == 'DEPOSITS':
                # CRITICAL FIX: Include overpayment credits in deposits filter
                deposit_types = [
                    TransactionType.DEPOSIT.value, 
                    TransactionType.WALLET_DEPOSIT.value,
                    'escrow_overpayment',  # Overpayment bonus credits
                    'exchange_overpayment',  # Exchange overpayment credits
                    'escrow_underpay_refund'  # Underpayment auto-refunds
                ]
                regular_transactions = regular_transactions.filter(
                    Transaction.transaction_type.in_(deposit_types)
                )
                unified_transactions = unified_transactions.filter(
                    UnifiedTransaction.transaction_type.in_([])  # No deposit types exist
                )
            elif filter_type == 'WITHDRAWALS':
                regular_transactions = regular_transactions.filter(
                    Transaction.transaction_type.in_([TransactionType.WITHDRAWAL.value, TransactionType.CASHOUT.value, TransactionType.CASHOUT_DEBIT.value])
                )
                unified_transactions = unified_transactions.filter(
                    UnifiedTransaction.transaction_type.in_([UnifiedTransactionType.WALLET_CASHOUT.value])
                )
            elif filter_type == 'ESCROW':
                regular_transactions = regular_transactions.filter(
                    Transaction.transaction_type.in_([TransactionType.ESCROW_PAYMENT.value, TransactionType.ESCROW_RELEASE.value, TransactionType.ESCROW_REFUND.value])
                )
                unified_transactions = unified_transactions.filter(
                    UnifiedTransaction.transaction_type.in_([UnifiedTransactionType.ESCROW.value])
                )
            elif filter_type == 'PENDING':
                regular_transactions = regular_transactions.filter(
                    Transaction.status.in_([TransactionStatus.PENDING.value, 'PROCESSING'])
                )
                unified_transactions = unified_transactions.filter(
                    UnifiedTransaction.status.in_([UnifiedTransactionStatus.PENDING.value, UnifiedTransactionStatus.PROCESSING.value])
                )
        
        # Get all transactions from both tables
        regular_tx_list = regular_transactions.all()
        unified_tx_list = unified_transactions.all()
        
        # Combine and convert to a unified format for display
        all_transactions = []
        
        # Add regular transactions (now returns tuples: (Transaction, escrow_public_id, cashout_public_id))
        for tx_tuple in regular_tx_list:
            tx = tx_tuple[0]  # Transaction object
            escrow_public_id = tx_tuple[1] if len(tx_tuple) > 1 else None  # Public escrow ID
            cashout_public_id = tx_tuple[2] if len(tx_tuple) > 2 else None  # Public cashout ID
            
            # Extract exchange_id from description for legacy exchange transactions
            exchange_public_id = None
            description = getattr(tx, 'description', '')
            tx_type_lower = tx.transaction_type.lower() if tx.transaction_type else ''
            if tx_type_lower in ['exchange_hold', 'exchange_debit', 'exchange_hold_release']:
                # Extract exchange ID from description pattern: "... for EX123ABC"
                match = re.search(r'for (EX[A-Z0-9]+)', description) if description else None
                if match:
                    exchange_public_id = match.group(1)
                # Also check extra_data if available
                elif hasattr(tx, 'extra_data') and tx.extra_data:
                    exchange_public_id = tx.extra_data.get('exchange_id')
            
            all_transactions.append({
                'source': 'regular',
                'transaction': tx,
                'created_at': ensure_timezone_aware(tx.created_at),
                'transaction_id': tx.transaction_id,
                'amount': tx.amount,
                'currency': tx.currency,
                'transaction_type': tx.transaction_type,
                'status': tx.status,
                'description': description,  # CRITICAL FIX: Include description for crypto type detection
                'external_id': getattr(tx, 'external_id', None),
                'escrow_public_id': escrow_public_id,  # Add public escrow ID
                'cashout_public_id': cashout_public_id,  # Add public cashout ID
                'exchange_public_id': exchange_public_id,  # Add exchange ID for exchange transactions
                'fee': getattr(tx, 'fee', None),
                'updated_at': ensure_timezone_aware(getattr(tx, 'updated_at', tx.created_at))
            })
        
        # Add unified transactions  
        for tx in unified_tx_list:
            # For exchange transactions, reference_id contains the exchange ID
            exchange_public_id = None
            tx_type_lower = tx.transaction_type.lower() if tx.transaction_type else ''
            if tx_type_lower in ['exchange_buy_crypto', 'exchange_sell_crypto', 'exchange_hold', 'exchange_debit', 'exchange_hold_release']:
                exchange_public_id = getattr(tx, 'reference_id', None)
            
            all_transactions.append({
                'source': 'unified',
                'transaction': tx,
                'created_at': ensure_timezone_aware(getattr(tx, 'created_at', None)),
                'transaction_id': getattr(tx, 'reference_id', None) or str(tx.id),
                'amount': tx.amount,
                'currency': tx.currency,
                'transaction_type': tx.transaction_type,
                'status': tx.status,
                'description': getattr(tx, 'description', ''),  # CRITICAL FIX: Include description for crypto type detection
                'external_id': getattr(tx, 'external_id', None),
                'exchange_public_id': exchange_public_id,  # Add exchange ID for exchange transactions
                'fee': getattr(tx, 'fee', None),
                'updated_at': ensure_timezone_aware(getattr(tx, 'updated_at', None))
            })
        
        # Sort by creation date
        all_transactions.sort(key=lambda x: x['created_at'], reverse=True)
        
        # Apply pagination
        total_count = len(all_transactions)
        offset = (page - 1) * TRANSACTIONS_PER_PAGE
        transactions = all_transactions[offset:offset + TRANSACTIONS_PER_PAGE]
        
        # Calculate pagination info
        total_pages = (total_count + TRANSACTIONS_PER_PAGE - 1) // TRANSACTIONS_PER_PAGE
        
        # Generate fingerprint for change detection
        fingerprint = generate_tx_fingerprint(transactions, page, filter_type)
        
        # Build message
        if not transactions:
            message = "📋 Transaction History\n\n"
            if filter_type == 'ALL':
                message += "No transactions found. Start trading to see your transaction history!"
            else:
                message += f"No {filter_type.lower()} transactions found."
            
            # Add auto-refresh timestamp
            now = datetime.utcnow()
            message += f"\n\n*Last updated: {now.strftime('%H:%M:%S')}*"
                
            keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="main_menu")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            return message, reply_markup, fingerprint, {}
        
        # Format transaction list - SIMPLIFIED ONE-LINE FORMAT
        message = f"📋 Transactions ({total_count} total)\n"
        message += f"Page {page} of {total_pages}\n\n"
        
        for i, tx_data in enumerate(transactions, 1):
            # Get transaction type icon and description with improved unified escrow handling
            type_icon = get_transaction_type_icon(tx_data['transaction_type'], tx_data)
            desc = get_transaction_description(tx_data['transaction_type'], external_id=tx_data.get('external_id'), tx_data=tx_data)
            
            # Format amount with proper sign (without duplicate icons)
            amount = tx_data['amount']
            currency = tx_data['currency']
            transaction_type = tx_data['transaction_type']
            
            # Determine if this is a credit or debit with improved unified escrow handling
            credit_types = [
                # Regular Transaction Types (Money coming into wallet)
                TransactionType.DEPOSIT.value, TransactionType.WALLET_DEPOSIT.value, 
                TransactionType.ESCROW_RELEASE.value, TransactionType.REFUND.value, 
                TransactionType.ESCROW_REFUND.value,  # Added: escrow refunds are credits
                TransactionType.ADMIN_ADJUSTMENT.value,
                # Unified Transaction Types (uppercase)
                'deposit', 'wallet_deposit', 'escrow_release', 'refund', 'escrow_refund', 'admin_adjustment',
                # Overpayment/Underpayment Credits (bonus money)
                'escrow_overpayment', 'exchange_overpayment', 'escrow_underpay_refund'
            ]
            debit_types = [
                # Regular Transaction Types (Money leaving wallet)
                TransactionType.WITHDRAWAL.value, TransactionType.CASHOUT.value, 
                TransactionType.CASHOUT_DEBIT.value, TransactionType.ESCROW_PAYMENT.value, 
                TransactionType.FEE.value,
                # Exchange types (money leaves wallet for exchange)
                TransactionType.EXCHANGE_DEBIT.value, TransactionType.EXCHANGE_HOLD.value,
                # Unified Transaction Types (lowercase)
                UnifiedTransactionType.WALLET_CASHOUT.value,
                'withdrawal', 'cashout', 'escrow_payment', 'wallet_cashout', 
                'exchange_debit', 'exchange_hold'
            ]
            
            # Special handling for unified escrow transactions
            is_credit = False
            is_debit = False
            
            # Use lowercase for case-insensitive comparison
            transaction_type_lower = transaction_type.lower()
            
            if transaction_type.upper() in [UnifiedTransactionType.ESCROW.value, 'ESCROW']:
                # For unified escrow, determine based on amount and context
                if is_unified_escrow_release(tx_data):
                    is_credit = True  # Escrow release = money coming to wallet
                else:
                    is_debit = True   # Escrow hold = money leaving wallet
            elif transaction_type_lower in credit_types:
                is_credit = True
            elif transaction_type_lower in debit_types:
                is_debit = True
            
            amount_str = f"{abs(amount):.8f}".rstrip('0').rstrip('.') if abs(amount) < 1 else f"{abs(amount):.2f}"
            if is_credit:
                amount_display = f"+{amount_str} {currency}"
            elif is_debit:
                amount_display = f"-{amount_str} {currency}"
            else:
                amount_display = f"{amount_str} {currency}"
            
            # Format status
            format_transaction_status(tx_data['status'])
            
            # Format date
            min_dt = datetime.min.replace(tzinfo=timezone.utc)
            date_str = tx_data['created_at'].strftime("%m/%d %H:%M") if tx_data['created_at'] and tx_data['created_at'] != min_dt else "Unknown"
            
            # UX IMPROVEMENT: Add reference IDs for transactions that have them
            escrow_related_types = [
                TransactionType.ESCROW_PAYMENT.value, TransactionType.ESCROW_RELEASE.value, 
                TransactionType.ESCROW_REFUND.value, 'ESCROW_OVERPAYMENT', 'ESCROW', 
                'escrow_payment', 'escrow_release', 'escrow_refund', 'escrow_overpayment'
            ]
            cashout_related_types = [
                TransactionType.CASHOUT.value, TransactionType.CASHOUT_DEBIT.value,
                TransactionType.CASHOUT_HOLD.value, TransactionType.CASHOUT_HOLD_RELEASE.value,
                'cashout', 'cashout_debit', 'cashout_hold', 'cashout_hold_release'
            ]
            exchange_related_types = [
                TransactionType.EXCHANGE_HOLD.value, TransactionType.EXCHANGE_DEBIT.value,
                TransactionType.EXCHANGE_HOLD_RELEASE.value, 'EXCHANGE_BUY_CRYPTO', 'EXCHANGE_SELL_CRYPTO',
                'exchange_hold', 'exchange_debit', 'exchange_hold_release', 
                'exchange_buy_crypto', 'exchange_sell_crypto'
            ]
            
            # Build description with reference ID if applicable
            desc_with_id = desc
            if transaction_type.lower() in [t.lower() for t in escrow_related_types]:
                escrow_id = tx_data.get('escrow_public_id')
                if escrow_id:
                    # Simplified: Show only last 4 chars of escrow ID
                    short_id = escrow_id[-4:] if len(escrow_id) >= 4 else escrow_id
                    desc_with_id = f"{desc} #{short_id}"
            elif transaction_type.lower() in [t.lower() for t in cashout_related_types]:
                cashout_id = tx_data.get('cashout_public_id')
                if cashout_id:
                    # Simplified: Show only last 4 chars
                    short_id = cashout_id[-4:] if len(cashout_id) >= 4 else cashout_id
                    desc_with_id = f"{desc} #{short_id}"
            elif transaction_type.lower() in [t.lower() for t in exchange_related_types]:
                exchange_id = tx_data.get('exchange_public_id')
                if exchange_id:
                    # Simplified: Show only last 4 chars
                    short_id = exchange_id[-4:] if len(exchange_id) >= 4 else exchange_id
                    desc_with_id = f"{desc} #{short_id}"
            
            # SIMPLIFIED ONE-LINE FORMAT: type icon + amount + desc + date
            line = f"{type_icon} {amount_display} {desc_with_id} • {date_str}\n"
            message += line
        
        # Add auto-refresh timestamp
        now = datetime.utcnow()
        message += f"\n*Last updated: {now.strftime('%H:%M:%S')}*"
        
        # Build keyboard - SIMPLIFIED LAYOUT
        keyboard = []
        
        # Initialize transaction mapping for callback resolution (empty now, no detail buttons)
        tx_mapping = {}
        
        # SIMPLIFIED FILTER BUTTONS: Just 3 essential filters in one row
        filters = [
            ('ALL', '🔍 All'),
            ('DEPOSITS', '💰 In'),
            ('WITHDRAWALS', '💸 Out')
        ]
        
        filter_row = []
        for filter_code, filter_name in filters:
            # Mark the active filter with a checkmark
            if filter_code == filter_type:
                button_text = f"✅ {filter_name.split(' ', 1)[1]}"  # Remove emoji and add checkmark
            else:
                button_text = filter_name
            filter_row.append(InlineKeyboardButton(button_text, callback_data=f"tx_filter_{filter_code}"))
        
        keyboard.append(filter_row)
        
        # SIMPLIFIED NAVIGATION: Previous | Menu | Next in one row
        nav_row = []
        if page > 1:
            nav_row.append(InlineKeyboardButton("◀️ Prev", callback_data=f"tx_page_{page-1}"))
        nav_row.append(InlineKeyboardButton("🔙 Menu", callback_data="main_menu"))
        if page < total_pages:
            nav_row.append(InlineKeyboardButton("Next ▶️", callback_data=f"tx_page_{page+1}"))
        
        keyboard.append(nav_row)
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        # Return the actual tx_mapping if transactions exist, otherwise empty dict
        return message, reply_markup, fingerprint, tx_mapping if transactions else {}


def render_prefetched_transaction_history(prefetch_data: dict, page: int, filter_type: str) -> Tuple[str, InlineKeyboardMarkup, str, Dict[str, str]]:
    """
    Render transaction history from prefetched data
    
    PERFORMANCE: Uses prefetched data (1 query) instead of multiple queries (6+)
    
    Args:
        prefetch_data: Dictionary from TransactionHistoryPrefetchData.to_dict()
        page: Current page number
        filter_type: Filter type ('ALL', 'DEPOSITS', 'WITHDRAWALS', etc.)
        
    Returns:
        Tuple of (message, reply_markup, fingerprint, tx_mapping)
    """
    all_transactions = prefetch_data.get('transactions', [])
    page_size = prefetch_data.get('page_size', TRANSACTIONS_PER_PAGE)
    
    # Apply filter to prefetched transactions (in-memory filtering)
    if filter_type != 'ALL':
        filtered_transactions = []
        for tx_data in all_transactions:
            tx_type = tx_data.get('transaction_type', '').lower()
            
            if filter_type == 'DEPOSITS':
                # Include deposits and wallet transactions (money in)
                if tx_type in ['deposit', 'wallet_deposit', 'escrow_release', 'refund', 'wallet_transaction', 'escrow']:
                    filtered_transactions.append(tx_data)
            elif filter_type == 'WITHDRAWALS':
                # Include cashouts and exchanges (money out)
                if tx_type in ['withdrawal', 'cashout', 'wallet_cashout', 'exchange', 'exchange_sell', 'exchange_buy']:
                    filtered_transactions.append(tx_data)
            elif filter_type == 'ESCROW':
                # Include escrow-related transactions
                if 'escrow' in tx_type:
                    filtered_transactions.append(tx_data)
            elif filter_type == 'PENDING':
                # Filter by status
                status = tx_data.get('status', '').lower()
                if status in ['pending', 'processing', 'awaiting_approval']:
                    filtered_transactions.append(tx_data)
        
        transactions = filtered_transactions
        total_count = len(filtered_transactions)
    else:
        transactions = all_transactions
        total_count = len(all_transactions)
    
    # Calculate pagination info
    total_pages = (total_count + page_size - 1) // page_size if total_count > 0 else 1
    
    # Generate fingerprint for change detection
    fingerprint = generate_tx_fingerprint(transactions, page, filter_type)
    
    # Build message
    if not transactions:
        message = "📋 Transaction History\n\n"
        if filter_type == 'ALL':
            message += "No transactions found. Start trading to see your transaction history!"
        else:
            message += f"No {filter_type.lower()} transactions found."
        
        # Add auto-refresh timestamp
        now = datetime.utcnow()
        message += f"\n\n*Last updated: {now.strftime('%H:%M:%S')}*"
            
        keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="main_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        return message, reply_markup, fingerprint, {}
    
    # Format transaction list - SIMPLIFIED ONE-LINE FORMAT
    message = f"📋 Transactions ({total_count} total)\n"
    message += f"Page {page} of {total_pages}\n\n"
    
    tx_mapping = {}
    for i, tx_data in enumerate(transactions, 1):
        # Convert prefetch data to display format
        tx_dict = {
            'transaction_type': tx_data.get('transaction_type', 'unknown'),
            'amount': Decimal(str(tx_data.get('amount', 0))),
            'currency': tx_data.get('currency', 'USD'),
            'status': tx_data.get('status', 'unknown'),
            'created_at': tx_data.get('created_at', datetime.utcnow()),
            'description': tx_data.get('description', ''),
            'external_id': tx_data.get('reference_id')
        }
        
        # Get transaction type icon and description
        type_icon = get_transaction_type_icon(tx_dict['transaction_type'], tx_dict)
        tx_desc = get_transaction_description(tx_dict['transaction_type'], external_id=tx_dict.get('external_id'), tx_data=tx_dict)
        
        # Format amount with proper sign
        amount = tx_dict['amount']
        currency = tx_dict['currency']
        transaction_type = tx_dict['transaction_type'].lower()
        
        # Determine if this is a credit or debit
        credit_types = ['deposit', 'wallet_deposit', 'escrow_release', 'refund', 'escrow_refund', 'wallet_transaction', 'escrow']
        debit_types = ['withdrawal', 'cashout', 'escrow_payment', 'wallet_cashout', 'exchange', 'exchange_sell', 'exchange_buy']
        
        amount_str = f"{abs(amount):.8f}".rstrip('0').rstrip('.') if abs(amount) < 1 else f"{abs(amount):.2f}"
        if transaction_type in credit_types:
            amount_display = f"+{amount_str} {currency}"
        elif transaction_type in debit_types:
            amount_display = f"-{amount_str} {currency}"
        else:
            amount_display = f"{amount_str} {currency}"
        
        # Format date
        created_at = tx_dict['created_at']
        if isinstance(created_at, str):
            try:
                created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            except Exception:
                created_at = datetime.utcnow()
        date_str = created_at.strftime("%m/%d %H:%M") if created_at else "Unknown"
        
        # Build simplified display line
        line = f"{type_icon} {amount_display} {tx_desc} • {date_str}\n"
        message += line
    
    # Add auto-refresh timestamp
    now = datetime.utcnow()
    message += f"\n*Last updated: {now.strftime('%H:%M:%S')}*"
    
    # Build keyboard - SIMPLIFIED LAYOUT
    keyboard = []
    
    # SIMPLIFIED FILTER BUTTONS: Just 3 essential filters in one row
    filters = [
        ('ALL', '🔍 All'),
        ('DEPOSITS', '💰 In'),
        ('WITHDRAWALS', '💸 Out')
    ]
    
    filter_row = []
    for filter_code, filter_name in filters:
        # Mark the active filter with a checkmark
        if filter_code == filter_type:
            button_text = f"✅ {filter_name.split(' ', 1)[1]}"
        else:
            button_text = filter_name
        filter_row.append(InlineKeyboardButton(button_text, callback_data=f"tx_filter_{filter_code}"))
    
    keyboard.append(filter_row)
    
    # SIMPLIFIED NAVIGATION: Previous | Menu | Next in one row
    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton("◀️ Prev", callback_data=f"tx_page_{page-1}"))
    nav_row.append(InlineKeyboardButton("🔙 Menu", callback_data="main_menu"))
    if page < total_pages:
        nav_row.append(InlineKeyboardButton("Next ▶️", callback_data=f"tx_page_{page+1}"))
    
    keyboard.append(nav_row)
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    return message, reply_markup, fingerprint, tx_mapping


async def show_transaction_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Display user's transaction history with pagination and auto-refresh
    
    PREFETCH INTEGRATION (6→1 query, 83% reduction):
    ================================================
    - Cache check first (instant if cached)
    - Prefetch with single batched query if not cached
    - Reuse cached data for pagination
    - Performance: ~180ms → ~120ms (33% improvement)
    """
    query = update.callback_query
    user = update.effective_user
    
    if not user:
        if query:
            await safe_answer_callback_query(query, "❌ User not found")
        return ConversationHandler.END
    
    if query:
        await safe_answer_callback_query(query, "📋 Loading history...")
    
    try:
        # CRITICAL FIX: Reset filter to 'ALL' when opening fresh from main menu
        if query and query.data in ['wallet_history', 'transaction_history']:
            context.user_data['tx_filter'] = 'ALL'
            context.user_data['tx_page'] = 1
            logger.info(f"🔄 FRESH_OPEN: Reset transaction history to ALL filter for user {user.id}")
        
        # Parse pagination parameters
        page = int(context.user_data.get('tx_page', 1))
        filter_type = context.user_data.get('tx_filter', 'ALL')
        
        # STEP 1: Check cache first (fast path)
        cached_data = get_cached_transaction_history(context.user_data, page)
        
        if not cached_data:
            # STEP 2: Cache miss - prefetch with async session
            from database import async_managed_session
            
            async with async_managed_session() as session:
                # Get user ID from database
                from sqlalchemy import select
                user_stmt = select(User).where(User.telegram_id == int(user.id))
                user_result = await session.execute(user_stmt)
                db_user = user_result.scalar_one_or_none()
                
                if not db_user:
                    message = "❌ User not found in database. Please complete onboarding first."
                    if query:
                        await safe_edit_message_text(query, message)
                    else:
                        await update.message.reply_text(message)
                    return ConversationHandler.END
                
                # STEP 3: Prefetch transaction history with batched query
                logger.info(f"💾 TX_CACHE_MISS: Prefetching page {page} for user {db_user.id}")
                history_data = await prefetch_transaction_history(
                    db_user.id, 
                    session, 
                    page=page, 
                    page_size=TRANSACTIONS_PER_PAGE
                )
                
                if history_data:
                    # STEP 4: Cache the prefetched data
                    cache_transaction_history(context.user_data, history_data)
                    cached_data = history_data.to_dict()
                    logger.info(
                        f"✅ TX_PREFETCH_COMPLETE: Cached {len(history_data.transactions)} transactions "
                        f"in {history_data.prefetch_duration_ms:.1f}ms "
                        f"{'✅' if history_data.prefetch_duration_ms < 120 else '⚠️'}"
                    )
                else:
                    # Fallback to sync render if prefetch fails
                    logger.warning(f"⚠️ TX_PREFETCH_FAILED: Falling back to sync render for user {user.id}")
                    with get_reusable_session("transaction_history", user_id=user.id) as sync_session:
                        db_user = sync_session.query(User).filter(User.telegram_id == int(user.id)).first()
                        if db_user:
                            message, reply_markup, fingerprint, tx_mapping = await render_transaction_history(
                                db_user.id, page, filter_type
                            )
                        else:
                            raise Exception("User not found in fallback")
        
        # STEP 5: Render from cached data (fast path)
        if cached_data:
            message, reply_markup, fingerprint, tx_mapping = render_prefetched_transaction_history(
                cached_data, page, filter_type
            )
            logger.debug(f"📦 TX_CACHE_HIT: Rendered page {page} from cache")
        else:
            # Already rendered in fallback path
            pass
        
        # Store transaction mapping for detail navigation
        context.user_data['tx_mapping'] = tx_mapping
        
        # Send/update message
        if query:
            result = await safe_edit_message_text(query, message, reply_markup=reply_markup, parse_mode='Markdown')
            message_id = query.message.message_id
            chat_id = query.message.chat_id
        else:
            result = await update.message.reply_text(message, reply_markup=reply_markup, parse_mode='Markdown')
            message_id = result.message_id
            chat_id = result.chat_id
        
        # Cancel any existing auto-refresh for this user
        await cancel_tx_auto_refresh(user.id, context)
        
        # Start auto-refresh job
        job_data = {
            'chat_id': chat_id,
            'message_id': message_id,
            'user_id': user.id,
            'db_user_id': cached_data.get('user_id') if cached_data else db_user.id,
            'page': page,
            'filter_type': filter_type,
            'fingerprint': fingerprint,
            'cycle_count': 0,
            'tx_mapping': tx_mapping
        }
        
        # Schedule auto-refresh job
        job = context.job_queue.run_repeating(
            callback=refresh_tx_history_job,
            interval=AUTO_REFRESH_INTERVAL,
            first=AUTO_REFRESH_INTERVAL,
            data=job_data,
            name=f"tx_refresh_{user.id}"
        )
        
        # Store job reference for cleanup
        ACTIVE_TX_REFRESHES[user.id] = job
        logger.info(f"🔄 Started auto-refresh for user {user.id}, message {message_id}")
            
        return TRANSACTION_FILTER
            
    except Exception as e:
        logger.error(f"Error showing transaction history: {e}", exc_info=True)
        error_msg = "❌ Error loading transaction history. Please try again."
        
        if query:
            await safe_edit_message_text(query, error_msg)
        else:
            await update.message.reply_text(error_msg)
        return ConversationHandler.END


async def refresh_tx_history_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Auto-refresh transaction history job with smart change detection"""
    job = context.job
    data = job.data
    
    try:
        # Increment cycle count
        data['cycle_count'] += 1
        
        # Stop after max cycles (2 minutes) - fix off-by-one
        if data['cycle_count'] >= AUTO_REFRESH_MAX_CYCLES:
            logger.info(f"🔄 Auto-refresh completed for user {data['user_id']} after {AUTO_REFRESH_MAX_CYCLES} cycles")
            await cancel_tx_auto_refresh(data['user_id'], context)
            return
        
        # Get fresh transaction data using the same render function
        message, reply_markup, new_fingerprint, tx_mapping = await render_transaction_history(
            data['db_user_id'], data['page'], data['filter_type']
        )
        
        # Update transaction mapping in job data and context for reliability
        data['tx_mapping'] = tx_mapping
        
        # Only update if data changed (fingerprint-based detection)
        if new_fingerprint != data['fingerprint']:
            try:
                await context.bot.edit_message_text(
                    chat_id=data['chat_id'],
                    message_id=data['message_id'],
                    text=message,
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
                # Update stored fingerprint
                data['fingerprint'] = new_fingerprint
                logger.info(f"🔄 Updated transaction history for user {data['user_id']} (cycle {data['cycle_count']})")
            except BadRequest as e:
                if "message is not modified" in str(e).lower():
                    # Message unchanged, continue silently
                    pass
                else:
                    logger.warning(f"🔄 Auto-refresh error for user {data['user_id']}: {e}")
                    await cancel_tx_auto_refresh(data['user_id'], context)
        else:
            logger.debug(f"🔄 No changes detected for user {data['user_id']} (cycle {data['cycle_count']})")
            
    except Exception as e:
        logger.error(f"🔄 Auto-refresh job error for user {data['user_id']}: {e}")
        await cancel_tx_auto_refresh(data['user_id'], context)


async def cancel_tx_auto_refresh(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancel active auto-refresh job for a user"""
    if user_id in ACTIVE_TX_REFRESHES:
        job = ACTIVE_TX_REFRESHES[user_id]
        job.schedule_removal()
        del ACTIVE_TX_REFRESHES[user_id]
        logger.info(f"🔄 Cancelled auto-refresh for user {user_id}")


async def handle_transaction_filter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle transaction filter changes"""
    query = update.callback_query
    if not query:
        return ConversationHandler.END
    
    data = query.data
    logger.info(f"🎯 TRANSACTION_FILTER_HANDLER: Received callback_data={data}")
    
    if data.startswith("tx_filter_"):
        filter_type = data.replace("tx_filter_", "")
        context.user_data['tx_filter'] = filter_type
        context.user_data['tx_page'] = 1  # Reset to first page
        # Cancel existing auto-refresh before starting new one
        await cancel_tx_auto_refresh(query.from_user.id, context)
        return await show_transaction_history(update, context)
    
    elif data.startswith("tx_page_"):
        page = int(data.replace("tx_page_", ""))
        context.user_data['tx_page'] = page
        # Cancel existing auto-refresh before starting new one
        await cancel_tx_auto_refresh(query.from_user.id, context)
        return await show_transaction_history(update, context)
    
    elif data == "transaction_history":
        # Refresh current view
        await cancel_tx_auto_refresh(query.from_user.id, context)
        return await show_transaction_history(update, context)
    
    elif data.startswith("tx_detail_"):
        # Handle transaction detail buttons
        index = data.replace("tx_detail_", "")
        # Resolve index to actual transaction ID - try both context and active refresh job
        tx_mapping = context.user_data.get('tx_mapping', {})
        
        # If not in context, try to get from active refresh job data
        if not tx_mapping and query.from_user.id in ACTIVE_TX_REFRESHES:
            job = ACTIVE_TX_REFRESHES[query.from_user.id]
            tx_mapping = job.data.get('tx_mapping', {})
        
        transaction_id = tx_mapping.get(index)
        
        if not transaction_id:
            await safe_answer_callback_query(query, "❌ Transaction not found")
            return TRANSACTION_FILTER
            
        await cancel_tx_auto_refresh(query.from_user.id, context)
        return await show_transaction_detail(update, context, transaction_id)
    
    elif data == "main_menu":
        # Cancel auto-refresh when navigating away
        await cancel_tx_auto_refresh(query.from_user.id, context)
        # Return to main menu
        from handlers.menu import show_hamburger_menu
        return await show_hamburger_menu(update, context)
    
    return TRANSACTION_FILTER


async def show_transaction_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, transaction_id: str) -> int:
    """Show detailed view of a specific transaction"""
    user = update.effective_user
    query = update.callback_query
    
    if not user:
        error_msg = "❌ User not found"
        if query:
            await safe_answer_callback_query(query, error_msg)
        else:
            await update.message.reply_text(error_msg)
        return ConversationHandler.END
    
    try:
        with get_reusable_session("transaction_detail", user_id=user.id) as session:
            # Get user from database
            db_user = session.query(User).filter(User.telegram_id == int(user.id)).first()
            if not db_user:
                error_msg = "❌ User not found in database."
                if query:
                    await safe_edit_message_text(query, error_msg)
                else:
                    await update.message.reply_text(error_msg)
                return ConversationHandler.END
            
            # Find transaction in both Transaction and UnifiedTransaction tables
            transaction = session.query(Transaction).filter(
                Transaction.transaction_id == transaction_id,
                Transaction.user_id == db_user.id
            ).first()
            
            unified_transaction = None
            if not transaction:
                # Check UnifiedTransaction table using reference_id or id
                unified_transaction = session.query(UnifiedTransaction).filter(
                    or_(
                        UnifiedTransaction.reference_id == transaction_id,
                        UnifiedTransaction.id == (transaction_id if transaction_id.isdigit() else -1)
                    ),
                    UnifiedTransaction.user_id == db_user.id
                ).first()
            
            if not transaction and not unified_transaction:
                error_msg = "❌ Transaction not found or not accessible."
                if query:
                    await safe_edit_message_text(query, error_msg)
                else:
                    await update.message.reply_text(error_msg)
                return ConversationHandler.END
            
            # Use the found transaction (prefer regular Transaction over UnifiedTransaction)
            tx = transaction if transaction else unified_transaction
            is_unified = unified_transaction is not None
            
            # Build detailed message for both transaction types
            if is_unified:
                amount_str = format_transaction_amount(tx.amount, tx.currency, tx.transaction_type)
                status_str = format_transaction_status(tx.status)
                desc = get_transaction_description(tx.transaction_type, external_id=getattr(tx, 'external_id', None), tx_data={'description': getattr(tx, 'description', '')})
                currency = tx.currency
                fee = getattr(tx, 'fee', None)
                provider = getattr(tx, 'provider', None)
                external_tx_id = getattr(tx, 'external_id', None)
                blockchain_hash = getattr(tx, 'reference_id', None)
                created_at = getattr(tx, 'created_at', None)
                updated_at = getattr(tx, 'updated_at', None)
                # FIX: Use safe identifier for unified transactions
                display_transaction_id = getattr(tx, 'reference_id', None) or str(tx.id)
            else:
                amount_str = format_transaction_amount(tx.amount, tx.currency, tx.transaction_type)
                status_str = format_transaction_status(tx.status)
                desc = get_transaction_description(tx.transaction_type, external_id=tx.external_id, tx_data={'description': getattr(tx, 'description', '')})
                currency = tx.currency
                fee = tx.fee
                provider = tx.provider
                external_tx_id = tx.external_tx_id
                blockchain_hash = tx.blockchain_tx_hash
                created_at = tx.created_at
                updated_at = tx.updated_at
                # Use actual transaction_id for regular transactions
                display_transaction_id = tx.transaction_id
            
            message = "📋 Transaction Details\n\n"
            message += f"Type: {escape_markdown(desc)}\n"
            message += f"Amount: {amount_str}\n"
            message += f"Status: {status_str}\n"
            message += f"Currency: {escape_markdown(currency)}\n"
            
            if fee and fee > 0:
                fee_str = f"{fee:.8f}".rstrip('0').rstrip('.')
                message += f"Fee: {fee_str} {escape_markdown(currency)}\n"
            
            if provider:
                message += f"Provider: {escape_markdown(provider)}\n"
            
            if external_tx_id:
                message += f"External ID: `{escape_markdown(external_tx_id)}`\n"
            
            if blockchain_hash:
                hash_display = blockchain_hash[:20] + "..." if len(blockchain_hash) > 20 else blockchain_hash
                message += f"Blockchain Hash: `{escape_markdown(hash_display)}`\n"
            
            message += f"Transaction ID: `{escape_markdown(display_transaction_id)}`\n"
            message += f"Created: {created_at.strftime('%Y-%m-%d %H:%M:%S UTC') if created_at else 'Unknown'}\n"
            
            if updated_at and updated_at != created_at:
                message += f"Updated: {updated_at.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
            
            # Add metadata if available
            if hasattr(transaction, 'metadata') and transaction.metadata:
                message += "\nAdditional Info:\n"
                # Handle metadata properly - check if it's a dictionary first
                try:
                    if isinstance(transaction.metadata, dict):
                        for key, value in transaction.metadata.items():
                            if key not in ['private_key', 'password', 'secret']:  # Skip sensitive data
                                message += f"• {key.title()}: {value}\n"
                    else:
                        # If metadata is not a dict, convert to string representation
                        message += f"• Metadata: {str(transaction.metadata)}\n"
                except Exception as e:
                    logger.warning(f"Could not process transaction metadata: {e}")
                    message += "• Metadata: Available but not displayable\n"
            
            keyboard = [
                [InlineKeyboardButton("🔙 Back to History", callback_data="transaction_history")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
            ]
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            if query:
                await safe_edit_message_text(query, message, reply_markup=reply_markup, parse_mode='Markdown')
            else:
                await update.message.reply_text(message, reply_markup=reply_markup, parse_mode='Markdown')
            
            return TRANSACTION_DETAIL
            
    except Exception as e:
        logger.error(f"Error showing transaction detail: {e}")
        error_msg = "❌ Error loading transaction details. Please try again."
        if query:
            await safe_edit_message_text(query, error_msg)
        else:
            await update.message.reply_text(error_msg)
        return ConversationHandler.END


def register_transaction_history_handlers(application):
    """Register transaction history handlers with the bot application"""
    import logging
    logger = logging.getLogger(__name__)
    logger.info("🚀 STARTING transaction history handler registration...")
    
    from telegram.ext import CallbackQueryHandler, MessageHandler, filters
    
    # Callback handlers for transaction history
    application.add_handler(CallbackQueryHandler(
        show_transaction_history, 
        pattern="^transaction_history$"
    ))
    
    application.add_handler(CallbackQueryHandler(
        handle_transaction_filter,
        pattern="^tx_filter_.*$"
    ))
    
    application.add_handler(CallbackQueryHandler(
        handle_transaction_filter,
        pattern="^tx_page_.*$"
    ))
    
    application.add_handler(CallbackQueryHandler(
        handle_transaction_filter,
        pattern="^tx_detail_.*$"
    ))
    
    application.add_handler(CallbackQueryHandler(
        show_transaction_history,
        pattern="^transaction_history$"
    ))
    
    # CRITICAL: MessageHandler for dynamic /tx_<id> commands using regex
    application.add_handler(MessageHandler(
        filters.Regex(r'^/tx_[A-Za-z0-9\-_]+$'),
        handle_transaction_detail_command
    ))
    
    logger.info("📋 Transaction history handlers registered")


async def handle_transaction_detail_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /tx_<transaction_id> commands"""
    message = update.message
    if not message or not message.text:
        return ConversationHandler.END
    
    # Extract transaction ID from command
    import re
    command_text = message.text.strip()
    match = re.match(r'^/tx_([A-Za-z0-9\-_]+)$', command_text)
    
    if not match:
        await message.reply_text("❌ Invalid transaction command format. Use: /tx_<transaction_id>")
        return ConversationHandler.END
    
    transaction_id = match.group(1)
    logger.info(f"User {update.effective_user.id if update.effective_user else 'unknown'} requested transaction details for {transaction_id}")
    
    # Show transaction detail
    return await show_transaction_detail(update, context, transaction_id)


async def handle_main_menu_return(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle return to main menu from transaction history"""
    user = update.effective_user
    if user:
        await cancel_tx_auto_refresh(user.id, context)
    from handlers.menu import show_hamburger_menu
    return await show_hamburger_menu(update, context)