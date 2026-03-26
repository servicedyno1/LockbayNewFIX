"""
Dynamic Database Pool Manager
Auto-scaling connection pool with intelligent workload pattern detection and optimization
"""

import logging
import time
import asyncio
import threading
from datetime import datetime
from typing import Optional, Dict, Any, List
from contextlib import contextmanager
from collections import deque, defaultdict
from dataclasses import dataclass
import statistics
from enum import Enum
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import QueuePool
from config import Config
from utils.ssl_connection_monitor import record_ssl_error, record_ssl_recovery, record_ssl_retry
from utils.enhanced_database_pool_analytics import record_pool_event

logger = logging.getLogger(__name__)


class WorkloadPattern(Enum):
    """Workload pattern types"""
    LOW = "low"          # <30% utilization
    MODERATE = "moderate" # 30-70% utilization
    HIGH = "high"        # 70-90% utilization
    PEAK = "peak"        # >90% utilization
    BURST = "burst"      # Sudden spike in demand


@dataclass
class PoolScalingEvent:
    """Pool scaling event record"""
    timestamp: datetime
    event_type: str  # scale_up, scale_down, pattern_detected
    old_pool_size: int
    new_pool_size: int
    old_overflow: int
    new_overflow: int
    trigger_reason: str
    workload_pattern: WorkloadPattern
    utilization_percentage: float


class DynamicConnectionPool:
    """Dynamic connection pool with intelligent auto-scaling"""
    
    def __init__(self):
        self.database_url = Config.DATABASE_URL
        if not self.database_url:
            raise ValueError("DATABASE_URL environment variable is required")
        
        # Dynamic pool configuration
        self.pool_config = {
            'base_pool_size': 10,      # Minimum pool size
            'max_pool_size': 30,       # Maximum pool size
            'base_overflow': 15,       # Base overflow connections
            'max_overflow': 50,        # Maximum overflow connections
            'scale_up_threshold': 0.8,  # Scale up when >80% utilized
            'scale_down_threshold': 0.3, # Scale down when <30% utilized
            'scale_cooldown_seconds': 120, # Wait between scaling operations
            'pattern_detection_window': 300,  # 5 minutes for pattern detection
        }
        
        # Current pool settings
        self.current_pool_size = self.pool_config['base_pool_size']
        self.current_overflow = self.pool_config['base_overflow']
        
        # Workload monitoring
        self.workload_history = deque(maxlen=100)
        self.utilization_samples = deque(maxlen=60)  # 5 minutes of samples (5s intervals)
        self.scaling_events = deque(maxlen=50)
        self.last_scaling_time = datetime.min
        self.current_workload_pattern = WorkloadPattern.LOW
        
        # Connection tracking
        self.active_connections = {}
        self.connection_lifecycle = defaultdict(dict)
        self.connection_performance = defaultdict(list)
        
        # Performance metrics
        self.performance_metrics = {
            'total_acquisitions': 0,
            'total_acquisition_time': 0.0,
            'slow_acquisitions': 0,
            'failed_acquisitions': 0,
            'ssl_recoveries': 0,
            'pool_exhaustion_events': 0
        }
        
        # Threading and locks
        self._scaling_lock = threading.Lock()
        self._metrics_lock = threading.Lock()
        self._pool_lock = threading.Lock()
        
        # Create initial engine
        self.engine = self._create_dynamic_engine()
        self.SessionFactory = sessionmaker(
            bind=self.engine,
            autocommit=False,
            autoflush=False,
            expire_on_commit=False
        )
        
        # Pre-warmed connection cache
        self._warmed_sessions = []
        self._session_cache_lock = threading.Lock()
        
        # Start background monitoring
        asyncio.create_task(self._workload_monitoring_loop())
        asyncio.create_task(self._auto_scaling_loop())
        asyncio.create_task(self._connection_health_loop())
        
        # Initial connection warming
        self._warm_connections_async()
        
        logger.info(
            f"🚀 Dynamic Database Pool initialized: "
            f"pool_size={self.current_pool_size}, overflow={self.current_overflow}"
        )
    
    def _create_dynamic_engine(self):
        """Create engine with dynamic pool configuration"""
        engine = create_engine(
            self.database_url,
            poolclass=QueuePool,
            pool_size=self.current_pool_size,
            max_overflow=self.current_overflow,
            pool_timeout=8,  # Balanced timeout for dynamic scaling
            pool_recycle=1800,  # 30 minutes
            pool_pre_ping=True,
            pool_reset_on_return='rollback',
            echo=False,
            connect_args={
                "application_name": "escrow_bot_dynamic_pool",
                "connect_timeout": 12,
                # ASYNC FIX: Use 'ssl' instead of 'sslmode' for async compatibility
                "ssl": "require",  # ASYNC FIX: Proper SSL parameter for async drivers
                "keepalives": 1,
                "keepalives_idle": 30,
                "keepalives_interval": 5,
                "keepalives_count": 3,
                "tcp_user_timeout": 25000,
                "options": "-c statement_timeout=30s -c idle_in_transaction_session_timeout=30s"
            }
        )
        
        # Enhanced connection event listeners
        @event.listens_for(engine, "connect")
        def on_connect(dbapi_conn, connection_record):
            connection_id = str(id(connection_record))
            connection_record.info['connection_id'] = connection_id
            connection_record.info['connect_time'] = time.time()
            connection_record.info['query_count'] = 0
            
            # Record analytics
            record_pool_event(
                'created', connection_id, 'dynamic_pool',
                duration_ms=(time.time() - connection_record.info.get('creation_start', time.time())) * 1000
            )
        
        @event.listens_for(engine, "checkout")
        def on_checkout(dbapi_conn, connection_record, connection_proxy):
            connection_id = connection_record.info.get('connection_id', 'unknown')
            checkout_start = time.time()
            connection_record.info['checkout_time'] = checkout_start
            
            # Track active connections
            with self._pool_lock:
                self.active_connections[connection_id] = {
                    'checkout_time': checkout_start,
                    'thread_id': threading.get_ident()
                }
        
        @event.listens_for(engine, "checkin")
        def on_checkin(dbapi_conn, connection_record):
            connection_id = connection_record.info.get('connection_id', 'unknown')
            checkout_time = connection_record.info.get('checkout_time', time.time())
            usage_duration = (time.time() - checkout_time) * 1000  # ms
            
            # Update query count
            connection_record.info['query_count'] = connection_record.info.get('query_count', 0) + 1
            
            # Record performance metrics
            with self._pool_lock:
                if connection_id in self.active_connections:
                    del self.active_connections[connection_id]
                
                self.connection_performance[connection_id].append({
                    'usage_duration_ms': usage_duration,
                    'timestamp': time.time()
                })
                
                # Keep only recent performance data
                cutoff = time.time() - 3600  # 1 hour
                self.connection_performance[connection_id] = [
                    perf for perf in self.connection_performance[connection_id]
                    if perf['timestamp'] >= cutoff
                ]
            
            # Record analytics
            record_pool_event(
                'released', connection_id, 'dynamic_pool',
                duration_ms=usage_duration,
                query_count=connection_record.info.get('query_count', 0)
            )
        
        return engine
    
    @contextmanager
    def get_session(self, context_id: str = "default", priority: str = "normal"):
        """Get a database session with dynamic scaling and enhanced error handling"""
        acquisition_start = time.time()
        session = None
        connection_id = None
        retry_count = 0
        max_retries = 3
        
        while retry_count < max_retries:
            try:
                # Try to get pre-warmed session first
                with self._session_cache_lock:
                    if self._warmed_sessions and priority != "high":
                        session = self._warmed_sessions.pop()
                        try:
                            # Validate session
                            session.execute(text("SELECT 1"))
                            connection_id = self._get_connection_id(session)
                            logger.debug(f"Using pre-warmed session {connection_id} for {context_id}")
                        except Exception as validation_error:
                            if "SSL" in str(validation_error):
                                logger.debug("🔌 Stale SSL session detected, creating new one")
                                record_ssl_error(f"dynamic_pool_validation_{context_id}", str(validation_error))
                            session.close()
                            session = None
                
                # Create new session if needed
                if session is None:
                    session = self.SessionFactory()
                    connection_id = self._get_connection_id(session)
                
                # Track acquisition time
                acquisition_time = (time.time() - acquisition_start) * 1000  # ms
                
                # Update performance metrics
                with self._metrics_lock:
                    self.performance_metrics['total_acquisitions'] += 1
                    self.performance_metrics['total_acquisition_time'] += acquisition_time
                    
                    if acquisition_time > 200:  # Slow acquisition
                        self.performance_metrics['slow_acquisitions'] += 1
                        logger.debug(f"⚠️ Slow connection acquisition: {acquisition_time:.1f}ms for {context_id}")
                    
                    # Check for pool exhaustion
                    pool_stats = self.get_pool_statistics()
                    if pool_stats['pool_checked_out'] >= pool_stats['pool_size']:
                        self.performance_metrics['pool_exhaustion_events'] += 1
                        logger.warning(f"🚨 Pool exhaustion detected for context: {context_id}")
                
                # Record successful acquisition
                record_pool_event(
                    'acquired', connection_id or 'unknown', context_id,
                    duration_ms=acquisition_time
                )
                
                # Update utilization for scaling decisions
                self._update_utilization_sample()
                
                yield session
                session.commit()
                break  # Success
                
            except Exception as e:
                error_msg = str(e)
                retry_count += 1
                
                if session:
                    session.rollback()
                
                # Handle SSL connection errors with enhanced retry logic
                if "SSL connection has been closed unexpectedly" in error_msg and retry_count < max_retries:
                    record_ssl_retry(f"dynamic_pool_session_{context_id}", retry_count, error_msg)
                    logger.debug(f"🔌 SSL retry {retry_count}/{max_retries} for {context_id}")
                    
                    if session:
                        session.close()
                        session = None
                    
                    # Consider scaling up on repeated SSL errors
                    if retry_count > 1:
                        self._consider_emergency_scaling("ssl_errors")
                        self.engine.dispose()  # Force fresh connections
                    
                    time.sleep(0.5 * retry_count)  # Exponential backoff
                    continue
                else:
                    # Final error or non-SSL error
                    if "SSL" in error_msg:
                        record_ssl_error(f"dynamic_pool_session_{context_id}", error_msg, retry_count)
                    
                    record_pool_event(
                        'error', connection_id or 'unknown', context_id,
                        error_message=error_msg
                    )
                    
                    with self._metrics_lock:
                        self.performance_metrics['failed_acquisitions'] += 1
                    
                    logger.error(f"❌ Connection failed for {context_id} after {retry_count} attempts: {error_msg}")
                    raise
                    
            finally:
                if session and retry_count < max_retries:
                    session.close()
                    
                    # Refill session cache if needed
                    if len(self._warmed_sessions) < 2:
                        threading.Thread(
                            target=self._warm_single_connection,
                            daemon=True
                        ).start()
    
    def _get_connection_id(self, session: Session) -> Optional[str]:
        """Get connection ID from session"""
        try:
            connection = session.connection()
            return str(id(connection))
        except Exception:
            return None
    
    def _update_utilization_sample(self):
        """Update current pool utilization sample"""
        try:
            stats = self.get_pool_statistics()
            utilization = (stats['pool_checked_out'] / max(stats['pool_size'], 1))
            
            with self._metrics_lock:
                self.utilization_samples.append({
                    'timestamp': time.time(),
                    'utilization': utilization,
                    'checked_out': stats['pool_checked_out'],
                    'pool_size': stats['pool_size'],
                    'overflow': stats['pool_overflow']
                })
        except Exception as e:
            logger.debug(f"Error updating utilization: {e}")
    
    def _consider_emergency_scaling(self, reason: str):
        """Consider emergency scaling based on error patterns"""
        current_time = time.time()
        
        # Only if we haven't scaled recently
        if current_time - time.mktime(self.last_scaling_time.timetuple()) > 30:  # 30 seconds
            logger.warning(f"🚨 Considering emergency scaling due to: {reason}")
            
            # Scale up by 25% immediately
            new_size = min(
                int(self.current_pool_size * 1.25),
                self.pool_config['max_pool_size']
            )
            
            if new_size > self.current_pool_size:
                asyncio.create_task(self._scale_pool(new_size, self.current_overflow, f"emergency_{reason}"))
    
    async def _workload_monitoring_loop(self):
        """Monitor workload patterns for intelligent scaling"""
        logger.info("📈 Starting workload monitoring loop...")
        
        while True:
            try:
                await asyncio.sleep(5)  # Sample every 5 seconds
                
                # Get current utilization
                if len(self.utilization_samples) > 0:
                    recent_samples = list(self.utilization_samples)[-12:]  # Last minute
                    avg_utilization = statistics.mean([s['utilization'] for s in recent_samples])
                    
                    # Detect workload pattern
                    pattern = self._detect_workload_pattern(avg_utilization, recent_samples)
                    
                    if pattern != self.current_workload_pattern:
                        logger.info(
                            f"🔄 Workload pattern changed: {self.current_workload_pattern.value} → {pattern.value} "
                            f"(avg utilization: {avg_utilization:.1%})"
                        )
                        self.current_workload_pattern = pattern
                        
                        # Record pattern change
                        self.workload_history.append({
                            'timestamp': datetime.utcnow(),
                            'pattern': pattern,
                            'utilization': avg_utilization,
                            'pool_size': self.current_pool_size
                        })
                
            except Exception as e:
                logger.error(f"Error in workload monitoring: {e}")
                await asyncio.sleep(30)
    
    def _detect_workload_pattern(self, avg_utilization: float, samples: List[Dict]) -> WorkloadPattern:
        """Detect current workload pattern"""
        if not samples:
            return WorkloadPattern.LOW
        
        utilizations = [s['utilization'] for s in samples]
        max_util = max(utilizations)
        min(utilizations)
        variance = statistics.variance(utilizations) if len(utilizations) > 1 else 0
        
        # Detect burst pattern (high variance and recent spike)
        if variance > 0.1 and max_util > 0.8:
            return WorkloadPattern.BURST
        
        # Detect based on average utilization
        if avg_utilization >= 0.9:
            return WorkloadPattern.PEAK
        elif avg_utilization >= 0.7:
            return WorkloadPattern.HIGH
        elif avg_utilization >= 0.3:
            return WorkloadPattern.MODERATE
        else:
            return WorkloadPattern.LOW
    
    async def _auto_scaling_loop(self):
        """Automatic pool scaling based on workload patterns"""
        logger.info("⚖️ Starting auto-scaling loop...")
        
        while True:
            try:
                await asyncio.sleep(30)  # Check every 30 seconds
                
                # Skip if recently scaled
                if (datetime.utcnow() - self.last_scaling_time).total_seconds() < self.pool_config['scale_cooldown_seconds']:
                    continue
                
                # Get scaling recommendation
                recommendation = self._get_scaling_recommendation()
                
                if recommendation and recommendation['action'] != 'none':
                    logger.info(
                        f"🎯 Scaling recommendation: {recommendation['action']} to "
                        f"pool_size={recommendation['target_pool_size']}, "
                        f"reason={recommendation['reason']}"
                    )
                    
                    await self._scale_pool(
                        recommendation['target_pool_size'],
                        recommendation['target_overflow'],
                        recommendation['reason']
                    )
                
            except Exception as e:
                logger.error(f"Error in auto-scaling loop: {e}")
                await asyncio.sleep(60)
    
    def _get_scaling_recommendation(self) -> Optional[Dict[str, Any]]:
        """Get intelligent scaling recommendation"""
        if len(self.utilization_samples) < 10:
            return None
        
        # Calculate recent utilization metrics
        recent_samples = list(self.utilization_samples)[-20:]  # Last ~2 minutes
        avg_utilization = statistics.mean([s['utilization'] for s in recent_samples])
        max_utilization = max([s['utilization'] for s in recent_samples])
        
        current_pattern = self.current_workload_pattern
        
        # Scaling logic based on pattern and utilization
        if current_pattern == WorkloadPattern.PEAK or max_utilization > 0.95:
            # Aggressive scale up for peak workload
            target_size = min(
                int(self.current_pool_size * 1.5),
                self.pool_config['max_pool_size']
            )
            target_overflow = min(
                int(self.current_overflow * 1.3),
                self.pool_config['max_overflow']
            )
            
            if target_size > self.current_pool_size:
                return {
                    'action': 'scale_up',
                    'target_pool_size': target_size,
                    'target_overflow': target_overflow,
                    'reason': f'peak_workload_util_{avg_utilization:.1%}'
                }
        
        elif current_pattern == WorkloadPattern.BURST:
            # Quick scale up for burst traffic
            target_size = min(
                int(self.current_pool_size * 1.3),
                self.pool_config['max_pool_size']
            )
            
            if target_size > self.current_pool_size:
                return {
                    'action': 'scale_up',
                    'target_pool_size': target_size,
                    'target_overflow': self.current_overflow,
                    'reason': f'burst_traffic_util_{avg_utilization:.1%}'
                }
        
        elif avg_utilization > self.pool_config['scale_up_threshold']:
            # Standard scale up
            target_size = min(
                self.current_pool_size + 3,
                self.pool_config['max_pool_size']
            )
            
            if target_size > self.current_pool_size:
                return {
                    'action': 'scale_up',
                    'target_pool_size': target_size,
                    'target_overflow': self.current_overflow,
                    'reason': f'high_utilization_{avg_utilization:.1%}'
                }
        
        elif avg_utilization < self.pool_config['scale_down_threshold'] and current_pattern == WorkloadPattern.LOW:
            # Scale down during low utilization
            target_size = max(
                self.current_pool_size - 2,
                self.pool_config['base_pool_size']
            )
            
            if target_size < self.current_pool_size:
                return {
                    'action': 'scale_down',
                    'target_pool_size': target_size,
                    'target_overflow': max(self.current_overflow - 5, self.pool_config['base_overflow']),
                    'reason': f'low_utilization_{avg_utilization:.1%}'
                }
        
        return {'action': 'none'}
    
    async def _scale_pool(self, new_pool_size: int, new_overflow: int, reason: str):
        """Scale the connection pool"""
        with self._scaling_lock:
            try:
                old_pool_size = self.current_pool_size
                old_overflow = self.current_overflow
                
                # Update pool configuration
                self.current_pool_size = new_pool_size
                self.current_overflow = new_overflow
                
                # Create new engine with updated configuration
                old_engine = self.engine
                self.engine = self._create_dynamic_engine()
                
                # Update session factory
                self.SessionFactory = sessionmaker(
                    bind=self.engine,
                    autocommit=False,
                    autoflush=False,
                    expire_on_commit=False
                )
                
                # Dispose old engine
                old_engine.dispose()
                
                # Clear cached sessions to force new connections
                with self._session_cache_lock:
                    for session in self._warmed_sessions:
                        try:
                            session.close()
                        except Exception:
                            pass
                    self._warmed_sessions.clear()
                
                # Record scaling event
                scaling_event = PoolScalingEvent(
                    timestamp=datetime.utcnow(),
                    event_type='scale_up' if new_pool_size > old_pool_size else 'scale_down',
                    old_pool_size=old_pool_size,
                    new_pool_size=new_pool_size,
                    old_overflow=old_overflow,
                    new_overflow=new_overflow,
                    trigger_reason=reason,
                    workload_pattern=self.current_workload_pattern,
                    utilization_percentage=self._get_current_utilization()
                )
                
                self.scaling_events.append(scaling_event)
                self.last_scaling_time = datetime.utcnow()
                
                logger.info(
                    f"🎯 Pool scaled successfully: {old_pool_size}→{new_pool_size} "
                    f"(overflow: {old_overflow}→{new_overflow}) | Reason: {reason}"
                )
                
                # Warm new connections
                await asyncio.sleep(1)  # Brief delay for engine to stabilize
                self._warm_connections_async()
                
                return True
                
            except Exception as e:
                logger.error(f"❌ Failed to scale pool: {e}")
                # Restore old values on error
                self.current_pool_size = old_pool_size if 'old_pool_size' in locals() else self.current_pool_size
                self.current_overflow = old_overflow if 'old_overflow' in locals() else self.current_overflow
                return False
    
    def _get_current_utilization(self) -> float:
        """Get current pool utilization percentage"""
        try:
            stats = self.get_pool_statistics()
            return (stats['pool_checked_out'] / max(stats['pool_size'], 1)) * 100
        except Exception:
            return 0.0
    
    def _warm_connections_async(self):
        """Asynchronously warm connections"""
        threading.Thread(
            target=self._warm_connections,
            args=(min(3, self.current_pool_size // 3),),
            daemon=True
        ).start()
    
    def _warm_connections(self, num_connections: int = 3):
        """Warm database connections with enhanced SSL handling"""
        try:
            logger.info(f"🔥 Warming {num_connections} connections for dynamic pool...")
            start_time = time.time()
            successful_connections = 0
            
            for i in range(num_connections):
                session = None
                for attempt in range(3):
                    try:
                        session = self.SessionFactory()
                        result = session.execute(text("SELECT 1, pg_backend_pid() as pid"))
                        row = result.fetchone()
                        
                        with self._session_cache_lock:
                            self._warmed_sessions.append(session)
                        
                        successful_connections += 1
                        logger.debug(f"Warmed connection {i+1} to PID {row.pid if row else 'unknown'}")
                        break
                        
                    except Exception as e:
                        error_msg = str(e)
                        if "SSL" in error_msg and attempt < 2:
                            record_ssl_retry(f"dynamic_pool_warmup_{i+1}", attempt + 1, error_msg)
                            if session:
                                session.close()
                            time.sleep(0.5)
                            continue
                        else:
                            if "SSL" in error_msg:
                                record_ssl_error(f"dynamic_pool_warmup_{i+1}", error_msg, attempt + 1)
                            logger.warning(f"Failed to warm connection {i+1}: {e}")
                            if session:
                                session.close()
                            break
            
            warm_time = time.time() - start_time
            logger.info(f"✅ Warmed {successful_connections}/{num_connections} connections in {warm_time:.2f}s")
            
        except Exception as e:
            logger.error(f"Connection warming failed: {e}")
    
    def _warm_single_connection(self):
        """Warm a single connection for session cache"""
        try:
            session = self.SessionFactory()
            session.execute(text("SELECT 1"))
            
            with self._session_cache_lock:
                if len(self._warmed_sessions) < 3:  # Limit cache size
                    self._warmed_sessions.append(session)
                else:
                    session.close()
                    
        except Exception as e:
            logger.debug(f"Failed to warm single connection: {e}")
    
    async def _connection_health_loop(self):
        """Monitor connection health and perform maintenance"""
        logger.info("🏥 Starting connection health monitoring...")
        
        while True:
            try:
                await asyncio.sleep(60)  # Check every minute
                
                # Check for stale connections
                self._cleanup_stale_connections()
                
                # Monitor SSL health
                ssl_issues = self._check_ssl_health()
                
                if ssl_issues > 0:
                    logger.warning(f"🔌 Detected {ssl_issues} SSL health issues")
                    # Consider scaling or engine refresh
                    if ssl_issues > 3:
                        logger.info("🔌 High SSL issues - refreshing engine connections")
                        self.engine.dispose()
                        self._warm_connections_async()
                
                # Performance maintenance
                self._performance_maintenance()
                
            except Exception as e:
                logger.error(f"Error in connection health loop: {e}")
                await asyncio.sleep(120)
    
    def _cleanup_stale_connections(self):
        """Clean up stale cached connections"""
        with self._session_cache_lock:
            healthy_sessions = []
            
            for session in self._warmed_sessions:
                try:
                    session.execute(text("SELECT 1"))
                    healthy_sessions.append(session)
                except Exception as e:
                    logger.debug(f"Removing stale session: {e}")
                    try:
                        session.close()
                    except Exception as e:
                        pass
            
            removed_count = len(self._warmed_sessions) - len(healthy_sessions)
            self._warmed_sessions = healthy_sessions
            
            if removed_count > 0:
                logger.debug(f"🧹 Cleaned up {removed_count} stale connections")
    
    def _check_ssl_health(self) -> int:
        """Check SSL connection health"""
        issues = 0
        
        # Check recent SSL errors from monitoring
        try:
            from utils.ssl_connection_monitor import get_ssl_health_summary
            ssl_summary = get_ssl_health_summary()
            
            if ssl_summary['overall_health'] in ['warning', 'critical']:
                issues += ssl_summary['metrics']['errors_last_5min']
                
                # Record SSL recovery if we've had issues
                if issues > 0:
                    record_ssl_recovery('dynamic_pool_health_check', 0.0)
                    
        except Exception as e:
            logger.debug(f"SSL health check error: {e}")
        
        return issues
    
    def _performance_maintenance(self):
        """Perform performance maintenance tasks"""
        try:
            # Clean old performance data
            cutoff_time = time.time() - 3600  # 1 hour ago
            
            with self._pool_lock:
                for conn_id in list(self.connection_performance.keys()):
                    self.connection_performance[conn_id] = [
                        perf for perf in self.connection_performance[conn_id]
                        if perf['timestamp'] >= cutoff_time
                    ]
                    
                    # Remove empty entries
                    if not self.connection_performance[conn_id]:
                        del self.connection_performance[conn_id]
        
        except Exception as e:
            logger.debug(f"Performance maintenance error: {e}")
    
    def get_pool_statistics(self) -> Dict[str, Any]:
        """Get comprehensive pool statistics"""
        try:
            base_stats = {
                'pool_size': self.engine.pool.size(),
                'pool_checked_out': self.engine.pool.checkedout(),
                'pool_overflow': self.engine.pool.overflow(),
                'pool_invalid': getattr(self.engine.pool, 'invalidated', 0),  # Use invalidated count instead
            }
        except Exception:
            base_stats = {
                'pool_size': self.current_pool_size,
                'pool_checked_out': 0,
                'pool_overflow': 0,
                'pool_invalid': 0,
            }
        
        with self._metrics_lock:
            avg_acquisition_time = (
                self.performance_metrics['total_acquisition_time'] / 
                max(self.performance_metrics['total_acquisitions'], 1)
            )
        
        return {
            **base_stats,
            'current_pool_size': self.current_pool_size,
            'current_overflow': self.current_overflow,
            'max_pool_size': self.pool_config['max_pool_size'],
            'warmed_sessions': len(self._warmed_sessions),
            'active_connections': len(self.active_connections),
            'workload_pattern': self.current_workload_pattern.value,
            'current_utilization': self._get_current_utilization(),
            'avg_acquisition_time_ms': round(avg_acquisition_time, 2),
            'total_acquisitions': self.performance_metrics['total_acquisitions'],
            'slow_acquisitions': self.performance_metrics['slow_acquisitions'],
            'failed_acquisitions': self.performance_metrics['failed_acquisitions'],
            'ssl_recoveries': self.performance_metrics['ssl_recoveries'],
            'scaling_events': len(self.scaling_events),
            'last_scaling': self.last_scaling_time.isoformat() if self.last_scaling_time != datetime.min else None
        }
    
    def get_scaling_history(self) -> List[Dict[str, Any]]:
        """Get pool scaling event history"""
        return [
            {
                'timestamp': event.timestamp.isoformat(),
                'event_type': event.event_type,
                'old_pool_size': event.old_pool_size,
                'new_pool_size': event.new_pool_size,
                'old_overflow': event.old_overflow,
                'new_overflow': event.new_overflow,
                'reason': event.trigger_reason,
                'workload_pattern': event.workload_pattern.value,
                'utilization_percentage': round(event.utilization_percentage, 2)
            }
            for event in self.scaling_events
        ]
    
    def get_workload_analysis(self) -> Dict[str, Any]:
        """Get workload pattern analysis"""
        if not self.workload_history:
            return {'patterns': [], 'recommendations': []}
        
        # Analyze pattern distribution
        pattern_counts = defaultdict(int)
        for entry in self.workload_history:
            pattern_counts[entry['pattern'].value] += 1
        
        # Get utilization trends
        recent_utilizations = [
            s['utilization'] for s in list(self.utilization_samples)[-20:]
        ]
        
        trend = "stable"
        if len(recent_utilizations) > 10:
            first_half = recent_utilizations[:len(recent_utilizations)//2]
            second_half = recent_utilizations[len(recent_utilizations)//2:]
            
            if statistics.mean(second_half) > statistics.mean(first_half) + 0.1:
                trend = "increasing"
            elif statistics.mean(second_half) < statistics.mean(first_half) - 0.1:
                trend = "decreasing"
        
        return {
            'current_pattern': self.current_workload_pattern.value,
            'pattern_distribution': dict(pattern_counts),
            'utilization_trend': trend,
            'avg_recent_utilization': round(statistics.mean(recent_utilizations), 3) if recent_utilizations else 0,
            'peak_utilization': round(max(recent_utilizations), 3) if recent_utilizations else 0,
            'recommendations': self._generate_workload_recommendations()
        }
    
    def _generate_workload_recommendations(self) -> List[str]:
        """Generate workload-based recommendations"""
        recommendations = []
        
        if self.current_workload_pattern == WorkloadPattern.PEAK:
            recommendations.append("Consider pre-scaling during known peak hours")
            recommendations.append("Monitor for sustained high utilization patterns")
        
        elif self.current_workload_pattern == WorkloadPattern.BURST:
            recommendations.append("Enable aggressive burst scaling")
            recommendations.append("Consider increasing overflow capacity")
        
        elif self.current_workload_pattern == WorkloadPattern.LOW:
            recommendations.append("Opportunity for connection pool optimization")
            recommendations.append("Consider reducing base pool size during off-peak")
        
        # Check scaling efficiency
        if len(self.scaling_events) > 10:  # Frequent scaling
            recommendations.append("Frequent scaling detected - consider tuning thresholds")
        
        return recommendations


# Global dynamic pool instance
dynamic_pool = DynamicConnectionPool()


def get_dynamic_session(context_id: str = "default", priority: str = "normal"):
    """Get a session from the dynamic pool"""
    return dynamic_pool.get_session(context_id, priority)


def get_dynamic_pool_stats() -> Dict[str, Any]:
    """Get dynamic pool statistics"""
    return dynamic_pool.get_pool_statistics()


def get_scaling_history() -> List[Dict[str, Any]]:
    """Get pool scaling history"""
    return dynamic_pool.get_scaling_history()


def get_workload_analysis() -> Dict[str, Any]:
    """Get workload analysis"""
    return dynamic_pool.get_workload_analysis()


# Export for backward compatibility
DynamicSessionLocal = dynamic_pool.SessionFactory