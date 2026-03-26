"""
Consolidated Notification Service
Single unified path for all user and admin notifications across all channels
"""

import logging
import time
import json
import html
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Union, TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
from decimal import Decimal
from enum import Enum
from dataclasses import dataclass
from collections import defaultdict
import uuid

# Database and models
from database import AsyncSessionLocal
from models import User, UserStatus, NotificationQueue, NotificationActivity
from utils.preferences import get_user_preferences, is_enabled
from utils.markdown_escaping import format_username_html
from sqlalchemy import select, update, and_, or_

# Service imports
from services.email import EmailService
from config import Config

# Safe user eligibility helpers to avoid non-existent attribute errors
def safe_email_eligibility(user) -> bool:
    """Safely check if user is eligible for email notifications"""
    return (bool(user.email) and 
            user.email.strip() and 
            (getattr(user, 'email_verified', None) is True or 
             getattr(user, 'is_verified', False) is True))

def safe_sms_eligibility(user) -> bool:
    """Safely check if user is eligible for SMS notifications"""
    return (bool(getattr(user, 'phone_number', None)) and 
            bool(getattr(user, 'phone_verified', False)))

def safe_telegram_eligibility(user) -> bool:
    """Safely check if user is eligible for Telegram notifications"""
    return bool(user.telegram_id)

# Telegram imports
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError

# SMS imports (Twilio integration)
from twilio.rest import Client as TwilioClient
from twilio.base.exceptions import TwilioException

logger = logging.getLogger(__name__)

# Configurable idempotency window (hours)
IDEMPOTENCY_WINDOW_HOURS = 24


class NotificationChannel(Enum):
    """Available notification channels"""
    TELEGRAM = "telegram"
    EMAIL = "email"
    SMS = "sms"
    ADMIN_ALERT = "admin_alert"


class NotificationPriority(Enum):
    """Notification priority levels"""
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class NotificationCategory(Enum):
    """Notification categories matching user preferences"""
    ESCROW_UPDATES = "escrow_updates"
    PAYMENTS = "payments"
    EXCHANGES = "exchanges"
    DISPUTES = "disputes"
    MARKETING = "marketing"
    MAINTENANCE = "maintenance"
    SECURITY_ALERTS = "security_alerts"
    ADMIN_ALERTS = "admin_alerts"


class DeliveryStatus(Enum):
    """Notification delivery status"""
    PENDING = "pending"
    SENT = "sent"
    DELIVERED = "delivered"
    FAILED = "failed"
    PERMANENTLY_FAILED = "permanently_failed"  # For non-retryable failures (e.g., user blocked bot)
    RETRYING = "retrying"
    EXPIRED = "expired"
    BLOCKED_BY_PREFERENCES = "blocked_by_preferences"


@dataclass
class NotificationRequest:
    """Structured notification request"""
    user_id: int
    category: NotificationCategory
    priority: NotificationPriority
    title: str
    message: str
    template_data: Optional[Dict[str, Any]] = None
    channels: Optional[List[NotificationChannel]] = None
    require_delivery: bool = False
    retry_config: Optional[Dict[str, Any]] = None
    admin_notification: bool = False
    broadcast_mode: bool = False  # NEW: True = send to all channels, False = fallback mode
    idempotency_key: Optional[str] = None  # NEW: Prevents duplicate notifications (e.g., "escrow_{escrow_id}_payment_confirmed")
    
    def __post_init__(self):
        if self.channels is None:
            self.channels = [NotificationChannel.TELEGRAM, NotificationChannel.EMAIL]


@dataclass
class DeliveryResult:
    """Result of notification delivery attempt"""
    channel: NotificationChannel
    status: DeliveryStatus
    message_id: Optional[str] = None
    error: Optional[str] = None
    response_time_ms: Optional[int] = None
    timestamp: Optional[datetime] = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.utcnow()


@dataclass
class RetryConfig:
    """Retry configuration for failed notifications"""
    max_attempts: int = 5
    initial_delay: int = 60  # seconds
    backoff_multiplier: float = 2.0
    max_delay: int = 3600  # 1 hour
    exponential: bool = True


class ConsolidatedNotificationService:
    """
    Unified notification service for ALL platform communications
    
    Features:
    - Single entry point for all notifications
    - Multi-channel support with fallback logic
    - User preference handling and channel availability detection
    - Comprehensive error handling and retry mechanisms
    - Transaction-safe patterns
    - Admin vs user notification routing
    - Delivery tracking and monitoring
    """
    
    def __init__(self):
        self.email_service = EmailService()
        self.twilio_client = None
        self.delivery_stats = defaultdict(int)
        self.initialized = False
        
        # NOTE: Removed in-memory retry_queues and failed_notifications 
        # Now using persistent NotificationQueue model for durable retry
        
        # Initialize Twilio if configured
        if hasattr(Config, 'TWILIO_ACCOUNT_SID') and hasattr(Config, 'TWILIO_AUTH_TOKEN'):
            try:
                self.twilio_client = TwilioClient(Config.TWILIO_ACCOUNT_SID, Config.TWILIO_AUTH_TOKEN)
            except Exception as e:
                logger.warning(f"❌ Failed to initialize Twilio client: {e}")
                
    async def initialize(self) -> bool:
        """Initialize the notification service"""
        try:
            logger.info("🔔 Initializing Consolidated Notification Service...")
            
            # Validate configuration
            if not Config.BOT_TOKEN:
                logger.warning("⚠️ BOT_TOKEN not configured - Telegram notifications disabled")
            
            if not self.email_service.enabled:
                logger.warning("⚠️ Email service not enabled - Email notifications disabled")
                
            if not self.twilio_client:
                logger.warning("⚠️ Twilio not configured - SMS notifications disabled")
            
            self.initialized = True
            logger.info("✅ Consolidated Notification Service ready")
            
            # Log channel availability
            available_channels = self._get_available_channels()
            logger.info(f"📡 Available channels: {[ch.value for ch in available_channels]}")
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to initialize Consolidated Notification Service: {e}")
            return False
    
    def _get_available_channels(self) -> List[NotificationChannel]:
        """Get list of currently available notification channels"""
        available = []
        
        if Config.BOT_TOKEN:
            available.append(NotificationChannel.TELEGRAM)
        
        if self.email_service.enabled:
            available.append(NotificationChannel.EMAIL)
        
        if self.twilio_client and hasattr(Config, 'TWILIO_PHONE_NUMBER'):
            available.append(NotificationChannel.SMS)
            
        available.append(NotificationChannel.ADMIN_ALERT)  # Always available
        
        return available
    
    async def send_notification(self, request: NotificationRequest, from_outbox: bool = False) -> Dict[str, DeliveryResult]:
        """
        Main entry point for sending notifications
        
        Args:
            request: Structured notification request
            
        Returns:
            Dict mapping channel names to delivery results
        """
        if not self.initialized:
            await self.initialize()
        
        notification_id = f"notif_{request.user_id}_{int(time.time())}"
        
        # IDEMPOTENCY CHECK: Skip if already sent with same idempotency_key
        if request.idempotency_key:
            from database import async_managed_session
            async with async_managed_session() as session:
                existing = await session.execute(
                    select(NotificationActivity).where(
                        and_(
                            NotificationActivity.idempotency_key == request.idempotency_key,
                            NotificationActivity.delivery_status.in_(["sent", "delivered"]),
                            NotificationActivity.created_at > datetime.utcnow() - timedelta(hours=IDEMPOTENCY_WINDOW_HOURS)
                        )
                    )
                )
                # FIX: Use scalars().first() to handle potential duplicates gracefully (webhook retry safety)
                if existing.scalars().first():
                    return {"idempotent_skip": DeliveryResult(
                        channel=NotificationChannel.TELEGRAM,
                        status=DeliveryStatus.DELIVERED,
                        message_id="idempotent_skip"
                    )}
        
        try:
            # Get user data and preferences in transaction-safe manner
            user_context = await self._get_user_context(request.user_id)
            if not user_context:
                error_result = DeliveryResult(
                    channel=NotificationChannel.TELEGRAM,
                    status=DeliveryStatus.FAILED,
                    error="User not found or invalid"
                )
                return {"error": error_result}
            
            # ONBOARDING SECURITY: Skip notifications to non-onboarded users
            onboarding_completed = user_context.get("onboarding_completed", False)
            if not onboarding_completed:
                logger.info(
                    f"🚨 ONBOARDING_SECURITY: Skipping notification to user {request.user_id} "
                    f"(telegram_id: {user_context.get('telegram_id', 'unknown')}) - onboarding not completed. "
                    f"Category: {request.category.value if request.category else 'unknown'}"
                )
                onboarding_result = DeliveryResult(
                    channel=NotificationChannel.TELEGRAM,
                    status=DeliveryStatus.BLOCKED_BY_PREFERENCES,
                    error="User has not completed onboarding - notifications blocked"
                )
                return {"onboarding_incomplete": onboarding_result}
            
            # Check if user can receive notifications
            if user_context["status"] in [UserStatus.BANNED.value, UserStatus.SUSPENDED.value]:
                logger.warning(f"🚫 User {request.user_id} is {user_context['status']} - blocking notifications")
                blocked_result = DeliveryResult(
                    channel=NotificationChannel.TELEGRAM,
                    status=DeliveryStatus.BLOCKED_BY_PREFERENCES,
                    error=f"User account {user_context['status']}"
                )
                return {"blocked": blocked_result}
            
            # Determine effective channels based on preferences and availability
            effective_channels = await self._determine_effective_channels(
                request, user_context
            )
            
            if not effective_channels:
                logger.warning(f"📵 No available channels for user {request.user_id}")
                no_channel_result = DeliveryResult(
                    channel=NotificationChannel.TELEGRAM,
                    status=DeliveryStatus.BLOCKED_BY_PREFERENCES,
                    error="No available channels or all disabled by preferences"
                )
                return {"no_channels": no_channel_result}
            
            # NEW: Implement proper fallback logic vs broadcast mode
            if request.broadcast_mode or self._is_broadcast_category(request.category):
                # BROADCAST MODE: Send to all channels simultaneously
                delivery_results = await self._send_broadcast_notification(
                    effective_channels, request, user_context, notification_id
                )
                successful_channels = [
                    ch for ch, result in delivery_results.items() 
                    if result.status in [DeliveryStatus.SENT, DeliveryStatus.DELIVERED]
                ]
            else:
                # FALLBACK MODE: Try channels in preference order, stop on first success
                delivery_results, successful_channels = await self._send_fallback_notification(
                    effective_channels, request, user_context, notification_id
                )
            
            # Handle retry logic for failed notifications requiring delivery
            # CRITICAL FIX: Skip retry scheduling when called from outbox processing
            if (not successful_channels and 
                (request.require_delivery or request.priority in [NotificationPriority.HIGH, NotificationPriority.CRITICAL]) and
                not from_outbox):
                logger.warning(f"⚠️ Critical notification {notification_id} failed on all channels - scheduling retry")
                await self._schedule_persistent_retry(request, user_context, notification_id, delivery_results)
            
            # Log final delivery status and record activity
            if successful_channels:
                logger.info(f"✅ NOTIFICATION_SUCCESS: {notification_id} delivered via {successful_channels}")
                await self._record_notification_activity(request, user_context, delivery_results, True)
            else:
                logger.error(f"❌ NOTIFICATION_FAILED: {notification_id} failed on all channels")
                await self._record_notification_activity(request, user_context, delivery_results, False)
            
            return delivery_results
            
        except Exception as e:
            logger.error(f"❌ NOTIFICATION_ERROR {notification_id}: {e}")
            error_result = DeliveryResult(
                channel=NotificationChannel.TELEGRAM,
                status=DeliveryStatus.FAILED,
                error=str(e)
            )
            return {"error": error_result}
    
    async def queue_notification(self, request: NotificationRequest, session: Optional['AsyncSession'] = None) -> str:
        """
        Queue notification for outbox pattern processing
        
        Persists notification to NotificationQueue with status="pending" for later processing.
        This method ensures notifications are durably stored and can survive system failures.
        
        Args:
            request: Structured notification request
            session: Optional database session for transaction safety
            
        Returns:
            notification_id: Unique identifier for tracking the queued notification
        """
        notification_id = f"outbox_{request.user_id}_{int(time.time())}_{uuid.uuid4().hex[:8]}"
        
        # Guard: Skip DB queue if disabled (send immediately instead)
        if not getattr(Config, 'NOTIFICATION_DB_QUEUE_ENABLED', True):
            logger.info(f"📧 SEND_DIRECT: {notification_id} - DB queue disabled, sending immediately")
            await self.send_notification(request, from_outbox=True)
            return notification_id
        
        # IDEMPOTENCY CHECK: Skip if already queued/sent successfully with same idempotency_key
        if request.idempotency_key:
            check_session = session if session else AsyncSessionLocal()
            try:
                if not session:
                    await check_session.__aenter__()
                    
                existing = await check_session.execute(
                    select(NotificationQueue).where(
                        and_(
                            NotificationQueue.idempotency_key == request.idempotency_key,
                            NotificationQueue.status.in_(["pending", "sending", "sent"]),
                            NotificationQueue.scheduled_at > datetime.utcnow() - timedelta(hours=IDEMPOTENCY_WINDOW_HOURS)
                        )
                    )
                )
                # FIX: Use scalars().first() to handle potential duplicates gracefully (webhook retry safety)
                if existing.scalars().first():
                    return f"idempotent_skip_{request.idempotency_key}"
            finally:
                if not session:
                    await check_session.__aexit__(None, None, None)
        
        try:
            # Use provided session or create new atomic transaction
            if session:
                # Use existing transaction - for atomic operations
                notification_record = self._create_notification_record(request, notification_id)
                if notification_record:
                    session.add(notification_record)
                    await session.flush()  # Ensure ID is available but don't commit
                else:
                    # Create blocked record for audit trail - always return ID to preserve contract
                    blocked_record = self._create_blocked_notification_record(request, notification_id)
                    session.add(blocked_record)
                    await session.flush()
                    logger.warning(f"⚠️ QUEUE_BLOCKED: {notification_id} - no available channels for user {request.user_id}")
            else:
                # Create new transaction for standalone queuing
                async with AsyncSessionLocal() as tx_session:
                    async with tx_session.begin():
                        notification_record = self._create_notification_record(request, notification_id)
                        if notification_record:
                            tx_session.add(notification_record)
                            await tx_session.flush()
                            # Commit happens automatically with session.begin() context
                        else:
                            # Create blocked record for audit trail - always return ID to preserve contract
                            blocked_record = self._create_blocked_notification_record(request, notification_id)
                            tx_session.add(blocked_record)
                            await tx_session.flush()
                            logger.warning(f"⚠️ QUEUE_BLOCKED: {notification_id} - no available channels for user {request.user_id}")
            
            return notification_id
            
        except Exception as e:
            logger.error(f"❌ QUEUE_NOTIFICATION_ERROR {notification_id}: {e}")
            raise
    
    def _create_notification_record(self, request: NotificationRequest, notification_id: str) -> Optional[NotificationQueue]:
        """Create NotificationQueue record from NotificationRequest - returns None if no channels available"""
        
        # Try to find first available channel with valid recipient
        available_channel = None
        available_recipient = None
        
        channels_to_try = [ch.value for ch in request.channels] if request.channels else ["telegram"]  # Focus on telegram for now
        
        for channel in channels_to_try:
            recipient = self._resolve_recipient(request.user_id, channel)
            if recipient:
                available_channel = channel
                available_recipient = recipient
                break
        
        if not available_channel or not available_recipient:
            # No available channels - log but don't raise, let caller handle
            logger.warning(f"⚠️ No available channels for user {request.user_id}. Tried: {channels_to_try}")
            return None
        
        # Map priority enum to integer (HIGH=1, NORMAL=3, LOW=5)
        priority_map = {
            NotificationPriority.CRITICAL: 1,
            NotificationPriority.HIGH: 1,
            NotificationPriority.NORMAL: 3,
            NotificationPriority.LOW: 5
        }
        priority_int = priority_map.get(request.priority, 3)
        
        return NotificationQueue(
            # Only use existing fields from NotificationQueue model
            user_id=request.user_id,
            channel=available_channel,
            recipient=available_recipient,  # Required field
            subject=request.title,
            content=request.message,
            template_name=None,  # Can be set if available
            template_data=self._serialize_template_data(request.template_data),
            status="pending",  # Start as pending for outbox processing
            priority=priority_int,  # Integer, not string
            scheduled_at=datetime.utcnow(),  # Use this for scheduling
            retry_count=0,
            error_message=None,
            idempotency_key=request.idempotency_key  # Store idempotency key for duplicate prevention
        )
    
    async def process_outbox_notifications(self) -> Dict[str, int]:
        """
        Process pending notifications from the outbox queue
        
        Uses SELECT FOR UPDATE SKIP LOCKED to safely process notifications
        in a distributed environment with multiple workers.
        
        Returns:
            Dict with processing statistics
        """
        results = {"processed": 0, "successful": 0, "failed": 0, "skipped": 0}
        batch_size = 50  # Process up to 50 notifications per cycle
        
        try:
            # Get pending notifications with row-level locking
            async with AsyncSessionLocal() as session:
                # Use async session patterns for safe processing
                now = datetime.utcnow()
                # Include stale "sending" rows to prevent crash-stranding
                stale_threshold = now - timedelta(minutes=5)  # 5 minute lease timeout
                
                query = (
                    select(NotificationQueue)
                    .where(
                        and_(
                            or_(
                                NotificationQueue.status == "pending",
                                and_(
                                    NotificationQueue.status == "sending",
                                    NotificationQueue.sent_at < stale_threshold  # Stale sending records
                                )
                            ),
                            or_(
                                NotificationQueue.scheduled_at.is_(None),
                                NotificationQueue.scheduled_at <= now
                            )
                        )
                    )
                    .order_by(NotificationQueue.scheduled_at, NotificationQueue.priority)
                    .limit(batch_size)
                    .with_for_update(skip_locked=True)
                )
                
                result = await session.execute(query)
                pending_notifications = list(result.scalars())
                
                if not pending_notifications:
                    return results
                
                # CRITICAL FIX: Extract all needed data into dictionaries BEFORE session closes
                # This prevents "Instance <NotificationQueue> is not bound to a Session" errors
                pending_notification_dicts = []
                for n in pending_notifications:
                    pending_notification_dicts.append({
                        'id': n.id,
                        'user_id': n.user_id,
                        'channel': n.channel,
                        'recipient': n.recipient,
                        'subject': n.subject,
                        'content': n.content,
                        'template_data': n.template_data,
                        'priority': n.priority,
                        'idempotency_key': n.idempotency_key
                    })
                
                # Mark notifications as 'sending' with processing timestamp to prevent duplicate processing
                notification_ids = [n['id'] for n in pending_notification_dicts]
                processing_start = datetime.utcnow()
                # Use async session pattern for updates - CRITICAL: Set sent_at as processing timestamp
                update_query = (
                    update(NotificationQueue)
                    .where(NotificationQueue.id.in_(notification_ids))
                    .values(
                        status="sending",
                        sent_at=processing_start  # Use sent_at as processing lease timestamp
                    )
                )
                await session.execute(update_query)
                
                await session.commit()
            
            # Process each notification dictionary (not ORM objects) to avoid session binding issues
            for notification_dict in pending_notification_dicts:
                results["processed"] += 1
                
                try:
                    # Convert notification dictionary back to NotificationRequest
                    request = self._queue_record_to_request(notification_dict)
                    
                    # Send the notification using existing delivery pipeline
                    # CRITICAL FIX: Pass from_outbox=True to prevent duplicate retry scheduling
                    delivery_results = await self.send_notification(request, from_outbox=True)
                    
                    # Determine overall success
                    successful_deliveries = [
                        result for result in delivery_results.values()
                        if result.status in [DeliveryStatus.SENT, DeliveryStatus.DELIVERED]
                    ]
                    
                    # Update notification status based on delivery results
                    notification_id_int = notification_dict['id']  # Direct access from dict
                    await self._update_outbox_status(
                        notification_id_int,  # Pass integer ID
                        successful_deliveries,
                        delivery_results
                    )
                    
                    if successful_deliveries:
                        results["successful"] += 1
                    else:
                        results["failed"] += 1
                        # Note: _update_outbox_status already handles retry scheduling
                    
                except Exception as e:
                    results["failed"] += 1
                    logger.error(f"❌ OUTBOX_ERROR: {notification_dict['id']} processing error: {e}")
                    await self._handle_outbox_error(notification_dict, str(e))
            
            return results
            
        except Exception as e:
            logger.error(f"❌ OUTBOX_PROCESSING_ERROR: {e}")
            results["failed"] = results.get("processed", 0)
            return results
    
    def _queue_record_to_request(self, record: Dict[str, Any]) -> NotificationRequest:
        """Convert NotificationQueue record dictionary back to NotificationRequest for processing"""
        
        # Parse channels from record dictionary - safely extract values
        try:
            channel_value = record.get('channel', 'telegram')
            channels = [NotificationChannel(channel_value)]
        except (ValueError, KeyError) as e:
            logger.warning(f"Invalid channel value '{record.get('channel')}': {e}, using Telegram")
            channels = [NotificationChannel.TELEGRAM]
        
        # Add additional channels based on priority
        try:
            priority_value = record.get('priority', 1)
            if priority_value == 1:  # High priority notifications
                channels = [NotificationChannel.TELEGRAM, NotificationChannel.EMAIL]
        except (TypeError, ValueError) as e:
            logger.warning(f"Invalid priority value: {e}, defaulting to 1")
            priority_value = 1
        
        # Extract actual values from database record dictionary safely
        try:
            user_id_value = record.get('user_id', 0)
            subject_value = record.get('subject', None) or "Notification"
            content_value = record.get('content', '') or ""
            idempotency_key_value = record.get('idempotency_key', None)
            
            # Fix template_data deserialization
            raw_template_data = record.get('template_data', None)
            if raw_template_data:
                if isinstance(raw_template_data, str):
                    try:
                        template_data_value = json.loads(raw_template_data)
                    except json.JSONDecodeError:
                        logger.warning(f"Failed to parse template_data JSON for record {record.get('id')}")
                        template_data_value = {}
                elif isinstance(raw_template_data, dict):
                    template_data_value = raw_template_data
                else:
                    template_data_value = {}
            else:
                template_data_value = None
        except (AttributeError, KeyError, TypeError) as e:
            logger.warning(f"Error extracting notification data: {e}, using defaults")
            user_id_value = 0
            subject_value = "Notification"
            content_value = ""
            template_data_value = None
            idempotency_key_value = None
        
        return NotificationRequest(
            user_id=user_id_value,
            category=NotificationCategory.PAYMENTS,  # Default to payments category
            priority=NotificationPriority.HIGH if priority_value == 1 else NotificationPriority.NORMAL,
            title=subject_value,
            message=content_value,
            template_data=template_data_value,
            channels=channels,
            require_delivery=priority_value == 1,  # High priority requires delivery
            broadcast_mode=priority_value == 1,  # High priority uses broadcast
            idempotency_key=idempotency_key_value
        )
    
    async def _update_outbox_status(
        self, 
        notification_id: int,  # Accept integer ID to match database type
        successful_deliveries: List[DeliveryResult], 
        all_results: Dict[str, DeliveryResult]
    ):
        """Update notification status in outbox after delivery attempt"""
        
        try:
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    if successful_deliveries:
                        # Mark as sent with delivery details
                        [
                            result.message_id for result in successful_deliveries 
                            if result.message_id
                        ]
                        
                        # Use async session pattern for success update
                        success_update = (
                            update(NotificationQueue)
                            .where(NotificationQueue.id == notification_id)
                            .values(
                                status="sent",
                                sent_at=datetime.utcnow()
                            )
                        )
                        result = await session.execute(success_update)
                        # Note: sent_at is updated here with actual delivery time (overwrites processing timestamp)
                    else:
                        # Mark as failed and potentially schedule retry
                        error_messages = [
                            result.error for result in all_results.values() 
                            if result.error
                        ]
                        
                        # Check if any delivery result is permanently failed
                        has_permanent_failure = any(
                            result.status == DeliveryStatus.PERMANENTLY_FAILED 
                            for result in all_results.values()
                        )
                        
                        if has_permanent_failure:
                            # Permanent failure - DO NOT RETRY
                            failure_update = (
                                update(NotificationQueue)
                                .where(NotificationQueue.id == notification_id)
                                .values(
                                    status="failed",
                                    error_message="; ".join(error_messages[:3])
                                )
                            )
                            result = await session.execute(failure_update)
                            logger.warning(
                                f"🚫 PERMANENT_FAILURE: Notification {notification_id} marked as permanently failed. "
                                f"No retries will be attempted. Errors: {'; '.join(error_messages[:3])}"
                            )
                        else:
                            # Transient failure - check if retries are available
                            # Get current retry count using async session
                            notification_query = (
                            select(NotificationQueue)
                            .where(NotificationQueue.id == notification_id)
                            )
                            result = await session.execute(notification_query)
                            notification = result.scalar_one_or_none()
                            
                            max_retries = 3  # Default max retries
                            current_retry_count = getattr(notification, 'retry_count', 0)
                            if notification and current_retry_count < max_retries:
                                # Schedule retry with exponential backoff
                                backoff_delay = min(300 * (2 ** current_retry_count), 3600)  # Cap at 1 hour
                                next_attempt = datetime.utcnow() + timedelta(seconds=backoff_delay)
                                
                                retry_update = (
                                    update(NotificationQueue)
                                    .where(NotificationQueue.id == notification_id)
                                    .values(
                                        status="pending",  # Back to pending for retry
                                        retry_count=current_retry_count + 1,
                                        scheduled_at=next_attempt,  # Proper backoff scheduling
                                        error_message="; ".join(error_messages[:3])  # Store first 3 errors
                                    )
                                )
                                result = await session.execute(retry_update)
                            else:
                                # Max retries reached - mark as permanently failed
                                failure_update = (
                                    update(NotificationQueue)
                                    .where(NotificationQueue.id == notification_id)
                                    .values(
                                        status="failed",
                                        error_message="; ".join(error_messages[:3])
                                    )
                                )
                                result = await session.execute(failure_update)
        except Exception as e:
            logger.error(f"❌ Error updating outbox status for {notification_id}: {e}")
    
    async def _handle_outbox_failure(self, notification: NotificationQueue, delivery_results: Dict[str, DeliveryResult]):
        """Handle failed notification delivery from outbox"""
        current_retry = getattr(notification, 'retry_count', 0)
        logger.warning(
            f"⚠️ OUTBOX_DELIVERY_FAILED: {notification.id} "
            f"(attempt {current_retry + 1}/3)"
        )
        
        # Additional failure handling can be added here
        # e.g., escalation, alternative channels, admin alerts
        
    async def _handle_outbox_error(self, notification_dict: Dict[str, Any], error_message: str):
        """Handle processing error for outbox notification"""
        try:
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    # Use SQLAlchemy 2.0 async pattern for error update
                    error_update = (
                        update(NotificationQueue)
                        .where(NotificationQueue.id == notification_dict['id'])
                        .values(
                            status="failed",
                            error_message=error_message
                        )
                    )
                    await session.execute(error_update)
        except Exception as e:
            logger.error(f"❌ Error updating outbox error status: {e}")
    
    async def _get_user_context(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Get user data and context in transaction-safe manner"""
        try:
            # SAFE PATTERN: Quick, separate transaction for data extraction
            async with AsyncSessionLocal() as session:
                # FIXED: Use SQLAlchemy 2.0 async pattern instead of session.query
                result = await session.execute(select(User).where(User.id == user_id))
                user = result.scalar_one_or_none()
                if not user:
                    return None
                
                # Extract all needed data while in transaction - using safe attribute access
                context = {
                    "user_id": user.id,
                    "telegram_id": user.telegram_id,
                    "username": getattr(user, 'username', None) or "User",
                    "first_name": getattr(user, 'first_name', None) or "",
                    "last_name": getattr(user, 'last_name', None) or "",
                    "email": user.email,
                    "email_eligible": safe_email_eligibility(user),
                    "sms_eligible": safe_sms_eligibility(user),
                    "telegram_eligible": safe_telegram_eligibility(user),
                    "status": user.status,
                    # Safe access to optional attributes that may not exist
                    "phone_number": getattr(user, 'phone_number', None),
                    "primary_notification_channel": getattr(user, 'primary_notification_channel', None),
                    "notification_preferences": getattr(user, 'notification_preferences', {}),
                    "onboarding_completed": getattr(user, 'onboarding_completed', False),
                }
                
                return context
            # Transaction committed here - database lock released
            
        except Exception as e:
            logger.error(f"❌ Error getting user context for {user_id}: {e}")
            return None
    
    async def _determine_effective_channels(
        self, 
        request: NotificationRequest, 
        user_context: Dict[str, Any]
    ) -> List[NotificationChannel]:
        """Determine which channels to use based on preferences and availability"""
        available_channels = self._get_available_channels()
        requested_channels = request.channels or [NotificationChannel.TELEGRAM, NotificationChannel.EMAIL]
        
        # Admin notifications use admin alert channel
        if request.admin_notification:
            return [NotificationChannel.ADMIN_ALERT]
        
        # Create a proper user object with notification_preferences attribute
        class TempUser:
            def __init__(self, context):
                self.telegram_id = context["telegram_id"]
                self.notification_preferences = context.get("notification_preferences", {})
        
        temp_user = TempUser(user_context)
        get_user_preferences(temp_user)
        
        effective_channels = []
        
        for channel in requested_channels:
            # Skip if channel not available
            if channel not in available_channels:
                continue
                
            # Check user preferences and channel-specific requirements
            if channel == NotificationChannel.TELEGRAM:
                if (user_context["telegram_id"] and 
                    is_enabled(temp_user, request.category.value, "telegram")):
                    effective_channels.append(channel)
                    
            elif channel == NotificationChannel.EMAIL:
                # CRITICAL FIX: For HIGH priority escrow updates, send email if user has verified email
                # This ensures important transaction notifications are delivered even without explicit preferences
                if user_context["email_eligible"]:
                    if (is_enabled(temp_user, request.category.value, "email") or
                        (request.priority == NotificationPriority.HIGH and 
                         request.category == NotificationCategory.ESCROW_UPDATES)):
                        effective_channels.append(channel)
                        if request.priority == NotificationPriority.HIGH and request.category == NotificationCategory.ESCROW_UPDATES:
                            logger.info(f"📧 EMAIL_FORCED: High-priority escrow update to user {request.user_id} with verified email")
                    
            elif channel == NotificationChannel.SMS:
                if (user_context["sms_eligible"] and self.twilio_client):
                    # SMS doesn't use category preferences - usually for critical notifications
                    effective_channels.append(channel)
        
        # For critical/high priority notifications, ensure at least one channel is available
        if (request.priority in [NotificationPriority.CRITICAL, NotificationPriority.HIGH] and 
            not effective_channels and 
            NotificationChannel.TELEGRAM in available_channels):
            # Force Telegram for critical/high priority notifications even if preferences are disabled
            # This ensures welcome notifications and critical alerts reach new users who haven't set preferences yet
            if user_context["telegram_id"]:
                effective_channels.append(NotificationChannel.TELEGRAM)
                logger.warning(f"🚨 Forcing Telegram for {request.priority.value} priority notification to user {request.user_id}")
        
        return effective_channels
    
    def _is_broadcast_category(self, category: NotificationCategory) -> bool:
        """Determine if notification category should use broadcast mode"""
        broadcast_categories = {
            NotificationCategory.MAINTENANCE,
            NotificationCategory.MARKETING,
            NotificationCategory.ADMIN_ALERTS,
            NotificationCategory.SECURITY_ALERTS
        }
        return category in broadcast_categories
    
    def _get_channel_preference_order(self, user_context: Dict[str, Any]) -> List[NotificationChannel]:
        """Get user's preferred channel order for fallback"""
        # Check user's primary notification channel preference
        primary_channel = user_context.get("primary_notification_channel")
        
        # Default fallback order: Telegram → Email → SMS
        default_order = [NotificationChannel.TELEGRAM, NotificationChannel.EMAIL, NotificationChannel.SMS]
        
        if primary_channel:
            # Move primary channel to front
            try:
                channel_enum = NotificationChannel(primary_channel.lower())
                if channel_enum in default_order:
                    default_order.remove(channel_enum)
                    default_order.insert(0, channel_enum)
            except ValueError:
                pass  # Invalid primary channel, use default order
        
        return default_order
    
    async def _send_broadcast_notification(
        self, 
        channels: List[NotificationChannel], 
        request: NotificationRequest,
        user_context: Dict[str, Any],
        notification_id: str
    ) -> Dict[str, DeliveryResult]:
        """Send notification to all channels simultaneously (broadcast mode)"""
        delivery_results = {}
        
        # Send to all channels in parallel
        tasks = []
        for channel in channels:
            task = self._send_through_channel(
                channel=channel,
                request=request,
                user_context=user_context,
                notification_id=notification_id
            )
            tasks.append((channel, task))
        
        # Wait for all channels to complete
        for channel, task in tasks:
            try:
                result = await task
                delivery_results[channel.value] = result
                
                if result.status in [DeliveryStatus.SENT, DeliveryStatus.DELIVERED]:
                    self.delivery_stats[f"{channel.value}_success"] += 1
                else:
                    self.delivery_stats[f"{channel.value}_failure"] += 1
                    
            except Exception as channel_error:
                logger.error(f"❌ Broadcast channel {channel.value} failed for {notification_id}: {channel_error}")
                error_result = DeliveryResult(
                    channel=channel,
                    status=DeliveryStatus.FAILED,
                    error=str(channel_error)
                )
                delivery_results[channel.value] = error_result
                self.delivery_stats[f"{channel.value}_error"] += 1
        
        return delivery_results
    
    async def _send_fallback_notification(
        self, 
        channels: List[NotificationChannel], 
        request: NotificationRequest,
        user_context: Dict[str, Any],
        notification_id: str
    ) -> tuple[Dict[str, DeliveryResult], List[str]]:
        """Send notification with proper fallback logic - stop on first success"""
        delivery_results = {}
        successful_channels = []
        
        # Get user's preferred channel order
        preferred_order = self._get_channel_preference_order(user_context)
        
        # Order channels by user preference
        ordered_channels = []
        for preferred_channel in preferred_order:
            if preferred_channel in channels:
                ordered_channels.append(preferred_channel)
        
        # Add any remaining channels not in preferred order
        for channel in channels:
            if channel not in ordered_channels:
                ordered_channels.append(channel)
        
        logger.info(f"🎯 FALLBACK_MODE: Trying {notification_id} in order: {[ch.value for ch in ordered_channels]}")
        
        # Try channels in order, stop on first success
        for channel in ordered_channels:
            try:
                result = await self._send_through_channel(
                    channel=channel,
                    request=request,
                    user_context=user_context,
                    notification_id=notification_id
                )
                
                delivery_results[channel.value] = result
                
                if result.status in [DeliveryStatus.SENT, DeliveryStatus.DELIVERED]:
                    successful_channels.append(channel.value)
                    self.delivery_stats[f"{channel.value}_success"] += 1
                    logger.info(f"✅ FALLBACK_SUCCESS: {notification_id} delivered via {channel.value} (primary attempt)")
                    break  # CRITICAL: Stop on first success
                else:
                    self.delivery_stats[f"{channel.value}_failure"] += 1
                    logger.warning(f"⚠️ FALLBACK_CONTINUE: {channel.value} failed for {notification_id}, trying next channel")
                    
            except Exception as channel_error:
                logger.error(f"❌ Fallback channel {channel.value} failed for {notification_id}: {channel_error}")
                error_result = DeliveryResult(
                    channel=channel,
                    status=DeliveryStatus.FAILED,
                    error=str(channel_error)
                )
                delivery_results[channel.value] = error_result
                self.delivery_stats[f"{channel.value}_error"] += 1
        
        return delivery_results, successful_channels
    
    def _serialize_template_data(self, template_data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Convert datetime objects to ISO strings for JSON serialization"""
        if not template_data:
            return template_data
            
        import json
        from datetime import datetime, date
        
        def datetime_serializer(obj):
            """JSON serializer for datetime objects"""
            if isinstance(obj, (datetime, date)):
                return obj.isoformat()
            raise TypeError(f"Object of type {type(obj)} is not JSON serializable")
        
        try:
            # Convert to JSON string and back to ensure serializability
            json_string = json.dumps(template_data, default=datetime_serializer)
            return json.loads(json_string)
        except Exception as e:
            logger.warning(f"⚠️ Template data serialization failed, using fallback: {e}")
            # Fallback: remove problematic fields
            serializable_data = {}
            for key, value in template_data.items():
                try:
                    json.dumps(value, default=datetime_serializer)
                    serializable_data[key] = value
                except (TypeError, ValueError, AttributeError) as json_err:
                    logger.debug(f"Skipping non-serializable field '{key}': {json_err}")
            return serializable_data

    async def _schedule_persistent_retry(
        self, 
        request: NotificationRequest, 
        user_context: Dict[str, Any],
        notification_id: str,
        failed_results: Dict[str, DeliveryResult]
    ) -> bool:
        """Schedule notification retry using persistent database storage"""
        try:
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    # Determine retry configuration
                    retry_config = request.retry_config or self._get_default_retry_config(request.priority)
                    
                    # Calculate first retry delay
                    initial_delay = retry_config.get("initial_delay", 60)
                    next_retry_time = datetime.utcnow() + timedelta(seconds=initial_delay)
                    
                    # Create persistent notification queue entry
                    primary_channel = self._get_primary_failed_channel(failed_results)
                    recipient = self._resolve_recipient(request.user_id, primary_channel)
                    
                    if not recipient:
                        logger.error(f"Cannot resolve recipient for user {request.user_id} on channel {primary_channel}")
                        return False
                    
                    priority_int = 1 if request.priority in [NotificationPriority.HIGH, NotificationPriority.CRITICAL] else 3
                    
                    notification_queue = NotificationQueue(
                        # Only use existing fields
                        user_id=request.user_id,
                        channel=primary_channel,
                        recipient=recipient,  # Required field
                        subject=request.title,
                        content=request.message,
                        template_name=None,
                        template_data=self._serialize_template_data(request.template_data),
                        status='pending',  # Will be processed by outbox
                        priority=priority_int,  # Integer not string
                        scheduled_at=next_retry_time,  # Use for retry scheduling
                        retry_count=0,
                        error_message=self._get_primary_error_message(failed_results),
                        idempotency_key=request.idempotency_key  # Store idempotency key for duplicate prevention
                    )
                    
                    session.add(notification_queue)
                    # Commit happens automatically when session.begin() context exits
                    
                    logger.info(f"📅 PERSISTENT_RETRY_SCHEDULED: {notification_id} for retry at {next_retry_time}")
                    return True
                
        except Exception as e:
            logger.error(f"❌ Failed to schedule persistent retry for {notification_id}: {e}")
            return False
    
    async def _record_notification_activity(
        self, 
        request: NotificationRequest, 
        user_context: Dict[str, Any],
        delivery_results: Dict[str, DeliveryResult],
        was_successful: bool
    ) -> None:
        """Record notification activity for analytics and optimization"""
        try:
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    activity_id = f"activity_{request.user_id}_{int(time.time())}"
                    
                    # Record activity for each channel attempted
                    for channel_name, result in delivery_results.items():
                        try:
                            channel_enum = NotificationChannel(channel_name)
                            
                            # Determine channel value based on type
                            if channel_enum == NotificationChannel.TELEGRAM:
                                channel_value = str(user_context.get("telegram_id", ""))
                            elif channel_enum == NotificationChannel.EMAIL:
                                channel_value = user_context.get("email", "")
                            elif channel_enum == NotificationChannel.SMS:
                                channel_value = user_context.get("phone_number", "")
                            else:
                                channel_value = channel_name
                            
                            activity = NotificationActivity(
                                activity_id=f"{activity_id}_{channel_name}",
                                user_id=request.user_id,
                                notification_type=request.category.value,
                                channel_type=channel_name,
                                channel_value=channel_value,
                                sent_at=datetime.utcnow(),
                                delivered_at=result.timestamp if result.status == DeliveryStatus.DELIVERED else None,
                                delivery_status=result.status.value,
                                engagement_level="opened" if was_successful else "none",
                                priority_score=1.0 if was_successful else 0.5,
                                idempotency_key=request.idempotency_key  # Store idempotency key for duplicate prevention
                            )
                            
                            session.add(activity)
                            
                        except ValueError:
                            # Invalid channel name, skip
                            continue
                    
                    # Commit happens automatically when session.begin() context exits
                    logger.debug(f"📊 Recorded notification activity for {len(delivery_results)} channels")
                
        except Exception as e:
            logger.error(f"❌ Failed to record notification activity: {e}")
    
    def _get_default_retry_config(self, priority: NotificationPriority) -> Dict[str, Any]:
        """Get default retry configuration based on notification priority"""
        if priority == NotificationPriority.CRITICAL:
            return {
                "max_attempts": 10,
                "initial_delay": 30,
                "backoff_multiplier": 1.5,
                "max_delay": 1800  # 30 minutes
            }
        elif priority == NotificationPriority.HIGH:
            return {
                "max_attempts": 5,
                "initial_delay": 60,
                "backoff_multiplier": 2.0,
                "max_delay": 3600  # 1 hour
            }
        else:
            return {
                "max_attempts": 3,
                "initial_delay": 300,
                "backoff_multiplier": 2.0,
                "max_delay": 7200  # 2 hours
            }
    
    def _get_primary_failed_channel(self, failed_results: Dict[str, DeliveryResult]) -> str:
        """Get the primary channel that failed for retry purposes"""
        # Return first failed channel or telegram as default
        for channel_name, result in failed_results.items():
            if result.status == DeliveryStatus.FAILED:
                return channel_name
        return "telegram"
    
    def _get_primary_error_message(self, failed_results: Dict[str, DeliveryResult]) -> str:
        """Get primary error message from failed delivery results"""
        error_messages = []
        for channel_name, result in failed_results.items():
            if result.error:
                error_messages.append(f"{channel_name}: {result.error}")
        return "; ".join(error_messages) if error_messages else "All channels failed"
    
    def _reconstruct_notification_request(self, notification: NotificationQueue) -> NotificationRequest:
        """Reconstruct NotificationRequest from database record"""
        # Since notification_type doesn't exist, default to PAYMENTS
        category = NotificationCategory.PAYMENTS
            
        try:
            priority_val = getattr(notification, 'priority', 1)
            if priority_val == 1:
                priority = NotificationPriority.HIGH
            else:
                priority = NotificationPriority.NORMAL
        except (AttributeError, ValueError) as e:
            logger.warning(f"Error getting priority: {e}, using NORMAL")
            priority = NotificationPriority.NORMAL
            
        try:
            channel_val = getattr(notification, 'channel', 'telegram')
            channel = NotificationChannel(channel_val)
            channels = [channel]
        except (AttributeError, ValueError) as e:
            logger.warning(f"Error getting channel: {e}, using TELEGRAM")
            channels = [NotificationChannel.TELEGRAM]
        
        # Safe access to record fields
        user_id_val = getattr(notification, 'user_id', 0)
        subject_val = getattr(notification, 'subject', None) or "Notification"
        content_val = getattr(notification, 'content', '') 
        template_data_val = getattr(notification, 'template_data', None)
        
        return NotificationRequest(
            user_id=user_id_val,
            category=category,
            priority=priority,
            title=subject_val,
            message=content_val,
            template_data=template_data_val,
            channels=channels,
            require_delivery=priority in [NotificationPriority.HIGH, NotificationPriority.CRITICAL],
            admin_notification=False,
            broadcast_mode=False
        )
    
    def _get_retry_config_from_notification(self, notification: NotificationQueue) -> Dict[str, Any]:
        """Extract retry configuration from notification record"""
        # Use template_data if it contains retry config, otherwise use defaults
        try:
            template_data_val = getattr(notification, 'template_data', None)
            if template_data_val and isinstance(template_data_val, dict):
                retry_config = template_data_val.get("retry_config", {})
                if retry_config:
                    return retry_config
        except (AttributeError, TypeError, KeyError) as e:
            logger.debug(f"Could not extract retry config from notification template_data: {e}")
        
        # Fall back to default based on priority
        try:
            priority = NotificationPriority(notification.priority)
            return self._get_default_retry_config(priority)
        except ValueError:
            return self._get_default_retry_config(NotificationPriority.NORMAL)
    
    def _calculate_retry_delay(self, retry_count: int, retry_config: Dict[str, Any]) -> int:
        """Calculate exponential backoff delay for retry"""
        initial_delay = retry_config.get("initial_delay", 60)
        backoff_multiplier = retry_config.get("backoff_multiplier", 2.0)
        max_delay = retry_config.get("max_delay", 3600)
        
        if retry_config.get("exponential", True):
            delay = initial_delay * (backoff_multiplier ** retry_count)
        else:
            delay = initial_delay
        
        return min(int(delay), max_delay)
    
    async def _send_through_channel(
        self, 
        channel: NotificationChannel, 
        request: NotificationRequest, 
        user_context: Dict[str, Any],
        notification_id: str
    ) -> DeliveryResult:
        """Send notification through specific channel"""
        start_time = time.time()
        
        try:
            if channel == NotificationChannel.TELEGRAM:
                return await self._send_telegram_notification(
                    request, user_context, notification_id, start_time
                )
            elif channel == NotificationChannel.EMAIL:
                return await self._send_email_notification(
                    request, user_context, notification_id, start_time
                )
            elif channel == NotificationChannel.SMS:
                return await self._send_sms_notification(
                    request, user_context, notification_id, start_time
                )
            elif channel == NotificationChannel.ADMIN_ALERT:
                return await self._send_admin_alert_notification(
                    request, user_context, notification_id, start_time
                )
            else:
                return DeliveryResult(
                    channel=channel,
                    status=DeliveryStatus.FAILED,
                    error=f"Unsupported channel: {channel.value}"
                )
                
        except Exception as e:
            response_time_ms = int((time.time() - start_time) * 1000)
            logger.error(f"❌ {channel.value} delivery error for {notification_id}: {e}")
            return DeliveryResult(
                channel=channel,
                status=DeliveryStatus.FAILED,
                error=str(e),
                response_time_ms=response_time_ms
            )
    
    async def _send_telegram_notification(
        self, 
        request: NotificationRequest, 
        user_context: Dict[str, Any],
        notification_id: str,
        start_time: float
    ) -> DeliveryResult:
        """Send Telegram notification with proper formatting"""
        try:
            if not Config.BOT_TOKEN or not user_context["telegram_id"]:
                return DeliveryResult(
                    channel=NotificationChannel.TELEGRAM,
                    status=DeliveryStatus.FAILED,
                    error="Bot token or user Telegram ID not available"
                )
            
            bot = Bot(Config.BOT_TOKEN)
            
            # Format message based on priority
            formatted_message = self._format_telegram_message(request, user_context)
            
            # Add inline keyboard if provided in template data
            reply_markup = None
            if request.template_data and "keyboard" in request.template_data and request.template_data["keyboard"] is not None:
                logger.info(f"🎮 KEYBOARD_DEBUG: Found keyboard in template_data with {len(request.template_data['keyboard'])} rows")
                reply_markup = self._create_telegram_keyboard(request.template_data["keyboard"])
                logger.info(f"🎮 KEYBOARD_DEBUG: Created InlineKeyboardMarkup: {reply_markup}")
            else:
                logger.info(f"🎮 KEYBOARD_DEBUG: No keyboard in template_data. template_data keys: {list(request.template_data.keys()) if request.template_data else 'None'}")
            
            # Get parse mode from template_data or default to Markdown
            parse_mode = "Markdown"
            if request.template_data and "parse_mode" in request.template_data:
                parse_mode = request.template_data["parse_mode"]
            
            # Send message
            telegram_message = await bot.send_message(
                chat_id=int(user_context["telegram_id"]),
                text=formatted_message,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
                disable_web_page_preview=True
            )
            
            response_time_ms = int((time.time() - start_time) * 1000)
            
            logger.info(f"📱 Telegram message sent to user {request.user_id} (msg_id: {telegram_message.message_id})")
            
            return DeliveryResult(
                channel=NotificationChannel.TELEGRAM,
                status=DeliveryStatus.DELIVERED,
                message_id=str(telegram_message.message_id),
                response_time_ms=response_time_ms
            )
            
        except TelegramError as e:
            response_time_ms = int((time.time() - start_time) * 1000)
            error_message = str(e)
            
            # Detect permanent failures that should not be retried
            permanent_error_indicators = [
                "Chat not found",
                "Forbidden: bot was blocked by the user",
                "Forbidden: user is deactivated",
                "Forbidden: bot can't initiate conversation",
                "USER_DEACTIVATED",
                "PEER_ID_INVALID"
            ]
            
            is_permanent = any(indicator in error_message for indicator in permanent_error_indicators)
            
            if is_permanent:
                logger.warning(
                    f"🚫 TELEGRAM_PERMANENT_FAILURE: User {request.user_id} unreachable - {error_message}. "
                    "Marking as permanently failed to prevent infinite retries."
                )
                return DeliveryResult(
                    channel=NotificationChannel.TELEGRAM,
                    status=DeliveryStatus.PERMANENTLY_FAILED,
                    error=f"Permanent failure: {error_message}",
                    response_time_ms=response_time_ms
                )
            else:
                # Transient error - can be retried
                logger.error(f"❌ Telegram delivery failed for {notification_id}: {error_message}")
                return DeliveryResult(
                    channel=NotificationChannel.TELEGRAM,
                    status=DeliveryStatus.FAILED,
                    error=f"Telegram error: {error_message}",
                    response_time_ms=response_time_ms
                )
    
    async def _send_email_notification(
        self, 
        request: NotificationRequest, 
        user_context: Dict[str, Any],
        notification_id: str,
        start_time: float
    ) -> DeliveryResult:
        """Send email notification with proper templates"""
        try:
            if not self.email_service.enabled or not user_context["email"]:
                return DeliveryResult(
                    channel=NotificationChannel.EMAIL,
                    status=DeliveryStatus.FAILED,
                    error="Email service not enabled or user email not available"
                )
            
            # Generate email content
            email_content = self._generate_email_content(request, user_context)
            
            # Send email
            success = self.email_service.send_email(
                to_email=user_context["email"],
                subject=email_content["subject"],
                html_content=email_content["html"],
                text_content=email_content["text"]
            )
            
            response_time_ms = int((time.time() - start_time) * 1000)
            
            if success:
                logger.info(f"📧 Email sent to user {request.user_id} ({user_context['email']})")
                return DeliveryResult(
                    channel=NotificationChannel.EMAIL,
                    status=DeliveryStatus.SENT,
                    response_time_ms=response_time_ms
                )
            else:
                return DeliveryResult(
                    channel=NotificationChannel.EMAIL,
                    status=DeliveryStatus.FAILED,
                    error="Email service returned false",
                    response_time_ms=response_time_ms
                )
                
        except Exception as e:
            response_time_ms = int((time.time() - start_time) * 1000)
            logger.error(f"❌ Email delivery failed for {notification_id}: {e}")
            return DeliveryResult(
                channel=NotificationChannel.EMAIL,
                status=DeliveryStatus.FAILED,
                error=f"Email error: {str(e)}",
                response_time_ms=response_time_ms
            )
    
    async def _send_sms_notification(
        self, 
        request: NotificationRequest, 
        user_context: Dict[str, Any],
        notification_id: str,
        start_time: float
    ) -> DeliveryResult:
        """Send SMS notification via Twilio"""
        try:
            if not self.twilio_client or not user_context["phone_number"]:
                return DeliveryResult(
                    channel=NotificationChannel.SMS,
                    status=DeliveryStatus.FAILED,
                    error="Twilio not configured or user phone number not available"
                )
            
            # Format SMS message (limit to 160 characters)
            sms_message = self._format_sms_message(request, user_context)
            
            # Send SMS
            message = self.twilio_client.messages.create(
                body=sms_message,
                from_=Config.TWILIO_PHONE_NUMBER,
                to=user_context["phone_number"]
            )
            
            response_time_ms = int((time.time() - start_time) * 1000)
            
            logger.info(f"📱 SMS sent to user {request.user_id} ({user_context['phone_number']}) - SID: {message.sid}")
            
            return DeliveryResult(
                channel=NotificationChannel.SMS,
                status=DeliveryStatus.SENT,
                message_id=message.sid,
                response_time_ms=response_time_ms
            )
            
        except TwilioException as e:
            response_time_ms = int((time.time() - start_time) * 1000)
            logger.error(f"❌ SMS delivery failed for {notification_id}: {e}")
            return DeliveryResult(
                channel=NotificationChannel.SMS,
                status=DeliveryStatus.FAILED,
                error=f"Twilio error: {str(e)}",
                response_time_ms=response_time_ms
            )
    
    async def _send_admin_alert_notification(
        self, 
        request: NotificationRequest, 
        user_context: Dict[str, Any],
        notification_id: str,
        start_time: float
    ) -> DeliveryResult:
        """Send admin alert notification"""
        try:
            if not hasattr(Config, 'ADMIN_ALERT_EMAIL') or not Config.ADMIN_ALERT_EMAIL:
                return DeliveryResult(
                    channel=NotificationChannel.ADMIN_ALERT,
                    status=DeliveryStatus.FAILED,
                    error="Admin alert email not configured"
                )
            
            # Generate admin alert content
            admin_content = self._generate_admin_alert_content(request, user_context)
            
            # Send admin alert email
            success = self.email_service.send_email(
                to_email=Config.ADMIN_ALERT_EMAIL,
                subject=admin_content["subject"],
                html_content=admin_content["html"],
                text_content=admin_content["text"]
            )
            
            response_time_ms = int((time.time() - start_time) * 1000)
            
            if success:
                logger.info(f"🚨 Admin alert sent for user {request.user_id} - {request.category.value}")
                return DeliveryResult(
                    channel=NotificationChannel.ADMIN_ALERT,
                    status=DeliveryStatus.SENT,
                    response_time_ms=response_time_ms
                )
            else:
                return DeliveryResult(
                    channel=NotificationChannel.ADMIN_ALERT,
                    status=DeliveryStatus.FAILED,
                    error="Admin alert email service returned false",
                    response_time_ms=response_time_ms
                )
                
        except Exception as e:
            response_time_ms = int((time.time() - start_time) * 1000)
            logger.error(f"❌ Admin alert failed for {notification_id}: {e}")
            return DeliveryResult(
                channel=NotificationChannel.ADMIN_ALERT,
                status=DeliveryStatus.FAILED,
                error=f"Admin alert error: {str(e)}",
                response_time_ms=response_time_ms
            )
    
    def _format_telegram_message(self, request: NotificationRequest, user_context: Dict[str, Any]) -> str:
        """Format Telegram message with proper styling based on priority"""
        priority_icons = {
            NotificationPriority.LOW: "ℹ️",
            NotificationPriority.NORMAL: "📢",
            NotificationPriority.HIGH: "⚠️",
            NotificationPriority.CRITICAL: "🚨"
        }
        
        icon = priority_icons.get(request.priority, "📢")
        name = user_context.get("first_name") or user_context.get("username", "User")
        
        # Format based on category and priority
        if request.priority == NotificationPriority.CRITICAL:
            message = f"{icon} URGENT: {request.title}\n\n"
        else:
            message = f"{icon} {request.title}\n\n"
        
        message += f"Hi {name},\n\n"
        message += request.message
        
        # Add footer with brand
        message += f"\n\n—{Config.BRAND}"
        
        return message
    
    def _format_sms_message(self, request: NotificationRequest, user_context: Dict[str, Any]) -> str:
        """Format SMS message (160 character limit)"""
        # SMS messages need to be very concise
        brand = Config.BRAND or "LockBay"
        
        if request.priority == NotificationPriority.CRITICAL:
            prefix = "URGENT: "
        else:
            prefix = ""
        
        # Create base message
        base_msg = f"{prefix}{request.title}: {request.message}"
        
        # Add brand signature
        signature = f" -{brand}"
        
        # Truncate to fit SMS limit (160 chars)
        max_content_length = 160 - len(signature)
        if len(base_msg) > max_content_length:
            base_msg = base_msg[:max_content_length-3] + "..."
        
        return base_msg + signature
    
    def _generate_email_content(self, request: NotificationRequest, user_context: Dict[str, Any]) -> Dict[str, str]:
        """Generate email content with proper HTML templates"""
        name = f"{user_context.get('first_name', '')} {user_context.get('last_name', '')}".strip() or user_context.get('username', 'User')
        
        # Determine color scheme based on priority
        if request.priority == NotificationPriority.CRITICAL:
            header_color = "#dc3545"
            accent_color = "#721c24"
        elif request.priority == NotificationPriority.HIGH:
            header_color = "#fd7e14"
            accent_color = "#dc6002"
        else:
            header_color = "#667eea"
            accent_color = "#764ba2"
        
        subject = f"{request.title} - {Config.BRAND}"
        
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>{request.title}</title>
        </head>
        <body style="font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 0; padding: 0; background-color: #f8f9fa;">
            <div style="max-width: 600px; margin: 0 auto; background-color: #ffffff;">
                <div style="background: linear-gradient(135deg, {header_color} 0%, {accent_color} 100%); color: white; padding: 30px; text-align: center;">
                    <h1 style="margin: 0; font-size: 24px; font-weight: 600;">{request.title}</h1>
                    <p style="margin: 10px 0 0 0; opacity: 0.9;">{Config.BRAND}</p>
                </div>
                
                <div style="padding: 30px;">
                    <p style="margin: 0 0 20px 0;">Hi {name},</p>
                    
                    <div style="background: #f8f9fa; padding: 20px; border-radius: 8px; margin: 20px 0;">
                        {request.message}
                    </div>
                    
                    <p style="color: #666; font-size: 14px; margin-top: 30px;">
                        Need help? Reply to this email or contact our support team.
                    </p>
                </div>
                
                <div style="background: #f8f9fa; padding: 20px; text-align: center; color: #666; font-size: 12px;">
                    <p style="margin: 0;">&copy; {Config.BRAND} - Secure Trading Platform</p>
                    <p style="margin: 5px 0 0 0;">This email was sent regarding your account activity.</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        text_content = f"""
        {request.title} - {Config.BRAND}
        
        Hi {name},
        
        {request.message}
        
        Need help? Reply to this email or contact our support team.
        
        Best regards,
        {Config.BRAND} Team
        """
        
        return {
            "subject": subject,
            "html": html_content,
            "text": text_content
        }
    
    def _generate_admin_alert_content(self, request: NotificationRequest, user_context: Dict[str, Any]) -> Dict[str, str]:
        """Generate admin alert email content"""
        subject = f"🚨 Admin Alert: {request.title} - User {request.user_id}"
        
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>Admin Alert</title>
        </head>
        <body style="font-family: Arial, sans-serif; margin: 0; padding: 20px; background-color: #f5f5f5;">
            <div style="max-width: 800px; margin: 0 auto; background: white; border-radius: 8px; overflow: hidden;">
                <div style="background: #dc3545; color: white; padding: 20px;">
                    <h1 style="margin: 0; font-size: 20px;">🚨 Admin Alert: {request.title}</h1>
                    <p style="margin: 5px 0 0 0; opacity: 0.9;">Priority: {request.priority.value.upper()}</p>
                </div>
                
                <div style="padding: 20px;">
                    <h3>User Information:</h3>
                    <ul>
                        <li><strong>User ID:</strong> {user_context['user_id']}</li>
                        <li><strong>Username:</strong> {user_context['username']}</li>
                        <li><strong>Telegram ID:</strong> {user_context['telegram_id']}</li>
                        <li><strong>Email:</strong> {user_context['email']}</li>
                        <li><strong>Status:</strong> {user_context['status']}</li>
                    </ul>
                    
                    <h3>Alert Details:</h3>
                    <div style="background: #f8f9fa; padding: 15px; border-radius: 4px; margin: 10px 0;">
                        <p><strong>Category:</strong> {request.category.value}</p>
                        <p><strong>Message:</strong> {request.message}</p>
                        <p><strong>Timestamp:</strong> {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}</p>
                    </div>
                    
                    {self._format_template_data_for_admin(request.template_data) if request.template_data else ""}
                </div>
            </div>
        </body>
        </html>
        """
        
        text_content = f"""
        Admin Alert: {request.title}
        Priority: {request.priority.value.upper()}
        
        User Information:
        - User ID: {user_context['user_id']}
        - Username: {user_context['username']}
        - Telegram ID: {user_context['telegram_id']}
        - Email: {user_context['email']}
        - Status: {user_context['status']}
        
        Alert Details:
        Category: {request.category.value}
        Message: {request.message}
        Timestamp: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}
        
        {json.dumps(self._serialize_template_data(request.template_data), indent=2) if request.template_data else ''}
        """
        
        return {
            "subject": subject,
            "html": html_content,
            "text": text_content
        }
    
    def _format_template_data_for_admin(self, template_data: Dict[str, Any]) -> str:
        """Format template data for admin email"""
        if not template_data:
            return ""
        
        html = "<h3>Additional Data:</h3><div style='background: #f8f9fa; padding: 15px; border-radius: 4px;'>"
        for key, value in template_data.items():
            if key != "keyboard":  # Skip keyboard data
                html += f"<p><strong>{key.replace('_', ' ').title()}:</strong> {value}</p>"
        html += "</div>"
        return html
    
    def _create_telegram_keyboard(self, keyboard_data: List[List[Dict[str, str]]]) -> InlineKeyboardMarkup:
        """Create Telegram inline keyboard from data"""
        keyboard = []
        for row in keyboard_data:
            button_row = []
            for button in row:
                button_row.append(InlineKeyboardButton(
                    text=button["text"],
                    callback_data=button.get("callback_data"),
                    url=button.get("url")
                ))
            keyboard.append(button_row)
        return InlineKeyboardMarkup(keyboard)
    
    async def _schedule_retry(self, request: NotificationRequest, user_context: Dict[str, Any], notification_id: str):
        """Schedule retry for failed notification"""
        retry_config = request.retry_config or {
            "max_attempts": 3,
            "initial_delay": 300,  # 5 minutes
            "backoff_multiplier": 2.0
        }
        
        # Schedule retry using database queue (persistent retry mechanism)
        try:
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    # Create a new notification queue entry for retry
                    recipient = str(user_context.get("telegram_id", ""))
                    if not recipient:
                        logger.error(f"Cannot resolve recipient for user {request.user_id}")
                        return
                    
                    priority_int = 1 if request.priority == NotificationPriority.HIGH else 3
                    
                    retry_notification = NotificationQueue(
                        user_id=request.user_id,
                        channel="telegram",  # Default channel for retry
                        recipient=recipient,
                        subject=request.title,
                        content=request.message,
                        template_data=self._serialize_template_data(request.template_data),
                        status="pending",
                        priority=priority_int,  # Integer not string
                        retry_count=0,
                        scheduled_at=datetime.utcnow() + timedelta(seconds=retry_config["initial_delay"]),
                        error_message=None,
                        idempotency_key=request.idempotency_key  # Store idempotency key for duplicate prevention
                    )
                    session.add(retry_notification)
                    await session.commit()
                    
                    logger.info(f"📅 Scheduled retry for {notification_id} in {retry_config['initial_delay']} seconds")
        except Exception as e:
            logger.error(f"❌ Error scheduling retry for {notification_id}: {e}")
    
    def _create_blocked_notification_record(self, request: NotificationRequest, notification_id: str) -> NotificationQueue:
        """Create blocked notification record for audit trail"""
        priority_int = 3  # Default priority for blocked notifications
        
        return NotificationQueue(
            user_id=request.user_id,
            channel="blocked",  # Special status to indicate blocking
            recipient="no_channels_available",  # Descriptive recipient
            subject=request.title,
            content=request.message,
            template_name=None,
            template_data=self._serialize_template_data(request.template_data),
            status="failed",  # Mark as failed due to no channels
            priority=priority_int,
            scheduled_at=datetime.utcnow(),
            retry_count=0,
            error_message="No available notification channels for user",
            idempotency_key=request.idempotency_key  # Store idempotency key for duplicate prevention
        )
    
    def _resolve_recipient(self, user_id: int, channel: str) -> Optional[str]:
        """Resolve recipient address for given user and channel"""
        try:
            # TODO: This should integrate with actual user service/database to get real addresses
            # For now, using reasonable defaults that work with the current system
            
            if channel == "telegram":
                # For Telegram, user_id is the actual Telegram user ID
                return str(user_id)
            elif channel == "email":
                # Would need to query user model for actual email address
                # Returning None for now until proper user service integration
                logger.debug(f"Email channel requested for user {user_id} but no email resolution implemented")
                return None
            elif channel == "sms":
                # Would need to query user model for phone number
                # Returning None for now until proper user service integration  
                logger.debug(f"SMS channel requested for user {user_id} but no SMS resolution implemented")
                return None
            else:
                logger.warning(f"Unknown notification channel: {channel}")
                return None
        except Exception as e:
            logger.error(f"Error resolving recipient for user {user_id} on channel {channel}: {e}")
            return None
    
    
    # ================== HIGH-LEVEL CONVENIENCE METHODS ==================
    
    async def send_wallet_deposit_confirmation(
        self, 
        user_id: int, 
        amount: Union[Decimal, float], 
        currency: str, 
        txid: str,
        amount_usd: Optional[Union[Decimal, float]] = None
    ) -> Dict[str, DeliveryResult]:
        """Send wallet deposit confirmation"""
        amount_str = f"{amount} {currency}"
        if amount_usd:
            amount_str += f" (${amount_usd:.2f} USD)"
        
        request = NotificationRequest(
            user_id=user_id,
            category=NotificationCategory.PAYMENTS,
            priority=NotificationPriority.HIGH,
            title="💰 Wallet Deposit Confirmed",
            message=f"Your deposit of {amount_str} has been confirmed!\n\nTransaction: {txid[:8]}...{txid[-4:]}\n\nYour funds are now available in your wallet.",
            template_data={
                "amount": float(amount),
                "currency": currency,
                "amount_usd": float(amount_usd) if amount_usd else None,
                "txid": txid
            }
        )
        
        return await self.send_notification(request)
    
    async def send_escrow_payment_confirmation(
        self, 
        user_id: int, 
        amount: Union[Decimal, float], 
        currency: str, 
        txid: str,
        amount_usd: Optional[Union[Decimal, float]] = None,
        escrow_id: Optional[str] = None
    ) -> Dict[str, DeliveryResult]:
        """Send escrow payment confirmation with optional referral invite for non-onboarded sellers"""
        amount_str = f"{amount} {currency}"
        if amount_usd:
            amount_str += f" (${amount_usd:.2f} USD)"
        
        # Check if seller is onboarded and build referral section if needed
        referral_section = ""
        keyboard = []
        
        if escrow_id:
            try:
                from database import async_managed_session
                from models import Escrow, User
                from sqlalchemy import select
                from config import Config
                
                async with async_managed_session() as session:
                    # Get escrow details
                    escrow_stmt = select(Escrow).where(Escrow.escrow_id == escrow_id)
                    escrow_result = await session.execute(escrow_stmt)
                    escrow = escrow_result.scalar_one_or_none()
                    
                    if escrow and escrow.seller_id is None:
                        # Seller not onboarded, get buyer's referral code
                        buyer_stmt = select(User).where(User.id == user_id)
                        buyer_result = await session.execute(buyer_stmt)
                        buyer = buyer_result.scalar_one_or_none()
                        
                        if buyer and buyer.referral_code:
                            from urllib.parse import quote
                            referral_link = f"https://t.me/{Config.BOT_USERNAME}?start=ref_{buyer.referral_code}"
                            share_text = quote("Hey! Join me on Lockbay for secure trades 🛡️")
                            
                            # Don't include raw URL in message to avoid parse errors - button is enough
                            referral_section = """
━━━━━━━━━━━━━━━━━━

⚠️ Seller not on Lockbay

Tap Share Invite below
━━━━━━━━━━━━━━━━━━
"""
                            # Add Share Invite button with URL-encoded parameters
                            keyboard.append({"text": "📤 Share Invite", "url": f"https://t.me/share/url?url={quote(referral_link)}&text={share_text}"})
            except Exception as e:
                logger.error(f"Error checking seller onboarding status for referral invite: {e}")
        
        # Build message with optional referral section
        message = f"✅ Payment Confirmed!\n\n${amount_usd:.2f} USD received\nTx: {txid[:8]}...{txid[-4:]}{referral_section}\n\n⏰ Waiting for seller (24h)\n🔒 Funds secured"
        
        # Build keyboard
        keyboard.append({"text": "📋 View Trades", "callback_data": "view_trades"})
        
        request = NotificationRequest(
            user_id=user_id,
            category=NotificationCategory.ESCROW_UPDATES,
            priority=NotificationPriority.HIGH,
            title="✅ Payment Confirmed",
            message=message,
            template_data={
                "amount": float(amount),
                "currency": currency,
                "amount_usd": float(amount_usd) if amount_usd else None,
                "txid": txid,
                "keyboard": [[kb] for kb in keyboard]  # Wrap each button in a row
            }
        )
        
        return await self.send_notification(request)
    
    async def send_delivery_notification(self, buyer_id: int, escrow_id: str, seller_name: str, amount: Decimal) -> Dict[str, DeliveryResult]:
        """Send delivery confirmation notification"""
        request = NotificationRequest(
            user_id=buyer_id,
            category=NotificationCategory.ESCROW_UPDATES,
            priority=NotificationPriority.HIGH,
            title="📦 Item Delivered",
            message=f"Trade #{html.escape(escrow_id[-6:])} • ${amount:.2f} USD\n\nSeller: {format_username_html(seller_name)}\n\n✅ Item marked as delivered\nPlease release funds to complete",
            template_data={
                "escrow_id": escrow_id,
                "seller_name": seller_name,
                "amount": amount,
                "keyboard": [
                    [{"text": "✅ Release Funds", "callback_data": f"release_funds_{escrow_id}"}],
                    [
                        {"text": "📋 View Trade", "callback_data": f"view_trade_{escrow_id}"},
                        {"text": "💬 Support", "callback_data": "start_support_chat"}
                    ]
                ],
                "parse_mode": "HTML"
            }
        )
        
        return await self.send_notification(request)
    
    async def send_funds_released_notification(self, seller_id: int, escrow_id: str, amount: float, escrow_numeric_id: Optional[int] = None) -> Dict[str, DeliveryResult]:
        """Send funds released notification with dual-channel delivery (Telegram + Email)"""
        # Only include rating button if we have the numeric ID
        keyboard = [[{"text": "💰 View Wallet", "callback_data": "menu_wallet"}]]
        if escrow_numeric_id:
            keyboard.append([{"text": "⭐ Rate Trade", "callback_data": f"rate_escrow_{escrow_numeric_id}"}])
        
        request = NotificationRequest(
            user_id=seller_id,
            category=NotificationCategory.ESCROW_UPDATES,
            priority=NotificationPriority.HIGH,
            title="💰 Funds Released",
            message=f"Trade #{escrow_id[-6:]} completed!\n\n${amount:.2f} USD has been credited to your wallet.\n\nThank you for using {Config.BRAND}!",
            template_data={
                "escrow_id": escrow_id,
                "amount": amount,
                "keyboard": keyboard
            },
            broadcast_mode=True  # DUAL-CHANNEL: Send to both Telegram AND email
        )
        
        return await self.send_notification(request)
    
    async def send_escrow_cancelled(self, escrow, cancellation_reason: str) -> Dict[str, DeliveryResult]:
        """Send escrow cancellation notification to appropriate parties"""
        try:
            escrow_display_id = escrow.escrow_id[-6:] if len(escrow.escrow_id) > 6 else escrow.escrow_id
            delivery_results = {}
            
            if cancellation_reason == "seller_declined":
                # Notify buyer that seller declined (DUAL-CHANNEL: Telegram bot + email)
                buyer_request = NotificationRequest(
                    user_id=escrow.buyer_id,
                    category=NotificationCategory.ESCROW_UPDATES,
                    priority=NotificationPriority.HIGH,
                    title="❌ Trade Declined",
                    message=f"Trade #{escrow_display_id} was declined by the seller.\n\nYour payment of ${float(escrow.total_amount):.2f} has been refunded to your wallet.",
                    template_data={
                        "escrow_id": escrow.escrow_id,
                        "amount": float(escrow.total_amount),
                        "cancellation_reason": cancellation_reason,
                        "keyboard": [
                            [{"text": "📋 View Trades", "callback_data": "view_trades"}],
                            [{"text": "💰 View Wallet", "callback_data": "menu_wallet"}]
                        ]
                    },
                    broadcast_mode=True  # DUAL-CHANNEL: Send to both Telegram AND email
                )
                delivery_results["buyer"] = await self.send_notification(buyer_request)
                
                # Notify seller about decline (DUAL-CHANNEL: Telegram bot + email)
                if escrow.seller_id:
                    seller_request = NotificationRequest(
                        user_id=escrow.seller_id,
                        category=NotificationCategory.ESCROW_UPDATES,
                        priority=NotificationPriority.HIGH,
                        title="✅ Trade Declined",
                        message=f"You declined trade #{escrow_display_id}.\n\nAmount: ${float(escrow.total_amount):.2f}\n\nBuyer has been refunded.",
                        template_data={
                            "escrow_id": escrow.escrow_id,
                            "amount": float(escrow.total_amount),
                            "keyboard": [
                                [{"text": "📋 View Trades", "callback_data": "view_trades"}],
                                [{"text": "🏠 Main Menu", "callback_data": "main_menu"}]
                            ]
                        },
                        broadcast_mode=True  # DUAL-CHANNEL: Send to both Telegram AND email
                    )
                    delivery_results["seller"] = await self.send_notification(seller_request)
                
            elif cancellation_reason == "buyer_cancelled":
                # Notify buyer that they cancelled (EMAIL ONLY - they already saw success screen)
                buyer_request = NotificationRequest(
                    user_id=escrow.buyer_id,
                    category=NotificationCategory.ESCROW_UPDATES,
                    priority=NotificationPriority.HIGH,
                    title="❌ Trade Cancelled",
                    message=f"Trade #{escrow_display_id} has been cancelled.\n\nYour payment of ${float(escrow.total_amount):.2f} has been refunded to your wallet.",
                    template_data={
                        "escrow_id": escrow.escrow_id,
                        "amount": float(escrow.total_amount),
                        "cancellation_reason": cancellation_reason,
                        "keyboard": [
                            [{"text": "📋 View Trades", "callback_data": "view_trades"}],
                            [{"text": "💰 View Wallet", "callback_data": "menu_wallet"}]
                        ]
                    },
                    channels=[NotificationChannel.EMAIL],  # EMAIL ONLY: Actor already saw success screen
                    broadcast_mode=False
                )
                delivery_results["buyer"] = await self.send_notification(buyer_request)
                
                # Notify seller that buyer cancelled (DUAL-CHANNEL: Telegram + Email - counterparty needs to know)
                if escrow.seller_id:
                    seller_request = NotificationRequest(
                        user_id=escrow.seller_id,
                        category=NotificationCategory.ESCROW_UPDATES,
                        priority=NotificationPriority.HIGH,
                        title="❌ Buyer Cancelled Trade",
                        message=f"Trade #{escrow_display_id} was cancelled by the buyer.\n\nAmount: ${float(escrow.total_amount):.2f}",
                        template_data={
                            "escrow_id": escrow.escrow_id,
                            "amount": float(escrow.total_amount),
                            "cancellation_reason": cancellation_reason,
                            "keyboard": [
                                [{"text": "📋 View Trades", "callback_data": "view_trades"}]
                            ]
                        },
                        broadcast_mode=True  # DUAL-CHANNEL: Counterparty gets Telegram + Email
                    )
                    delivery_results["seller"] = await self.send_notification(seller_request)
            else:
                logger.warning(f"Unknown cancellation reason: {cancellation_reason}")
                return {"error": DeliveryResult(
                    channel=NotificationChannel.TELEGRAM,
                    status=DeliveryStatus.FAILED,
                    error=f"Unknown cancellation reason: {cancellation_reason}"
                )}
            
            # Send admin alert
            await self.send_admin_alert(
                title=f"Escrow Cancelled: {escrow.escrow_id}",
                message=f"Escrow {escrow.escrow_id} was cancelled.\nReason: {cancellation_reason}\nAmount: ${float(escrow.total_amount):.2f}",
                priority=NotificationPriority.NORMAL,
                additional_data={
                    "escrow_id": escrow.escrow_id,
                    "cancellation_reason": cancellation_reason,
                    "amount": float(escrow.total_amount)
                }
            )
            
            logger.info(f"✅ Escrow cancellation notifications sent for {escrow.escrow_id} - Reason: {cancellation_reason}")
            return delivery_results
            
        except Exception as e:
            logger.error(f"❌ Failed to send escrow cancellation notification: {e}")
            return {"error": DeliveryResult(
                channel=NotificationChannel.TELEGRAM,
                status=DeliveryStatus.FAILED,
                error=str(e)
            )}
    
    async def send_admin_alert(
        self, 
        title: str, 
        message: str, 
        user_id: Optional[int] = None,
        priority: NotificationPriority = NotificationPriority.HIGH,
        additional_data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, DeliveryResult]:
        """Send admin alert notification"""
        # Use system user ID if no user specified
        target_user_id = user_id or 1  # System user
        
        request = NotificationRequest(
            user_id=target_user_id,
            category=NotificationCategory.ADMIN_ALERTS,
            priority=priority,
            title=title,
            message=message,
            template_data=additional_data,
            channels=[NotificationChannel.ADMIN_ALERT],
            admin_notification=True
        )
        
        return await self.send_notification(request)
    
    # ================== LEGACY COMPATIBILITY METHODS ==================
    
    async def send_telegram_message(self, bot, user_id: int, message: str, **kwargs):
        """Legacy compatibility method - redirect to unified service"""
        try:
            request = NotificationRequest(
                user_id=user_id,
                category=NotificationCategory.MAINTENANCE,
                priority=NotificationPriority.NORMAL,
                title="Notification",
                message=message,
                channels=[NotificationChannel.TELEGRAM]
            )
            
            result = await self.send_notification(request)
            
            # Return success if Telegram delivery succeeded
            telegram_result = result.get('telegram')
            if telegram_result and telegram_result.status in [DeliveryStatus.SENT, DeliveryStatus.DELIVERED]:
                logger.debug(f"✅ Legacy telegram message sent to {user_id}")
                return
            else:
                error_msg = telegram_result.error if telegram_result else "Unknown error"
                logger.error(f"❌ Legacy telegram message failed to {user_id}: {error_msg}")
                raise Exception(error_msg)
                
        except Exception as e:
            logger.error(f"❌ Failed to send legacy Telegram message to {user_id}: {e}")
            raise e
    
    # ================== DELIVERY STATISTICS AND MONITORING ==================
    
    async def get_delivery_stats(self) -> Dict[str, Any]:
        """Get delivery statistics from persistent storage"""
        try:
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    # Count failed notifications in queue
                    failed_count_result = await session.execute(
                        select(NotificationQueue).where(NotificationQueue.status == 'failed')
                    )
                    failed_notifications_count = len(list(failed_count_result.scalars()))
                    
                    # Count pending retries (using existing fields only)
                    pending_retries_result = await session.execute(
                        select(NotificationQueue).where(
                            and_(
                                NotificationQueue.status == 'pending',
                                NotificationQueue.retry_count > 0
                            )
                        )
                    )
                    pending_retries_count = len(list(pending_retries_result.scalars()))
                    
                    return {
                        "delivery_stats": dict(self.delivery_stats),
                        "failed_notifications_count": failed_notifications_count,
                        "pending_retries_count": pending_retries_count,
                        "available_channels": [ch.value for ch in self._get_available_channels()],
                        "initialized": self.initialized,
                        "persistent_storage": True,
                        "in_memory_storage": False  # Migrated to persistent storage
                    }
        except Exception as e:
            logger.error(f"❌ Error getting delivery stats: {e}")
            return {
                "delivery_stats": dict(self.delivery_stats),
                "failed_notifications_count": 0,
                "pending_retries_count": 0,
                "available_channels": [ch.value for ch in self._get_available_channels()],
                "initialized": self.initialized,
                "error": str(e)
            }
    
    async def get_failed_notifications(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent failed notifications from persistent storage"""
        try:
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    result = await session.execute(
                        select(NotificationQueue).where(
                            NotificationQueue.status == 'failed'
                        ).order_by(NotificationQueue.sent_at.desc()).limit(limit)
                    )
                    
                    failed_notifications = list(result.scalars())
                    
                    return [
                        {
                            "notification_id": notif.id,
                            "user_id": notif.user_id,
                            "category": "payments",  # Default since field doesn't exist
                            "priority": notif.priority,
                            "channel": notif.channel,
                            "retry_count": notif.retry_count,
                            "max_retries": 3,  # Default value
                            "error_message": notif.error_message,
                            "failed_at": None,  # Field doesn't exist
                            "next_retry_at": (lambda x: x.isoformat() if x else None)(getattr(notif, 'scheduled_at', None)),
                            "created_at": (lambda x: x.isoformat() if x else None)(getattr(notif, 'scheduled_at', None)),
                            "updated_at": (lambda x: x.isoformat() if x else None)(getattr(notif, 'sent_at', None))
                        }
                        for notif in failed_notifications
                    ]
        except Exception as e:
            logger.error(f"❌ Error getting failed notifications: {e}")
            return []
    
    async def process_retry_queue(self) -> Dict[str, int]:
        """Process pending notification retries from persistent storage"""
        processed = {"success": 0, "failed": 0, "rescheduled": 0}
        
        try:
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    # Get notifications ready for retry
                    current_time = datetime.utcnow()
                    
                    # Query notifications that are ready for retry with row-level locking
                    # Use SELECT FOR UPDATE SKIP LOCKED to prevent duplicate processing
                    result = await session.execute(
                        select(NotificationQueue).where(
                            and_(
                                NotificationQueue.status == 'pending',
                                NotificationQueue.scheduled_at <= current_time,
                                NotificationQueue.retry_count < 3  # Fixed max retries
                            )
                        ).with_for_update(skip_locked=True).limit(20)  # Process in batches safely
                    )
                    
                    retry_notifications = list(result.scalars())
                    
                    for notification in retry_notifications:
                        try:
                            # Reconstruct notification request
                            request = self._reconstruct_notification_request(notification)
                            
                            # Update status to sending using proper SQL UPDATE
                            await session.execute(
                                update(NotificationQueue)
                                .where(NotificationQueue.id == notification.id)
                                .values(
                                    status='sending',
                                    retry_count=NotificationQueue.retry_count + 1
                                )
                            )
                            
                            result = await self.send_notification(request)
                            successful_channels = [
                                k for k, v in result.items() 
                                if v.status in [DeliveryStatus.SENT, DeliveryStatus.DELIVERED]
                            ]
                            
                            if successful_channels:
                                # Mark as sent using SQL UPDATE
                                await session.execute(
                                    update(NotificationQueue)
                                    .where(NotificationQueue.id == notification.id)
                                    .values(
                                        status='sent',
                                        sent_at=current_time
                                    )
                                )
                                processed["success"] += 1
                                logger.info(f"✅ Retry successful for {notification.id} via {successful_channels}")
                            else:
                                # Mark as failed using SQL UPDATE
                                await session.execute(
                                    update(NotificationQueue)
                                    .where(NotificationQueue.id == notification.id)
                                    .values(status='failed')
                                )
                                processed["failed"] += 1
                                logger.error(f"❌ Retry failed for {notification.id}")
                        
                        except Exception as e:
                            logger.error(f"❌ Error during retry processing for {notification.id}: {e}")
                            # Mark as failed using SQL UPDATE
                            await session.execute(
                                update(NotificationQueue)
                                .where(NotificationQueue.id == notification.id)
                                .values(
                                    status='failed',
                                    error_message=str(e)
                                )
                            )
                            processed["failed"] += 1
                    
                    # Commit happens automatically when session.begin() context exits
                
        except Exception as e:
            logger.error(f"❌ Error in retry queue processing: {e}")
            processed["failed"] += 1
        
        return processed


# Global instance
consolidated_notification_service = ConsolidatedNotificationService()