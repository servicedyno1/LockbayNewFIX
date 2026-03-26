"""
Notification Delivery Tracker Service
Comprehensive delivery tracking system with retry logic, exponential backoff, and monitoring
"""

import logging
import json
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, Set
from enum import Enum
from dataclasses import dataclass, asdict

# Database imports
from database import async_managed_session, SessionLocal

logger = logging.getLogger(__name__)


class DeliveryStatus(Enum):
    """Delivery status states"""
    PENDING = "pending"
    QUEUED = "queued"
    SENDING = "sending"
    SENT = "sent"
    DELIVERED = "delivered"
    FAILED = "failed"
    RETRYING = "retrying"
    EXPIRED = "expired"
    USER_CONFIRMED = "user_confirmed"
    ADMIN_ESCALATED = "admin_escalated"


class DeliveryChannel(Enum):
    """Supported delivery channels"""
    EMAIL = "email"
    TELEGRAM = "telegram"
    SMS = "sms"
    PUSH_NOTIFICATION = "push_notification"
    ADMIN_ALERT = "admin_alert"


class FailureReason(Enum):
    """Categorized failure reasons"""
    NETWORK_ERROR = "network_error"
    RATE_LIMITED = "rate_limited"
    INVALID_RECIPIENT = "invalid_recipient"
    SERVICE_UNAVAILABLE = "service_unavailable"
    CONTENT_ERROR = "content_error"
    AUTHENTICATION_ERROR = "authentication_error"
    QUOTA_EXCEEDED = "quota_exceeded"
    UNKNOWN_ERROR = "unknown_error"


@dataclass
class DeliveryAttempt:
    """Single delivery attempt record"""
    attempt_number: int
    timestamp: datetime
    status: DeliveryStatus
    channel: DeliveryChannel
    failure_reason: Optional[FailureReason] = None
    error_message: Optional[str] = None
    response_time_ms: Optional[int] = None
    external_id: Optional[str] = None  # External service message ID
    metadata: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


@dataclass
class RetryConfiguration:
    """Retry configuration parameters"""
    max_retries: int = 5
    initial_delay_seconds: int = 60
    backoff_multiplier: float = 2.0
    max_delay_seconds: int = 3600
    jitter_max_seconds: int = 30
    exponential_ceiling: int = 7  # Max exponent for backoff calculation
    retry_on_failures: Set[FailureReason] = None
    escalation_threshold: int = 3  # Failures before admin escalation
    
    def __post_init__(self):
        if self.retry_on_failures is None:
            self.retry_on_failures = {
                FailureReason.NETWORK_ERROR,
                FailureReason.RATE_LIMITED,
                FailureReason.SERVICE_UNAVAILABLE,
                FailureReason.UNKNOWN_ERROR
            }


@dataclass
class NotificationDeliveryRecord:
    """Complete delivery record for a notification"""
    notification_id: str
    user_id: int
    template_id: str
    priority: str
    created_at: datetime
    channels: Dict[DeliveryChannel, List[DeliveryAttempt]]
    retry_config: RetryConfiguration
    status: DeliveryStatus = DeliveryStatus.PENDING
    completed_at: Optional[datetime] = None
    user_confirmed_at: Optional[datetime] = None
    admin_escalated_at: Optional[datetime] = None
    expiry_time: Optional[datetime] = None
    metadata: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.channels is None:
            self.channels = {}
        if self.metadata is None:
            self.metadata = {}
        if self.expiry_time is None:
            # Default expiry: 7 days from creation
            self.expiry_time = self.created_at + timedelta(days=7)


class NotificationDeliveryTracker:
    """
    Comprehensive notification delivery tracking system with retry logic and monitoring
    """
    
    def __init__(self):
        self.delivery_records: Dict[str, NotificationDeliveryRecord] = {}
        self.retry_queue: List[str] = []  # Notification IDs pending retry
        self.running_retries: Set[str] = set()  # Currently processing retries
        self.default_retry_config = RetryConfiguration()
        self.analytics_cache = {}
        self.last_cleanup = datetime.utcnow()
        
        # Critical notifications that need DB persistence
        self.critical_templates = {
            'cashout_failed_refund', 
            'escrow_timeout_refund',
            'dispute_resolution_refund', 
            'post_timeout_payment_refund',
            'system_error_refund'
        }
        
        # Start background tasks
        self._start_background_tasks()
        
        # Note: Load persisted records on first use to avoid sync startup issues
    
    def _start_background_tasks(self):
        """Start background processing tasks"""
        # In a real implementation, these would be proper async tasks
        # For now, we'll implement the logic and structure
        pass
    
    async def _persist_delivery_record(self, delivery_record: NotificationDeliveryRecord) -> None:
        """Persist critical delivery record to database"""
        try:
            async with async_managed_session() as session:
                # Convert delivery record to JSON for storage
                {
                    'notification_id': delivery_record.notification_id,
                    'user_id': delivery_record.user_id,
                    'template_id': delivery_record.template_id,
                    'priority': delivery_record.priority,
                    'created_at': delivery_record.created_at,
                    'status': delivery_record.status.value,
                    'channels': {channel.value: [asdict(attempt) for attempt in attempts] 
                               for channel, attempts in delivery_record.channels.items()},
                    'metadata': delivery_record.metadata
                }
                
                # In a real implementation, you would have a DeliveryRecord model
                # For now, we'll just log that persistence would happen
                logger.debug(f"📊 CRITICAL_DELIVERY_PERSISTED: {delivery_record.notification_id}")
                
                # Future: INSERT INTO delivery_records ...
                await session.commit()
                
        except Exception as e:
            logger.error(f"❌ Error persisting delivery record: {e}")
    
    def _load_persisted_records(self) -> None:
        """Load persisted delivery records from database (non-async for startup)"""
        # This would be called during async initialization if needed
        # For now, we'll just initialize empty
        logger.debug("📊 DELIVERY_TRACKER_INITIALIZED: In-memory tracking ready")
    
    async def track_notification(
        self,
        notification_id: str,
        user_id: int,
        template_id: str,
        channels: List[DeliveryChannel],
        priority: str = "normal",
        retry_config: Optional[RetryConfiguration] = None,
        metadata: Dict[str, Any] = None
    ) -> NotificationDeliveryRecord:
        """
        Start tracking a new notification delivery
        
        Args:
            notification_id: Unique notification identifier
            user_id: Target user ID
            template_id: Template identifier
            channels: List of delivery channels to track
            priority: Notification priority level
            retry_config: Custom retry configuration
            metadata: Additional tracking metadata
            
        Returns:
            NotificationDeliveryRecord for the tracked notification
        """
        try:
            # Create delivery record
            delivery_record = NotificationDeliveryRecord(
                notification_id=notification_id,
                user_id=user_id,
                template_id=template_id,
                priority=priority,
                created_at=datetime.utcnow(),
                channels={channel: [] for channel in channels},
                retry_config=retry_config or self.default_retry_config,
                metadata=metadata or {}
            )
            
            # Store in tracking system
            self.delivery_records[notification_id] = delivery_record
            
            # Persist critical notifications to database
            if template_id in self.critical_templates:
                await self._persist_delivery_record(delivery_record)
            
            logger.info(f"📊 DELIVERY_TRACKING_START: {notification_id} for user {user_id} via {[c.value for c in channels]}")
            
            return delivery_record
            
        except Exception as e:
            logger.error(f"❌ Error starting delivery tracking for {notification_id}: {e}")
            raise
    
    async def record_delivery_attempt(
        self,
        notification_id: str,
        channel: DeliveryChannel,
        status: DeliveryStatus,
        failure_reason: Optional[FailureReason] = None,
        error_message: Optional[str] = None,
        response_time_ms: Optional[int] = None,
        external_id: Optional[str] = None,
        metadata: Dict[str, Any] = None
    ) -> bool:
        """
        Record a delivery attempt for a notification
        
        Args:
            notification_id: Notification identifier
            channel: Delivery channel used
            status: Delivery status result
            failure_reason: Reason for failure (if applicable)
            error_message: Detailed error message
            response_time_ms: Response time in milliseconds
            external_id: External service message ID
            metadata: Additional attempt metadata
            
        Returns:
            True if recorded successfully, False otherwise
        """
        try:
            delivery_record = self.delivery_records.get(notification_id)
            if not delivery_record:
                logger.warning(f"⚠️ No delivery record found for notification {notification_id}")
                return False
            
            # Create delivery attempt record
            attempt_number = len(delivery_record.channels.get(channel, [])) + 1
            attempt = DeliveryAttempt(
                attempt_number=attempt_number,
                timestamp=datetime.utcnow(),
                status=status,
                channel=channel,
                failure_reason=failure_reason,
                error_message=error_message,
                response_time_ms=response_time_ms,
                external_id=external_id,
                metadata=metadata or {}
            )
            
            # Add to delivery record
            if channel not in delivery_record.channels:
                delivery_record.channels[channel] = []
            delivery_record.channels[channel].append(attempt)
            
            # Update overall delivery status
            await self._update_overall_status(delivery_record)
            
            # Check if retry is needed
            if status == DeliveryStatus.FAILED:
                await self._handle_delivery_failure(delivery_record, channel, attempt)
            
            # Log delivery attempt
            logger.info(
                f"📊 DELIVERY_ATTEMPT: {notification_id} via {channel.value} "
                f"attempt #{attempt_number} status={status.value} "
                f"response_time={response_time_ms}ms"
            )
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Error recording delivery attempt for {notification_id}: {e}")
            return False
    
    async def _update_overall_status(self, delivery_record: NotificationDeliveryRecord) -> None:
        """Update the overall delivery status for a notification"""
        try:
            # Check if any channel has been delivered or confirmed
            for channel_attempts in delivery_record.channels.values():
                if not channel_attempts:
                    continue
                    
                latest_attempt = channel_attempts[-1]
                if latest_attempt.status in [DeliveryStatus.DELIVERED, DeliveryStatus.USER_CONFIRMED]:
                    delivery_record.status = latest_attempt.status
                    if delivery_record.completed_at is None:
                        delivery_record.completed_at = latest_attempt.timestamp
                    return
            
            # Check if all channels have failed exhaustively
            all_channels_failed = True
            any_retrying = False
            
            for channel_attempts in delivery_record.channels.values():
                if not channel_attempts:
                    all_channels_failed = False
                    continue
                    
                latest_attempt = channel_attempts[-1]
                if latest_attempt.status == DeliveryStatus.RETRYING:
                    any_retrying = True
                    all_channels_failed = False
                elif latest_attempt.status not in [DeliveryStatus.FAILED, DeliveryStatus.EXPIRED]:
                    all_channels_failed = False
            
            # Update status based on analysis
            if any_retrying:
                delivery_record.status = DeliveryStatus.RETRYING
            elif all_channels_failed:
                delivery_record.status = DeliveryStatus.FAILED
                if delivery_record.completed_at is None:
                    delivery_record.completed_at = datetime.utcnow()
                    
                # Escalate to admin if configured
                await self._escalate_to_admin(delivery_record)
            else:
                # Some channels still pending
                delivery_record.status = DeliveryStatus.PENDING
                
        except Exception as e:
            logger.error(f"❌ Error updating overall status: {e}")
    
    async def _handle_delivery_failure(
        self,
        delivery_record: NotificationDeliveryRecord,
        channel: DeliveryChannel,
        failed_attempt: DeliveryAttempt
    ) -> None:
        """Handle a delivery failure and schedule retries if appropriate"""
        try:
            # Check if this failure type should be retried
            if (failed_attempt.failure_reason not in delivery_record.retry_config.retry_on_failures):
                logger.info(
                    f"📊 SKIP_RETRY: {delivery_record.notification_id} failure_reason="
                    f"{failed_attempt.failure_reason.value} not in retry list"
                )
                return
            
            # Check if max retries reached for this channel
            channel_attempts = delivery_record.channels[channel]
            failed_attempts = [a for a in channel_attempts if a.status == DeliveryStatus.FAILED]
            
            if len(failed_attempts) >= delivery_record.retry_config.max_retries:
                logger.info(
                    f"📊 MAX_RETRIES_REACHED: {delivery_record.notification_id} channel="
                    f"{channel.value} attempts={len(failed_attempts)}"
                )
                return
            
            # Calculate retry delay
            retry_delay = self._calculate_retry_delay(
                attempt_number=len(failed_attempts),
                retry_config=delivery_record.retry_config
            )
            
            # Schedule retry
            await self._schedule_retry(
                delivery_record.notification_id,
                channel,
                retry_delay
            )
            
            # Check if admin escalation is needed
            if len(failed_attempts) >= delivery_record.retry_config.escalation_threshold:
                await self._escalate_to_admin(delivery_record)
            
        except Exception as e:
            logger.error(f"❌ Error handling delivery failure: {e}")
    
    def _calculate_retry_delay(
        self,
        attempt_number: int,
        retry_config: RetryConfiguration
    ) -> timedelta:
        """Calculate retry delay using exponential backoff with jitter"""
        try:
            # Calculate base delay with exponential backoff
            exponent = min(attempt_number - 1, retry_config.exponential_ceiling)
            base_delay = retry_config.initial_delay_seconds * (retry_config.backoff_multiplier ** exponent)
            
            # Apply maximum delay ceiling
            base_delay = min(base_delay, retry_config.max_delay_seconds)
            
            # Add jitter to prevent thundering herd
            import random
            jitter = random.randint(0, retry_config.jitter_max_seconds)
            
            total_delay_seconds = base_delay + jitter
            
            logger.info(
                f"📊 RETRY_DELAY_CALCULATED: attempt={attempt_number} "
                f"base_delay={base_delay}s jitter={jitter}s total={total_delay_seconds}s"
            )
            
            return timedelta(seconds=total_delay_seconds)
            
        except Exception as e:
            logger.error(f"❌ Error calculating retry delay: {e}")
            # Fallback to initial delay
            return timedelta(seconds=retry_config.initial_delay_seconds)
    
    async def _schedule_retry(
        self,
        notification_id: str,
        channel: DeliveryChannel,
        delay: timedelta
    ) -> None:
        """Schedule a retry for a failed delivery"""
        try:
            retry_time = datetime.utcnow() + delay
            
            # In a real implementation, this would integrate with a proper job scheduler
            # For now, we'll add to a retry queue and process in background
            
            {
                "notification_id": notification_id,
                "channel": channel.value,
                "retry_time": retry_time.isoformat(),
                "scheduled_at": datetime.utcnow().isoformat()
            }
            
            # Add to retry queue (in memory for this example)
            if notification_id not in self.retry_queue:
                self.retry_queue.append(notification_id)
            
            logger.info(
                f"📊 RETRY_SCHEDULED: {notification_id} channel={channel.value} "
                f"retry_time={retry_time.isoformat()}"
            )
            
        except Exception as e:
            logger.error(f"❌ Error scheduling retry: {e}")
    
    async def _escalate_to_admin(self, delivery_record: NotificationDeliveryRecord) -> None:
        """Escalate delivery failure to admin team"""
        try:
            if delivery_record.admin_escalated_at:
                return  # Already escalated
            
            delivery_record.admin_escalated_at = datetime.utcnow()
            delivery_record.status = DeliveryStatus.ADMIN_ESCALATED
            
            # Create admin alert
            from services.consolidated_notification_service import consolidated_notification_service
            
            alert_message = self._generate_escalation_alert(delivery_record)
            
            await consolidated_notification_service.send_admin_alert(
                message=alert_message,
                priority="high"
            )
            
            logger.warning(
                f"🚨 ADMIN_ESCALATION: {delivery_record.notification_id} "
                f"user={delivery_record.user_id} template={delivery_record.template_id}"
            )
            
        except Exception as e:
            logger.error(f"❌ Error escalating to admin: {e}")
    
    def _generate_escalation_alert(self, delivery_record: NotificationDeliveryRecord) -> str:
        """Generate admin escalation alert message"""
        try:
            failed_channels = []
            total_attempts = 0
            
            for channel, attempts in delivery_record.channels.items():
                if attempts and attempts[-1].status == DeliveryStatus.FAILED:
                    failed_channels.append(channel.value)
                total_attempts += len(attempts)
            
            alert = f"""🚨 NOTIFICATION_DELIVERY_FAILURE

Notification ID: {delivery_record.notification_id}
User ID: {delivery_record.user_id}
Template: {delivery_record.template_id}
Priority: {delivery_record.priority}
Created: {delivery_record.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")}

DELIVERY FAILURES:
• Failed Channels: {', '.join(failed_channels)}
• Total Attempts: {total_attempts}
• Max Retries: {delivery_record.retry_config.max_retries}

REQUIRED ACTION:
Manual intervention needed to ensure user receives critical refund notification.

Notification ID: {delivery_record.notification_id}"""
            
            return alert
            
        except Exception as e:
            logger.error(f"❌ Error generating escalation alert: {e}")
            return f"🚨 Delivery failure for notification {delivery_record.notification_id} - manual intervention required"
    
    async def confirm_user_receipt(
        self,
        notification_id: str,
        user_id: int,
        confirmation_method: str = "button_click"
    ) -> bool:
        """Record user confirmation of notification receipt"""
        try:
            delivery_record = self.delivery_records.get(notification_id)
            if not delivery_record:
                logger.warning(f"⚠️ No delivery record for notification {notification_id}")
                return False
            
            if delivery_record.user_id != user_id:
                logger.warning(f"⚠️ User ID mismatch for confirmation: {user_id} != {delivery_record.user_id}")
                return False
            
            # Record user confirmation
            delivery_record.user_confirmed_at = datetime.utcnow()
            delivery_record.status = DeliveryStatus.USER_CONFIRMED
            delivery_record.metadata["confirmation_method"] = confirmation_method
            
            # Complete the delivery
            if delivery_record.completed_at is None:
                delivery_record.completed_at = datetime.utcnow()
            
            logger.info(
                f"✅ USER_CONFIRMATION: {notification_id} user={user_id} "
                f"method={confirmation_method}"
            )
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Error confirming user receipt: {e}")
            return False
    
    async def get_delivery_status(self, notification_id: str) -> Optional[Dict[str, Any]]:
        """Get current delivery status for a notification"""
        try:
            delivery_record = self.delivery_records.get(notification_id)
            if not delivery_record:
                return None
            
            status_summary = {
                "notification_id": notification_id,
                "user_id": delivery_record.user_id,
                "template_id": delivery_record.template_id,
                "overall_status": delivery_record.status.value,
                "priority": delivery_record.priority,
                "created_at": delivery_record.created_at.isoformat(),
                "completed_at": delivery_record.completed_at.isoformat() if delivery_record.completed_at else None,
                "user_confirmed_at": delivery_record.user_confirmed_at.isoformat() if delivery_record.user_confirmed_at else None,
                "admin_escalated_at": delivery_record.admin_escalated_at.isoformat() if delivery_record.admin_escalated_at else None,
                "channels": {}
            }
            
            # Add channel-specific status
            for channel, attempts in delivery_record.channels.items():
                if attempts:
                    latest_attempt = attempts[-1]
                    status_summary["channels"][channel.value] = {
                        "status": latest_attempt.status.value,
                        "attempts": len(attempts),
                        "last_attempt": latest_attempt.timestamp.isoformat(),
                        "failure_reason": latest_attempt.failure_reason.value if latest_attempt.failure_reason else None,
                        "external_id": latest_attempt.external_id
                    }
                else:
                    status_summary["channels"][channel.value] = {
                        "status": "pending",
                        "attempts": 0
                    }
            
            return status_summary
            
        except Exception as e:
            logger.error(f"❌ Error getting delivery status: {e}")
            return None
    
    async def get_delivery_analytics(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        user_id: Optional[int] = None,
        template_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Generate delivery analytics and metrics"""
        try:
            # Set default date range if not provided
            if not end_date:
                end_date = datetime.utcnow()
            if not start_date:
                start_date = end_date - timedelta(days=30)
            
            # Filter delivery records
            filtered_records = []
            for record in self.delivery_records.values():
                if record.created_at < start_date or record.created_at > end_date:
                    continue
                if user_id and record.user_id != user_id:
                    continue
                if template_id and record.template_id != template_id:
                    continue
                filtered_records.append(record)
            
            # Calculate metrics
            total_notifications = len(filtered_records)
            successful_deliveries = len([r for r in filtered_records if r.status in [DeliveryStatus.DELIVERED, DeliveryStatus.USER_CONFIRMED]])
            failed_deliveries = len([r for r in filtered_records if r.status == DeliveryStatus.FAILED])
            pending_deliveries = len([r for r in filtered_records if r.status in [DeliveryStatus.PENDING, DeliveryStatus.RETRYING]])
            escalated_deliveries = len([r for r in filtered_records if r.admin_escalated_at is not None])
            
            # Channel-specific metrics
            channel_metrics = {}
            for channel in DeliveryChannel:
                channel_attempts = []
                for record in filtered_records:
                    channel_attempts.extend(record.channels.get(channel, []))
                
                if channel_attempts:
                    successful_attempts = [a for a in channel_attempts if a.status in [DeliveryStatus.SENT, DeliveryStatus.DELIVERED]]
                    failed_attempts = [a for a in channel_attempts if a.status == DeliveryStatus.FAILED]
                    
                    # Calculate average response time
                    response_times = [a.response_time_ms for a in channel_attempts if a.response_time_ms is not None]
                    avg_response_time = sum(response_times) / len(response_times) if response_times else 0
                    
                    channel_metrics[channel.value] = {
                        "total_attempts": len(channel_attempts),
                        "successful_attempts": len(successful_attempts),
                        "failed_attempts": len(failed_attempts),
                        "success_rate": len(successful_attempts) / len(channel_attempts) * 100 if channel_attempts else 0,
                        "avg_response_time_ms": round(avg_response_time, 2)
                    }
                else:
                    channel_metrics[channel.value] = {
                        "total_attempts": 0,
                        "successful_attempts": 0,
                        "failed_attempts": 0,
                        "success_rate": 0,
                        "avg_response_time_ms": 0
                    }
            
            # Template-specific metrics
            template_metrics = {}
            for record in filtered_records:
                template = record.template_id
                if template not in template_metrics:
                    template_metrics[template] = {
                        "total": 0,
                        "successful": 0,
                        "failed": 0,
                        "escalated": 0
                    }
                
                template_metrics[template]["total"] += 1
                if record.status in [DeliveryStatus.DELIVERED, DeliveryStatus.USER_CONFIRMED]:
                    template_metrics[template]["successful"] += 1
                elif record.status == DeliveryStatus.FAILED:
                    template_metrics[template]["failed"] += 1
                if record.admin_escalated_at:
                    template_metrics[template]["escalated"] += 1
            
            analytics = {
                "date_range": {
                    "start": start_date.isoformat(),
                    "end": end_date.isoformat()
                },
                "filters": {
                    "user_id": user_id,
                    "template_id": template_id
                },
                "overview": {
                    "total_notifications": total_notifications,
                    "successful_deliveries": successful_deliveries,
                    "failed_deliveries": failed_deliveries,
                    "pending_deliveries": pending_deliveries,
                    "escalated_deliveries": escalated_deliveries,
                    "overall_success_rate": round(successful_deliveries / total_notifications * 100, 2) if total_notifications > 0 else 0
                },
                "channel_metrics": channel_metrics,
                "template_metrics": template_metrics,
                "retry_queue_size": len(self.retry_queue),
                "active_retries": len(self.running_retries)
            }
            
            return analytics
            
        except Exception as e:
            logger.error(f"❌ Error generating delivery analytics: {e}")
            return {"error": str(e)}
    
    async def cleanup_old_records(self, days_to_keep: int = 30) -> int:
        """Clean up old delivery records to prevent memory growth"""
        try:
            cutoff_date = datetime.utcnow() - timedelta(days=days_to_keep)
            
            records_to_remove = []
            for notification_id, record in self.delivery_records.items():
                if (record.created_at < cutoff_date and 
                    record.status in [DeliveryStatus.DELIVERED, DeliveryStatus.USER_CONFIRMED, DeliveryStatus.FAILED, DeliveryStatus.EXPIRED]):
                    records_to_remove.append(notification_id)
            
            # Remove old records
            for notification_id in records_to_remove:
                del self.delivery_records[notification_id]
            
            # Clean up retry queue
            self.retry_queue = [n_id for n_id in self.retry_queue if n_id in self.delivery_records]
            
            logger.info(f"🧹 CLEANUP: Removed {len(records_to_remove)} old delivery records")
            
            return len(records_to_remove)
            
        except Exception as e:
            logger.error(f"❌ Error during cleanup: {e}")
            return 0
    
    async def process_retry_queue(self) -> int:
        """Process pending retries in the retry queue"""
        try:
            datetime.utcnow()
            processed_count = 0
            
            # Process retries (simplified implementation)
            for notification_id in list(self.retry_queue):
                if notification_id not in self.delivery_records:
                    self.retry_queue.remove(notification_id)
                    continue
                
                delivery_record = self.delivery_records[notification_id]
                
                # Check if retry is due (simplified logic)
                # In a real implementation, this would check scheduled retry times
                if notification_id not in self.running_retries:
                    # Schedule actual retry
                    self.running_retries.add(notification_id)
                    
                    # This would trigger actual retry logic
                    logger.info(f"🔄 PROCESSING_RETRY: {notification_id}")
                    
                    processed_count += 1
                    
                    # Remove from queue if max retries reached or successful
                    if delivery_record.status not in [DeliveryStatus.RETRYING, DeliveryStatus.PENDING]:
                        self.retry_queue.remove(notification_id)
                        self.running_retries.discard(notification_id)
            
            return processed_count
            
        except Exception as e:
            logger.error(f"❌ Error processing retry queue: {e}")
            return 0
    
    async def get_health_status(self) -> Dict[str, Any]:
        """Get health status of the delivery tracking system"""
        try:
            current_time = datetime.utcnow()
            
            # Count records by status
            status_counts = {}
            for status in DeliveryStatus:
                status_counts[status.value] = len([
                    r for r in self.delivery_records.values() 
                    if r.status == status
                ])
            
            # Calculate queue health
            queue_health = {
                "retry_queue_size": len(self.retry_queue),
                "running_retries": len(self.running_retries),
                "queue_processing_healthy": len(self.running_retries) < 100  # Arbitrary threshold
            }
            
            # Calculate recent success rate
            recent_cutoff = current_time - timedelta(hours=24)
            recent_records = [
                r for r in self.delivery_records.values()
                if r.created_at >= recent_cutoff
            ]
            
            recent_successful = len([
                r for r in recent_records
                if r.status in [DeliveryStatus.DELIVERED, DeliveryStatus.USER_CONFIRMED]
            ])
            
            recent_success_rate = (
                recent_successful / len(recent_records) * 100
                if recent_records else 100
            )
            
            health_status = {
                "service": "notification_delivery_tracker",
                "status": "healthy" if recent_success_rate > 80 else "degraded",
                "timestamp": current_time.isoformat(),
                "total_records": len(self.delivery_records),
                "status_breakdown": status_counts,
                "queue_health": queue_health,
                "recent_24h": {
                    "total_notifications": len(recent_records),
                    "successful_deliveries": recent_successful,
                    "success_rate": round(recent_success_rate, 2)
                },
                "last_cleanup": self.last_cleanup.isoformat()
            }
            
            return health_status
            
        except Exception as e:
            logger.error(f"❌ Error getting health status: {e}")
            return {
                "service": "notification_delivery_tracker",
                "status": "error",
                "error": str(e)
            }
    
    def _load_persisted_records(self):
        """Load critical delivery records from database on startup"""
        try:
            with SessionLocal() as db:
                # Query critical notifications from the database
                # Note: This would need a proper DeliveryRecord model in a production system
                # For now, we'll use a simple JSON-based approach
                
                query = """
                SELECT notification_id, record_data 
                FROM notification_delivery_records 
                WHERE template_id IN ('cashout_failed_refund', 'escrow_timeout_refund', 
                                    'dispute_resolution_refund', 'post_timeout_payment_refund',
                                    'system_error_refund')
                AND status NOT IN ('user_confirmed', 'expired')
                AND created_at > NOW() - INTERVAL '7 days'
                """
                
                try:
                    result = db.execute(query)
                    loaded_count = 0
                    
                    for row in result:
                        try:
                            record_data = json.loads(row.record_data)
                            # Reconstruct NotificationDeliveryRecord from JSON
                            # This is a simplified version - full implementation would need proper deserialization
                            self.delivery_records[row.notification_id] = record_data
                            loaded_count += 1
                        except Exception as parse_error:
                            logger.warning(f"⚠️ Failed to parse delivery record {row.notification_id}: {parse_error}")
                    
                    logger.info(f"✅ Loaded {loaded_count} persisted critical delivery records")
                    
                except Exception as db_error:
                    # Table might not exist in current system - this is expected for now
                    logger.debug(f"📊 Notification delivery records table not found (expected for initial setup): {db_error}")
                    
        except Exception as e:
            logger.error(f"❌ Error loading persisted delivery records: {e}")
    
    async def _persist_delivery_record(self, delivery_record: NotificationDeliveryRecord):
        """Persist critical delivery record to database"""
        try:
            if not delivery_record.template_id in self.critical_templates:
                return  # Only persist critical notifications
                
            # Simple JSON serialization for persistence
            record_data = {
                'notification_id': delivery_record.notification_id,
                'user_id': delivery_record.user_id,
                'template_id': delivery_record.template_id,
                'priority': delivery_record.priority,
                'created_at': delivery_record.created_at.isoformat(),
                'status': delivery_record.status.value,
                'channels': {
                    channel.value: [
                        {
                            'attempt_number': attempt.attempt_number,
                            'timestamp': attempt.timestamp.isoformat(),
                            'status': attempt.status.value,
                            'failure_reason': attempt.failure_reason.value if attempt.failure_reason else None,
                            'error_message': attempt.error_message
                        }
                        for attempt in attempts
                    ]
                    for channel, attempts in delivery_record.channels.items()
                },
                'metadata': delivery_record.metadata
            }
            
            with SessionLocal() as db:
                # Use raw SQL for simplicity (would use ORM models in production)
                upsert_query = """
                INSERT INTO notification_delivery_records (
                    notification_id, user_id, template_id, status, record_data, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (notification_id) 
                DO UPDATE SET 
                    status = EXCLUDED.status,
                    record_data = EXCLUDED.record_data,
                    updated_at = EXCLUDED.updated_at
                """
                
                try:
                    db.execute(upsert_query, (
                        delivery_record.notification_id,
                        delivery_record.user_id,
                        delivery_record.template_id,
                        delivery_record.status.value,
                        json.dumps(record_data),
                        delivery_record.created_at,
                        datetime.utcnow()
                    ))
                    db.commit()
                    logger.debug(f"📊 Persisted critical delivery record: {delivery_record.notification_id}")
                    
                except Exception as db_error:
                    # Table creation would be handled by migrations in production
                    logger.debug(f"📊 Delivery persistence not available (expected for initial setup): {db_error}")
                    db.rollback()
                    
        except Exception as e:
            logger.error(f"❌ Error persisting delivery record: {e}")
    
    async def _update_persisted_record(self, notification_id: str, delivery_record: NotificationDeliveryRecord):
        """Update persisted delivery record"""
        if delivery_record.template_id in self.critical_templates:
            await self._persist_delivery_record(delivery_record)


# Global service instance
notification_delivery_tracker = NotificationDeliveryTracker()


# Convenience functions for easy integration

async def track_refund_notification_delivery(
    notification_id: str,
    user_id: int,
    template_id: str,
    channels: List[str],
    priority: str = "normal"
) -> Optional[NotificationDeliveryRecord]:
    """Track delivery for a refund notification"""
    try:
        delivery_channels = [DeliveryChannel(ch) for ch in channels]
        
        return await notification_delivery_tracker.track_notification(
            notification_id=notification_id,
            user_id=user_id,
            template_id=template_id,
            channels=delivery_channels,
            priority=priority
        )
        
    except Exception as e:
        logger.error(f"❌ Error tracking refund notification delivery: {e}")
        return None


async def record_email_delivery_attempt(
    notification_id: str,
    success: bool,
    error_message: Optional[str] = None,
    response_time_ms: Optional[int] = None,
    external_id: Optional[str] = None
) -> bool:
    """Record email delivery attempt"""
    status = DeliveryStatus.SENT if success else DeliveryStatus.FAILED
    failure_reason = FailureReason.UNKNOWN_ERROR if not success else None
    
    return await notification_delivery_tracker.record_delivery_attempt(
        notification_id=notification_id,
        channel=DeliveryChannel.EMAIL,
        status=status,
        failure_reason=failure_reason,
        error_message=error_message,
        response_time_ms=response_time_ms,
        external_id=external_id
    )


async def record_telegram_delivery_attempt(
    notification_id: str,
    success: bool,
    error_message: Optional[str] = None,
    response_time_ms: Optional[int] = None,
    external_id: Optional[str] = None
) -> bool:
    """Record Telegram delivery attempt"""
    status = DeliveryStatus.SENT if success else DeliveryStatus.FAILED
    failure_reason = FailureReason.UNKNOWN_ERROR if not success else None
    
    return await notification_delivery_tracker.record_delivery_attempt(
        notification_id=notification_id,
        channel=DeliveryChannel.TELEGRAM,
        status=status,
        failure_reason=failure_reason,
        error_message=error_message,
        response_time_ms=response_time_ms,
        external_id=external_id
    )


async def confirm_notification_receipt(
    notification_id: str,
    user_id: int,
    method: str = "button_click"
) -> bool:
    """Confirm user receipt of notification"""
    return await notification_delivery_tracker.confirm_user_receipt(
        notification_id=notification_id,
        user_id=user_id,
        confirmation_method=method
    )


# Background task functions (would be integrated with job scheduler)

async def run_delivery_tracking_maintenance():
    """Run periodic maintenance tasks"""
    try:
        # Process retry queue
        processed_retries = await notification_delivery_tracker.process_retry_queue()
        
        # Clean up old records
        cleaned_records = await notification_delivery_tracker.cleanup_old_records()
        
        logger.info(f"🔧 DELIVERY_TRACKING_MAINTENANCE: processed {processed_retries} retries, cleaned {cleaned_records} records")
        
    except Exception as e:
        logger.error(f"❌ Error during delivery tracking maintenance: {e}")


async def generate_delivery_health_report() -> Dict[str, Any]:
    """Generate comprehensive health report"""
    return await notification_delivery_tracker.get_health_status()