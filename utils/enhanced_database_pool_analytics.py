"""
Enhanced Database Pool Analytics
Advanced monitoring, analytics, and optimization for database connection pools
"""

import logging
import asyncio
import threading
from datetime import datetime, timedelta
from collections import deque, defaultdict
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
import statistics
from enum import Enum
from concurrent.futures import ThreadPoolExecutor
import psutil

logger = logging.getLogger(__name__)


class ConnectionPoolMetric(Enum):
    """Connection pool metrics"""
    ACQUISITION_TIME = "acquisition_time"
    CONNECTION_AGE = "connection_age"
    QUERY_COUNT = "query_count"
    IDLE_TIME = "idle_time"
    ERROR_RATE = "error_rate"
    SSL_HANDSHAKE_TIME = "ssl_handshake_time"


@dataclass
class ConnectionEvent:
    """Individual connection event record"""
    timestamp: datetime
    event_type: str  # created, acquired, released, closed, error
    connection_id: str
    context_id: str
    duration_ms: Optional[float] = None
    error_message: Optional[str] = None
    ssl_info: Optional[Dict[str, Any]] = None
    query_count: int = 0
    memory_usage_mb: Optional[float] = None


@dataclass
class ConnectionAnalytics:
    """Connection analytics data"""
    connection_id: str
    created_at: datetime
    total_acquisitions: int = 0
    total_queries: int = 0
    total_duration_ms: float = 0.0
    avg_acquisition_time_ms: float = 0.0
    max_acquisition_time_ms: float = 0.0
    ssl_handshakes: int = 0
    ssl_handshake_time_ms: float = 0.0
    error_count: int = 0
    last_error: Optional[str] = None
    contexts_used: set = field(default_factory=set)
    performance_score: float = 1.0
    is_healthy: bool = True


class AdvancedPoolAnalytics:
    """Advanced connection pool analytics and optimization engine"""
    
    def __init__(self, max_events: int = 2000, analysis_interval: int = 60):
        self.max_events = max_events
        self.analysis_interval = analysis_interval
        
        # Event storage
        self.connection_events = deque(maxlen=max_events)
        self.connection_analytics = {}  # connection_id -> ConnectionAnalytics
        self.context_stats = defaultdict(lambda: {
            'total_connections': 0,
            'avg_acquisition_time': 0.0,
            'peak_acquisition_time': 0.0,
            'error_rate': 0.0,
            'queries_per_connection': 0.0,
            'ssl_issues': 0
        })
        
        # Performance metrics
        self.pool_metrics = {
            'hourly_patterns': defaultdict(list),  # hour -> [metric_values]
            'workload_patterns': defaultdict(list),  # pattern_type -> values
            'performance_trends': deque(maxlen=288),  # 24h of 5-min intervals
            'optimization_recommendations': [],
            'alert_conditions': []
        }
        
        # Real-time monitoring
        self._metrics_lock = threading.Lock()
        self._optimization_cache = {}
        self._last_analysis = datetime.utcnow()
        self._executor = ThreadPoolExecutor(max_workers=2)
        
        # Performance thresholds
        self.thresholds = {
            'acquisition_time_warning_ms': 100,
            'acquisition_time_critical_ms': 500,
            'connection_age_max_hours': 4,
            'error_rate_warning': 0.05,  # 5%
            'error_rate_critical': 0.15,  # 15%
            'ssl_handshake_max_ms': 1000,
            'memory_per_connection_mb': 10,
            'idle_connection_max_minutes': 30
        }
        
        # Start background analytics
        asyncio.create_task(self._analytics_loop())
    
    def record_connection_event(
        self, 
        event_type: str, 
        connection_id: str, 
        context_id: str,
        duration_ms: Optional[float] = None,
        error_message: Optional[str] = None,
        ssl_info: Optional[Dict[str, Any]] = None,
        query_count: int = 0
    ):
        """Record a connection event with rich analytics data"""
        with self._metrics_lock:
            # Create event record
            event = ConnectionEvent(
                timestamp=datetime.utcnow(),
                event_type=event_type,
                connection_id=connection_id,
                context_id=context_id,
                duration_ms=duration_ms,
                error_message=error_message,
                ssl_info=ssl_info,
                query_count=query_count,
                memory_usage_mb=self._get_memory_usage()
            )
            
            self.connection_events.append(event)
            
            # Update connection analytics
            self._update_connection_analytics(event)
            
            # Update context statistics
            self._update_context_stats(event)
            
            # Check for performance anomalies
            self._check_performance_anomalies(event)
    
    def _update_connection_analytics(self, event: ConnectionEvent):
        """Update analytics for a specific connection"""
        conn_id = event.connection_id
        
        if conn_id not in self.connection_analytics:
            self.connection_analytics[conn_id] = ConnectionAnalytics(
                connection_id=conn_id,
                created_at=event.timestamp
            )
        
        analytics = self.connection_analytics[conn_id]
        
        # Update based on event type
        if event.event_type == 'acquired':
            analytics.total_acquisitions += 1
            if event.duration_ms:
                analytics.total_duration_ms += event.duration_ms
                analytics.avg_acquisition_time_ms = (
                    analytics.total_duration_ms / analytics.total_acquisitions
                )
                analytics.max_acquisition_time_ms = max(
                    analytics.max_acquisition_time_ms, 
                    event.duration_ms
                )
        
        elif event.event_type == 'query':
            analytics.total_queries += event.query_count
            
        elif event.event_type == 'ssl_handshake':
            analytics.ssl_handshakes += 1
            if event.duration_ms:
                analytics.ssl_handshake_time_ms += event.duration_ms
                
        elif event.event_type == 'error':
            analytics.error_count += 1
            analytics.last_error = event.error_message
            analytics.is_healthy = False
        
        # Update context usage
        analytics.contexts_used.add(event.context_id)
        
        # Calculate performance score (0.0 = worst, 1.0 = best)
        analytics.performance_score = self._calculate_performance_score(analytics)
    
    def _calculate_performance_score(self, analytics: ConnectionAnalytics) -> float:
        """Calculate performance score for a connection"""
        score = 1.0
        
        # Penalize slow acquisition times
        if analytics.avg_acquisition_time_ms > self.thresholds['acquisition_time_critical_ms']:
            score -= 0.4
        elif analytics.avg_acquisition_time_ms > self.thresholds['acquisition_time_warning_ms']:
            score -= 0.2
        
        # Penalize high error rates
        if analytics.total_acquisitions > 0:
            error_rate = analytics.error_count / analytics.total_acquisitions
            if error_rate > self.thresholds['error_rate_critical']:
                score -= 0.3
            elif error_rate > self.thresholds['error_rate_warning']:
                score -= 0.15
        
        # Penalize slow SSL handshakes
        if analytics.ssl_handshakes > 0:
            avg_ssl_time = analytics.ssl_handshake_time_ms / analytics.ssl_handshakes
            if avg_ssl_time > self.thresholds['ssl_handshake_max_ms']:
                score -= 0.2
        
        # Bonus for high utilization
        if analytics.total_queries > 100:
            score += 0.1
            
        return max(0.0, min(1.0, score))
    
    def _update_context_stats(self, event: ConnectionEvent):
        """Update statistics for a specific context"""
        context = event.context_id
        stats = self.context_stats[context]
        
        if event.event_type == 'acquired':
            stats['total_connections'] += 1
            if event.duration_ms:
                # Update acquisition time stats
                current_avg = stats['avg_acquisition_time']
                total_conns = stats['total_connections']
                stats['avg_acquisition_time'] = (
                    (current_avg * (total_conns - 1) + event.duration_ms) / total_conns
                )
                stats['peak_acquisition_time'] = max(
                    stats['peak_acquisition_time'], 
                    event.duration_ms
                )
        
        elif event.event_type == 'error':
            # Update error rate
            if stats['total_connections'] > 0:
                error_events = len([
                    e for e in self.connection_events 
                    if e.context_id == context and e.event_type == 'error'
                ])
                stats['error_rate'] = error_events / stats['total_connections']
        
        elif event.event_type == 'ssl_error':
            stats['ssl_issues'] += 1
    
    def _check_performance_anomalies(self, event: ConnectionEvent):
        """Check for performance anomalies and generate alerts"""
        anomalies = []
        
        # Check acquisition time anomalies
        if (event.event_type == 'acquired' and event.duration_ms and 
            event.duration_ms > self.thresholds['acquisition_time_critical_ms']):
            anomalies.append({
                'type': 'slow_acquisition',
                'severity': 'critical',
                'message': f"Very slow connection acquisition: {event.duration_ms:.1f}ms",
                'context': event.context_id,
                'threshold': self.thresholds['acquisition_time_critical_ms']
            })
        
        # Check SSL handshake anomalies
        if (event.event_type == 'ssl_handshake' and event.duration_ms and
            event.duration_ms > self.thresholds['ssl_handshake_max_ms']):
            anomalies.append({
                'type': 'slow_ssl_handshake',
                'severity': 'warning',
                'message': f"Slow SSL handshake: {event.duration_ms:.1f}ms",
                'context': event.context_id,
                'ssl_info': event.ssl_info
            })
        
        # Log anomalies
        for anomaly in anomalies:
            if anomaly['severity'] == 'critical':
                logger.warning(f"🚨 POOL ANOMALY [{anomaly['type']}]: {anomaly['message']}")
            else:
                logger.debug(f"⚠️ Pool anomaly [{anomaly['type']}]: {anomaly['message']}")
        
        # Store for analysis
        if anomalies:
            self.pool_metrics['alert_conditions'].extend(anomalies)
    
    def get_comprehensive_analytics(self) -> Dict[str, Any]:
        """Get comprehensive pool analytics"""
        with self._metrics_lock:
            now = datetime.utcnow()
            hour_ago = now - timedelta(hours=1)
            
            # Filter recent events
            recent_events = [
                e for e in self.connection_events 
                if e.timestamp >= hour_ago
            ]
            
            # Calculate aggregate metrics
            acquisition_times = [
                e.duration_ms for e in recent_events 
                if e.event_type == 'acquired' and e.duration_ms
            ]
            
            ssl_handshake_times = [
                e.duration_ms for e in recent_events 
                if e.event_type == 'ssl_handshake' and e.duration_ms
            ]
            
            error_events = [
                e for e in recent_events 
                if e.event_type == 'error'
            ]
            
            # Connection health analysis
            healthy_connections = sum(
                1 for analytics in self.connection_analytics.values()
                if analytics.is_healthy
            )
            
            total_connections = len(self.connection_analytics)
            health_percentage = (
                (healthy_connections / max(total_connections, 1)) * 100
            )
            
            return {
                'timestamp': now.isoformat(),
                'summary': {
                    'total_connections': total_connections,
                    'healthy_connections': healthy_connections,
                    'health_percentage': round(health_percentage, 2),
                    'events_last_hour': len(recent_events),
                    'errors_last_hour': len(error_events),
                    'avg_performance_score': round(
                        statistics.mean([
                            a.performance_score 
                            for a in self.connection_analytics.values()
                        ]) if self.connection_analytics else 0.0, 3
                    )
                },
                'performance_metrics': {
                    'acquisition_times_ms': {
                        'count': len(acquisition_times),
                        'avg': round(statistics.mean(acquisition_times), 2) if acquisition_times else 0,
                        'median': round(statistics.median(acquisition_times), 2) if acquisition_times else 0,
                        'p95': round(
                            sorted(acquisition_times)[int(len(acquisition_times) * 0.95)]
                            if acquisition_times else 0, 2
                        ),
                        'max': round(max(acquisition_times), 2) if acquisition_times else 0
                    },
                    'ssl_handshake_times_ms': {
                        'count': len(ssl_handshake_times),
                        'avg': round(statistics.mean(ssl_handshake_times), 2) if ssl_handshake_times else 0,
                        'max': round(max(ssl_handshake_times), 2) if ssl_handshake_times else 0
                    },
                    'error_analysis': {
                        'total_errors': len(error_events),
                        'error_rate_percentage': round(
                            (len(error_events) / max(len(recent_events), 1)) * 100, 2
                        ),
                        'most_common_errors': self._get_most_common_errors(error_events)
                    }
                },
                'context_performance': dict(self.context_stats),
                'optimization_recommendations': self._generate_optimization_recommendations(),
                'recent_alerts': self.pool_metrics['alert_conditions'][-10:],  # Last 10 alerts
                'connection_details': [
                    {
                        'connection_id': analytics.connection_id,
                        'performance_score': analytics.performance_score,
                        'total_acquisitions': analytics.total_acquisitions,
                        'avg_acquisition_time_ms': round(analytics.avg_acquisition_time_ms, 2),
                        'error_count': analytics.error_count,
                        'contexts_used': len(analytics.contexts_used),
                        'is_healthy': analytics.is_healthy
                    }
                    for analytics in sorted(
                        self.connection_analytics.values(),
                        key=lambda x: x.performance_score,
                        reverse=True
                    )[:20]  # Top 20 connections
                ]
            }
    
    def _generate_optimization_recommendations(self) -> List[Dict[str, Any]]:
        """Generate pool optimization recommendations"""
        recommendations = []
        
        # Analyze acquisition times
        recent_acquisitions = [
            e.duration_ms for e in list(self.connection_events)[-100:]
            if e.event_type == 'acquired' and e.duration_ms
        ]
        
        if recent_acquisitions:
            avg_acq_time = statistics.mean(recent_acquisitions)
            p95_acq_time = sorted(recent_acquisitions)[int(len(recent_acquisitions) * 0.95)]
            
            if avg_acq_time > self.thresholds['acquisition_time_warning_ms']:
                recommendations.append({
                    'type': 'pool_sizing',
                    'priority': 'high' if avg_acq_time > 200 else 'medium',
                    'title': 'Increase Pool Size',
                    'description': f"Average acquisition time ({avg_acq_time:.1f}ms) exceeds threshold. Consider increasing pool size.",
                    'suggested_action': 'Increase pool_size by 20-30%'
                })
            
            if p95_acq_time > self.thresholds['acquisition_time_critical_ms']:
                recommendations.append({
                    'type': 'pool_overflow',
                    'priority': 'critical',
                    'title': 'Pool Exhaustion Risk',
                    'description': f"95th percentile acquisition time ({p95_acq_time:.1f}ms) is critical.",
                    'suggested_action': 'Increase max_overflow or investigate connection leaks'
                })
        
        # Analyze error patterns
        error_rate = len([e for e in list(self.connection_events)[-100:] if e.event_type == 'error']) / 100
        if error_rate > self.thresholds['error_rate_warning']:
            recommendations.append({
                'type': 'error_handling',
                'priority': 'high' if error_rate > 0.1 else 'medium',
                'title': 'High Error Rate Detected',
                'description': f"Connection error rate ({error_rate:.1%}) exceeds threshold.",
                'suggested_action': 'Investigate SSL stability and connection timeout settings'
            })
        
        # Check for connection age issues
        old_connections = [
            analytics for analytics in self.connection_analytics.values()
            if (datetime.utcnow() - analytics.created_at) > timedelta(hours=self.thresholds['connection_age_max_hours'])
        ]
        
        if len(old_connections) > len(self.connection_analytics) * 0.3:  # >30% old connections
            recommendations.append({
                'type': 'connection_recycling',
                'priority': 'medium',
                'title': 'Connection Age Management',
                'description': f"{len(old_connections)} connections are older than {self.thresholds['connection_age_max_hours']}h",
                'suggested_action': 'Reduce pool_recycle time or implement connection rotation'
            })
        
        return recommendations
    
    def _get_most_common_errors(self, error_events: List[ConnectionEvent]) -> List[Dict[str, Any]]:
        """Get most common error types"""
        error_counts = defaultdict(int)
        
        for event in error_events:
            if event.error_message:
                # Categorize errors
                if "SSL" in event.error_message:
                    error_counts["SSL Connection Error"] += 1
                elif "timeout" in event.error_message.lower():
                    error_counts["Connection Timeout"] += 1
                elif "refused" in event.error_message.lower():
                    error_counts["Connection Refused"] += 1
                else:
                    error_counts["Other"] += 1
        
        return [
            {'error_type': error_type, 'count': count}
            for error_type, count in sorted(
                error_counts.items(), 
                key=lambda x: x[1], 
                reverse=True
            )[:5]
        ]
    
    def _get_memory_usage(self) -> float:
        """Get current memory usage in MB"""
        try:
            process = psutil.Process()
            return process.memory_info().rss / 1024 / 1024
        except Exception as e:
            logger.debug(f"Could not get memory usage: {e}")
            return 0.0
    
    async def _analytics_loop(self):
        """Background analytics processing loop"""
        logger.info("📊 Starting advanced database pool analytics engine...")
        
        while True:
            try:
                await asyncio.sleep(self.analysis_interval)
                
                # Perform periodic analysis
                await self._periodic_analysis()
                
                # Clean up old data
                self._cleanup_old_data()
                
            except Exception as e:
                logger.error(f"Error in pool analytics loop: {e}")
                await asyncio.sleep(60)
    
    async def _periodic_analysis(self):
        """Perform periodic analytics and optimization"""
        with self._metrics_lock:
            now = datetime.utcnow()
            
            # Update performance trends
            current_metrics = {
                'timestamp': now,
                'total_connections': len(self.connection_analytics),
                'avg_acquisition_time': 0.0,
                'error_rate': 0.0,
                'memory_usage_mb': self._get_memory_usage()
            }
            
            # Calculate current averages
            recent_events = [
                e for e in list(self.connection_events)[-50:]
                if e.timestamp >= now - timedelta(minutes=5)
            ]
            
            acquisition_times = [
                e.duration_ms for e in recent_events
                if e.event_type == 'acquired' and e.duration_ms
            ]
            
            if acquisition_times:
                current_metrics['avg_acquisition_time'] = statistics.mean(acquisition_times)
            
            error_count = len([e for e in recent_events if e.event_type == 'error'])
            if recent_events:
                current_metrics['error_rate'] = error_count / len(recent_events)
            
            self.pool_metrics['performance_trends'].append(current_metrics)
            
            # Log performance summary
            if len(self.pool_metrics['performance_trends']) % 12 == 0:  # Every hour
                logger.info(
                    f"📊 Pool Analytics Summary: "
                    f"Connections={current_metrics['total_connections']}, "
                    f"AvgAcqTime={current_metrics['avg_acquisition_time']:.1f}ms, "
                    f"ErrorRate={current_metrics['error_rate']:.1%}, "
                    f"Memory={current_metrics['memory_usage_mb']:.1f}MB"
                )
    
    def _cleanup_old_data(self):
        """Clean up old analytics data"""
        cutoff_time = datetime.utcnow() - timedelta(hours=24)
        
        # Clean up old connection analytics for closed connections
        to_remove = [
            conn_id for conn_id, analytics in self.connection_analytics.items()
            if analytics.created_at < cutoff_time and not analytics.is_healthy
        ]
        
        for conn_id in to_remove:
            del self.connection_analytics[conn_id]
        
        # Clean up old alert conditions
        self.pool_metrics['alert_conditions'] = [
            alert for alert in self.pool_metrics['alert_conditions']
            if 'timestamp' not in alert or 
            datetime.fromisoformat(alert['timestamp']) >= cutoff_time
        ]
        
        logger.debug(f"🧹 Cleaned up {len(to_remove)} old connection analytics records")
    
    def get_real_time_metrics(self) -> Dict[str, Any]:
        """Get real-time performance metrics"""
        with self._metrics_lock:
            now = datetime.utcnow()
            last_5_min = now - timedelta(minutes=5)
            
            recent_events = [
                e for e in self.connection_events
                if e.timestamp >= last_5_min
            ]
            
            return {
                'timestamp': now.isoformat(),
                'connections_active': len(self.connection_analytics),
                'events_last_5min': len(recent_events),
                'current_memory_mb': self._get_memory_usage(),
                'performance_score': round(
                    statistics.mean([
                        a.performance_score 
                        for a in self.connection_analytics.values()
                    ]) if self.connection_analytics else 1.0, 3
                ),
                'health_status': self._calculate_overall_health()
            }
    
    def _calculate_overall_health(self) -> str:
        """Calculate overall pool health status"""
        if not self.connection_analytics:
            return 'healthy'
        
        healthy_count = sum(1 for a in self.connection_analytics.values() if a.is_healthy)
        health_percentage = healthy_count / len(self.connection_analytics)
        
        if health_percentage >= 0.95:
            return 'healthy'
        elif health_percentage >= 0.85:
            return 'warning'
        else:
            return 'critical'


# Global analytics instance
pool_analytics = AdvancedPoolAnalytics()


def record_pool_event(
    event_type: str,
    connection_id: str,
    context_id: str,
    duration_ms: Optional[float] = None,
    error_message: Optional[str] = None,
    ssl_info: Optional[Dict[str, Any]] = None,
    query_count: int = 0
):
    """Record a pool event for analytics"""
    pool_analytics.record_connection_event(
        event_type, connection_id, context_id, duration_ms,
        error_message, ssl_info, query_count
    )


def get_pool_analytics() -> Dict[str, Any]:
    """Get comprehensive pool analytics"""
    return pool_analytics.get_comprehensive_analytics()


def get_real_time_pool_metrics() -> Dict[str, Any]:
    """Get real-time pool metrics"""
    return pool_analytics.get_real_time_metrics()