"""
Keyboard caching system to prevent redundant UI recreation
"""
from functools import lru_cache
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from typing import Optional, Dict
import hashlib
import json
import logging

logger = logging.getLogger(__name__)

class KeyboardCache:
    """Centralized keyboard caching to prevent duplicate UI creation"""
    
    _cache: Dict[str, InlineKeyboardMarkup] = {}
    
    @classmethod
    def get_cache_key(cls, keyboard_type: str, **kwargs) -> str:
        """Generate cache key from keyboard type and parameters"""
        # Create deterministic hash of parameters
        params_str = json.dumps(kwargs, sort_keys=True, default=str)
        hash_obj = hashlib.md5(f"{keyboard_type}:{params_str}".encode())
        return hash_obj.hexdigest()
    
    @classmethod
    def get_cached_keyboard(cls, keyboard_type: str, **kwargs) -> Optional[InlineKeyboardMarkup]:
        """Get cached keyboard if exists"""
        cache_key = cls.get_cache_key(keyboard_type, **kwargs)
        return cls._cache.get(cache_key)
    
    @classmethod
    def cache_keyboard(cls, keyboard_type: str, keyboard: InlineKeyboardMarkup, **kwargs) -> None:
        """Cache keyboard for future use"""
        cache_key = cls.get_cache_key(keyboard_type, **kwargs)
        cls._cache[cache_key] = keyboard
        
        # Prevent memory bloat - keep only 50 most recent keyboards
        if len(cls._cache) > 50:
            # Remove oldest entry (simple FIFO)
            oldest_key = next(iter(cls._cache))
            del cls._cache[oldest_key]
    
    @classmethod
    def clear_cache(cls) -> None:
        """Clear all cached keyboards"""
        cls._cache.clear()
    
    @classmethod
    def invalidate_user_cache(cls, user_id: int) -> None:
        """Invalidate cached keyboards for a specific user"""
        # Clear all cache since keyboards may contain user-specific balance info
        cls._cache.clear()
        
        # Also clear LRU cache functions that contain balance data
        try:
            create_main_menu_keyboard_cached.cache_clear()
        except Exception as e:
            logger.warning(f"Failed to clear main menu keyboard cache: {e}")
        
        try:
            create_payment_keyboard_cached.cache_clear()
        except Exception as e:
            logger.warning(f"Failed to clear payment keyboard cache: {e}")

# Cached keyboard creation functions
@lru_cache(maxsize=32)
def create_payment_keyboard_cached(wallet_balance_text: str, include_back: bool = False, back_callback: str = "back_to_fee_options") -> InlineKeyboardMarkup:
    """Create payment keyboard with caching"""
    from handlers.escrow import _create_payment_keyboard
    return _create_payment_keyboard(wallet_balance_text, include_back, back_callback)

@lru_cache(maxsize=16) 
def create_main_menu_keyboard_cached(balance: float = 0.0, total_trades: int = 0, active_escrows: int = 0) -> InlineKeyboardMarkup:
    """Create main menu keyboard with caching"""
    from handlers.start import main_menu_keyboard
    return main_menu_keyboard(balance, total_trades, active_escrows)

@lru_cache(maxsize=16)
def create_crypto_keyboard_cached() -> InlineKeyboardMarkup:
    """Create crypto selection keyboard with caching"""
    keyboard = [
        [
            InlineKeyboardButton("₿ Bitcoin", callback_data="crypto_BTC"),
            InlineKeyboardButton("Ξ Ethereum", callback_data="crypto_ETH"),
        ],
        [
            InlineKeyboardButton("Đ Dogecoin", callback_data="crypto_DOGE"),
            InlineKeyboardButton("Ł Litecoin", callback_data="crypto_LTC"),
        ],
        [
            InlineKeyboardButton("₮ Tether TRC20", callback_data="crypto_USDT-TRC20"),
            InlineKeyboardButton("₮ Tether ERC20", callback_data="crypto_USDT-ERC20"),
        ],
        [
            InlineKeyboardButton("⚡ Tron", callback_data="crypto_TRX"),
            InlineKeyboardButton("🪙 Bitcoin Cash", callback_data="crypto_BCH"),
        ],
        [
            InlineKeyboardButton("🔙 Back", callback_data="back_to_payment"),
            InlineKeyboardButton("❌ Cancel Trade", callback_data="cancel_escrow"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)

def get_admin_keyboard_cached(admin_type: str = "main") -> InlineKeyboardMarkup:
    """Get cached admin keyboard by type"""
    cached = KeyboardCache.get_cached_keyboard("admin", admin_type=admin_type)
    if cached:
        return cached
    
    # Create new admin keyboard based on type
    if admin_type == "main":
        keyboard = [
            [
                InlineKeyboardButton("📊 Analytics", callback_data="admin_analytics"),
                InlineKeyboardButton("💰 Transactions", callback_data="admin_transactions"),
            ],
            [
                InlineKeyboardButton("👥 Users", callback_data="admin_users"),
                InlineKeyboardButton("🛡️ Security", callback_data="admin_security"),
            ],
            [
                InlineKeyboardButton("⚙️ Settings", callback_data="admin_settings"),
                InlineKeyboardButton("❌ Close", callback_data="main_menu"),
            ],
        ]
    else:
        # Default fallback
        keyboard = [
            [InlineKeyboardButton("🏠 Admin Main", callback_data="admin_main_menu")]
        ]
    
    markup = InlineKeyboardMarkup(keyboard)
    KeyboardCache.cache_keyboard("admin", markup, admin_type=admin_type)
    return markup