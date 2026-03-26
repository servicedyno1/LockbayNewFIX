"""
Balance Cache Invalidation Service
CRITICAL FIX: Prevents balance cache staleness issues by automatically invalidating
all balance-related caches when wallet operations occur
"""

import logging
from typing import List
from threading import RLock

logger = logging.getLogger(__name__)

class BalanceCacheInvalidationService:
    """Service for managing balance cache invalidation across all caching systems"""
    
    def __init__(self):
        self._lock = RLock()
    
    def invalidate_user_balance_caches(self, user_id: int, operation_type: str = "unknown") -> bool:
        """
        CRITICAL FIX: Invalidate all balance-related caches for a user
        
        Args:
            user_id: User whose balance caches should be invalidated
            operation_type: Type of operation that triggered invalidation (for logging)
        
        Returns:
            bool: True if all invalidations succeeded
        """
        try:
            with self._lock:
                success_count = 0
                total_attempts = 0
                
                # 1. Invalidate PerformanceCache balance cache
                try:
                    from utils.performance_cache import PerformanceCache
                    PerformanceCache.invalidate_user_cache(user_id)
                    success_count += 1
                    logger.debug(f"CACHE_INVALIDATION: PerformanceCache cleared for user {user_id}")
                except Exception as e:
                    logger.warning(f"Failed to invalidate PerformanceCache for user {user_id}: {e}")
                finally:
                    total_attempts += 1
                
                # 2. Invalidate FastWalletService cache (FIXED: Proper cache handling)
                try:
                    from utils.wallet_performance import FastWalletService
                    if hasattr(FastWalletService, 'WALLET_DISPLAY_CACHE'):
                        cache = getattr(FastWalletService, 'WALLET_DISPLAY_CACHE', {})
                        if user_id in cache:
                            del cache[user_id]
                            success_count += 1
                            logger.debug(f"CACHE_INVALIDATION: FastWalletService cache cleared for user {user_id}")
                        else:
                            # Cache exists but user not in it - still count as success
                            success_count += 1
                            logger.debug(f"CACHE_INVALIDATION: FastWalletService cache empty for user {user_id} (expected)")
                    else:
                        # Try global cache directly
                        try:
                            from utils.wallet_performance import WALLET_DISPLAY_CACHE
                            if user_id in WALLET_DISPLAY_CACHE:
                                del WALLET_DISPLAY_CACHE[user_id]
                                success_count += 1
                                logger.debug(f"CACHE_INVALIDATION: WALLET_DISPLAY_CACHE cleared for user {user_id}")
                            else:
                                # Cache exists but user not in it - still count as success
                                success_count += 1
                                logger.debug(f"CACHE_INVALIDATION: WALLET_DISPLAY_CACHE empty for user {user_id} (expected)")
                        except ImportError:
                            # Cache doesn't exist - this is fine, count as success
                            success_count += 1
                            logger.debug("CACHE_INVALIDATION: FastWalletService cache not available (no cache to clear)")
                except Exception as e:
                    logger.warning(f"Failed to invalidate FastWalletService cache for user {user_id}: {e}")
                finally:
                    total_attempts += 1
                
                # 3. Invalidate keyboard cache (contains balance info)
                try:
                    from utils.keyboard_cache import KeyboardCache
                    KeyboardCache.invalidate_user_cache(user_id)
                    success_count += 1
                    logger.debug(f"CACHE_INVALIDATION: KeyboardCache cleared for user {user_id}")
                except Exception as e:
                    logger.warning(f"Failed to invalidate KeyboardCache for user {user_id}: {e}")
                finally:
                    total_attempts += 1
                
                # 4. Invalidate production cache balance-related entries
                try:
                    from utils.production_cache import delete_cached
                    
                    # Clear user-specific balance keys
                    balance_keys = [
                        f"user_balance_{user_id}",
                        f"wallet_data_{user_id}",
                        f"available_balance_{user_id}",
                        f"total_balance_{user_id}"
                    ]
                    
                    for key in balance_keys:
                        delete_cached(key)
                    
                    success_count += 1
                    logger.debug(f"CACHE_INVALIDATION: ProductionCache balance keys cleared for user {user_id}")
                except Exception as e:
                    logger.warning(f"Failed to invalidate ProductionCache for user {user_id}: {e}")
                finally:
                    total_attempts += 1
                
                # 5. Clear any LRU caches that might contain balance data
                try:
                    # Force clear of any @lru_cache decorated functions that might cache balance
                    self._clear_lru_caches_for_user(user_id)
                    success_count += 1
                    logger.debug(f"CACHE_INVALIDATION: LRU caches cleared for user {user_id}")
                except Exception as e:
                    logger.warning(f"Failed to clear LRU caches for user {user_id}: {e}")
                finally:
                    total_attempts += 1
                
                # Log comprehensive invalidation result
                if success_count == total_attempts:
                    logger.critical(
                        f"BALANCE_CACHE_INVALIDATED: User {user_id}, Operation: {operation_type}, "
                        f"Cleared {success_count}/{total_attempts} cache systems successfully"
                    )
                    return True
                else:
                    logger.error(
                        f"PARTIAL_CACHE_INVALIDATION: User {user_id}, Operation: {operation_type}, "
                        f"Cleared {success_count}/{total_attempts} cache systems - some failures occurred"
                    )
                    return False
                    
        except Exception as e:
            logger.error(f"Critical error in balance cache invalidation for user {user_id}: {e}")
            return False
    
    def invalidate_multiple_users(self, user_ids: List[int], operation_type: str = "batch_operation") -> bool:
        """Invalidate balance caches for multiple users (e.g., in escrow releases)"""
        try:
            success_count = 0
            for user_id in user_ids:
                if self.invalidate_user_balance_caches(user_id, operation_type):
                    success_count += 1
            
            logger.info(
                f"BATCH_CACHE_INVALIDATION: {success_count}/{len(user_ids)} users processed for {operation_type}"
            )
            return success_count == len(user_ids)
            
        except Exception as e:
            logger.error(f"Error in batch cache invalidation: {e}")
            return False
    
    def force_clear_all_balance_caches(self) -> bool:
        """Emergency function to clear all balance caches system-wide"""
        try:
            with self._lock:
                logger.warning("EMERGENCY_CACHE_CLEAR: Clearing all balance caches system-wide")
                
                # Clear all cache systems
                try:
                    from utils.performance_cache import BALANCE_CACHE
                    BALANCE_CACHE.clear()
                    logger.info("Cleared BALANCE_CACHE")
                except Exception as e:
                    logger.warning(f"Failed to clear BALANCE_CACHE: {e}")
                
                try:
                    from utils.wallet_performance import WALLET_DISPLAY_CACHE
                    WALLET_DISPLAY_CACHE.clear()
                    logger.info("Cleared WALLET_DISPLAY_CACHE")
                except Exception as e:
                    logger.warning(f"Failed to clear WALLET_DISPLAY_CACHE: {e}")
                
                try:
                    from utils.production_cache import clear_cache
                    clear_cache()
                    logger.info("Cleared ProductionCache")
                except Exception as e:
                    logger.warning(f"Failed to clear ProductionCache: {e}")
                
                logger.critical("EMERGENCY_CACHE_CLEAR_COMPLETED: All balance caches cleared")
                return True
                
        except Exception as e:
            logger.error(f"Failed to clear all balance caches: {e}")
            return False
    
    def _clear_lru_caches_for_user(self, user_id: int):
        """Clear LRU caches that might contain user balance data"""
        try:
            # Try to clear keyboard cache LRU functions
            from utils.keyboard_cache import create_main_menu_keyboard_cached
            if hasattr(create_main_menu_keyboard_cached, 'cache_clear'):
                create_main_menu_keyboard_cached.cache_clear()
        except (ImportError, AttributeError) as e:
            logger.debug(f"Main menu keyboard cache not available: {e}")
        
        try:
            from utils.keyboard_cache import create_payment_keyboard_cached
            if hasattr(create_payment_keyboard_cached, 'cache_clear'):
                create_payment_keyboard_cached.cache_clear()
        except (ImportError, AttributeError) as e:
            logger.debug(f"Payment keyboard cache not available: {e}")
    
    def invalidate_user_balance_cache(self, user_id: int, operation_type: str = "unknown") -> bool:
        """
        BACKWARD COMPATIBILITY ALIAS: Calls the plural method
        Some legacy code still uses the singular method name - this provides compatibility.
        
        Args:
            user_id: User whose balance caches should be invalidated
            operation_type: Type of operation that triggered invalidation
        
        Returns:
            bool: True if all invalidations succeeded
        """
        logger.warning("DEPRECATED: Using singular invalidate_user_balance_cache method - update to plural invalidate_user_balance_caches")
        return self.invalidate_user_balance_caches(user_id, operation_type)
    
    def get_cache_invalidation_stats(self) -> dict:
        """Get statistics about current cache states for monitoring"""
        try:
            stats = {}
            
            # PerformanceCache stats
            try:
                from utils.performance_cache import PerformanceCache
                stats['performance_cache'] = PerformanceCache.get_cache_stats()
            except (ImportError, AttributeError) as e:
                logger.debug(f"PerformanceCache stats not available: {e}")
            
            # Wallet cache stats
            try:
                from utils.wallet_performance import WALLET_DISPLAY_CACHE
                stats['wallet_display_cache_size'] = len(WALLET_DISPLAY_CACHE)
            except (ImportError, AttributeError) as e:
                logger.debug(f"Wallet display cache stats not available: {e}")
            
            # Production cache stats
            try:
                from utils.production_cache import get_cache_stats
                stats['production_cache'] = get_cache_stats()
            except (ImportError, AttributeError) as e:
                logger.debug(f"Production cache stats not available: {e}")
            
            return stats
            
        except Exception as e:
            logger.error(f"Error getting cache stats: {e}")
            return {}


# Global instance for service-wide use
balance_cache_invalidation_service = BalanceCacheInvalidationService()