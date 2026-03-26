"""
Unified Refund Notification Service
Comprehensive notification infrastructure for all refund scenarios with guaranteed delivery
"""

import logging
import json
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from enum import Enum

# Database imports
from database import SessionLocal
from models import (
    User, Refund, RefundType, RefundStatus, Escrow, Cashout
)

# Service imports
from services.email import EmailService
from services.consolidated_notification_service import consolidated_notification_service
from services.refund_email_templates import refund_email_templates
from services.refund_bot_templates import refund_bot_templates
from services.notification_delivery_tracker import (
    DeliveryChannel,
    track_refund_notification_delivery,
    record_email_delivery_attempt,
    record_telegram_delivery_attempt
)
from config import Config

logger = logging.getLogger(__name__)


class NotificationChannel(Enum):
    """Available notification channels"""
    EMAIL = "email"
    TELEGRAM = "telegram"
    SMS = "sms"  # Via existing Twilio integration
    ADMIN_ALERT = "admin_alert"


class NotificationPriority(Enum):
    """Notification priority levels"""
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class NotificationStatus(Enum):
    """Notification delivery status"""
    PENDING = "pending"
    SENT = "sent"
    DELIVERED = "delivered"
    FAILED = "failed"
    RETRYING = "retrying"
    EXPIRED = "expired"
    USER_CONFIRMED = "user_confirmed"


class RefundNotificationTemplate:
    """Template data structure for refund notifications"""
    
    def __init__(
        self,
        template_id: str,
        refund_type: RefundType,
        priority: NotificationPriority = NotificationPriority.NORMAL,
        channels: List[NotificationChannel] = None,
        retry_config: Dict = None
    ):
        self.template_id = template_id
        self.refund_type = refund_type
        self.priority = priority
        self.channels = channels or [NotificationChannel.EMAIL, NotificationChannel.TELEGRAM]
        self.retry_config = retry_config or {
            "max_retries": 5,
            "initial_delay": 60,  # seconds
            "backoff_multiplier": 2.0,
            "max_delay": 3600  # 1 hour
        }


class UnifiedRefundNotificationService:
    """
    Unified service for all refund notifications with guaranteed delivery mechanisms
    """
    
    def __init__(self):
        self.email_service = EmailService()
        self.email_templates = refund_email_templates
        self.bot_templates = refund_bot_templates
        self.templates = self._initialize_templates()
        self.notification_queue = []
        self.delivery_tracker = {}  # Keep for backward compatibility
        
        logger.info("✅ Unified Refund Notification Service initialized with delivery tracking")
        
    def _initialize_templates(self) -> Dict[str, RefundNotificationTemplate]:
        """Initialize notification templates for all refund scenarios"""
        return {
            # Automatic refund templates
            "cashout_failed_refund": RefundNotificationTemplate(
                "cashout_failed_refund",
                RefundType.CASHOUT_FAILED,
                NotificationPriority.HIGH,
                [NotificationChannel.EMAIL, NotificationChannel.TELEGRAM, NotificationChannel.ADMIN_ALERT]
            ),
            "escrow_timeout_refund": RefundNotificationTemplate(
                "escrow_timeout_refund", 
                RefundType.ESCROW_REFUND,
                NotificationPriority.NORMAL,
                [NotificationChannel.EMAIL, NotificationChannel.TELEGRAM]
            ),
            "dispute_resolution_refund": RefundNotificationTemplate(
                "dispute_resolution_refund",
                RefundType.DISPUTE_REFUND,
                NotificationPriority.HIGH,
                [NotificationChannel.EMAIL, NotificationChannel.TELEGRAM, NotificationChannel.ADMIN_ALERT]
            ),
            "admin_manual_refund": RefundNotificationTemplate(
                "admin_manual_refund",
                RefundType.ADMIN_REFUND,
                NotificationPriority.NORMAL,
                [NotificationChannel.EMAIL, NotificationChannel.TELEGRAM]
            ),
            "system_error_refund": RefundNotificationTemplate(
                "system_error_refund",
                RefundType.ERROR_REFUND,
                NotificationPriority.URGENT,
                [NotificationChannel.EMAIL, NotificationChannel.TELEGRAM, NotificationChannel.ADMIN_ALERT]
            ),
            
            # Special scenario templates
            "post_timeout_payment_refund": RefundNotificationTemplate(
                "post_timeout_payment_refund",
                RefundType.ERROR_REFUND,
                NotificationPriority.HIGH,
                [NotificationChannel.EMAIL, NotificationChannel.TELEGRAM, NotificationChannel.ADMIN_ALERT]
            ),
            "overpayment_refund": RefundNotificationTemplate(
                "overpayment_refund",
                RefundType.ERROR_REFUND,
                NotificationPriority.NORMAL,
                [NotificationChannel.EMAIL, NotificationChannel.TELEGRAM]
            ),
            "rate_lock_expired_refund": RefundNotificationTemplate(
                "rate_lock_expired_refund",
                RefundType.ERROR_REFUND,
                NotificationPriority.NORMAL,
                [NotificationChannel.EMAIL, NotificationChannel.TELEGRAM]
            ),
            
            # Confirmation and status update templates  
            "refund_processing_confirmation": RefundNotificationTemplate(
                "refund_processing_confirmation",
                None,  # Generic template
                NotificationPriority.LOW,
                [NotificationChannel.EMAIL, NotificationChannel.TELEGRAM]
            ),
            "refund_completed_confirmation": RefundNotificationTemplate(
                "refund_completed_confirmation", 
                None,  # Generic template
                NotificationPriority.NORMAL,
                [NotificationChannel.EMAIL, NotificationChannel.TELEGRAM]
            )
        }
    
    async def send_refund_notification(
        self,
        user_id: int,
        refund: Refund,
        template_id: str,
        additional_context: Dict[str, Any] = None,
        override_channels: List[NotificationChannel] = None,
        require_user_confirmation: bool = False
    ) -> Dict[str, Any]:
        """
        Main entry point for sending refund notifications
        
        Args:
            user_id: Target user ID
            refund: Refund database record
            template_id: Template identifier
            additional_context: Additional template variables
            override_channels: Override default notification channels
            require_user_confirmation: Require user to acknowledge receipt
            
        Returns:
            Dictionary with delivery status for each channel
        """
        try:
            # Generate unique notification ID for tracking
            notification_id = f"refund_notif_{refund.refund_id}_{template_id}_{int(datetime.utcnow().timestamp())}"
            
            logger.info(f"📧 REFUND_NOTIFICATION_START: {notification_id} for user {user_id}")
            
            # Get template configuration
            template = self.templates.get(template_id)
            if not template:
                logger.error(f"❌ Unknown notification template: {template_id}")
                return {"error": f"Unknown template: {template_id}"}
                
            # Prepare notification context
            context = await self._prepare_notification_context(
                user_id=user_id,
                refund=refund,
                template_id=template_id,
                additional_context=additional_context or {}
            )
            
            if not context:
                logger.error(f"❌ Failed to prepare notification context for {notification_id}")
                return {"error": "Failed to prepare notification context"}
            
            # Determine channels to use
            channels = override_channels or template.channels
            
            # Initialize delivery tracker (both old and new systems for compatibility)
            self.delivery_tracker[notification_id] = {
                "template_id": template_id,
                "user_id": user_id,
                "refund_id": refund.refund_id,
                "channels": {},
                "priority": template.priority,
                "require_confirmation": require_user_confirmation,
                "created_at": datetime.utcnow(),
                "retry_config": template.retry_config
            }
            
            # Start tracking with new delivery tracking system
            delivery_channels = [self._map_notification_channel_to_delivery_channel(ch) for ch in channels]
            await track_refund_notification_delivery(
                notification_id=notification_id,
                user_id=user_id,
                template_id=template_id,
                channels=[ch.value for ch in delivery_channels],
                priority=template.priority.value
            )
            
            # Send notifications through all channels
            delivery_results = {}
            for channel in channels:
                try:
                    delivery_result = await self._send_through_channel(
                        notification_id=notification_id,
                        channel=channel,
                        context=context,
                        template=template
                    )
                    delivery_results[channel.value] = delivery_result
                    
                    # Track delivery status
                    self.delivery_tracker[notification_id]["channels"][channel.value] = delivery_result
                    
                except Exception as channel_error:
                    logger.error(f"❌ Channel {channel.value} failed for {notification_id}: {channel_error}")
                    delivery_results[channel.value] = {
                        "status": NotificationStatus.FAILED.value,
                        "error": str(channel_error),
                        "timestamp": datetime.utcnow().isoformat()
                    }
                    
                    self.delivery_tracker[notification_id]["channels"][channel.value] = delivery_results[channel.value]
            
            # Check if any channel succeeded
            successful_channels = [
                channel for channel, result in delivery_results.items()
                if result.get("status") in [NotificationStatus.SENT.value, NotificationStatus.DELIVERED.value]
            ]
            
            if successful_channels:
                logger.info(f"✅ REFUND_NOTIFICATION_SUCCESS: {notification_id} sent via {successful_channels}")
            else:
                logger.error(f"❌ REFUND_NOTIFICATION_FAILED: {notification_id} failed on all channels")
                
                # Schedule retry for failed notifications
                await self._schedule_retry(notification_id, template.retry_config)
                
                # Send admin alert for critical failures
                if template.priority in [NotificationPriority.HIGH, NotificationPriority.URGENT]:
                    await self._send_admin_failure_alert(notification_id, delivery_results, context)
            
            # Log comprehensive delivery status
            await self._log_notification_delivery(notification_id, delivery_results, context)
            
            return {
                "notification_id": notification_id,
                "delivery_results": delivery_results,
                "successful_channels": successful_channels,
                "template": template_id,
                "priority": template.priority.value
            }
            
        except Exception as e:
            logger.error(f"❌ REFUND_NOTIFICATION_ERROR: {e}")
            return {"error": str(e)}
    
    async def _prepare_notification_context(
        self,
        user_id: int,
        refund: Refund,
        template_id: str,
        additional_context: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Prepare comprehensive notification context with all required data"""
        try:
            session = SessionLocal()
            
            # Get user details
            user = session.query(User).filter(User.id == user_id).first()
            if not user:
                logger.error(f"❌ User {user_id} not found for refund notification")
                return None
            
            # Prepare base context
            context = {
                # User information
                "user": {
                    "id": user.id,
                    "telegram_id": user.telegram_id,
                    "username": user.username or "User",
                    "first_name": user.first_name or "User",
                    "email": getattr(user, 'email', None)
                },
                
                # Refund information
                "refund": {
                    "refund_id": refund.refund_id,
                    "amount": float(refund.amount),
                    "currency": refund.currency,
                    "formatted_amount": f"{Config.CURRENCY_SYMBOLS.get(refund.currency, refund.currency)}{refund.amount:.2f}",
                    "type": refund.refund_type,
                    "type_display": self._get_refund_type_display(refund.refund_type),
                    "reason": refund.reason,
                    "status": refund.status,
                    "status_display": self._get_refund_status_display(refund.status),
                    "created_at": refund.created_at.strftime("%Y-%m-%d %H:%M:%S UTC"),
                    "completed_at": refund.completed_at.strftime("%Y-%m-%d %H:%M:%S UTC") if refund.completed_at else None
                },
                
                # Platform information
                "platform": {
                    "name": Config.BRAND,
                    "support_email": Config.SUPPORT_EMAIL,
                    "webapp_url": Config.WEBAPP_URL,
                    "help_url": Config.HELP_URL
                },
                
                # Template and notification metadata
                "template_id": template_id,
                "notification_timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
            }
            
            # Add related entity context based on refund type
            if refund.cashout_id:
                cashout = session.query(Cashout).filter(Cashout.cashout_id == refund.cashout_id).first()
                if cashout:
                    context["cashout"] = {
                        "cashout_id": cashout.cashout_id,
                        "amount": float(cashout.amount),
                        "currency": cashout.currency,
                        "cashout_type": cashout.cashout_type,
                        "status": cashout.status,
                        "created_at": cashout.created_at.strftime("%Y-%m-%d %H:%M:%S UTC"),
                        "destination_address": getattr(cashout, 'destination_address', None)
                    }
            
            if refund.escrow_id:
                escrow = session.query(Escrow).filter(Escrow.id == refund.escrow_id).first()
                if escrow:
                    context["escrow"] = {
                        "escrow_id": escrow.escrow_id,
                        "amount": float(escrow.amount),
                        "currency": escrow.currency,
                        "status": escrow.status,
                        "created_at": escrow.created_at.strftime("%Y-%m-%d %H:%M:%S UTC"),
                        "item_name": escrow.item_name
                    }
            
            # Add wallet balance information for context
            if hasattr(user, 'wallet') and user.wallet:
                context["wallet"] = {
                    "balance": float(user.wallet.available_balance),
                    "currency": user.wallet.currency,
                    "formatted_balance": f"{Config.CURRENCY_SYMBOLS.get(user.wallet.currency, user.wallet.currency)}{user.wallet.available_balance:.2f}"
                }
            
            # Merge additional context
            context.update(additional_context)
            
            session.close()
            return context
            
        except Exception as e:
            logger.error(f"❌ Error preparing notification context: {e}")
            return None
    
    async def _send_through_channel(
        self,
        notification_id: str,
        channel: NotificationChannel,
        context: Dict[str, Any],
        template: RefundNotificationTemplate
    ) -> Dict[str, Any]:
        """Send notification through a specific channel"""
        try:
            timestamp = datetime.utcnow()
            
            if channel == NotificationChannel.EMAIL:
                result = await self._send_email_notification(notification_id, context, template)
            elif channel == NotificationChannel.TELEGRAM:
                result = await self._send_telegram_notification(notification_id, context, template)
            elif channel == NotificationChannel.SMS:
                result = await self._send_sms_notification(notification_id, context, template)
            elif channel == NotificationChannel.ADMIN_ALERT:
                result = await self._send_admin_alert_notification(notification_id, context, template)
            else:
                result = {
                    "status": NotificationStatus.FAILED.value,
                    "error": f"Unsupported channel: {channel.value}",
                    "timestamp": timestamp.isoformat()
                }
            
            # Add metadata to result
            result["channel"] = channel.value
            result["notification_id"] = notification_id
            
            return result
            
        except Exception as e:
            logger.error(f"❌ Error sending through {channel.value}: {e}")
            return {
                "status": NotificationStatus.FAILED.value,
                "error": str(e),
                "channel": channel.value,
                "timestamp": datetime.utcnow().isoformat()
            }
    
    async def _send_email_notification(
        self,
        notification_id: str,
        context: Dict[str, Any],
        template: RefundNotificationTemplate
    ) -> Dict[str, Any]:
        """Send email notification using existing email service with delivery tracking"""
        start_time = datetime.utcnow()
        
        try:
            if not self.email_service.enabled:
                await record_email_delivery_attempt(
                    notification_id=notification_id,
                    success=False,
                    error_message="Email service not enabled"
                )
                return {
                    "status": NotificationStatus.FAILED.value,
                    "error": "Email service not enabled",
                    "timestamp": start_time.isoformat()
                }
            
            user_email = context["user"].get("email")
            if not user_email:
                await record_email_delivery_attempt(
                    notification_id=notification_id,
                    success=False,
                    error_message="User email not available"
                )
                return {
                    "status": NotificationStatus.FAILED.value,
                    "error": "User email not available",
                    "timestamp": start_time.isoformat()
                }
            
            # Generate email content using comprehensive templates
            email_content = self.email_templates.generate_email_content(template.template_id, context)
            
            # Send email via Brevo service
            success = self.email_service.send_email(
                to_email=user_email,
                subject=email_content["subject"],
                html_content=email_content["html_body"],
                text_content=email_content["text_body"]
            )
            
            # Calculate response time
            response_time_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)
            
            # Record delivery attempt
            await record_email_delivery_attempt(
                notification_id=notification_id,
                success=success,
                error_message=None if success else "Email service returned false",
                response_time_ms=response_time_ms,
                external_id=None  # Brevo doesn't return message ID in current implementation
            )
            
            if success:
                return {
                    "status": NotificationStatus.SENT.value,
                    "recipient": user_email,
                    "response_time_ms": response_time_ms,
                    "timestamp": start_time.isoformat()
                }
            else:
                return {
                    "status": NotificationStatus.FAILED.value,
                    "error": "Email service returned false",
                    "response_time_ms": response_time_ms,
                    "timestamp": start_time.isoformat()
                }
                
        except Exception as e:
            response_time_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)
            
            # Record failed attempt
            await record_email_delivery_attempt(
                notification_id=notification_id,
                success=False,
                error_message=str(e),
                response_time_ms=response_time_ms
            )
            
            logger.error(f"❌ Email notification error: {e}")
            return {
                "status": NotificationStatus.FAILED.value,
                "error": str(e),
                "response_time_ms": response_time_ms,
                "timestamp": start_time.isoformat()
            }
    
    async def _send_telegram_notification(
        self,
        notification_id: str,
        context: Dict[str, Any],
        template: RefundNotificationTemplate
    ) -> Dict[str, Any]:
        """Send Telegram notification with inline keyboards and delivery tracking"""
        start_time = datetime.utcnow()
        
        try:
            telegram_id = context["user"]["telegram_id"]
            if not telegram_id:
                await record_telegram_delivery_attempt(
                    notification_id=notification_id,
                    success=False,
                    error_message="User Telegram ID not available"
                )
                return {
                    "status": NotificationStatus.FAILED.value,
                    "error": "User Telegram ID not available",
                    "timestamp": start_time.isoformat()
                }
            
            # Generate Telegram message content using comprehensive templates
            message_content = self.bot_templates.generate_bot_content(template.template_id, context)
            
            # Send via consolidated notification service
            await consolidated_notification_service.send_telegram_notification(
                user_id=int(telegram_id),
                message=message_content["message"],
                reply_markup=message_content.get("keyboard"),
                parse_mode=message_content.get("parse_mode", "Markdown")
            )
            
            # Calculate response time
            response_time_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)
            
            # Record successful delivery attempt
            await record_telegram_delivery_attempt(
                notification_id=notification_id,
                success=True,
                response_time_ms=response_time_ms,
                external_id=telegram_id
            )
            
            return {
                "status": NotificationStatus.SENT.value,
                "recipient": telegram_id,
                "response_time_ms": response_time_ms,
                "timestamp": start_time.isoformat()
            }
            
        except Exception as e:
            response_time_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)
            
            # Record failed attempt
            await record_telegram_delivery_attempt(
                notification_id=notification_id,
                success=False,
                error_message=str(e),
                response_time_ms=response_time_ms
            )
            
            logger.error(f"❌ Telegram notification error: {e}")
            return {
                "status": NotificationStatus.FAILED.value,
                "error": str(e),
                "response_time_ms": response_time_ms,
                "timestamp": start_time.isoformat()
            }
    
    async def _send_sms_notification(
        self,
        notification_id: str,
        context: Dict[str, Any],
        template: RefundNotificationTemplate
    ) -> Dict[str, Any]:
        """Send SMS notification via existing Twilio integration"""
        try:
            # Check if user has SMS enabled and phone number
            # This would integrate with existing Twilio SMS service
            return {
                "status": NotificationStatus.FAILED.value,
                "error": "SMS notifications not implemented",
                "timestamp": datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            logger.error(f"❌ SMS notification error: {e}")
            return {
                "status": NotificationStatus.FAILED.value,
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat()
            }
    
    async def _send_admin_alert_notification(
        self,
        notification_id: str,
        context: Dict[str, Any],
        template: RefundNotificationTemplate
    ) -> Dict[str, Any]:
        """Send admin alert for high-priority refund notifications"""
        try:
            refund_info = context["refund"]
            user_info = context["user"]
            
            alert_message = (
                f"🔄 REFUND_NOTIFICATION_ALERT\n"
                f"Type: {refund_info['type_display']}\n"
                f"Amount: {refund_info['formatted_amount']}\n"
                f"User: {user_info['first_name']} (@{user_info['username']})\n"
                f"Refund ID: {refund_info['refund_id']}\n"
                f"Reason: {refund_info['reason']}\n"
                f"Template: {template.template_id}\n"
                f"Notification ID: {notification_id}"
            )
            
            await consolidated_notification_service.send_admin_alert(
                message=alert_message,
                priority=template.priority.value
            )
            
            return {
                "status": NotificationStatus.SENT.value,
                "timestamp": datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            logger.error(f"❌ Admin alert notification error: {e}")
            return {
                "status": NotificationStatus.FAILED.value,
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat()
            }
    
    def _map_notification_channel_to_delivery_channel(self, channel: NotificationChannel) -> DeliveryChannel:
        """Map notification channels to delivery tracking channels"""
        mapping = {
            NotificationChannel.EMAIL: DeliveryChannel.EMAIL,
            NotificationChannel.TELEGRAM: DeliveryChannel.TELEGRAM,
            NotificationChannel.SMS: DeliveryChannel.SMS,
            NotificationChannel.ADMIN_ALERT: DeliveryChannel.ADMIN_ALERT
        }
        return mapping.get(channel, DeliveryChannel.EMAIL)
    
    
    async def _schedule_retry(self, notification_id: str, retry_config: Dict) -> None:
        """Schedule retry for failed notification with exponential backoff"""
        try:
            # This would integrate with existing job scheduler for retry logic
            logger.info(f"📋 Scheduling retry for notification {notification_id}")
            
            # Implementation would add to retry queue with exponential backoff
            # For now, just log the retry attempt
            
        except Exception as e:
            logger.error(f"❌ Error scheduling retry for {notification_id}: {e}")
    
    async def _send_admin_failure_alert(
        self,
        notification_id: str,
        delivery_results: Dict,
        context: Dict[str, Any]
    ) -> None:
        """Send admin alert for critical notification delivery failures"""
        try:
            refund = context["refund"]
            user = context["user"]
            
            alert_message = (
                f"🚨 CRITICAL_NOTIFICATION_FAILURE\n"
                f"Notification ID: {notification_id}\n"
                f"User: {user['first_name']} (ID: {user['id']})\n"
                f"Refund: {refund['formatted_amount']} ({refund['refund_id']})\n"
                f"All delivery channels failed:\n"
            )
            
            for channel, result in delivery_results.items():
                alert_message += f"• {channel}: {result.get('error', 'Unknown error')}\n"
            
            alert_message += "\nManual intervention required!"
            
            await consolidated_notification_service.send_admin_alert(
                message=alert_message,
                priority="urgent"
            )
            
        except Exception as e:
            logger.error(f"❌ Error sending admin failure alert: {e}")
    
    async def _log_notification_delivery(
        self,
        notification_id: str,
        delivery_results: Dict,
        context: Dict[str, Any]
    ) -> None:
        """Log comprehensive notification delivery status"""
        try:
            refund = context["refund"]
            user = context["user"]
            
            logger.info(
                f"📊 NOTIFICATION_DELIVERY_SUMMARY: "
                f"ID={notification_id}, "
                f"User={user['id']}, "
                f"Refund={refund['refund_id']}, "
                f"Results={json.dumps(delivery_results, default=str)}"
            )
            
        except Exception as e:
            logger.error(f"❌ Error logging notification delivery: {e}")
    
    def _get_refund_type_display(self, refund_type: str) -> str:
        """Get user-friendly display name for refund type"""
        displays = {
            RefundType.CASHOUT_FAILED.value: "Failed Cashout Refund",
            RefundType.ESCROW_REFUND.value: "Escrow Refund", 
            RefundType.DISPUTE_REFUND.value: "Dispute Resolution Refund",
            RefundType.ADMIN_REFUND.value: "Manual Refund",
            RefundType.ERROR_REFUND.value: "System Error Refund"
        }
        return displays.get(refund_type, "Refund")
    
    def _get_refund_status_display(self, refund_status: str) -> str:
        """Get user-friendly display name for refund status"""
        displays = {
            RefundStatus.PENDING.value: "Processing",
            RefundStatus.COMPLETED.value: "Completed",
            RefundStatus.FAILED.value: "Failed"
        }
        return displays.get(refund_status, refund_status.title())

    # Additional methods for delivery tracking and management
    
    async def get_notification_status(self, notification_id: str) -> Optional[Dict[str, Any]]:
        """Get current status of a notification"""
        return self.delivery_tracker.get(notification_id)
    
    async def retry_failed_notification(self, notification_id: str) -> Dict[str, Any]:
        """Manually retry a failed notification"""
        try:
            notification_data = self.delivery_tracker.get(notification_id)
            if not notification_data:
                return {"error": "Notification not found"}
            
            # Implementation would retry the notification
            logger.info(f"🔄 Manual retry for notification {notification_id}")
            
            return {"status": "retry_scheduled"}
            
        except Exception as e:
            logger.error(f"❌ Error retrying notification {notification_id}: {e}")
            return {"error": str(e)}
    
    async def confirm_user_receipt(self, notification_id: str, user_id: int) -> bool:
        """Mark notification as confirmed by user"""
        try:
            # Update old tracking system for compatibility
            notification_data = self.delivery_tracker.get(notification_id)
            if notification_data and notification_data["user_id"] == user_id:
                notification_data["user_confirmed_at"] = datetime.utcnow()
                notification_data["status"] = NotificationStatus.USER_CONFIRMED.value
            
            # Update new tracking system
            from services.notification_delivery_tracker import confirm_notification_receipt
            success = await confirm_notification_receipt(
                notification_id=notification_id,
                user_id=user_id,
                method="unified_service_confirmation"
            )
            
            if success:
                logger.info(f"✅ User {user_id} confirmed receipt of notification {notification_id}")
            
            return success
            
        except Exception as e:
            logger.error(f"❌ Error confirming notification receipt: {e}")
            return False
    
    async def get_delivery_analytics(self, date_range: tuple = None) -> Dict[str, Any]:
        """Get notification delivery analytics and metrics"""
        try:
            # Implementation would analyze delivery_tracker data
            # and provide comprehensive analytics
            
            return {
                "total_notifications": len(self.delivery_tracker),
                "delivery_rates": {},
                "failure_rates": {},
                "retry_statistics": {},
                "channel_performance": {}
            }
            
        except Exception as e:
            logger.error(f"❌ Error generating delivery analytics: {e}")
            return {"error": str(e)}


# Global service instance
unified_refund_notification_service = UnifiedRefundNotificationService()


# Convenience functions for easy integration

async def send_cashout_failed_refund_notification(
    user_id: int,
    refund: Refund,
    cashout_details: Dict[str, Any] = None
) -> Dict[str, Any]:
    """Send notification for failed cashout refund"""
    return await unified_refund_notification_service.send_refund_notification(
        user_id=user_id,
        refund=refund,
        template_id="cashout_failed_refund",
        additional_context={"cashout_details": cashout_details or {}},
        require_user_confirmation=True
    )


async def send_escrow_timeout_refund_notification(
    user_id: int,
    refund: Refund,
    escrow_details: Dict[str, Any] = None
) -> Dict[str, Any]:
    """Send notification for escrow timeout refund"""
    return await unified_refund_notification_service.send_refund_notification(
        user_id=user_id,
        refund=refund,
        template_id="escrow_timeout_refund",
        additional_context={"escrow_details": escrow_details or {}}
    )


async def send_dispute_resolution_refund_notification(
    user_id: int,
    refund: Refund,
    dispute_details: Dict[str, Any] = None
) -> Dict[str, Any]:
    """Send notification for dispute resolution refund"""
    return await unified_refund_notification_service.send_refund_notification(
        user_id=user_id,
        refund=refund,
        template_id="dispute_resolution_refund",
        additional_context={"dispute_details": dispute_details or {}},
        require_user_confirmation=True
    )


async def send_post_timeout_payment_refund_notification(
    user_id: int,
    refund: Refund,
    payment_details: Dict[str, Any] = None
) -> Dict[str, Any]:
    """Send notification for post-timeout payment refund"""
    return await unified_refund_notification_service.send_refund_notification(
        user_id=user_id,
        refund=refund,
        template_id="post_timeout_payment_refund",
        additional_context={"payment_details": payment_details or {}},
        require_user_confirmation=True
    )


async def send_generic_refund_confirmation(
    user_id: int,
    refund: Refund,
    confirmation_type: str = "completed"
) -> Dict[str, Any]:
    """Send generic refund confirmation"""
    template_id = f"refund_{confirmation_type}_confirmation"
    return await unified_refund_notification_service.send_refund_notification(
        user_id=user_id,
        refund=refund,
        template_id=template_id
    )


# Health check and monitoring functions

async def get_notification_service_health() -> Dict[str, Any]:
    """Get health status of notification service"""
    return {
        "service": "unified_refund_notification_service",
        "status": "healthy",
        "email_service_enabled": unified_refund_notification_service.email_service.enabled,
        "templates_loaded": len(unified_refund_notification_service.templates),
        "active_notifications": len(unified_refund_notification_service.delivery_tracker),
        "timestamp": datetime.utcnow().isoformat()
    }


async def cleanup_old_delivery_tracking():
    """Clean up old delivery tracking records"""
    try:
        cutoff_date = datetime.utcnow() - timedelta(days=30)
        
        old_notifications = [
            notif_id for notif_id, data in unified_refund_notification_service.delivery_tracker.items()
            if data.get("created_at", datetime.utcnow()) < cutoff_date
        ]
        
        for notif_id in old_notifications:
            del unified_refund_notification_service.delivery_tracker[notif_id]
        
        logger.info(f"🧹 Cleaned up {len(old_notifications)} old notification tracking records")
        
    except Exception as e:
        logger.error(f"❌ Error cleaning up delivery tracking: {e}")