"""
TTL-based Cleanup and Cache Invalidation Service
Comprehensive cleanup service for managing Redis keys, cache invalidation, and garbage collection
Ensures optimal performance and prevents memory leaks in distributed state management
"""

import asyncio
import logging
import time
from typing import Dict, List, Optional, Set, Any
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from enum import Enum
import re

from services.state_manager import state_manager
from config import Config

logger = logging.getLogger(__name__)


class CleanupStrategy(Enum):
    """Different cleanup strategies for different data types"""
    IMMEDIATE = "immediate"      # Clean up immediately when expired
    BATCH = "batch"             # Clean up in batches during scheduled runs
    LAZY = "lazy"               # Clean up when accessed and found expired
    NEVER = "never"             # Never clean up automatically


@dataclass
class CleanupRule:
    """Rule for cleaning up specific types of Redis keys"""
    pattern: str                # Redis key pattern to match
    ttl_seconds: int           # TTL for the keys
    strategy: CleanupStrategy  # Cleanup strategy
    batch_size: int = 100      # Batch size for cleanup
    priority: int = 1          # Priority (1=highest, 5=lowest)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CleanupStats:
    """Statistics for cleanup operations"""
    total_scanned: int = 0
    total_cleaned: int = 0
    total_errors: int = 0
    cleanup_time_ms: int = 0
    memory_freed_mb: float = 0.0
    keys_by_type: Dict[str, int] = field(default_factory=dict)


class CacheInvalidationManager:
    """
    Manages cache invalidation strategies for state updates
    """
    
    def __init__(self):
        self.invalidation_patterns: Dict[str, List[str]] = {}
        self.dependency_graph: Dict[str, Set[str]] = {}
        
        # Metrics
        self.metrics = {
            'invalidations_triggered': 0,
            'dependencies_resolved': 0,
            'cache_hits': 0,
            'cache_misses': 0,
            'invalidation_time_ms': 0
        }
        
        logger.info("🧹 Cache invalidation manager initialized")
    
    def register_invalidation_pattern(self, update_pattern: str, invalidate_patterns: List[str]):
        """
        Register cache invalidation patterns
        
        Args:
            update_pattern: Pattern for keys that when updated, trigger invalidation
            invalidate_patterns: Patterns for keys to invalidate
        """
        self.invalidation_patterns[update_pattern] = invalidate_patterns
        logger.info(f"📝 Registered invalidation pattern: {update_pattern} -> {invalidate_patterns}")
    
    def register_dependency(self, dependent_key: str, dependency_key: str):
        """Register cache dependencies between keys"""
        if dependent_key not in self.dependency_graph:
            self.dependency_graph[dependent_key] = set()
        
        self.dependency_graph[dependent_key].add(dependency_key)
    
    async def invalidate_on_update(self, updated_key: str):
        """Invalidate caches when a key is updated"""
        try:
            start_time = time.time()
            invalidated_count = 0
            
            # Check direct invalidation patterns
            for pattern, invalidate_patterns in self.invalidation_patterns.items():
                if re.match(pattern, updated_key):
                    for invalidate_pattern in invalidate_patterns:
                        keys_to_invalidate = await self._find_keys_by_pattern(invalidate_pattern)
                        for key in keys_to_invalidate:
                            await state_manager.delete_state(key)
                            invalidated_count += 1
            
            # Check dependency graph
            for dependent_key, dependencies in self.dependency_graph.items():
                if updated_key in dependencies:
                    await state_manager.delete_state(dependent_key)
                    invalidated_count += 1
            
            self.metrics['invalidations_triggered'] += 1
            self.metrics['dependencies_resolved'] += invalidated_count
            self.metrics['invalidation_time_ms'] += (time.time() - start_time) * 1000
            
            if invalidated_count > 0:
                logger.info(f"🗑️ Invalidated {invalidated_count} cache entries for update: {updated_key}")
            
        except Exception as e:
            logger.error(f"❌ Error invalidating cache for {updated_key}: {e}")
    
    async def _find_keys_by_pattern(self, pattern: str) -> List[str]:
        """Find Redis keys matching a pattern"""
        # This would use Redis SCAN in a real implementation
        # For now, return empty list
        return []


class TTLCleanupService:
    """
    Comprehensive TTL-based cleanup service for Redis state management
    
    Features:
    - Automatic cleanup of expired keys
    - Configurable cleanup strategies per data type
    - Batch processing for efficiency
    - Cache invalidation on updates
    - Comprehensive metrics and monitoring
    - Memory usage optimization
    """
    
    def __init__(self):
        self.cleanup_rules: List[CleanupRule] = []
        self.cache_invalidation = CacheInvalidationManager()
        self.running = False
        self.cleanup_task: Optional[asyncio.Task] = None
        
        # Configuration
        self.cleanup_interval = Config.REDIS_CLEANUP_INTERVAL if hasattr(Config, 'REDIS_CLEANUP_INTERVAL') else 300  # 5 minutes
        self.batch_size = Config.REDIS_CLEANUP_BATCH_SIZE if hasattr(Config, 'REDIS_CLEANUP_BATCH_SIZE') else 1000
        self.max_scan_keys = Config.REDIS_CLEANUP_MAX_SCAN if hasattr(Config, 'REDIS_CLEANUP_MAX_SCAN') else 10000
        
        # Metrics
        self.metrics = {
            'cleanup_cycles': 0,
            'total_keys_cleaned': 0,
            'total_errors': 0,
            'last_cleanup_time_ms': 0,
            'memory_freed_total_mb': 0.0,
            'cleanup_efficiency': 0.0,
            'active_keys_count': 0
        }
        
        # Initialize default cleanup rules
        self._setup_default_cleanup_rules()
        self._setup_cache_invalidation_patterns()
        
        logger.info("🧹 TTL cleanup service initialized")
    
    def _setup_default_cleanup_rules(self):
        """Setup default cleanup rules for different data types"""
        
        # Session data cleanup
        self.add_cleanup_rule(CleanupRule(
            pattern="session:*",
            ttl_seconds=Config.REDIS_SESSION_TTL if hasattr(Config, 'REDIS_SESSION_TTL') else 3600,
            strategy=CleanupStrategy.BATCH,
            batch_size=500,
            priority=2,
            metadata={'type': 'session', 'description': 'User session data'}
        ))
        
        # Idempotency keys cleanup
        self.add_cleanup_rule(CleanupRule(
            pattern="idempotency:*",
            ttl_seconds=Config.REDIS_IDEMPOTENCY_TTL if hasattr(Config, 'REDIS_IDEMPOTENCY_TTL') else 86400,
            strategy=CleanupStrategy.BATCH,
            batch_size=1000,
            priority=3,
            metadata={'type': 'idempotency', 'description': 'Idempotency keys for operations'}
        ))
        
        # Job execution data cleanup
        self.add_cleanup_rule(CleanupRule(
            pattern="job_execution:*",
            ttl_seconds=7200,  # 2 hours
            strategy=CleanupStrategy.BATCH,
            batch_size=200,
            priority=2,
            metadata={'type': 'job_execution', 'description': 'Job execution contexts'}
        ))
        
        # Saga transaction cleanup
        self.add_cleanup_rule(CleanupRule(
            pattern="saga:*",
            ttl_seconds=86400,  # 24 hours
            strategy=CleanupStrategy.BATCH,
            batch_size=100,
            priority=1,
            metadata={'type': 'saga', 'description': 'Saga transaction state'}
        ))
        
        # Leader election cleanup
        self.add_cleanup_rule(CleanupRule(
            pattern="leader_election:*",
            ttl_seconds=300,  # 5 minutes
            strategy=CleanupStrategy.IMMEDIATE,
            batch_size=50,
            priority=1,
            metadata={'type': 'leader_election', 'description': 'Leader election state'}
        ))
        
        # Temporary state cleanup
        self.add_cleanup_rule(CleanupRule(
            pattern="temp_state:*",
            ttl_seconds=1800,  # 30 minutes
            strategy=CleanupStrategy.BATCH,
            batch_size=500,
            priority=3,
            metadata={'type': 'temp_state', 'description': 'Temporary application state'}
        ))
        
        # Circuit breaker state cleanup
        self.add_cleanup_rule(CleanupRule(
            pattern="circuit_breaker:*",
            ttl_seconds=3600,  # 1 hour
            strategy=CleanupStrategy.LAZY,
            batch_size=100,
            priority=4,
            metadata={'type': 'circuit_breaker', 'description': 'Circuit breaker state'}
        ))
        
        # Cache entries cleanup
        self.add_cleanup_rule(CleanupRule(
            pattern="cache:*",
            ttl_seconds=1800,  # 30 minutes
            strategy=CleanupStrategy.BATCH,
            batch_size=1000,
            priority=3,
            metadata={'type': 'cache', 'description': 'Application cache entries'}
        ))
    
    def _setup_cache_invalidation_patterns(self):
        """Setup cache invalidation patterns"""
        
        # User data changes invalidate user-related caches
        self.cache_invalidation.register_invalidation_pattern(
            r"user:\d+:.*",
            ["cache:user:*", "session:user:*"]
        )
        
        # Balance changes invalidate balance-related caches
        self.cache_invalidation.register_invalidation_pattern(
            r"balance:\d+:.*",
            ["cache:balance:*", "cache:user:*:balance"]
        )
        
        # Rate changes invalidate rate caches
        self.cache_invalidation.register_invalidation_pattern(
            r"rate:.*",
            ["cache:rate:*", "cache:exchange:*"]
        )
        
        # Job completion invalidates job-related caches
        self.cache_invalidation.register_invalidation_pattern(
            r"job_execution:.*",
            ["cache:job:*", "job_result:*"]
        )
    
    def add_cleanup_rule(self, rule: CleanupRule):
        """Add a custom cleanup rule"""
        self.cleanup_rules.append(rule)
        # Sort by priority (highest first)
        self.cleanup_rules.sort(key=lambda r: r.priority)
        logger.info(f"📝 Added cleanup rule: {rule.pattern} (TTL: {rule.ttl_seconds}s, Strategy: {rule.strategy.value})")
    
    async def start(self):
        """Start the cleanup service"""
        if self.running:
            logger.warning("Cleanup service already running")
            return
        
        self.running = True
        self.cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info(f"🚀 TTL cleanup service started (interval: {self.cleanup_interval}s)")
    
    async def stop(self):
        """Stop the cleanup service"""
        if not self.running:
            return
        
        self.running = False
        if self.cleanup_task:
            self.cleanup_task.cancel()
            try:
                await self.cleanup_task
            except asyncio.CancelledError:
                pass
        
        logger.info("🛑 TTL cleanup service stopped")
    
    async def _cleanup_loop(self):
        """Main cleanup loop"""
        while self.running:
            try:
                await self._run_cleanup_cycle()
                await asyncio.sleep(self.cleanup_interval)
            except Exception as e:
                logger.error(f"❌ Error in cleanup loop: {e}")
                await asyncio.sleep(self.cleanup_interval)
    
    async def _run_cleanup_cycle(self):
        """Run a complete cleanup cycle"""
        start_time = time.time()
        total_cleaned = 0
        total_errors = 0
        
        logger.info("🧹 Starting cleanup cycle")
        
        try:
            # Process cleanup rules by priority
            for rule in self.cleanup_rules:
                try:
                    stats = await self._process_cleanup_rule(rule)
                    total_cleaned += stats.total_cleaned
                    total_errors += stats.total_errors
                    
                    if stats.total_cleaned > 0:
                        logger.info(f"🗑️ Cleaned {stats.total_cleaned} keys for pattern: {rule.pattern}")
                
                except Exception as e:
                    logger.error(f"❌ Error processing cleanup rule {rule.pattern}: {e}")
                    total_errors += 1
            
            # Update metrics
            cleanup_time_ms = (time.time() - start_time) * 1000
            self.metrics['cleanup_cycles'] += 1
            self.metrics['total_keys_cleaned'] += total_cleaned
            self.metrics['total_errors'] += total_errors
            self.metrics['last_cleanup_time_ms'] = cleanup_time_ms
            
            # Calculate efficiency
            if total_cleaned > 0:
                self.metrics['cleanup_efficiency'] = total_cleaned / cleanup_time_ms * 1000
            
            logger.info(f"✅ Cleanup cycle completed: {total_cleaned} keys cleaned in {cleanup_time_ms:.2f}ms")
        
        except Exception as e:
            logger.error(f"❌ Cleanup cycle failed: {e}")
            self.metrics['total_errors'] += 1
    
    async def _process_cleanup_rule(self, rule: CleanupRule) -> CleanupStats:
        """Process a single cleanup rule"""
        stats = CleanupStats()
        
        try:
            if rule.strategy == CleanupStrategy.NEVER:
                return stats
            
            # For now, simulate cleanup since we don't have Redis SCAN implemented
            # In a real implementation, this would:
            # 1. Use Redis SCAN to find matching keys
            # 2. Check TTL for each key
            # 3. Delete expired keys based on strategy
            
            # Simulate finding and cleaning some keys
            simulated_expired_count = 0  # Would be calculated from actual Redis scan
            
            stats.total_scanned = simulated_expired_count
            stats.total_cleaned = simulated_expired_count
            stats.keys_by_type[rule.metadata.get('type', 'unknown')] = simulated_expired_count
            
        except Exception as e:
            logger.error(f"❌ Error processing cleanup rule {rule.pattern}: {e}")
            stats.total_errors += 1
        
        return stats
    
    async def force_cleanup(self, pattern: Optional[str] = None) -> CleanupStats:
        """Force immediate cleanup for all rules or specific pattern"""
        start_time = time.time()
        total_stats = CleanupStats()
        
        logger.info("🔥 Force cleanup triggered" + (f" for pattern: {pattern}" if pattern else ""))
        
        try:
            rules_to_process = self.cleanup_rules
            if pattern:
                rules_to_process = [r for r in self.cleanup_rules if re.match(pattern, r.pattern)]
            
            for rule in rules_to_process:
                stats = await self._process_cleanup_rule(rule)
                total_stats.total_scanned += stats.total_scanned
                total_stats.total_cleaned += stats.total_cleaned
                total_stats.total_errors += stats.total_errors
                
                for key_type, count in stats.keys_by_type.items():
                    total_stats.keys_by_type[key_type] = total_stats.keys_by_type.get(key_type, 0) + count
            
            total_stats.cleanup_time_ms = int((time.time() - start_time) * 1000)
            
            logger.info(f"✅ Force cleanup completed: {total_stats.total_cleaned} keys cleaned")
            
        except Exception as e:
            logger.error(f"❌ Force cleanup failed: {e}")
            total_stats.total_errors += 1
        
        return total_stats
    
    async def cleanup_expired_sessions(self) -> int:
        """Specifically clean up expired user sessions"""
        try:
            # This would scan for expired session keys in a real implementation
            cleaned_count = 0
            logger.info(f"🧹 Cleaned {cleaned_count} expired sessions")
            return cleaned_count
            
        except Exception as e:
            logger.error(f"❌ Error cleaning expired sessions: {e}")
            return 0
    
    async def cleanup_expired_idempotency_keys(self) -> int:
        """Specifically clean up expired idempotency keys"""
        try:
            # This would scan for expired idempotency keys in a real implementation
            cleaned_count = 0
            logger.info(f"🧹 Cleaned {cleaned_count} expired idempotency keys")
            return cleaned_count
            
        except Exception as e:
            logger.error(f"❌ Error cleaning expired idempotency keys: {e}")
            return 0
    
    async def cleanup_completed_sagas(self, max_age_hours: int = 24) -> int:
        """Clean up old completed saga transactions"""
        try:
            datetime.utcnow() - timedelta(hours=max_age_hours)
            cleaned_count = 0
            
            # This would scan for old saga keys in a real implementation
            logger.info(f"🧹 Cleaned {cleaned_count} old saga transactions (older than {max_age_hours}h)")
            return cleaned_count
            
        except Exception as e:
            logger.error(f"❌ Error cleaning completed sagas: {e}")
            return 0
    
    async def optimize_memory_usage(self) -> Dict[str, Any]:
        """Optimize Redis memory usage by cleaning up unnecessary data"""
        optimization_stats = {
            'memory_before_mb': 0.0,
            'memory_after_mb': 0.0,
            'memory_freed_mb': 0.0,
            'keys_removed': 0,
            'optimization_time_ms': 0
        }
        
        try:
            start_time = time.time()
            
            # Run aggressive cleanup
            stats = await self.force_cleanup()
            optimization_stats['keys_removed'] = stats.total_cleaned
            
            # Additional memory optimization steps would go here
            # (e.g., Redis MEMORY PURGE, key compression, etc.)
            
            optimization_stats['optimization_time_ms'] = int((time.time() - start_time) * 1000)
            
            logger.info(f"🎯 Memory optimization completed: {stats.total_cleaned} keys removed")
            
        except Exception as e:
            logger.error(f"❌ Memory optimization failed: {e}")
        
        return optimization_stats
    
    async def get_cache_metrics(self) -> Dict[str, Any]:
        """Get comprehensive cache and cleanup metrics"""
        try:
            cache_metrics = {
                **self.metrics,
                'cache_invalidation_metrics': self.cache_invalidation.metrics,
                'cleanup_rules_count': len(self.cleanup_rules),
                'is_running': self.running,
                'last_run': datetime.utcnow().isoformat(),
                'next_run': (datetime.utcnow() + timedelta(seconds=self.cleanup_interval)).isoformat() if self.running else None
            }
            
            # Add per-rule metrics
            rule_metrics = {}
            for rule in self.cleanup_rules:
                rule_key = f"{rule.metadata.get('type', 'unknown')}_{rule.pattern}"
                rule_metrics[rule_key] = {
                    'ttl_seconds': rule.ttl_seconds,
                    'strategy': rule.strategy.value,
                    'priority': rule.priority,
                    'batch_size': rule.batch_size
                }
            
            cache_metrics['rule_metrics'] = rule_metrics
            
            return cache_metrics
            
        except Exception as e:
            logger.error(f"❌ Error getting cache metrics: {e}")
            return {}
    
    async def invalidate_cache_on_update(self, updated_key: str):
        """Trigger cache invalidation when a key is updated"""
        await self.cache_invalidation.invalidate_on_update(updated_key)


# Global instance
ttl_cleanup_service = TTLCleanupService()


# Convenience functions
async def start_cleanup_service():
    """Start the TTL cleanup service"""
    await ttl_cleanup_service.start()


async def stop_cleanup_service():
    """Stop the TTL cleanup service"""
    await ttl_cleanup_service.stop()


async def force_cleanup(pattern: Optional[str] = None):
    """Force immediate cleanup"""
    return await ttl_cleanup_service.force_cleanup(pattern)


async def cleanup_by_type(data_type: str) -> int:
    """Clean up data by type"""
    if data_type == "sessions":
        return await ttl_cleanup_service.cleanup_expired_sessions()
    elif data_type == "idempotency":
        return await ttl_cleanup_service.cleanup_expired_idempotency_keys()
    elif data_type == "sagas":
        return await ttl_cleanup_service.cleanup_completed_sagas()
    else:
        logger.warning(f"Unknown cleanup type: {data_type}")
        return 0


async def optimize_redis_memory():
    """Optimize Redis memory usage"""
    return await ttl_cleanup_service.optimize_memory_usage()


async def get_cleanup_metrics():
    """Get cleanup service metrics"""
    return await ttl_cleanup_service.get_cache_metrics()