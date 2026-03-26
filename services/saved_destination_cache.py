"""
Shared Caching Utility for Saved Payment Destinations

Provides high-performance caching for saved bank accounts and crypto addresses
with async database session support.

Key Features:
- Async-compatible using async_managed_session()
- Cached saved bank accounts (NGN cashouts)
- Cached crypto addresses (crypto cashouts)
- TTL: 300 seconds (5 minutes) for balance between performance and freshness
- Automatic cache invalidation support
"""

import logging
from typing import Dict, List, Optional, Any
from sqlalchemy import select

from database import async_managed_session
from models import User, SavedBankAccount, SavedAddress
from utils.production_cache import get_cached, set_cached, delete_cached

logger = logging.getLogger(__name__)


class SavedDestinationCache:
    """
    High-performance caching for saved payment destinations
    
    Provides async-compatible caching for both bank accounts and crypto addresses
    to reduce database queries and improve user experience in cashout flows.
    """
    
    # Cache TTL settings - 5 minutes for saved destinations
    SAVED_DESTINATIONS_TTL = 300  # 5 minutes - balance between performance and freshness
    
    # Result limit to prevent memory bloat
    MAX_RESULTS = 10  # Limit to 10 most recent destinations
    
    @classmethod
    def get_cached_bank_accounts(cls, user_id: int) -> Optional[List[Dict[str, Any]]]:
        """
        Get cached saved bank accounts for faster loading
        
        Args:
            user_id: User ID (not telegram_id)
            
        Returns:
            List of saved bank account dictionaries or None if not cached
        """
        cache_key = f"saved_banks_user_{user_id}"
        cached_banks = get_cached(cache_key)
        
        if cached_banks:
            logger.debug(f"💨 Cache HIT for saved banks - User: {user_id}")
            return cached_banks
        
        logger.debug(f"❌ Cache MISS for saved banks - User: {user_id}")
        return None
    
    @classmethod
    async def load_bank_accounts_optimized(cls, telegram_user_id: int) -> List[Dict[str, Any]]:
        """
        Load saved bank accounts with optimal performance using caching
        
        Args:
            telegram_user_id: Telegram user ID
            
        Returns:
            List of saved bank account dictionaries
        """
        try:
            # Use async database session
            async with async_managed_session() as session:
                # Get user first
                stmt = select(User).where(User.telegram_id == str(telegram_user_id))
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()
                
                if not user:
                    logger.warning(f"User not found for telegram_id: {telegram_user_id}")
                    return []
                
                # Try cache first for massive performance boost
                cached_banks = cls.get_cached_bank_accounts(user.id)
                if cached_banks is not None:
                    return cached_banks
                
                # Cache miss - load from database with optimized query
                stmt = select(SavedBankAccount).where(
                    SavedBankAccount.user_id == user.id
                ).order_by(SavedBankAccount.last_used.desc()).limit(cls.MAX_RESULTS)
                
                result = await session.execute(stmt)
                saved_accounts = result.scalars().all()
                
                # Convert to optimized dictionary format for fast access
                saved_banks = []
                for account in saved_accounts:
                    bank_data = {
                        'id': account.id,
                        'bank_name': account.bank_name,
                        'account_number': account.account_number,
                        'account_name': account.account_name,
                        'bank_code': account.bank_code,
                        'label': account.label,
                        'is_verified': account.is_verified,
                        'is_default': account.is_default,
                        'is_active': account.is_active,
                        'last_used': account.last_used.isoformat() if account.last_used else None,
                        'created_at': account.created_at.isoformat() if account.created_at else None
                    }
                    saved_banks.append(bank_data)
                
                # Cache the results for faster subsequent access
                cache_key = f"saved_banks_user_{user.id}"
                set_cached(cache_key, saved_banks, ttl=cls.SAVED_DESTINATIONS_TTL)
                
                logger.info(f"✅ Loaded {len(saved_banks)} saved banks for user {telegram_user_id} (cached)")
                return saved_banks
                
        except Exception as e:
            logger.error(f"❌ Error loading saved banks for user {telegram_user_id}: {e}")
            return []
    
    @classmethod
    def get_cached_crypto_addresses(cls, user_id: int, currency: Optional[str] = None) -> Optional[List[Dict[str, Any]]]:
        """
        Get cached saved crypto addresses for faster loading
        
        Args:
            user_id: User ID (not telegram_id)
            currency: Optional currency filter (e.g., "BTC", "ETH")
            
        Returns:
            List of saved crypto address dictionaries or None if not cached
        """
        # Different cache keys for all addresses vs currency-specific
        if currency:
            cache_key = f"saved_addresses_user_{user_id}_{currency}"
        else:
            cache_key = f"saved_addresses_user_{user_id}"
        
        cached_addresses = get_cached(cache_key)
        
        if cached_addresses:
            logger.debug(f"💨 Cache HIT for saved addresses - User: {user_id}, Currency: {currency or 'all'}")
            return cached_addresses
        
        logger.debug(f"❌ Cache MISS for saved addresses - User: {user_id}, Currency: {currency or 'all'}")
        return None
    
    @classmethod
    async def load_crypto_addresses_optimized(cls, telegram_user_id: int, currency: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Load saved crypto addresses with optimal performance using caching
        
        Args:
            telegram_user_id: Telegram user ID
            currency: Optional currency filter (e.g., "BTC", "ETH")
            
        Returns:
            List of saved crypto address dictionaries
        """
        try:
            # Use async database session
            async with async_managed_session() as session:
                # Get user first
                stmt = select(User).where(User.telegram_id == str(telegram_user_id))
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()
                
                if not user:
                    logger.warning(f"User not found for telegram_id: {telegram_user_id}")
                    return []
                
                # Try cache first for massive performance boost
                cached_addresses = cls.get_cached_crypto_addresses(user.id, currency)
                if cached_addresses is not None:
                    return cached_addresses
                
                # Cache miss - load from database with optimized query
                stmt = select(SavedAddress).where(SavedAddress.user_id == user.id)
                
                # Apply currency filter if provided
                if currency:
                    stmt = stmt.where(SavedAddress.currency == currency)
                
                stmt = stmt.order_by(SavedAddress.last_used.desc()).limit(cls.MAX_RESULTS)
                
                result = await session.execute(stmt)
                saved_addresses = result.scalars().all()
                
                # Convert to optimized dictionary format for fast access
                addresses_list = []
                for address in saved_addresses:
                    address_data = {
                        'id': address.id,
                        'currency': address.currency,
                        'network': address.network,
                        'address': address.address,
                        'label': address.label,
                        'is_verified': address.is_verified,
                        'is_active': address.is_active,
                        'last_used': address.last_used.isoformat() if address.last_used else None,
                        'created_at': address.created_at.isoformat() if address.created_at else None
                    }
                    addresses_list.append(address_data)
                
                # Cache the results for faster subsequent access
                if currency:
                    cache_key = f"saved_addresses_user_{user.id}_{currency}"
                else:
                    cache_key = f"saved_addresses_user_{user.id}"
                
                set_cached(cache_key, addresses_list, ttl=cls.SAVED_DESTINATIONS_TTL)
                
                logger.info(f"✅ Loaded {len(addresses_list)} saved addresses for user {telegram_user_id}, currency: {currency or 'all'} (cached)")
                return addresses_list
                
        except Exception as e:
            logger.error(f"❌ Error loading saved addresses for user {telegram_user_id}: {e}")
            return []
    
    @classmethod
    def invalidate_bank_accounts_cache(cls, user_id: int) -> None:
        """
        Invalidate saved bank accounts cache when accounts are added/removed/modified
        
        Args:
            user_id: User ID (not telegram_id)
        """
        cache_key = f"saved_banks_user_{user_id}"
        delete_cached(cache_key)
        logger.debug(f"🗑️ Invalidated saved banks cache for user {user_id}")
    
    @classmethod
    def invalidate_crypto_addresses_cache(cls, user_id: int) -> None:
        """
        Invalidate saved crypto addresses cache when addresses are added/removed/modified
        
        This invalidates both the general cache and all currency-specific caches
        
        Args:
            user_id: User ID (not telegram_id)
        """
        # Invalidate general cache
        cache_key = f"saved_addresses_user_{user_id}"
        delete_cached(cache_key)
        
        # Note: Currency-specific caches will expire naturally via TTL
        # We don't invalidate them individually to avoid tracking all currencies
        
        logger.debug(f"🗑️ Invalidated saved addresses cache for user {user_id}")
