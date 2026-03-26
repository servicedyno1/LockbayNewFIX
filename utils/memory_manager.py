"""
Memory Management Utilities
Fix #5: Automatic memory cleanup for handlers
"""

import gc
import logging
import asyncio
import functools
import psutil

logger = logging.getLogger(__name__)


def cleanup_memory():
    """Force immediate garbage collection"""
    collected = gc.collect()
    if collected > 0:
        logger.debug(f"Garbage collected {collected} objects")
    return collected


def get_memory_usage():
    """Get current memory usage in MB"""
    process = psutil.Process()
    return process.memory_info().rss / 1024 / 1024


def memory_cleanup_decorator(func):
    """
    Decorator to automatically clean up memory after handler execution.
    Applies to both sync and async functions.
    """
    if asyncio.iscoroutinefunction(func):
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            try:
                result = await func(*args, **kwargs)
            finally:
                # Clean up memory after handler completes
                cleanup_memory()
            return result
        return async_wrapper
    else:
        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            try:
                result = func(*args, **kwargs)
            finally:
                # Clean up memory after handler completes
                cleanup_memory()
            return result
        return sync_wrapper


class MemoryManager:
    """
    Centralized memory management system
    Monitors and optimizes memory usage
    """
    
    def __init__(self):
        self.memory_threshold_mb = 500  # Trigger cleanup if memory exceeds 500MB
        self.last_cleanup_time = None
        self.cleanup_interval = 60  # Minimum seconds between cleanups
    
    def check_memory(self):
        """Check if memory cleanup is needed"""
        current_memory = get_memory_usage()
        
        if current_memory > self.memory_threshold_mb:
            logger.warning(f"High memory usage detected: {current_memory:.1f}MB")
            cleanup_memory()
            new_memory = get_memory_usage()
            logger.info(f"Memory cleanup: {current_memory:.1f}MB → {new_memory:.1f}MB (freed {current_memory - new_memory:.1f}MB)")
            return True
        return False
    
    async def periodic_cleanup(self):
        """Run periodic memory cleanup"""
        while True:
            try:
                await asyncio.sleep(self.cleanup_interval)
                self.check_memory()
            except Exception as e:
                logger.error(f"Error in periodic memory cleanup: {e}")
                await asyncio.sleep(10)


# Global memory manager instance
memory_manager = MemoryManager()