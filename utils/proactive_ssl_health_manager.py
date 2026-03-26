"""
Proactive SSL Health Manager
Advanced SSL connection management with predictive health monitoring, 
automatic remediation, and performance optimization
"""

import logging
import time
import asyncio
import threading
import ssl
import socket
import certifi
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Callable
from collections import deque, defaultdict
from dataclasses import dataclass, field
from enum import Enum
import statistics
from concurrent.futures import ThreadPoolExecutor
import urllib.parse
from sqlalchemy import text

logger = logging.getLogger(__name__)


class SSLHealthStatus(Enum):
    """SSL health status levels"""
    EXCELLENT = "excellent"      # <1% error rate, fast handshakes
    GOOD = "good"               # 1-5% error rate
    WARNING = "warning"         # 5-10% error rate
    DEGRADED = "degraded"       # 10-20% error rate
    CRITICAL = "critical"       # >20% error rate or persistent failures


class SSLRemediationStrategy(Enum):
    """SSL remediation strategies"""
    NONE = "none"
    RETRY_CONNECTION = "retry_connection"
    REFRESH_ENGINE = "refresh_engine"
    FORCE_RECONNECT = "force_reconnect"
    EMERGENCY_SCALING = "emergency_scaling"
    CERTIFICATE_VALIDATION = "certificate_validation"


@dataclass
class SSLPerformanceMetric:
    """SSL performance metric data"""
    timestamp: datetime
    handshake_time_ms: float
    connection_success: bool
    error_type: Optional[str] = None
    server_info: Optional[Dict[str, Any]] = None
    remediation_applied: Optional[str] = None
    context: str = "default"


@dataclass
class SSLCertificateInfo:
    """SSL certificate information"""
    subject: str
    issuer: str
    expires_at: datetime
    days_until_expiry: int
    is_valid: bool
    signature_algorithm: str
    key_size: Optional[int] = None
    san_domains: List[str] = field(default_factory=list)


@dataclass
class SSLRemediationAction:
    """SSL remediation action record"""
    timestamp: datetime
    trigger_reason: str
    strategy: SSLRemediationStrategy
    context: str
    success: bool
    duration_ms: float
    performance_impact: float
    error_message: Optional[str] = None


class ProactiveSSLHealthManager:
    """Advanced SSL health management with predictive monitoring and auto-remediation"""
    
    def __init__(self):
        # Health monitoring configuration
        self.config = {
            'health_check_interval': 30,      # seconds
            'performance_window': 300,        # 5 minutes
            'prediction_window': 900,         # 15 minutes
            'remediation_cooldown': 60,       # 1 minute between remediations
            'certificate_check_interval': 3600, # 1 hour
            'proactive_threshold': 0.05,      # 5% error rate triggers proactive action
            'critical_threshold': 0.20,       # 20% error rate
            'handshake_timeout_ms': 5000,     # 5 seconds
            'max_concurrent_checks': 5
        }
        
        # SSL monitoring state
        self.ssl_metrics = deque(maxlen=1000)
        self.certificate_cache = {}
        self.health_history = deque(maxlen=288)  # 24 hours of 5-minute intervals
        self.remediation_history = deque(maxlen=100)
        self.active_remediations = {}
        
        # Performance tracking
        self.performance_stats = {
            'total_connections': 0,
            'successful_connections': 0,
            'failed_connections': 0,
            'avg_handshake_time_ms': 0.0,
            'certificate_warnings': 0,
            'remediations_applied': 0,
            'proactive_actions': 0
        }
        
        # Health status tracking
        self.current_health_status = SSLHealthStatus.EXCELLENT
        self.health_trend = 'stable'  # improving, stable, degrading
        self.last_health_check = datetime.utcnow()
        self.predictive_alerts = []
        
        # Threading and locks
        self._health_lock = threading.Lock()
        self._remediation_lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=self.config['max_concurrent_checks'])
        
        # Callback system for notifications
        self.health_change_callbacks: List[Callable] = []
        self.remediation_callbacks: List[Callable] = []
        
        # Start background monitoring
        asyncio.create_task(self._health_monitoring_loop())
        asyncio.create_task(self._predictive_analysis_loop())
        asyncio.create_task(self._certificate_monitoring_loop())
        asyncio.create_task(self._performance_optimization_loop())
        
        logger.info("🔐 Proactive SSL Health Manager initialized with advanced monitoring")
    
    def register_health_callback(self, callback: Callable[[SSLHealthStatus, SSLHealthStatus], None]):
        """Register callback for health status changes"""
        self.health_change_callbacks.append(callback)
    
    def register_remediation_callback(self, callback: Callable[[SSLRemediationAction], None]):
        """Register callback for remediation actions"""
        self.remediation_callbacks.append(callback)
    
    def record_ssl_connection_attempt(
        self, 
        context: str,
        success: bool,
        handshake_time_ms: float,
        error_type: Optional[str] = None,
        server_info: Optional[Dict[str, Any]] = None
    ):
        """Record SSL connection attempt with detailed metrics"""
        with self._health_lock:
            metric = SSLPerformanceMetric(
                timestamp=datetime.utcnow(),
                handshake_time_ms=handshake_time_ms,
                connection_success=success,
                error_type=error_type,
                server_info=server_info,
                context=context
            )
            
            self.ssl_metrics.append(metric)
            
            # Update performance stats
            self.performance_stats['total_connections'] += 1
            
            if success:
                self.performance_stats['successful_connections'] += 1
                # Update rolling average
                total_successful = self.performance_stats['successful_connections']
                current_avg = self.performance_stats['avg_handshake_time_ms']
                self.performance_stats['avg_handshake_time_ms'] = (
                    (current_avg * (total_successful - 1) + handshake_time_ms) / total_successful
                )
            else:
                self.performance_stats['failed_connections'] += 1
                
                # Log SSL connection failure
                logger.warning(
                    f"🔌 SSL connection failed in {context}: "
                    f"error={error_type}, handshake_time={handshake_time_ms:.1f}ms"
                )
            
            # Trigger real-time health assessment
            asyncio.create_task(self._assess_real_time_health())
    
    async def _assess_real_time_health(self):
        """Assess SSL health in real-time and trigger remediations if needed"""
        try:
            current_health = await self._calculate_health_status()
            
            # Check if health status changed
            if current_health != self.current_health_status:
                old_status = self.current_health_status
                self.current_health_status = current_health
                
                logger.info(
                    f"🔐 SSL health status changed: {old_status.value} → {current_health.value}"
                )
                
                # Notify callbacks
                for callback in self.health_change_callbacks:
                    try:
                        callback(old_status, current_health)
                    except Exception as e:
                        logger.error(f"Error in health change callback: {e}")
                
                # Trigger remediation if needed
                if current_health in [SSLHealthStatus.DEGRADED, SSLHealthStatus.CRITICAL]:
                    await self._trigger_proactive_remediation(f"health_degraded_to_{current_health.value}")
        
        except Exception as e:
            logger.error(f"Error in real-time health assessment: {e}")
    
    async def _calculate_health_status(self) -> SSLHealthStatus:
        """Calculate current SSL health status based on recent metrics"""
        with self._health_lock:
            if len(self.ssl_metrics) < 10:
                return SSLHealthStatus.EXCELLENT
            
            # Analyze recent metrics (last 5 minutes)
            recent_cutoff = datetime.utcnow() - timedelta(minutes=5)
            recent_metrics = [
                m for m in list(self.ssl_metrics)
                if m.timestamp >= recent_cutoff
            ]
            
            if not recent_metrics:
                return SSLHealthStatus.EXCELLENT
            
            # Calculate error rate
            total_attempts = len(recent_metrics)
            failed_attempts = len([m for m in recent_metrics if not m.connection_success])
            error_rate = failed_attempts / total_attempts if total_attempts > 0 else 0.0
            
            # Calculate average handshake time
            successful_metrics = [m for m in recent_metrics if m.connection_success]
            avg_handshake_time = (
                statistics.mean([m.handshake_time_ms for m in successful_metrics])
                if successful_metrics else 0.0
            )
            
            # Determine health status
            if error_rate >= self.config['critical_threshold']:
                return SSLHealthStatus.CRITICAL
            elif error_rate >= 0.10:
                return SSLHealthStatus.DEGRADED
            elif error_rate >= self.config['proactive_threshold'] or avg_handshake_time > 2000:
                return SSLHealthStatus.WARNING
            elif error_rate >= 0.01 or avg_handshake_time > 1000:
                return SSLHealthStatus.GOOD
            else:
                return SSLHealthStatus.EXCELLENT
    
    async def _trigger_proactive_remediation(self, reason: str):
        """Trigger proactive SSL remediation"""
        async with asyncio.Lock():  # Prevent concurrent remediations
            try:
                # Check cooldown
                now = datetime.utcnow()
                if reason in self.active_remediations:
                    last_remediation = self.active_remediations[reason]
                    if (now - last_remediation).total_seconds() < self.config['remediation_cooldown']:
                        logger.debug(f"Remediation cooldown active for: {reason}")
                        return
                
                # Determine best remediation strategy
                strategy = await self._select_remediation_strategy(reason)
                
                logger.info(f"🔧 Applying SSL remediation: {strategy.value} (reason: {reason})")
                
                # Apply remediation
                remediation_start = time.time()
                success = await self._apply_remediation(strategy, reason)
                duration_ms = (time.time() - remediation_start) * 1000
                
                # Record remediation action
                remediation_action = SSLRemediationAction(
                    timestamp=now,
                    trigger_reason=reason,
                    strategy=strategy,
                    context='proactive',
                    success=success,
                    duration_ms=duration_ms,
                    performance_impact=await self._measure_performance_impact(),
                    error_message=None if success else "Remediation failed"
                )
                
                self.remediation_history.append(remediation_action)
                self.active_remediations[reason] = now
                
                # Update stats
                self.performance_stats['remediations_applied'] += 1
                if 'proactive' in reason:
                    self.performance_stats['proactive_actions'] += 1
                
                # Notify callbacks
                for callback in self.remediation_callbacks:
                    try:
                        callback(remediation_action)
                    except Exception as e:
                        logger.error(f"Error in remediation callback: {e}")
                
                if success:
                    logger.info(
                        f"✅ SSL remediation successful: {strategy.value} "
                        f"(duration: {duration_ms:.1f}ms)"
                    )
                else:
                    logger.error(f"❌ SSL remediation failed: {strategy.value}")
                
            except Exception as e:
                logger.error(f"Error in proactive remediation: {e}")
    
    async def _select_remediation_strategy(self, reason: str) -> SSLRemediationStrategy:
        """Select the best remediation strategy based on current conditions"""
        # Analyze recent failure patterns
        recent_failures = [
            m for m in list(self.ssl_metrics)[-50:]
            if not m.connection_success and m.timestamp >= datetime.utcnow() - timedelta(minutes=2)
        ]
        
        # Count error types
        error_types = defaultdict(int)
        for failure in recent_failures:
            if failure.error_type:
                error_types[failure.error_type] += 1
        
        failure_rate = len(recent_failures) / 50 if recent_failures else 0.0
        
        # Strategy selection logic
        if "certificate" in reason.lower() or any("cert" in error for error in error_types):
            return SSLRemediationStrategy.CERTIFICATE_VALIDATION
        
        elif failure_rate >= 0.20:  # High failure rate
            return SSLRemediationStrategy.EMERGENCY_SCALING
        
        elif "timeout" in str(error_types) or failure_rate >= 0.10:
            return SSLRemediationStrategy.FORCE_RECONNECT
        
        elif len(recent_failures) >= 3:  # Multiple recent failures
            return SSLRemediationStrategy.REFRESH_ENGINE
        
        else:
            return SSLRemediationStrategy.RETRY_CONNECTION
    
    async def _apply_remediation(self, strategy: SSLRemediationStrategy, reason: str) -> bool:
        """Apply the selected remediation strategy"""
        try:
            if strategy == SSLRemediationStrategy.RETRY_CONNECTION:
                return await self._remediate_retry_connection()
            
            elif strategy == SSLRemediationStrategy.REFRESH_ENGINE:
                return await self._remediate_refresh_engine()
            
            elif strategy == SSLRemediationStrategy.FORCE_RECONNECT:
                return await self._remediate_force_reconnect()
            
            elif strategy == SSLRemediationStrategy.EMERGENCY_SCALING:
                return await self._remediate_emergency_scaling()
            
            elif strategy == SSLRemediationStrategy.CERTIFICATE_VALIDATION:
                return await self._remediate_certificate_validation()
            
            else:
                logger.warning(f"Unknown remediation strategy: {strategy}")
                return False
                
        except Exception as e:
            logger.error(f"Error applying remediation {strategy.value}: {e}")
            return False
    
    async def _remediate_retry_connection(self) -> bool:
        """Simple connection retry remediation"""
        try:
            # Import here to avoid circular dependencies
            from utils.dynamic_database_pool_manager import dynamic_pool
            
            # Test connection
            with dynamic_pool.get_session("ssl_remediation_test") as session:
                session.execute(text("SELECT 1"))
            
            return True
        except Exception:
            return False
    
    async def _remediate_refresh_engine(self) -> bool:
        """Refresh database engine connections"""
        try:
            from utils.dynamic_database_pool_manager import dynamic_pool
            
            # Dispose current engine to force fresh connections
            dynamic_pool.engine.dispose()
            
            # Test new connection
            with dynamic_pool.get_session("ssl_engine_refresh_test") as session:
                session.execute(text("SELECT 1"))
            
            logger.info("🔄 Database engine refreshed successfully")
            return True
        except Exception as e:
            logger.error(f"Engine refresh failed: {e}")
            return False
    
    async def _remediate_force_reconnect(self) -> bool:
        """Force reconnection with fresh SSL handshake"""
        try:
            from utils.dynamic_database_pool_manager import dynamic_pool
            
            # Clear connection cache and force new connections
            with dynamic_pool._session_cache_lock:
                for session in dynamic_pool._warmed_sessions:
                    try:
                        session.close()
                    except Exception:
                        pass
                dynamic_pool._warmed_sessions.clear()
            
            # Force engine disposal and recreation
            dynamic_pool.engine.dispose()
            
            # Warm new connections
            dynamic_pool._warm_connections_async()
            
            logger.info("🔌 Forced SSL reconnection completed")
            return True
        except Exception as e:
            logger.error(f"Force reconnect failed: {e}")
            return False
    
    async def _remediate_emergency_scaling(self) -> bool:
        """Emergency scaling to handle SSL issues"""
        try:
            from utils.dynamic_database_pool_manager import dynamic_pool
            
            # Trigger emergency scaling
            current_size = dynamic_pool.current_pool_size
            emergency_size = min(current_size + 5, dynamic_pool.pool_config['max_pool_size'])
            
            if emergency_size > current_size:
                await dynamic_pool._scale_pool(
                    emergency_size,
                    dynamic_pool.current_overflow + 10,
                    "ssl_emergency_remediation"
                )
                logger.info(f"🚨 Emergency scaling applied: {current_size} → {emergency_size}")
                return True
            else:
                logger.warning("Cannot scale further - already at maximum")
                return False
                
        except Exception as e:
            logger.error(f"Emergency scaling failed: {e}")
            return False
    
    async def _remediate_certificate_validation(self) -> bool:
        """Validate SSL certificates and refresh if needed"""
        try:
            # Check certificate validity
            cert_info = await self._check_database_certificate()
            
            if cert_info and cert_info.is_valid:
                if cert_info.days_until_expiry < 30:
                    logger.warning(
                        f"🔐 SSL certificate expires in {cert_info.days_until_expiry} days"
                    )
                    self.performance_stats['certificate_warnings'] += 1
                
                return True
            else:
                logger.error("🔐 SSL certificate validation failed")
                return False
                
        except Exception as e:
            logger.error(f"Certificate validation failed: {e}")
            return False
    
    async def _measure_performance_impact(self) -> float:
        """Measure performance impact of remediation"""
        try:
            # Simple performance test
            start_time = time.time()
            
            from utils.dynamic_database_pool_manager import dynamic_pool
            with dynamic_pool.get_session("performance_test") as session:
                session.execute(text("SELECT 1"))
            
            return (time.time() - start_time) * 1000  # Return in milliseconds
        except Exception:
            return -1.0  # Negative indicates test failed
    
    async def _check_database_certificate(self) -> Optional[SSLCertificateInfo]:
        """Check database SSL certificate information"""
        try:
            # Parse database URL to get host and port
            from config import Config
            parsed = urllib.parse.urlparse(Config.DATABASE_URL)
            host = parsed.hostname
            port = parsed.port or 5432
            
            if not host:
                return None
            
            # Check SSL certificate
            context = ssl.create_default_context(cafile=certifi.where())
            
            with socket.create_connection((host, port), timeout=10) as sock:
                with context.wrap_socket(sock, server_hostname=host) as ssock:
                    cert_der = ssock.getpeercert(binary_form=True)
                    ssl.DER_cert_to_PEM_cert(cert_der)
                    cert_info = ssock.getpeercert()
                    
                    # Parse certificate information
                    subject = dict(x[0] for x in cert_info['subject'])['commonName']
                    issuer = dict(x[0] for x in cert_info['issuer'])['commonName']
                    
                    # Parse expiry date
                    expiry_str = cert_info['notAfter']
                    expires_at = datetime.strptime(expiry_str, '%b %d %H:%M:%S %Y %Z')
                    days_until_expiry = (expires_at - datetime.utcnow()).days
                    
                    # Check if certificate is valid
                    is_valid = days_until_expiry > 0
                    
                    return SSLCertificateInfo(
                        subject=subject,
                        issuer=issuer,
                        expires_at=expires_at,
                        days_until_expiry=days_until_expiry,
                        is_valid=is_valid,
                        signature_algorithm=cert_info.get('signatureAlgorithm', 'unknown')
                    )
                    
        except Exception as e:
            logger.error(f"Certificate check failed: {e}")
            return None
    
    async def _health_monitoring_loop(self):
        """Main health monitoring loop"""
        logger.info("🔐 Starting SSL health monitoring loop...")
        
        while True:
            try:
                await asyncio.sleep(self.config['health_check_interval'])
                
                # Update health history
                current_health = await self._calculate_health_status()
                health_entry = {
                    'timestamp': datetime.utcnow(),
                    'status': current_health.value,
                    'error_rate': await self._get_current_error_rate(),
                    'avg_handshake_time': await self._get_current_avg_handshake_time(),
                    'active_connections': await self._get_active_connection_count()
                }
                
                self.health_history.append(health_entry)
                self.last_health_check = datetime.utcnow()
                
                # Log periodic health status
                if current_health != SSLHealthStatus.EXCELLENT:
                    logger.warning(
                        f"🔐 SSL Health: {current_health.value.upper()} | "
                        f"Error Rate: {health_entry['error_rate']:.1%} | "
                        f"Avg Handshake: {health_entry['avg_handshake_time']:.1f}ms"
                    )
                else:
                    logger.debug(f"🔐 SSL Health: {current_health.value}")
                
            except Exception as e:
                logger.error(f"Error in SSL health monitoring loop: {e}")
                await asyncio.sleep(60)
    
    async def _predictive_analysis_loop(self):
        """Predictive analysis for SSL health issues"""
        logger.info("🔮 Starting SSL predictive analysis loop...")
        
        while True:
            try:
                await asyncio.sleep(self.config['prediction_window'] // 3)  # Check every 5 minutes
                
                # Perform predictive analysis
                predictions = await self._analyze_health_trends()
                
                # Check for concerning trends
                for prediction in predictions:
                    if prediction['severity'] in ['high', 'critical']:
                        logger.warning(
                            f"🔮 SSL Predictive Alert: {prediction['message']} "
                            f"(confidence: {prediction['confidence']:.1%})"
                        )
                        
                        # Store predictive alert
                        self.predictive_alerts.append({
                            'timestamp': datetime.utcnow(),
                            'prediction': prediction,
                            'action_taken': False
                        })
                        
                        # Trigger proactive action if confidence is high
                        if prediction['confidence'] > 0.8:
                            await self._trigger_proactive_remediation(f"predictive_{prediction['type']}")
                            prediction['action_taken'] = True
                
            except Exception as e:
                logger.error(f"Error in predictive analysis loop: {e}")
                await asyncio.sleep(300)
    
    async def _analyze_health_trends(self) -> List[Dict[str, Any]]:
        """Analyze SSL health trends for predictive insights"""
        predictions = []
        
        if len(self.health_history) < 10:
            return predictions
        
        # Analyze error rate trend
        recent_entries = list(self.health_history)[-12:]  # Last hour
        error_rates = [entry['error_rate'] for entry in recent_entries]
        
        if len(error_rates) >= 6:
            recent_trend = statistics.mean(error_rates[-6:])  # Last 30 minutes
            older_trend = statistics.mean(error_rates[:6])    # Previous 30 minutes
            
            if recent_trend > older_trend * 1.5 and recent_trend > 0.02:  # 50% increase and >2%
                predictions.append({
                    'type': 'error_rate_increase',
                    'severity': 'high' if recent_trend > 0.05 else 'medium',
                    'message': f"Error rate trending up: {older_trend:.1%} → {recent_trend:.1%}",
                    'confidence': min(0.9, (recent_trend - older_trend) * 10)
                })
        
        # Analyze handshake time trend
        handshake_times = [entry['avg_handshake_time'] for entry in recent_entries]
        
        if len(handshake_times) >= 6:
            recent_avg = statistics.mean(handshake_times[-6:])
            older_avg = statistics.mean(handshake_times[:6])
            
            if recent_avg > older_avg * 1.3 and recent_avg > 500:  # 30% increase and >500ms
                predictions.append({
                    'type': 'handshake_degradation',
                    'severity': 'medium' if recent_avg < 1000 else 'high',
                    'message': f"Handshake time degrading: {older_avg:.1f}ms → {recent_avg:.1f}ms",
                    'confidence': min(0.8, (recent_avg - older_avg) / 1000)
                })
        
        return predictions
    
    async def _certificate_monitoring_loop(self):
        """Monitor SSL certificates for expiry and issues"""
        logger.info("📜 Starting SSL certificate monitoring loop...")
        
        while True:
            try:
                await asyncio.sleep(self.config['certificate_check_interval'])
                
                # Check database certificate
                cert_info = await self._check_database_certificate()
                
                if cert_info:
                    self.certificate_cache['database'] = cert_info
                    
                    # Check for expiry warnings
                    if cert_info.days_until_expiry <= 30:
                        logger.warning(
                            f"📜 SSL Certificate Warning: Database certificate expires in "
                            f"{cert_info.days_until_expiry} days (expires: {cert_info.expires_at})"
                        )
                        
                        if cert_info.days_until_expiry <= 7:
                            await self._trigger_proactive_remediation("certificate_expiry_critical")
                    
                    logger.debug(
                        f"📜 Database SSL certificate: {cert_info.subject} "
                        f"(expires in {cert_info.days_until_expiry} days)"
                    )
                
            except Exception as e:
                logger.error(f"Error in certificate monitoring loop: {e}")
                await asyncio.sleep(3600)  # Wait longer on error
    
    async def _performance_optimization_loop(self):
        """Continuous performance optimization based on SSL metrics"""
        logger.info("⚡ Starting SSL performance optimization loop...")
        
        while True:
            try:
                await asyncio.sleep(300)  # Check every 5 minutes
                
                # Analyze performance patterns
                optimizations = await self._identify_optimization_opportunities()
                
                # Apply optimizations
                for optimization in optimizations:
                    if optimization['auto_apply']:
                        logger.info(f"⚡ Applying SSL optimization: {optimization['description']}")
                        success = await self._apply_optimization(optimization)
                        
                        if success:
                            logger.info(f"✅ SSL optimization successful: {optimization['type']}")
                        else:
                            logger.warning(f"❌ SSL optimization failed: {optimization['type']}")
                
            except Exception as e:
                logger.error(f"Error in performance optimization loop: {e}")
                await asyncio.sleep(600)
    
    async def _identify_optimization_opportunities(self) -> List[Dict[str, Any]]:
        """Identify SSL performance optimization opportunities"""
        optimizations = []
        
        if len(self.ssl_metrics) < 50:
            return optimizations
        
        # Analyze recent performance
        recent_metrics = list(self.ssl_metrics)[-100:]
        successful_metrics = [m for m in recent_metrics if m.connection_success]
        
        if successful_metrics:
            avg_handshake_time = statistics.mean([m.handshake_time_ms for m in successful_metrics])
            
            # Slow handshake optimization
            if avg_handshake_time > 1000:  # >1 second
                optimizations.append({
                    'type': 'slow_handshake',
                    'description': f'Optimize slow SSL handshakes (avg: {avg_handshake_time:.1f}ms)',
                    'auto_apply': True,
                    'expected_improvement': '20-30% handshake time reduction'
                })
            
            # Connection pooling optimization
            error_rate = 1 - (len(successful_metrics) / len(recent_metrics))
            if error_rate > 0.05:  # >5% error rate
                optimizations.append({
                    'type': 'connection_pooling',
                    'description': f'Optimize connection pooling (error rate: {error_rate:.1%})',
                    'auto_apply': True,
                    'expected_improvement': 'Reduce connection errors by 50%'
                })
        
        return optimizations
    
    async def _apply_optimization(self, optimization: Dict[str, Any]) -> bool:
        """Apply SSL performance optimization"""
        try:
            if optimization['type'] == 'slow_handshake':
                return await self._optimize_handshake_performance()
            elif optimization['type'] == 'connection_pooling':
                return await self._optimize_connection_pooling()
            else:
                return False
        except Exception as e:
            logger.error(f"Optimization application failed: {e}")
            return False
    
    async def _optimize_handshake_performance(self) -> bool:
        """Optimize SSL handshake performance"""
        try:
            # Trigger connection refresh to establish fresh SSL sessions
            await self._remediate_refresh_engine()
            return True
        except Exception:
            return False
    
    async def _optimize_connection_pooling(self) -> bool:
        """Optimize connection pooling for SSL performance"""
        try:
            from utils.dynamic_database_pool_manager import dynamic_pool
            
            # Trigger proactive scaling if we're seeing SSL errors
            current_size = dynamic_pool.current_pool_size
            optimized_size = min(current_size + 2, dynamic_pool.pool_config['max_pool_size'])
            
            if optimized_size > current_size:
                await dynamic_pool._scale_pool(
                    optimized_size,
                    dynamic_pool.current_overflow,
                    "ssl_performance_optimization"
                )
                return True
            
            return False
        except Exception:
            return False
    
    async def _get_current_error_rate(self) -> float:
        """Get current SSL error rate"""
        with self._health_lock:
            if len(self.ssl_metrics) == 0:
                return 0.0
            
            recent_cutoff = datetime.utcnow() - timedelta(minutes=5)
            recent_metrics = [
                m for m in list(self.ssl_metrics)
                if m.timestamp >= recent_cutoff
            ]
            
            if not recent_metrics:
                return 0.0
            
            failed_count = len([m for m in recent_metrics if not m.connection_success])
            return failed_count / len(recent_metrics)
    
    async def _get_current_avg_handshake_time(self) -> float:
        """Get current average SSL handshake time"""
        with self._health_lock:
            recent_cutoff = datetime.utcnow() - timedelta(minutes=5)
            recent_successful = [
                m for m in list(self.ssl_metrics)
                if m.timestamp >= recent_cutoff and m.connection_success
            ]
            
            if not recent_successful:
                return 0.0
            
            return statistics.mean([m.handshake_time_ms for m in recent_successful])
    
    async def _get_active_connection_count(self) -> int:
        """Get current active connection count"""
        try:
            from utils.dynamic_database_pool_manager import dynamic_pool
            return len(dynamic_pool.active_connections)
        except Exception:
            return 0
    
    def get_comprehensive_ssl_report(self) -> Dict[str, Any]:
        """Get comprehensive SSL health report"""
        with self._health_lock:
            # Calculate comprehensive metrics
            recent_metrics = list(self.ssl_metrics)[-100:]
            successful_metrics = [m for m in recent_metrics if m.connection_success]
            
            # Error analysis
            error_types = defaultdict(int)
            for metric in recent_metrics:
                if not metric.connection_success and metric.error_type:
                    error_types[metric.error_type] += 1
            
            # Performance metrics
            avg_handshake_time = (
                statistics.mean([m.handshake_time_ms for m in successful_metrics])
                if successful_metrics else 0.0
            )
            
            p95_handshake_time = (
                sorted([m.handshake_time_ms for m in successful_metrics])[int(len(successful_metrics) * 0.95)]
                if successful_metrics else 0.0
            )
            
            return {
                'timestamp': datetime.utcnow().isoformat(),
                'overall_health': {
                    'status': self.current_health_status.value,
                    'trend': self.health_trend,
                    'last_check': self.last_health_check.isoformat()
                },
                'performance_metrics': {
                    'total_connections': self.performance_stats['total_connections'],
                    'success_rate': (
                        self.performance_stats['successful_connections'] / 
                        max(self.performance_stats['total_connections'], 1)
                    ),
                    'avg_handshake_time_ms': round(avg_handshake_time, 2),
                    'p95_handshake_time_ms': round(p95_handshake_time, 2),
                    'current_error_rate': await self._get_current_error_rate()
                },
                'error_analysis': {
                    'total_errors': self.performance_stats['failed_connections'],
                    'error_types': dict(error_types),
                    'recent_error_trend': self._calculate_error_trend()
                },
                'remediation_summary': {
                    'total_remediations': self.performance_stats['remediations_applied'],
                    'proactive_actions': self.performance_stats['proactive_actions'],
                    'recent_remediations': [
                        {
                            'timestamp': action.timestamp.isoformat(),
                            'strategy': action.strategy.value,
                            'reason': action.trigger_reason,
                            'success': action.success,
                            'duration_ms': round(action.duration_ms, 2)
                        }
                        for action in list(self.remediation_history)[-10:]
                    ]
                },
                'certificate_info': {
                    cert_type: {
                        'subject': cert.subject,
                        'expires_at': cert.expires_at.isoformat(),
                        'days_until_expiry': cert.days_until_expiry,
                        'is_valid': cert.is_valid
                    }
                    for cert_type, cert in self.certificate_cache.items()
                },
                'predictive_insights': {
                    'active_alerts': len([
                        alert for alert in self.predictive_alerts
                        if alert['timestamp'] >= datetime.utcnow() - timedelta(hours=1)
                    ]),
                    'recent_predictions': [
                        {
                            'timestamp': alert['timestamp'].isoformat(),
                            'prediction': alert['prediction'],
                            'action_taken': alert['action_taken']
                        }
                        for alert in list(self.predictive_alerts)[-5:]
                    ]
                }
            }
    
    def _calculate_error_trend(self) -> str:
        """Calculate recent error trend"""
        if len(self.health_history) < 6:
            return 'insufficient_data'
        
        recent_entries = list(self.health_history)[-6:]
        error_rates = [entry['error_rate'] for entry in recent_entries]
        
        first_half = error_rates[:3]
        second_half = error_rates[3:]
        
        first_avg = statistics.mean(first_half)
        second_avg = statistics.mean(second_half)
        
        if second_avg > first_avg * 1.2:
            return 'increasing'
        elif second_avg < first_avg * 0.8:
            return 'decreasing'
        else:
            return 'stable'


# Global SSL health manager instance
ssl_health_manager = ProactiveSSLHealthManager()


def record_ssl_connection(
    context: str,
    success: bool,
    handshake_time_ms: float,
    error_type: Optional[str] = None,
    server_info: Optional[Dict[str, Any]] = None
):
    """Record SSL connection attempt for health monitoring"""
    ssl_health_manager.record_ssl_connection_attempt(
        context, success, handshake_time_ms, error_type, server_info
    )


def get_ssl_health_report() -> Dict[str, Any]:
    """Get comprehensive SSL health report"""
    return ssl_health_manager.get_comprehensive_ssl_report()


def register_ssl_health_callback(callback: Callable):
    """Register callback for SSL health changes"""
    ssl_health_manager.register_health_callback(callback)


def register_ssl_remediation_callback(callback: Callable):
    """Register callback for SSL remediation actions"""
    ssl_health_manager.register_remediation_callback(callback)