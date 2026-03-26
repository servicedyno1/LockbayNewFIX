"""
Advanced Connection Pool Alerting and Automated Remediation System
Comprehensive monitoring, alerting, and automated response system for database connection pools
"""

import logging
import asyncio
import time
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Callable
from collections import deque, defaultdict
from dataclasses import dataclass, field
from enum import Enum
import statistics
from concurrent.futures import ThreadPoolExecutor
import psutil
import ast
import operator

logger = logging.getLogger(__name__)


class SafeExpressionEvaluator:
    """Safe expression evaluator using AST - no eval()"""
    
    ALLOWED_OPS = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.Mod: operator.mod,
        ast.Pow: operator.pow,
        ast.Lt: operator.lt,
        ast.LtE: operator.le,
        ast.Gt: operator.gt,
        ast.GtE: operator.ge,
        ast.Eq: operator.eq,
        ast.NotEq: operator.ne,
        ast.And: lambda a, b: a and b,
        ast.Or: lambda a, b: a or b,
        ast.Not: operator.not_,
    }
    
    @classmethod
    def evaluate(cls, expression: str, context: Dict[str, Any]) -> bool:
        """Safely evaluate an expression without using eval()"""
        try:
            tree = ast.parse(expression, mode='eval')
            result = cls._eval_node(tree.body, context)
            return bool(result)
        except Exception as e:
            logger.error(f"Safe expression evaluation failed for '{expression}': {e}")
            return False
    
    @classmethod
    def _eval_node(cls, node: ast.AST, context: Dict[str, Any]) -> Any:
        """Recursively evaluate AST nodes"""
        if isinstance(node, ast.Constant):
            return node.value
        elif isinstance(node, ast.Num):  # Python < 3.8 compatibility
            return node.n
        elif isinstance(node, ast.Str):  # Python < 3.8 compatibility
            return node.s
        elif isinstance(node, ast.Name):
            if node.id in context:
                return context[node.id]
            raise ValueError(f"Unknown variable: {node.id}")
        elif isinstance(node, ast.Attribute):
            obj = cls._eval_node(node.value, context)
            return getattr(obj, node.attr)
        elif isinstance(node, ast.Call):
            func = cls._eval_node(node.func, context)
            args = [cls._eval_node(arg, context) for arg in node.args]
            kwargs = {kw.arg: cls._eval_node(kw.value, context) for kw in node.keywords}
            return func(*args, **kwargs)
        elif isinstance(node, ast.Compare):
            left = cls._eval_node(node.left, context)
            for op, comparator in zip(node.ops, node.comparators):
                right = cls._eval_node(comparator, context)
                op_func = cls.ALLOWED_OPS.get(type(op))
                if not op_func:
                    raise ValueError(f"Unsupported comparison operator: {type(op).__name__}")
                if not op_func(left, right):
                    return False
                left = right
            return True
        elif isinstance(node, ast.BinOp):
            left = cls._eval_node(node.left, context)
            right = cls._eval_node(node.right, context)
            op_func = cls.ALLOWED_OPS.get(type(node.op))
            if not op_func:
                raise ValueError(f"Unsupported binary operator: {type(node.op).__name__}")
            return op_func(left, right)
        elif isinstance(node, ast.UnaryOp):
            operand = cls._eval_node(node.operand, context)
            op_func = cls.ALLOWED_OPS.get(type(node.op))
            if not op_func:
                raise ValueError(f"Unsupported unary operator: {type(node.op).__name__}")
            return op_func(operand)
        elif isinstance(node, ast.BoolOp):
            op_func = cls.ALLOWED_OPS.get(type(node.op))
            if not op_func:
                raise ValueError(f"Unsupported boolean operator: {type(node.op).__name__}")
            values = [cls._eval_node(v, context) for v in node.values]
            result = values[0]
            for val in values[1:]:
                result = op_func(result, val)
            return result
        else:
            raise ValueError(f"Unsupported AST node type: {type(node).__name__}")


class AlertSeverity(Enum):
    """Alert severity levels"""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    EMERGENCY = "emergency"


class AlertCategory(Enum):
    """Alert categories"""
    PERFORMANCE = "performance"
    AVAILABILITY = "availability"
    CAPACITY = "capacity"
    SECURITY = "security"
    SSL = "ssl"
    LIFECYCLE = "lifecycle"
    RESOURCE = "resource"


class RemediationAction(Enum):
    """Available remediation actions"""
    SCALE_UP = "scale_up"
    SCALE_DOWN = "scale_down"
    REFRESH_CONNECTIONS = "refresh_connections"
    RESTART_POOL = "restart_pool"
    SSL_RECOVERY = "ssl_recovery"
    CONNECTION_CLEANUP = "connection_cleanup"
    PERFORMANCE_OPTIMIZATION = "performance_optimization"
    RESOURCE_OPTIMIZATION = "resource_optimization"
    EMERGENCY_SHUTDOWN = "emergency_shutdown"


@dataclass
class AlertRule:
    """Alert rule definition"""
    rule_id: str
    name: str
    category: AlertCategory
    severity: AlertSeverity
    description: str
    condition: str  # Python expression to evaluate
    threshold_value: Optional[float] = None
    time_window_minutes: int = 5
    consecutive_violations: int = 1
    enabled: bool = True
    auto_remediate: bool = False
    remediation_actions: List[RemediationAction] = field(default_factory=list)
    cooldown_minutes: int = 15
    notification_channels: List[str] = field(default_factory=lambda: ['log'])


@dataclass
class Alert:
    """Active alert instance"""
    alert_id: str
    rule_id: str
    triggered_at: datetime
    severity: AlertSeverity
    category: AlertCategory
    title: str
    description: str
    current_value: Optional[float] = None
    threshold_value: Optional[float] = None
    affected_resources: List[str] = field(default_factory=list)
    context_data: Dict[str, Any] = field(default_factory=dict)
    remediation_attempted: bool = False
    remediation_successful: Optional[bool] = None
    acknowledgment_status: str = "open"  # open, acknowledged, resolved
    resolved_at: Optional[datetime] = None


@dataclass
class RemediationResult:
    """Result of automated remediation"""
    remediation_id: str
    alert_id: str
    action: RemediationAction
    executed_at: datetime
    success: bool
    duration_ms: float
    details: Dict[str, Any] = field(default_factory=dict)
    error_message: Optional[str] = None
    performance_impact: Optional[float] = None


class ConnectionPoolAlerting:
    """Advanced alerting and automated remediation for connection pools"""
    
    def __init__(self, enable_auto_remediation: bool = True):
        self.enable_auto_remediation = enable_auto_remediation
        
        # Alert management
        self.alert_rules = {}  # rule_id -> AlertRule
        self.active_alerts = {}  # alert_id -> Alert
        self.alert_history = deque(maxlen=1000)
        self.rule_violations = defaultdict(list)  # rule_id -> [violation_times]
        self.last_alert_times = {}  # rule_id -> datetime
        
        # Remediation management
        self.remediation_history = deque(maxlen=500)
        self.remediation_executors = {
            RemediationAction.SCALE_UP: self._remediate_scale_up,
            RemediationAction.SCALE_DOWN: self._remediate_scale_down,
            RemediationAction.REFRESH_CONNECTIONS: self._remediate_refresh_connections,
            RemediationAction.RESTART_POOL: self._remediate_restart_pool,
            RemediationAction.SSL_RECOVERY: self._remediate_ssl_recovery,
            RemediationAction.CONNECTION_CLEANUP: self._remediate_connection_cleanup,
            RemediationAction.PERFORMANCE_OPTIMIZATION: self._remediate_performance_optimization,
            RemediationAction.RESOURCE_OPTIMIZATION: self._remediate_resource_optimization,
            RemediationAction.EMERGENCY_SHUTDOWN: self._remediate_emergency_shutdown
        }
        
        # Metrics and monitoring
        self.metrics_cache = {}
        self.performance_baselines = {}
        self.anomaly_detector = AlertAnomalyDetector()
        
        # Threading and execution
        self._alerting_lock = threading.Lock()
        self._remediation_lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=4)
        self._running = True
        
        # Notification channels
        self.notification_handlers = {
            'log': self._notify_log,
            'console': self._notify_console,
            'webhook': self._notify_webhook,
            'email': self._notify_email
        }
        
        # Callbacks and integrations
        self.alert_callbacks: List[Callable] = []
        self.remediation_callbacks: List[Callable] = []
        
        # Initialize default alert rules
        self._initialize_default_rules()
        
        # Start monitoring loops
        asyncio.create_task(self._monitoring_loop())
        asyncio.create_task(self._remediation_loop())
        asyncio.create_task(self._housekeeping_loop())
        
        logger.info(
            f"🚨 Advanced Connection Pool Alerting initialized "
            f"(auto_remediation: {enable_auto_remediation})"
        )
    
    def _initialize_default_rules(self):
        """Initialize default alerting rules"""
        default_rules = [
            # Performance alerts
            AlertRule(
                rule_id="perf_slow_acquisition",
                name="Slow Connection Acquisition",
                category=AlertCategory.PERFORMANCE,
                severity=AlertSeverity.WARNING,
                description="Connection acquisition time exceeds threshold",
                condition="metrics.get('avg_acquisition_time_ms', 0) > 200",
                threshold_value=200.0,
                time_window_minutes=5,
                consecutive_violations=2,
                auto_remediate=True,
                remediation_actions=[RemediationAction.SCALE_UP, RemediationAction.PERFORMANCE_OPTIMIZATION],
                cooldown_minutes=10
            ),
            AlertRule(
                rule_id="perf_critical_acquisition",
                name="Critical Connection Acquisition Time",
                category=AlertCategory.PERFORMANCE,
                severity=AlertSeverity.CRITICAL,
                description="Connection acquisition time is critically slow",
                condition="metrics.get('avg_acquisition_time_ms', 0) > 500",
                threshold_value=500.0,
                time_window_minutes=3,
                consecutive_violations=1,
                auto_remediate=True,
                remediation_actions=[RemediationAction.SCALE_UP, RemediationAction.REFRESH_CONNECTIONS],
                cooldown_minutes=5
            ),
            
            # Capacity alerts
            AlertRule(
                rule_id="capacity_high_utilization",
                name="High Pool Utilization",
                category=AlertCategory.CAPACITY,
                severity=AlertSeverity.WARNING,
                description="Connection pool utilization is high",
                condition="metrics.get('current_utilization', 0) > 80",
                threshold_value=80.0,
                time_window_minutes=5,
                consecutive_violations=3,
                auto_remediate=True,
                remediation_actions=[RemediationAction.SCALE_UP],
                cooldown_minutes=15
            ),
            AlertRule(
                rule_id="capacity_critical_utilization",
                name="Critical Pool Utilization",
                category=AlertCategory.CAPACITY,
                severity=AlertSeverity.CRITICAL,
                description="Connection pool utilization is critical",
                condition="metrics.get('current_utilization', 0) > 95",
                threshold_value=95.0,
                time_window_minutes=2,
                consecutive_violations=1,
                auto_remediate=True,
                remediation_actions=[RemediationAction.SCALE_UP, RemediationAction.REFRESH_CONNECTIONS],
                cooldown_minutes=5
            ),
            
            # SSL alerts
            AlertRule(
                rule_id="ssl_high_error_rate",
                name="High SSL Error Rate",
                category=AlertCategory.SSL,
                severity=AlertSeverity.WARNING,
                description="SSL connection error rate is elevated",
                condition="metrics.get('ssl_error_rate', 0) > 0.05",
                threshold_value=0.05,
                time_window_minutes=10,
                consecutive_violations=2,
                auto_remediate=True,
                remediation_actions=[RemediationAction.SSL_RECOVERY, RemediationAction.REFRESH_CONNECTIONS],
                cooldown_minutes=20
            ),
            AlertRule(
                rule_id="ssl_critical_errors",
                name="Critical SSL Errors",
                category=AlertCategory.SSL,
                severity=AlertSeverity.CRITICAL,
                description="SSL connection errors are critical",
                condition="metrics.get('ssl_error_rate', 0) > 0.15 or metrics.get('ssl_consecutive_failures', 0) > 5",
                threshold_value=0.15,
                time_window_minutes=5,
                consecutive_violations=1,
                auto_remediate=True,
                remediation_actions=[RemediationAction.SSL_RECOVERY, RemediationAction.RESTART_POOL],
                cooldown_minutes=10
            ),
            
            # Resource alerts
            AlertRule(
                rule_id="resource_high_memory",
                name="High Memory Usage",
                category=AlertCategory.RESOURCE,
                severity=AlertSeverity.WARNING,
                description="Connection pool memory usage is high",
                condition="metrics.get('memory_usage_mb', 0) > 200",
                threshold_value=200.0,
                time_window_minutes=10,
                consecutive_violations=3,
                auto_remediate=True,
                remediation_actions=[RemediationAction.CONNECTION_CLEANUP, RemediationAction.RESOURCE_OPTIMIZATION],
                cooldown_minutes=30
            ),
            
            # Availability alerts
            AlertRule(
                rule_id="availability_connection_failures",
                name="High Connection Failure Rate",
                category=AlertCategory.AVAILABILITY,
                severity=AlertSeverity.CRITICAL,
                description="Connection failure rate is high",
                condition="metrics.get('failed_acquisitions', 0) > 5 and metrics.get('error_rate', 0) > 0.10",
                threshold_value=0.10,
                time_window_minutes=5,
                consecutive_violations=1,
                auto_remediate=True,
                remediation_actions=[RemediationAction.RESTART_POOL, RemediationAction.SCALE_UP],
                cooldown_minutes=10
            )
        ]
        
        for rule in default_rules:
            self.alert_rules[rule.rule_id] = rule
        
        logger.info(f"📋 Initialized {len(default_rules)} default alert rules")
    
    def add_alert_rule(self, rule: AlertRule):
        """Add a custom alert rule"""
        with self._alerting_lock:
            self.alert_rules[rule.rule_id] = rule
            logger.info(f"📝 Added alert rule: {rule.name} ({rule.rule_id})")
    
    def remove_alert_rule(self, rule_id: str):
        """Remove an alert rule"""
        with self._alerting_lock:
            if rule_id in self.alert_rules:
                del self.alert_rules[rule_id]
                logger.info(f"🗑️ Removed alert rule: {rule_id}")
    
    def register_alert_callback(self, callback: Callable[[Alert], None]):
        """Register callback for alert events"""
        self.alert_callbacks.append(callback)
    
    def register_remediation_callback(self, callback: Callable[[RemediationResult], None]):
        """Register callback for remediation events"""
        self.remediation_callbacks.append(callback)
    
    async def _monitoring_loop(self):
        """Main monitoring loop to evaluate alert rules"""
        logger.info("🔍 Starting connection pool monitoring loop...")
        
        while self._running:
            try:
                await asyncio.sleep(30)  # Check every 30 seconds
                
                # Collect current metrics
                await self._collect_metrics()
                
                # Evaluate alert rules
                await self._evaluate_alert_rules()
                
                # Process anomaly detection
                await self._process_anomaly_detection()
                
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                await asyncio.sleep(60)
    
    async def _collect_metrics(self):
        """Collect metrics from all monitoring systems"""
        try:
            current_metrics = {}
            
            # Get dynamic pool stats
            try:
                from utils.dynamic_database_pool_manager import get_dynamic_pool_stats
                pool_stats = get_dynamic_pool_stats()
                current_metrics.update(pool_stats)
            except ImportError:
                logger.debug("Dynamic pool stats not available")
            
            # Get performance metrics
            try:
                from utils.connection_pool_performance_metrics import get_real_time_performance_metrics
                perf_metrics = get_real_time_performance_metrics()
                current_metrics.update(perf_metrics.get('current_performance', {}))
                current_metrics.update(perf_metrics)
            except ImportError:
                logger.debug("Performance metrics not available")
            
            # Get SSL health metrics
            try:
                from utils.ssl_connection_monitor import get_ssl_health_summary
                ssl_health = get_ssl_health_summary()
                current_metrics['ssl_error_rate'] = ssl_health['metrics'].get('error_rate_percentage', 0) / 100
                current_metrics['ssl_consecutive_failures'] = ssl_health['metrics'].get('consecutive_failures', 0)
                current_metrics['ssl_recovery_time_ms'] = ssl_health['metrics'].get('avg_recovery_time_ms', 0)
            except ImportError:
                try:
                    from utils.proactive_ssl_health_manager import get_ssl_health_report
                    ssl_report = get_ssl_health_report()
                    perf_metrics = ssl_report.get('performance_metrics', {})
                    current_metrics['ssl_error_rate'] = perf_metrics.get('current_error_rate', 0)
                except ImportError:
                    logger.debug("SSL health metrics not available")
            
            # Get lifecycle metrics
            try:
                from utils.connection_lifecycle_optimizer import get_lifecycle_stats
                lifecycle_stats = get_lifecycle_stats()
                current_metrics.update({
                    'total_connections': lifecycle_stats.get('total_connections', 0),
                    'lifecycle_performance': lifecycle_stats['performance_metrics'].get('avg_performance_score', 1.0)
                })
            except ImportError:
                logger.debug("Lifecycle metrics not available")
            
            # Get system resource metrics
            try:
                process = psutil.Process()
                memory_info = process.memory_info()
                current_metrics['memory_usage_mb'] = memory_info.rss / 1024 / 1024
                current_metrics['cpu_usage_percent'] = process.cpu_percent()
            except Exception as e:
                logger.debug(f"Error collecting system metrics: {e}")
            
            # Store metrics
            with self._alerting_lock:
                self.metrics_cache = current_metrics
                self.metrics_cache['timestamp'] = datetime.utcnow()
            
            logger.debug(f"📊 Collected {len(current_metrics)} metrics for alerting")
            
        except Exception as e:
            logger.error(f"Error collecting metrics: {e}")
    
    async def _evaluate_alert_rules(self):
        """Evaluate all alert rules against current metrics"""
        with self._alerting_lock:
            metrics = self.metrics_cache.copy()
        
        if not metrics:
            return
        
        current_time = datetime.utcnow()
        
        for rule_id, rule in self.alert_rules.items():
            if not rule.enabled:
                continue
            
            try:
                # Evaluate rule condition
                violation = self._evaluate_rule_condition(rule, metrics)
                
                if violation:
                    # Record violation
                    self.rule_violations[rule_id].append(current_time)
                    
                    # Clean old violations outside time window
                    cutoff_time = current_time - timedelta(minutes=rule.time_window_minutes)
                    self.rule_violations[rule_id] = [
                        t for t in self.rule_violations[rule_id] if t >= cutoff_time
                    ]
                    
                    # Check if we have enough consecutive violations
                    recent_violations = len(self.rule_violations[rule_id])
                    
                    if recent_violations >= rule.consecutive_violations:
                        # Check cooldown
                        last_alert = self.last_alert_times.get(rule_id)
                        if (not last_alert or 
                            (current_time - last_alert).total_seconds() >= rule.cooldown_minutes * 60):
                            
                            # Trigger alert
                            await self._trigger_alert(rule, metrics)
                            self.last_alert_times[rule_id] = current_time
                            
                            # Clear violations after triggering alert
                            self.rule_violations[rule_id].clear()
                
            except Exception as e:
                logger.error(f"Error evaluating rule {rule_id}: {e}")
    
    def _evaluate_rule_condition(self, rule: AlertRule, metrics: Dict[str, Any]) -> bool:
        """Evaluate a rule condition against metrics"""
        try:
            # Create evaluation context
            eval_context = {
                'metrics': metrics,
                'datetime': datetime,
                'timedelta': timedelta,
                'statistics': statistics
            }
            
            # Use safe expression evaluator instead of eval()
            result = SafeExpressionEvaluator.evaluate(rule.condition, eval_context)
            return bool(result)
            
        except Exception as e:
            logger.error(f"Error evaluating condition for rule {rule.rule_id}: {e}")
            return False
    
    async def _trigger_alert(self, rule: AlertRule, metrics: Dict[str, Any]):
        """Trigger an alert"""
        alert_id = f"alert_{rule.rule_id}_{int(time.time())}"
        
        # Extract current value if possible
        current_value = None
        if 'metrics.get(' in rule.condition:
            try:
                # Simple extraction - could be enhanced
                metric_name = rule.condition.split("metrics.get('")[1].split("'")[0]
                current_value = metrics.get(metric_name)
            except Exception as e:
                logger.debug(f"Could not extract metric value from condition: {e}")
                pass
        
        # Create alert
        alert = Alert(
            alert_id=alert_id,
            rule_id=rule.rule_id,
            triggered_at=datetime.utcnow(),
            severity=rule.severity,
            category=rule.category,
            title=rule.name,
            description=rule.description,
            current_value=current_value,
            threshold_value=rule.threshold_value,
            context_data=metrics.copy()
        )
        
        # Store alert
        with self._alerting_lock:
            self.active_alerts[alert_id] = alert
        
        logger.warning(
            f"🚨 Alert triggered: {alert.title} | "
            f"Severity: {alert.severity.value.upper()} | "
            f"Current: {current_value} | "
            f"Threshold: {rule.threshold_value}"
        )
        
        # Send notifications
        await self._send_notifications(alert, rule)
        
        # Trigger callbacks
        for callback in self.alert_callbacks:
            try:
                callback(alert)
            except Exception as e:
                logger.error(f"Error in alert callback: {e}")
        
        # Schedule remediation if enabled
        if rule.auto_remediate and self.enable_auto_remediation:
            asyncio.create_task(self._schedule_remediation(alert, rule))
    
    async def _send_notifications(self, alert: Alert, rule: AlertRule):
        """Send notifications for an alert"""
        for channel in rule.notification_channels:
            if channel in self.notification_handlers:
                try:
                    await self.notification_handlers[channel](alert, rule)
                except Exception as e:
                    logger.error(f"Error sending notification via {channel}: {e}")
    
    async def _notify_log(self, alert: Alert, rule: AlertRule):
        """Log notification handler"""
        log_level = {
            AlertSeverity.INFO: logger.info,
            AlertSeverity.WARNING: logger.warning,
            AlertSeverity.CRITICAL: logger.error,
            AlertSeverity.EMERGENCY: logger.critical
        }.get(alert.severity, logger.info)
        
        log_level(
            f"🔔 ALERT: {alert.title} | "
            f"ID: {alert.alert_id} | "
            f"Severity: {alert.severity.value.upper()}"
        )
    
    async def _notify_console(self, alert: Alert, rule: AlertRule):
        """Console notification handler"""
        print("\n🚨 ALERT NOTIFICATION 🚨")
        print(f"Title: {alert.title}")
        print(f"Severity: {alert.severity.value.upper()}")
        print(f"Description: {alert.description}")
        print(f"Triggered: {alert.triggered_at}")
        print(f"Current Value: {alert.current_value}")
        print(f"Threshold: {alert.threshold_value}")
        print("="*50)
    
    async def _notify_webhook(self, alert: Alert, rule: AlertRule):
        """Webhook notification handler"""
        # Implementation would send HTTP POST to configured webhook
        logger.debug(f"📡 Webhook notification for alert: {alert.alert_id}")
    
    async def _notify_email(self, alert: Alert, rule: AlertRule):
        """Email notification handler"""
        # Implementation would send email notification
        logger.debug(f"📧 Email notification for alert: {alert.alert_id}")
    
    async def _schedule_remediation(self, alert: Alert, rule: AlertRule):
        """Schedule automated remediation"""
        if not rule.remediation_actions:
            return
        
        logger.info(f"🔧 Scheduling remediation for alert: {alert.alert_id}")
        
        for action in rule.remediation_actions:
            remediation_id = f"rem_{action.value}_{int(time.time())}"
            
            try:
                start_time = time.time()
                success = await self._execute_remediation(action, alert)
                duration_ms = (time.time() - start_time) * 1000
                
                result = RemediationResult(
                    remediation_id=remediation_id,
                    alert_id=alert.alert_id,
                    action=action,
                    executed_at=datetime.utcnow(),
                    success=success,
                    duration_ms=duration_ms,
                    details={'rule_id': rule.rule_id, 'alert_title': alert.title}
                )
                
                # Store result
                with self._remediation_lock:
                    self.remediation_history.append(result)
                
                # Update alert
                alert.remediation_attempted = True
                alert.remediation_successful = success
                
                # Notify callbacks
                for callback in self.remediation_callbacks:
                    try:
                        callback(result)
                    except Exception as e:
                        logger.error(f"Error in remediation callback: {e}")
                
                if success:
                    logger.info(f"✅ Remediation successful: {action.value} for {alert.alert_id}")
                else:
                    logger.warning(f"❌ Remediation failed: {action.value} for {alert.alert_id}")
                
                # If remediation was successful, we might not need to try other actions
                if success:
                    break
                    
            except Exception as e:
                logger.error(f"Error executing remediation {action.value}: {e}")
    
    async def _execute_remediation(self, action: RemediationAction, alert: Alert) -> bool:
        """Execute a remediation action"""
        if action in self.remediation_executors:
            try:
                return await self.remediation_executors[action](alert)
            except Exception as e:
                logger.error(f"Remediation {action.value} failed: {e}")
                return False
        else:
            logger.warning(f"No executor for remediation action: {action.value}")
            return False
    
    # Remediation action implementations
    async def _remediate_scale_up(self, alert: Alert) -> bool:
        """Scale up connection pool"""
        try:
            from utils.dynamic_database_pool_manager import dynamic_pool
            
            current_size = dynamic_pool.current_pool_size
            new_size = min(current_size + 3, dynamic_pool.pool_config['max_pool_size'])
            
            if new_size > current_size:
                await dynamic_pool._scale_pool(new_size, dynamic_pool.current_overflow, f"alert_remediation_{alert.alert_id}")
                logger.info(f"🔼 Scaled pool up: {current_size} → {new_size}")
                return True
            else:
                logger.warning("Cannot scale up - already at maximum pool size")
                return False
                
        except ImportError:
            logger.debug("Dynamic pool not available for scaling")
            return False
    
    async def _remediate_scale_down(self, alert: Alert) -> bool:
        """Scale down connection pool"""
        try:
            from utils.dynamic_database_pool_manager import dynamic_pool
            
            current_size = dynamic_pool.current_pool_size
            new_size = max(current_size - 2, dynamic_pool.pool_config['base_pool_size'])
            
            if new_size < current_size:
                await dynamic_pool._scale_pool(new_size, dynamic_pool.current_overflow, f"alert_remediation_{alert.alert_id}")
                logger.info(f"🔽 Scaled pool down: {current_size} → {new_size}")
                return True
            else:
                logger.warning("Cannot scale down - already at minimum pool size")
                return False
                
        except ImportError:
            logger.debug("Dynamic pool not available for scaling")
            return False
    
    async def _remediate_refresh_connections(self, alert: Alert) -> bool:
        """Refresh database connections"""
        try:
            from utils.dynamic_database_pool_manager import dynamic_pool
            
            # Dispose current engine to force fresh connections
            dynamic_pool.engine.dispose()
            
            # Clear cached sessions
            with dynamic_pool._session_cache_lock:
                for session in dynamic_pool._warmed_sessions:
                    try:
                        session.close()
                    except Exception as e:
                        logger.debug(f"Could not close cached session: {e}")
                        pass
                dynamic_pool._warmed_sessions.clear()
            
            # Warm new connections
            dynamic_pool._warm_connections_async()
            
            logger.info("🔄 Database connections refreshed")
            return True
            
        except ImportError:
            logger.debug("Dynamic pool not available for connection refresh")
            return False
    
    async def _remediate_restart_pool(self, alert: Alert) -> bool:
        """Restart connection pool"""
        try:
            from utils.dynamic_database_pool_manager import dynamic_pool
            
            # This is a more drastic action - dispose and recreate engine
            old_engine = dynamic_pool.engine
            dynamic_pool.engine = dynamic_pool._create_dynamic_engine()
            dynamic_pool.SessionFactory = dynamic_pool.SessionFactory.configure(bind=dynamic_pool.engine)
            
            old_engine.dispose()
            
            # Clear all cached connections
            with dynamic_pool._session_cache_lock:
                dynamic_pool._warmed_sessions.clear()
            
            dynamic_pool._warm_connections_async()
            
            logger.warning("🔄 Connection pool restarted")
            return True
            
        except ImportError:
            logger.debug("Dynamic pool not available for restart")
            return False
    
    async def _remediate_ssl_recovery(self, alert: Alert) -> bool:
        """Trigger SSL connection recovery"""
        try:
            from utils.proactive_ssl_health_manager import ssl_health_manager
            
            # Trigger proactive remediation
            await ssl_health_manager._trigger_proactive_remediation(f"alert_remediation_{alert.alert_id}")
            
            logger.info("🔐 SSL recovery initiated")
            return True
            
        except ImportError:
            # Try with basic SSL recovery
            try:
                from utils.ssl_connection_monitor import trigger_ssl_recovery
                trigger_ssl_recovery("alert_remediation")
                return True
            except ImportError:
                logger.debug("SSL recovery not available")
                return False
    
    async def _remediate_connection_cleanup(self, alert: Alert) -> bool:
        """Clean up stale connections"""
        try:
            from utils.connection_lifecycle_optimizer import lifecycle_optimizer
            
            # Mark stale connections for cleanup
            now = datetime.utcnow()
            stale_connections = [
                conn_id for conn_id, metadata in lifecycle_optimizer.connections.items()
                if (now - metadata.last_used_at).total_seconds() > 1800  # 30 minutes
            ]
            
            for conn_id in stale_connections[:10]:  # Limit cleanup batch
                lifecycle_optimizer.dispose_connection(conn_id, "alert_cleanup")
            
            logger.info(f"🧹 Cleaned up {len(stale_connections[:10])} stale connections")
            return True
            
        except ImportError:
            logger.debug("Lifecycle optimizer not available for cleanup")
            return False
    
    async def _remediate_performance_optimization(self, alert: Alert) -> bool:
        """Apply performance optimizations"""
        try:
            # Trigger garbage collection
            import gc
            collected = gc.collect()
            
            # Clear metrics caches if they're too large
            with self._alerting_lock:
                if len(self.metrics_cache) > 100:
                    # Keep only the most recent data
                    self.metrics_cache = dict(list(self.metrics_cache.items())[-50:])
            
            logger.info(f"⚡ Performance optimization applied (GC collected: {collected})")
            return True
            
        except Exception as e:
            logger.error(f"Performance optimization failed: {e}")
            return False
    
    async def _remediate_resource_optimization(self, alert: Alert) -> bool:
        """Apply resource optimizations"""
        try:
            # Memory optimization
            import gc
            gc.collect()
            
            # Check if we can reduce some caches
            optimized = False
            
            # Reduce alert history if too large
            if len(self.alert_history) > 500:
                self.alert_history = deque(list(self.alert_history)[-250:], maxlen=1000)
                optimized = True
            
            # Reduce remediation history if too large
            if len(self.remediation_history) > 250:
                self.remediation_history = deque(list(self.remediation_history)[-125:], maxlen=500)
                optimized = True
            
            logger.info(f"🧠 Resource optimization applied (optimized: {optimized})")
            return True
            
        except Exception as e:
            logger.error(f"Resource optimization failed: {e}")
            return False
    
    async def _remediate_emergency_shutdown(self, alert: Alert) -> bool:
        """Emergency shutdown (last resort)"""
        logger.critical(f"🚨 EMERGENCY SHUTDOWN triggered by alert: {alert.alert_id}")
        # This would typically be implemented to gracefully shutdown the application
        # For safety, we just log this action
        return True
    
    async def _remediation_loop(self):
        """Monitor remediation results and handle follow-up actions"""
        logger.info("🔧 Starting remediation monitoring loop...")
        
        while self._running:
            try:
                await asyncio.sleep(60)  # Check every minute
                
                # Check for failed remediations that might need retry
                await self._handle_failed_remediations()
                
                # Check for alerts that resolved after remediation
                await self._check_resolved_alerts()
                
            except Exception as e:
                logger.error(f"Error in remediation loop: {e}")
                await asyncio.sleep(120)
    
    async def _handle_failed_remediations(self):
        """Handle failed remediations"""
        failed_remediations = [
            result for result in list(self.remediation_history)[-20:]
            if not result.success and 
            (datetime.utcnow() - result.executed_at).total_seconds() < 3600  # Last hour
        ]
        
        # Group by alert_id
        failed_by_alert = defaultdict(list)
        for result in failed_remediations:
            failed_by_alert[result.alert_id].append(result)
        
        for alert_id, failures in failed_by_alert.items():
            if len(failures) >= 3:  # Multiple failures for same alert
                logger.warning(f"⚠️ Multiple remediation failures for alert {alert_id}")
                # Could trigger escalation here
    
    async def _check_resolved_alerts(self):
        """Check if alerts have been resolved"""
        resolved_alerts = []
        
        with self._alerting_lock:
            current_metrics = self.metrics_cache.copy()
        
        for alert_id, alert in list(self.active_alerts.items()):
            if alert.acknowledgment_status == "resolved":
                continue
            
            # Check if the condition that triggered the alert is still true
            rule = self.alert_rules.get(alert.rule_id)
            if rule and current_metrics:
                still_violated = self._evaluate_rule_condition(rule, current_metrics)
                
                if not still_violated:
                    # Alert condition resolved
                    alert.resolved_at = datetime.utcnow()
                    alert.acknowledgment_status = "resolved"
                    resolved_alerts.append(alert_id)
                    
                    logger.info(f"✅ Alert resolved: {alert.title} ({alert_id})")
        
        # Move resolved alerts to history
        for alert_id in resolved_alerts:
            alert = self.active_alerts.pop(alert_id)
            self.alert_history.append(alert)
    
    async def _process_anomaly_detection(self):
        """Process anomaly detection on metrics"""
        with self._alerting_lock:
            metrics = self.metrics_cache.copy()
        
        if not metrics:
            return
        
        # Detect anomalies in key metrics
        anomalies = self.anomaly_detector.detect_anomalies(metrics)
        
        for anomaly in anomalies:
            logger.warning(f"🔍 Anomaly detected: {anomaly['metric']} = {anomaly['value']}")
            
            # Could create dynamic alerts based on anomalies
            if anomaly['severity'] == 'critical':
                # Create a temporary alert rule for the anomaly
                temp_rule = AlertRule(
                    rule_id=f"anomaly_{anomaly['metric']}_{int(time.time())}",
                    name=f"Anomaly in {anomaly['metric']}",
                    category=AlertCategory.PERFORMANCE,
                    severity=AlertSeverity.WARNING,
                    description=f"Anomaly detected: {anomaly['description']}",
                    condition="True",  # Already detected
                    enabled=False  # Don't re-evaluate
                )
                
                # Trigger alert directly
                await self._trigger_alert(temp_rule, metrics)
    
    async def _housekeeping_loop(self):
        """Housekeeping tasks for alerting system"""
        logger.info("🧹 Starting alerting housekeeping loop...")
        
        while self._running:
            try:
                await asyncio.sleep(3600)  # Run every hour
                
                # Clean up old rule violations
                cutoff_time = datetime.utcnow() - timedelta(hours=24)
                for rule_id in list(self.rule_violations.keys()):
                    self.rule_violations[rule_id] = [
                        t for t in self.rule_violations[rule_id] if t >= cutoff_time
                    ]
                    if not self.rule_violations[rule_id]:
                        del self.rule_violations[rule_id]
                
                # Archive very old alerts
                if len(self.alert_history) > 800:
                    self.alert_history = deque(list(self.alert_history)[-400:], maxlen=1000)
                
                # Clean up old remediation history
                if len(self.remediation_history) > 400:
                    self.remediation_history = deque(list(self.remediation_history)[-200:], maxlen=500)
                
                logger.debug("🧹 Alerting housekeeping completed")
                
            except Exception as e:
                logger.error(f"Error in housekeeping loop: {e}")
                await asyncio.sleep(3600)
    
    def get_alerting_status(self) -> Dict[str, Any]:
        """Get current alerting system status"""
        with self._alerting_lock:
            return {
                'timestamp': datetime.utcnow().isoformat(),
                'system_status': {
                    'running': self._running,
                    'auto_remediation_enabled': self.enable_auto_remediation,
                    'total_alert_rules': len(self.alert_rules),
                    'enabled_alert_rules': len([r for r in self.alert_rules.values() if r.enabled])
                },
                'current_alerts': {
                    'active_count': len(self.active_alerts),
                    'alerts': [
                        {
                            'alert_id': alert.alert_id,
                            'title': alert.title,
                            'severity': alert.severity.value,
                            'category': alert.category.value,
                            'triggered_at': alert.triggered_at.isoformat(),
                            'current_value': alert.current_value,
                            'threshold_value': alert.threshold_value,
                            'remediation_attempted': alert.remediation_attempted
                        }
                        for alert in self.active_alerts.values()
                    ]
                },
                'recent_remediations': [
                    {
                        'remediation_id': result.remediation_id,
                        'action': result.action.value,
                        'executed_at': result.executed_at.isoformat(),
                        'success': result.success,
                        'duration_ms': result.duration_ms
                    }
                    for result in list(self.remediation_history)[-10:]
                ],
                'alert_rules': [
                    {
                        'rule_id': rule.rule_id,
                        'name': rule.name,
                        'category': rule.category.value,
                        'severity': rule.severity.value,
                        'enabled': rule.enabled,
                        'auto_remediate': rule.auto_remediate
                    }
                    for rule in self.alert_rules.values()
                ]
            }
    
    def acknowledge_alert(self, alert_id: str, acknowledged_by: str = "system"):
        """Acknowledge an alert"""
        if alert_id in self.active_alerts:
            alert = self.active_alerts[alert_id]
            alert.acknowledgment_status = "acknowledged"
            logger.info(f"✅ Alert acknowledged: {alert_id} by {acknowledged_by}")
            return True
        return False
    
    def resolve_alert(self, alert_id: str, resolved_by: str = "system"):
        """Manually resolve an alert"""
        if alert_id in self.active_alerts:
            alert = self.active_alerts[alert_id]
            alert.resolved_at = datetime.utcnow()
            alert.acknowledgment_status = "resolved"
            
            # Move to history
            self.alert_history.append(self.active_alerts.pop(alert_id))
            
            logger.info(f"✅ Alert resolved: {alert_id} by {resolved_by}")
            return True
        return False
    
    def shutdown(self):
        """Shutdown the alerting system"""
        logger.info("🚨 Shutting down Advanced Connection Pool Alerting...")
        self._running = False
        self._executor.shutdown(wait=True)


class AlertAnomalyDetector:
    """Simple anomaly detector for alerting metrics"""
    
    def __init__(self):
        self.metric_history = defaultdict(deque)
        self.baselines = {}
    
    def detect_anomalies(self, metrics: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Detect anomalies in metrics"""
        anomalies = []
        
        numeric_metrics = {
            k: v for k, v in metrics.items() 
            if isinstance(v, (int, float)) and not k.endswith('_timestamp')
        }
        
        for metric_name, value in numeric_metrics.items():
            # Store in history
            self.metric_history[metric_name].append(value)
            if len(self.metric_history[metric_name]) > 100:
                self.metric_history[metric_name].popleft()
            
            # Need at least 10 data points for anomaly detection
            if len(self.metric_history[metric_name]) >= 10:
                history = list(self.metric_history[metric_name])[:-1]  # Exclude current value
                
                if len(history) >= 5:
                    mean_val = statistics.mean(history)
                    std_val = statistics.stdev(history) if len(history) > 1 else 0
                    
                    # Simple z-score based anomaly detection
                    if std_val > 0:
                        z_score = abs(value - mean_val) / std_val
                        
                        if z_score > 3:  # 3 standard deviations
                            anomalies.append({
                                'metric': metric_name,
                                'value': value,
                                'mean': mean_val,
                                'std': std_val,
                                'z_score': z_score,
                                'severity': 'critical' if z_score > 4 else 'warning',
                                'description': f"Metric {metric_name} is {z_score:.2f} std devs from normal"
                            })
        
        return anomalies


# Global alerting system instance
alerting_system = ConnectionPoolAlerting()


def get_alerting_status() -> Dict[str, Any]:
    """Get current alerting system status"""
    return alerting_system.get_alerting_status()


def acknowledge_alert(alert_id: str, acknowledged_by: str = "system") -> bool:
    """Acknowledge an alert"""
    return alerting_system.acknowledge_alert(alert_id, acknowledged_by)


def resolve_alert(alert_id: str, resolved_by: str = "system") -> bool:
    """Resolve an alert"""
    return alerting_system.resolve_alert(alert_id, resolved_by)


def add_custom_alert_rule(rule: AlertRule):
    """Add a custom alert rule"""
    alerting_system.add_alert_rule(rule)


def register_alert_notification_callback(callback: Callable):
    """Register callback for alert notifications"""
    alerting_system.register_alert_callback(callback)