"""
SSL Connection Monitor
Comprehensive monitoring and alerting for SSL database connection stability
"""

import logging
import threading
from datetime import datetime, timedelta
from collections import deque, defaultdict
from typing import Dict, Optional, Any
from dataclasses import dataclass, asdict
import asyncio

logger = logging.getLogger(__name__)


@dataclass
class SSLConnectionEvent:
    """SSL connection event record"""
    timestamp: datetime
    event_type: str  # 'error', 'recovery', 'timeout', 'retry'
    context: str
    error_message: str
    attempt_number: int = 1
    recovery_time_ms: Optional[float] = None


class SSLConnectionHealthTracker:
    """Track SSL connection health across the application"""
    
    def __init__(self, max_events: int = 200):
        self.max_events = max_events
        self.ssl_events = deque(maxlen=max_events)
        self.context_stats = defaultdict(lambda: {
            'total_errors': 0,
            'consecutive_errors': 0,
            'last_error': None,
            'last_recovery': None,
            'total_recoveries': 0
        })
        self._lock = threading.Lock()
        
        # Alert thresholds
        self.alert_thresholds = {
            'consecutive_errors': 5,
            'errors_per_hour': 20,
            'error_rate_percentage': 15.0,  # % of connections failing
            'recovery_time_threshold_ms': 5000  # 5 seconds
        }
        
        # Health status
        self.overall_health = 'healthy'  # healthy, warning, critical
        self.last_health_check = datetime.utcnow()
        self.last_alert_sent = {}
        self.alert_cooldown = 300  # 5 minutes
    
    def record_ssl_error(self, context: str, error_message: str, attempt_number: int = 1):
        """Record an SSL connection error"""
        with self._lock:
            event = SSLConnectionEvent(
                timestamp=datetime.utcnow(),
                event_type='error',
                context=context,
                error_message=error_message,
                attempt_number=attempt_number
            )
            
            self.ssl_events.append(event)
            
            # Update context stats
            stats = self.context_stats[context]
            stats['total_errors'] += 1
            stats['consecutive_errors'] += 1
            stats['last_error'] = event.timestamp
            
            # Log SSL error with context
            logger.warning(
                f"🔌 SSL CONNECTION ERROR in {context} (attempt {attempt_number}): {error_message}"
            )
            
            # Check if we need to send alerts
            self._check_and_send_alerts(context)
    
    def record_ssl_recovery(self, context: str, recovery_time_ms: float):
        """Record SSL connection recovery"""
        with self._lock:
            event = SSLConnectionEvent(
                timestamp=datetime.utcnow(),
                event_type='recovery',
                context=context,
                error_message='',
                recovery_time_ms=recovery_time_ms
            )
            
            self.ssl_events.append(event)
            
            # Update context stats
            stats = self.context_stats[context]
            stats['consecutive_errors'] = 0  # Reset consecutive error count
            stats['last_recovery'] = event.timestamp
            stats['total_recoveries'] += 1
            
            # Log recovery
            if recovery_time_ms > self.alert_thresholds['recovery_time_threshold_ms']:
                logger.warning(
                    f"🔌 SSL CONNECTION RECOVERED in {context} after {recovery_time_ms:.1f}ms (slow recovery)"
                )
            else:
                logger.info(
                    f"✅ SSL CONNECTION RECOVERED in {context} after {recovery_time_ms:.1f}ms"
                )
    
    def record_ssl_retry(self, context: str, attempt_number: int, error_message: str):
        """Record SSL connection retry attempt"""
        with self._lock:
            event = SSLConnectionEvent(
                timestamp=datetime.utcnow(),
                event_type='retry',
                context=context,
                error_message=error_message,
                attempt_number=attempt_number
            )
            
            self.ssl_events.append(event)
            
            logger.debug(
                f"🔄 SSL CONNECTION RETRY in {context} (attempt {attempt_number}): {error_message}"
            )
    
    def get_ssl_health_summary(self) -> Dict[str, Any]:
        """Get comprehensive SSL health summary"""
        with self._lock:
            now = datetime.utcnow()
            last_hour = now - timedelta(hours=1)
            last_5_min = now - timedelta(minutes=5)
            
            # Filter recent events
            recent_events = [e for e in self.ssl_events if e.timestamp >= last_hour]
            very_recent_events = [e for e in self.ssl_events if e.timestamp >= last_5_min]
            
            # Calculate metrics
            total_errors = len([e for e in recent_events if e.event_type == 'error'])
            total_recoveries = len([e for e in recent_events if e.event_type == 'recovery'])
            total_retries = len([e for e in recent_events if e.event_type == 'retry'])
            
            recent_errors = len([e for e in very_recent_events if e.event_type == 'error'])
            
            # Calculate error rate
            total_connection_attempts = total_errors + total_recoveries + sum(
                1 for stats in self.context_stats.values() 
                if stats['last_recovery'] and stats['last_recovery'] >= last_hour
            )
            
            error_rate = (total_errors / max(total_connection_attempts, 1)) * 100
            
            # Determine overall health
            health_status = self._calculate_health_status(total_errors, recent_errors, error_rate)
            
            # Context-specific stats
            context_health = {}
            for context, stats in self.context_stats.items():
                context_health[context] = {
                    'consecutive_errors': stats['consecutive_errors'],
                    'total_errors': stats['total_errors'],
                    'total_recoveries': stats['total_recoveries'],
                    'last_error': stats['last_error'].isoformat() if stats['last_error'] else None,
                    'last_recovery': stats['last_recovery'].isoformat() if stats['last_recovery'] else None,
                    'status': 'critical' if stats['consecutive_errors'] >= self.alert_thresholds['consecutive_errors'] else 'healthy'
                }
            
            return {
                'overall_health': health_status,
                'last_updated': now.isoformat(),
                'metrics': {
                    'errors_last_hour': total_errors,
                    'errors_last_5min': recent_errors,
                    'recoveries_last_hour': total_recoveries,
                    'retries_last_hour': total_retries,
                    'error_rate_percentage': round(error_rate, 2),
                    'total_connection_attempts': total_connection_attempts
                },
                'context_health': context_health,
                'alert_thresholds': self.alert_thresholds,
                'recent_events': [asdict(e) for e in very_recent_events[-10:]]  # Last 10 events
            }
    
    def _calculate_health_status(self, total_errors: int, recent_errors: int, error_rate: float) -> str:
        """Calculate overall SSL health status"""
        if (recent_errors >= 3 or 
            total_errors >= self.alert_thresholds['errors_per_hour'] or
            error_rate >= self.alert_thresholds['error_rate_percentage']):
            return 'critical'
        elif recent_errors >= 1 or total_errors >= self.alert_thresholds['errors_per_hour'] // 2:
            return 'warning'
        else:
            return 'healthy'
    
    def _check_and_send_alerts(self, context: str):
        """Check if alerts should be sent and send them"""
        now = datetime.utcnow()
        stats = self.context_stats[context]
        
        # Check consecutive errors threshold
        if stats['consecutive_errors'] >= self.alert_thresholds['consecutive_errors']:
            alert_key = f"{context}_consecutive"
            if (alert_key not in self.last_alert_sent or 
                now - self.last_alert_sent[alert_key] > timedelta(seconds=self.alert_cooldown)):
                
                self._send_ssl_alert(
                    f"CRITICAL: {stats['consecutive_errors']} consecutive SSL errors in {context}",
                    'critical',
                    context
                )
                self.last_alert_sent[alert_key] = now
    
    def _send_ssl_alert(self, message: str, severity: str, context: str):
        """Send SSL connection alert"""
        logger.error(f"🚨 SSL ALERT [{severity.upper()}]: {message}")
        
        # Here you could integrate with your alerting system
        # For example: send to admin notifications, email alerts, etc.
        try:
            # Integration point for admin alerts
            from utils.admin_alert_system import send_admin_alert
            send_admin_alert(f"SSL Connection Alert: {message}", severity)
        except ImportError:
            logger.debug("Admin alert system not available for SSL alerts")
        except Exception as e:
            logger.error(f"Failed to send SSL alert: {e}")
    
    def get_context_health(self, context: str) -> Dict[str, Any]:
        """Get health status for specific context"""
        with self._lock:
            if context not in self.context_stats:
                return {'status': 'healthy', 'consecutive_errors': 0}
            
            stats = self.context_stats[context]
            status = 'critical' if stats['consecutive_errors'] >= self.alert_thresholds['consecutive_errors'] else 'healthy'
            
            return {
                'status': status,
                'consecutive_errors': stats['consecutive_errors'],
                'total_errors': stats['total_errors'],
                'total_recoveries': stats['total_recoveries']
            }
    
    async def health_monitor_loop(self, check_interval: int = 60):
        """Background task to monitor SSL health"""
        logger.info("🔌 Starting SSL connection health monitor...")
        
        while True:
            try:
                await asyncio.sleep(check_interval)
                
                # Get health summary
                health_summary = self.get_ssl_health_summary()
                
                # Log periodic health status
                if health_summary['overall_health'] != 'healthy':
                    logger.warning(
                        f"🔌 SSL HEALTH: {health_summary['overall_health'].upper()} | "
                        f"Errors (1h): {health_summary['metrics']['errors_last_hour']} | "
                        f"Error Rate: {health_summary['metrics']['error_rate_percentage']}%"
                    )
                else:
                    logger.debug(f"🔌 SSL Health: {health_summary['overall_health']}")
                
                # Check for degraded contexts
                for context, health in health_summary['context_health'].items():
                    if health['status'] != 'healthy':
                        logger.warning(
                            f"🔌 SSL CONTEXT ALERT [{context}]: "
                            f"consecutive_errors={health['consecutive_errors']}, "
                            f"total_errors={health['total_errors']}"
                        )
                        
            except Exception as e:
                logger.error(f"Error in SSL health monitor: {e}")
                await asyncio.sleep(60)  # Wait longer on error


# Global SSL monitor instance
ssl_monitor = SSLConnectionHealthTracker()


def record_ssl_error(context: str, error_message: str, attempt_number: int = 1):
    """Convenience function to record SSL error"""
    ssl_monitor.record_ssl_error(context, error_message, attempt_number)


def record_ssl_recovery(context: str, recovery_time_ms: float):
    """Convenience function to record SSL recovery"""
    ssl_monitor.record_ssl_recovery(context, recovery_time_ms)


def record_ssl_retry(context: str, attempt_number: int, error_message: str):
    """Convenience function to record SSL retry"""
    ssl_monitor.record_ssl_retry(context, attempt_number, error_message)


def get_ssl_health_summary() -> Dict[str, Any]:
    """Get SSL health summary"""
    return ssl_monitor.get_ssl_health_summary()


def get_context_health(context: str) -> Dict[str, Any]:
    """Get health for specific context"""
    return ssl_monitor.get_context_health(context)