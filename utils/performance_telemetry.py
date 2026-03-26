"""
Lightweight Performance Telemetry Module
Tracks cache hit rates and webhook latency for performance monitoring
"""

import logging
from typing import Dict, Optional
from dataclasses import dataclass, field
from datetime import datetime
from collections import deque

logger = logging.getLogger(__name__)


@dataclass
class CacheMetrics:
    """Metrics for a specific cache"""
    hits: int = 0
    misses: int = 0
    invalidations: int = 0
    
    @property
    def total_requests(self) -> int:
        return self.hits + self.misses
    
    @property
    def hit_rate(self) -> float:
        """Calculate cache hit rate as percentage"""
        if self.total_requests == 0:
            return 0.0
        return (self.hits / self.total_requests) * 100


@dataclass
class LatencyMetrics:
    """Metrics for latency tracking"""
    samples: deque = field(default_factory=lambda: deque(maxlen=1000))
    total_time: float = 0.0
    count: int = 0
    
    def record(self, latency_ms: float) -> None:
        """Record a latency sample"""
        self.samples.append(latency_ms)
        self.total_time += latency_ms
        self.count += 1
    
    @property
    def average(self) -> float:
        """Calculate average latency"""
        if self.count == 0:
            return 0.0
        return self.total_time / self.count
    
    @property
    def p95(self) -> float:
        """Calculate 95th percentile latency"""
        if not self.samples:
            return 0.0
        sorted_samples = sorted(self.samples)
        idx = int(len(sorted_samples) * 0.95)
        return sorted_samples[idx] if idx < len(sorted_samples) else sorted_samples[-1]
    
    @property
    def p99(self) -> float:
        """Calculate 99th percentile latency"""
        if not self.samples:
            return 0.0
        sorted_samples = sorted(self.samples)
        idx = int(len(sorted_samples) * 0.99)
        return sorted_samples[idx] if idx < len(sorted_samples) else sorted_samples[-1]


class PerformanceTelemetry:
    """Centralized performance telemetry tracking"""
    
    def __init__(self):
        # Cache metrics
        self._cache_metrics: Dict[str, CacheMetrics] = {}
        
        # Latency metrics
        self._latency_metrics: Dict[str, LatencyMetrics] = {}
        
        # Initialization time
        self._start_time = datetime.utcnow()
        
        logger.info("📊 Performance telemetry initialized")
    
    # Cache Tracking
    
    def record_cache_hit(self, cache_name: str) -> None:
        """Record a cache hit"""
        if cache_name not in self._cache_metrics:
            self._cache_metrics[cache_name] = CacheMetrics()
        self._cache_metrics[cache_name].hits += 1
    
    def record_cache_miss(self, cache_name: str) -> None:
        """Record a cache miss"""
        if cache_name not in self._cache_metrics:
            self._cache_metrics[cache_name] = CacheMetrics()
        self._cache_metrics[cache_name].misses += 1
    
    def record_cache_invalidation(self, cache_name: str) -> None:
        """Record a cache invalidation"""
        if cache_name not in self._cache_metrics:
            self._cache_metrics[cache_name] = CacheMetrics()
        self._cache_metrics[cache_name].invalidations += 1
    
    def get_cache_metrics(self, cache_name: str) -> Optional[CacheMetrics]:
        """Get metrics for a specific cache"""
        return self._cache_metrics.get(cache_name)
    
    # Latency Tracking
    
    def record_latency(self, operation_name: str, latency_ms: float) -> None:
        """Record operation latency in milliseconds"""
        if operation_name not in self._latency_metrics:
            self._latency_metrics[operation_name] = LatencyMetrics()
        self._latency_metrics[operation_name].record(latency_ms)
    
    def get_latency_metrics(self, operation_name: str) -> Optional[LatencyMetrics]:
        """Get latency metrics for a specific operation"""
        return self._latency_metrics.get(operation_name)
    
    # Reporting
    
    def get_summary(self) -> Dict[str, any]:
        """Get comprehensive performance summary"""
        uptime = (datetime.utcnow() - self._start_time).total_seconds()
        
        cache_summary = {}
        for name, metrics in self._cache_metrics.items():
            cache_summary[name] = {
                'hits': metrics.hits,
                'misses': metrics.misses,
                'invalidations': metrics.invalidations,
                'total_requests': metrics.total_requests,
                'hit_rate': f"{metrics.hit_rate:.1f}%"
            }
        
        latency_summary = {}
        for name, metrics in self._latency_metrics.items():
            latency_summary[name] = {
                'count': metrics.count,
                'average_ms': f"{metrics.average:.1f}",
                'p95_ms': f"{metrics.p95:.1f}",
                'p99_ms': f"{metrics.p99:.1f}"
            }
        
        return {
            'uptime_seconds': uptime,
            'cache_metrics': cache_summary,
            'latency_metrics': latency_summary
        }
    
    def log_summary(self) -> None:
        """Log performance summary"""
        summary = self.get_summary()
        
        logger.info("=" * 60)
        logger.info("📊 PERFORMANCE TELEMETRY SUMMARY")
        logger.info("=" * 60)
        logger.info(f"⏱️  Uptime: {summary['uptime_seconds']:.1f}s")
        
        if summary['cache_metrics']:
            logger.info("\n📦 CACHE METRICS:")
            for cache_name, metrics in summary['cache_metrics'].items():
                logger.info(f"  {cache_name}:")
                logger.info(f"    • Requests: {metrics['total_requests']} (hits: {metrics['hits']}, misses: {metrics['misses']})")
                logger.info(f"    • Hit Rate: {metrics['hit_rate']}")
                logger.info(f"    • Invalidations: {metrics['invalidations']}")
        
        if summary['latency_metrics']:
            logger.info("\n⏱️  LATENCY METRICS:")
            for op_name, metrics in summary['latency_metrics'].items():
                logger.info(f"  {op_name}:")
                logger.info(f"    • Count: {metrics['count']}")
                logger.info(f"    • Avg: {metrics['average_ms']}ms")
                logger.info(f"    • P95: {metrics['p95_ms']}ms")
                logger.info(f"    • P99: {metrics['p99_ms']}ms")
        
        logger.info("=" * 60)


# Global telemetry instance
telemetry = PerformanceTelemetry()
