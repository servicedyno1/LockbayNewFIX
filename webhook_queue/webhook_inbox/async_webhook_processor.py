"""
Async Webhook Processor with Configurable Workers
Replaces sync webhook processor with fully async implementation
Fixes Issues #4, #5, #6: Configurable workers, no lock contention, memory leak prevention
"""

import asyncio
import logging
import time
import traceback
import os
from typing import Dict, Any, Optional, Callable, List
from datetime import datetime

from .postgres_async_queue import postgres_async_webhook_queue

logger = logging.getLogger(__name__)


class AsyncWebhookProcessor:
    """
    Async webhook event processor with configurable concurrency.
    
    Key Features:
    - Fully async operations (no blocking)
    - Configurable max_workers via environment variable (FIXES ISSUE #4)
    - Automatic task cleanup to prevent memory leaks (FIXES ISSUE #6)
    - No database pool locks (FIXES ISSUE #5)
    - Comprehensive error handling and logging
    """
    
    def __init__(self):
        self.is_running = False
        self.processors: Dict[str, Callable] = {}
        self.processing_tasks: List[asyncio.Task] = []
        
        # FIXES ISSUE #4: Configurable max workers from environment
        self.max_workers = int(os.getenv("WEBHOOK_MAX_WORKERS", "10"))
        self._semaphore: Optional[asyncio.Semaphore] = None
        
        self._stats = {
            'events_processed': 0,
            'events_failed': 0,
            'processing_errors': 0,
            'average_processing_time_ms': 0.0,
            'last_processed_at': None,
            'max_workers': self.max_workers
        }
        
        logger.info(f"🚀 AsyncWebhookProcessor initialized with max_workers={self.max_workers}")
        
    def register_processor(self, provider: str, endpoint: str, processor_func: Callable):
        """Register a processor function for specific provider/endpoint combinations"""
        key = f"{provider}/{endpoint}"
        self.processors[key] = processor_func
        logger.info(f"✅ WEBHOOK_PROCESSOR: Registered processor for {key}")
    
    async def start_processing(self, batch_size: int = 5, poll_interval: float = 1.0):
        """Start async webhook event processing loop"""
        if self.is_running:
            logger.warning("⚠️ WEBHOOK_PROCESSOR: Already running")
            return
            
        self.is_running = True
        self._semaphore = asyncio.Semaphore(self.max_workers)
        logger.info(
            f"🚀 WEBHOOK_PROCESSOR: Starting with batch_size={batch_size}, "
            f"poll_interval={poll_interval}s, max_workers={self.max_workers}"
        )
        
        try:
            while self.is_running:
                try:
                    # FIXES ISSUE #6: Cleanup completed tasks to prevent memory leak
                    self._cleanup_completed_tasks()
                    
                    # Dequeue events for processing (async)
                    events = await postgres_async_webhook_queue.dequeue_webhook(batch_size)
                    
                    if events:
                        logger.info(
                            f"📥 WEBHOOK_PROCESSOR: Processing {len(events)} events "
                            f"with max {self.max_workers} concurrent workers"
                        )
                        
                        # Process events concurrently with worker limit
                        tasks = [
                            asyncio.create_task(self._process_event_with_semaphore(event))
                            for event in events
                        ]
                        self.processing_tasks.extend(tasks)
                        
                        # Don't wait for all tasks - let them run in background
                        # They'll be cleaned up by _cleanup_completed_tasks()
                        
                        self._stats['last_processed_at'] = datetime.now().isoformat()
                    else:
                        # No events to process, wait
                        await asyncio.sleep(poll_interval)
                        
                except Exception as e:
                    logger.error(f"❌ WEBHOOK_PROCESSOR: Processing loop error: {e}")
                    self._stats['processing_errors'] += 1
                    await asyncio.sleep(poll_interval * 2)
                    
        finally:
            self.is_running = False
            # Wait for remaining tasks to complete
            if self.processing_tasks:
                logger.info(f"⏳ WEBHOOK_PROCESSOR: Waiting for {len(self.processing_tasks)} tasks...")
                await asyncio.gather(*self.processing_tasks, return_exceptions=True)
            logger.info("🛑 WEBHOOK_PROCESSOR: Stopped")
    
    async def stop_processing(self):
        """Stop webhook event processing"""
        logger.info("🛑 WEBHOOK_PROCESSOR: Stopping...")
        self.is_running = False
        
        # Wait for current tasks to complete
        if self.processing_tasks:
            logger.info(f"⏳ WEBHOOK_PROCESSOR: Waiting for {len(self.processing_tasks)} tasks...")
            await asyncio.gather(*self.processing_tasks, return_exceptions=True)
            self.processing_tasks.clear()
    
    def _cleanup_completed_tasks(self):
        """
        FIXES ISSUE #6: Periodic cleanup of completed tasks to prevent memory leak
        """
        initial_count = len(self.processing_tasks)
        self.processing_tasks = [task for task in self.processing_tasks if not task.done()]
        cleaned_count = initial_count - len(self.processing_tasks)
        
        if cleaned_count > 0:
            logger.debug(
                f"🧹 WEBHOOK_PROCESSOR: Cleaned {cleaned_count} completed tasks "
                f"(active: {len(self.processing_tasks)})"
            )
    
    async def _process_event_with_semaphore(self, event: Dict[str, Any]):
        """Process a single webhook event with worker limit"""
        async with self._semaphore:
            await self._process_event(event)
    
    async def _process_event(self, event: Dict[str, Any]):
        """Process a single webhook event"""
        start_time = time.time()
        provider = event.get('provider')
        endpoint = event.get('endpoint')
        event_key = f"{provider}/{endpoint}"
        event_db_id = event.get('id')
        event_id = event.get('event_id', 'unknown')
        retry_count = event.get('retry_count', 0)
        
        logger.info(
            f"🔄 WEBHOOK_PROCESSOR: Processing {event_key} event {event_id[:16]} "
            f"(DB ID: {event_db_id}, attempt {retry_count + 1})"
        )
        
        try:
            # Find appropriate processor
            processor_func = self.processors.get(event_key)
            if not processor_func:
                error_msg = f"No processor registered for {event_key}"
                logger.error(f"❌ WEBHOOK_PROCESSOR: {error_msg}")
                
                # Mark as failed - no retry for missing processors
                await postgres_async_webhook_queue.update_event_status(
                    event_db_id,
                    'failed',
                    error_msg,
                    (time.time() - start_time) * 1000
                )
                return
            
            # Prepare processor arguments
            processor_args = {
                'payload': event.get('payload', {}),
                'headers': event.get('headers', {}),
                'client_ip': event.get('client_ip', 'unknown'),
                'signature': event.get('signature'),
                'metadata': event.get('metadata', {}),
                'event_id': event_id
            }
            
            # Call processor (may be sync or async)
            try:
                if asyncio.iscoroutinefunction(processor_func):
                    result = await processor_func(**processor_args)
                else:
                    result = processor_func(**processor_args)
                
                # Check result
                if isinstance(result, dict):
                    if result.get('status') == 'success' or result.get('ok'):
                        # Success
                        processing_time_ms = (time.time() - start_time) * 1000
                        await postgres_async_webhook_queue.update_event_status(
                            event_db_id,
                            'completed',
                            None,
                            processing_time_ms
                        )
                        
                        self._stats['events_processed'] += 1
                        self._update_average_processing_time(processing_time_ms)
                        
                        logger.info(
                            f"✅ WEBHOOK_PROCESSOR: Successfully processed {event_key} event "
                            f"{event_id[:16]} in {processing_time_ms:.1f}ms"
                        )
                        return
                    
                    elif result.get('status') == 'already_processing':
                        # Already processing - mark as completed to prevent retry
                        await postgres_async_webhook_queue.update_event_status(
                            event_db_id,
                            'completed',
                            "Duplicate/Already processing",
                            (time.time() - start_time) * 1000
                        )
                        logger.info(f"✅ WEBHOOK_PROCESSOR: Event {event_id[:16]} already processing")
                        return
                    
                    elif result.get('status') == 'retry':
                        # Explicit retry request
                        retry_delay = result.get('retry_delay', None)
                        error_msg = result.get('message', 'Processor requested retry')
                        
                        if await postgres_async_webhook_queue.retry_event(event_db_id, retry_delay):
                            logger.info(f"🔄 WEBHOOK_PROCESSOR: Scheduled retry for {event_key} event {event_id[:16]}")
                        else:
                            # Max retries exceeded
                            await postgres_async_webhook_queue.update_event_status(
                                event_db_id,
                                'failed',
                                f"Max retries exceeded: {error_msg}",
                                (time.time() - start_time) * 1000
                            )
                            self._stats['events_failed'] += 1
                        return
                    
                    else:
                        # Error result
                        error_msg = result.get('message', 'Processor returned error status')
                        raise Exception(f"Processor error: {error_msg}")
                
                else:
                    # Assume success if no specific result format
                    processing_time_ms = (time.time() - start_time) * 1000
                    await postgres_async_webhook_queue.update_event_status(
                        event_db_id,
                        'completed',
                        None,
                        processing_time_ms
                    )
                    
                    self._stats['events_processed'] += 1
                    self._update_average_processing_time(processing_time_ms)
                    
                    logger.info(
                        f"✅ WEBHOOK_PROCESSOR: Successfully processed {event_key} event "
                        f"{event_id[:16]} in {processing_time_ms:.1f}ms"
                    )
                    
            except Exception as e:
                # Processor function failed
                error_msg = f"Processor function error: {str(e)}"
                logger.error(f"❌ WEBHOOK_PROCESSOR: {error_msg} for event {event_id[:16]}")
                logger.debug(f"Full traceback: {traceback.format_exc()}")
                
                # Check if this is a retryable error
                if self._is_retryable_error(e):
                    if await postgres_async_webhook_queue.retry_event(event_db_id):
                        logger.info(
                            f"🔄 WEBHOOK_PROCESSOR: Scheduled retry for {event_key} "
                            f"event {event_id[:16]} due to: {e}"
                        )
                    else:
                        # Max retries exceeded
                        await postgres_async_webhook_queue.update_event_status(
                            event_db_id,
                            'failed',
                            f"Max retries exceeded: {error_msg}",
                            (time.time() - start_time) * 1000
                        )
                        self._stats['events_failed'] += 1
                else:
                    # Non-retryable error, mark as failed immediately
                    await postgres_async_webhook_queue.update_event_status(
                        event_db_id,
                        'failed',
                        error_msg,
                        (time.time() - start_time) * 1000
                    )
                    self._stats['events_failed'] += 1
                    
        except Exception as e:
            # Unexpected error in processing logic
            error_msg = f"Unexpected processing error: {str(e)}"
            logger.error(f"❌ WEBHOOK_PROCESSOR: {error_msg} for event {event_id[:16]}")
            logger.debug(f"Full traceback: {traceback.format_exc()}")
            
            # Schedule retry for unexpected errors
            if await postgres_async_webhook_queue.retry_event(event_db_id):
                logger.info(f"🔄 WEBHOOK_PROCESSOR: Scheduled retry for event {event_id[:16]}")
            else:
                await postgres_async_webhook_queue.update_event_status(
                    event_db_id,
                    'failed',
                    error_msg,
                    (time.time() - start_time) * 1000
                )
                self._stats['events_failed'] += 1
    
    def _is_retryable_error(self, error: Exception) -> bool:
        """Determine if an error should trigger a retry"""
        error_str = str(error).lower()
        
        # Database connectivity errors - retryable
        retryable_patterns = [
            'database',
            'connection',
            'timeout',
            'pool',
            'ssl',
            'network',
            'temporary',
            'unavailable',
            'psycopg2.operationalerror',
            'sqlalchemy',
            'circuit breaker'
        ]
        
        # Non-retryable errors
        non_retryable_patterns = [
            'validation',
            'invalid',
            'not found',
            'unauthorized',
            'forbidden',
            'bad request',
            'missing',
            'duplicate',
            'json',
            'parse'
        ]
        
        # Check for non-retryable patterns first
        for pattern in non_retryable_patterns:
            if pattern in error_str:
                return False
        
        # Check for retryable patterns
        for pattern in retryable_patterns:
            if pattern in error_str:
                return True
        
        # Default to retryable for unknown errors
        return True
    
    def _update_average_processing_time(self, processing_time_ms: float):
        """Update average processing time statistics"""
        if self._stats['events_processed'] == 1:
            self._stats['average_processing_time_ms'] = processing_time_ms
        else:
            # Calculate running average
            current_avg = self._stats['average_processing_time_ms']
            count = self._stats['events_processed']
            self._stats['average_processing_time_ms'] = (
                (current_avg * (count - 1) + processing_time_ms) / count
            )
    
    async def get_stats(self) -> Dict[str, Any]:
        """Get processor statistics"""
        queue_stats = await postgres_async_webhook_queue.get_queue_stats()
        return {
            'processor_stats': self._stats.copy(),
            'registered_processors': list(self.processors.keys()),
            'is_running': self.is_running,
            'active_tasks': len(self.processing_tasks),
            'queue_stats': queue_stats
        }


# Global instance
async_webhook_processor = AsyncWebhookProcessor()
