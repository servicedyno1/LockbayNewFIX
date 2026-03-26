"""Helper utilities for the Telegram Escrow Bot"""

import uuid
import re
import time
import threading
import logging
from datetime import datetime
from typing import Optional, Dict, Tuple
from collections import OrderedDict
from telegram import User as TelegramUser
from models import User, Escrow
from config import Config
from utils.constants import CURRENCY_EMOJIS, STATUS_EMOJIS, DATETIME_FORMAT
from utils.universal_id_generator import UniversalIDGenerator

logger = logging.getLogger(__name__)


async def check_user_blocked_async(user_id: int, session) -> bool:
    """Check if user is blocked. Returns True if blocked, False otherwise."""
    try:
        from sqlalchemy import select
        stmt = select(User.is_blocked).where(User.id == user_id)
        result = await session.execute(stmt)
        is_blocked = result.scalar() or False
        return is_blocked
    except Exception as e:
        logger.error(f"Error checking blocked status for user {user_id}: {e}")
        return False


def generate_utid(entity_type: str) -> str:
    """
    DEPRECATED: Use UniversalIDGenerator.generate_id() instead.
    
    This function now delegates to UniversalIDGenerator for consistency.
    Kept for backward compatibility only.
    
    Args:
        entity_type: Two-character entity type (ES, TX, EX, etc.)
        
    Returns:
        Unified ID from UniversalIDGenerator
    """
    # Map old prefixes to new entity types for backward compatibility
    prefix_mapping = {
        'ES': 'escrow',
        'TX': 'transaction', 
        'EX': 'exchange',
        'CO': 'cashout',
        'RF': 'refund',
        'DP': 'dispute',
        'WD': 'wallet_deposit',
        'WT': 'wallet_transfer',
        'PM': 'payment',
        'FE': 'fee',
        'US': 'user_session',
        'OT': 'otp_token',
        'IT': 'invite_token',
        'VF': 'verification',
        'JB': 'job_task',
        'AL': 'audit_log',
        'NT': 'notification',
        'ER': 'error_report',
        'AA': 'admin_action',
        'ST': 'support_ticket',
        'BC': 'broadcast',
        'WH': 'webhook_event',
        'AR': 'api_request',
        'XR': 'external_ref'
    }
    
    # Convert entity_type to uppercase for consistent mapping
    entity_type_upper = entity_type.upper()
    
    # Map to full entity name or use the input if it's already a full name
    if entity_type_upper in prefix_mapping:
        mapped_entity = prefix_mapping[entity_type_upper]
    elif entity_type.lower() in UniversalIDGenerator.ENTITY_PREFIXES:
        mapped_entity = entity_type.lower()
    else:
        # Default to transaction for unknown types
        mapped_entity = 'transaction'
    
    # Delegate to UniversalIDGenerator for consistent ID generation
    return UniversalIDGenerator.generate_id(mapped_entity)


# Legacy compatibility functions - All delegate to UniversalIDGenerator
def generate_escrow_id() -> str:
    """Legacy function - use UniversalIDGenerator.generate_escrow_id() instead"""
    return UniversalIDGenerator.generate_escrow_id()

def generate_transaction_id() -> str:
    """Legacy function - use UniversalIDGenerator.generate_transaction_id() instead"""
    return UniversalIDGenerator.generate_transaction_id()

def generate_exchange_id() -> str:
    """Legacy function - use UniversalIDGenerator.generate_exchange_id() instead"""
    return UniversalIDGenerator.generate_exchange_id()

def generate_cashout_id() -> str:
    """Legacy function - use UniversalIDGenerator.generate_cashout_id() instead"""
    return UniversalIDGenerator.generate_cashout_id()

def generate_dispute_id() -> str:
    """Legacy function - use UniversalIDGenerator.generate_dispute_id() instead"""
    return UniversalIDGenerator.generate_dispute_id()

def generate_refund_id() -> str:
    """Legacy function - use UniversalIDGenerator.generate_refund_id() instead"""
    return UniversalIDGenerator.generate_refund_id()


def generate_unique_id(prefix: str = "") -> str:
    """Legacy function - use UniversalIDGenerator.generate_id() instead"""
    if prefix and len(prefix) <= 2:
        # Use UniversalIDGenerator with clean prefix
        clean_prefix = prefix.upper().ljust(2, 'X')
        return UniversalIDGenerator.generate_id('transaction', custom_prefix=clean_prefix)
    
    # Fallback for non-standard prefixes (maintain backward compatibility)
    timestamp = int(datetime.utcnow().timestamp())
    random_part = uuid.uuid4().hex[:8].upper()
    
    if prefix:
        return f"{prefix.upper()}_{timestamp % 1000000:06d}_{random_part}"
    else:
        return f"{timestamp % 1000000:06d}_{random_part}"


class EmailValidationCache:
    """Thread-safe LRU cache for email validation results with TTL support"""
    
    def __init__(self, max_size: int = 1000, ttl_seconds: int = 3600):
        """Initialize cache with max size and TTL (time to live)
        
        Args:
            max_size: Maximum number of cached validations
            ttl_seconds: Time to live for cached results (default: 1 hour)
        """
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self.cache: OrderedDict[str, Tuple[bool, float]] = OrderedDict()
        self.lock = threading.RLock()  # Use RLock for potential recursive calls
        self.hits = 0
        self.misses = 0
        self.evictions = 0
    
    def get(self, email: str) -> Optional[bool]:
        """Get cached validation result if still valid"""
        with self.lock:
            if email in self.cache:
                result, timestamp = self.cache[email]
                
                # Check if result is still valid (not expired)
                if time.time() - timestamp < self.ttl_seconds:
                    # Move to end (LRU behavior)
                    self.cache.move_to_end(email)
                    self.hits += 1
                    return result
                else:
                    # Expired, remove from cache
                    del self.cache[email]
            
            self.misses += 1
            return None
    
    def put(self, email: str, result: bool):
        """Store validation result in cache"""
        with self.lock:
            # Remove existing entry if present
            if email in self.cache:
                del self.cache[email]
            
            # Add new entry
            self.cache[email] = (result, time.time())
            
            # Evict oldest entries if cache is full
            while len(self.cache) > self.max_size:
                oldest_email = next(iter(self.cache))
                del self.cache[oldest_email]
                self.evictions += 1
    
    def clear(self):
        """Clear all cached entries"""
        with self.lock:
            self.cache.clear()
            self.hits = 0
            self.misses = 0
            self.evictions = 0
    
    def get_stats(self) -> Dict[str, int]:
        """Get cache performance statistics"""
        with self.lock:
            total_requests = self.hits + self.misses
            hit_rate = (self.hits / total_requests * 100) if total_requests > 0 else 0
            
            return {
                'size': len(self.cache),
                'max_size': self.max_size,
                'hits': self.hits,
                'misses': self.misses,
                'evictions': self.evictions,
                'hit_rate_percent': round(hit_rate, 2),
                'total_requests': total_requests
            }


# Global email validation cache instance
_email_cache = EmailValidationCache(max_size=1000, ttl_seconds=3600)  # 1 hour TTL


def validate_email(email: str) -> bool:
    """Validate email address format with proper internationalization support and caching
    
    This function now includes intelligent caching to avoid re-validating the same
    email addresses, significantly improving performance for repeated validations.
    """
    if not email or not isinstance(email, str):
        return False
    
    # Normalize email for consistent caching (lowercase domain)
    try:
        local_part, domain_part = email.split('@')
        normalized_email = f"{local_part}@{domain_part.lower()}"
    except ValueError:
        # Invalid format (no @ or multiple @)
        return False
    
    # PERFORMANCE OPTIMIZATION: Check cache first
    cached_result = _email_cache.get(normalized_email)
    if cached_result is not None:
        return cached_result
    
    # Perform actual validation if not cached
    is_valid = _validate_email_internal(email)
    
    # Cache the result for future use
    _email_cache.put(normalized_email, is_valid)
    
    return is_valid


def _validate_email_internal(email: str) -> bool:
    """Internal email validation logic (not cached)"""
    # Basic format check - must have exactly one @ symbol
    if email.count('@') != 1:
        return False
    
    local_part, domain_part = email.split('@')
    
    # Local part validation
    if not local_part or len(local_part) > 64:
        return False
        
    # Reject consecutive dots
    if '..' in local_part:
        return False
        
    # Domain part validation
    if not domain_part or len(domain_part) > 253:
        return False
        
    # Domain must contain at least one dot for TLD
    if '.' not in domain_part:
        return False
        
    # Reject consecutive dots in domain
    if '..' in domain_part:
        return False
    
    # Enhanced pattern with Unicode support for internationalized domains
    # Allow Unicode characters in domain (for IDN domains like тест@домен.рф)
    try:
        # Try to encode domain as ASCII (will fail for IDN domains)
        domain_part.encode('ascii')
        # Use strict ASCII pattern for ASCII domains
        pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
        return re.match(pattern, email) is not None
    except UnicodeEncodeError:
        # Handle internationalized domain names (IDN)
        # For Unicode domains, validate parts separately
        
        # Local part should allow Unicode characters for international emails
        # Be more permissive for Unicode local parts
        if not local_part or len(local_part) == 0:
            return False
        
        # Domain can contain Unicode characters - check basic structure
        domain_parts = domain_part.split('.')
        if len(domain_parts) < 2:
            return False
        
        # Each domain part should be non-empty and reasonable length
        for part in domain_parts:
            if not part or len(part) > 63:
                return False
        
        # Last part (TLD) should be at least 2 characters
        if len(domain_parts[-1]) < 2:
            return False
            
        # Accept Unicode domains as valid
        return True


def get_email_validation_cache_stats() -> Dict[str, int]:
    """Get email validation cache performance statistics
    
    Returns:
        Dictionary with cache statistics including hit rate, size, etc.
    """
    return _email_cache.get_stats()


def clear_email_validation_cache():
    """Clear the email validation cache (useful for testing or maintenance)"""
    _email_cache.clear()


def validate_username(username: str) -> bool:
    """Validate Telegram username format"""
    if not username:
        return False

    # Remove @ if present
    username = username.lstrip("@")

    # Telegram username requirements:
    # - Must start with a letter (a-z, A-Z)
    # - 5-32 characters total
    # - Can contain letters, numbers, and underscores
    # - Cannot be all numbers
    pattern = r"^[a-zA-Z][a-zA-Z0-9_]{4,31}$"

    # Additional check: reject pure numeric usernames
    if username.isdigit():
        return False

    return re.match(pattern, username) is not None


def validate_crypto_address(address: str, currency: str, network: str) -> bool:
    """Basic crypto address validation"""
    if not address:
        return False

    # Basic validation patterns (in production, use proper libraries)
    patterns = {
        "BTC": r"^[13][a-km-zA-HJ-NP-Z1-9]{25,34}$|^bc1[a-z0-9]{39,59}$",
        "ETH": r"^0x[a-fA-F0-9]{40}$",
        "USDT_TRC20": r"^T[A-Za-z1-9]{33}$",
        "USDT_ERC20": r"^0x[a-fA-F0-9]{40}$",
        "USDT_BEP20": r"^0x[a-fA-F0-9]{40}$",
        "USDC_ERC20": r"^0x[a-fA-F0-9]{40}$",
        "USDC_BEP20": r"^0x[a-fA-F0-9]{40}$",
    }

    pattern_key = f"{currency}_{network}" if network else currency
    pattern = patterns.get(pattern_key, patterns.get(currency))

    if pattern:
        return re.match(pattern, address) is not None

    return len(address) >= 20  # Basic length check


def format_crypto_amount(amount) -> str:
    """Format crypto amount with appropriate precision"""
    from decimal import Decimal

    if isinstance(amount, (int, float)):
        amount = Decimal(str(amount))

    # Format based on magnitude
    if amount >= 1:
        return f"{amount:.6f}"
    elif amount >= 0.001:
        return f"{amount:.8f}"
    else:
        return f"{amount:.12f}"


def calculate_fee(amount: float) -> float:
    """Calculate escrow fee with mathematical precision"""
    from decimal import Decimal, ROUND_HALF_UP

    # Use Decimal for precise financial calculations
    amount_decimal = Decimal(str(amount))
    fee_percentage = Decimal(str(Config.ESCROW_FEE_PERCENTAGE)) / Decimal("100")
    fee = amount_decimal * fee_percentage
    # Round to 2 decimal places for USD currency
    return float(fee.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def format_amount(amount: float, currency: str) -> str:
    """Format amount with currency"""
    emoji = CURRENCY_EMOJIS.get(currency, "💰")
    if currency in ["BTC", "ETH"]:
        return f"{emoji} {amount:.6f} {currency}"
    else:
        return f"{emoji} {amount:.2f} {currency}"


def format_datetime(dt: datetime) -> str:
    """Format datetime for display"""
    return dt.strftime(DATETIME_FORMAT)


def get_time_ago(dt: datetime) -> str:
    """Get human-readable time ago - handles both timezone-aware and naive datetimes"""
    from datetime import timezone
    
    # Ensure we're working with timezone-aware UTC datetimes
    if dt.tzinfo is None:
        # If input is naive, assume it's UTC
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        # Convert to UTC if it has a different timezone
        dt = dt.astimezone(timezone.utc)
    
    # Get current time in UTC (timezone-aware)
    now = datetime.now(timezone.utc)
    
    # Now we can safely subtract
    diff = now - dt

    if diff.days > 0:
        return f"{diff.days} day{'s' if diff.days != 1 else ''}"
    elif diff.seconds >= 3600:
        hours = diff.seconds // 3600
        return f"{hours} hour{'s' if hours != 1 else ''}"
    elif diff.seconds >= 60:
        minutes = diff.seconds // 60
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    else:
        return "just now"


def create_deep_link(escrow_id: str, token: str) -> str:
    """Create deep link for escrow invitation"""
    return f"https://t.me/{Config.BOT_USERNAME}?start=escrow_{escrow_id}_{token}"


def parse_start_parameter(parameter: str) -> Optional[Dict[str, str]]:
    """Parse start parameter for deep links"""
    if not parameter:
        return None

    # Handle underscore-separated format: escrow_E55228604C7DC_c6b202aa163048b2
    parts = parameter.split("_")
    if len(parts) >= 3 and parts[0] == "escrow":
        # Join remaining parts in case token contains underscores
        token = "_".join(parts[2:]) if len(parts) > 3 else parts[2]
        return {"type": "escrow_invitation", "escrow_id": parts[1], "token": token}

    # Handle invite format: invite_TOKEN (email invitation tokens)
    if len(parts) >= 2 and parts[0] == "invite":
        token = "_".join(parts[1:]) if len(parts) > 2 else parts[1]
        return {"type": "email_invitation", "token": token}

    # Handle concatenated format: escrowE55228604C7DCc6b202aa163048b2
    if parameter.startswith("escrow") and len(parameter) > 6:
        param_content = parameter[6:]  # Remove 'escrow' prefix
        # Try to split escrow ID and token
        # Escrow IDs are typically 13 characters (E + 12 chars)
        if len(param_content) > 13:
            escrow_id = param_content[:13]
            token = param_content[13:]
            return {"type": "escrow_invitation", "escrow_id": escrow_id, "token": token}

    # Handle plain invite token format: inviteTOKEN
    if parameter.startswith("invite") and len(parameter) > 6:
        token = parameter[6:]  # Remove 'invite' prefix
        return {"type": "email_invitation", "token": token}

    return None


def get_user_display_name(user: Optional[User]) -> str:
    """Get user display name (handles None users and Mock objects safely)"""
    if user is None:
        return "❌ User not found"
    
    # Handle Mock objects gracefully to prevent format string errors
    try:
        username = getattr(user, "username", None)
        first_name = getattr(user, "first_name", None)
        last_name = getattr(user, "last_name", None)
        telegram_id = getattr(user, "telegram_id", None)
        
        # Convert Mock objects to strings to prevent format errors
        if hasattr(username, '_mock_name') and username is not None:
            username = str(username) if username else None
        if hasattr(first_name, '_mock_name') and first_name is not None:
            first_name = str(first_name) if first_name else None
        if hasattr(last_name, '_mock_name') and last_name is not None:
            last_name = str(last_name) if last_name else None
        if hasattr(telegram_id, '_mock_name') and telegram_id is not None:
            telegram_id = str(telegram_id) if telegram_id else None

        if username and username != "None":
            return f"@{username}"
        elif first_name and first_name != "None" and last_name and last_name != "None":
            return f"{first_name} {last_name}"
        elif first_name and first_name != "None":
            return first_name
        else:
            return f"User {telegram_id or 'unknown'}"
    except Exception:
        # Fallback for any formatting issues
        return f"User {getattr(user, 'id', 'unknown')}"


def format_escrow_summary(
    escrow: Escrow, show_fee_breakdown: bool = True, use_html: bool = True
) -> str:
    """Format escrow summary for display"""
    status_emoji = STATUS_EMOJIS.get(str(escrow.status), "❓")

    # Use HTML or Markdown formatting based on parameter
    if use_html:
        bold_start, bold_end = "*", "*"
    else:
        bold_start, bold_end = "", ""

    summary = f"{status_emoji} {bold_start}Escrow #{escrow.escrow_id}{bold_end}\n\n"

    if escrow.buyer:
        summary += (
            f"👤 {bold_start}Buyer:{bold_end} {get_user_display_name(escrow.buyer)}\n"
        )

    if escrow.seller:
        summary += (
            f"🛒 {bold_start}Seller:{bold_end} {get_user_display_name(escrow.seller)}\n"
        )
    elif escrow.seller_phone is not None:
        # Display phone number seller with mobile icon
        summary += f"🛒 {bold_start}Seller:{bold_end} 📱 {escrow.seller_phone}\n"
    elif escrow.seller_email is not None:
        # Display email seller with email icon
        summary += f"🛒 {bold_start}Seller:{bold_end} 📧 {escrow.seller_email}\n"
    elif escrow.seller_username is not None:
        # Display username seller with username icon
        seller_display = (
            escrow.seller_username
            if "@" in escrow.seller_username
            else f"@{escrow.seller_username}"
        )
        summary += f"🛒 {bold_start}Seller:{bold_end} 👤 {seller_display}\n"

    amount_attr = getattr(escrow, "amount", None)
    amount_value = float(amount_attr) if amount_attr is not None else 0.0
    summary += f"💰 {bold_start}Amount:{bold_end} ${amount_value:.2f}\n"

    if show_fee_breakdown:
        fee_attr = getattr(escrow, "fee_amount", None)
        total_attr = getattr(escrow, "total_amount", None)
        fee_value = float(fee_attr) if fee_attr is not None else 0.0
        total_value = float(total_attr) if total_attr is not None else 0.0
        summary += f"💳 {bold_start}Fee:{bold_end} ${fee_value:.2f}\n"
        summary += f"📊 {bold_start}Total:{bold_end} ${total_value:.2f}\n"

    if escrow.network is not None:
        summary += f"🌐 {bold_start}Network:{bold_end} {escrow.network}\n"

    summary += f"📝 {bold_start}Description:{bold_end} {escrow.description}\n"

    deadline_attr = getattr(escrow, "delivery_deadline", None)
    if deadline_attr is not None:
        deadline_dt = deadline_attr if hasattr(deadline_attr, "strftime") else None
        if deadline_dt:
            summary += (
                f"⏰ {bold_start}Deadline:{bold_end} {format_datetime(deadline_dt)}\n"
            )

    created_attr = getattr(escrow, "created_at", None)
    created_dt = (
        created_attr if created_attr and hasattr(created_attr, "strftime") else None
    )
    if created_dt:
        summary += f"📅 {bold_start}Created:{bold_end} {format_datetime(created_dt)}\n"

    return summary


def get_escrow_status_text(status: str) -> str:
    """Get human-readable escrow status"""
    status_map = {
        "pending_seller": "Waiting for seller acceptance",
        "pending_deposit": "Waiting for deposit",
        "active": "Active - awaiting completion",
        "completed": "Completed successfully",
        "disputed": "Under dispute review",
        "cancelled": "Cancelled",
        "refunded": "Refunded to buyer",
        "expired": "Expired",
    }
    return status_map.get(status, status.title())


def truncate_text(text: str, max_length: int = 100) -> str:
    """Truncate text with ellipsis"""
    if len(text) <= max_length:
        return text
    return text[: max_length - 3] + "..."


def is_valid_amount(amount: str, currency: str) -> tuple[bool, float]:
    """Validate and parse amount"""
    try:
        amount_float = float(amount)
        if amount_float <= 0:
            return False, 0.0

        min_amount = getattr(Config, "MIN_ESCROW_AMOUNT_USD", 5.0)
        if amount_float < min_amount:
            return False, 0.0

        return True, amount_float
    except ValueError:
        return False, 0.0


# Deprecated: generate_deposit_address moved to services/crypto.py with BlockBee integration
# This function is no longer used as we now use CryptoService.generate_deposit_address()


def update_user_from_telegram(db_user: User, tg_user: TelegramUser) -> User:
    """Update user info from Telegram user object and auto-sync profile slug"""
    from database import SessionLocal
    import re
    import random
    import string
    
    # Track if username or first_name changed (for profile slug update)
    username_changed = db_user.username != tg_user.username
    first_name_changed = db_user.first_name != tg_user.first_name
    
    # Update basic fields
    setattr(db_user, "username", tg_user.username)
    setattr(db_user, "first_name", tg_user.first_name)
    setattr(db_user, "last_name", tg_user.last_name)
    setattr(db_user, "last_activity", datetime.utcnow())
    
    # Auto-sync profile_slug if username or first_name changed
    if username_changed or first_name_changed:
        try:
            session = SessionLocal()
            
            def slugify_text(text: str) -> str:
                text = text.lower().strip()
                text = re.sub(r'[^\w\s-]', '', text)
                text = re.sub(r'[-\s]+', '_', text)
                return text[:30]
            
            def gen_suffix(length: int = 6) -> str:
                return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))
            
            # Generate base slug
            if tg_user.username:
                base_slug = slugify_text(tg_user.username.lstrip('@'))
            elif tg_user.first_name:
                base_slug = slugify_text(tg_user.first_name)
            else:
                base_slug = f"user_{db_user.telegram_id}"
            
            # Check uniqueness and add suffix if needed
            candidate = base_slug
            for attempt in range(10):
                existing = session.query(User).filter(
                    User.profile_slug == candidate,
                    User.id != db_user.id  # Exclude current user
                ).first()
                
                if not existing:
                    setattr(db_user, "profile_slug", candidate)
                    logger.info(f"🔄 Auto-synced profile slug to '{candidate}' for user {db_user.telegram_id}")
                    break
                candidate = f"{base_slug}_{gen_suffix()}"
            
            session.close()
        except Exception as e:
            logger.error(f"Error auto-syncing profile slug for user {db_user.telegram_id}: {e}")
    
    return db_user


async def async_update_user_from_telegram(db_user: User, tg_user: TelegramUser, session=None) -> User:
    """
    ASYNC version: Update user info from Telegram user object and auto-sync profile slug
    
    Args:
        db_user: User model instance
        tg_user: Telegram User object
        session: Optional AsyncSession - if provided, reuses session for slug uniqueness check
        
    Returns:
        Updated User instance
        
    Note: If session is None, falls back to sync version for backward compatibility
    """
    import re
    import random
    import string
    from sqlalchemy import select
    
    # Track if username or first_name changed (for profile slug update)
    username_changed = db_user.username != tg_user.username
    first_name_changed = db_user.first_name != tg_user.first_name
    
    # Update basic fields
    setattr(db_user, "username", tg_user.username)
    setattr(db_user, "first_name", tg_user.first_name)
    setattr(db_user, "last_name", tg_user.last_name)
    setattr(db_user, "last_activity", datetime.utcnow())
    
    # Auto-sync profile_slug if username or first_name changed
    if username_changed or first_name_changed:
        try:
            def slugify_text(text: str) -> str:
                text = text.lower().strip()
                text = re.sub(r'[^\w\s-]', '', text)
                text = re.sub(r'[-\s]+', '_', text)
                return text[:30]
            
            def gen_suffix(length: int = 6) -> str:
                return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))
            
            # Generate base slug
            if tg_user.username:
                base_slug = slugify_text(tg_user.username.lstrip('@'))
            elif tg_user.first_name:
                base_slug = slugify_text(tg_user.first_name)
            else:
                base_slug = f"user_{db_user.telegram_id}"
            
            # Check uniqueness and add suffix if needed (using async session if provided)
            candidate = base_slug
            for attempt in range(10):
                if session:
                    # ASYNC path: Use provided session
                    result = await session.execute(
                        select(User).where(
                            User.profile_slug == candidate,
                            User.id != db_user.id  # Exclude current user
                        )
                    )
                    existing = result.scalar_one_or_none()
                else:
                    # SYNC fallback for backward compatibility
                    from database import SessionLocal
                    sync_session = SessionLocal()
                    existing = sync_session.query(User).filter(
                        User.profile_slug == candidate,
                        User.id != db_user.id
                    ).first()
                    sync_session.close()
                
                if not existing:
                    setattr(db_user, "profile_slug", candidate)
                    logger.info(f"🔄 Auto-synced profile slug to '{candidate}' for user {db_user.telegram_id}")
                    break
                candidate = f"{base_slug}_{gen_suffix()}"
                
        except Exception as e:
            logger.error(f"Error auto-syncing profile slug for user {db_user.telegram_id}: {e}")
    
    return db_user


def get_file_type_from_mime(mime_type: str) -> str:
    """Get file type category from MIME type"""
    if mime_type.startswith("image/"):
        return "image"
    elif mime_type.startswith("video/"):
        return "video"
    elif mime_type.startswith("audio/"):
        return "audio"
    elif mime_type in ["application/pdf", "text/plain", "application/msword"]:
        return "document"
    elif mime_type in ["application/zip", "application/x-rar"]:
        return "archive"
    else:
        return "other"


def mask_address(address: str) -> str:
    """Mask crypto address for display"""
    if len(address) <= 10:
        return address
    return f"{address[:6]}...{address[-4:]}"


def format_deposit_address_with_qr_art(address: str, currency: str) -> str:
    """Format deposit address information (QR code sent separately)"""
    CURRENCY_EMOJIS.get(currency, "💰")

    result = f"""📍 {currency} Deposit Address

`{address}`

💡 Instructions:
• Tap address above to copy
• Send only {currency} to this address"""

    return result


def format_user_name(user) -> str:
    """Format user name from Telegram user object or string"""
    if hasattr(user, "first_name"):
        # Telegram user object
        name = user.first_name or ""
        if hasattr(user, "last_name") and user.last_name:
            name += f" {user.last_name}"
        if hasattr(user, "username") and user.username:
            return f"{name} (@{user.username})"
        return name
    elif isinstance(user, str):
        # String representation
        return user
    else:
        return "User"


def get_currency_emoji(currency: str) -> str:
    """Get emoji for a given currency code"""
    return CURRENCY_EMOJIS.get(currency, "💰")


def shorten_bank_name(bank_name: str, max_length: int = 20) -> str:
    """
    Apply consistent bank name shortening across the entire application.
    Single source of truth for bank name display formatting.

    Args:
        bank_name (str): Original bank name
        max_length (int): Maximum length after shortening (default: 20)

    Returns:
        str: Shortened bank name
    """
    if not bank_name:
        return bank_name

    short_name = bank_name
    # Apply consistent shortening rules
    short_name = short_name.replace("Microfinance Bank", "MFB")
    short_name = short_name.replace("Plc", "")
    short_name = short_name.replace("Limited", "Ltd")
    short_name = short_name.replace("Nigeria", "")
    short_name = short_name.strip()

    # Truncate if still too long
    if len(short_name) > max_length:
        short_name = short_name[:max_length] + "..."

    return short_name


def create_user_wallet(user_id: int, session, currency: str = "USD", balance: float = 0.0):
    """
    Create a USD wallet for a user with consistent parameters.
    
    Args:
        user_id (int): User database ID
        session: SQLAlchemy session
        currency (str): Wallet currency (default: "USD")
        balance (float): Initial balance (default: 0.0)
    
    Returns:
        Wallet: Created wallet object
    """
    from models import Wallet
    from decimal import Decimal
    
    wallet = Wallet(
        user_id=user_id,
        currency=currency,
        balance=Decimal(str(balance))
    )
    session.add(wallet)
    return wallet


def ensure_user_has_wallet(user_id: int, session):
    """
    Ensure a user has a USD wallet, create one if missing.
    
    Args:
        user_id (int): User database ID
        session: SQLAlchemy session
    
    Returns:
        Wallet: Existing or newly created wallet
    """
    from models import Wallet
    
    # Check if wallet exists
    existing_wallet = session.query(Wallet).filter_by(user_id=user_id, currency="USD").first()
    
    if existing_wallet:
        return existing_wallet
    
    # Create wallet if missing
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"Creating missing USD wallet for user {user_id}")
    
    return create_user_wallet(user_id, session)


def generate_short_crypto_callback(address: str, action: str = "save") -> str:
    """
    Generate short callback data for crypto address buttons to stay under Telegram's 64-byte limit.
    
    Args:
        address (str): Crypto address (42 chars for ETH)
        action (str): Action type - 'save' or 'skip'
    
    Returns:
        str: Short callback format like "cs:16chars" or "ck:16chars" (max 19 chars)
    """
    import hashlib
    import logging
    
    logger = logging.getLogger(__name__)
    
    # Create hash of address for uniqueness
    address_hash = hashlib.sha256(address.encode()).hexdigest()[:16]
    
    # Store the mapping in context temporarily (handled by calling code)
    if action == "save":
        callback = f"cs:{address_hash}"  # cs = crypto save
    else:
        callback = f"ck:{address_hash}"  # ck = crypto skip
    
    logger.info(f"🔗 Generated short crypto callback: {callback} (length: {len(callback)}) for address: {address[:10]}...{address[-8:]}")
    
    if len(callback) >= 64:
        logger.error(f"❌ CRITICAL: Callback still too long: {len(callback)} bytes")
        # Emergency fallback
        import secrets
        callback = f"c:{secrets.token_urlsafe(8)[:8]}"
        logger.warning(f"⚠️ Using emergency fallback: {callback}")
    
    return callback


def get_public_profile_url(user) -> str:
    """
    Generate the public profile URL for a user.
    Uses branded domain (lockbay.io) for customer-facing links in production.
    
    Args:
        user: User model instance with profile_slug
        
    Returns:
        str: Full public profile URL (e.g., https://lockbay.io/u/john_crypto)
    """
    from config import Config
    
    if not user or not hasattr(user, 'profile_slug') or not user.profile_slug:
        return ""
    
    # Use PUBLIC_PROFILE_BASE_URL for customer-facing branded links
    base_url = Config.PUBLIC_PROFILE_BASE_URL
    return f"{base_url}/u/{user.profile_slug}"
