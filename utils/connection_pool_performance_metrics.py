"""
Connection Pool Performance Metrics
Comprehensive metrics collection, analysis, and reporting system for database connection pools
"""

import logging
import asyncio
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Union
from collections import deque, defaultdict
from dataclasses import dataclass, field
from enum import Enum
import statistics
import json
import psutil
from concurrent.futures import ThreadPoolExecutor
import numpy as np

logger = logging.getLogger(__name__)


class PerformanceMetricType(Enum):
    """Types of performance metrics"""
    LATENCY = "latency"
    THROUGHPUT = "throughput"
    ERROR_RATE = "error_rate"
    UTILIZATION = "utilization"
    RESOURCE_USAGE = "resource_usage"
    SSL_PERFORMANCE = "ssl_performance"
    CONNECTION_LIFECYCLE = "connection_lifecycle"


class MetricAggregationType(Enum):
    """Metric aggregation methods"""
    AVERAGE = "average"
    MEDIAN = "median"
    P95 = "p95"
    P99 = "p99"
    SUM = "sum"
    COUNT = "count"
    MIN = "min"
    MAX = "max"


@dataclass
class PerformanceDataPoint:
    """Individual performance data point"""
    timestamp: datetime
    metric_type: PerformanceMetricType
    value: float
    context: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    tags: Dict[str, str] = field(default_factory=dict)


@dataclass
class ConnectionPoolMetrics:
    """Aggregated connection pool metrics"""
    timestamp: datetime
    pool_size: int
    active_connections: int
    idle_connections: int
    overflow_connections: int
    queue_length: int
    avg_acquisition_time_ms: float
    p95_acquisition_time_ms: float
    p99_acquisition_time_ms: float
    throughput_per_second: float
    error_rate_percentage: float
    ssl_handshake_time_ms: float
    memory_usage_mb: float
    cpu_usage_percentage: float
    connection_create_rate: float
    connection_close_rate: float
    connection_reuse_rate: float


@dataclass
class PerformanceTrend:
    """Performance trend analysis"""
    metric_name: str
    trend_direction: str  # increasing, decreasing, stable
    trend_strength: float  # 0.0 to 1.0
    confidence: float  # 0.0 to 1.0
    regression_slope: float
    correlation_coefficient: float
    forecast_values: List[float]
    anomaly_score: float


class ConnectionPoolPerformanceCollector:
    """Advanced performance metrics collector for connection pools"""
    
    def __init__(self, retention_hours: int = 48, collection_interval: int = 5):
        self.retention_hours = retention_hours
        self.collection_interval = collection_interval
        
        # Metrics storage
        self.raw_metrics = deque(maxlen=int(retention_hours * 3600 / collection_interval))
        self.aggregated_metrics = deque(maxlen=int(retention_hours * 12))  # 5-minute aggregates
        self.performance_snapshots = deque(maxlen=100)
        
        # Real-time metrics
        self.current_metrics = {}
        self.metric_buffers = defaultdict(lambda: deque(maxlen=1000))
        self.performance_counters = defaultdict(int)
        
        # Trend analysis
        self.trend_analyzer = PerformanceTrendAnalyzer()
        self.anomaly_detector = PerformanceAnomalyDetector()
        
        # Thread safety
        self._metrics_lock = threading.Lock()
        self._collection_lock = threading.Lock()
        
        # Background collection
        self._collection_executor = ThreadPoolExecutor(max_workers=2)
        self._running = True
        
        # Start collection loops
        asyncio.create_task(self._metrics_collection_loop())
        asyncio.create_task(self._aggregation_loop())
        asyncio.create_task(self._trend_analysis_loop())
        
        logger.info(
            f"📊 Connection Pool Performance Collector initialized "
            f"(retention: {retention_hours}h, interval: {collection_interval}s)"
        )
    
    def record_metric(
        self, 
        metric_type: PerformanceMetricType, 
        value: float, 
        context: str = "default",
        metadata: Optional[Dict[str, Any]] = None,
        tags: Optional[Dict[str, str]] = None
    ):
        """Record a performance metric"""
        try:
            data_point = PerformanceDataPoint(
                timestamp=datetime.utcnow(),
                metric_type=metric_type,
                value=value,
                context=context,
                metadata=metadata or {},
                tags=tags or {}
            )
            
            with self._metrics_lock:
                self.raw_metrics.append(data_point)
                self.metric_buffers[metric_type.value].append(data_point)
                self.performance_counters[f"{metric_type.value}_count"] += 1
                
                # Update current metrics
                self.current_metrics[f"{metric_type.value}_{context}"] = {
                    'value': value,
                    'timestamp': data_point.timestamp,
                    'metadata': metadata or {}
                }
            
        except Exception as e:
            logger.error(f"Error recording metric: {e}")
    
    def record_connection_acquisition(
        self, 
        acquisition_time_ms: float, 
        context: str,
        pool_size: int,
        queue_length: int = 0,
        success: bool = True
    ):
        """Record connection acquisition metrics"""
        self.record_metric(
            PerformanceMetricType.LATENCY,
            acquisition_time_ms,
            context,
            metadata={
                'operation': 'connection_acquisition',
                'pool_size': pool_size,
                'queue_length': queue_length,
                'success': success
            }
        )
        
        if not success:
            self.record_metric(
                PerformanceMetricType.ERROR_RATE,
                1.0,
                context,
                metadata={'error_type': 'acquisition_failure'}
            )
    
    def record_connection_utilization(
        self, 
        active_connections: int, 
        pool_size: int,
        context: str = "default"
    ):
        """Record connection pool utilization"""
        utilization = (active_connections / max(pool_size, 1)) * 100
        
        self.record_metric(
            PerformanceMetricType.UTILIZATION,
            utilization,
            context,
            metadata={
                'active_connections': active_connections,
                'pool_size': pool_size,
                'idle_connections': pool_size - active_connections
            }
        )
    
    def record_ssl_performance(
        self, 
        handshake_time_ms: float, 
        context: str,
        success: bool = True,
        certificate_info: Optional[Dict] = None
    ):
        """Record SSL performance metrics"""
        self.record_metric(
            PerformanceMetricType.SSL_PERFORMANCE,
            handshake_time_ms,
            context,
            metadata={
                'operation': 'ssl_handshake',
                'success': success,
                'certificate_info': certificate_info or {}
            }
        )
    
    def record_resource_usage(
        self, 
        memory_mb: float, 
        cpu_percentage: float,
        context: str = "system"
    ):
        """Record system resource usage"""
        self.record_metric(
            PerformanceMetricType.RESOURCE_USAGE,
            memory_mb,
            f"{context}_memory",
            metadata={'type': 'memory', 'unit': 'MB'}
        )
        
        self.record_metric(
            PerformanceMetricType.RESOURCE_USAGE,
            cpu_percentage,
            f"{context}_cpu",
            metadata={'type': 'cpu', 'unit': 'percentage'}
        )
    
    def record_throughput(
        self, 
        operations_per_second: float, 
        context: str,
        operation_type: str = "database_operation"
    ):
        """Record throughput metrics"""
        self.record_metric(
            PerformanceMetricType.THROUGHPUT,
            operations_per_second,
            context,
            metadata={
                'operation_type': operation_type,
                'unit': 'operations_per_second'
            }
        )
    
    async def _metrics_collection_loop(self):
        """Main metrics collection loop"""
        logger.info("📊 Starting metrics collection loop...")
        
        while self._running:
            try:
                await asyncio.sleep(self.collection_interval)
                
                # Collect system metrics
                await self._collect_system_metrics()
                
                # Collect pool metrics
                await self._collect_pool_metrics()
                
                # Collect SSL metrics
                await self._collect_ssl_metrics()
                
            except Exception as e:
                logger.error(f"Error in metrics collection loop: {e}")
                await asyncio.sleep(30)
    
    async def _collect_system_metrics(self):
        """Collect system resource metrics"""
        try:
            # Memory usage
            process = psutil.Process()
            memory_info = process.memory_info()
            memory_mb = memory_info.rss / 1024 / 1024
            
            # CPU usage
            cpu_percent = process.cpu_percent()
            
            # Record metrics
            self.record_resource_usage(memory_mb, cpu_percent, "connection_pool_process")
            
            # System-wide metrics
            system_memory = psutil.virtual_memory()
            system_cpu = psutil.cpu_percent()
            
            self.record_resource_usage(
                system_memory.used / 1024 / 1024, 
                system_cpu, 
                "system_global"
            )
            
        except Exception as e:
            logger.debug(f"Error collecting system metrics: {e}")
    
    async def _collect_pool_metrics(self):
        """Collect connection pool metrics"""
        try:
            # Try to get metrics from dynamic pool
            try:
                from utils.dynamic_database_pool_manager import get_dynamic_pool_stats
                pool_stats = get_dynamic_pool_stats()
                
                # Record utilization
                self.record_connection_utilization(
                    pool_stats.get('pool_checked_out', 0),
                    pool_stats.get('pool_size', 0),
                    "dynamic_pool"
                )
                
                # Record throughput (based on recent acquisitions)
                recent_acquisitions = pool_stats.get('total_acquisitions', 0)
                if hasattr(self, '_last_acquisitions'):
                    acquisition_rate = (recent_acquisitions - self._last_acquisitions) / self.collection_interval
                    self.record_throughput(acquisition_rate, "dynamic_pool", "connection_acquisition")
                self._last_acquisitions = recent_acquisitions
                
            except ImportError:
                logger.debug("Dynamic pool not available for metrics collection")
            
            # Try to get metrics from standard pool
            try:
                from utils.database_pool_manager import database_pool
                standard_stats = database_pool.get_pool_statistics()
                
                self.record_connection_utilization(
                    standard_stats.get('pool_checked_out', 0),
                    standard_stats.get('pool_size', 0),
                    "standard_pool"
                )
                
            except (ImportError, AttributeError):
                logger.debug("Standard pool not available for metrics collection")
            
        except Exception as e:
            logger.debug(f"Error collecting pool metrics: {e}")
    
    async def _collect_ssl_metrics(self):
        """Collect SSL performance metrics"""
        try:
            # Get SSL health summary
            try:
                from utils.ssl_connection_monitor import get_ssl_health_summary
                ssl_summary = get_ssl_health_summary()
                
                # Record SSL error rate
                error_rate = ssl_summary['metrics'].get('error_rate_percentage', 0.0)
                self.record_metric(
                    PerformanceMetricType.SSL_PERFORMANCE,
                    error_rate,
                    "ssl_error_rate",
                    metadata={'type': 'error_rate', 'unit': 'percentage'}
                )
                
                # Record recent SSL events
                recent_events = ssl_summary.get('recent_events', [])
                for event in recent_events[-5:]:  # Last 5 events
                    if event.get('recovery_time_ms'):
                        self.record_ssl_performance(
                            event['recovery_time_ms'],
                            f"ssl_recovery_{event.get('context', 'default')}",
                            success=True
                        )
                
            except ImportError:
                logger.debug("SSL monitor not available for metrics collection")
            
        except Exception as e:
            logger.debug(f"Error collecting SSL metrics: {e}")
    
    async def _aggregation_loop(self):
        """Aggregate raw metrics into time-based summaries"""
        logger.info("📈 Starting metrics aggregation loop...")
        
        while self._running:
            try:
                await asyncio.sleep(300)  # Aggregate every 5 minutes
                
                # Perform aggregation
                await self._aggregate_metrics()
                
                # Clean old data
                await self._cleanup_old_metrics()
                
            except Exception as e:
                logger.error(f"Error in aggregation loop: {e}")
                await asyncio.sleep(300)
    
    async def _aggregate_metrics(self):
        """Aggregate raw metrics into summary statistics"""
        try:
            with self._collection_lock:
                now = datetime.utcnow()
                cutoff_time = now - timedelta(minutes=5)
                
                # Get recent metrics
                recent_metrics = [
                    m for m in list(self.raw_metrics)
                    if m.timestamp >= cutoff_time
                ]
                
                if not recent_metrics:
                    return
                
                # Group metrics by type
                metrics_by_type = defaultdict(list)
                for metric in recent_metrics:
                    metrics_by_type[metric.metric_type].append(metric)
                
                # Calculate aggregated metrics
                aggregated = {}
                
                # Latency metrics (connection acquisition)
                latency_metrics = metrics_by_type[PerformanceMetricType.LATENCY]
                if latency_metrics:
                    latency_values = [m.value for m in latency_metrics]
                    aggregated['avg_latency_ms'] = statistics.mean(latency_values)
                    aggregated['p95_latency_ms'] = np.percentile(latency_values, 95)
                    aggregated['p99_latency_ms'] = np.percentile(latency_values, 99)
                    aggregated['max_latency_ms'] = max(latency_values)
                
                # Utilization metrics
                utilization_metrics = metrics_by_type[PerformanceMetricType.UTILIZATION]
                if utilization_metrics:
                    util_values = [m.value for m in utilization_metrics]
                    aggregated['avg_utilization_pct'] = statistics.mean(util_values)
                    aggregated['peak_utilization_pct'] = max(util_values)
                
                # Throughput metrics
                throughput_metrics = metrics_by_type[PerformanceMetricType.THROUGHPUT]
                if throughput_metrics:
                    throughput_values = [m.value for m in throughput_metrics]
                    aggregated['avg_throughput_ops'] = statistics.mean(throughput_values)
                    aggregated['peak_throughput_ops'] = max(throughput_values)
                
                # Error rate metrics
                error_metrics = metrics_by_type[PerformanceMetricType.ERROR_RATE]
                error_count = len(error_metrics)
                total_operations = len(recent_metrics)
                aggregated['error_rate_pct'] = (error_count / max(total_operations, 1)) * 100
                
                # SSL performance metrics
                ssl_metrics = metrics_by_type[PerformanceMetricType.SSL_PERFORMANCE]
                if ssl_metrics:
                    ssl_values = [m.value for m in ssl_metrics]
                    aggregated['avg_ssl_handshake_ms'] = statistics.mean(ssl_values)
                    aggregated['p95_ssl_handshake_ms'] = np.percentile(ssl_values, 95)
                
                # Resource usage metrics
                resource_metrics = metrics_by_type[PerformanceMetricType.RESOURCE_USAGE]
                memory_metrics = [m for m in resource_metrics if 'memory' in m.context]
                cpu_metrics = [m for m in resource_metrics if 'cpu' in m.context]
                
                if memory_metrics:
                    memory_values = [m.value for m in memory_metrics]
                    aggregated['avg_memory_mb'] = statistics.mean(memory_values)
                    aggregated['peak_memory_mb'] = max(memory_values)
                
                if cpu_metrics:
                    cpu_values = [m.value for m in cpu_metrics]
                    aggregated['avg_cpu_pct'] = statistics.mean(cpu_values)
                    aggregated['peak_cpu_pct'] = max(cpu_values)
                
                # Store aggregated metrics
                self.aggregated_metrics.append({
                    'timestamp': now,
                    'metrics': aggregated,
                    'sample_count': len(recent_metrics)
                })
                
                logger.debug(f"📊 Aggregated {len(recent_metrics)} metrics into summary")
                
        except Exception as e:
            logger.error(f"Error in metrics aggregation: {e}")
    
    async def _cleanup_old_metrics(self):
        """Clean up old metrics data"""
        try:
            cutoff_time = datetime.utcnow() - timedelta(hours=self.retention_hours)
            
            with self._metrics_lock:
                # Clean raw metrics (deque handles this automatically with maxlen)
                
                # Clean metric buffers
                for metric_type, buffer in self.metric_buffers.items():
                    cleaned_buffer = deque([
                        m for m in buffer if m.timestamp >= cutoff_time
                    ], maxlen=buffer.maxlen)
                    self.metric_buffers[metric_type] = cleaned_buffer
                
                # Clean current metrics
                expired_keys = [
                    key for key, value in self.current_metrics.items()
                    if value['timestamp'] < cutoff_time
                ]
                
                for key in expired_keys:
                    del self.current_metrics[key]
            
            logger.debug(f"🧹 Cleaned metrics older than {self.retention_hours} hours")
            
        except Exception as e:
            logger.error(f"Error cleaning old metrics: {e}")
    
    async def _trend_analysis_loop(self):
        """Analyze performance trends"""
        logger.info("📈 Starting trend analysis loop...")
        
        while self._running:
            try:
                await asyncio.sleep(900)  # Analyze every 15 minutes
                
                # Perform trend analysis
                trends = await self._analyze_trends()
                
                # Detect anomalies
                anomalies = await self._detect_anomalies()
                
                # Log significant trends
                for trend in trends:
                    if trend.confidence > 0.7:
                        logger.info(
                            f"📈 Performance trend detected: {trend.metric_name} "
                            f"trending {trend.trend_direction} "
                            f"(confidence: {trend.confidence:.2f})"
                        )
                
                # Log anomalies
                for anomaly in anomalies:
                    logger.warning(
                        f"🚨 Performance anomaly detected: {anomaly['metric']} "
                        f"value={anomaly['value']:.2f}, "
                        f"expected={anomaly['expected_range']}"
                    )
                
            except Exception as e:
                logger.error(f"Error in trend analysis loop: {e}")
                await asyncio.sleep(900)
    
    async def _analyze_trends(self) -> List[PerformanceTrend]:
        """Analyze performance trends using statistical methods"""
        trends = []
        
        try:
            if len(self.aggregated_metrics) < 12:  # Need at least 1 hour of data
                return trends
            
            recent_aggregates = list(self.aggregated_metrics)[-24:]  # Last 2 hours
            
            # Analyze trends for key metrics
            metric_names = [
                'avg_latency_ms', 'avg_utilization_pct', 'avg_throughput_ops',
                'error_rate_pct', 'avg_ssl_handshake_ms', 'avg_memory_mb'
            ]
            
            for metric_name in metric_names:
                values = []
                timestamps = []
                
                for aggregate in recent_aggregates:
                    if metric_name in aggregate['metrics']:
                        values.append(aggregate['metrics'][metric_name])
                        timestamps.append(aggregate['timestamp'].timestamp())
                
                if len(values) >= 6:  # Need sufficient data points
                    trend = self.trend_analyzer.analyze_trend(
                        metric_name, timestamps, values
                    )
                    if trend:
                        trends.append(trend)
            
        except Exception as e:
            logger.error(f"Error analyzing trends: {e}")
        
        return trends
    
    async def _detect_anomalies(self) -> List[Dict[str, Any]]:
        """Detect performance anomalies"""
        anomalies = []
        
        try:
            if len(self.aggregated_metrics) < 6:
                return anomalies
            
            recent_aggregates = list(self.aggregated_metrics)[-6:]
            latest_metrics = recent_aggregates[-1]['metrics']
            
            # Get baseline from historical data
            baseline_aggregates = list(self.aggregated_metrics)[-24:-1]  # Exclude latest
            
            for metric_name, current_value in latest_metrics.items():
                if metric_name in ['avg_latency_ms', 'error_rate_pct', 'avg_memory_mb']:
                    # Get historical values
                    historical_values = [
                        a['metrics'].get(metric_name, 0) for a in baseline_aggregates
                        if metric_name in a['metrics']
                    ]
                    
                    if len(historical_values) >= 5:
                        anomaly = self.anomaly_detector.detect_anomaly(
                            metric_name, current_value, historical_values
                        )
                        if anomaly:
                            anomalies.append(anomaly)
            
        except Exception as e:
            logger.error(f"Error detecting anomalies: {e}")
        
        return anomalies
    
    def get_real_time_metrics(self) -> Dict[str, Any]:
        """Get current real-time metrics"""
        with self._metrics_lock:
            # Calculate current statistics
            now = datetime.utcnow()
            recent_cutoff = now - timedelta(minutes=1)
            
            recent_metrics = [
                m for m in list(self.raw_metrics)
                if m.timestamp >= recent_cutoff
            ]
            
            # Current performance summary
            latency_metrics = [
                m.value for m in recent_metrics 
                if m.metric_type == PerformanceMetricType.LATENCY
            ]
            
            utilization_metrics = [
                m.value for m in recent_metrics 
                if m.metric_type == PerformanceMetricType.UTILIZATION
            ]
            
            return {
                'timestamp': now.isoformat(),
                'collection_interval': self.collection_interval,
                'total_metrics_collected': sum(self.performance_counters.values()),
                'recent_metrics_count': len(recent_metrics),
                'current_performance': {
                    'avg_latency_ms': statistics.mean(latency_metrics) if latency_metrics else 0,
                    'max_latency_ms': max(latency_metrics) if latency_metrics else 0,
                    'avg_utilization_pct': statistics.mean(utilization_metrics) if utilization_metrics else 0,
                    'max_utilization_pct': max(utilization_metrics) if utilization_metrics else 0,
                    'error_count_last_minute': len([
                        m for m in recent_metrics 
                        if m.metric_type == PerformanceMetricType.ERROR_RATE
                    ])
                },
                'metric_counts': dict(self.performance_counters),
                'current_metrics': dict(self.current_metrics)
            }
    
    def get_performance_summary(
        self, 
        hours: int = 1,
        aggregation: MetricAggregationType = MetricAggregationType.AVERAGE
    ) -> Dict[str, Any]:
        """Get performance summary for specified time period"""
        try:
            now = datetime.utcnow()
            cutoff_time = now - timedelta(hours=hours)
            
            # Get relevant aggregated metrics
            relevant_aggregates = [
                a for a in list(self.aggregated_metrics)
                if a['timestamp'] >= cutoff_time
            ]
            
            if not relevant_aggregates:
                return {'message': 'No data available for specified time period'}
            
            # Aggregate across time period
            all_metrics = {}
            for aggregate in relevant_aggregates:
                for metric_name, value in aggregate['metrics'].items():
                    if metric_name not in all_metrics:
                        all_metrics[metric_name] = []
                    all_metrics[metric_name].append(value)
            
            # Apply aggregation method
            summary = {}
            for metric_name, values in all_metrics.items():
                if aggregation == MetricAggregationType.AVERAGE:
                    summary[metric_name] = round(statistics.mean(values), 3)
                elif aggregation == MetricAggregationType.MEDIAN:
                    summary[metric_name] = round(statistics.median(values), 3)
                elif aggregation == MetricAggregationType.P95:
                    summary[metric_name] = round(np.percentile(values, 95), 3)
                elif aggregation == MetricAggregationType.P99:
                    summary[metric_name] = round(np.percentile(values, 99), 3)
                elif aggregation == MetricAggregationType.MIN:
                    summary[metric_name] = round(min(values), 3)
                elif aggregation == MetricAggregationType.MAX:
                    summary[metric_name] = round(max(values), 3)
                elif aggregation == MetricAggregationType.SUM:
                    summary[metric_name] = round(sum(values), 3)
                elif aggregation == MetricAggregationType.COUNT:
                    summary[metric_name] = len(values)
            
            return {
                'timestamp': now.isoformat(),
                'period_hours': hours,
                'aggregation_method': aggregation.value,
                'sample_count': len(relevant_aggregates),
                'metrics': summary,
                'data_points': len(relevant_aggregates) * 5  # Approximate raw data points
            }
            
        except Exception as e:
            logger.error(f"Error generating performance summary: {e}")
            return {'error': str(e)}
    
    def export_metrics(
        self, 
        format_type: str = 'json',
        hours: int = 24
    ) -> Union[str, Dict[str, Any]]:
        """Export metrics data"""
        try:
            cutoff_time = datetime.utcnow() - timedelta(hours=hours)
            
            # Get relevant data
            relevant_aggregates = [
                {
                    'timestamp': a['timestamp'].isoformat(),
                    'metrics': a['metrics'],
                    'sample_count': a.get('sample_count', 0)
                }
                for a in list(self.aggregated_metrics)
                if a['timestamp'] >= cutoff_time
            ]
            
            export_data = {
                'export_timestamp': datetime.utcnow().isoformat(),
                'period_hours': hours,
                'total_data_points': len(relevant_aggregates),
                'collection_interval_seconds': self.collection_interval,
                'metrics_data': relevant_aggregates,
                'performance_counters': dict(self.performance_counters)
            }
            
            if format_type.lower() == 'json':
                return json.dumps(export_data, indent=2)
            else:
                return export_data
                
        except Exception as e:
            logger.error(f"Error exporting metrics: {e}")
            return {'error': str(e)}
    
    def shutdown(self):
        """Shutdown the metrics collector"""
        logger.info("📊 Shutting down Connection Pool Performance Collector...")
        self._running = False
        self._collection_executor.shutdown(wait=True)


class PerformanceTrendAnalyzer:
    """Analyze performance trends using statistical methods"""
    
    def analyze_trend(
        self, 
        metric_name: str, 
        timestamps: List[float], 
        values: List[float]
    ) -> Optional[PerformanceTrend]:
        """Analyze trend for a metric"""
        try:
            if len(values) < 3:
                return None
            
            # Calculate linear regression
            x = np.array(timestamps)
            y = np.array(values)
            
            # Normalize timestamps to start from 0
            x_norm = x - x[0]
            
            # Linear regression
            slope, intercept = np.polyfit(x_norm, y, 1)
            
            # Calculate correlation coefficient
            correlation = np.corrcoef(x_norm, y)[0, 1]
            
            # Determine trend direction and strength
            trend_strength = abs(correlation)
            confidence = trend_strength if not np.isnan(trend_strength) else 0.0
            
            if slope > 0:
                trend_direction = "increasing"
            elif slope < 0:
                trend_direction = "decreasing"
            else:
                trend_direction = "stable"
            
            # Generate forecast (simple linear projection)
            future_x = np.array([x_norm[-1] + i * 300 for i in range(1, 4)])  # Next 3 intervals
            forecast_values = slope * future_x + intercept
            
            # Calculate anomaly score (deviation from trend)
            predicted_values = slope * x_norm + intercept
            residuals = y - predicted_values
            anomaly_score = np.std(residuals) if len(residuals) > 1 else 0.0
            
            return PerformanceTrend(
                metric_name=metric_name,
                trend_direction=trend_direction,
                trend_strength=trend_strength,
                confidence=confidence,
                regression_slope=slope,
                correlation_coefficient=correlation,
                forecast_values=forecast_values.tolist(),
                anomaly_score=anomaly_score
            )
            
        except Exception as e:
            logger.error(f"Error analyzing trend for {metric_name}: {e}")
            return None


class PerformanceAnomalyDetector:
    """Detect performance anomalies using statistical methods"""
    
    def detect_anomaly(
        self, 
        metric_name: str, 
        current_value: float, 
        historical_values: List[float]
    ) -> Optional[Dict[str, Any]]:
        """Detect if current value is anomalous"""
        try:
            if len(historical_values) < 3:
                return None
            
            # Calculate baseline statistics
            mean_value = statistics.mean(historical_values)
            std_dev = statistics.stdev(historical_values)
            
            if std_dev == 0:  # No variance
                return None
            
            # Calculate z-score
            z_score = (current_value - mean_value) / std_dev
            
            # Determine if anomalous (beyond 2 standard deviations)
            if abs(z_score) > 2.0:
                severity = 'critical' if abs(z_score) > 3.0 else 'warning'
                
                return {
                    'metric': metric_name,
                    'value': current_value,
                    'expected_mean': round(mean_value, 3),
                    'expected_range': f"{mean_value - 2*std_dev:.3f} - {mean_value + 2*std_dev:.3f}",
                    'z_score': round(z_score, 3),
                    'severity': severity,
                    'deviation_percentage': round(((current_value - mean_value) / mean_value) * 100, 2)
                }
            
            return None
            
        except Exception as e:
            logger.error(f"Error detecting anomaly for {metric_name}: {e}")
            return None


# Global metrics collector instance
performance_collector = ConnectionPoolPerformanceCollector()


# Convenience functions
def record_connection_acquisition(acquisition_time_ms: float, context: str, pool_size: int, success: bool = True):
    """Record connection acquisition metrics"""
    performance_collector.record_connection_acquisition(acquisition_time_ms, context, pool_size, success=success)


def record_pool_utilization(active_connections: int, pool_size: int, context: str = "default"):
    """Record connection pool utilization"""
    performance_collector.record_connection_utilization(active_connections, pool_size, context)


def record_ssl_handshake(handshake_time_ms: float, context: str, success: bool = True):
    """Record SSL handshake performance"""
    performance_collector.record_ssl_performance(handshake_time_ms, context, success)


def get_real_time_performance_metrics() -> Dict[str, Any]:
    """Get real-time performance metrics"""
    return performance_collector.get_real_time_metrics()


def get_performance_summary(hours: int = 1) -> Dict[str, Any]:
    """Get performance summary"""
    return performance_collector.get_performance_summary(hours)


def export_performance_data(hours: int = 24) -> str:
    """Export performance data as JSON"""
    return performance_collector.export_metrics('json', hours)