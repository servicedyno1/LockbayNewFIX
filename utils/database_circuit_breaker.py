"""
Database Circuit Breaker for Webhook Processing
Implements circuit breaker pattern to prevent cascade failures during database outages
"""

import time
import logging
import threading
from typing import Callable, Any, Dict
from enum import Enum
from dataclasses import dataclass
from contextlib import contextmanager
from collections import deque

logger = logging.getLogger(__name__)


class CircuitBreakerState(Enum):
    """Circuit breaker states"""
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Circuit breaker tripped, rejecting calls
    HALF_OPEN = "half_open"  # Testing if service has recovered


@dataclass
class CircuitBreakerConfig:
    """Circuit breaker configuration"""
    failure_threshold: int = 5           # Number of failures to trip circuit
    recovery_timeout: int = 60           # Seconds before attempting recovery
    success_threshold: int = 3           # Consecutive successes to close circuit
    timeout: float = 30.0               # Operation timeout in seconds
    expected_exception: type = Exception  # Exception type that counts as failure


class DatabaseCircuitBreaker:
    """
    Circuit breaker for database operations with enhanced webhook-specific features.
    
    Features:
    - Automatic failure detection and recovery
    - Configurable thresholds and timeouts
    - Comprehensive metrics and monitoring
    - Thread-safe operation
    - Fallback mode support
    """
    
    def __init__(self, name: str, config: CircuitBreakerConfig = None):
        self.name = name
        self.config = config or CircuitBreakerConfig()
        
        # Circuit breaker state
        self._state = CircuitBreakerState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time = 0
        self._state_change_time = time.time()
        
        # Thread safety
        self._lock = threading.RLock()
        
        # Metrics and monitoring
        self._metrics = {
            'total_calls': 0,
            'successful_calls': 0,
            'failed_calls': 0,
            'rejected_calls': 0,
            'state_changes': 0,
            'average_response_time_ms': 0.0,
            'last_error': None,
            'last_error_time': None
        }
        
        # Recent failures for debugging
        self._recent_failures = deque(maxlen=10)
        
        logger.info(f"✅ CIRCUIT_BREAKER: Initialized '{name}' with threshold={config.failure_threshold}, "
                   f"timeout={config.recovery_timeout}s")
    
    @property
    def state(self) -> CircuitBreakerState:
        """Get current circuit breaker state"""
        with self._lock:
            return self._state
    
    @property
    def is_closed(self) -> bool:
        """Check if circuit is closed (normal operation)"""
        return self.state == CircuitBreakerState.CLOSED
    
    @property
    def is_open(self) -> bool:
        """Check if circuit is open (failing fast)"""
        return self.state == CircuitBreakerState.OPEN
    
    @property
    def is_half_open(self) -> bool:
        """Check if circuit is half-open (testing recovery)"""
        return self.state == CircuitBreakerState.HALF_OPEN
    
    def call(self, func: Callable, *args, **kwargs) -> Any:
        """
        Execute function with circuit breaker protection.
        
        Args:
            func: Function to execute
            *args, **kwargs: Arguments to pass to function
            
        Returns:
            Function result
            
        Raises:
            CircuitBreakerOpenError: When circuit is open
            Original exception: When function fails
        """
        with self._lock:
            self._metrics['total_calls'] += 1
            
            # Check if we should reject the call
            if self._should_reject_call():
                self._metrics['rejected_calls'] += 1
                raise CircuitBreakerOpenError(
                    f"Circuit breaker '{self.name}' is OPEN. "
                    f"State: {self._state.value}, Failures: {self._failure_count}"
                )
            
            # Attempt to transition to half-open if needed
            self._attempt_reset()
        
        # Execute the function
        start_time = time.time()
        try:
            result = func(*args, **kwargs)
            
            # Record success
            execution_time_ms = (time.time() - start_time) * 1000
            self._record_success(execution_time_ms)
            
            return result
            
        except self.config.expected_exception as e:
            # Record failure
            execution_time_ms = (time.time() - start_time) * 1000
            self._record_failure(e, execution_time_ms)
            raise
    
    @contextmanager
    def protect(self):
        """
        Context manager for protecting code blocks.
        
        Usage:
            with circuit_breaker.protect():
                # database operation
                result = db.query(...)
        """
        if self._should_reject_call():
            with self._lock:
                self._metrics['rejected_calls'] += 1
            raise CircuitBreakerOpenError(
                f"Circuit breaker '{self.name}' is OPEN"
            )
        
        self._attempt_reset()
        
        start_time = time.time()
        try:
            with self._lock:
                self._metrics['total_calls'] += 1
            
            yield
            
            # Record success
            execution_time_ms = (time.time() - start_time) * 1000
            self._record_success(execution_time_ms)
            
        except self.config.expected_exception as e:
            # Record failure
            execution_time_ms = (time.time() - start_time) * 1000
            self._record_failure(e, execution_time_ms)
            raise
    
    def _should_reject_call(self) -> bool:
        """Check if we should reject the call due to circuit breaker state"""
        return self._state == CircuitBreakerState.OPEN
    
    def _attempt_reset(self):
        """Attempt to reset circuit breaker if recovery timeout has passed"""
        with self._lock:
            if (self._state == CircuitBreakerState.OPEN and 
                time.time() - self._last_failure_time >= self.config.recovery_timeout):
                
                logger.info(f"🔄 CIRCUIT_BREAKER: '{self.name}' transitioning to HALF_OPEN for recovery test")
                self._set_state(CircuitBreakerState.HALF_OPEN)
    
    def _record_success(self, execution_time_ms: float):
        """Record successful operation"""
        with self._lock:
            self._metrics['successful_calls'] += 1
            self._update_average_response_time(execution_time_ms)
            
            if self._state == CircuitBreakerState.HALF_OPEN:
                self._success_count += 1
                logger.debug(f"🔄 CIRCUIT_BREAKER: '{self.name}' recovery success "
                           f"{self._success_count}/{self.config.success_threshold}")
                
                if self._success_count >= self.config.success_threshold:
                    logger.info(f"✅ CIRCUIT_BREAKER: '{self.name}' CLOSED - recovery successful")
                    self._set_state(CircuitBreakerState.CLOSED)
                    self._failure_count = 0
                    self._success_count = 0
            
            elif self._state == CircuitBreakerState.CLOSED:
                # Reset failure count on successful operation in closed state
                if self._failure_count > 0:
                    self._failure_count = max(0, self._failure_count - 1)
    
    def _record_failure(self, error: Exception, execution_time_ms: float):
        """Record failed operation"""
        with self._lock:
            self._metrics['failed_calls'] += 1
            self._metrics['last_error'] = str(error)
            self._metrics['last_error_time'] = time.time()
            self._update_average_response_time(execution_time_ms)
            
            # Add to recent failures
            self._recent_failures.append({
                'error': str(error),
                'timestamp': time.time(),
                'execution_time_ms': execution_time_ms
            })
            
            self._failure_count += 1
            self._last_failure_time = time.time()
            
            logger.warning(f"❌ CIRCUIT_BREAKER: '{self.name}' failure {self._failure_count} - {error}")
            
            # Check if we should trip the circuit
            if (self._state == CircuitBreakerState.CLOSED and 
                self._failure_count >= self.config.failure_threshold):
                
                logger.critical(f"🚨 CIRCUIT_BREAKER: '{self.name}' OPENED after {self._failure_count} failures")
                self._set_state(CircuitBreakerState.OPEN)
                
            elif (self._state == CircuitBreakerState.HALF_OPEN):
                logger.warning(f"🔄 CIRCUIT_BREAKER: '{self.name}' recovery failed, returning to OPEN")
                self._set_state(CircuitBreakerState.OPEN)
                self._success_count = 0
    
    def _set_state(self, new_state: CircuitBreakerState):
        """Change circuit breaker state"""
        if self._state != new_state:
            old_state = self._state
            self._state = new_state
            self._state_change_time = time.time()
            self._metrics['state_changes'] += 1
            
            logger.info(f"🔄 CIRCUIT_BREAKER: '{self.name}' state change: {old_state.value} → {new_state.value}")
    
    def _update_average_response_time(self, execution_time_ms: float):
        """Update average response time"""
        current_avg = self._metrics['average_response_time_ms']
        total_calls = self._metrics['total_calls']
        
        if total_calls == 1:
            self._metrics['average_response_time_ms'] = execution_time_ms
        else:
            self._metrics['average_response_time_ms'] = (
                (current_avg * (total_calls - 1) + execution_time_ms) / total_calls
            )
    
    def reset(self):
        """Manually reset circuit breaker to closed state"""
        with self._lock:
            logger.info(f"🔄 CIRCUIT_BREAKER: '{self.name}' manually reset to CLOSED")
            self._set_state(CircuitBreakerState.CLOSED)
            self._failure_count = 0
            self._success_count = 0
            self._last_failure_time = 0
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get circuit breaker metrics"""
        with self._lock:
            return {
                'name': self.name,
                'state': self._state.value,
                'failure_count': self._failure_count,
                'success_count': self._success_count,
                'last_failure_time': self._last_failure_time,
                'state_change_time': self._state_change_time,
                'time_in_current_state': time.time() - self._state_change_time,
                'config': {
                    'failure_threshold': self.config.failure_threshold,
                    'recovery_timeout': self.config.recovery_timeout,
                    'success_threshold': self.config.success_threshold,
                    'timeout': self.config.timeout
                },
                'metrics': self._metrics.copy(),
                'recent_failures': list(self._recent_failures)
            }


class CircuitBreakerOpenError(Exception):
    """Exception raised when circuit breaker is open"""
    pass


class DatabaseResilienceManager:
    """
    Manages multiple circuit breakers for different database operations.
    Provides centralized configuration and monitoring for webhook database resilience.
    """
    
    def __init__(self):
        self.circuit_breakers: Dict[str, DatabaseCircuitBreaker] = {}
        self._global_metrics = {
            'total_breakers': 0,
            'open_breakers': 0,
            'failed_operations': 0,
            'successful_operations': 0
        }
        
        # Create specific circuit breakers for webhook operations
        self._init_webhook_circuit_breakers()
        
        logger.info("✅ DATABASE_RESILIENCE: Manager initialized with webhook-specific circuit breakers")
    
    def _init_webhook_circuit_breakers(self):
        """Initialize circuit breakers for webhook-specific database operations"""
        
        # Webhook processing circuit breaker (more tolerant)
        webhook_config = CircuitBreakerConfig(
            failure_threshold=3,      # Trip after 3 failures (webhooks are time-sensitive)
            recovery_timeout=30,      # Try recovery after 30 seconds
            success_threshold=2,      # Only need 2 successes to close
            timeout=45.0             # 45 second timeout for webhook operations
        )
        
        # General database circuit breaker (standard tolerance)
        general_config = CircuitBreakerConfig(
            failure_threshold=5,      # Standard threshold
            recovery_timeout=60,      # Standard recovery time
            success_threshold=3,      # Standard success requirement
            timeout=30.0             # Standard timeout
        )
        
        # Critical operations circuit breaker (very tolerant)
        critical_config = CircuitBreakerConfig(
            failure_threshold=10,     # Very high threshold for critical ops
            recovery_timeout=120,     # Longer recovery time
            success_threshold=5,      # More successes required
            timeout=60.0             # Longer timeout
        )
        
        self.circuit_breakers = {
            'webhook_processing': DatabaseCircuitBreaker('webhook_processing', webhook_config),
            'general_database': DatabaseCircuitBreaker('general_database', general_config),
            'critical_operations': DatabaseCircuitBreaker('critical_operations', critical_config),
            'payment_processing': DatabaseCircuitBreaker('payment_processing', webhook_config),  # Same as webhook
            'user_operations': DatabaseCircuitBreaker('user_operations', general_config)
        }
        
        self._global_metrics['total_breakers'] = len(self.circuit_breakers)
    
    def get_circuit_breaker(self, operation_type: str) -> DatabaseCircuitBreaker:
        """Get circuit breaker for specific operation type"""
        # Map operation types to circuit breakers
        operation_mapping = {
            'webhook': 'webhook_processing',
            'webhook_processing': 'webhook_processing',
            'payment': 'payment_processing',
            'payment_processing': 'payment_processing',
            'critical': 'critical_operations',
            'critical_operations': 'critical_operations',
            'user': 'user_operations',
            'user_operations': 'user_operations',
            'general': 'general_database',
            'default': 'general_database'
        }
        
        breaker_name = operation_mapping.get(operation_type, 'general_database')
        return self.circuit_breakers[breaker_name]
    
    def protect_database_operation(self, operation_type: str = 'general'):
        """Get context manager for protecting database operations"""
        circuit_breaker = self.get_circuit_breaker(operation_type)
        return circuit_breaker.protect()
    
    def is_database_available(self, operation_type: str = 'general') -> bool:
        """Check if database is available for specific operation type"""
        circuit_breaker = self.get_circuit_breaker(operation_type)
        return circuit_breaker.is_closed or circuit_breaker.is_half_open
    
    def reset_all_circuit_breakers(self):
        """Reset all circuit breakers to closed state"""
        for name, breaker in self.circuit_breakers.items():
            breaker.reset()
        logger.info("🔄 DATABASE_RESILIENCE: All circuit breakers reset")
    
    def get_global_status(self) -> Dict[str, Any]:
        """Get overall status of all circuit breakers"""
        open_count = sum(1 for breaker in self.circuit_breakers.values() if breaker.is_open)
        
        status = {
            'overall_status': 'healthy' if open_count == 0 else 'degraded' if open_count < len(self.circuit_breakers) else 'critical',
            'total_breakers': len(self.circuit_breakers),
            'open_breakers': open_count,
            'closed_breakers': sum(1 for breaker in self.circuit_breakers.values() if breaker.is_closed),
            'half_open_breakers': sum(1 for breaker in self.circuit_breakers.values() if breaker.is_half_open),
            'breaker_details': {name: breaker.get_metrics() for name, breaker in self.circuit_breakers.items()},
            'global_metrics': self._global_metrics.copy(),
            'timestamp': time.time()
        }
        
        return status


# Global instance for use throughout the application
database_resilience_manager = DatabaseResilienceManager()