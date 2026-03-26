"""
Performance Optimization Cache System
Implements caching for security checks, callback responses, and frequently accessed data
"""

import logging
from typing import Dict, Any, Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Security check cache - prevents duplicate database queries
SECURITY_CHECK_CACHE: Dict[str, Dict[str, Any]] = {}
SECURITY_CACHE_TTL = timedelta(seconds=30)  # 30-second cache for security data

# User balance cache - reduces database load
BALANCE_CACHE: Dict[int, Dict[str, Any]] = {}
BALANCE_CACHE_TTL = timedelta(seconds=15)  # 15-second balance cache


class PerformanceCache:
    """High-performance caching system for bot operations"""
    
    @staticmethod
    def get_cached_security_check(user_id: int) -> Optional[Dict[str, Any]]:
        """Get cached security check data"""
        cache_key = f"security_{user_id}"
        
        if cache_key in SECURITY_CHECK_CACHE:
            cached_data = SECURITY_CHECK_CACHE[cache_key]
            
            # Check if cache is still valid
            if datetime.utcnow() - cached_data["cached_at"] < SECURITY_CACHE_TTL:
                logger.debug(f"Security check cache HIT for user {user_id}")
                return cached_data["data"]
            else:
                # Remove expired cache
                del SECURITY_CHECK_CACHE[cache_key]
        
        return None
    
    @staticmethod
    def cache_security_check(user_id: int, security_data: Dict[str, Any]):
        """Cache security check results"""
        cache_key = f"security_{user_id}"
        
        SECURITY_CHECK_CACHE[cache_key] = {
            "data": security_data,
            "cached_at": datetime.utcnow()
        }
        
        logger.debug(f"Security check cached for user {user_id}")
        
        # Clean old cache entries
        PerformanceCache._cleanup_security_cache()
    
    @staticmethod
    def get_cached_balance(user_id: int) -> Optional[Dict[str, Any]]:
        """Get cached balance data"""
        if user_id in BALANCE_CACHE:
            cached_data = BALANCE_CACHE[user_id]
            
            # Check if cache is still valid
            if datetime.utcnow() - cached_data["cached_at"] < BALANCE_CACHE_TTL:
                logger.debug(f"Balance cache HIT for user {user_id}")
                return cached_data["data"]
            else:
                # Remove expired cache
                del BALANCE_CACHE[user_id]
        
        return None
    
    @staticmethod
    def cache_balance(user_id: int, balance_data: Dict[str, Any]):
        """Cache balance data"""
        BALANCE_CACHE[user_id] = {
            "data": balance_data,
            "cached_at": datetime.utcnow()
        }
        
        logger.debug(f"Balance cached for user {user_id}")
        
        # Clean old cache entries
        PerformanceCache._cleanup_balance_cache()
    
    @staticmethod
    def invalidate_user_cache(user_id: int):
        """Invalidate all cache entries for a user"""
        # Remove security cache
        cache_key = f"security_{user_id}"
        if cache_key in SECURITY_CHECK_CACHE:
            del SECURITY_CHECK_CACHE[cache_key]
        
        # Remove balance cache
        if user_id in BALANCE_CACHE:
            del BALANCE_CACHE[user_id]
        
        logger.debug(f"All cache invalidated for user {user_id}")
    
    @staticmethod
    def _cleanup_security_cache():
        """Remove expired security cache entries"""
        current_time = datetime.utcnow()
        expired_keys = [
            key for key, data in SECURITY_CHECK_CACHE.items()
            if current_time - data["cached_at"] > SECURITY_CACHE_TTL
        ]
        
        for key in expired_keys:
            del SECURITY_CHECK_CACHE[key]
    
    @staticmethod
    def _cleanup_balance_cache():
        """Remove expired balance cache entries"""
        current_time = datetime.utcnow()
        expired_keys = [
            user_id for user_id, data in BALANCE_CACHE.items()
            if current_time - data["cached_at"] > BALANCE_CACHE_TTL
        ]
        
        for user_id in expired_keys:
            del BALANCE_CACHE[user_id]
    
    @staticmethod
    def get_cache_stats() -> Dict[str, int]:
        """Get cache statistics for monitoring"""
        return {
            "security_cache_entries": len(SECURITY_CHECK_CACHE),
            "balance_cache_entries": len(BALANCE_CACHE)
        }