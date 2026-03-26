"""
User Cache System
Caches frequently accessed user data to reduce database queries
"""

import logging
from typing import Optional, Dict, Any
from dataclasses import dataclass
from datetime import datetime, timedelta
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from models import User

logger = logging.getLogger(__name__)


@dataclass
class CachedUser:
    """Cached user data with expiration"""
    user_data: Dict[str, Any]
    cached_at: datetime
    ttl_seconds: int = 300  # 5 minutes default TTL
    
    def is_expired(self) -> bool:
        """Check if cache entry has expired"""
        return datetime.utcnow() > (self.cached_at + timedelta(seconds=self.ttl_seconds))


class UserCache:
    """Thread-safe user cache with automatic cleanup"""
    
    def __init__(self, default_ttl: int = 900):  # 15 minutes for better performance
        self.cache: Dict[str, CachedUser] = {}
        self.default_ttl = default_ttl
        self.lock = threading.RLock()
        self.stats = {
            "hits": 0,
            "misses": 0,
            "evictions": 0
        }
        
    def _user_to_dict(self, user: "User") -> Dict[str, Any]:
        """Convert User model to cacheable dictionary"""
        return {
            "id": user.id,
            "telegram_id": user.telegram_id,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "username": user.username,
            "email": user.email,
            "email_verified": getattr(user, "email_verified", False),  # Default to False if field doesn't exist
            "phone_number": user.phone_number,
            "created_at": user.created_at.isoformat() if hasattr(user.created_at, 'isoformat') and user.created_at is not None else None,
            "is_admin": getattr(user, "is_admin", False),
            "referral_code": user.referral_code,
            "referred_by_id": getattr(user, "referred_by_id", None),  # Use referred_by_id field
            "onboarding_completed": getattr(user, "onboarding_completed", True),  # Default True for backwards compat
        }
    
    def get(self, telegram_id: str) -> Optional[Dict[str, Any]]:
        """Get user from cache"""
        with self.lock:
            cached_user = self.cache.get(telegram_id)
            
            if not cached_user:
                self.stats["misses"] += 1
                logger.debug(f"Cache MISS for user {telegram_id}")
                return None
                
            if cached_user.is_expired():
                del self.cache[telegram_id]
                self.stats["evictions"] += 1
                self.stats["misses"] += 1
                logger.debug(f"Cache EXPIRED for user {telegram_id}")
                return None
                
            self.stats["hits"] += 1
            logger.debug(f"Cache HIT for user {telegram_id}")
            return cached_user.user_data
    
    def set(self, telegram_id: str, user: "User", ttl: Optional[int] = None) -> None:
        """Cache user data"""
        with self.lock:
            user_data = self._user_to_dict(user)
            cached_user = CachedUser(
                user_data=user_data,
                cached_at=datetime.utcnow(),
                ttl_seconds=ttl or self.default_ttl
            )
            self.cache[telegram_id] = cached_user
            logger.debug(f"Cached user {telegram_id} for {cached_user.ttl_seconds}s")
    
    def invalidate(self, telegram_id: str) -> bool:
        """Remove user from cache"""
        with self.lock:
            if telegram_id in self.cache:
                del self.cache[telegram_id]
                logger.debug(f"Invalidated cache for user {telegram_id}")
                return True
            return False
    
    def clear(self) -> None:
        """Clear all cache entries"""
        with self.lock:
            count = len(self.cache)
            self.cache.clear()
            logger.info(f"Cleared {count} cache entries")
    
    def cleanup_expired(self) -> int:
        """Remove expired entries and return count"""
        with self.lock:
            expired_keys = [
                key for key, cached_user in self.cache.items()
                if cached_user.is_expired()
            ]
            
            for key in expired_keys:
                del self.cache[key]
                self.stats["evictions"] += 1
            
            if expired_keys:
                logger.debug(f"Cleaned up {len(expired_keys)} expired cache entries")
            
            return len(expired_keys)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        with self.lock:
            total_requests = self.stats["hits"] + self.stats["misses"]
            hit_rate = (self.stats["hits"] / total_requests * 100) if total_requests > 0 else 0
            
            return {
                "cache_size": len(self.cache),
                "hits": self.stats["hits"],
                "misses": self.stats["misses"],
                "evictions": self.stats["evictions"],
                "hit_rate_percent": round(hit_rate, 1),
                "total_requests": total_requests
            }


# Global user cache instance
user_cache = UserCache(default_ttl=300)  # 5 minute cache


def get_cached_user(telegram_id: str) -> Optional[Dict[str, Any]]:
    """Get user from cache - helper function"""
    return user_cache.get(telegram_id)


def cache_user(telegram_id: str, user: "User", ttl: Optional[int] = None) -> None:
    """Cache user data - helper function"""
    user_cache.set(telegram_id, user, ttl)


def invalidate_user_cache(telegram_id: str) -> bool:
    """Invalidate user cache - helper function"""
    return user_cache.invalidate(telegram_id)


# Background cleanup function
def cleanup_user_cache():
    """Background task to cleanup expired cache entries"""
    try:
        expired_count = user_cache.cleanup_expired()
        if expired_count > 0:
            logger.info(f"🧹 Cleaned up {expired_count} expired user cache entries")
        return expired_count
    except Exception as e:
        logger.error(f"Error cleaning user cache: {e}")
        return 0