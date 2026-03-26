"""
Performance Monitoring and Optimization System
Tracks startup times, memory usage, and system bottlenecks
"""

import time
import psutil
import logging
from typing import Dict, Optional, Any
from datetime import datetime
from functools import wraps
import asyncio
from contextlib import asynccontextmanager

# Import safe timing utilities
from utils.safe_timing import safe_duration_calculation, validate_and_log_duration, TIMING_CONSTANTS

logger = logging.getLogger(__name__)


class PerformanceMonitor:
    """System performance monitoring and optimization"""
    
    def __init__(self):
        self.startup_start_time = None
        self.initialization_stages = {}
        self.memory_checkpoints = []
        self.operation_timings = {}
        self.enable_auto_cleanup = True  # Enable automatic memory cleanup for startup optimization
    
    def start_startup_monitoring(self):
        """Begin monitoring startup performance"""
        self.startup_start_time = time.perf_counter()  # Use perf_counter for better precision
        self.memory_checkpoints = []
        self.initialization_stages = {}
        logger.info("🔍 Performance monitoring started")
    
    def log_stage(self, stage_name: str, details: Optional[Dict[str, Any]] = None):
        """Log completion of initialization stage"""
        if not self.startup_start_time:
            return
        
        # Use safe duration calculation to prevent negative values
        elapsed = safe_duration_calculation(
            self.startup_start_time,
            time.perf_counter(),
            scale_factor=1.0,  # Keep in seconds
            min_duration=0.0
        )
        
        # Validate elapsed time is reasonable for startup
        elapsed = validate_and_log_duration(
            elapsed * 1000,  # Convert to ms for validation
            f"startup_stage_{stage_name}",
            max_expected_ms=TIMING_CONSTANTS['MAX_REASONABLE_STARTUP_TIME_MS']
        ) / 1000  # Convert back to seconds
        
        # Memory usage
        process = psutil.Process()
        memory_mb = process.memory_info().rss / 1024 / 1024
        
        stage_info = {
            "elapsed_seconds": round(elapsed, 2),
            "memory_mb": round(memory_mb, 1),
            "timestamp": datetime.utcnow().isoformat(),
            "details": details or {}
        }
        
        self.initialization_stages[stage_name] = stage_info
        self.memory_checkpoints.append((stage_name, memory_mb))
        
        logger.info(f"⏱️  Stage '{stage_name}' completed in {elapsed:.2f}s (Memory: {memory_mb:.1f}MB)")
    
    def finish_startup_monitoring(self):
        """Complete startup monitoring and log summary"""
        if not self.startup_start_time:
            return
        
        # Use safe duration calculation to prevent negative values
        total_time = safe_duration_calculation(
            self.startup_start_time,
            time.perf_counter(),
            scale_factor=1.0,  # Keep in seconds
            min_duration=0.0
        )
        
        # Validate total startup time is reasonable
        total_time = validate_and_log_duration(
            total_time * 1000,  # Convert to ms for validation
            "total_startup_time",
            max_expected_ms=TIMING_CONSTANTS['MAX_REASONABLE_STARTUP_TIME_MS']
        ) / 1000  # Convert back to seconds
        final_memory = psutil.Process().memory_info().rss / 1024 / 1024
        
        logger.info(f"🚀 Bot startup completed in {total_time:.2f}s (Final memory: {final_memory:.1f}MB)")
        
        # Log performance summary
        slowest_stages = sorted(
            [(stage, info["elapsed_seconds"]) for stage, info in self.initialization_stages.items()],
            key=lambda x: x[1],
            reverse=True
        )[:3]
        
        if slowest_stages:
            logger.info(f"⚠️  Slowest startup stages: {', '.join([f'{stage}({time}s)' for stage, time in slowest_stages])}")
        
        # Memory growth analysis
        if len(self.memory_checkpoints) > 1:
            memory_growth = self.memory_checkpoints[-1][1] - self.memory_checkpoints[0][1]
            logger.info(f"📈 Memory growth during startup: {memory_growth:.1f}MB")
        
        return {
            "total_startup_time": total_time,
            "final_memory_mb": final_memory,
            "stages": self.initialization_stages,
            "memory_checkpoints": self.memory_checkpoints
        }
    
    @asynccontextmanager
    async def time_operation(self, operation_name: str):
        """Context manager to time operations"""
        start_time = time.perf_counter()  # Use perf_counter for better precision
        try:
            yield
        finally:
            # Use safe duration calculation to prevent negative values
            elapsed = safe_duration_calculation(
                start_time,
                time.perf_counter(),
                scale_factor=1.0,  # Keep in seconds
                min_duration=0.0
            )
            
            # Validate operation duration
            elapsed = validate_and_log_duration(
                elapsed * 1000,  # Convert to ms for validation
                f"operation_{operation_name}",
                max_expected_ms=TIMING_CONSTANTS['MAX_REASONABLE_OPERATION_TIME_MS']
            ) / 1000  # Convert back to seconds
            
            self.operation_timings[operation_name] = elapsed
            
            if elapsed > 1.0:  # Log slow operations
                logger.warning(f"⏳ Slow operation '{operation_name}': {elapsed:.2f}s")
            else:
                logger.debug(f"⚡ Operation '{operation_name}': {elapsed:.3f}s")


class LazyLoader:
    """Implement lazy loading for non-critical components"""
    
    def __init__(self):
        self._loaded_components = set()
        self._loaders = {}
    
    def register_lazy_component(self, name: str, loader_func):
        """Register a component for lazy loading"""
        self._loaders[name] = loader_func
        logger.debug(f"🔄 Registered lazy component: {name}")
    
    async def load_component(self, name: str):
        """Load component on-demand"""
        if name in self._loaded_components:
            return
            
        if name not in self._loaders:
            logger.warning(f"Unknown lazy component: {name}")
            return
        
        start_time = time.perf_counter()  # Use perf_counter for better precision
        try:
            await self._loaders[name]()
            self._loaded_components.add(name)
            
            # Use safe duration calculation to prevent negative values
            elapsed = safe_duration_calculation(
                start_time,
                time.perf_counter(),
                scale_factor=1.0,  # Keep in seconds
                min_duration=0.0
            )
            
            # Validate component load time
            elapsed = validate_and_log_duration(
                elapsed * 1000,  # Convert to ms for validation
                f"lazy_load_{name}",
                max_expected_ms=TIMING_CONSTANTS['MAX_REASONABLE_OPERATION_TIME_MS']
            ) / 1000  # Convert back to seconds
            
            logger.info(f"📦 Lazy loaded '{name}' in {elapsed:.2f}s")
        except Exception as e:
            logger.error(f"❌ Failed to lazy load '{name}': {e}")
    
    def is_loaded(self, name: str) -> bool:
        """Check if component is loaded"""
        return name in self._loaded_components


class MemoryOptimizer:
    """Memory usage optimization utilities"""
    
    @staticmethod
    def cleanup_startup_memory():
        """Aggressive memory cleanup after startup"""
        import gc
        import sys
        
        # Force garbage collection multiple times
        for _ in range(3):
            collected = gc.collect()
            
        # Clear import caches and optimize memory
        if hasattr(sys, '_clear_type_cache'):
            sys._clear_type_cache()
            
        # Clear module caches
        for module_name in list(sys.modules.keys()):
            if module_name.startswith('__pycache__'):
                del sys.modules[module_name]
        
        logger.info(f"🧹 Memory cleanup completed - collected {collected} objects")
    
    @staticmethod
    def optimize_memory_usage():
        """Ongoing memory optimization"""
        import gc
        
        # Set more aggressive garbage collection
        gc.set_threshold(500, 10, 10)  # More frequent collection
        
        # Enable automatic cleanup
        gc.enable()
        
        logger.info("🔧 Memory optimization settings applied")
    
    @staticmethod
    def get_memory_info():
        """Get detailed memory information"""
        import psutil
        
        process = psutil.Process()
        memory_info = process.memory_info()
        
        return {
            "rss_mb": round(memory_info.rss / 1024 / 1024, 1),
            "vms_mb": round(memory_info.vms / 1024 / 1024, 1),
            "percent": round(process.memory_percent(), 1)
        }
    
    @staticmethod
    def get_memory_usage() -> Dict[str, float]:
        """Get current memory usage statistics"""
        process = psutil.Process()
        return {
            "rss_mb": process.memory_info().rss / 1024 / 1024,
            "vms_mb": process.memory_info().vms / 1024 / 1024,
            "percent": process.memory_percent(),
            "available_mb": psutil.virtual_memory().available / 1024 / 1024
        }
    
    @staticmethod
    def log_memory_usage(context: str = ""):
        """Log current memory usage"""
        usage = MemoryOptimizer.get_memory_usage()
        logger.info(
            f"🧠 Memory usage{' (' + context + ')' if context else ''}: "
            f"RSS={usage['rss_mb']:.1f}MB, "
            f"VMS={usage['vms_mb']:.1f}MB, "
            f"Usage={usage['percent']:.1f}%, "
            f"Available={usage['available_mb']:.1f}MB"
        )
    
    @staticmethod
    async def optimize_garbage_collection():
        """Trigger garbage collection for memory optimization"""
        import gc
        before = MemoryOptimizer.get_memory_usage()["rss_mb"]
        
        collected = gc.collect()
        
        after = MemoryOptimizer.get_memory_usage()["rss_mb"]
        freed = before - after
        
        if freed > 1.0:  # Only log if significant memory freed
            logger.info(f"🗑️  Garbage collection freed {freed:.1f}MB (collected {collected} objects)")


def monitor_performance(operation_name: str):
    """Decorator to monitor function performance"""
    def decorator(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            start_time = time.time()
            try:
                result = await func(*args, **kwargs)
                return result
            finally:
                elapsed = time.time() - start_time
                if elapsed > 0.5:  # Log operations taking more than 500ms
                    logger.warning(f"⏳ Slow async operation '{operation_name}': {elapsed:.2f}s")
        
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            start_time = time.time()
            try:
                result = func(*args, **kwargs)
                return result
            finally:
                elapsed = time.time() - start_time
                if elapsed > 0.5:  # Log operations taking more than 500ms
                    logger.warning(f"⏳ Slow sync operation '{operation_name}': {elapsed:.2f}s")
        
        # Return appropriate wrapper based on function type
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
    
    return decorator


# Global instances
performance_monitor = PerformanceMonitor()
lazy_loader = LazyLoader()
memory_optimizer = MemoryOptimizer()