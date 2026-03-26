"""
Webhook Event Processor for Durable Webhook Intake System
Processes queued webhook events with database resilience
"""

import asyncio
import logging
import time
import json
import traceback
from typing import Dict, Any, Callable
from datetime import datetime

from .persistent_webhook_queue import (
    persistent_webhook_queue, 
    WebhookEvent, 
    WebhookEventStatus
)

logger = logging.getLogger(__name__)


class WebhookProcessor:
    """
    Processes webhook events from the persistent queue with database resilience.
    
    Key Features:
    - Asynchronous batch processing
    - Circuit breaker integration
    - Automatic retry with exponential backoff
    - Provider-specific routing
    - Comprehensive error handling and logging
    """
    
    def __init__(self):
        self.is_running = False
        self.processors: Dict[str, Callable] = {}
        self.processing_tasks = []
        self.max_workers = 4  # Limit concurrent webhook processing
        self._semaphore = None
        self._stats = {
            'events_processed': 0,
            'events_failed': 0,
            'processing_errors': 0,
            'average_processing_time_ms': 0.0,
            'last_processed_at': None,
            'max_workers': 4
        }
        
    def register_processor(self, provider: str, endpoint: str, processor_func: Callable):
        """Register a processor function for specific provider/endpoint combinations"""
        key = f"{provider}/{endpoint}"
        self.processors[key] = processor_func
        logger.info(f"✅ WEBHOOK_PROCESSOR: Registered processor for {key}")
    
    async def start_processing(self, batch_size: int = 5, poll_interval: float = 1.0):
        """Start webhook event processing loop"""
        if self.is_running:
            logger.warning("⚠️ WEBHOOK_PROCESSOR: Already running")
            return
            
        self.is_running = True
        # Initialize semaphore for limiting concurrent workers
        self._semaphore = asyncio.Semaphore(self.max_workers)
        logger.info(f"🚀 WEBHOOK_PROCESSOR: Starting with batch_size={batch_size}, poll_interval={poll_interval}s, max_workers={self.max_workers}")
        
        try:
            while self.is_running:
                try:
                    # Dequeue events for processing
                    events = persistent_webhook_queue.dequeue_webhook(batch_size)
                    
                    if events:
                        logger.info(f"📥 WEBHOOK_PROCESSOR: Processing {len(events)} events with max {self.max_workers} concurrent workers")
                        
                        # Process events concurrently with worker limit
                        tasks = [self._process_event_with_semaphore(event) for event in events]
                        await asyncio.gather(*tasks, return_exceptions=True)
                        
                        self._stats['last_processed_at'] = datetime.now().isoformat()
                    else:
                        # No events to process, wait
                        await asyncio.sleep(poll_interval)
                        
                except Exception as e:
                    logger.error(f"❌ WEBHOOK_PROCESSOR: Processing loop error: {e}")
                    self._stats['processing_errors'] += 1
                    await asyncio.sleep(poll_interval * 2)  # Longer delay on error
                    
        finally:
            self.is_running = False
            logger.info("🛑 WEBHOOK_PROCESSOR: Stopped")
    
    async def stop_processing(self):
        """Stop webhook event processing"""
        logger.info("🛑 WEBHOOK_PROCESSOR: Stopping...")
        self.is_running = False
        
        # Wait for current tasks to complete
        if self.processing_tasks:
            logger.info(f"⏳ WEBHOOK_PROCESSOR: Waiting for {len(self.processing_tasks)} tasks to complete...")
            await asyncio.gather(*self.processing_tasks, return_exceptions=True)
            self.processing_tasks.clear()
    
    async def _process_event_with_semaphore(self, event: WebhookEvent):
        """Process a single webhook event with worker limit"""
        async with self._semaphore:
            await self._process_event(event)
    
    async def _process_event(self, event: WebhookEvent):
        """Process a single webhook event"""
        start_time = time.time()
        event_key = f"{event.provider}/{event.endpoint}"
        
        logger.info(f"🔄 WEBHOOK_PROCESSOR: Processing {event_key} event {event.id[:8]} "
                   f"(attempt {event.retry_count + 1})")
        
        try:
            # Find appropriate processor
            processor_func = self.processors.get(event_key)
            if not processor_func:
                error_msg = f"No processor registered for {event_key}"
                logger.error(f"❌ WEBHOOK_PROCESSOR: {error_msg}")
                
                # Mark as failed - no retry for missing processors
                persistent_webhook_queue.update_event_status(
                    event.id, 
                    WebhookEventStatus.FAILED,
                    error_msg,
                    (time.time() - start_time) * 1000
                )
                return
            
            # Parse webhook data
            try:
                payload = json.loads(event.payload)
                headers = json.loads(event.headers)
                metadata = json.loads(event.metadata)
            except json.JSONDecodeError as e:
                error_msg = f"Invalid JSON in event data: {e}"
                logger.error(f"❌ WEBHOOK_PROCESSOR: {error_msg}")
                
                # Mark as failed - no retry for invalid JSON
                persistent_webhook_queue.update_event_status(
                    event.id,
                    WebhookEventStatus.FAILED,
                    error_msg,
                    (time.time() - start_time) * 1000
                )
                return
            
            # Call the processor function
            try:
                # Prepare processor arguments
                processor_args = {
                    'payload': payload,
                    'headers': headers,
                    'client_ip': event.client_ip,
                    'signature': event.signature,
                    'metadata': metadata,
                    'event_id': event.id
                }
                
                # Call processor (may be sync or async)
                if asyncio.iscoroutinefunction(processor_func):
                    result = await processor_func(**processor_args)
                else:
                    result = processor_func(**processor_args)
                
                # Check result
                if isinstance(result, dict):
                    if result.get('status') == 'success' or result.get('ok'):
                        # Success
                        processing_time_ms = (time.time() - start_time) * 1000
                        persistent_webhook_queue.update_event_status(
                            event.id,
                            WebhookEventStatus.COMPLETED,
                            None,
                            processing_time_ms
                        )
                        
                        self._stats['events_processed'] += 1
                        self._update_average_processing_time(processing_time_ms)
                        
                        logger.info(f"✅ WEBHOOK_PROCESSOR: Successfully processed {event_key} event "
                                   f"{event.id[:8]} in {processing_time_ms:.1f}ms")
                        return
                    
                    elif result.get('status') == 'already_processing':
                        # Already processing - mark as completed to prevent retry
                        persistent_webhook_queue.update_event_status(
                            event.id, 
                            WebhookEventStatus.COMPLETED, 
                            "Duplicate/Already processing", 
                            (time.time() - start_time) * 1000
                        )
                        logger.info(f"✅ WEBHOOK_PROCESSOR: Event {event.id[:8]} already processing - marked as completed")
                        return
                    
                    elif result.get('status') == 'retry':
                        # Explicit retry request
                        retry_delay = result.get('retry_delay', None)
                        error_msg = result.get('message', 'Processor requested retry')
                        
                        if persistent_webhook_queue.retry_event(event.id, retry_delay):
                            logger.info(f"🔄 WEBHOOK_PROCESSOR: Scheduled retry for {event_key} event {event.id[:8]}")
                        else:
                            # Max retries exceeded
                            persistent_webhook_queue.update_event_status(
                                event.id,
                                WebhookEventStatus.FAILED,
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
                    persistent_webhook_queue.update_event_status(
                        event.id,
                        WebhookEventStatus.COMPLETED,
                        None,
                        processing_time_ms
                    )
                    
                    self._stats['events_processed'] += 1
                    self._update_average_processing_time(processing_time_ms)
                    
                    logger.info(f"✅ WEBHOOK_PROCESSOR: Successfully processed {event_key} event "
                               f"{event.id[:8]} in {processing_time_ms:.1f}ms")
                    
            except Exception as e:
                # Processor function failed
                error_msg = f"Processor function error: {str(e)}"
                logger.error(f"❌ WEBHOOK_PROCESSOR: {error_msg} for event {event.id[:8]}")
                logger.debug(f"Full traceback: {traceback.format_exc()}")
                
                # Check if this is a retryable error
                if self._is_retryable_error(e):
                    if persistent_webhook_queue.retry_event(event.id):
                        logger.info(f"🔄 WEBHOOK_PROCESSOR: Scheduled retry for {event_key} event {event.id[:8]} due to: {e}")
                    else:
                        # Max retries exceeded
                        persistent_webhook_queue.update_event_status(
                            event.id,
                            WebhookEventStatus.FAILED,
                            f"Max retries exceeded: {error_msg}",
                            (time.time() - start_time) * 1000
                        )
                        self._stats['events_failed'] += 1
                else:
                    # Non-retryable error, mark as failed immediately
                    persistent_webhook_queue.update_event_status(
                        event.id,
                        WebhookEventStatus.FAILED,
                        error_msg,
                        (time.time() - start_time) * 1000
                    )
                    self._stats['events_failed'] += 1
                    
        except Exception as e:
            # Unexpected error in processing logic
            error_msg = f"Unexpected processing error: {str(e)}"
            logger.error(f"❌ WEBHOOK_PROCESSOR: {error_msg} for event {event.id[:8]}")
            logger.debug(f"Full traceback: {traceback.format_exc()}")
            
            # Schedule retry for unexpected errors
            if persistent_webhook_queue.retry_event(event.id):
                logger.info(f"🔄 WEBHOOK_PROCESSOR: Scheduled retry for event {event.id[:8]} due to unexpected error")
            else:
                persistent_webhook_queue.update_event_status(
                    event.id,
                    WebhookEventStatus.FAILED,
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
    
    def get_stats(self) -> Dict[str, Any]:
        """Get processor statistics"""
        return {
            'processor_stats': self._stats.copy(),
            'registered_processors': list(self.processors.keys()),
            'is_running': self.is_running,
            'queue_stats': persistent_webhook_queue.get_queue_stats()
        }


# Global instance
webhook_processor = WebhookProcessor()