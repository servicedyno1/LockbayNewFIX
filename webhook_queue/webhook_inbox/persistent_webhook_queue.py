"""
SQLite-based Persistent Webhook Queue for Durable Webhook Intake
Critical System Hardening Component

This module provides a durable, SQLite-backed queue system for webhook events
that ensures no webhook payloads are lost even during database connectivity failures.
"""

import sqlite3
import json
import uuid
import time
import logging
import threading
import os
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from contextlib import contextmanager

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
    id: str
    provider: str  # DynoPay, BlockBee, etc.
    endpoint: str  # payment, escrow_deposit, etc.
    payload: str  # JSON serialized webhook payload
    headers: str  # JSON serialized headers
    client_ip: str
    signature: Optional[str]
    status: WebhookEventStatus
    priority: WebhookEventPriority
    retry_count: int
    max_retries: int
    created_at: float  # Unix timestamp
    updated_at: float
    scheduled_at: Optional[float]  # For delayed processing
    error_message: Optional[str]
    processing_duration_ms: Optional[float]
    metadata: str  # JSON serialized metadata

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization"""
        return {
            'id': self.id,
            'provider': self.provider,
            'endpoint': self.endpoint,
            'payload': self.payload,
            'headers': self.headers,
            'client_ip': self.client_ip,
            'signature': self.signature,
            'status': self.status.value,
            'priority': self.priority.value,
            'retry_count': self.retry_count,
            'max_retries': self.max_retries,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'scheduled_at': self.scheduled_at,
            'error_message': self.error_message,
            'processing_duration_ms': self.processing_duration_ms,
            'metadata': self.metadata
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'WebhookEvent':
        """Create from dictionary"""
        return cls(
            id=data['id'],
            provider=data['provider'],
            endpoint=data['endpoint'],
            payload=data['payload'],
            headers=data['headers'],
            client_ip=data['client_ip'],
            signature=data.get('signature'),
            status=WebhookEventStatus(data['status']),
            priority=WebhookEventPriority(data['priority']),
            retry_count=data['retry_count'],
            max_retries=data['max_retries'],
            created_at=data['created_at'],
            updated_at=data['updated_at'],
            scheduled_at=data.get('scheduled_at'),
            error_message=data.get('error_message'),
            processing_duration_ms=data.get('processing_duration_ms'),
            metadata=data.get('metadata', '{}')
        )


class PersistentWebhookQueue:
    """
    SQLite-based persistent webhook queue for durable webhook intake.
    
    Key Features:
    - Atomic enqueue operations (< 50ms target)
    - Crash-resistant persistence
    - Priority-based processing
    - Automatic retry with exponential backoff
    - Circuit breaker integration
    - Comprehensive monitoring and metrics
    """
    
    def __init__(self, db_path: str = None):
        """Initialize persistent webhook queue"""
        if db_path is None:
            # Default to queue/webhook_inbox directory
            queue_dir = Path(__file__).parent
            queue_dir.mkdir(exist_ok=True)
            db_path = str(queue_dir / "webhook_events.db")
        
        self.db_path = db_path
        self._lock = threading.RLock()  # Reentrant lock for thread safety
        self._circuit_breaker_open = False
        self._circuit_breaker_failures = 0
        self._circuit_breaker_last_failure = 0
        self._circuit_breaker_threshold = 5  # Open after 5 consecutive failures
        self._circuit_breaker_timeout = 60  # Reset after 60 seconds
        
        # Performance metrics
        self._metrics = {
            'events_enqueued': 0,
            'events_processed': 0,
            'events_failed': 0,
            'average_enqueue_time_ms': 0.0,
            'average_processing_time_ms': 0.0,
            'total_enqueue_time_ms': 0.0,
            'database_errors': 0,
            'circuit_breaker_openings': 0
        }
        
        # Initialize database
        self._init_database()
        
        logger.info(f"✅ WEBHOOK_QUEUE: Initialized persistent webhook queue at {self.db_path}")
    
    def _init_database(self):
        """Initialize SQLite database with proper schema"""
        try:
            with self._get_connection() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS webhook_events (
                        id TEXT PRIMARY KEY,
                        provider TEXT NOT NULL,
                        endpoint TEXT NOT NULL,
                        payload TEXT NOT NULL,
                        headers TEXT NOT NULL,
                        client_ip TEXT NOT NULL,
                        signature TEXT,
                        status TEXT NOT NULL DEFAULT 'pending',
                        priority INTEGER NOT NULL DEFAULT 2,
                        retry_count INTEGER NOT NULL DEFAULT 0,
                        max_retries INTEGER NOT NULL DEFAULT 3,
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL,
                        scheduled_at REAL,
                        error_message TEXT,
                        processing_duration_ms REAL,
                        metadata TEXT DEFAULT '{}'
                    )
                """)
                
                # Create indexes for efficient querying
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_webhook_events_status_priority 
                    ON webhook_events(status, priority DESC, created_at ASC)
                """)
                
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_webhook_events_scheduled 
                    ON webhook_events(scheduled_at) WHERE scheduled_at IS NOT NULL
                """)
                
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_webhook_events_provider 
                    ON webhook_events(provider, endpoint)
                """)
                
                conn.commit()
                logger.info("✅ WEBHOOK_QUEUE: Database schema initialized successfully")
                
        except Exception as e:
            logger.error(f"❌ WEBHOOK_QUEUE: Failed to initialize database: {e}")
            raise
    
    @contextmanager
    def _get_connection(self):
        """Get SQLite connection with proper configuration"""
        conn = None
        try:
            # Configure SQLite for optimal webhook processing
            conn = sqlite3.connect(
                self.db_path,
                timeout=30.0,  # 30 second timeout for database operations
                check_same_thread=False,  # Allow multi-threaded access
                isolation_level=None  # Autocommit mode for atomic operations
            )
            
            # Configure SQLite for better performance and reliability
            conn.execute("PRAGMA journal_mode = WAL")  # Write-Ahead Logging for better concurrency
            conn.execute("PRAGMA synchronous = NORMAL")  # Balance between speed and safety
            conn.execute("PRAGMA cache_size = -64000")  # 64MB cache
            conn.execute("PRAGMA temp_store = MEMORY")  # Use memory for temporary storage
            conn.execute("PRAGMA mmap_size = 268435456")  # 256MB memory mapping
            
            conn.row_factory = sqlite3.Row  # Enable dict-like access to rows
            
            yield conn
            
        except Exception as e:
            if conn:
                conn.rollback()
            self._handle_database_error(e)
            raise
        finally:
            if conn:
                conn.close()
    
    def _handle_database_error(self, error: Exception):
        """Handle database errors and update circuit breaker state"""
        self._metrics['database_errors'] += 1
        self._circuit_breaker_failures += 1
        self._circuit_breaker_last_failure = time.time()
        
        # Open circuit breaker if too many failures
        if self._circuit_breaker_failures >= self._circuit_breaker_threshold:
            if not self._circuit_breaker_open:
                self._circuit_breaker_open = True
                self._metrics['circuit_breaker_openings'] += 1
                logger.critical(f"🚨 WEBHOOK_QUEUE: Circuit breaker OPENED after {self._circuit_breaker_failures} failures")
        
        logger.error(f"❌ WEBHOOK_QUEUE: Database error - {error}")
    
    def _check_circuit_breaker(self) -> bool:
        """Check if circuit breaker should be reset"""
        if self._circuit_breaker_open:
            time_since_failure = time.time() - self._circuit_breaker_last_failure
            if time_since_failure > self._circuit_breaker_timeout:
                self._circuit_breaker_open = False
                self._circuit_breaker_failures = 0
                logger.info("✅ WEBHOOK_QUEUE: Circuit breaker RESET - database operations resumed")
                return False
            return True
        return False
    
    def enqueue_webhook(
        self,
        provider: str,
        endpoint: str,
        payload: Dict[str, Any],
        headers: Dict[str, str],
        client_ip: str,
        signature: str = None,
        priority: WebhookEventPriority = WebhookEventPriority.NORMAL,
        max_retries: int = 3,
        metadata: Dict[str, Any] = None
    ) -> Tuple[bool, str, float]:
        """
        Enqueue webhook event with atomic persistence.
        
        Returns:
            Tuple of (success: bool, event_id: str, duration_ms: float)
        """
        start_time = time.time()
        
        # Check circuit breaker
        if self._check_circuit_breaker():
            logger.warning("⚡ WEBHOOK_QUEUE: Circuit breaker OPEN - cannot enqueue webhook")
            return False, "", 0.0
        
        event_id = str(uuid.uuid4())
        current_time = time.time()
        
        try:
            with self._lock:
                webhook_event = WebhookEvent(
                    id=event_id,
                    provider=provider,
                    endpoint=endpoint,
                    payload=json.dumps(payload),
                    headers=json.dumps(headers),
                    client_ip=client_ip,
                    signature=signature,
                    status=WebhookEventStatus.PENDING,
                    priority=priority,
                    retry_count=0,
                    max_retries=max_retries,
                    created_at=current_time,
                    updated_at=current_time,
                    scheduled_at=None,
                    error_message=None,
                    processing_duration_ms=None,
                    metadata=json.dumps(metadata or {})
                )
                
                with self._get_connection() as conn:
                    conn.execute("""
                        INSERT INTO webhook_events (
                            id, provider, endpoint, payload, headers, client_ip, signature,
                            status, priority, retry_count, max_retries, created_at, updated_at,
                            scheduled_at, error_message, processing_duration_ms, metadata
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        webhook_event.id, webhook_event.provider, webhook_event.endpoint,
                        webhook_event.payload, webhook_event.headers, webhook_event.client_ip,
                        webhook_event.signature, webhook_event.status.value, webhook_event.priority.value,
                        webhook_event.retry_count, webhook_event.max_retries, webhook_event.created_at,
                        webhook_event.updated_at, webhook_event.scheduled_at, webhook_event.error_message,
                        webhook_event.processing_duration_ms, webhook_event.metadata
                    ))
                
                # Update metrics
                duration_ms = (time.time() - start_time) * 1000
                self._metrics['events_enqueued'] += 1
                self._metrics['total_enqueue_time_ms'] += duration_ms
                self._metrics['average_enqueue_time_ms'] = (
                    self._metrics['total_enqueue_time_ms'] / self._metrics['events_enqueued']
                )
                
                # Reset circuit breaker on successful operation
                if self._circuit_breaker_failures > 0:
                    self._circuit_breaker_failures = 0
                    logger.debug("🔄 WEBHOOK_QUEUE: Circuit breaker failure count reset")
                
                logger.info(
                    f"✅ WEBHOOK_QUEUE: Enqueued {provider}/{endpoint} webhook "
                    f"(ID: {event_id[:8]}, Priority: {priority.name}, Duration: {duration_ms:.1f}ms)"
                )
                
                return True, event_id, duration_ms
                
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            self._handle_database_error(e)
            logger.error(f"❌ WEBHOOK_QUEUE: Failed to enqueue webhook: {e} (Duration: {duration_ms:.1f}ms)")
            return False, "", duration_ms
    
    def dequeue_webhook(self, batch_size: int = 1) -> List[WebhookEvent]:
        """
        Dequeue webhook events for processing (highest priority first).
        
        Returns list of webhook events ready for processing.
        """
        if self._check_circuit_breaker():
            logger.debug("⚡ WEBHOOK_QUEUE: Circuit breaker OPEN - cannot dequeue webhooks")
            return []
        
        try:
            with self._lock:
                with self._get_connection() as conn:
                    # Get ready events (pending + scheduled events that are due)
                    current_time = time.time()
                    
                    cursor = conn.execute("""
                        SELECT * FROM webhook_events 
                        WHERE (status = 'pending' OR status = 'retry')
                        AND (scheduled_at IS NULL OR scheduled_at <= ?)
                        ORDER BY priority DESC, created_at ASC 
                        LIMIT ?
                    """, (current_time, batch_size))
                    
                    rows = cursor.fetchall()
                    events = []
                    
                    for row in rows:
                        # Mark as processing
                        event_dict = dict(row)
                        event = WebhookEvent.from_dict(event_dict)
                        event.status = WebhookEventStatus.PROCESSING
                        event.updated_at = current_time
                        
                        # Update in database
                        conn.execute("""
                            UPDATE webhook_events 
                            SET status = ?, updated_at = ?
                            WHERE id = ?
                        """, (event.status.value, event.updated_at, event.id))
                        
                        events.append(event)
                    
                    return events
                    
        except Exception as e:
            self._handle_database_error(e)
            logger.error(f"❌ WEBHOOK_QUEUE: Failed to dequeue webhooks: {e}")
            return []
    
    def update_event_status(
        self,
        event_id: str,
        status: WebhookEventStatus,
        error_message: str = None,
        processing_duration_ms: float = None
    ) -> bool:
        """Update webhook event status and metadata"""
        if self._check_circuit_breaker():
            return False
        
        try:
            with self._lock:
                with self._get_connection() as conn:
                    current_time = time.time()
                    
                    conn.execute("""
                        UPDATE webhook_events 
                        SET status = ?, updated_at = ?, error_message = ?, processing_duration_ms = ?
                        WHERE id = ?
                    """, (status.value, current_time, error_message, processing_duration_ms, event_id))
                    
                    # Update metrics
                    if status == WebhookEventStatus.COMPLETED:
                        self._metrics['events_processed'] += 1
                    elif status == WebhookEventStatus.FAILED:
                        self._metrics['events_failed'] += 1
                    
                    return True
                    
        except Exception as e:
            self._handle_database_error(e)
            logger.error(f"❌ WEBHOOK_QUEUE: Failed to update event status: {e}")
            return False
    
    def retry_event(self, event_id: str, delay_seconds: int = None) -> bool:
        """Schedule event for retry with exponential backoff"""
        if self._check_circuit_breaker():
            return False
        
        try:
            with self._lock:
                with self._get_connection() as conn:
                    # Get current event
                    cursor = conn.execute("SELECT * FROM webhook_events WHERE id = ?", (event_id,))
                    row = cursor.fetchone()
                    
                    if not row:
                        logger.warning(f"⚠️ WEBHOOK_QUEUE: Event {event_id} not found for retry")
                        return False
                    
                    event = WebhookEvent.from_dict(dict(row))
                    
                    # Check retry limits
                    if event.retry_count >= event.max_retries:
                        # Mark as permanently failed
                        self.update_event_status(event_id, WebhookEventStatus.FAILED, 
                                               f"Max retries exceeded ({event.max_retries})")
                        logger.warning(f"❌ WEBHOOK_QUEUE: Event {event_id} failed after {event.max_retries} retries")
                        return False
                    
                    # Calculate retry delay (exponential backoff)
                    if delay_seconds is None:
                        base_delay = 60  # 1 minute base delay
                        delay_seconds = base_delay * (2 ** event.retry_count)  # Exponential backoff
                        delay_seconds = min(delay_seconds, 3600)  # Cap at 1 hour
                    
                    scheduled_at = time.time() + delay_seconds
                    
                    # Update event for retry
                    conn.execute("""
                        UPDATE webhook_events 
                        SET status = ?, retry_count = ?, scheduled_at = ?, updated_at = ?
                        WHERE id = ?
                    """, (WebhookEventStatus.RETRY.value, event.retry_count + 1, 
                          scheduled_at, time.time(), event_id))
                    
                    logger.info(f"🔄 WEBHOOK_QUEUE: Scheduled retry {event.retry_count + 1}/{event.max_retries} "
                               f"for event {event_id} in {delay_seconds}s")
                    
                    return True
                    
        except Exception as e:
            self._handle_database_error(e)
            logger.error(f"❌ WEBHOOK_QUEUE: Failed to schedule retry: {e}")
            return False
    
    def get_queue_stats(self) -> Dict[str, Any]:
        """Get comprehensive queue statistics"""
        try:
            with self._get_connection() as conn:
                # Get status counts
                cursor = conn.execute("""
                    SELECT status, COUNT(*) as count 
                    FROM webhook_events 
                    GROUP BY status
                """)
                status_counts = {row['status']: row['count'] for row in cursor.fetchall()}
                
                # Get provider statistics
                cursor = conn.execute("""
                    SELECT provider, COUNT(*) as count 
                    FROM webhook_events 
                    GROUP BY provider
                """)
                provider_counts = {row['provider']: row['count'] for row in cursor.fetchall()}
                
                # Get recent activity (last hour)
                hour_ago = time.time() - 3600
                cursor = conn.execute("""
                    SELECT COUNT(*) as count 
                    FROM webhook_events 
                    WHERE created_at > ?
                """, (hour_ago,))
                recent_events = cursor.fetchone()['count']
                
                return {
                    'queue_status': {
                        'circuit_breaker_open': self._circuit_breaker_open,
                        'circuit_breaker_failures': self._circuit_breaker_failures,
                        'status_counts': status_counts,
                        'provider_counts': provider_counts,
                        'recent_events_1h': recent_events
                    },
                    'performance_metrics': self._metrics.copy(),
                    'database_info': {
                        'db_path': self.db_path,
                        'db_size_mb': os.path.getsize(self.db_path) / (1024 * 1024) if os.path.exists(self.db_path) else 0
                    }
                }
                
        except Exception as e:
            self._handle_database_error(e)
            logger.error(f"❌ WEBHOOK_QUEUE: Failed to get queue stats: {e}")
            return {
                'queue_status': {'error': str(e)},
                'performance_metrics': self._metrics.copy(),
                'database_info': {'error': str(e)}
            }
    
    def cleanup_old_events(self, retention_hours: int = 168):  # 7 days default
        """Clean up old completed/failed events"""
        if self._check_circuit_breaker():
            return False
        
        try:
            cutoff_time = time.time() - (retention_hours * 3600)
            
            with self._lock:
                with self._get_connection() as conn:
                    cursor = conn.execute("""
                        DELETE FROM webhook_events 
                        WHERE created_at < ? 
                        AND status IN ('completed', 'failed')
                    """, (cutoff_time,))
                    
                    deleted_count = cursor.rowcount
                    
                    if deleted_count > 0:
                        logger.info(f"🧹 WEBHOOK_QUEUE: Cleaned up {deleted_count} old events "
                                   f"(older than {retention_hours} hours)")
                    
                    return True
                    
        except Exception as e:
            self._handle_database_error(e)
            logger.error(f"❌ WEBHOOK_QUEUE: Failed to cleanup old events: {e}")
            return False

    def manage_queue_backlog(self, max_pending: int = 100) -> Dict[str, Any]:
        """
        Manage queue backlog by prioritizing high-priority events and cleaning up stuck items
        
        Returns:
            Dict with backlog management results
        """
        results = {
            'prioritized_events': 0,
            'stuck_items_reset': 0,
            'backlog_status': 'normal',
            'total_pending': 0
        }
        
        try:
            with self._lock:
                with self._get_connection() as conn:
                    # Get current backlog status
                    cursor = conn.execute("""
                        SELECT COUNT(*) FROM webhook_events WHERE status = 'pending'
                    """)
                    pending_count = cursor.fetchone()[0]
                    results['total_pending'] = pending_count
                    
                    if pending_count > max_pending:
                        results['backlog_status'] = 'high'
                        
                        # Prioritize wallet deposit webhooks
                        conn.execute("""
                            UPDATE webhook_events 
                            SET priority = 4 
                            WHERE status = 'pending' 
                            AND (endpoint = 'payment' OR endpoint = 'deposit')
                            AND priority < 4
                        """)
                        results['prioritized_events'] = conn.total_changes
                        
                        # Reset stuck processing items (older than 10 minutes)
                        stuck_cutoff = time.time() - 600  # 10 minutes
                        cursor = conn.execute("""
                            UPDATE webhook_events 
                            SET status = 'pending', updated_at = ?
                            WHERE status = 'processing' 
                            AND updated_at < ?
                        """, (time.time(), stuck_cutoff))
                        results['stuck_items_reset'] = cursor.rowcount
                        
                        if results['stuck_items_reset'] > 0:
                            logger.warning(f"🔄 WEBHOOK_QUEUE: Reset {results['stuck_items_reset']} stuck processing items")
                    
                    elif pending_count > max_pending // 2:
                        results['backlog_status'] = 'moderate'
                    
                    logger.debug(f"📊 WEBHOOK_QUEUE_BACKLOG: {pending_count} pending items, status: {results['backlog_status']}")
                    
        except Exception as e:
            self._handle_database_error(e)
            logger.error(f"❌ WEBHOOK_QUEUE: Failed to manage backlog: {e}")
            results['error'] = str(e)
        
        return results

    def classify_error_type(self, error_message: str) -> str:
        """
        Classify webhook processing errors for appropriate retry handling
        
        Returns:
            'transient' - Should retry with exponential backoff
            'permanent' - Should not retry, needs manual intervention  
            'database' - Database connectivity issue, retry immediately when recovered
        """
        error_lower = error_message.lower()
        
        # Database and connectivity issues (high priority retry)
        if any(keyword in error_lower for keyword in [
            'database', 'connection', 'timeout', 'ssl', 'network',
            'operational error', 'connection pool', 'connection refused'
        ]):
            return 'database'
        
        # Transient errors that should be retried
        if any(keyword in error_lower for keyword in [
            'rate limit', 'throttle', 'busy', 'unavailable', 
            'service unavailable', '503', '502', '504', 'gateway timeout'
        ]):
            return 'transient'
        
        # Permanent errors that should not be retried
        if any(keyword in error_lower for keyword in [
            'invalid signature', 'authentication failed', 'unauthorized',
            'bad request', '400', '401', '403', 'forbidden',
            'not found', '404', 'method not allowed', '405'
        ]):
            return 'permanent'
        
        # Default to transient for unknown errors (safer to retry)
        return 'transient'

    def get_processing_stats(self) -> Dict[str, Any]:
        """Get detailed processing statistics for monitoring"""
        try:
            with self._get_connection() as conn:
                # Processing performance stats
                cursor = conn.execute("""
                    SELECT 
                        COUNT(*) as total_processed,
                        AVG(processing_duration_ms) as avg_processing_time,
                        MAX(processing_duration_ms) as max_processing_time,
                        COUNT(*) FILTER (WHERE updated_at > ?) as processed_last_hour
                    FROM webhook_events 
                    WHERE status = 'completed'
                """, (time.time() - 3600,))  # Last hour
                
                processing_stats = cursor.fetchone()
                
                # Get last processed timestamp
                cursor = conn.execute("""
                    SELECT MAX(updated_at) 
                    FROM webhook_events 
                    WHERE status = 'completed'
                """)
                
                last_processed_timestamp = cursor.fetchone()
                last_processed_at = None
                if last_processed_timestamp and last_processed_timestamp[0]:
                    last_processed_at = datetime.fromtimestamp(last_processed_timestamp[0])
                
                # Calculate throughput
                throughput = 0
                if processing_stats and processing_stats[3]:  # processed_last_hour
                    throughput = processing_stats[3]  # Already per hour
                
                return {
                    'total_processed': processing_stats[0] if processing_stats else 0,
                    'average_processing_time_ms': processing_stats[1] if processing_stats else 0,
                    'max_processing_time_ms': processing_stats[2] if processing_stats else 0,
                    'processed_last_hour': processing_stats[3] if processing_stats else 0,
                    'throughput_per_hour': throughput,
                    'last_processed_at': last_processed_at
                }
                
        except Exception as e:
            self._handle_database_error(e)
            logger.error(f"❌ WEBHOOK_QUEUE: Failed to get processing stats: {e}")
            return {
                'total_processed': 0,
                'average_processing_time_ms': 0,
                'max_processing_time_ms': 0,
                'processed_last_hour': 0,
                'throughput_per_hour': 0,
                'last_processed_at': None
            }

    def get_oldest_pending_age(self) -> float:
        """Get age of oldest pending item in seconds"""
        try:
            with self._get_connection() as conn:
                cursor = conn.execute("""
                    SELECT MIN(created_at)
                    FROM webhook_events
                    WHERE status = 'pending'
                """)
                
                result = cursor.fetchone()
                if result and result[0]:
                    return time.time() - result[0]
                return 0
                
        except Exception as e:
            self._handle_database_error(e)
            logger.error(f"❌ WEBHOOK_QUEUE: Failed to get oldest pending age: {e}")
            return 0


# Global instance for use throughout the application
persistent_webhook_queue = PersistentWebhookQueue()