"""
Standardized Performance Metrics Collection Framework
Provides unified metrics collection, naming, and reporting across all system components
"""

import time
import logging
import asyncio
from typing import Dict, List, Optional, Any, Protocol
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from enum import Enum
from collections import deque
import threading
from contextlib import asynccontextmanager

# Import existing safe timing utilities
from utils.safe_timing import safe_duration_calculation
from utils.shared_cpu_monitor import get_cpu_usage, get_memory_usage

logger = logging.getLogger(__name__)


class MetricType(Enum):
    """Standardized metric types"""
    COUNTER = "counter"          # Monotonically increasing values (e.g., request count)
    GAUGE = "gauge"             # Point-in-time values (e.g., memory usage)
    HISTOGRAM = "histogram"     # Distribution of values (e.g., response times)
    TIMER = "timer"             # Duration measurements (e.g., operation time)


class MetricUnit(Enum):
    """Standardized metric units"""
    # Memory units
    BYTES = "bytes"
    KILOBYTES = "kb"
    MEGABYTES = "mb"
    GIGABYTES = "gb"
    
    # Time units
    NANOSECONDS = "ns"
    MICROSECONDS = "us"
    MILLISECONDS = "ms"
    SECONDS = "s"
    MINUTES = "m"
    HOURS = "h"
    
    # Percentage
    PERCENT = "percent"
    
    # Count/Rate
    COUNT = "count"
    PER_SECOND = "per_second"
    PER_MINUTE = "per_minute"
    PER_HOUR = "per_hour"


class MetricCategory(Enum):
    """Standardized metric categories"""
    SYSTEM = "system"
    APPLICATION = "application" 
    NETWORK = "network"
    DATABASE = "database"
    WEBHOOK = "webhook"
    USER_ACTIVITY = "user_activity"
    BUSINESS = "business"


@dataclass
class StandardMetric:
    """Standardized metric structure"""
    name: str                           # Standard metric name (snake_case)
    value: float                        # Numeric value
    unit: MetricUnit                    # Standard unit
    metric_type: MetricType             # Type of metric
    category: MetricCategory            # Category classification
    timestamp: datetime = field(default_factory=lambda: datetime.utcnow())
    labels: Dict[str, str] = field(default_factory=dict)  # Additional labels
    source: str = "unknown"             # Source system/component
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization"""
        return {
            'name': self.name,
            'value': self.value,
            'unit': self.unit.value,
            'metric_type': self.metric_type.value,
            'category': self.category.value,
            'timestamp': self.timestamp.isoformat(),
            'labels': self.labels,
            'source': self.source
        }
    
    def format_value(self) -> str:
        """Format value with appropriate precision based on unit"""
        if self.unit in [MetricUnit.PERCENT]:
            return f"{self.value:.1f}%"
        elif self.unit in [MetricUnit.MEGABYTES, MetricUnit.GIGABYTES]:
            return f"{self.value:.1f}{self.unit.value.upper()}"
        elif self.unit in [MetricUnit.MILLISECONDS]:
            return f"{self.value:.0f}ms"
        elif self.unit in [MetricUnit.SECONDS]:
            return f"{self.value:.2f}s"
        else:
            return f"{self.value:.2f}"


@dataclass
class MetricThreshold:
    """Performance thresholds for metrics"""
    warning_level: float
    critical_level: float
    unit: MetricUnit
    higher_is_worse: bool = True  # True if higher values are worse (e.g., CPU, memory)


@dataclass
class PerformanceBaseline:
    """Standard performance baselines"""
    memory_usage_mb: MetricThreshold = field(default_factory=lambda: MetricThreshold(
        warning_level=200.0, critical_level=300.0, unit=MetricUnit.MEGABYTES))
    cpu_usage_percent: MetricThreshold = field(default_factory=lambda: MetricThreshold(
        warning_level=70.0, critical_level=90.0, unit=MetricUnit.PERCENT))
    response_time_ms: MetricThreshold = field(default_factory=lambda: MetricThreshold(
        warning_level=1000.0, critical_level=3000.0, unit=MetricUnit.MILLISECONDS))
    database_query_time_ms: MetricThreshold = field(default_factory=lambda: MetricThreshold(
        warning_level=500.0, critical_level=2000.0, unit=MetricUnit.MILLISECONDS))
    webhook_latency_ms: MetricThreshold = field(default_factory=lambda: MetricThreshold(
        warning_level=200.0, critical_level=1000.0, unit=MetricUnit.MILLISECONDS))
    startup_time_s: MetricThreshold = field(default_factory=lambda: MetricThreshold(
        warning_level=30.0, critical_level=120.0, unit=MetricUnit.SECONDS))


class MetricCollector(Protocol):
    """Protocol for metric collection sources"""
    
    async def collect_metrics(self) -> List[StandardMetric]:
        """Collect metrics from this source"""
        ...
    
    def get_source_name(self) -> str:
        """Get name of this metric source"""
        ...


class StandardizedMetricsFramework:
    """
    Unified performance metrics collection framework
    Standardizes naming, units, collection intervals, and reporting
    """
    
    def __init__(self, collection_interval_seconds: int = 30):
        self.collection_interval = collection_interval_seconds
        self.metrics_history = deque(maxlen=2000)  # Keep 2000 most recent metrics
        self.collectors: Dict[str, MetricCollector] = {}
        self.baselines = PerformanceBaseline()
        self.is_collecting = False
        self.collection_task: Optional[asyncio.Task] = None
        self._lock = threading.Lock()
        
        # Aggregated metrics for dashboard
        self.current_aggregated_metrics: Dict[str, StandardMetric] = {}
        
        # Standard metric names (enforced across all systems)
        self.standard_metric_names = {
            # System metrics
            'system.memory.used_mb': 'System memory usage in megabytes',
            'system.memory.available_mb': 'System available memory in megabytes', 
            'system.memory.percent_used': 'System memory usage as percentage',
            'process.memory.rss_mb': 'Process resident memory in megabytes',
            'process.memory.vms_mb': 'Process virtual memory in megabytes',
            'system.cpu.usage_percent': 'System CPU usage as percentage',
            'process.cpu.usage_percent': 'Process CPU usage as percentage',
            
            # Application metrics
            'app.startup.total_time_s': 'Total application startup time in seconds',
            'app.startup.stage_time_s': 'Individual startup stage time in seconds',
            'app.operation.duration_ms': 'Operation duration in milliseconds',
            'app.active_users.count': 'Number of currently active users',
            'app.error.rate_per_hour': 'Error rate per hour',
            
            # Network/Webhook metrics
            'webhook.request.latency_ms': 'Webhook request latency in milliseconds',
            'webhook.request.count': 'Number of webhook requests',
            'webhook.cold_start.count': 'Number of webhook cold starts',
            'network.response.time_ms': 'Network response time in milliseconds',
            
            # Database metrics
            'database.query.duration_ms': 'Database query duration in milliseconds',
            'database.connection.count': 'Number of database connections',
            'database.connection.time_ms': 'Database connection time in milliseconds',
            
            # Business metrics
            'business.transaction.count': 'Number of business transactions',
            'business.transaction.value_usd': 'Transaction value in USD',
            'business.user.activity_count': 'User activity count'
        }
        
        logger.info(f"🔧 Standardized Metrics Framework initialized (interval: {collection_interval_seconds}s)")
    
    def register_collector(self, name: str, collector: MetricCollector):
        """Register a metrics collector"""
        self.collectors[name] = collector
        logger.info(f"📊 Registered metrics collector: {name}")
    
    def remove_collector(self, name: str):
        """Remove a metrics collector"""
        if name in self.collectors:
            del self.collectors[name]
            logger.info(f"🗑️ Removed metrics collector: {name}")
    
    async def start_collection(self):
        """Start automated metrics collection"""
        if self.is_collecting:
            logger.warning("Metrics collection already running")
            return
        
        self.is_collecting = True
        self.collection_task = asyncio.create_task(self._collection_loop())
        logger.info(f"🚀 Started standardized metrics collection (interval: {self.collection_interval}s)")
    
    async def stop_collection(self):
        """Stop automated metrics collection"""
        self.is_collecting = False
        if self.collection_task:
            self.collection_task.cancel()
            try:
                await self.collection_task
            except asyncio.CancelledError:
                pass
        
        logger.info("🛑 Stopped standardized metrics collection")
    
    async def _collection_loop(self):
        """Main metrics collection loop"""
        logger.info("📊 Starting standardized metrics collection loop")
        
        while self.is_collecting:
            try:
                collection_start = time.time()
                
                # Collect built-in system metrics
                system_metrics = await self._collect_system_metrics()
                
                # Collect from registered collectors
                collector_metrics = []
                for name, collector in self.collectors.items():
                    try:
                        metrics = await collector.collect_metrics()
                        collector_metrics.extend(metrics)
                    except Exception as e:
                        logger.error(f"Error collecting metrics from {name}: {e}")
                
                # Combine all metrics
                all_metrics = system_metrics + collector_metrics
                
                # Store in history
                with self._lock:
                    for metric in all_metrics:
                        self.metrics_history.append(metric)
                    
                    # Update current aggregated metrics
                    self._update_aggregated_metrics(all_metrics)
                
                # Check thresholds and log alerts
                await self._check_thresholds(all_metrics)
                
                # Log collection summary
                collection_time = time.time() - collection_start
                logger.debug(f"📊 Collected {len(all_metrics)} metrics in {collection_time:.3f}s")
                
                # Log key metrics in standardized format
                self._log_key_metrics()
                
            except Exception as e:
                logger.error(f"Error in metrics collection loop: {e}")
            
            await asyncio.sleep(self.collection_interval)
    
    async def _collect_system_metrics(self) -> List[StandardMetric]:
        """Collect standard system metrics"""
        metrics = []
        current_time = datetime.utcnow()
        
        try:
            # Memory metrics using shared monitor
            memory_info = await get_memory_usage()
            
            metrics.extend([
                StandardMetric(
                    name='process.memory.rss_mb',
                    value=memory_info['process_memory_mb'],
                    unit=MetricUnit.MEGABYTES,
                    metric_type=MetricType.GAUGE,
                    category=MetricCategory.SYSTEM,
                    timestamp=current_time,
                    source='standardized_framework'
                ),
                StandardMetric(
                    name='system.memory.available_gb',
                    value=memory_info['system_memory_available_gb'],
                    unit=MetricUnit.GIGABYTES,
                    metric_type=MetricType.GAUGE,
                    category=MetricCategory.SYSTEM,
                    timestamp=current_time,
                    source='standardized_framework'
                ),
                StandardMetric(
                    name='system.memory.percent_used',
                    value=memory_info['system_memory_percent'],
                    unit=MetricUnit.PERCENT,
                    metric_type=MetricType.GAUGE,
                    category=MetricCategory.SYSTEM,
                    timestamp=current_time,
                    source='standardized_framework'
                )
            ])
            
            # CPU metrics using shared monitor
            cpu_reading = await get_cpu_usage()
            
            metrics.extend([
                StandardMetric(
                    name='system.cpu.usage_percent',
                    value=cpu_reading.cpu_percent,
                    unit=MetricUnit.PERCENT,
                    metric_type=MetricType.GAUGE,
                    category=MetricCategory.SYSTEM,
                    timestamp=current_time,
                    labels={'cached': str(cpu_reading.is_cached)},
                    source='standardized_framework'
                ),
                StandardMetric(
                    name='process.cpu.usage_percent',
                    value=cpu_reading.process_cpu,
                    unit=MetricUnit.PERCENT,
                    metric_type=MetricType.GAUGE,
                    category=MetricCategory.SYSTEM,
                    timestamp=current_time,
                    labels={'cached': str(cpu_reading.is_cached)},
                    source='standardized_framework'
                )
            ])
            
        except Exception as e:
            logger.error(f"Error collecting system metrics: {e}")
        
        return metrics
    
    def _update_aggregated_metrics(self, new_metrics: List[StandardMetric]):
        """Update current aggregated metrics for dashboard"""
        for metric in new_metrics:
            self.current_aggregated_metrics[metric.name] = metric
    
    async def _check_thresholds(self, metrics: List[StandardMetric]):
        """Check metrics against performance thresholds"""
        for metric in metrics:
            threshold = self._get_threshold_for_metric(metric.name)
            if not threshold:
                continue
            
            is_warning = False
            is_critical = False
            
            if threshold.higher_is_worse:
                is_warning = metric.value >= threshold.warning_level
                is_critical = metric.value >= threshold.critical_level
            else:
                is_warning = metric.value <= threshold.warning_level
                is_critical = metric.value <= threshold.critical_level
            
            if is_critical:
                logger.critical(
                    f"🚨 CRITICAL: {metric.name} = {metric.format_value()} "
                    f"(threshold: {threshold.critical_level}{threshold.unit.value})"
                )
            elif is_warning:
                logger.warning(
                    f"⚠️ WARNING: {metric.name} = {metric.format_value()} "
                    f"(threshold: {threshold.warning_level}{threshold.unit.value})"
                )
    
    def _get_threshold_for_metric(self, metric_name: str) -> Optional[MetricThreshold]:
        """Get threshold configuration for a metric"""
        threshold_map = {
            'process.memory.rss_mb': self.baselines.memory_usage_mb,
            'system.cpu.usage_percent': self.baselines.cpu_usage_percent,
            'process.cpu.usage_percent': self.baselines.cpu_usage_percent,
            'app.operation.duration_ms': self.baselines.response_time_ms,
            'database.query.duration_ms': self.baselines.database_query_time_ms,
            'webhook.request.latency_ms': self.baselines.webhook_latency_ms,
            'app.startup.total_time_s': self.baselines.startup_time_s
        }
        return threshold_map.get(metric_name)
    
    def _log_key_metrics(self):
        """Log key metrics in standardized format"""
        try:
            # Get current key metrics
            memory_metric = self.current_aggregated_metrics.get('process.memory.rss_mb')
            cpu_metric = self.current_aggregated_metrics.get('process.cpu.usage_percent')
            
            if memory_metric and cpu_metric:
                logger.info(
                    f"📊 STANDARD_METRICS: Memory={memory_metric.format_value()}, "
                    f"CPU={cpu_metric.format_value()}"
                )
        except Exception as e:
            logger.error(f"Error logging key metrics: {e}")
    
    def get_current_metrics(self) -> Dict[str, StandardMetric]:
        """Get current aggregated metrics"""
        with self._lock:
            return dict(self.current_aggregated_metrics)
    
    def get_metrics_history(self, limit: int = 100, 
                          category: Optional[MetricCategory] = None,
                          time_range_minutes: Optional[int] = None) -> List[StandardMetric]:
        """Get metrics history with filtering"""
        with self._lock:
            metrics = list(self.metrics_history)
        
        # Filter by category
        if category:
            metrics = [m for m in metrics if m.category == category]
        
        # Filter by time range
        if time_range_minutes:
            cutoff_time = datetime.utcnow() - timedelta(minutes=time_range_minutes)
            metrics = [m for m in metrics if m.timestamp > cutoff_time]
        
        # Sort by timestamp (newest first) and limit
        metrics.sort(key=lambda x: x.timestamp, reverse=True)
        return metrics[:limit]
    
    def get_performance_summary(self) -> Dict[str, Any]:
        """Get comprehensive performance summary"""
        current_metrics = self.get_current_metrics()
        
        summary = {
            'timestamp': datetime.utcnow().isoformat(),
            'collection_interval_seconds': self.collection_interval,
            'total_metrics_collected': len(self.metrics_history),
            'registered_collectors': list(self.collectors.keys()),
            'current_metrics': {name: metric.to_dict() for name, metric in current_metrics.items()},
            'performance_status': self._get_performance_status(current_metrics),
            'baselines': {
                'memory_warning_mb': self.baselines.memory_usage_mb.warning_level,
                'memory_critical_mb': self.baselines.memory_usage_mb.critical_level,
                'cpu_warning_percent': self.baselines.cpu_usage_percent.warning_level,
                'cpu_critical_percent': self.baselines.cpu_usage_percent.critical_level,
                'response_time_warning_ms': self.baselines.response_time_ms.warning_level,
                'response_time_critical_ms': self.baselines.response_time_ms.critical_level
            }
        }
        
        return summary
    
    def _get_performance_status(self, metrics: Dict[str, StandardMetric]) -> str:
        """Determine overall performance status"""
        memory_metric = metrics.get('process.memory.rss_mb')
        cpu_metric = metrics.get('process.cpu.usage_percent')
        
        critical_issues = 0
        warning_issues = 0
        
        if memory_metric:
            if memory_metric.value >= self.baselines.memory_usage_mb.critical_level:
                critical_issues += 1
            elif memory_metric.value >= self.baselines.memory_usage_mb.warning_level:
                warning_issues += 1
        
        if cpu_metric:
            if cpu_metric.value >= self.baselines.cpu_usage_percent.critical_level:
                critical_issues += 1
            elif cpu_metric.value >= self.baselines.cpu_usage_percent.warning_level:
                warning_issues += 1
        
        if critical_issues > 0:
            return 'critical'
        elif warning_issues > 0:
            return 'warning'
        else:
            return 'healthy'
    
    @asynccontextmanager
    async def measure_operation(self, operation_name: str, 
                              category: MetricCategory = MetricCategory.APPLICATION,
                              labels: Optional[Dict[str, str]] = None):
        """Context manager to measure operation duration"""
        start_time = time.perf_counter()
        try:
            yield
        finally:
            duration_ms = safe_duration_calculation(
                start_time, 
                time.perf_counter(), 
                scale_factor=1000.0
            )
            
            # Create metric for operation duration
            metric = StandardMetric(
                name='app.operation.duration_ms',
                value=duration_ms,
                unit=MetricUnit.MILLISECONDS,
                metric_type=MetricType.TIMER,
                category=category,
                labels={'operation': operation_name, **(labels or {})},
                source='standardized_framework'
            )
            
            # Add to history
            with self._lock:
                self.metrics_history.append(metric)
            
            # Log if slow
            if duration_ms > 1000:
                logger.warning(f"⏳ Slow operation '{operation_name}': {duration_ms:.0f}ms")
            else:
                logger.debug(f"⚡ Operation '{operation_name}': {duration_ms:.1f}ms")


# Global instance for easy access
standardized_metrics = StandardizedMetricsFramework()


# Convenience functions for common operations
async def record_metric(name: str, value: float, unit: MetricUnit, 
                       metric_type: MetricType, category: MetricCategory,
                       labels: Optional[Dict[str, str]] = None,
                       source: str = "unknown"):
    """Record a custom metric"""
    metric = StandardMetric(
        name=name,
        value=value,
        unit=unit,
        metric_type=metric_type,
        category=category,
        labels=labels or {},
        source=source
    )
    
    with standardized_metrics._lock:
        standardized_metrics.metrics_history.append(metric)
        standardized_metrics.current_aggregated_metrics[name] = metric


async def measure_operation_time(operation_name: str, 
                               category: MetricCategory = MetricCategory.APPLICATION,
                               labels: Optional[Dict[str, str]] = None):
    """Decorator/context manager for measuring operation time"""
    return standardized_metrics.measure_operation(operation_name, category, labels)


def get_current_performance_summary() -> Dict[str, Any]:
    """Get current performance summary"""
    return standardized_metrics.get_performance_summary()


def set_performance_baselines(memory_warning_mb: float = None,
                            memory_critical_mb: float = None,
                            cpu_warning_percent: float = None,
                            cpu_critical_percent: float = None,
                            response_warning_ms: float = None,
                            response_critical_ms: float = None):
    """Update performance baselines"""
    if memory_warning_mb is not None:
        standardized_metrics.baselines.memory_usage_mb.warning_level = memory_warning_mb
    if memory_critical_mb is not None:
        standardized_metrics.baselines.memory_usage_mb.critical_level = memory_critical_mb
    if cpu_warning_percent is not None:
        standardized_metrics.baselines.cpu_usage_percent.warning_level = cpu_warning_percent
    if cpu_critical_percent is not None:
        standardized_metrics.baselines.cpu_usage_percent.critical_level = cpu_critical_percent
    if response_warning_ms is not None:
        standardized_metrics.baselines.response_time_ms.warning_level = response_warning_ms
    if response_critical_ms is not None:
        standardized_metrics.baselines.response_time_ms.critical_level = response_critical_ms
    
    logger.info("📊 Performance baselines updated")