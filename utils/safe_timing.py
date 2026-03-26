"""
Safe Timing Utilities
Provides robust timing calculation functions that prevent negative durations
and handle edge cases like clock drift, timezone issues, and precision errors.
"""

import time
import logging
from datetime import datetime, timezone
from typing import Optional
from contextlib import asynccontextmanager
from functools import wraps

logger = logging.getLogger(__name__)


def safe_duration_calculation(start_time: float, end_time: Optional[float] = None, 
                            scale_factor: float = 1.0, min_duration: float = 0.0) -> float:
    """
    Safely calculate duration preventing negative values
    
    Args:
        start_time: Start time (from time.time() or time.perf_counter())
        end_time: End time (defaults to current time)
        scale_factor: Multiplier for duration (e.g. 1000 for milliseconds)
        min_duration: Minimum allowed duration (defaults to 0.0)
    
    Returns:
        Safe duration >= min_duration
    """
    try:
        if end_time is None:
            end_time = time.time()
        
        # Ensure we have valid numeric values
        if not isinstance(start_time, (int, float)) or not isinstance(end_time, (int, float)):
            logger.warning(f"Invalid timing values: start={type(start_time)}, end={type(end_time)}")
            return min_duration
        
        # Calculate raw duration
        raw_duration = (end_time - start_time) * scale_factor
        
        # Ensure non-negative result
        if raw_duration < min_duration:
            # Log the incident for debugging
            if raw_duration < -1.0:  # Only log significantly negative durations
                logger.warning(f"Negative duration detected: {raw_duration:.3f} "
                             f"(start: {start_time}, end: {end_time}, scale: {scale_factor}) - "
                             f"returning {min_duration}")
            return min_duration
        
        return raw_duration
        
    except Exception as e:
        logger.error(f"Error in duration calculation: {e} - returning {min_duration}")
        return min_duration


def safe_datetime_duration(start_datetime: datetime, end_datetime: Optional[datetime] = None,
                         scale_factor: float = 1.0, min_duration: float = 0.0) -> float:
    """
    Safely calculate duration from datetime objects preventing negative values
    
    Args:
        start_datetime: Start datetime
        end_datetime: End datetime (defaults to current UTC time)
        scale_factor: Multiplier for duration (e.g. 1000 for milliseconds)  
        min_duration: Minimum allowed duration (defaults to 0.0)
    
    Returns:
        Safe duration >= min_duration
    """
    try:
        if end_datetime is None:
            end_datetime = datetime.now(timezone.utc)
        
        # Ensure timezone consistency - convert both to UTC if possible
        if start_datetime.tzinfo is None:
            start_datetime = start_datetime.replace(tzinfo=timezone.utc)
        if end_datetime.tzinfo is None:
            end_datetime = end_datetime.replace(tzinfo=timezone.utc)
        
        # Calculate duration
        duration_delta = end_datetime - start_datetime
        raw_duration = duration_delta.total_seconds() * scale_factor
        
        # Ensure non-negative result
        if raw_duration < min_duration:
            if raw_duration < -1.0:  # Only log significantly negative durations
                logger.warning(f"Negative datetime duration detected: {raw_duration:.3f}s "
                             f"(start: {start_datetime}, end: {end_datetime}) - "
                             f"returning {min_duration}")
            return min_duration
        
        return raw_duration
        
    except Exception as e:
        logger.error(f"Error in datetime duration calculation: {e} - returning {min_duration}")
        return min_duration


class SafeTimer:
    """Context manager for safe timing operations"""
    
    def __init__(self, operation_name: str = "operation", use_perf_counter: bool = True):
        self.operation_name = operation_name
        self.use_perf_counter = use_perf_counter
        self.start_time = None
        self.duration_ms = 0.0
    
    def __enter__(self):
        self.start_time = time.perf_counter() if self.use_perf_counter else time.time()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        end_time = time.perf_counter() if self.use_perf_counter else time.time()
        self.duration_ms = safe_duration_calculation(
            self.start_time, 
            end_time, 
            scale_factor=1000.0  # Convert to milliseconds
        )
    
    def get_duration_ms(self) -> float:
        """Get duration in milliseconds"""
        return self.duration_ms


@asynccontextmanager
async def async_safe_timer(operation_name: str = "operation", use_perf_counter: bool = True):
    """Async context manager for safe timing operations"""
    start_time = time.perf_counter() if use_perf_counter else time.time()
    try:
        yield
    finally:
        end_time = time.perf_counter() if use_perf_counter else time.time()
        duration_ms = safe_duration_calculation(
            start_time, 
            end_time, 
            scale_factor=1000.0
        )
        logger.debug(f"Operation '{operation_name}' completed in {duration_ms:.3f}ms")


def safe_timing_decorator(operation_name_func=None, use_perf_counter: bool = True):
    """Decorator for safe timing of functions"""
    def decorator(func):
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            operation_name = operation_name_func(func) if operation_name_func else func.__name__
            with SafeTimer(operation_name, use_perf_counter) as timer:
                result = func(*args, **kwargs)
            
            # Log slow operations
            if timer.get_duration_ms() > 1000:  # > 1 second
                logger.warning(f"Slow operation '{operation_name}': {timer.get_duration_ms():.0f}ms")
            
            return result
        
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            operation_name = operation_name_func(func) if operation_name_func else func.__name__
            async with async_safe_timer(operation_name, use_perf_counter):
                result = await func(*args, **kwargs)
            return result
        
        # Return appropriate wrapper based on function type
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
    
    return decorator


def validate_and_log_duration(duration: float, operation_name: str, 
                            max_expected_ms: float = 60000) -> float:
    """
    Validate duration and log warnings for unusual values
    
    Args:
        duration: Duration in milliseconds
        operation_name: Name of operation for logging
        max_expected_ms: Maximum expected duration in ms
    
    Returns:
        Validated duration (>= 0)
    """
    # Ensure non-negative
    if duration < 0:
        logger.warning(f"Negative duration for {operation_name}: {duration}ms - setting to 0")
        return 0.0
    
    # Log unusually long durations
    if duration > max_expected_ms:
        logger.warning(f"Unusually long duration for {operation_name}: {duration:.0f}ms")
    
    return duration


# Migration helpers for existing code
def fix_existing_duration_calculation(original_calc_func):
    """Wrapper to fix existing duration calculations"""
    @wraps(original_calc_func)
    def wrapper(*args, **kwargs):
        try:
            result = original_calc_func(*args, **kwargs)
            # Ensure result is non-negative
            return max(0.0, float(result))
        except Exception as e:
            logger.error(f"Error in duration calculation {original_calc_func.__name__}: {e}")
            return 0.0
    return wrapper


# Constants for common timing scenarios
TIMING_CONSTANTS = {
    'MS_PER_SECOND': 1000.0,
    'SECONDS_PER_MINUTE': 60.0,
    'MAX_REASONABLE_STARTUP_TIME_MS': 300000,  # 5 minutes
    'MAX_REASONABLE_OPERATION_TIME_MS': 60000,  # 1 minute
    'MAX_REASONABLE_DB_QUERY_TIME_MS': 30000,   # 30 seconds
    'MAX_REASONABLE_WEBHOOK_TIME_MS': 10000,    # 10 seconds
}