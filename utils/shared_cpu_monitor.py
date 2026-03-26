"""
Shared CPU Monitoring Service
Singleton service to prevent CPU measurement collisions and resource contention
"""

import logging
import time
import asyncio
import threading
from typing import Optional
import psutil
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class CPUReading:
    """CPU measurement with timestamp"""
    cpu_percent: float
    timestamp: float
    process_cpu: float = 0.0
    is_cached: bool = False

class SharedCPUMonitor:
    """
    Singleton CPU monitoring service that prevents measurement collisions
    Uses 5-second caching to prevent redundant psutil calls
    """
    
    _instance: Optional['SharedCPUMonitor'] = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
            
        self._initialized = True
        self._last_reading: Optional[CPUReading] = None
        self._cache_duration = 5.0  # 5-second cache
        self._measurement_lock = asyncio.Lock()
        self._process = None
        self._system_cpu_start_time = None
        
        # Initialize process handle once
        try:
            self._process = psutil.Process()
            logger.info("🔧 SharedCPUMonitor initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize process handle: {e}")
            
    async def get_cpu_usage(self, force_refresh: bool = False) -> CPUReading:
        """
        Get CPU usage with intelligent caching
        Returns cached value if within 5-second window unless forced
        """
        current_time = time.time()
        
        # Return cached reading if available and fresh
        if (not force_refresh and 
            self._last_reading and 
            current_time - self._last_reading.timestamp < self._cache_duration):
            
            # Mark as cached for transparency
            cached_reading = CPUReading(
                cpu_percent=self._last_reading.cpu_percent,
                timestamp=self._last_reading.timestamp,
                process_cpu=self._last_reading.process_cpu,
                is_cached=True
            )
            logger.debug(f"🔄 Returning cached CPU reading: {cached_reading.cpu_percent:.1f}%")
            return cached_reading
        
        # Take new measurement with lock to prevent collisions
        async with self._measurement_lock:
            # Double-check cache in case another coroutine just updated
            if (not force_refresh and 
                self._last_reading and 
                current_time - self._last_reading.timestamp < self._cache_duration):
                return self._last_reading
            
            try:
                # OPTIMIZED: Use non-blocking measurements to prevent collisions
                system_cpu = await self._get_system_cpu_nonblocking()
                process_cpu = await self._get_process_cpu_safe()
                
                # Create new reading
                new_reading = CPUReading(
                    cpu_percent=system_cpu,
                    timestamp=current_time,
                    process_cpu=process_cpu,
                    is_cached=False
                )
                
                self._last_reading = new_reading
                logger.debug(f"📊 New CPU measurement: System={system_cpu:.1f}%, Process={process_cpu:.1f}%")
                return new_reading
                
            except Exception as e:
                logger.error(f"CPU measurement failed: {e}")
                
                # Return safe fallback reading
                fallback_reading = CPUReading(
                    cpu_percent=0.0,
                    timestamp=current_time,
                    process_cpu=0.0,
                    is_cached=False
                )
                return fallback_reading
    
    async def _get_system_cpu_nonblocking(self) -> float:
        """Get system CPU usage without blocking interval calls"""
        try:
            # CRITICAL FIX: Use non-blocking instant reading
            # This prevents the collision that caused 6206.2% CPU spike
            cpu_percent = psutil.cpu_percent(interval=None)
            
            # If instant reading returns 0.0 (no previous measurement), 
            # use a very short interval as fallback
            if cpu_percent == 0.0:
                # Run in thread pool to prevent blocking the event loop
                loop = asyncio.get_event_loop()
                cpu_percent = await loop.run_in_executor(
                    None, 
                    lambda: psutil.cpu_percent(interval=0.1)
                )
            
            return cpu_percent
            
        except Exception as e:
            logger.warning(f"System CPU measurement fallback: {e}")
            return 0.0
    
    async def _get_process_cpu_safe(self) -> float:
        """Get process CPU usage safely"""
        try:
            if not self._process:
                return 0.0
                
            # Use non-blocking process CPU measurement
            process_cpu = self._process.cpu_percent(interval=None)
            
            # Fallback if no previous reading available
            if process_cpu == 0.0:
                loop = asyncio.get_event_loop()
                process_cpu = await loop.run_in_executor(
                    None,
                    lambda: self._process.cpu_percent(interval=0.1)
                )
            
            return process_cpu
            
        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            logger.warning(f"Process CPU access denied: {e}")
            return 0.0
        except Exception as e:
            logger.warning(f"Process CPU measurement failed: {e}")
            return 0.0
    
    async def get_memory_usage(self) -> dict:
        """Get memory usage information (bonus method for convenience)"""
        try:
            # System memory
            memory = psutil.virtual_memory()
            
            # Process memory
            process_memory_mb = 0.0
            if self._process:
                process_memory_mb = self._process.memory_info().rss / (1024 * 1024)
            
            return {
                'system_memory_percent': memory.percent,
                'system_memory_available_gb': memory.available / (1024**3),
                'process_memory_mb': process_memory_mb,
                'timestamp': time.time()
            }
            
        except Exception as e:
            logger.error(f"Memory usage measurement failed: {e}")
            return {
                'system_memory_percent': 0.0,
                'system_memory_available_gb': 0.0,
                'process_memory_mb': 0.0,
                'timestamp': time.time()
            }
    
    def invalidate_cache(self):
        """Force invalidate current cache (useful for testing)"""
        self._last_reading = None
        logger.debug("🗑️ CPU cache invalidated")
    
    def get_cache_status(self) -> dict:
        """Get cache status for debugging"""
        if not self._last_reading:
            return {"cached": False, "age_seconds": None}
        
        age = time.time() - self._last_reading.timestamp
        return {
            "cached": age < self._cache_duration,
            "age_seconds": age,
            "last_cpu_percent": self._last_reading.cpu_percent,
            "cache_duration": self._cache_duration
        }

# Global singleton instance for easy access
_cpu_monitor = SharedCPUMonitor()

async def get_cpu_usage(force_refresh: bool = False) -> CPUReading:
    """
    Convenience function to get CPU usage from singleton instance
    
    Args:
        force_refresh: If True, bypass cache and take new measurement
        
    Returns:
        CPUReading with system and process CPU percentages
    """
    return await _cpu_monitor.get_cpu_usage(force_refresh)

async def get_memory_usage() -> dict:
    """Convenience function to get memory usage from singleton instance"""
    return await _cpu_monitor.get_memory_usage()

def invalidate_cache():
    """Convenience function to invalidate cache"""
    _cpu_monitor.invalidate_cache()

def get_cache_status() -> dict:
    """Convenience function to get cache status"""
    return _cpu_monitor.get_cache_status()