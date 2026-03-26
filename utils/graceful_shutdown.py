"""
Graceful Shutdown Handler
Ensures proper cleanup of database connections and async tasks during bot shutdown
"""

import logging
import asyncio
import signal
import sys
from typing import Set
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

class GracefulShutdownManager:
    """Manages graceful shutdown of async applications with proper cleanup"""
    
    def __init__(self):
        self.shutdown_event = asyncio.Event()
        self.running_tasks: Set[asyncio.Task] = set()
        self.cleanup_tasks = []
        
    def add_cleanup_task(self, cleanup_func):
        """Add a cleanup function to be called during shutdown"""
        self.cleanup_tasks.append(cleanup_func)
        
    def track_task(self, task: asyncio.Task):
        """Track an async task for proper cleanup"""
        self.running_tasks.add(task)
        task.add_done_callback(self.running_tasks.discard)
        
    async def shutdown(self):
        """Perform graceful shutdown"""
        logger.info("🔄 Starting graceful shutdown...")
        
        # Set shutdown event
        self.shutdown_event.set()
        
        # Cancel all tracked tasks
        if self.running_tasks:
            logger.info(f"📋 Cancelling {len(self.running_tasks)} pending tasks...")
            for task in self.running_tasks:
                if not task.done():
                    task.cancel()
            
            # Wait for tasks to complete cancellation
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self.running_tasks, return_exceptions=True),
                    timeout=5.0
                )
                logger.info("✅ All tasks cancelled successfully")
            except asyncio.TimeoutError:
                logger.warning("⚠️ Some tasks didn't cancel within timeout")
        
        # Run cleanup tasks
        for cleanup_func in self.cleanup_tasks:
            try:
                if asyncio.iscoroutinefunction(cleanup_func):
                    await cleanup_func()
                else:
                    cleanup_func()
                logger.debug(f"✅ Cleanup completed: {cleanup_func.__name__}")
            except Exception as e:
                logger.error(f"❌ Cleanup failed for {cleanup_func.__name__}: {e}")
        
        logger.info("✅ Graceful shutdown completed")
    
    def setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown"""
        def signal_handler(signum, frame):
            logger.info(f"🛑 Received signal {signum}, initiating shutdown...")
            # Schedule shutdown in the event loop
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self.shutdown())
            except RuntimeError:
                # If no event loop is running, exit directly
                logger.warning("No event loop running, exiting immediately")
                sys.exit(0)
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)


# Global shutdown manager instance
shutdown_manager = GracefulShutdownManager()


@asynccontextmanager
async def managed_task(coro):
    """Context manager for tracking async tasks"""
    task = asyncio.create_task(coro)
    shutdown_manager.track_task(task)
    try:
        yield task
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


def create_managed_task(coro) -> asyncio.Task:
    """Create and track an async task for proper cleanup"""
    task = asyncio.create_task(coro)
    shutdown_manager.track_task(task)
    return task


async def cleanup_database_connections():
    """Cleanup function for database connections"""
    try:
        from database import engine
        if hasattr(engine, 'dispose'):
            engine.dispose()
            logger.info("✅ Database connections cleaned up")
    except Exception as e:
        logger.error(f"❌ Database cleanup failed: {e}")


async def cleanup_telegram_application(application):
    """Cleanup function for Telegram application"""
    try:
        if application and hasattr(application, 'stop'):
            await application.stop()
            logger.info("✅ Telegram application stopped")
    except Exception as e:
        logger.error(f"❌ Telegram cleanup failed: {e}")


# Register default cleanup tasks
shutdown_manager.add_cleanup_task(cleanup_database_connections)