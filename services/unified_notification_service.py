"""
Unified Notification Service - Simplified Architecture

Single service to handle all notification types (Telegram, Email, SMS) synchronously.
Replaces multiple scattered notification services with one unified interface.
"""

import logging
import requests
from enum import Enum
from typing import Dict, List, Optional
from dataclasses import dataclass

# Database and models
from database import get_sync_db_session
from models import User
from config import Config

# Import existing services to leverage
from services.email import EmailService
from telegram import Bot
from telegram.error import TelegramError

logger = logging.getLogger(__name__)


class NotificationType(Enum):
    """Types of notifications"""
    TELEGRAM = "telegram"
    EMAIL = "email"
    SMS = "sms"
    ALL = "all"  # Send via all available channels


@dataclass
class NotificationResult:
    """Result of notification attempt"""
    success: bool
    channels_sent: List[str]
    errors: Dict[str, str]


class UnifiedNotificationService:
    """
    Unified service for all notifications - replaces scattered notification services.
    Handles Telegram, Email, and SMS from a single interface.
    """
    
    def __init__(self):
        # Initialize services
        self.email_service = EmailService()
        self.telegram_bot = Bot(token=Config.BOT_TOKEN) if Config.BOT_TOKEN else None
        
        # Configuration
        self.admin_email = getattr(Config, 'ADMIN_ALERT_EMAIL', None)
        self.admin_telegram_id = getattr(Config, 'ADMIN_TELEGRAM_ID', None)
        
        logger.info("✅ UnifiedNotificationService initialized")
    
    def notify_user(
        self, 
        user_id: int, 
        message: str, 
        subject: Optional[str] = None,
        notification_type: NotificationType = NotificationType.ALL
    ) -> NotificationResult:
        """
        Send notification to user via specified channels.
        
        Args:
            user_id: Database user ID
            message: Message content
            subject: Email subject (optional, defaults to message)
            notification_type: Which channels to use
            
        Returns:
            NotificationResult with success status and details
        """
        channels_sent = []
        errors = {}
        
        try:
            # Get user from database
            user = self._get_user(user_id)
            if not user:
                return NotificationResult(
                    success=False,
                    channels_sent=[],
                    errors={"user": f"User {user_id} not found"}
                )
            
            # Send via Telegram
            if notification_type in [NotificationType.TELEGRAM, NotificationType.ALL]:
                if self._send_telegram(user, message):
                    channels_sent.append("telegram")
                else:
                    errors["telegram"] = "Failed to send Telegram message"
            
            # Send via Email
            if notification_type in [NotificationType.EMAIL, NotificationType.ALL]:
                email_subject = subject or self._extract_subject_from_message(message)
                if self._send_email(user, email_subject, message):
                    channels_sent.append("email")
                else:
                    errors["email"] = "Failed to send email"
            
            # Send via SMS (placeholder for future implementation)
            if notification_type in [NotificationType.SMS, NotificationType.ALL]:
                if self._send_sms(user, message):
                    channels_sent.append("sms")
                else:
                    errors["sms"] = "SMS not implemented"
            
            success = len(channels_sent) > 0
            
            if success:
                logger.info(f"✅ USER_NOTIFY: user={user_id}, channels={channels_sent}")
            else:
                logger.error(f"❌ USER_NOTIFY_FAILED: user={user_id}, errors={errors}")
            
            return NotificationResult(
                success=success,
                channels_sent=channels_sent,
                errors=errors
            )
            
        except Exception as e:
            logger.error(f"❌ USER_NOTIFY_ERROR: user={user_id}, error={e}")
            return NotificationResult(
                success=False,
                channels_sent=[],
                errors={"system": str(e)}
            )
    
    def notify_admin(
        self, 
        subject: str, 
        message: str,
        notification_type: NotificationType = NotificationType.ALL
    ) -> NotificationResult:
        """
        Send notification to admin via specified channels.
        
        Args:
            subject: Notification subject/title
            message: Message content
            notification_type: Which channels to use
            
        Returns:
            NotificationResult with success status and details
        """
        channels_sent = []
        errors = {}
        
        try:
            # Send via admin email
            if notification_type in [NotificationType.EMAIL, NotificationType.ALL]:
                if self._send_admin_email(subject, message):
                    channels_sent.append("admin_email")
                else:
                    errors["admin_email"] = "Failed to send admin email"
            
            # Send via admin Telegram
            if notification_type in [NotificationType.TELEGRAM, NotificationType.ALL]:
                if self._send_admin_telegram(f"🚨 {subject}\n\n{message}"):
                    channels_sent.append("admin_telegram")
                else:
                    errors["admin_telegram"] = "Failed to send admin Telegram"
            
            success = len(channels_sent) > 0
            
            if success:
                logger.info(f"✅ ADMIN_NOTIFY: subject={subject}, channels={channels_sent}")
            else:
                logger.error(f"❌ ADMIN_NOTIFY_FAILED: subject={subject}, errors={errors}")
            
            return NotificationResult(
                success=success,
                channels_sent=channels_sent,
                errors=errors
            )
            
        except Exception as e:
            logger.error(f"❌ ADMIN_NOTIFY_ERROR: subject={subject}, error={e}")
            return NotificationResult(
                success=False,
                channels_sent=[],
                errors={"system": str(e)}
            )
    
    def _get_user(self, user_id: int) -> Optional[User]:
        """Get user from database"""
        try:
            with get_sync_db_session() as session:
                return session.query(User).filter(User.id == user_id).first()
        except Exception as e:
            logger.error(f"❌ GET_USER_ERROR: user_id={user_id}, error={e}")
            return None
    
    def _send_telegram(self, user: User, message: str) -> bool:
        """Send Telegram message to user"""
        try:
            if not self.telegram_bot:
                return False
            
            # Use telegram_id from user model
            telegram_id = getattr(user, 'telegram_id', None)
            if not telegram_id:
                logger.warning(f"⚠️ TELEGRAM_SKIP: user={user.id} has no telegram_id")
                return False
            
            # Use synchronous HTTP request to avoid async event loop conflicts
            self._telegram_send_sync_http(telegram_id, message)
            return True
            
        except TelegramError as e:
            logger.error(f"❌ TELEGRAM_ERROR: user={user.id}, error={e}")
            return False
        except Exception as e:
            logger.error(f"❌ TELEGRAM_UNEXPECTED: user={user.id}, error={e}")
            return False
    
    def _send_email(self, user: User, subject: str, message: str) -> bool:
        """Send email to user"""
        try:
            email = getattr(user, 'email', None)
            if not email:
                logger.warning(f"⚠️ EMAIL_SKIP: user={user.id} has no email")
                return False
            
            # Convert to HTML
            html_content = self._format_html_message(message)
            
            return self.email_service.send_email(
                to_email=email,
                subject=subject,
                text_content=message,
                html_content=html_content
            )
            
        except Exception as e:
            logger.error(f"❌ EMAIL_ERROR: user={user.id}, error={e}")
            return False
    
    def _send_sms(self, user: User, message: str) -> bool:
        """Send SMS to user (placeholder for future implementation)"""
        # TODO: Implement SMS via Twilio integration
        logger.debug(f"📱 SMS_PLACEHOLDER: user={user.id}")
        return False
    
    def _send_admin_email(self, subject: str, message: str) -> bool:
        """Send email to admin"""
        try:
            if not self.admin_email:
                logger.warning("⚠️ ADMIN_EMAIL_SKIP: No admin email configured")
                return False
            
            html_content = self._format_html_message(message)
            
            return self.email_service.send_email(
                to_email=self.admin_email,
                subject=f"[LockBay Admin] {subject}",
                text_content=message,
                html_content=html_content
            )
            
        except Exception as e:
            logger.error(f"❌ ADMIN_EMAIL_ERROR: error={e}")
            return False
    
    def _send_admin_telegram(self, message: str) -> bool:
        """Send Telegram message to admin"""
        try:
            if not self.telegram_bot or not self.admin_telegram_id:
                logger.warning("⚠️ ADMIN_TELEGRAM_SKIP: No admin Telegram configured")
                return False
            
            # Use synchronous HTTP request to avoid async event loop conflicts
            self._telegram_send_sync_http(self.admin_telegram_id, message)
            return True
            
        except TelegramError as e:
            logger.error(f"❌ ADMIN_TELEGRAM_ERROR: error={e}")
            return False
        except Exception as e:
            logger.error(f"❌ ADMIN_TELEGRAM_UNEXPECTED: error={e}")
            return False
    
    def _telegram_send_sync_http(self, chat_id: int, text: str) -> bool:
        """Send Telegram message using synchronous HTTP request to avoid event loop conflicts"""
        try:
            bot_token = Config.BOT_TOKEN
            if not bot_token:
                logger.error("❌ TELEGRAM_HTTP: No bot token configured")
                return False
            
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            data = {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML"
            }
            
            response = requests.post(url, data=data, timeout=10)
            response.raise_for_status()
            
            logger.info(f"✅ TELEGRAM_HTTP_SUCCESS: sent to chat_id={chat_id}")
            return True
            
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ TELEGRAM_HTTP_ERROR: chat_id={chat_id}, error={e}")
            return False
        except Exception as e:
            logger.error(f"❌ TELEGRAM_HTTP_UNEXPECTED: chat_id={chat_id}, error={e}")
            return False
    
    def _format_html_message(self, text_message: str) -> str:
        """Convert plain text to HTML"""
        html_message = text_message.replace('\n', '<br>')
        return f"""
        <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6;">
            <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                {html_message}
            </div>
        </body>
        </html>
        """
    
    def _extract_subject_from_message(self, message: str) -> str:
        """Extract subject from message (first line or truncated)"""
        lines = message.split('\n')
        subject = lines[0] if lines else "Notification"
        
        # Truncate if too long
        if len(subject) > 50:
            subject = subject[:47] + "..."
        
        return subject


# Global instance for easy import
unified_notification_service = UnifiedNotificationService()


# Convenience functions for backward compatibility and easy usage
def notify_user(
    user_id: int, 
    message: str, 
    subject: Optional[str] = None,
    notification_type: NotificationType = NotificationType.ALL
) -> NotificationResult:
    """
    Convenience function to notify a user.
    
    Args:
        user_id: Database user ID
        message: Message content
        subject: Email subject (optional)
        notification_type: Which channels to use
        
    Returns:
        NotificationResult with success status and details
    """
    return unified_notification_service.notify_user(user_id, message, subject, notification_type)


def notify_admin(
    subject: str, 
    message: str,
    notification_type: NotificationType = NotificationType.ALL
) -> NotificationResult:
    """
    Convenience function to notify admin.
    
    Args:
        subject: Notification subject/title
        message: Message content
        notification_type: Which channels to use
        
    Returns:
        NotificationResult with success status and details
    """
    return unified_notification_service.notify_admin(subject, message, notification_type)


# Quick notification functions for common use cases
def notify_user_telegram(user_id: int, message: str) -> bool:
    """Quick Telegram-only notification"""
    result = notify_user(user_id, message, notification_type=NotificationType.TELEGRAM)
    return result.success


def notify_user_email(user_id: int, subject: str, message: str) -> bool:
    """Quick Email-only notification"""
    result = notify_user(user_id, message, subject, NotificationType.EMAIL)
    return result.success


def notify_admin_urgent(subject: str, message: str) -> bool:
    """Quick admin notification for urgent matters"""
    result = notify_admin(f"🚨 URGENT: {subject}", message)
    return result.success