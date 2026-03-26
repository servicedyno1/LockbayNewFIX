"""
Connection Lifecycle Optimizer
Intelligent connection management with optimized reuse patterns, aging strategies, and performance optimization
"""

import logging
import time
import asyncio
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Set, Callable
from collections import deque, defaultdict
from dataclasses import dataclass, field
from enum import Enum
import statistics
import weakref
import random
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)


class ConnectionState(Enum):
    """Connection lifecycle states"""
    CREATING = "creating"
    ACTIVE = "active"
    IDLE = "idle"
    AGING = "aging"
    STALE = "stale"
    RECYCLING = "recycling"
    DISPOSING = "disposing"
    DISPOSED = "disposed"


class ConnectionReuseStrategy(Enum):
    """Connection reuse strategies"""
    FIFO = "fifo"  # First In, First Out
    LIFO = "lifo"  # Last In, First Out
    LEAST_USED = "least_used"  # Least recently used
    BEST_PERFORMANCE = "best_performance"  # Best performing connection
    ROUND_ROBIN = "round_robin"  # Distribute load evenly


@dataclass
class ConnectionMetadata:
    """Metadata for a connection"""
    connection_id: str
    created_at: datetime
    last_used_at: datetime
    total_usage_count: int = 0
    total_usage_time_ms: float = 0.0
    avg_query_time_ms: float = 0.0
    error_count: int = 0
    ssl_handshakes: int = 0
    state: ConnectionState = ConnectionState.CREATING
    performance_score: float = 1.0
    context_affinity: Set[str] = field(default_factory=set)
    last_query_type: Optional[str] = None
    memory_footprint_mb: float = 0.0
    tcp_keepalives_sent: int = 0
    connection_weight: float = 1.0


@dataclass
class ConnectionUsagePattern:
    """Connection usage pattern analysis"""
    context: str
    avg_session_duration_ms: float
    typical_query_types: List[str]
    peak_usage_hours: List[int]
    preferred_connection_features: List[str]
    performance_requirements: Dict[str, Any]


@dataclass
class ConnectionOptimizationRecommendation:
    """Connection optimization recommendation"""
    recommendation_id: str
    optimization_type: str
    description: str
    expected_improvement: str
    implementation_priority: int  # 1-5, 1 = highest
    estimated_effort: str  # low, medium, high
    affects_connections: List[str]
    performance_impact: float  # 0.0 to 1.0


class ConnectionLifecycleOptimizer:
    """Advanced connection lifecycle management with intelligent optimization"""
    
    def __init__(self):
        # Configuration
        self.config = {
            # Lifecycle management
            'connection_max_age_hours': 4,
            'idle_timeout_minutes': 30,
            'stale_threshold_hours': 2,
            'recycling_threshold_minutes': 5,
            
            # Performance optimization
            'reuse_strategy': ConnectionReuseStrategy.BEST_PERFORMANCE,
            'context_affinity_threshold': 0.7,
            'performance_weight_factor': 0.3,
            'aging_grace_period_minutes': 10,
            
            # Monitoring and analysis
            'usage_pattern_analysis_interval': 300,  # 5 minutes
            'optimization_analysis_interval': 900,   # 15 minutes
            'cleanup_interval': 600,                 # 10 minutes
            
            # Advanced features
            'enable_predictive_warming': True,
            'enable_context_affinity': True,
            'enable_performance_scoring': True,
            'enable_intelligent_aging': True,
        }
        
        # Connection tracking
        self.connections = {}  # connection_id -> ConnectionMetadata
        self.connection_pools = {}  # pool_name -> pool_reference
        self.usage_patterns = {}  # context -> ConnectionUsagePattern
        self.optimization_history = deque(maxlen=100)
        
        # Performance and usage tracking
        self.performance_metrics = defaultdict(list)
        self.context_performance = defaultdict(dict)
        self.reuse_statistics = defaultdict(int)
        self.lifecycle_events = deque(maxlen=500)
        
        # Intelligent reuse management
        self.connection_queues = {
            strategy: deque() for strategy in ConnectionReuseStrategy
        }
        self.context_affinities = defaultdict(set)  # context -> set of connection_ids
        self.performance_rankings = deque(maxlen=50)
        
        # Threading and execution
        self._lifecycle_lock = threading.Lock()
        self._optimization_lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=3)
        self._running = True
        
        # Callbacks and notifications
        self.lifecycle_callbacks: List[Callable] = []
        self.optimization_callbacks: List[Callable] = []
        
        # Background processing
        asyncio.create_task(self._lifecycle_monitoring_loop())
        asyncio.create_task(self._usage_pattern_analysis_loop())
        asyncio.create_task(self._optimization_engine_loop())
        asyncio.create_task(self._connection_cleanup_loop())
        
        logger.info(
            "🔄 Connection Lifecycle Optimizer initialized with intelligent reuse patterns"
        )
    
    def register_connection_pool(self, pool_name: str, pool_instance):
        """Register a connection pool for lifecycle management"""
        with self._lifecycle_lock:
            self.connection_pools[pool_name] = weakref.ref(pool_instance)
            logger.info(f"📝 Registered connection pool: {pool_name}")
    
    def register_lifecycle_callback(self, callback: Callable):
        """Register callback for lifecycle events"""
        self.lifecycle_callbacks.append(callback)
    
    def register_optimization_callback(self, callback: Callable):
        """Register callback for optimization events"""
        self.optimization_callbacks.append(callback)
    
    def track_connection_creation(
        self, 
        connection_id: str, 
        context: str = "default",
        initial_metadata: Optional[Dict[str, Any]] = None
    ):
        """Track connection creation with lifecycle metadata"""
        with self._lifecycle_lock:
            metadata = ConnectionMetadata(
                connection_id=connection_id,
                created_at=datetime.utcnow(),
                last_used_at=datetime.utcnow(),
                state=ConnectionState.CREATING
            )
            
            # Apply initial metadata if provided
            if initial_metadata:
                for key, value in initial_metadata.items():
                    if hasattr(metadata, key):
                        setattr(metadata, key, value)
            
            self.connections[connection_id] = metadata
            
            # Add to reuse queue
            self._add_to_reuse_queues(connection_id)
            
            # Record lifecycle event
            self._record_lifecycle_event('connection_created', connection_id, context, {
                'creation_time': metadata.created_at.isoformat()
            })
            
            logger.debug(f"🔄 Tracking new connection: {connection_id} in context {context}")
    
    def track_connection_usage(
        self, 
        connection_id: str, 
        context: str,
        usage_duration_ms: float,
        query_type: Optional[str] = None,
        success: bool = True,
        memory_usage_mb: float = 0.0
    ):
        """Track connection usage for optimization"""
        with self._lifecycle_lock:
            if connection_id not in self.connections:
                logger.warning(f"Tracking usage for unknown connection: {connection_id}")
                return
            
            metadata = self.connections[connection_id]
            
            # Update usage statistics
            metadata.last_used_at = datetime.utcnow()
            metadata.total_usage_count += 1
            metadata.total_usage_time_ms += usage_duration_ms
            metadata.state = ConnectionState.ACTIVE
            
            if metadata.total_usage_count > 0:
                metadata.avg_query_time_ms = (
                    metadata.total_usage_time_ms / metadata.total_usage_count
                )
            
            if not success:
                metadata.error_count += 1
            
            if query_type:
                metadata.last_query_type = query_type
            
            if memory_usage_mb > 0:
                metadata.memory_footprint_mb = memory_usage_mb
            
            # Update context affinity
            metadata.context_affinity.add(context)
            self.context_affinities[context].add(connection_id)
            
            # Update performance score
            metadata.performance_score = self._calculate_performance_score(metadata)
            
            # Update reuse queues
            self._update_reuse_queues(connection_id)
            
            # Record usage metrics
            self.performance_metrics['usage_duration_ms'].append(usage_duration_ms)
            self.context_performance[context]['last_usage'] = usage_duration_ms
            self.reuse_statistics[f"{context}_usage"] += 1
            
            # Record lifecycle event
            self._record_lifecycle_event('connection_used', connection_id, context, {
                'usage_duration_ms': usage_duration_ms,
                'query_type': query_type,
                'success': success,
                'total_usage_count': metadata.total_usage_count
            })
            
            logger.debug(
                f"🔄 Connection usage tracked: {connection_id} "
                f"({usage_duration_ms:.1f}ms, score: {metadata.performance_score:.3f})"
            )
    
    def get_optimal_connection(
        self, 
        context: str, 
        requirements: Optional[Dict[str, Any]] = None
    ) -> Optional[str]:
        """Get the optimal connection for reuse based on intelligent selection"""
        with self._lifecycle_lock:
            if not self.connections:
                logger.debug("No connections available for reuse")
                return None
            
            # Get available connections (not in use, not stale)
            available_connections = [
                conn_id for conn_id, metadata in self.connections.items()
                if metadata.state in [ConnectionState.IDLE, ConnectionState.ACTIVE] and
                self._is_connection_healthy(metadata)
            ]
            
            if not available_connections:
                logger.debug("No healthy connections available for reuse")
                return None
            
            # Apply selection strategy
            selected_connection = self._select_connection_by_strategy(
                available_connections, context, requirements
            )
            
            if selected_connection:
                # Update connection state and statistics
                metadata = self.connections[selected_connection]
                metadata.state = ConnectionState.ACTIVE
                metadata.last_used_at = datetime.utcnow()
                
                self.reuse_statistics[f"{context}_reuse"] += 1
                self.reuse_statistics[f"strategy_{self.config['reuse_strategy'].value}"] += 1
                
                logger.debug(
                    f"🎯 Selected optimal connection {selected_connection} for {context} "
                    f"using {self.config['reuse_strategy'].value} strategy"
                )
            
            return selected_connection
    
    def _select_connection_by_strategy(
        self, 
        available_connections: List[str], 
        context: str,
        requirements: Optional[Dict[str, Any]] = None
    ) -> Optional[str]:
        """Select connection based on configured strategy"""
        if not available_connections:
            return None
        
        strategy = self.config['reuse_strategy']
        
        if strategy == ConnectionReuseStrategy.BEST_PERFORMANCE:
            return self._select_best_performance_connection(available_connections, context)
        
        elif strategy == ConnectionReuseStrategy.LEAST_USED:
            return self._select_least_used_connection(available_connections)
        
        elif strategy == ConnectionReuseStrategy.ROUND_ROBIN:
            return self._select_round_robin_connection(available_connections, context)
        
        elif strategy == ConnectionReuseStrategy.LIFO:
            # Last In, First Out - most recently used
            sorted_connections = sorted(
                available_connections,
                key=lambda cid: self.connections[cid].last_used_at,
                reverse=True
            )
            return sorted_connections[0]
        
        elif strategy == ConnectionReuseStrategy.FIFO:
            # First In, First Out - oldest connection
            sorted_connections = sorted(
                available_connections,
                key=lambda cid: self.connections[cid].created_at
            )
            return sorted_connections[0]
        
        else:
            # Default to random selection
            return random.choice(available_connections)
    
    def _select_best_performance_connection(
        self, 
        available_connections: List[str], 
        context: str
    ) -> str:
        """Select connection with best performance score and context affinity"""
        # Score connections based on performance and context affinity
        connection_scores = []
        
        for conn_id in available_connections:
            metadata = self.connections[conn_id]
            
            # Base performance score
            score = metadata.performance_score
            
            # Context affinity bonus
            if self.config['enable_context_affinity']:
                if context in metadata.context_affinity:
                    affinity_strength = len(metadata.context_affinity.intersection({context}))
                    affinity_bonus = affinity_strength * self.config['context_affinity_threshold']
                    score += affinity_bonus
            
            # Recent usage penalty (to encourage distribution)
            time_since_last_use = (
                datetime.utcnow() - metadata.last_used_at
            ).total_seconds()
            
            if time_since_last_use < 30:  # Used within last 30 seconds
                score *= 0.9  # Small penalty
            
            # Error penalty
            if metadata.error_count > 0:
                error_penalty = min(0.5, metadata.error_count * 0.1)
                score *= (1.0 - error_penalty)
            
            connection_scores.append((conn_id, score))
        
        # Select highest scoring connection
        best_connection = max(connection_scores, key=lambda x: x[1])
        return best_connection[0]
    
    def _select_least_used_connection(self, available_connections: List[str]) -> str:
        """Select connection with least usage"""
        least_used = min(
            available_connections,
            key=lambda cid: self.connections[cid].total_usage_count
        )
        return least_used
    
    def _select_round_robin_connection(
        self, 
        available_connections: List[str], 
        context: str
    ) -> str:
        """Select connection using round-robin for even distribution"""
        # Simple round-robin based on context hash
        context_hash = abs(hash(context + str(time.time() // 60)))  # Change every minute
        index = context_hash % len(available_connections)
        return available_connections[index]
    
    def _calculate_performance_score(self, metadata: ConnectionMetadata) -> float:
        """Calculate performance score for a connection"""
        base_score = 1.0
        
        # Factor in average query time (lower is better)
        if metadata.avg_query_time_ms > 0:
            # Normalize to 0-1 scale where 100ms = 0.5 score impact
            time_factor = min(1.0, metadata.avg_query_time_ms / 200.0)
            base_score *= (1.0 - time_factor * 0.5)
        
        # Factor in error rate
        if metadata.total_usage_count > 0:
            error_rate = metadata.error_count / metadata.total_usage_count
            base_score *= (1.0 - error_rate)
        
        # Factor in connection age (moderate penalty for very old connections)
        age_hours = (datetime.utcnow() - metadata.created_at).total_seconds() / 3600
        if age_hours > self.config['connection_max_age_hours']:
            age_penalty = min(0.3, (age_hours - self.config['connection_max_age_hours']) * 0.1)
            base_score *= (1.0 - age_penalty)
        
        # Factor in usage frequency (more used = higher score, up to a point)
        usage_bonus = min(0.2, metadata.total_usage_count * 0.01)
        base_score += usage_bonus
        
        return max(0.1, min(1.0, base_score))
    
    def _is_connection_healthy(self, metadata: ConnectionMetadata) -> bool:
        """Check if connection is healthy for reuse"""
        now = datetime.utcnow()
        
        # Check if connection is too old
        if (now - metadata.created_at).total_seconds() > self.config['connection_max_age_hours'] * 3600:
            return False
        
        # Check if connection has been idle too long
        if (now - metadata.last_used_at).total_seconds() > self.config['idle_timeout_minutes'] * 60:
            return False
        
        # Check if connection has too many errors
        if metadata.total_usage_count > 10 and metadata.error_count / metadata.total_usage_count > 0.2:
            return False
        
        # Check connection state
        if metadata.state in [ConnectionState.STALE, ConnectionState.DISPOSING, ConnectionState.DISPOSED]:
            return False
        
        return True
    
    def _add_to_reuse_queues(self, connection_id: str):
        """Add connection to appropriate reuse queues"""
        # Add to all strategy queues for flexibility
        for strategy in ConnectionReuseStrategy:
            self.connection_queues[strategy].append(connection_id)
    
    def _update_reuse_queues(self, connection_id: str):
        """Update connection position in reuse queues"""
        # For LIFO strategy, move to end (most recent)
        if connection_id in self.connection_queues[ConnectionReuseStrategy.LIFO]:
            self.connection_queues[ConnectionReuseStrategy.LIFO].remove(connection_id)
            self.connection_queues[ConnectionReuseStrategy.LIFO].append(connection_id)
    
    def _record_lifecycle_event(
        self, 
        event_type: str, 
        connection_id: str, 
        context: str,
        details: Dict[str, Any]
    ):
        """Record a lifecycle event for analysis"""
        event = {
            'timestamp': datetime.utcnow(),
            'event_type': event_type,
            'connection_id': connection_id,
            'context': context,
            'details': details
        }
        
        self.lifecycle_events.append(event)
        
        # Notify callbacks
        for callback in self.lifecycle_callbacks:
            try:
                callback(event)
            except Exception as e:
                logger.error(f"Error in lifecycle callback: {e}")
    
    def mark_connection_for_recycling(
        self, 
        connection_id: str, 
        reason: str = "scheduled_recycling"
    ):
        """Mark connection for recycling"""
        with self._lifecycle_lock:
            if connection_id in self.connections:
                metadata = self.connections[connection_id]
                metadata.state = ConnectionState.RECYCLING
                
                self._record_lifecycle_event('connection_recycling', connection_id, 'system', {
                    'reason': reason,
                    'connection_age_hours': (
                        datetime.utcnow() - metadata.created_at
                    ).total_seconds() / 3600
                })
                
                logger.debug(f"🔄 Marked connection {connection_id} for recycling: {reason}")
    
    def dispose_connection(self, connection_id: str, reason: str = "normal_disposal"):
        """Dispose of a connection"""
        with self._lifecycle_lock:
            if connection_id in self.connections:
                metadata = self.connections[connection_id]
                metadata.state = ConnectionState.DISPOSED
                
                # Remove from reuse queues
                for strategy in ConnectionReuseStrategy:
                    if connection_id in self.connection_queues[strategy]:
                        self.connection_queues[strategy].remove(connection_id)
                
                # Remove from context affinities
                for context, connections in self.context_affinities.items():
                    connections.discard(connection_id)
                
                self._record_lifecycle_event('connection_disposed', connection_id, 'system', {
                    'reason': reason,
                    'total_usage_count': metadata.total_usage_count,
                    'total_usage_time_ms': metadata.total_usage_time_ms,
                    'connection_lifetime_hours': (
                        datetime.utcnow() - metadata.created_at
                    ).total_seconds() / 3600
                })
                
                # Clean up metadata
                del self.connections[connection_id]
                
                logger.debug(f"🗑️ Disposed connection {connection_id}: {reason}")
    
    async def _lifecycle_monitoring_loop(self):
        """Monitor connection lifecycle and apply aging strategies"""
        logger.info("🔄 Starting connection lifecycle monitoring...")
        
        while self._running:
            try:
                await asyncio.sleep(60)  # Check every minute
                
                await self._apply_aging_strategies()
                await self._identify_stale_connections()
                await self._optimize_connection_distribution()
                
            except Exception as e:
                logger.error(f"Error in lifecycle monitoring: {e}")
                await asyncio.sleep(120)
    
    async def _apply_aging_strategies(self):
        """Apply intelligent aging strategies to connections"""
        if not self.config['enable_intelligent_aging']:
            return
        
        now = datetime.utcnow()
        aged_connections = []
        
        with self._lifecycle_lock:
            for conn_id, metadata in self.connections.items():
                connection_age = (now - metadata.created_at).total_seconds() / 3600
                
                # Mark connections as aging if they're old but still active
                if (connection_age > self.config['connection_max_age_hours'] * 0.8 and
                    metadata.state == ConnectionState.ACTIVE):
                    
                    metadata.state = ConnectionState.AGING
                    aged_connections.append(conn_id)
                    
                    logger.debug(
                        f"🕰️ Connection {conn_id} entering aging state "
                        f"(age: {connection_age:.1f}h)"
                    )
        
        # Apply aging strategies
        for conn_id in aged_connections:
            self._record_lifecycle_event('connection_aging', conn_id, 'system', {
                'aging_strategy': 'graceful_retirement'
            })
    
    async def _identify_stale_connections(self):
        """Identify and mark stale connections"""
        now = datetime.utcnow()
        stale_connections = []
        
        with self._lifecycle_lock:
            for conn_id, metadata in self.connections.items():
                # Check for stale conditions
                idle_time = (now - metadata.last_used_at).total_seconds() / 3600
                
                if (idle_time > self.config['stale_threshold_hours'] and
                    metadata.state not in [ConnectionState.STALE, ConnectionState.DISPOSED]):
                    
                    metadata.state = ConnectionState.STALE
                    stale_connections.append(conn_id)
                    
                    logger.debug(
                        f"🥀 Connection {conn_id} marked as stale "
                        f"(idle: {idle_time:.1f}h)"
                    )
        
        # Schedule stale connections for recycling
        for conn_id in stale_connections:
            self.mark_connection_for_recycling(conn_id, "stale_connection")
    
    async def _optimize_connection_distribution(self):
        """Optimize connection distribution across contexts"""
        if not self.config['enable_context_affinity']:
            return
        
        # Analyze context usage patterns
        context_usage = defaultdict(int)
        context_performance = defaultdict(list)
        
        with self._lifecycle_lock:
            for conn_id, metadata in self.connections.items():
                for context in metadata.context_affinity:
                    context_usage[context] += metadata.total_usage_count
                    context_performance[context].append(metadata.performance_score)
        
        # Identify optimization opportunities
        optimizations = []
        
        for context, usage_count in context_usage.items():
            if usage_count > 0:
                avg_performance = statistics.mean(context_performance[context])
                
                if avg_performance < 0.7:  # Poor performance threshold
                    optimizations.append({
                        'type': 'context_performance_optimization',
                        'context': context,
                        'current_performance': avg_performance,
                        'recommendation': 'Consider dedicated high-performance connections'
                    })
        
        # Log optimization opportunities
        for opt in optimizations:
            logger.info(
                f"🎯 Optimization opportunity: {opt['type']} for {opt['context']} "
                f"(performance: {opt['current_performance']:.3f})"
            )
    
    async def _usage_pattern_analysis_loop(self):
        """Analyze usage patterns for optimization"""
        logger.info("📊 Starting usage pattern analysis...")
        
        while self._running:
            try:
                await asyncio.sleep(self.config['usage_pattern_analysis_interval'])
                
                # Analyze current usage patterns
                patterns = await self._analyze_usage_patterns()
                
                # Update usage pattern database
                for context, pattern in patterns.items():
                    self.usage_patterns[context] = pattern
                    
                    logger.debug(
                        f"📈 Updated usage pattern for {context}: "
                        f"avg_duration={pattern.avg_session_duration_ms:.1f}ms"
                    )
                
            except Exception as e:
                logger.error(f"Error in usage pattern analysis: {e}")
                await asyncio.sleep(600)
    
    async def _analyze_usage_patterns(self) -> Dict[str, ConnectionUsagePattern]:
        """Analyze connection usage patterns by context"""
        patterns = {}
        
        # Analyze recent lifecycle events
        recent_events = [
            event for event in list(self.lifecycle_events)
            if (datetime.utcnow() - event['timestamp']).total_seconds() < 3600  # Last hour
        ]
        
        # Group by context
        context_events = defaultdict(list)
        for event in recent_events:
            context_events[event['context']].append(event)
        
        # Analyze each context
        for context, events in context_events.items():
            usage_events = [e for e in events if e['event_type'] == 'connection_used']
            
            if usage_events:
                # Calculate average session duration
                durations = [
                    e['details'].get('usage_duration_ms', 0) 
                    for e in usage_events 
                    if 'usage_duration_ms' in e['details']
                ]
                
                avg_duration = statistics.mean(durations) if durations else 0.0
                
                # Extract query types
                query_types = [
                    e['details'].get('query_type', 'unknown')
                    for e in usage_events
                    if e['details'].get('query_type')
                ]
                
                # Determine peak hours
                hour_usage = defaultdict(int)
                for event in usage_events:
                    hour = event['timestamp'].hour
                    hour_usage[hour] += 1
                
                peak_hours = sorted(
                    hour_usage.keys(), 
                    key=lambda h: hour_usage[h], 
                    reverse=True
                )[:3]
                
                patterns[context] = ConnectionUsagePattern(
                    context=context,
                    avg_session_duration_ms=avg_duration,
                    typical_query_types=list(set(query_types)),
                    peak_usage_hours=peak_hours,
                    preferred_connection_features=[],  # To be enhanced
                    performance_requirements={'avg_response_time_ms': avg_duration}
                )
        
        return patterns
    
    async def _optimization_engine_loop(self):
        """Main optimization engine loop"""
        logger.info("🎯 Starting connection optimization engine...")
        
        while self._running:
            try:
                await asyncio.sleep(self.config['optimization_analysis_interval'])
                
                # Generate optimization recommendations
                recommendations = await self._generate_optimization_recommendations()
                
                # Apply automatic optimizations
                applied_optimizations = await self._apply_automatic_optimizations(recommendations)
                
                # Store recommendations
                for rec in recommendations:
                    self.optimization_history.append({
                        'timestamp': datetime.utcnow(),
                        'recommendation': rec,
                        'auto_applied': rec.recommendation_id in applied_optimizations
                    })
                
                if recommendations:
                    logger.info(f"🎯 Generated {len(recommendations)} optimization recommendations")
                
                # Notify callbacks
                for callback in self.optimization_callbacks:
                    try:
                        callback(recommendations)
                    except Exception as e:
                        logger.error(f"Error in optimization callback: {e}")
                
            except Exception as e:
                logger.error(f"Error in optimization engine: {e}")
                await asyncio.sleep(900)
    
    async def _generate_optimization_recommendations(self) -> List[ConnectionOptimizationRecommendation]:
        """Generate intelligent optimization recommendations"""
        recommendations = []
        
        with self._lifecycle_lock:
            # Analyze connection performance
            low_performance_connections = [
                conn_id for conn_id, metadata in self.connections.items()
                if metadata.performance_score < 0.6
            ]
            
            if low_performance_connections:
                recommendations.append(ConnectionOptimizationRecommendation(
                    recommendation_id=f"perf_opt_{int(time.time())}",
                    optimization_type="performance_improvement",
                    description=f"Replace {len(low_performance_connections)} low-performing connections",
                    expected_improvement="15-25% reduction in query response time",
                    implementation_priority=2,
                    estimated_effort="low",
                    affects_connections=low_performance_connections,
                    performance_impact=0.20
                ))
            
            # Analyze connection age distribution
            aged_connections = [
                conn_id for conn_id, metadata in self.connections.items()
                if (datetime.utcnow() - metadata.created_at).total_seconds() > 
                self.config['connection_max_age_hours'] * 3600 * 0.9
            ]
            
            if len(aged_connections) > len(self.connections) * 0.3:  # >30% are aged
                recommendations.append(ConnectionOptimizationRecommendation(
                    recommendation_id=f"age_opt_{int(time.time())}",
                    optimization_type="connection_refresh",
                    description="Refresh aging connection pool to improve performance",
                    expected_improvement="10-15% improvement in connection reliability",
                    implementation_priority=3,
                    estimated_effort="medium",
                    affects_connections=aged_connections,
                    performance_impact=0.12
                ))
            
            # Analyze context affinity optimization
            if self.config['enable_context_affinity']:
                contexts_without_affinity = []
                for context in self.context_affinities:
                    if len(self.context_affinities[context]) < 2:  # Very few dedicated connections
                        contexts_without_affinity.append(context)
                
                if contexts_without_affinity:
                    recommendations.append(ConnectionOptimizationRecommendation(
                        recommendation_id=f"affinity_opt_{int(time.time())}",
                        optimization_type="context_affinity_optimization",
                        description=f"Optimize context affinity for {len(contexts_without_affinity)} contexts",
                        expected_improvement="5-10% improvement in context-specific performance",
                        implementation_priority=4,
                        estimated_effort="low",
                        affects_connections=[],
                        performance_impact=0.08
                    ))
        
        return recommendations
    
    async def _apply_automatic_optimizations(
        self, 
        recommendations: List[ConnectionOptimizationRecommendation]
    ) -> List[str]:
        """Apply optimization recommendations automatically where appropriate"""
        applied = []
        
        for rec in recommendations:
            # Only auto-apply low-effort, high-priority optimizations
            if rec.implementation_priority <= 2 and rec.estimated_effort == "low":
                try:
                    success = await self._execute_optimization(rec)
                    if success:
                        applied.append(rec.recommendation_id)
                        logger.info(f"✅ Auto-applied optimization: {rec.description}")
                    else:
                        logger.warning(f"❌ Failed to auto-apply optimization: {rec.description}")
                        
                except Exception as e:
                    logger.error(f"Error applying optimization {rec.recommendation_id}: {e}")
        
        return applied
    
    async def _execute_optimization(self, recommendation: ConnectionOptimizationRecommendation) -> bool:
        """Execute a specific optimization recommendation"""
        try:
            if recommendation.optimization_type == "performance_improvement":
                # Mark low-performing connections for recycling
                for conn_id in recommendation.affects_connections:
                    self.mark_connection_for_recycling(conn_id, "performance_optimization")
                return True
            
            elif recommendation.optimization_type == "connection_refresh":
                # Gradually refresh aged connections
                connections_to_refresh = recommendation.affects_connections[:5]  # Limit batch size
                for conn_id in connections_to_refresh:
                    self.mark_connection_for_recycling(conn_id, "scheduled_refresh")
                return True
            
            elif recommendation.optimization_type == "context_affinity_optimization":
                # This would require coordination with pool managers
                logger.debug("Context affinity optimization scheduled for next pool scaling event")
                return True
            
            else:
                logger.warning(f"Unknown optimization type: {recommendation.optimization_type}")
                return False
                
        except Exception as e:
            logger.error(f"Error executing optimization: {e}")
            return False
    
    async def _connection_cleanup_loop(self):
        """Clean up disposed connections and maintain data structures"""
        logger.info("🧹 Starting connection cleanup loop...")
        
        while self._running:
            try:
                await asyncio.sleep(self.config['cleanup_interval'])
                
                # Clean up old lifecycle events
                cutoff_time = datetime.utcnow() - timedelta(hours=24)
                old_events_count = len(self.lifecycle_events)
                
                self.lifecycle_events = deque([
                    event for event in self.lifecycle_events
                    if event['timestamp'] >= cutoff_time
                ], maxlen=500)
                
                cleaned_events = old_events_count - len(self.lifecycle_events)
                
                # Clean up performance metrics
                for metric_type in list(self.performance_metrics.keys()):
                    if len(self.performance_metrics[metric_type]) > 1000:
                        self.performance_metrics[metric_type] = self.performance_metrics[metric_type][-500:]
                
                # Clean up empty context affinities
                empty_contexts = [
                    context for context, connections in self.context_affinities.items()
                    if not connections
                ]
                
                for context in empty_contexts:
                    del self.context_affinities[context]
                
                logger.debug(
                    f"🧹 Cleanup completed: {cleaned_events} old events, "
                    f"{len(empty_contexts)} empty contexts"
                )
                
            except Exception as e:
                logger.error(f"Error in cleanup loop: {e}")
                await asyncio.sleep(600)
    
    def get_lifecycle_statistics(self) -> Dict[str, Any]:
        """Get comprehensive lifecycle statistics"""
        with self._lifecycle_lock:
            now = datetime.utcnow()
            
            # Connection state distribution
            state_distribution = defaultdict(int)
            for metadata in self.connections.values():
                state_distribution[metadata.state.value] += 1
            
            # Performance statistics
            performance_scores = [m.performance_score for m in self.connections.values()]
            avg_performance = statistics.mean(performance_scores) if performance_scores else 0.0
            
            # Age statistics
            connection_ages = [
                (now - m.created_at).total_seconds() / 3600
                for m in self.connections.values()
            ]
            
            avg_age_hours = statistics.mean(connection_ages) if connection_ages else 0.0
            
            # Usage statistics
            total_usage = sum(m.total_usage_count for m in self.connections.values())
            
            return {
                'timestamp': now.isoformat(),
                'total_connections': len(self.connections),
                'connection_states': dict(state_distribution),
                'performance_metrics': {
                    'avg_performance_score': round(avg_performance, 3),
                    'min_performance_score': round(min(performance_scores), 3) if performance_scores else 0,
                    'max_performance_score': round(max(performance_scores), 3) if performance_scores else 0,
                },
                'age_statistics': {
                    'avg_age_hours': round(avg_age_hours, 2),
                    'max_age_hours': round(max(connection_ages), 2) if connection_ages else 0,
                    'connections_over_max_age': len([
                        age for age in connection_ages 
                        if age > self.config['connection_max_age_hours']
                    ])
                },
                'usage_statistics': {
                    'total_usage_count': total_usage,
                    'avg_usage_per_connection': round(total_usage / max(len(self.connections), 1), 2),
                    'reuse_statistics': dict(self.reuse_statistics),
                    'context_affinities': {
                        context: len(connections)
                        for context, connections in self.context_affinities.items()
                    }
                },
                'optimization_info': {
                    'current_strategy': self.config['reuse_strategy'].value,
                    'optimization_history_count': len(self.optimization_history),
                    'lifecycle_events_count': len(self.lifecycle_events)
                },
                'configuration': {
                    key: value.value if isinstance(value, Enum) else value
                    for key, value in self.config.items()
                }
            }
    
    def get_optimization_recommendations(self) -> List[Dict[str, Any]]:
        """Get current optimization recommendations"""
        recent_recommendations = []
        cutoff_time = datetime.utcnow() - timedelta(hours=1)
        
        for entry in list(self.optimization_history):
            if entry['timestamp'] >= cutoff_time:
                rec = entry['recommendation']
                recent_recommendations.append({
                    'timestamp': entry['timestamp'].isoformat(),
                    'recommendation_id': rec.recommendation_id,
                    'optimization_type': rec.optimization_type,
                    'description': rec.description,
                    'expected_improvement': rec.expected_improvement,
                    'implementation_priority': rec.implementation_priority,
                    'estimated_effort': rec.estimated_effort,
                    'performance_impact': rec.performance_impact,
                    'auto_applied': entry['auto_applied']
                })
        
        return recent_recommendations
    
    def shutdown(self):
        """Shutdown the lifecycle optimizer"""
        logger.info("🔄 Shutting down Connection Lifecycle Optimizer...")
        self._running = False
        self._executor.shutdown(wait=True)


# Global lifecycle optimizer instance
lifecycle_optimizer = ConnectionLifecycleOptimizer()


# Convenience functions
def track_connection_creation(connection_id: str, context: str = "default", metadata: Optional[Dict] = None):
    """Track connection creation"""
    lifecycle_optimizer.track_connection_creation(connection_id, context, metadata)


def track_connection_usage(connection_id: str, context: str, duration_ms: float, success: bool = True):
    """Track connection usage"""
    lifecycle_optimizer.track_connection_usage(connection_id, context, duration_ms, success=success)


def get_optimal_connection(context: str, requirements: Optional[Dict] = None) -> Optional[str]:
    """Get optimal connection for reuse"""
    return lifecycle_optimizer.get_optimal_connection(context, requirements)


def get_lifecycle_stats() -> Dict[str, Any]:
    """Get lifecycle statistics"""
    return lifecycle_optimizer.get_lifecycle_statistics()


def register_pool_for_lifecycle_management(pool_name: str, pool_instance):
    """Register a pool for lifecycle management"""
    lifecycle_optimizer.register_connection_pool(pool_name, pool_instance)