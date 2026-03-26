"""
Callback Performance Optimization System
Reduces duplicate processing and improves response times
"""

import logging
import asyncio
from typing import Dict, Set
from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

# Track callback processing to prevent race conditions
PROCESSING_CALLBACKS: Set[str] = set()
CALLBACK_LOCKS: Dict[str, asyncio.Lock] = {}


class CallbackPerformanceOptimizer:
    """Optimizes callback processing performance"""
    
    @staticmethod
    async def process_callback_safely(
        update: Update, 
        context: ContextTypes.DEFAULT_TYPE,
        callback_handler,
        callback_name: str
    ):
        """
        Process callbacks with performance optimization and race condition prevention
        """
        if not update.callback_query:
            return await callback_handler(update, context)
        
        query = update.callback_query
        user_id = query.from_user.id if query.from_user else 0
        callback_data = query.data or "unknown"
        
        # Create unique identifier for this callback
        callback_key = f"{user_id}:{callback_data}:{callback_name}"
        
        # Check if this callback is already being processed
        if callback_key in PROCESSING_CALLBACKS:
            logger.debug(f"Callback {callback_key} already processing - ignoring duplicate")
            await query.answer("⏳ Processing...", show_alert=False)
            return
        
        # Get or create lock for this callback
        if callback_key not in CALLBACK_LOCKS:
            CALLBACK_LOCKS[callback_key] = asyncio.Lock()
        
        # Process with lock to prevent race conditions
        async with CALLBACK_LOCKS[callback_key]:
            try:
                # Mark as processing
                PROCESSING_CALLBACKS.add(callback_key)
                
                # Execute the actual callback handler
                result = await callback_handler(update, context)
                
                return result
                
            finally:
                # Always remove from processing set
                PROCESSING_CALLBACKS.discard(callback_key)
                
                # Clean up old locks (prevent memory leak)
                CallbackPerformanceOptimizer._cleanup_locks()
    
    @staticmethod
    def _cleanup_locks():
        """Clean up old callback locks to prevent memory leaks"""
        # Keep only the last 100 locks to prevent memory growth
        if len(CALLBACK_LOCKS) > 100:
            # Remove oldest locks (simple FIFO cleanup)
            keys_to_remove = list(CALLBACK_LOCKS.keys())[:-50]  # Keep last 50
            for key in keys_to_remove:
                if key not in PROCESSING_CALLBACKS:  # Don't remove active locks
                    del CALLBACK_LOCKS[key]
    
    @staticmethod
    def get_stats() -> Dict[str, int]:
        """Get performance statistics"""
        return {
            "active_callbacks": len(PROCESSING_CALLBACKS),
            "callback_locks": len(CALLBACK_LOCKS)
        }


async def optimized_callback_wrapper(callback_handler, callback_name: str):
    """
    Decorator wrapper for callback handlers to add performance optimization
    """
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        return await CallbackPerformanceOptimizer.process_callback_safely(
            update, context, callback_handler, callback_name
        )
    return wrapper