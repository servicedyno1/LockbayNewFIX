"""
State Management Monitoring and Metrics
Comprehensive monitoring for Redis sessions, financial locks, and database operations
"""

import logging
import asyncio
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from dataclasses import dataclass
from enum import Enum

from utils.session_migration_helper import session_migration_helper
from utils.financial_operation_locker import financial_locker
from utils.enhanced_db_session_manager import enhanced_db_session_manager
from services.state_manager import state_manager

logger = logging.getLogger(__name__)


class HealthStatus(Enum):
    """System health status levels"""
    HEALTHY = "healthy"
    WARNING = "warning"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


@dataclass
class StateManagementMetrics:
    """Comprehensive state management metrics"""
    # Session metrics
    active_sessions: int
    total_sessions_created: int
    session_errors: int
    average_session_duration: float
    
    # Financial lock metrics
    active_locks: int
    total_locks_acquired: int
    lock_timeouts: int
    optimistic_conflicts: int
    
    # Database session metrics
    active_db_sessions: int
    total_db_operations: int
    db_operation_failures: int
    connection_pool_usage: float
    
    # Redis state manager metrics
    redis_operations: int
    redis_failures: int
    redis_connection_status: str
    
    # Overall health
    overall_status: HealthStatus
    last_updated: datetime
    
    # Performance metrics
    avg_response_time_ms: float
    p95_response_time_ms: float
    error_rate_percent: float


class StateManagementMonitor:
    """
    Comprehensive monitoring system for state management components
    
    Features:
    - Real-time metrics collection
    - Health status assessment
    - Performance monitoring
    - Alerting for critical issues
    - Historical trend tracking
    """
    
    def __init__(self):
        self.metrics_history: List[StateManagementMetrics] = []
        self.alert_thresholds = {
            'max_session_errors': 10,
            'max_lock_timeouts': 5,
            'max_db_failures': 5,
            'max_error_rate': 5.0,  # percent
            'max_response_time': 1000,  # milliseconds
            'max_connection_usage': 80.0  # percent
        }
        
        # Performance tracking
        self.operation_times = []
        self.error_counts = {
            'session_errors': 0,
            'lock_errors': 0,
            'db_errors': 0,
            'redis_errors': 0
        }
        
        # Background monitoring task
        self._monitoring_task = None
        self._initialize_monitoring()
    
    def _initialize_monitoring(self):
        """Initialize background monitoring task"""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                self._monitoring_task = loop.create_task(self._periodic_monitoring())
                logger.info("📈 Started state management monitoring task")
        except Exception as e:
            logger.warning(f"Could not start monitoring task: {e}")
    
    async def collect_comprehensive_metrics(self) -> StateManagementMetrics:
        """Collect comprehensive metrics from all state management components"""
        try:
            # Get session migration helper metrics
            session_metrics = getattr(session_migration_helper, 'metrics', {
                'active_sessions': 0,
                'total_created': 0,
                'errors': 0
            })
            
            # Get financial locker metrics
            lock_metrics = financial_locker.get_lock_metrics()
            
            # Get database session metrics
            db_metrics = enhanced_db_session_manager.get_session_metrics()
            
            # Get Redis state manager metrics
            redis_metrics = await state_manager.get_metrics() if state_manager else {
                'operations_total': 0,
                'operations_failed': 0,
                'is_connected': False
            }
            
            # Calculate performance metrics
            avg_response_time = sum(self.operation_times) / len(self.operation_times) if self.operation_times else 0.0
            p95_response_time = self._calculate_percentile(self.operation_times, 95) if self.operation_times else 0.0
            
            # Calculate error rate
            total_operations = (
                session_metrics.get('total_created', 0) +
                lock_metrics.get('total_locks', 0) +
                db_metrics.get('operations_total', 0) +
                redis_metrics.get('operations_total', 0)
            )
            
            total_errors = sum(self.error_counts.values())
            error_rate = (total_errors / total_operations * 100) if total_operations > 0 else 0.0
            
            # Assess overall health
            health_status = self._assess_health_status(
                session_metrics, lock_metrics, db_metrics, redis_metrics, error_rate
            )
            
            # Create comprehensive metrics
            metrics = StateManagementMetrics(
                # Session metrics
                active_sessions=session_metrics.get('active_sessions', 0),
                total_sessions_created=session_metrics.get('total_created', 0),
                session_errors=session_metrics.get('errors', 0),
                average_session_duration=session_metrics.get('avg_duration', 0.0),
                
                # Financial lock metrics
                active_locks=lock_metrics.get('active_locks', 0),
                total_locks_acquired=lock_metrics.get('successful_locks', 0),
                lock_timeouts=lock_metrics.get('lock_timeouts', 0),
                optimistic_conflicts=lock_metrics.get('optimistic_conflicts', 0),
                
                # Database session metrics
                active_db_sessions=db_metrics.get('active_sessions', {}).get('total', 0),
                total_db_operations=db_metrics.get('operations_total', 0),
                db_operation_failures=db_metrics.get('operations_failed', 0),
                connection_pool_usage=self._calculate_pool_usage(db_metrics.get('connection_pool', {})),
                
                # Redis metrics
                redis_operations=redis_metrics.get('operations_total', 0),
                redis_failures=redis_metrics.get('operations_failed', 0),
                redis_connection_status='connected' if redis_metrics.get('is_connected', False) else 'disconnected',
                
                # Overall health
                overall_status=health_status,
                last_updated=datetime.utcnow(),
                
                # Performance metrics
                avg_response_time_ms=avg_response_time,
                p95_response_time_ms=p95_response_time,
                error_rate_percent=error_rate
            )
            
            return metrics
            
        except Exception as e:
            logger.error(f"❌ Error collecting state management metrics: {e}")
            # Return minimal metrics on error
            return StateManagementMetrics(
                active_sessions=0, total_sessions_created=0, session_errors=1,
                average_session_duration=0.0, active_locks=0, total_locks_acquired=0,
                lock_timeouts=0, optimistic_conflicts=0, active_db_sessions=0,
                total_db_operations=0, db_operation_failures=1, connection_pool_usage=0.0,
                redis_operations=0, redis_failures=1, redis_connection_status='error',
                overall_status=HealthStatus.CRITICAL, last_updated=datetime.utcnow(),
                avg_response_time_ms=0.0, p95_response_time_ms=0.0, error_rate_percent=100.0
            )
    
    def _assess_health_status(
        self, 
        session_metrics: Dict[str, Any],
        lock_metrics: Dict[str, Any],
        db_metrics: Dict[str, Any],
        redis_metrics: Dict[str, Any],
        error_rate: float
    ) -> HealthStatus:
        """Assess overall health status based on all metrics"""
        critical_issues = []
        warning_issues = []
        
        # Check critical thresholds
        if session_metrics.get('errors', 0) > self.alert_thresholds['max_session_errors']:
            critical_issues.append('high_session_errors')
        
        if lock_metrics.get('lock_timeouts', 0) > self.alert_thresholds['max_lock_timeouts']:
            critical_issues.append('high_lock_timeouts')
        
        if db_metrics.get('operations_failed', 0) > self.alert_thresholds['max_db_failures']:
            critical_issues.append('high_db_failures')
        
        if error_rate > self.alert_thresholds['max_error_rate']:
            critical_issues.append('high_error_rate')
        
        if not redis_metrics.get('is_connected', False):
            critical_issues.append('redis_disconnected')
        
        # Check warning thresholds
        pool_usage = self._calculate_pool_usage(db_metrics.get('connection_pool', {}))
        if pool_usage > self.alert_thresholds['max_connection_usage']:
            warning_issues.append('high_connection_usage')
        
        avg_response = sum(self.operation_times) / len(self.operation_times) if self.operation_times else 0
        if avg_response > self.alert_thresholds['max_response_time']:
            warning_issues.append('slow_response_time')
        
        # Determine overall status
        if critical_issues:
            logger.error(f"❌ Critical state management issues detected: {critical_issues}")
            return HealthStatus.CRITICAL
        elif warning_issues:
            logger.warning(f"⚠️ State management warnings: {warning_issues}")
            return HealthStatus.WARNING
        else:
            return HealthStatus.HEALTHY
    
    def _calculate_pool_usage(self, pool_metrics: Dict[str, Any]) -> float:
        """Calculate connection pool usage percentage"""
        try:
            size = pool_metrics.get('size', 0)
            checked_out = pool_metrics.get('checked_out', 0)
            if size > 0:
                return (checked_out / size) * 100.0
            return 0.0
        except Exception:
            return 0.0
    
    def _calculate_percentile(self, data: List[float], percentile: int) -> float:
        """Calculate percentile from list of values"""
        if not data:
            return 0.0
        
        sorted_data = sorted(data)
        index = int((percentile / 100) * len(sorted_data))
        return sorted_data[min(index, len(sorted_data) - 1)]
    
    async def _periodic_monitoring(self):
        """Periodic monitoring loop"""
        while True:
            try:
                await asyncio.sleep(60)  # Monitor every minute
                
                # Collect metrics
                metrics = await self.collect_comprehensive_metrics()
                
                # Store in history (keep last 24 hours)
                self.metrics_history.append(metrics)
                if len(self.metrics_history) > 1440:  # 24 hours * 60 minutes
                    self.metrics_history = self.metrics_history[-1440:]
                
                # Log status
                logger.info(
                    f"📈 State Management Health Check: {metrics.overall_status.value.upper()} | "
                    f"Sessions: {metrics.active_sessions}, Locks: {metrics.active_locks}, "
                    f"DB Sessions: {metrics.active_db_sessions}, Error Rate: {metrics.error_rate_percent:.1f}%"
                )
                
                # Send alerts for critical issues
                if metrics.overall_status == HealthStatus.CRITICAL:
                    await self._send_critical_alert(metrics)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ Error in state management monitoring: {e}")
    
    async def _send_critical_alert(self, metrics: StateManagementMetrics):
        """Send critical alert for state management issues"""
        try:
            # This could be extended to send alerts via email/Telegram/Slack
            logger.critical(
                f"🚨 CRITICAL STATE MANAGEMENT ALERT:\n"
                f"Status: {metrics.overall_status.value}\n"
                f"Error Rate: {metrics.error_rate_percent:.1f}%\n"
                f"Active Sessions: {metrics.active_sessions}\n"
                f"Active Locks: {metrics.active_locks}\n"
                f"DB Failures: {metrics.db_operation_failures}\n"
                f"Redis Status: {metrics.redis_connection_status}"
            )
        except Exception as e:
            logger.error(f"Failed to send critical alert: {e}")
    
    def get_current_metrics(self) -> Optional[StateManagementMetrics]:
        """Get the most recent metrics"""
        return self.metrics_history[-1] if self.metrics_history else None
    
    def get_metrics_summary(self, hours: int = 1) -> Dict[str, Any]:
        """Get metrics summary for the specified time period"""
        cutoff_time = datetime.utcnow() - timedelta(hours=hours)
        recent_metrics = [
            m for m in self.metrics_history 
            if m.last_updated >= cutoff_time
        ]
        
        if not recent_metrics:
            return {'error': 'No metrics available for the specified period'}
        
        return {
            'time_period_hours': hours,
            'metrics_count': len(recent_metrics),
            'avg_active_sessions': sum(m.active_sessions for m in recent_metrics) / len(recent_metrics),
            'avg_active_locks': sum(m.active_locks for m in recent_metrics) / len(recent_metrics),
            'avg_error_rate': sum(m.error_rate_percent for m in recent_metrics) / len(recent_metrics),
            'avg_response_time_ms': sum(m.avg_response_time_ms for m in recent_metrics) / len(recent_metrics),
            'health_status_distribution': {
                status.value: sum(1 for m in recent_metrics if m.overall_status == status)
                for status in HealthStatus
            },
            'total_operations': sum(m.total_sessions_created + m.total_locks_acquired + m.total_db_operations for m in recent_metrics),
            'total_errors': sum(m.session_errors + m.lock_timeouts + m.db_operation_failures for m in recent_metrics)
        }
    
    def record_operation_time(self, duration_ms: float, operation_type: str = None):
        """Record operation timing for performance monitoring"""
        self.operation_times.append(duration_ms)
        # Keep only recent times (last 1000 operations)
        if len(self.operation_times) > 1000:
            self.operation_times = self.operation_times[-1000:]
    
    def record_error(self, error_type: str):
        """Record error for monitoring"""
        if error_type in self.error_counts:
            self.error_counts[error_type] += 1
        else:
            self.error_counts['other'] = self.error_counts.get('other', 0) + 1
    
    async def shutdown(self):
        """Graceful shutdown of monitoring"""
        logger.info("📋 Shutting down state management monitor")
        
        if self._monitoring_task:
            self._monitoring_task.cancel()
        
        logger.info("✅ State management monitor shutdown complete")


# Global state management monitor instance
state_management_monitor = StateManagementMonitor()
