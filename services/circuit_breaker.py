"""
Circuit Breaker Pattern for External API Calls
Prevents cascading failures and protects system stability
"""

import asyncio
import logging
from typing import Any, Callable, Optional, Dict
from enum import Enum
from functools import wraps
from datetime import datetime

from services.state_manager import state_manager

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Blocking calls due to failures
    HALF_OPEN = "half_open"  # Testing if service recovered


class CircuitBreaker:
    """
    Redis-backed circuit breaker implementation for external API calls
    
    States:
    - CLOSED: Normal operation, requests pass through
    - OPEN: Too many failures, requests blocked
    - HALF_OPEN: Testing recovery with limited requests
    """
    
    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
        expected_exception: type = Exception,
        use_atomic_locking: bool = True
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception
        self.use_atomic_locking = use_atomic_locking  # Use atomic locking for critical operations
        
        # Redis keys for distributed state (fallback)
        self.state_key = f"circuit_breaker:{name}:state"
        self.stats_key = f"circuit_breaker:{name}:stats"
        
        # Default stats structure
        self.default_stats = {
            'total_calls': 0,
            'successful_calls': 0,
            'failed_calls': 0,
            'blocked_calls': 0
        }
        
        # Default state structure
        self.default_state = {
            'state': CircuitState.CLOSED.value,
            'failure_count': 0,
            'last_failure_time': None,
            'half_open_attempts': 0,
            'created_at': datetime.utcnow().isoformat(),
            'updated_at': datetime.utcnow().isoformat()
        }
    
    async def call(self, func: Callable, *args, **kwargs) -> Any:
        """Execute function with circuit breaker protection"""
        await self._increment_stat('total_calls')
        
        state_data = await self._get_state()
        current_state = CircuitState(state_data['state'])
        
        if current_state == CircuitState.OPEN:
            if await self._should_attempt_reset(state_data):
                await self._set_state(
                    CircuitState.HALF_OPEN, 
                    state_data['failure_count'],
                    half_open_attempts=0
                )
                logger.info(f"Circuit {self.name} entering HALF_OPEN state")
            else:
                await self._increment_stat('blocked_calls')
                raise Exception(f"Circuit breaker {self.name} is OPEN")
        
        try:
            # Check if function is async and handle appropriately
            if asyncio.iscoroutinefunction(func):
                result = await func(*args, **kwargs)
            else:
                # Wrap synchronous function call with asyncio.to_thread() to prevent event loop blocking
                result = await asyncio.to_thread(func, *args, **kwargs)
            await self._on_success()
            return result
        except self.expected_exception as e:
            await self._on_failure()
            raise e
    
    async def async_call(self, func: Callable, *args, **kwargs) -> Any:
        """Execute async function with circuit breaker protection"""
        await self._increment_stat('total_calls')
        
        state_data = await self._get_state()
        current_state = CircuitState(state_data['state'])
        
        if current_state == CircuitState.OPEN:
            if await self._should_attempt_reset(state_data):
                await self._set_state(
                    CircuitState.HALF_OPEN, 
                    state_data['failure_count'],
                    half_open_attempts=0
                )
                logger.info(f"Circuit {self.name} entering HALF_OPEN state")
            else:
                await self._increment_stat('blocked_calls')
                raise Exception(f"Circuit breaker {self.name} is OPEN. Service unavailable.")
        
        try:
            result = await func(*args, **kwargs)
            await self._on_success()
            return result
        except self.expected_exception as e:
            await self._on_failure()
            raise e
    
    async def _should_attempt_reset(self, state_data: Dict) -> bool:
        """Check if enough time has passed to attempt reset"""
        last_failure_time = state_data.get('last_failure_time')
        if last_failure_time is None:
            return False
        
        # Convert ISO string back to timestamp for comparison
        try:
            from datetime import datetime
            last_failure_dt = datetime.fromisoformat(last_failure_time)
            current_time = datetime.utcnow()
            time_passed = (current_time - last_failure_dt).total_seconds()
            return time_passed >= self.recovery_timeout
        except (ValueError, TypeError):
            logger.warning(f"Invalid last_failure_time format: {last_failure_time}")
            return True  # Allow reset attempt if timestamp is invalid
    
    async def _on_success(self):
        """Handle successful call"""
        await self._increment_stat('successful_calls')
        
        state_data = await self._get_state()
        current_state = CircuitState(state_data['state'])
        
        if current_state == CircuitState.HALF_OPEN:
            half_open_attempts = state_data.get('half_open_attempts', 0) + 1
            if half_open_attempts >= 3:  # Require 3 successful calls
                await self._set_state(
                    CircuitState.CLOSED, 
                    failure_count=0,
                    half_open_attempts=0
                )
                logger.info(f"Circuit {self.name} recovered - now CLOSED")
            else:
                await self._set_state(
                    current_state,
                    state_data['failure_count'],
                    half_open_attempts=half_open_attempts
                )
        else:
            # Reset failure count on success in CLOSED state
            await self._set_state(
                current_state,
                failure_count=0,
                half_open_attempts=state_data.get('half_open_attempts', 0)
            )
    
    async def _on_failure(self):
        """Handle failed call"""
        await self._increment_stat('failed_calls')
        
        state_data = await self._get_state()
        current_state = CircuitState(state_data['state'])
        failure_count = state_data.get('failure_count', 0) + 1
        
        # Set last failure time to current UTC timestamp
        last_failure_time = datetime.utcnow().isoformat()
        
        if current_state == CircuitState.HALF_OPEN:
            await self._set_state(
                CircuitState.OPEN,
                failure_count=failure_count,
                half_open_attempts=0,
                last_failure_time=last_failure_time
            )
            logger.warning(f"Circuit {self.name} failed in HALF_OPEN - returning to OPEN")
        elif failure_count >= self.failure_threshold:
            await self._set_state(
                CircuitState.OPEN,
                failure_count=failure_count,
                half_open_attempts=0,
                last_failure_time=last_failure_time
            )
            logger.error(f"Circuit {self.name} opened due to {failure_count} failures")
        else:
            # Update failure count but stay in current state
            await self._set_state(
                current_state,
                failure_count=failure_count,
                half_open_attempts=state_data.get('half_open_attempts', 0),
                last_failure_time=last_failure_time
            )
    
    async def reset(self):
        """Manually reset the circuit breaker"""
        await self._set_state(CircuitState.CLOSED, 0, half_open_attempts=0)
        await state_manager.set_state(
            self.stats_key, 
            self.default_stats, 
            ttl=None,  # Persistent stats
            source='circuit_breaker_reset'
        )
        logger.info(f"Circuit {self.name} manually reset")
    
    async def get_state(self) -> Dict:
        """Get current circuit breaker state and statistics"""
        state_data = await self._get_state()
        stats = await self._get_stats()
        
        return {
            'name': self.name,
            'state': state_data['state'],
            'failure_count': state_data['failure_count'],
            'stats': stats,
            'last_failure': state_data.get('last_failure_time'),
            'half_open_attempts': state_data['half_open_attempts'],
            'updated_at': state_data['updated_at']
        }
    
    # Redis helper methods
    
    async def _get_state(self) -> Dict:
        """Get circuit breaker state from Redis"""
        state_data = await state_manager.get_state(self.state_key, self.default_state)
        
        # Ensure all required fields exist
        for key, default_value in self.default_state.items():
            if key not in state_data:
                state_data[key] = default_value
        
        return state_data
    
    async def _set_state(
        self, 
        state: CircuitState, 
        failure_count: int, 
        half_open_attempts: int = 0,
        last_failure_time: Optional[str] = None
    ) -> bool:
        """Set circuit breaker state in Redis"""
        state_data = {
            'state': state.value,
            'failure_count': failure_count,
            'half_open_attempts': half_open_attempts,
            'last_failure_time': last_failure_time,
            'updated_at': datetime.utcnow().isoformat(),
            'created_at': (await self._get_state()).get('created_at', datetime.utcnow().isoformat())
        }
        
        return await state_manager.set_state(
            self.state_key,
            state_data,
            ttl=None,  # Persistent state
            tags=['circuit_breaker', f'service_{self.name.lower()}'],
            source='circuit_breaker'
        )
    
    async def _get_stats(self) -> Dict:
        """Get circuit breaker statistics from Redis"""
        stats = await state_manager.get_state(self.stats_key, self.default_stats)
        
        # Ensure all required fields exist
        for key, default_value in self.default_stats.items():
            if key not in stats:
                stats[key] = default_value
        
        return stats
    
    async def _increment_stat(self, stat_name: str) -> bool:
        """Increment a specific statistic"""
        stats = await self._get_stats()
        stats[stat_name] = stats.get(stat_name, 0) + 1
        
        return await state_manager.set_state(
            self.stats_key,
            stats,
            ttl=None,  # Persistent stats
            tags=['circuit_breaker', 'stats', f'service_{self.name.lower()}'],
            source='circuit_breaker_stats'
        )


# Global circuit breakers for different services
circuit_breakers = {
    'binance': CircuitBreaker('binance', failure_threshold=5, recovery_timeout=60),
    'fincra': CircuitBreaker('fincra', failure_threshold=3, recovery_timeout=120),
    'blockbee': CircuitBreaker('blockbee', failure_threshold=5, recovery_timeout=60),
    'email': CircuitBreaker('email', failure_threshold=10, recovery_timeout=30),
    'sms': CircuitBreaker('sms', failure_threshold=5, recovery_timeout=60),
    'kraken': CircuitBreaker('kraken', failure_threshold=5, recovery_timeout=90),
}


def with_circuit_breaker(service_name: str):
    """
    Decorator to apply circuit breaker to async functions
    
    Usage:
        @with_circuit_breaker('binance')
        async def call_binance_api():
            ...
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            breaker = circuit_breakers.get(service_name)
            if not breaker:
                logger.warning(f"No circuit breaker configured for {service_name}")
                return await func(*args, **kwargs)
            
            try:
                return await breaker.async_call(func, *args, **kwargs)
            except Exception as e:
                # Check circuit state from Redis for fallback logic
                state_data = await breaker._get_state()
                if CircuitState(state_data['state']) == CircuitState.OPEN:
                    logger.error(f"{service_name} circuit is OPEN - fallback triggered")
                    # Return fallback response
                    return {'success': False, 'error': f'{service_name} temporarily unavailable'}
                raise e
        
        return wrapper
    return decorator


# Enhanced transaction rollback decorator
def with_transaction_rollback(session_getter: Callable):
    """
    Decorator to ensure database transaction rollback on failure
    
    Usage:
        @with_transaction_rollback(lambda: SessionLocal())
        async def update_wallet(wallet_id, amount):
            ...
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            session = session_getter()
            try:
                result = await func(session, *args, **kwargs)
                await asyncio.to_thread(session.commit)
                return result
            except Exception as e:
                logger.error(f"Transaction failed in {func.__name__}: {e}")
                await asyncio.to_thread(session.rollback)
                raise
            finally:
                await asyncio.to_thread(session.close)
        
        return wrapper
    return decorator


# Get circuit breaker status for monitoring
async def get_all_breaker_states() -> Dict:
    """Get status of all circuit breakers"""
    states = {}
    for name, breaker in circuit_breakers.items():
        try:
            states[name] = await breaker.get_state()
        except Exception as e:
            logger.error(f"Failed to get state for circuit {name}: {e}")
            states[name] = {'error': str(e)}
    return states


# Reset specific circuit breaker (admin function)
async def reset_circuit_breaker(service_name: str) -> bool:
    """Reset a specific circuit breaker"""
    breaker = circuit_breakers.get(service_name)
    if breaker:
        try:
            await breaker.reset()
            return True
        except Exception as e:
            logger.error(f"Failed to reset circuit breaker {service_name}: {e}")
            return False
    return False