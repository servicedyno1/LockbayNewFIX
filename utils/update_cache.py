"""
Per-Update Caching System
=========================

Caches User/Escrow/Wallet objects within a single update to eliminate redundant
database queries. Reduces 3-5 queries per update → 1 query (5x reduction).

Usage:
    # In handlers:
    user = await get_cached_user(update, context)  # First call: fetches from DB
    # ... later in same handler ...
    user = await get_cached_user(update, context)  # Second call: returns cached copy!

Cache Scope (AUTOMATIC PER-UPDATE):
    - Cache is scoped to a SINGLE update only
    - Automatically cleared when a new update arrives
    - No stale data risk from background mutations (deposits, cashouts, etc.)
    - Fresh data guaranteed on every new user interaction

How It Works:
    1. Stores user data + update_id in context.user_data
    2. On next call, checks if update_id changed
    3. If changed → clears old cache → fetches fresh data
    4. If same → returns cached data (same update)

Cache Invalidation:
    ✅ AUTOMATIC: Cache clears on new update (no manual invalidation needed!)
    ⚠️ Optional: Call invalidate_user_cache() for immediate invalidation within same update
    
    Example (optional manual invalidation):
        # After balance change within same update:
        invalidate_user_cache(context, user.telegram_id)
        
    Note: Manual invalidation rarely needed due to automatic per-update clearing.
"""

import logging
from typing import Optional
from telegram import Update
from telegram.ext import ContextTypes
from database import get_async_session
from models import User
from sqlalchemy import select
from sqlalchemy.orm import joinedload

logger = logging.getLogger(__name__)


async def get_cached_user(
    update: Update, 
    context: ContextTypes.DEFAULT_TYPE
) -> Optional[User]:
    """
    Get user from cache or database, with per-update cache scope.
    
    Cache is automatically cleared when a new update arrives, ensuring fresh data
    while eliminating redundant queries within the same update.
    
    Args:
        update: Telegram update object
        context: Bot context with user_data storage
        
    Returns:
        User object with eager-loaded wallets, or None if not found
        
    Example:
        user = await get_cached_user(update, context)
        if user:
            print(f"User balance: {user.wallets[0].balance}")
    """
    if not update.effective_user:
        logger.debug("No effective_user in update, cannot fetch user")
        return None
    
    telegram_id = update.effective_user.id
    cache_key = f"user_{telegram_id}"
    update_id_key = f"update_id_{telegram_id}"
    current_update_id = update.update_id
    
    # Check if this is a new update - if so, clear stale cache
    if context.user_data:
        stored_update_id = context.user_data.get(update_id_key)
        
        if stored_update_id and stored_update_id != current_update_id:
            # New update detected - clear stale cache
            if cache_key in context.user_data:
                del context.user_data[cache_key]
                logger.debug(
                    f"🔄 NEW UPDATE: Cleared stale cache for user {telegram_id} "
                    f"(old: {stored_update_id}, new: {current_update_id})"
                )
        
        # Update stored update_id
        context.user_data[update_id_key] = current_update_id
        
        # Check cache for current update
        if cache_key in context.user_data:
            logger.debug(
                f"✅ CACHE HIT: User {telegram_id} from update {current_update_id}"
            )
            return context.user_data[cache_key]
    
    # Cache miss: Fetch from DB with eager loading
    logger.debug(
        f"❌ CACHE MISS: Fetching user {telegram_id} from database "
        f"(update {current_update_id})"
    )
    
    try:
        async with get_async_session() as session:
            result = await session.execute(
                select(User)
                .options(joinedload(User.wallets))  # Eager load wallets
                .where(User.telegram_id == telegram_id)
            )
            user = result.unique().scalar_one_or_none()
            
            if user:
                # Store in cache for this update
                if context.user_data is not None:
                    context.user_data[cache_key] = user
                    context.user_data[update_id_key] = current_update_id
                logger.debug(
                    f"💾 CACHED: User {telegram_id} for update {current_update_id} "
                    f"(wallets: {len(user.wallets)})"
                )
            else:
                logger.debug(f"⚠️ User {telegram_id} not found in database")
            
            return user
            
    except Exception as e:
        logger.error(f"❌ Error fetching user {telegram_id} from database: {e}")
        return None


def invalidate_user_cache(context: ContextTypes.DEFAULT_TYPE, telegram_id: int) -> None:
    """
    Invalidate user cache when data changes.
    
    Call this function after:
    - User profile updates (email verification, name changes)
    - Wallet balance changes
    - Any other user data modifications
    
    Args:
        context: Bot context with user_data storage
        telegram_id: Telegram user ID to invalidate
        
    Example:
        # After updating user email:
        invalidate_user_cache(context, user.telegram_id)
    """
    if not context.user_data:
        return
        
    cache_key = f"user_{telegram_id}"
    
    if cache_key in context.user_data:
        del context.user_data[cache_key]
        logger.debug(f"🗑️ INVALIDATED: User {telegram_id} cache cleared")
    else:
        logger.debug(f"ℹ️ No cache to invalidate for user {telegram_id}")


async def get_cached_user_with_escrows(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
) -> Optional[User]:
    """
    Get user with eager-loaded wallets AND escrows, with per-update cache scope.
    
    Use this variant when you need escrow data in addition to user/wallet data.
    Slightly more expensive than get_cached_user() but still cached per-update.
    
    Args:
        update: Telegram update object
        context: Bot context with user_data storage
        
    Returns:
        User object with eager-loaded wallets and escrows, or None if not found
    """
    if not update.effective_user:
        return None
    
    telegram_id = update.effective_user.id
    cache_key = f"user_escrows_{telegram_id}"
    update_id_key = f"update_id_escrows_{telegram_id}"
    current_update_id = update.update_id
    
    # Check if this is a new update - if so, clear stale cache
    if context.user_data:
        stored_update_id = context.user_data.get(update_id_key)
        
        if stored_update_id and stored_update_id != current_update_id:
            # New update detected - clear stale cache
            if cache_key in context.user_data:
                del context.user_data[cache_key]
                logger.debug(
                    f"🔄 NEW UPDATE: Cleared stale escrow cache for user {telegram_id} "
                    f"(old: {stored_update_id}, new: {current_update_id})"
                )
        
        # Update stored update_id
        context.user_data[update_id_key] = current_update_id
        
        # Check cache for current update
        if cache_key in context.user_data:
            logger.debug(
                f"✅ CACHE HIT: User+Escrows {telegram_id} from update {current_update_id}"
            )
            return context.user_data[cache_key]
    
    # Cache miss: Fetch from DB with eager loading
    logger.debug(
        f"❌ CACHE MISS: Fetching user+escrows {telegram_id} from database "
        f"(update {current_update_id})"
    )
    
    try:
        async with get_async_session() as session:
            
            result = await session.execute(
                select(User)
                .options(
                    joinedload(User.wallets),
                    joinedload(User.escrows_as_buyer),
                    joinedload(User.escrows_as_seller)
                )
                .where(User.telegram_id == telegram_id)
            )
            user = result.unique().scalar_one_or_none()
            
            if user and context.user_data is not None:
                context.user_data[cache_key] = user
                context.user_data[update_id_key] = current_update_id
                logger.debug(
                    f"💾 CACHED: User+Escrows {telegram_id} for update {current_update_id} "
                    f"(wallets: {len(user.wallets)}, "
                    f"buyer escrows: {len(user.escrows_as_buyer)}, "
                    f"seller escrows: {len(user.escrows_as_seller)})"
                )
            
            return user
            
    except Exception as e:
        logger.error(f"❌ Error fetching user+escrows {telegram_id}: {e}")
        return None


def clear_all_user_caches(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Clear ALL user caches and update tracking (useful on /start or session reset).
    
    This removes all cached user data and update_id tracking to force fresh fetches.
    Rarely needed due to automatic per-update clearing.
    
    Args:
        context: Bot context with user_data storage
    """
    if not context.user_data:
        return
        
    keys_to_remove = [
        key for key in context.user_data.keys()
        if key.startswith("user_") or key.startswith("update_id_")
    ]
    
    for key in keys_to_remove:
        del context.user_data[key]
    
    if keys_to_remove:
        logger.debug(f"🗑️ CLEARED: {len(keys_to_remove)} cache entries (data + update tracking)")
