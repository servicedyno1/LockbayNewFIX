"""
Completion Time Trends Monitor
Tracks performance metrics over time for key system operations with trend analysis,
regression detection, and historical performance insights.
"""

import logging
import asyncio
import time
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict
from enum import Enum
from collections import deque, defaultdict
import statistics
from contextlib import asynccontextmanager
from functools import wraps

# Import safe timing utilities
from utils.safe_timing import safe_duration_calculation, validate_and_log_duration

logger = logging.getLogger(__name__)


class OperationType(Enum):
    """Types of operations to monitor"""
    ONBOARDING = "onboarding"
    WEBHOOK_PROCESSING = "webhook_processing"
    DATABASE_QUERY = "database_query"
    SYSTEM_HEALTH_CHECK = "system_health_check"
    TRANSACTION_PROCESSING = "transaction_processing"
    EMAIL_VERIFICATION = "email_verification"
    OTP_VERIFICATION = "otp_verification"
    CASHOUT_PROCESSING = "cashout_processing"
    EXCHANGE_PROCESSING = "exchange_processing"
    ESCROW_CREATION = "escrow_creation"
    API_CALL = "api_call"
    FILE_PROCESSING = "file_processing"
    CRYPTO_VALIDATION = "crypto_validation"
    USER_AUTHENTICATION = "user_authentication"


class TrendDirection(Enum):
    """Trend direction indicators"""
    IMPROVING = "improving"
    STABLE = "stable"
    DEGRADING = "degrading"
    VOLATILE = "volatile"
    INSUFFICIENT_DATA = "insufficient_data"


@dataclass
class CompletionTimeRecord:
    """Individual completion time record"""
    operation_type: OperationType
    operation_name: str
    completion_time_ms: float
    timestamp: datetime
    user_id: Optional[int] = None
    metadata: Dict[str, Any] = None
    success: bool = True
    error_message: Optional[str] = None


@dataclass
class TrendMetrics:
    """Trend analysis metrics for an operation"""
    operation_type: OperationType
    operation_name: str
    
    # Current metrics
    current_avg_ms: float
    current_median_ms: float
    current_p95_ms: float
    current_p99_ms: float
    
    # Historical comparison (vs previous period)
    avg_change_percent: float
    median_change_percent: float
    p95_change_percent: float
    trend_direction: TrendDirection
    
    # Analysis period
    sample_count: int
    analysis_period_hours: int
    last_updated: datetime
    
    # Performance indicators
    baseline_avg_ms: float  # Historical baseline
    performance_score: float  # 0-100, where 100 is optimal
    regression_detected: bool = False
    improvement_detected: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        result = asdict(self)
        result['operation_type'] = self.operation_type.value
        result['trend_direction'] = self.trend_direction.value
        result['last_updated'] = self.last_updated.isoformat()
        return result


class CompletionTimeTrendsMonitor:
    """Comprehensive completion time trends monitoring system"""
    
    def __init__(self, 
                 max_records_per_operation: int = 1000,
                 trend_analysis_hours: int = 4,
                 baseline_analysis_days: int = 7):
        """
        Initialize the trends monitor
        
        Args:
            max_records_per_operation: Maximum records to keep per operation type
            trend_analysis_hours: Hours to analyze for current trends
            baseline_analysis_days: Days to analyze for baseline metrics
        """
        self.max_records_per_operation = max_records_per_operation
        self.trend_analysis_hours = trend_analysis_hours
        self.baseline_analysis_days = baseline_analysis_days
        
        # Storage for completion time records
        self.completion_records: Dict[str, deque] = defaultdict(
            lambda: deque(maxlen=max_records_per_operation)
        )
        
        # Cached trend metrics
        self.trend_metrics: Dict[str, TrendMetrics] = {}
        self.last_trend_update = {}
        
        # Performance thresholds (can be configured per operation)
        self.performance_thresholds = {
            OperationType.ONBOARDING: {
                'baseline_ms': 15000,  # 15 seconds baseline
                'warning_ms': 25000,   # 25 seconds warning
                'critical_ms': 45000,  # 45 seconds critical
                'regression_threshold': 0.3  # 30% increase triggers regression alert
            },
            OperationType.WEBHOOK_PROCESSING: {
                'baseline_ms': 500,
                'warning_ms': 2000,
                'critical_ms': 5000,
                'regression_threshold': 0.5
            },
            OperationType.DATABASE_QUERY: {
                'baseline_ms': 100,
                'warning_ms': 1000,
                'critical_ms': 3000,
                'regression_threshold': 0.4
            },
            OperationType.TRANSACTION_PROCESSING: {
                'baseline_ms': 3000,
                'warning_ms': 10000,
                'critical_ms': 30000,
                'regression_threshold': 0.3
            },
            # Add more operation-specific thresholds as needed
        }
        
        # Default thresholds for unconfigured operations
        self.default_thresholds = {
            'baseline_ms': 1000,
            'warning_ms': 5000,
            'critical_ms': 15000,
            'regression_threshold': 0.4
        }
        
        # Alerting and reporting
        self.alert_callbacks = []
        self.is_monitoring_active = False
        
        logger.info("🔍 Completion Time Trends Monitor initialized")

    def start_monitoring(self):
        """Start the trends monitoring system"""
        if self.is_monitoring_active:
            logger.warning("Trends monitoring already active")
            return
            
        self.is_monitoring_active = True
        
        # Start background trend analysis
        asyncio.create_task(self._trend_analysis_loop())
        
        logger.info("📈 COMPLETION_TIME_MONITOR: Monitoring started with comprehensive data collection logging")
        logger.info(f"📊 MONITOR_CONFIG: max_records={self.max_records_per_operation}, "
                   f"trend_analysis_hours={self.trend_analysis_hours}, "
                   f"baseline_days={self.baseline_analysis_days}")
        logger.info(f"🎯 TRACKING_OPERATIONS: {[op.value for op in OperationType]}")

    def stop_monitoring(self):
        """Stop the trends monitoring system"""
        self.is_monitoring_active = False
        logger.info("⏹️ Completion time trends monitoring stopped")

    def record_completion_time(self, 
                             operation_type: OperationType,
                             operation_name: str,
                             completion_time_ms: float,
                             user_id: Optional[int] = None,
                             metadata: Dict[str, Any] = None,
                             success: bool = True,
                             error_message: Optional[str] = None):
        """Record a completion time for trend analysis"""
        try:
            # Validate completion time is non-negative before recording
            validated_completion_time = validate_and_log_duration(
                completion_time_ms,
                f"{operation_type.value}:{operation_name}",
                max_expected_ms=300000  # 5 minutes max reasonable time
            )
            
            # Additional safety check for extremely long durations
            if validated_completion_time > 300000:  # 5 minutes
                logger.warning(f"Extremely long completion time for {operation_name}: "
                             f"{validated_completion_time:.0f}ms - this may indicate a timing issue")
            
            record = CompletionTimeRecord(
                operation_type=operation_type,
                operation_name=operation_name,
                completion_time_ms=validated_completion_time,
                timestamp=datetime.now(),
                user_id=user_id,
                metadata=metadata or {},
                success=success,
                error_message=error_message
            )
            
            key = f"{operation_type.value}:{operation_name}"
            self.completion_records[key].append(record)
            
            # COMPREHENSIVE LOGGING: Log all data collection for verification
            logger.info(f"📊 DATA_COLLECTED: {operation_type.value}:{operation_name} | "
                       f"Time: {completion_time_ms:.0f}ms | "
                       f"User: {user_id or 'N/A'} | "
                       f"Success: {success} | "
                       f"Records in buffer: {len(self.completion_records[key])}")
            
            # Log significant completion times
            thresholds = self.performance_thresholds.get(operation_type, self.default_thresholds)
            
            if completion_time_ms > thresholds['critical_ms']:
                logger.warning(f"⚠️ CRITICAL completion time: {operation_name} took {completion_time_ms:.0f}ms (threshold: {thresholds['critical_ms']:.0f}ms)")
            elif completion_time_ms > thresholds['warning_ms']:
                logger.info(f"⏳ Slow completion: {operation_name} took {completion_time_ms:.0f}ms (threshold: {thresholds['warning_ms']:.0f}ms)")
            else:
                logger.debug(f"✅ {operation_name} completed in {completion_time_ms:.0f}ms")
            
            # Log metadata if provided (for debugging)
            if metadata:
                logger.debug(f"📋 METADATA for {operation_name}: {metadata}")
                
        except Exception as e:
            logger.error(f"❌ ERROR recording completion time for {operation_name}: {e}", exc_info=True)

    @asynccontextmanager
    async def track_operation(self, 
                            operation_type: OperationType,
                            operation_name: str,
                            user_id: Optional[int] = None,
                            metadata: Dict[str, Any] = None):
        """Context manager to automatically track operation completion time"""
        start_time = time.perf_counter()  # Use perf_counter for better precision
        success = True
        error_message = None
        
        try:
            yield
        except Exception as e:
            success = False
            error_message = str(e)
            raise
        finally:
            # Use safe duration calculation to prevent negative values
            completion_time_ms = safe_duration_calculation(
                start_time, 
                time.perf_counter(),
                scale_factor=1000.0,  # Convert to milliseconds
                min_duration=0.0
            )
            
            # Validate duration and log if unusual
            completion_time_ms = validate_and_log_duration(
                completion_time_ms, 
                f"{operation_type.value}:{operation_name}"
            )
            
            self.record_completion_time(
                operation_type=operation_type,
                operation_name=operation_name,
                completion_time_ms=completion_time_ms,
                user_id=user_id,
                metadata=metadata,
                success=success,
                error_message=error_message
            )

    def track_completion_time(self, 
                            operation_type: OperationType,
                            operation_name: str,
                            user_id: Optional[int] = None,
                            metadata: Dict[str, Any] = None):
        """Decorator to automatically track function completion time"""
        def decorator(func):
            @wraps(func)
            async def async_wrapper(*args, **kwargs):
                async with self.track_operation(operation_type, operation_name, user_id, metadata):
                    return await func(*args, **kwargs)
            
            @wraps(func)
            def sync_wrapper(*args, **kwargs):
                # For sync functions, we'll track manually
                start_time = time.perf_counter()  # Use perf_counter for better precision
                success = True
                error_message = None
                
                try:
                    result = func(*args, **kwargs)
                    return result
                except Exception as e:
                    success = False
                    error_message = str(e)
                    raise
                finally:
                    # Use safe duration calculation to prevent negative values
                    completion_time_ms = safe_duration_calculation(
                        start_time,
                        time.perf_counter(),
                        scale_factor=1000.0,  # Convert to milliseconds
                        min_duration=0.0
                    )
                    
                    # Validate duration and log if unusual
                    completion_time_ms = validate_and_log_duration(
                        completion_time_ms,
                        f"{operation_type.value}:{operation_name}"
                    )
                    
                    self.record_completion_time(
                        operation_type=operation_type,
                        operation_name=operation_name,
                        completion_time_ms=completion_time_ms,
                        user_id=user_id,
                        metadata=metadata,
                        success=success,
                        error_message=error_message
                    )
            
            # Return appropriate wrapper based on function type
            if asyncio.iscoroutinefunction(func):
                return async_wrapper
            else:
                return sync_wrapper
        
        return decorator

    async def _trend_analysis_loop(self):
        """Background loop for trend analysis"""
        while self.is_monitoring_active:
            try:
                await self._update_all_trends()
                await asyncio.sleep(300)  # Update trends every 5 minutes
            except Exception as e:
                logger.error(f"Error in trend analysis loop: {e}")
                await asyncio.sleep(60)

    async def _update_all_trends(self):
        """Update trend metrics for all tracked operations"""
        for key, records in self.completion_records.items():
            if len(records) >= 10:  # Need minimum data for trends
                try:
                    trend_metrics = await self._calculate_trend_metrics(key, records)
                    self.trend_metrics[key] = trend_metrics
                    self.last_trend_update[key] = datetime.now()
                    
                    # Check for significant changes
                    await self._check_for_alerts(trend_metrics)
                    
                except Exception as e:
                    logger.error(f"Error calculating trends for {key}: {e}")

    async def _calculate_trend_metrics(self, key: str, records: deque) -> TrendMetrics:
        """Calculate comprehensive trend metrics for an operation"""
        operation_type_str, operation_name = key.split(":", 1)
        operation_type = OperationType(operation_type_str)
        
        # Get current period data (last N hours)
        current_cutoff = datetime.now() - timedelta(hours=self.trend_analysis_hours)
        current_records = [r for r in records if r.timestamp > current_cutoff and r.success]
        
        # Get historical baseline data (previous period of same duration)
        baseline_start = current_cutoff - timedelta(hours=self.trend_analysis_hours)
        baseline_records = [r for r in records 
                          if baseline_start <= r.timestamp <= current_cutoff and r.success]
        
        if len(current_records) < 5:
            return TrendMetrics(
                operation_type=operation_type,
                operation_name=operation_name,
                current_avg_ms=0,
                current_median_ms=0,
                current_p95_ms=0,
                current_p99_ms=0,
                avg_change_percent=0,
                median_change_percent=0,
                p95_change_percent=0,
                trend_direction=TrendDirection.INSUFFICIENT_DATA,
                sample_count=len(current_records),
                analysis_period_hours=self.trend_analysis_hours,
                last_updated=datetime.now(),
                baseline_avg_ms=0,
                performance_score=0
            )
        
        # Calculate current metrics
        current_times = [r.completion_time_ms for r in current_records]
        current_avg = statistics.mean(current_times)
        current_median = statistics.median(current_times)
        current_p95 = statistics.quantiles(current_times, n=20)[18] if len(current_times) > 20 else max(current_times)
        current_p99 = statistics.quantiles(current_times, n=100)[98] if len(current_times) > 100 else max(current_times)
        
        # Calculate baseline metrics if available
        baseline_avg = 0
        avg_change_percent = 0
        median_change_percent = 0
        p95_change_percent = 0
        
        if baseline_records:
            baseline_times = [r.completion_time_ms for r in baseline_records]
            baseline_avg = statistics.mean(baseline_times)
            baseline_median = statistics.median(baseline_times)
            baseline_p95 = statistics.quantiles(baseline_times, n=20)[18] if len(baseline_times) > 20 else max(baseline_times)
            
            # Calculate percentage changes
            avg_change_percent = ((current_avg - baseline_avg) / baseline_avg) * 100 if baseline_avg > 0 else 0
            median_change_percent = ((current_median - baseline_median) / baseline_median) * 100 if baseline_median > 0 else 0
            p95_change_percent = ((current_p95 - baseline_p95) / baseline_p95) * 100 if baseline_p95 > 0 else 0
        
        # Determine trend direction
        trend_direction = self._determine_trend_direction(avg_change_percent, median_change_percent, p95_change_percent)
        
        # Calculate performance score
        thresholds = self.performance_thresholds.get(operation_type, self.default_thresholds)
        performance_score = self._calculate_performance_score(current_avg, thresholds['baseline_ms'])
        
        # Check for regression/improvement
        regression_threshold = thresholds['regression_threshold']
        regression_detected = avg_change_percent > (regression_threshold * 100)
        improvement_detected = avg_change_percent < -(regression_threshold * 100)
        
        return TrendMetrics(
            operation_type=operation_type,
            operation_name=operation_name,
            current_avg_ms=current_avg,
            current_median_ms=current_median,
            current_p95_ms=current_p95,
            current_p99_ms=current_p99,
            avg_change_percent=avg_change_percent,
            median_change_percent=median_change_percent,
            p95_change_percent=p95_change_percent,
            trend_direction=trend_direction,
            sample_count=len(current_records),
            analysis_period_hours=self.trend_analysis_hours,
            last_updated=datetime.now(),
            baseline_avg_ms=baseline_avg,
            performance_score=performance_score,
            regression_detected=regression_detected,
            improvement_detected=improvement_detected
        )

    def _determine_trend_direction(self, avg_change: float, median_change: float, p95_change: float) -> TrendDirection:
        """Determine overall trend direction based on metric changes"""
        changes = [avg_change, median_change, p95_change]
        
        # Check for volatility (high variance in changes)
        if max(changes) - min(changes) > 50:  # High variance
            return TrendDirection.VOLATILE
        
        avg_of_changes = sum(changes) / len(changes)
        
        if avg_of_changes > 15:  # 15% degradation
            return TrendDirection.DEGRADING
        elif avg_of_changes < -15:  # 15% improvement
            return TrendDirection.IMPROVING
        else:
            return TrendDirection.STABLE

    def _calculate_performance_score(self, current_avg: float, baseline: float) -> float:
        """Calculate performance score (0-100) based on current vs baseline"""
        if baseline <= 0:
            return 50.0  # Neutral score if no baseline
        
        ratio = current_avg / baseline
        
        if ratio <= 1.0:  # At or better than baseline
            return min(100.0, 100.0 / ratio)
        else:  # Worse than baseline
            return max(0.0, 100.0 - ((ratio - 1.0) * 100))

    async def _check_for_alerts(self, metrics: TrendMetrics):
        """Check trend metrics for alerting conditions"""
        alerts = []
        
        # Regression detection
        if metrics.regression_detected:
            alerts.append({
                'type': 'regression',
                'severity': 'critical',
                'message': f"Performance regression detected for {metrics.operation_name}: "
                          f"{metrics.avg_change_percent:.1f}% increase in completion time",
                'metrics': metrics
            })
        
        # Improvement detection
        elif metrics.improvement_detected:
            alerts.append({
                'type': 'improvement',
                'severity': 'info',
                'message': f"Performance improvement detected for {metrics.operation_name}: "
                          f"{abs(metrics.avg_change_percent):.1f}% decrease in completion time",
                'metrics': metrics
            })
        
        # High completion times
        thresholds = self.performance_thresholds.get(metrics.operation_type, self.default_thresholds)
        if metrics.current_avg_ms > thresholds['critical_ms']:
            alerts.append({
                'type': 'high_completion_time',
                'severity': 'critical',
                'message': f"Critical completion time for {metrics.operation_name}: "
                          f"{metrics.current_avg_ms:.0f}ms (threshold: {thresholds['critical_ms']:.0f}ms)",
                'metrics': metrics
            })
        elif metrics.current_avg_ms > thresholds['warning_ms']:
            alerts.append({
                'type': 'high_completion_time',
                'severity': 'warning',
                'message': f"High completion time for {metrics.operation_name}: "
                          f"{metrics.current_avg_ms:.0f}ms (threshold: {thresholds['warning_ms']:.0f}ms)",
                'metrics': metrics
            })
        
        # Send alerts
        for alert in alerts:
            await self._send_alert(alert)

    async def _send_alert(self, alert: Dict[str, Any]):
        """Send alert to configured callbacks and log"""
        level = getattr(logging, alert['severity'].upper(), logging.INFO)
        logger.log(level, f"📈 TREND ALERT: {alert['message']}")
        
        # Call registered alert callbacks
        for callback in self.alert_callbacks:
            try:
                await callback(alert)
            except Exception as e:
                logger.error(f"Error in alert callback: {e}")

    def get_trends_summary(self, operation_type: Optional[OperationType] = None) -> Dict[str, Any]:
        """Get comprehensive trends summary"""
        if operation_type:
            # Filter by operation type
            filtered_metrics = {k: v for k, v in self.trend_metrics.items() 
                              if v.operation_type == operation_type}
        else:
            filtered_metrics = self.trend_metrics
        
        # Overall statistics
        total_operations = len(filtered_metrics)
        regressions = sum(1 for m in filtered_metrics.values() if m.regression_detected)
        improvements = sum(1 for m in filtered_metrics.values() if m.improvement_detected)
        
        # Performance score summary
        scores = [m.performance_score for m in filtered_metrics.values()]
        avg_performance_score = statistics.mean(scores) if scores else 0
        
        return {
            'summary': {
                'total_operations_monitored': total_operations,
                'regressions_detected': regressions,
                'improvements_detected': improvements,
                'avg_performance_score': avg_performance_score,
                'last_updated': datetime.now().isoformat()
            },
            'metrics': {k: v.to_dict() for k, v in filtered_metrics.items()},
            'operation_type_filter': operation_type.value if operation_type else None
        }

    def add_alert_callback(self, callback):
        """Add callback function for alerts"""
        self.alert_callbacks.append(callback)

    def get_operation_history(self, 
                            operation_type: OperationType,
                            operation_name: str,
                            hours: int = 24) -> List[Dict[str, Any]]:
        """Get historical completion time data for an operation"""
        key = f"{operation_type.value}:{operation_name}"
        records = self.completion_records.get(key, [])
        
        cutoff = datetime.now() - timedelta(hours=hours)
        historical_records = [r for r in records if r.timestamp > cutoff]
        
        return [
            {
                'completion_time_ms': r.completion_time_ms,
                'timestamp': r.timestamp.isoformat(),
                'success': r.success,
                'user_id': r.user_id,
                'metadata': r.metadata,
                'error_message': r.error_message
            }
            for r in historical_records
        ]


# Global instance for easy access
completion_time_monitor = CompletionTimeTrendsMonitor()