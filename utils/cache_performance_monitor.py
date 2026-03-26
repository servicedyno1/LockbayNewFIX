"""
Cache Performance Monitor
Monitors and optimizes cache performance, provides cache warming and statistics
"""

import asyncio
import logging
import time
from typing import Dict, Any

logger = logging.getLogger(__name__)


class CachePerformanceMonitor:
    """Monitors and optimizes cache performance"""
    
    def __init__(self):
        self.last_stats = {}
        self.optimization_running = False
        
    async def initialize_cache_system(self):
        """Initialize cache system with warming and optimization"""
        try:
            from utils.production_cache import setup_production_cache, get_cache_stats
            
            logger.info("🚀 Initializing optimized cache system...")
            
            # Setup production cache
            setup_production_cache()
            
            # Get initial stats
            initial_stats = get_cache_stats()
            logger.info(f"📊 Initial cache state: {initial_stats['size']}/{initial_stats['max_size']} items, {initial_stats['hit_rate']} hit rate")
            
            # Warm the cache with frequently accessed data
            await self.warm_cache()
            
            # Get post-warming stats
            post_stats = get_cache_stats()
            logger.info(f"🔥 Post-warming cache state: {post_stats['size']}/{post_stats['max_size']} items, {post_stats['hit_rate']} hit rate")
            
            # Schedule periodic optimization
            asyncio.create_task(self.periodic_optimization())
            
            logger.info("✅ Cache system initialization completed successfully")
            return True
            
        except Exception as e:
            logger.error(f"Cache system initialization failed: {e}")
            return False
    
    async def warm_cache(self):
        """Warm cache with frequently accessed data"""
        try:
            logger.info("🔥 Starting comprehensive cache warming...")
            
            # Warm FastForex rates
            await self._warm_exchange_rates()
            
            # Warm user data (if we have common user IDs)
            await self._warm_user_data()
            
            # Warm configuration data
            await self._warm_config_data()
            
            logger.info("✅ Cache warming completed successfully")
            
        except Exception as e:
            logger.error(f"Cache warming failed: {e}")
    
    async def _warm_exchange_rates(self):
        """Warm exchange rate cache"""
        try:
            from services.fastforex_service import warm_fastforex_cache
            await warm_fastforex_cache()
            logger.info("📈 Exchange rates cache warmed")
        except Exception as e:
            logger.warning(f"Exchange rate warming failed: {e}")
    
    async def _warm_user_data(self):
        """Warm user data cache with recent users"""
        try:
            from database import SessionLocal
            from models import User
            
            # Get some recent users to warm the cache
            session = SessionLocal()
            try:
                recent_users = session.query(User).filter(
                    User.total_trades > 0
                ).order_by(User.last_seen.desc()).limit(10).all()
                
                for user in recent_users:
                    from utils.production_cache import UserCacheOptimized
                    UserCacheOptimized.cache_user_data(user, ttl=600)
                
                logger.info(f"👥 Warmed cache for {len(recent_users)} recent users")
                
            finally:
                session.close()
                
        except Exception as e:
            logger.warning(f"User data warming failed: {e}")
    
    async def _warm_config_data(self):
        """Warm configuration data cache"""
        try:
            from utils.production_cache import set_cached
            from config import Config
            
            # Cache frequently accessed config values
            config_data = {
                'exchange_markup': getattr(Config, 'EXCHANGE_MARKUP_PERCENTAGE', 2.0),
                'wallet_markup': getattr(Config, 'WALLET_NGN_MARKUP_PERCENTAGE', 2.0),
                'min_trade_amounts': {
                    'BTC': 0.001,
                    'ETH': 0.01,
                    'LTC': 0.1,
                    'USDT': 10
                }
            }
            
            set_cached('system_config', config_data, ttl=3600)  # 1 hour
            logger.info("⚙️ System configuration cache warmed")
            
        except Exception as e:
            logger.warning(f"Config data warming failed: {e}")
    
    async def get_performance_report(self) -> Dict[str, Any]:
        """Generate comprehensive cache performance report"""
        try:
            from utils.production_cache import get_cache_stats
            
            current_stats = get_cache_stats()
            
            # Calculate improvements since last check
            improvements = {}
            if self.last_stats:
                improvements = {
                    'hit_rate_change': float(current_stats['hit_rate'].replace('%', '')) - float(self.last_stats.get('hit_rate', '0%').replace('%', '')),
                    'size_change': current_stats['size'] - self.last_stats.get('size', 0),
                    'hits_change': current_stats['hits'] - self.last_stats.get('hits', 0)
                }
            
            self.last_stats = current_stats
            
            report = {
                'current_stats': current_stats,
                'improvements': improvements,
                'timestamp': time.time(),
                'recommendations': self._generate_recommendations(current_stats)
            }
            
            return report
            
        except Exception as e:
            logger.error(f"Failed to generate performance report: {e}")
            return {}
    
    def _generate_recommendations(self, stats: Dict[str, Any]) -> list:
        """Generate optimization recommendations based on stats"""
        recommendations = []
        
        hit_rate = float(stats['hit_rate'].replace('%', ''))
        
        if hit_rate < 10:
            recommendations.append("Critical: Cache hit rate is very low. Check if caching is being used in services.")
        elif hit_rate < 30:
            recommendations.append("Low cache hit rate. Consider increasing cache TTL for stable data.")
        elif hit_rate > 80:
            recommendations.append("Excellent cache performance! Consider increasing cache size for more data.")
        
        if stats['size'] == 0:
            recommendations.append("Cache is empty. Ensure cache warming is working and services are using cache.")
        
        if stats['evictions'] > stats['hits']:
            recommendations.append("High eviction rate. Consider increasing cache size or reducing TTL for less important data.")
        
        return recommendations
    
    async def periodic_optimization(self):
        """Periodic cache optimization and monitoring"""
        while True:
            try:
                await asyncio.sleep(300)  # Run every 5 minutes
                
                if not self.optimization_running:
                    self.optimization_running = True
                    
                    # Generate performance report
                    report = await self.get_performance_report()
                    
                    if report and report.get('current_stats'):
                        stats = report['current_stats']
                        hit_rate = float(stats['hit_rate'].replace('%', ''))
                        
                        logger.info(f"📊 Cache Performance: {stats['size']}/{stats['max_size']} items, {stats['hit_rate']} hit rate, {stats['hits']} hits")
                        
                        # Auto-optimization if hit rate is very low
                        if hit_rate < 5 and stats['size'] < 100:
                            logger.warning("🔧 Low cache utilization detected, triggering cache warming...")
                            await self.warm_cache()
                    
                    self.optimization_running = False
                    
            except Exception as e:
                logger.error(f"Periodic optimization error: {e}")
                self.optimization_running = False
            

# Global instance
cache_performance_monitor = CachePerformanceMonitor()


async def initialize_optimized_cache():
    """Initialize optimized cache system"""
    return await cache_performance_monitor.initialize_cache_system()


async def get_cache_performance_report():
    """Get cache performance report"""
    return await cache_performance_monitor.get_performance_report()


def get_cache_recommendations():
    """Get cache optimization recommendations"""
    try:
        from utils.production_cache import get_cache_stats
        stats = get_cache_stats()
        return cache_performance_monitor._generate_recommendations(stats)
    except Exception as e:
        logger.error(f"Failed to get recommendations: {e}")
        return []