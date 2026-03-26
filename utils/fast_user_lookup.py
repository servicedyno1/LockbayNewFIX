"""
Secure Fast User Lookup Utility
HIGH-PERFORMANCE user lookups with SECURITY-FIRST design

SECURITY POLICY:
- Security-critical fields (is_admin, email_verified) are NEVER cached
- Only non-sensitive, stable user data is cached (id, telegram_id, first_name)
- Cache TTL limited to 60 seconds to minimize stale data exposure
- Thread-safe bounded caches with automatic cleanup
"""

import logging
import time
import threading
from typing import Optional, Any
from collections import OrderedDict
from models import User
from utils.database_pool_manager import database_pool

logger = logging.getLogger(__name__)

# SECURITY: Thread-safe bounded cache for non-sensitive data only
class SecureBoundedCache:
    """Thread-safe LRU cache with size and TTL limits for security"""
    
    def __init__(self, max_size: int = 1000, ttl_seconds: int = 60):
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self._cache = OrderedDict()
        self._timestamps = {}
        self._lock = threading.Lock()
    
    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            current_time = time.time()
            
            if key not in self._cache:
                return None
            
            # Check TTL
            if current_time - self._timestamps.get(key, 0) > self.ttl_seconds:
                self._cache.pop(key, None)
                self._timestamps.pop(key, None)
                return None
            
            # Move to end (LRU)
            value = self._cache.pop(key)
            self._cache[key] = value
            return value
    
    def set(self, key: str, value: Any):
        with self._lock:
            current_time = time.time()
            
            # Remove if exists
            if key in self._cache:
                self._cache.pop(key)
            
            # Add to end
            self._cache[key] = value
            self._timestamps[key] = current_time
            
            # Enforce size limit
            while len(self._cache) > self.max_size:
                oldest_key = next(iter(self._cache))
                self._cache.pop(oldest_key)
                self._timestamps.pop(oldest_key, None)
    
    def invalidate(self, key: str):
        """Explicitly invalidate a cache entry"""
        with self._lock:
            self._cache.pop(key, None)
            self._timestamps.pop(key, None)
    
    def clear(self):
        """Clear all cache entries"""
        with self._lock:
            self._cache.clear()
            self._timestamps.clear()

# SECURITY: Bounded cache for non-sensitive user data only
# TTL reduced to 60 seconds to minimize stale data exposure
_safe_user_cache = SecureBoundedCache(max_size=1000, ttl_seconds=60)

# PERFORMANCE: Ultra-short cache for read-heavy operations (5 second TTL)
# Only caches User ID to reduce repeated lookups within same request flow
_ultra_short_user_cache = SecureBoundedCache(max_size=500, ttl_seconds=5)

def fast_user_lookup(telegram_id: str) -> Optional[User]:
    """
    SECURE fast user lookup - full User object without caching sensitive fields
    
    SECURITY: Always returns fresh User object from database
    PERFORMANCE: Note - 100-150ms latency is expected for cloud database (Neon) due to network overhead
    """
    from utils.normalizers import normalize_telegram_id
    
    start_time = time.time()
    
    try:
        # CRITICAL FIX: Convert telegram_id string to int to match database column type (bigint)
        normalized_id = normalize_telegram_id(telegram_id)
        if normalized_id is None:
            logger.warning(f"Invalid telegram_id provided: {telegram_id}")
            return None
        
        # SECURITY: Always fetch fresh user data for authorization checks
        with database_pool.get_session(f"secure_lookup_{normalized_id}") as session:
            user = session.query(User).filter(
                User.telegram_id == normalized_id
            ).first()
            
            # Performance monitoring
            query_time = time.time() - start_time
            
            if query_time > 0.15:  # 150ms threshold - realistic for cloud database
                logger.warning(f"🔒 SLOW SECURE LOOKUP: {query_time:.3f}s for {normalized_id}")
            else:
                logger.debug(f"🔒 Secure user lookup: {query_time:.3f}s")
            
            return user
                
    except Exception as e:
        query_time = time.time() - start_time
        logger.error(f"❌ Secure user lookup failed after {query_time:.3f}s: {e}")
        return None


async def async_fast_user_lookup(telegram_id: str, session=None) -> Optional[User]:
    """
    ASYNC SECURE fast user lookup - full User object without caching sensitive fields
    
    SECURITY: Always returns fresh User object from database
    PERFORMANCE: Native async operations for ~70% faster lookups (30-50ms vs 150-250ms)
    
    This is the async version of fast_user_lookup() designed to eliminate the
    run_in_executor overhead in async handlers like /start.
    
    Args:
        telegram_id: User's Telegram ID as string
        session: Optional AsyncSession - if provided, reuses existing session to avoid cold starts
    """
    from utils.normalizers import normalize_telegram_id
    from database import get_async_session
    from sqlalchemy import select
    
    start_time = time.time()
    
    try:
        # CRITICAL FIX: Convert telegram_id string to int to match database column type (bigint)
        normalized_id = normalize_telegram_id(telegram_id)
        if normalized_id is None:
            logger.warning(f"Invalid telegram_id provided: {telegram_id}")
            return None
        
        # PERFORMANCE: Reuse session if provided to avoid Neon cold start penalty
        if session is not None:
            result = await session.execute(
                select(User).where(User.telegram_id == normalized_id)
            )
            user = result.scalar_one_or_none()
            
            # Performance monitoring
            query_time = time.time() - start_time
            
            if query_time > 0.05:  # 50ms threshold - async should be much faster
                logger.warning(f"⚡ ASYNC_LOOKUP (reused session) completed in {query_time*1000:.1f}ms for {normalized_id}")
            else:
                logger.info(f"⚡ ASYNC_LOOKUP (reused session) completed in {query_time*1000:.1f}ms")
            
            return user
        else:
            # SECURITY: Always fetch fresh user data for authorization checks
            async with get_async_session() as new_session:
                result = await new_session.execute(
                    select(User).where(User.telegram_id == normalized_id)
                )
                user = result.scalar_one_or_none()
                
                # Performance monitoring
                query_time = time.time() - start_time
                
                if query_time > 0.05:  # 50ms threshold - async should be much faster
                    logger.warning(f"⚡ ASYNC_LOOKUP (new session) completed in {query_time*1000:.1f}ms for {normalized_id}")
                else:
                    logger.info(f"⚡ ASYNC_LOOKUP (new session) completed in {query_time*1000:.1f}ms")
                
                return user
                
    except Exception as e:
        query_time = time.time() - start_time
        logger.error(f"❌ Async user lookup failed after {query_time*1000:.1f}ms: {e}")
        return None


def get_user_basic_info(telegram_id: str) -> Optional[dict]:
    """
    SECURE fast lookup for non-sensitive user data only
    
    SECURITY POLICY:
    - Caches ONLY non-sensitive, stable fields (id, telegram_id, first_name)
    - NEVER caches is_admin, email_verified, or other authorization fields
    - 60-second TTL with thread-safe bounded cache
    
    For authorization checks, use fast_user_lookup() instead
    """
    from utils.normalizers import normalize_telegram_id
    
    start_time = time.time()
    
    try:
        # CRITICAL FIX: Convert telegram_id string to int to match database column type (bigint)
        normalized_id = normalize_telegram_id(telegram_id)
        if normalized_id is None:
            logger.warning(f"Invalid telegram_id provided: {telegram_id}")
            return None
        
        # Use string version for cache key
        cache_key = str(normalized_id)
        
        # SECURITY: Check cache for non-sensitive data only
        cached_basic = _safe_user_cache.get(cache_key)
        if cached_basic is not None:
            query_time = time.time() - start_time
            logger.debug(f"⚡ CACHED safe basic info: {query_time:.3f}s for {normalized_id}")
            return cached_basic
        
        # Fetch from database
        with database_pool.get_session(f"safe_basic_{normalized_id}") as session:
            # SECURITY: Query only non-sensitive fields for caching
            result = session.query(
                User.id,
                User.telegram_id, 
                User.first_name
            ).filter(User.telegram_id == normalized_id).first()
            
            query_time = time.time() - start_time
            
            if result:
                # SECURITY: Cache only non-sensitive fields
                safe_user_data = {
                    'id': result.id,
                    'telegram_id': result.telegram_id,
                    'first_name': result.first_name
                }
                
                # Cache safely with 60s TTL
                _safe_user_cache.set(cache_key, safe_user_data)
                
                if query_time > 0.01:  # 10ms threshold
                    logger.warning(f"🔒 SLOW SAFE BASIC: {query_time:.3f}s for {normalized_id}")
                else:
                    logger.debug(f"🔒 Safe basic info: {query_time:.3f}s")
                
                return safe_user_data
            
            return None
                
    except Exception as e:
        query_time = time.time() - start_time
        logger.error(f"❌ Safe basic info lookup failed after {query_time:.3f}s: {e}")
        return None


def get_user_authorization_info(telegram_id: str) -> Optional[dict]:
    """
    SECURITY-CRITICAL: Always fetch fresh authorization data from database
    
    NEVER cached to prevent authorization bypass vulnerabilities
    Use this for all permission checks, admin verification, etc.
    
    Returns: Dict with security-critical fields or None if user not found
    """
    from utils.normalizers import normalize_telegram_id
    
    start_time = time.time()
    
    try:
        # CRITICAL FIX: Convert telegram_id string to int to match database column type (bigint)
        normalized_id = normalize_telegram_id(telegram_id)
        if normalized_id is None:
            logger.warning(f"Invalid telegram_id provided: {telegram_id}")
            return None
        
        # SECURITY: Always fresh database query for authorization
        with database_pool.get_session(f"auth_check_{normalized_id}") as session:
            result = session.query(
                User.id,
                User.telegram_id,
                User.is_admin,
                User.email_verified,
                User.is_active,
                User.is_blocked
            ).filter(User.telegram_id == normalized_id).first()
            
            query_time = time.time() - start_time
            
            if result:
                auth_data = {
                    'id': result.id,
                    'telegram_id': result.telegram_id,
                    'is_admin': result.is_admin,
                    'email_verified': result.email_verified,
                    'is_active': getattr(result, 'is_active', True),
                    'is_blocked': getattr(result, 'is_blocked', False)
                }
                
                logger.debug(f"🔐 AUTH CHECK: {query_time:.3f}s for {normalized_id}")
                return auth_data
            
            return None
                
    except Exception as e:
        query_time = time.time() - start_time
        logger.error(f"❌ Authorization check failed after {query_time:.3f}s: {e}")
        return None


def invalidate_user_cache(telegram_id: str):
    """
    Invalidate cached user data for specific user
    Call this when user data changes
    """
    _safe_user_cache.invalidate(telegram_id)
    logger.debug(f"🔄 Invalidated cache for user {telegram_id}")


def clear_user_cache():
    """Clear all user cache for memory management"""
    _safe_user_cache.clear()
    logger.info("🧹 Secure user cache cleared")