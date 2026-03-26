"""
Retry Service with Exponential Backoff
Ensures resilient external API calls
"""

import asyncio
import logging
import random
from typing import Any, Callable, TypeVar
from functools import wraps

logger = logging.getLogger(__name__)

T = TypeVar('T')


class RetryService:
    """Service for handling retries with exponential backoff"""
    
    @staticmethod
    async def retry_async(
        func: Callable,
        max_attempts: int = 3,
        initial_delay: float = 1.0,
        max_delay: float = 60.0,
        exponential_base: float = 2.0,
        jitter: bool = True,
        exceptions: tuple = (Exception,)
    ) -> Any:
        """
        Retry an async function with exponential backoff
        
        Args:
            func: Async function to retry
            max_attempts: Maximum number of attempts
            initial_delay: Initial delay in seconds
            max_delay: Maximum delay in seconds
            exponential_base: Base for exponential backoff
            jitter: Add random jitter to prevent thundering herd
            exceptions: Tuple of exceptions to catch and retry
        """
        attempt = 0
        delay = initial_delay
        
        while attempt < max_attempts:
            try:
                return await func()
            except exceptions as e:
                attempt += 1
                
                if attempt >= max_attempts:
                    logger.error(f"Max retry attempts ({max_attempts}) reached for {func.__name__}")
                    raise
                
                # Calculate next delay with exponential backoff
                if jitter:
                    actual_delay = delay * (0.5 + random.random())
                else:
                    actual_delay = delay
                
                logger.warning(
                    f"Attempt {attempt}/{max_attempts} failed for {func.__name__}: {e}. "
                    f"Retrying in {actual_delay:.2f}s"
                )
                
                await asyncio.sleep(actual_delay)
                
                # Exponential backoff
                delay = min(delay * exponential_base, max_delay)
        
        raise Exception(f"Failed after {max_attempts} attempts")


def retry_async_decorator(
    max_attempts: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 60.0,
    exponential_base: float = 2.0,
    jitter: bool = True,
    exceptions: tuple = (Exception,)
):
    """
    Decorator for retrying async functions with exponential backoff
    
    Usage:
        @retry_async_decorator(max_attempts=5, initial_delay=2.0)
        async def call_external_api():
            ...
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            attempt = 0
            delay = initial_delay
            
            while attempt < max_attempts:
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    attempt += 1
                    
                    if attempt >= max_attempts:
                        logger.error(
                            f"Max retry attempts ({max_attempts}) reached for {func.__name__}"
                        )
                        raise
                    
                    # Calculate next delay with exponential backoff
                    if jitter:
                        actual_delay = delay * (0.5 + random.random())
                    else:
                        actual_delay = delay
                    
                    logger.warning(
                        f"Attempt {attempt}/{max_attempts} failed for {func.__name__}: {e}. "
                        f"Retrying in {actual_delay:.2f}s"
                    )
                    
                    await asyncio.sleep(actual_delay)
                    
                    # Exponential backoff
                    delay = min(delay * exponential_base, max_delay)
            
            raise Exception(f"Failed after {max_attempts} attempts")
        
        return wrapper
    return decorator


# Predefined retry strategies for different services
RETRY_STRATEGIES = {
    'payment': {
        'max_attempts': 5,
        'initial_delay': 2.0,
        'max_delay': 30.0,
        'exponential_base': 2.0
    },
    'blockchain': {
        'max_attempts': 10,
        'initial_delay': 3.0,
        'max_delay': 60.0,
        'exponential_base': 1.5
    },
    'email': {
        'max_attempts': 3,
        'initial_delay': 1.0,
        'max_delay': 10.0,
        'exponential_base': 2.0
    },
    'api_call': {
        'max_attempts': 3,
        'initial_delay': 1.0,
        'max_delay': 20.0,
        'exponential_base': 2.0
    }
}


# Global retry service instance
retry_service = RetryService()