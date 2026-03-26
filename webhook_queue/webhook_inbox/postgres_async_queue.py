"""
PostgreSQL-based Async Webhook Queue
Replaces SQLite queue with async-native PostgreSQL implementation using row-level locking
Fixes Issues #1 & #2: Eliminates event loop blocking and race conditions
"""

import asyncio
import logging
import json
import time
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
from dataclasses import dataclass
from enum import Enum

from sqlalchemy import select, update, delete, func, and_, or_
from sqlalchemy.dialects.postgresql import insert

from database import async_engine, AsyncSessionLocal
from models import WebhookEventLedger

logger = logging.getLogger(__name__)


class WebhookEventStatus(Enum):
    """Webhook event processing status"""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRY = "retry"


class WebhookEventPriority(Enum):
    """Webhook event priority levels"""
    LOW = 1
    NORMAL = 2
    HIGH = 3
    CRITICAL = 4


@dataclass
class WebhookEvent:
    """Webhook event data structure"""
    id: int
    provider: str
    endpoint: str
    event_id: str
    event_type: str
    payload: Dict[str, Any]
    headers: Dict[str, str]
    client_ip: str
    signature: Optional[str]
    status: str
    priority: int
    retry_count: int
    max_retries: int
    created_at: datetime
    updated_at: datetime
    scheduled_at: Optional[datetime]
    error_message: Optional[str]
    processing_duration_ms: Optional[float]
    metadata: Dict[str, Any]


class PostgresAsyncWebhookQueue:
    """
    PostgreSQL-based async webhook queue with row-level locking.
    
    Key Features:
    - Fully async operations (no event loop blocking)
    - Row-level locking with FOR UPDATE SKIP LOCKED (prevents race conditions)
    - Automatic retry with exponential backoff
    - Circuit breaker integration with thread-safe state
    - No SQLite/threading.RLock bottlenecks
    """
    
    def __init__(self):
        """
        Initialize async PostgreSQL webhook queue.
        
        PERFORMANCE FIX: Reuse shared async_engine from database.py instead of
        creating a 3rd independent pool. This keeps total connections at 60
        (sync 30 + async 30) instead of 90, preventing Neon connection exhaustion.
        """
        # Reuse shared async engine - prevents connection pool explosion
        self.engine = async_engine
        self.SessionLocal = AsyncSessionLocal
        
        # Thread-safe circuit breaker state with asyncio.Lock
        self._circuit_breaker_lock = asyncio.Lock()
        self._circuit_breaker_open = False
        self._circuit_breaker_failures = 0
        self._circuit_breaker_last_failure = 0.0
        self._circuit_breaker_threshold = 5
        self._circuit_breaker_timeout = 60
        
        # Performance metrics
        self._metrics = {
            'events_enqueued': 0,
            'events_processed': 0,
            'events_failed': 0,
            'average_enqueue_time_ms': 0.0,
            'database_errors': 0,
            'circuit_breaker_openings': 0
        }
        
        logger.info("✅ PostgreSQL Async Webhook Queue initialized")
    
    async def _check_circuit_breaker(self) -> bool:
        """Thread-safe circuit breaker check (FIXES ISSUE #3)"""
        async with self._circuit_breaker_lock:
            if self._circuit_breaker_open:
                time_since_failure = time.time() - self._circuit_breaker_last_failure
                if time_since_failure > self._circuit_breaker_timeout:
                    self._circuit_breaker_open = False
                    self._circuit_breaker_failures = 0
                    logger.info("✅ WEBHOOK_QUEUE: Circuit breaker RESET")
                    return False
                return True
            return False
    
    async def _handle_database_error(self, error: Exception):
        """Thread-safe database error handling (FIXES ISSUE #3)"""
        async with self._circuit_breaker_lock:
            self._metrics['database_errors'] += 1
            self._circuit_breaker_failures += 1
            self._circuit_breaker_last_failure = time.time()
            
            if self._circuit_breaker_failures >= self._circuit_breaker_threshold:
                if not self._circuit_breaker_open:
                    self._circuit_breaker_open = True
                    self._metrics['circuit_breaker_openings'] += 1
                    logger.critical(
                        f"🚨 WEBHOOK_QUEUE: Circuit breaker OPENED after "
                        f"{self._circuit_breaker_failures} failures"
                    )
        
        logger.error(f"❌ WEBHOOK_QUEUE: Database error - {error}")
    
    async def enqueue_webhook(
        self,
        provider: str,
        endpoint: str,
        event_id: str,
        event_type: str,
        payload: Dict[str, Any],
        headers: Dict[str, str],
        client_ip: str,
        signature: str = None,
        priority: WebhookEventPriority = WebhookEventPriority.NORMAL,
        max_retries: int = 3,
        metadata: Dict[str, Any] = None
    ) -> tuple[bool, str, float]:
        """
        Async enqueue webhook event (FIXES ISSUE #1).
        
        Returns:
            Tuple of (success: bool, event_id: str, duration_ms: float)
        """
        start_time = time.time()
        
        # Check circuit breaker
        if await self._check_circuit_breaker():
            logger.warning("⚡ WEBHOOK_QUEUE: Circuit breaker OPEN - cannot enqueue")
            return False, "", 0.0
        
        try:
            async with self.SessionLocal() as session:
                # Use INSERT ON CONFLICT for idempotency
                stmt = insert(WebhookEventLedger).values(
                    event_provider=provider,
                    event_id=event_id,
                    event_type=event_type,
                    payload=payload,
                    txid=None,
                    reference_id=None,
                    status='pending',
                    amount=None,
                    currency=None,
                    webhook_payload=json.dumps({
                        'headers': headers,
                        'client_ip': client_ip,
                        'signature': signature,
                        'endpoint': endpoint
                    }),
                    processing_result=None,
                    error_message=None,
                    retry_count=0,
                    user_id=None,
                    event_metadata=metadata or {},
                    processing_duration_ms=None
                ).on_conflict_do_nothing(
                    index_elements=['event_provider', 'event_id']
                )
                
                await session.execute(stmt)
                await session.commit()
                
                # Update metrics
                duration_ms = (time.time() - start_time) * 1000
                self._metrics['events_enqueued'] += 1
                
                # Reset circuit breaker on success
                async with self._circuit_breaker_lock:
                    if self._circuit_breaker_failures > 0:
                        self._circuit_breaker_failures = 0
                
                logger.info(
                    f"✅ WEBHOOK_QUEUE: Enqueued {provider}/{endpoint} webhook "
                    f"(ID: {event_id[:16]}, Priority: {priority.name}, Duration: {duration_ms:.1f}ms)"
                )
                
                return True, event_id, duration_ms
                
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            await self._handle_database_error(e)
            logger.error(f"❌ WEBHOOK_QUEUE: Failed to enqueue webhook: {e}")
            return False, "", duration_ms
    
    async def dequeue_webhook(self, batch_size: int = 1) -> List[Dict[str, Any]]:
        """
        Async dequeue with FOR UPDATE SKIP LOCKED (FIXES ISSUE #2).
        
        Prevents race conditions by using PostgreSQL row-level locking.
        """
        if await self._check_circuit_breaker():
            return []
        
        try:
            async with self.SessionLocal() as session:
                current_time = datetime.now(timezone.utc)
                
                # Use FOR UPDATE SKIP LOCKED to prevent race conditions
                stmt = (
                    select(WebhookEventLedger)
                    .where(
                        and_(
                            or_(
                                WebhookEventLedger.status == 'pending',
                                WebhookEventLedger.status == 'retry'
                            ),
                            or_(
                                WebhookEventLedger.processed_at == None,
                                WebhookEventLedger.processed_at <= current_time
                            )
                        )
                    )
                    .order_by(WebhookEventLedger.created_at.asc())
                    .limit(batch_size)
                    .with_for_update(skip_locked=True)  # CRITICAL: Prevents race conditions
                )
                
                result = await session.execute(stmt)
                events = result.scalars().all()
                
                # Mark as processing
                event_ids = []
                for event in events:
                    event.status = 'processing'
                    event.updated_at = current_time
                    event_ids.append(event.id)
                
                await session.commit()
                
                # Convert to dict format
                webhook_events = []
                for event in events:
                    webhook_payload = json.loads(event.webhook_payload) if event.webhook_payload else {}
                    webhook_events.append({
                        'id': event.id,
                        'provider': event.event_provider,
                        'event_id': event.event_id,
                        'event_type': event.event_type,
                        'payload': event.payload,
                        'headers': webhook_payload.get('headers', {}),
                        'client_ip': webhook_payload.get('client_ip', 'unknown'),
                        'signature': webhook_payload.get('signature'),
                        'endpoint': webhook_payload.get('endpoint', 'unknown'),
                        'status': event.status,
                        'retry_count': event.retry_count,
                        'metadata': event.event_metadata or {}
                    })
                
                if webhook_events:
                    logger.info(f"📥 WEBHOOK_QUEUE: Dequeued {len(webhook_events)} events: {event_ids}")
                
                return webhook_events
                
        except Exception as e:
            await self._handle_database_error(e)
            logger.error(f"❌ WEBHOOK_QUEUE: Failed to dequeue webhooks: {e}")
            return []
    
    async def update_event_status(
        self,
        event_db_id: int,
        status: str,
        error_message: str = None,
        processing_duration_ms: float = None
    ) -> bool:
        """Update webhook event status"""
        if await self._check_circuit_breaker():
            return False
        
        try:
            async with self.SessionLocal() as session:
                stmt = (
                    update(WebhookEventLedger)
                    .where(WebhookEventLedger.id == event_db_id)
                    .values(
                        status=status,
                        error_message=error_message,
                        processing_duration_ms=processing_duration_ms,
                        completed_at=datetime.now(timezone.utc) if status in ['completed', 'failed'] else None,
                        updated_at=datetime.now(timezone.utc)
                    )
                )
                
                await session.execute(stmt)
                await session.commit()
                
                # Update metrics
                if status == 'completed':
                    self._metrics['events_processed'] += 1
                elif status == 'failed':
                    self._metrics['events_failed'] += 1
                
                return True
                
        except Exception as e:
            await self._handle_database_error(e)
            logger.error(f"❌ WEBHOOK_QUEUE: Failed to update event status: {e}")
            return False
    
    async def retry_event(self, event_db_id: int, delay_seconds: int = None) -> bool:
        """Schedule event for retry with exponential backoff"""
        if await self._check_circuit_breaker():
            return False
        
        try:
            async with self.SessionLocal() as session:
                # Get current event
                stmt = select(WebhookEventLedger).where(WebhookEventLedger.id == event_db_id)
                result = await session.execute(stmt)
                event = result.scalar_one_or_none()
                
                if not event:
                    logger.warning(f"⚠️ WEBHOOK_QUEUE: Event {event_db_id} not found for retry")
                    return False
                
                max_retries = 3
                if event.retry_count >= max_retries:
                    # Mark as permanently failed
                    event.status = 'failed'
                    event.error_message = f"Max retries exceeded ({max_retries})"
                    event.updated_at = datetime.now(timezone.utc)
                    await session.commit()
                    logger.warning(f"❌ WEBHOOK_QUEUE: Event {event_db_id} failed after {max_retries} retries")
                    return False
                
                # Calculate retry delay (exponential backoff)
                if delay_seconds is None:
                    base_delay = 60
                    delay_seconds = min(base_delay * (2 ** event.retry_count), 3600)
                
                scheduled_at = datetime.now(timezone.utc).timestamp() + delay_seconds
                
                # Update for retry
                event.status = 'retry'
                event.retry_count += 1
                event.processed_at = datetime.fromtimestamp(scheduled_at, tz=timezone.utc)
                event.updated_at = datetime.now(timezone.utc)
                
                await session.commit()
                
                logger.info(
                    f"🔄 WEBHOOK_QUEUE: Scheduled retry {event.retry_count}/{max_retries} "
                    f"for event {event_db_id} in {delay_seconds}s"
                )
                
                return True
                
        except Exception as e:
            await self._handle_database_error(e)
            logger.error(f"❌ WEBHOOK_QUEUE: Failed to schedule retry: {e}")
            return False
    
    async def cleanup_old_events(self, retention_hours: int = 168) -> int:
        """Clean up old completed/failed events (FIXES ISSUE #7)"""
        if await self._check_circuit_breaker():
            return 0
        
        try:
            cutoff_time = datetime.now(timezone.utc).timestamp() - (retention_hours * 3600)
            cutoff_datetime = datetime.fromtimestamp(cutoff_time, tz=timezone.utc)
            
            async with self.SessionLocal() as session:
                stmt = (
                    delete(WebhookEventLedger)
                    .where(
                        and_(
                            WebhookEventLedger.created_at < cutoff_datetime,
                            or_(
                                WebhookEventLedger.status == 'completed',
                                WebhookEventLedger.status == 'failed'
                            )
                        )
                    )
                )
                
                result = await session.execute(stmt)
                await session.commit()
                deleted_count = result.rowcount
                
                if deleted_count > 0:
                    logger.info(
                        f"🧹 WEBHOOK_QUEUE: Cleaned up {deleted_count} old events "
                        f"(retention: {retention_hours}h)"
                    )
                
                return deleted_count
                
        except Exception as e:
            await self._handle_database_error(e)
            logger.error(f"❌ WEBHOOK_QUEUE: Failed to cleanup old events: {e}")
            return 0
    
    async def get_queue_stats(self) -> Dict[str, Any]:
        """Get comprehensive queue statistics"""
        try:
            async with self.SessionLocal() as session:
                # Get status counts
                stmt = (
                    select(
                        WebhookEventLedger.status,
                        func.count(WebhookEventLedger.id).label('count')
                    )
                    .group_by(WebhookEventLedger.status)
                )
                result = await session.execute(stmt)
                status_counts = {row.status: row.count for row in result}
                
                # Get provider counts
                stmt = (
                    select(
                        WebhookEventLedger.event_provider,
                        func.count(WebhookEventLedger.id).label('count')
                    )
                    .group_by(WebhookEventLedger.event_provider)
                )
                result = await session.execute(stmt)
                provider_counts = {row.event_provider: row.count for row in result}
                
                async with self._circuit_breaker_lock:
                    circuit_breaker_state = {
                        'circuit_breaker_open': self._circuit_breaker_open,
                        'circuit_breaker_failures': self._circuit_breaker_failures,
                    }
                
                return {
                    'queue_status': {
                        **circuit_breaker_state,
                        'status_counts': status_counts,
                        'provider_counts': provider_counts,
                    },
                    'performance_metrics': self._metrics.copy(),
                }
                
        except Exception as e:
            await self._handle_database_error(e)
            logger.error(f"❌ WEBHOOK_QUEUE: Failed to get queue stats: {e}")
            return {
                'queue_status': {'error': str(e)},
                'performance_metrics': self._metrics.copy(),
            }


# Global instance
postgres_async_webhook_queue = PostgresAsyncWebhookQueue()
