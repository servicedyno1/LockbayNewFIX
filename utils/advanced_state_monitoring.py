"""
Advanced State Management Monitoring and Metrics
Comprehensive monitoring for all advanced state management features
Provides real-time insights into system health, performance, and coordination
"""

import asyncio
import logging
from typing import Dict, List, Optional, Any, Union
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict

from services.advanced_state_management import advanced_state_manager, get_system_health, get_system_metrics
from utils.admin_alert_system import send_admin_alert
from config import Config

logger = logging.getLogger(__name__)


@dataclass
class MetricSnapshot:
    """Snapshot of metrics at a point in time"""
    timestamp: datetime
    instance_id: str
    metrics: Dict[str, Any]
    health_status: Dict[str, Any]
    system_load: Dict[str, float]


@dataclass
class AlertRule:
    """Rule for triggering alerts based on metrics"""
    name: str
    metric_path: str  # e.g., "saga_metrics.sagas_failed"
    operator: str     # >, <, >=, <=, ==, !=
    threshold: Union[int, float]
    window_minutes: int = 5
    cooldown_minutes: int = 15
    severity: str = "warning"  # info, warning, error, critical


class AdvancedStateMonitor:
    """
    Comprehensive monitoring for advanced state management systems
    
    Features:
    - Real-time metrics collection
    - Health status monitoring
    - Performance trend analysis
    - Alert rules and notifications
    - Dashboard data aggregation
    - Anomaly detection
    """
    
    def __init__(self):
        self.running = False
        self.monitor_task: Optional[asyncio.Task] = None
        self.alert_task: Optional[asyncio.Task] = None
        
        # Configuration
        self.collection_interval = Config.ADVANCED_MONITORING_INTERVAL if hasattr(Config, 'ADVANCED_MONITORING_INTERVAL') else 30
        self.retention_hours = Config.ADVANCED_MONITORING_RETENTION if hasattr(Config, 'ADVANCED_MONITORING_RETENTION') else 24
        self.alert_check_interval = Config.ADVANCED_ALERT_INTERVAL if hasattr(Config, 'ADVANCED_ALERT_INTERVAL') else 60
        
        # Metrics storage
        self.metric_snapshots: List[MetricSnapshot] = []
        self.max_snapshots = (self.retention_hours * 3600) // self.collection_interval
        
        # Alert rules
        self.alert_rules: List[AlertRule] = []
        self.alert_history: Dict[str, datetime] = {}
        
        # Performance baselines
        self.baselines = {
            'avg_response_time_ms': 0.0,
            'avg_cleanup_time_ms': 0.0,
            'avg_saga_execution_time_ms': 0.0,
            'job_success_rate': 1.0,
            'leader_election_stability': 1.0
        }
        
        # Setup default alert rules
        self._setup_default_alert_rules()
        
        logger.info("📊 Advanced state monitoring initialized")
    
    def _setup_default_alert_rules(self):
        """Setup default alert rules for critical metrics"""
        
        # System health alerts
        self.add_alert_rule(AlertRule(
            name="system_unhealthy",
            metric_path="health_status.overall_healthy",
            operator="==",
            threshold=False,
            window_minutes=2,
            cooldown_minutes=10,
            severity="critical"
        ))
        
        # Leader election alerts
        self.add_alert_rule(AlertRule(
            name="leader_election_frequent_changes",
            metric_path="system_metrics.leader_election_changes",
            operator=">",
            threshold=5,
            window_minutes=10,
            cooldown_minutes=30,
            severity="warning"
        ))
        
        # Saga failure alerts
        self.add_alert_rule(AlertRule(
            name="saga_high_failure_rate",
            metric_path="saga_metrics.sagas_failed",
            operator=">",
            threshold=10,
            window_minutes=15,
            cooldown_minutes=30,
            severity="error"
        ))
        
        # Job coordination alerts
        self.add_alert_rule(AlertRule(
            name="job_coordination_conflicts",
            metric_path="job_idempotency_metrics.coordination_conflicts",
            operator=">",
            threshold=20,
            window_minutes=10,
            cooldown_minutes=20,
            severity="warning"
        ))
        
        # Cleanup performance alerts
        self.add_alert_rule(AlertRule(
            name="cleanup_performance_degraded",
            metric_path="cleanup_metrics.last_cleanup_time_ms",
            operator=">",
            threshold=30000,  # 30 seconds
            window_minutes=5,
            cooldown_minutes=15,
            severity="warning"
        ))
        
        # State manager connectivity alerts
        self.add_alert_rule(AlertRule(
            name="state_manager_errors",
            metric_path="state_manager_metrics.connection_errors",
            operator=">",
            threshold=5,
            window_minutes=5,
            cooldown_minutes=10,
            severity="error"
        ))
    
    def add_alert_rule(self, rule: AlertRule):
        """Add a custom alert rule"""
        self.alert_rules.append(rule)
        logger.info(f"📢 Added alert rule: {rule.name}")
    
    async def start(self):
        """Start the monitoring service"""
        if self.running:
            logger.warning("Advanced state monitoring already running")
            return
        
        self.running = True
        
        # Start monitoring tasks
        self.monitor_task = asyncio.create_task(self._monitoring_loop())
        self.alert_task = asyncio.create_task(self._alert_loop())
        
        logger.info(f"🚀 Advanced state monitoring started (interval: {self.collection_interval}s)")
    
    async def stop(self):
        """Stop the monitoring service"""
        if not self.running:
            return
        
        self.running = False
        
        # Cancel tasks
        for task in [self.monitor_task, self.alert_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        
        logger.info("🛑 Advanced state monitoring stopped")
    
    async def _monitoring_loop(self):
        """Main monitoring loop for metrics collection"""
        while self.running:
            try:
                await self._collect_metrics()
                await asyncio.sleep(self.collection_interval)
            except Exception as e:
                logger.error(f"❌ Error in monitoring loop: {e}")
                await asyncio.sleep(self.collection_interval)
    
    async def _alert_loop(self):
        """Alert monitoring loop"""
        while self.running:
            try:
                await self._check_alert_rules()
                await asyncio.sleep(self.alert_check_interval)
            except Exception as e:
                logger.error(f"❌ Error in alert loop: {e}")
                await asyncio.sleep(self.alert_check_interval)
    
    async def _collect_metrics(self):
        """Collect comprehensive metrics from all systems"""
        try:
            # Get metrics from advanced state manager
            metrics = await get_system_metrics()
            health_status = await get_system_health()
            
            # Add system load information
            system_load = await self._get_system_load()
            
            # Create snapshot
            snapshot = MetricSnapshot(
                timestamp=datetime.utcnow(),
                instance_id=advanced_state_manager.instance_id,
                metrics=metrics,
                health_status=asdict(health_status) if health_status else {},
                system_load=system_load
            )
            
            # Store snapshot
            self.metric_snapshots.append(snapshot)
            
            # Maintain retention limit
            if len(self.metric_snapshots) > self.max_snapshots:
                self.metric_snapshots = self.metric_snapshots[-self.max_snapshots:]
            
            # Update baselines
            await self._update_baselines(snapshot)
            
            logger.debug(f"📊 Collected metrics snapshot: {len(self.metric_snapshots)} total snapshots")
            
        except Exception as e:
            logger.error(f"❌ Error collecting metrics: {e}")
    
    async def _get_system_load(self) -> Dict[str, float]:
        """Get system load information"""
        try:
            import psutil
            
            return {
                'cpu_percent': psutil.cpu_percent(interval=1),
                'memory_percent': psutil.virtual_memory().percent,
                'disk_percent': psutil.disk_usage('/').percent,
                'load_average_1m': psutil.getloadavg()[0] if hasattr(psutil, 'getloadavg') else 0.0,
                'network_io_read_mb': psutil.net_io_counters().bytes_recv / (1024 * 1024),
                'network_io_write_mb': psutil.net_io_counters().bytes_sent / (1024 * 1024)
            }
        except Exception as e:
            logger.warning(f"Could not collect system load: {e}")
            return {}
    
    async def _update_baselines(self, snapshot: MetricSnapshot):
        """Update performance baselines based on recent metrics"""
        try:
            # Calculate moving averages for key metrics
            recent_snapshots = self.metric_snapshots[-10:]  # Last 10 snapshots
            
            if len(recent_snapshots) >= 5:  # Need minimum data
                # Update response time baseline
                response_times = []
                for s in recent_snapshots:
                    cleanup_time = s.metrics.get('cleanup_metrics', {}).get('last_cleanup_time_ms', 0)
                    if cleanup_time > 0:
                        response_times.append(cleanup_time)
                
                if response_times:
                    self.baselines['avg_cleanup_time_ms'] = sum(response_times) / len(response_times)
                
                # Update success rate baselines
                completed_sagas = sum(s.metrics.get('saga_metrics', {}).get('sagas_completed', 0) for s in recent_snapshots)
                failed_sagas = sum(s.metrics.get('saga_metrics', {}).get('sagas_failed', 0) for s in recent_snapshots)
                
                if completed_sagas + failed_sagas > 0:
                    self.baselines['job_success_rate'] = completed_sagas / (completed_sagas + failed_sagas)
        
        except Exception as e:
            logger.warning(f"Error updating baselines: {e}")
    
    async def _check_alert_rules(self):
        """Check all alert rules and trigger alerts if needed"""
        if not self.metric_snapshots:
            return
        
        current_time = datetime.utcnow()
        
        for rule in self.alert_rules:
            try:
                # Check cooldown
                last_alert = self.alert_history.get(rule.name)
                if last_alert and (current_time - last_alert).total_seconds() < rule.cooldown_minutes * 60:
                    continue
                
                # Get recent snapshots for window
                window_start = current_time - timedelta(minutes=rule.window_minutes)
                recent_snapshots = [
                    s for s in self.metric_snapshots 
                    if s.timestamp >= window_start
                ]
                
                if not recent_snapshots:
                    continue
                
                # Check rule condition
                if await self._evaluate_alert_rule(rule, recent_snapshots):
                    await self._trigger_alert(rule, recent_snapshots[-1])
                    self.alert_history[rule.name] = current_time
            
            except Exception as e:
                logger.error(f"❌ Error checking alert rule {rule.name}: {e}")
    
    async def _evaluate_alert_rule(self, rule: AlertRule, snapshots: List[MetricSnapshot]) -> bool:
        """Evaluate if an alert rule condition is met"""
        try:
            latest_snapshot = snapshots[-1]
            
            # Navigate to the metric value using dot notation
            value = latest_snapshot.metrics
            for part in rule.metric_path.split('.'):
                if isinstance(value, dict) and part in value:
                    value = value[part]
                else:
                    return False  # Metric path not found
            
            # Check if health status path
            if rule.metric_path.startswith('health_status.'):
                value = latest_snapshot.health_status
                for part in rule.metric_path.split('.')[1:]:  # Skip 'health_status'
                    if isinstance(value, dict) and part in value:
                        value = value[part]
                    else:
                        return False
            
            # Evaluate condition
            if rule.operator == ">":
                return value > rule.threshold
            elif rule.operator == "<":
                return value < rule.threshold
            elif rule.operator == ">=":
                return value >= rule.threshold
            elif rule.operator == "<=":
                return value <= rule.threshold
            elif rule.operator == "==":
                return value == rule.threshold
            elif rule.operator == "!=":
                return value != rule.threshold
            else:
                logger.warning(f"Unknown operator in alert rule: {rule.operator}")
                return False
        
        except Exception as e:
            logger.warning(f"Error evaluating alert rule {rule.name}: {e}")
            return False
    
    async def _trigger_alert(self, rule: AlertRule, snapshot: MetricSnapshot):
        """Trigger an alert based on rule violation"""
        try:
            alert_message = f"🚨 Advanced State Alert: {rule.name}\n"
            alert_message += f"Severity: {rule.severity.upper()}\n"
            alert_message += f"Condition: {rule.metric_path} {rule.operator} {rule.threshold}\n"
            alert_message += f"Instance: {snapshot.instance_id}\n"
            alert_message += f"Time: {snapshot.timestamp.isoformat()}\n"
            
            # Add relevant context
            if rule.name == "system_unhealthy":
                issues = snapshot.health_status.get('issues', [])
                alert_message += f"Issues: {', '.join(issues)}\n"
            
            # Send alert
            logger.warning(alert_message)
            
            # Send to admin alert system if available
            try:
                await send_admin_alert(
                    title=f"Advanced State Alert: {rule.name}",
                    message=alert_message,
                    severity=rule.severity,
                    category="advanced_state_management"
                )
            except Exception as e:
                logger.warning(f"Could not send admin alert: {e}")
        
        except Exception as e:
            logger.error(f"❌ Error triggering alert: {e}")
    
    def get_current_metrics(self) -> Optional[Dict[str, Any]]:
        """Get the most recent metrics snapshot"""
        if not self.metric_snapshots:
            return None
        
        return {
            'snapshot': asdict(self.metric_snapshots[-1]),
            'baselines': self.baselines,
            'alert_rules_count': len(self.alert_rules),
            'snapshots_count': len(self.metric_snapshots)
        }
    
    def get_metrics_history(self, hours: int = 1) -> List[Dict[str, Any]]:
        """Get metrics history for the specified time period"""
        cutoff_time = datetime.utcnow() - timedelta(hours=hours)
        
        return [
            asdict(snapshot) for snapshot in self.metric_snapshots
            if snapshot.timestamp >= cutoff_time
        ]
    
    def get_performance_trends(self) -> Dict[str, Any]:
        """Get performance trend analysis"""
        if len(self.metric_snapshots) < 10:
            return {'error': 'Insufficient data for trend analysis'}
        
        try:
            recent_snapshots = self.metric_snapshots[-20:]  # Last 20 snapshots
            older_snapshots = self.metric_snapshots[-40:-20] if len(self.metric_snapshots) >= 40 else []
            
            trends = {}
            
            # Cleanup time trend
            recent_cleanup_times = [
                s.metrics.get('cleanup_metrics', {}).get('last_cleanup_time_ms', 0)
                for s in recent_snapshots
            ]
            older_cleanup_times = [
                s.metrics.get('cleanup_metrics', {}).get('last_cleanup_time_ms', 0)
                for s in older_snapshots
            ]
            
            if recent_cleanup_times and older_cleanup_times:
                recent_avg = sum(recent_cleanup_times) / len(recent_cleanup_times)
                older_avg = sum(older_cleanup_times) / len(older_cleanup_times)
                trends['cleanup_time_trend'] = 'improving' if recent_avg < older_avg else 'degrading'
            
            # Success rate trend
            recent_successes = sum(s.metrics.get('saga_metrics', {}).get('sagas_completed', 0) for s in recent_snapshots)
            recent_failures = sum(s.metrics.get('saga_metrics', {}).get('sagas_failed', 0) for s in recent_snapshots)
            
            if recent_successes + recent_failures > 0:
                recent_success_rate = recent_successes / (recent_successes + recent_failures)
                trends['success_rate'] = recent_success_rate
                trends['success_rate_status'] = 'good' if recent_success_rate > 0.95 else 'needs_attention'
            
            return trends
        
        except Exception as e:
            return {'error': f'Error calculating trends: {e}'}
    
    async def generate_health_report(self) -> Dict[str, Any]:
        """Generate comprehensive health report"""
        try:
            current_metrics = self.get_current_metrics()
            trends = self.get_performance_trends()
            
            if not current_metrics:
                return {'error': 'No metrics available'}
            
            snapshot = current_metrics['snapshot']
            
            report = {
                'timestamp': datetime.utcnow().isoformat(),
                'instance_id': snapshot['instance_id'],
                'overall_status': 'healthy' if snapshot['health_status'].get('overall_healthy') else 'unhealthy',
                'system_uptime': snapshot['metrics'].get('uptime_seconds', 0),
                'leader_status': 'leader' if snapshot['metrics'].get('is_leader') else 'follower',
                'performance_summary': {
                    'job_processing': {
                        'total_processed': snapshot['metrics'].get('job_idempotency_metrics', {}).get('jobs_completed', 0),
                        'duplicates_prevented': snapshot['metrics'].get('job_idempotency_metrics', {}).get('duplicates_prevented', 0),
                        'coordination_conflicts': snapshot['metrics'].get('job_idempotency_metrics', {}).get('coordination_conflicts', 0)
                    },
                    'saga_processing': {
                        'total_completed': snapshot['metrics'].get('saga_metrics', {}).get('sagas_completed', 0),
                        'total_failed': snapshot['metrics'].get('saga_metrics', {}).get('sagas_failed', 0),
                        'total_compensated': snapshot['metrics'].get('saga_metrics', {}).get('sagas_compensated', 0),
                        'running_sagas': snapshot['metrics'].get('saga_metrics', {}).get('running_sagas', 0)
                    },
                    'cleanup_efficiency': {
                        'total_cleaned': snapshot['metrics'].get('cleanup_metrics', {}).get('total_keys_cleaned', 0),
                        'last_cleanup_time_ms': snapshot['metrics'].get('cleanup_metrics', {}).get('last_cleanup_time_ms', 0),
                        'memory_freed_mb': snapshot['metrics'].get('cleanup_metrics', {}).get('memory_freed_total_mb', 0)
                    }
                },
                'system_load': snapshot['system_load'],
                'health_issues': snapshot['health_status'].get('issues', []),
                'trends': trends,
                'baselines': self.baselines,
                'active_alerts': len([
                    rule.name for rule in self.alert_rules
                    if rule.name in self.alert_history and 
                    (datetime.utcnow() - self.alert_history[rule.name]).total_seconds() < rule.cooldown_minutes * 60
                ])
            }
            
            return report
        
        except Exception as e:
            return {'error': f'Error generating health report: {e}'}


# Global instance
advanced_state_monitor = AdvancedStateMonitor()


# Integration functions
async def start_advanced_monitoring():
    """Start advanced state monitoring"""
    await advanced_state_monitor.start()


async def stop_advanced_monitoring():
    """Stop advanced state monitoring"""
    await advanced_state_monitor.stop()


async def get_advanced_metrics():
    """Get current advanced metrics"""
    return advanced_state_monitor.get_current_metrics()


async def get_health_report():
    """Get comprehensive health report"""
    return await advanced_state_monitor.generate_health_report()


def add_custom_alert_rule(name: str, metric_path: str, operator: str, threshold: Union[int, float], **kwargs):
    """Add a custom alert rule"""
    rule = AlertRule(
        name=name,
        metric_path=metric_path,
        operator=operator,
        threshold=threshold,
        **kwargs
    )
    advanced_state_monitor.add_alert_rule(rule)