"""
Seller Notification Router - Routes notifications to correct channel based on seller contact type

This helper determines the appropriate notification channel (Telegram, Email, SMS) 
based on the seller's contact type and routes notifications accordingly.
"""

import logging
import os
from typing import Optional
from dataclasses import dataclass

from database import SessionLocal
from models import SellerContactType
from services.unified_notification_service import UnifiedNotificationService, NotificationType
from services.seller_invitation import SellerInvitationService
from services.email import EmailService

# Twilio SMS integration
try:
    from twilio.rest import Client as TwilioClient
    TWILIO_AVAILABLE = True
except ImportError:
    TwilioClient = None
    TWILIO_AVAILABLE = False

logger = logging.getLogger(__name__)


@dataclass
class NotificationRoute:
    """Result of notification routing determination"""
    channel: str  # 'telegram', 'email', 'sms', 'none'
    recipient: Optional[str]  # Username, email, or phone
    user_id: Optional[int]  # Database user ID if user exists
    needs_invitation: bool  # Whether user needs to be invited first
    message_sent: bool  # Whether message was actually delivered
    success: bool  # Overall operation success
    error: Optional[str] = None


class SellerNotificationRouter:
    """
    Routes notifications to sellers based on their contact type.
    
    Handles three invitation methods:
    - username: Send via Telegram 
    - email: Send via email
    - phone: Send via SMS
    """
    
    def __init__(self):
        self.unified_service = UnifiedNotificationService()
        self.email_service = EmailService()
        
        # Initialize Twilio client if available
        self.twilio_client = None
        if TWILIO_AVAILABLE:
            try:
                account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
                auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
                self.twilio_phone = os.environ.get("TWILIO_PHONE_NUMBER")
                
                if account_sid and auth_token and self.twilio_phone:
                    self.twilio_client = TwilioClient(account_sid, auth_token)
                    logger.info("✅ Twilio SMS service initialized")
                else:
                    logger.warning("⚠️ Twilio credentials not configured")
            except Exception as e:
                logger.error(f"❌ Failed to initialize Twilio: {e}")
        else:
            logger.warning("⚠️ Twilio not available - SMS functionality disabled")
        
        logger.info("✅ SellerNotificationRouter initialized")
    
    def route_seller_notification(
        self, 
        seller_contact_type: str,
        seller_contact_value: str,
        message: str,
        subject: Optional[str] = None
    ) -> NotificationRoute:
        """
        Route notification to seller based on their contact type.
        
        Args:
            seller_contact_type: 'username', 'email', or 'phone'
            seller_contact_value: The actual contact value (username, email, or phone)
            message: Message content to send
            subject: Email subject (for email notifications)
            
        Returns:
            NotificationRoute with routing result and success status
        """
        try:
            # Handle each contact type
            if seller_contact_type == SellerContactType.USERNAME.value:
                return self._route_username_notification(seller_contact_value, message)
            elif seller_contact_type == SellerContactType.EMAIL.value:
                return self._route_email_notification(seller_contact_value, message, subject)
            elif seller_contact_type == SellerContactType.PHONE.value:
                return self._route_phone_notification(seller_contact_value, message)
            else:
                logger.error(f"❌ Invalid seller contact type: {seller_contact_type}")
                return NotificationRoute(
                    channel='none',
                    recipient=None,
                    user_id=None,
                    needs_invitation=False,
                    message_sent=False,
                    success=False,
                    error=f"Invalid contact type: {seller_contact_type}"
                )
                
        except Exception as e:
            logger.error(f"❌ ROUTING_ERROR: type={seller_contact_type}, value={seller_contact_value}, error={e}")
            return NotificationRoute(
                channel='none',
                recipient=None,
                user_id=None,
                needs_invitation=False,
                message_sent=False,
                success=False,
                error=str(e)
            )
    
    def _route_username_notification(self, username: str, message: str) -> NotificationRoute:
        """Route notification via Telegram username"""
        try:
            # Find existing user by username
            session = SessionLocal()
            try:
                existing_user = SellerInvitationService.find_user_by_username(username, session)
                
                if existing_user and existing_user.telegram_id:
                    # User exists - send via Telegram
                    result = self.unified_service.notify_user(
                        int(existing_user.id), 
                        message, 
                        notification_type=NotificationType.TELEGRAM
                    )
                    
                    return NotificationRoute(
                        channel='telegram',
                        recipient=f"@{username}",
                        user_id=int(existing_user.id),
                        needs_invitation=False,
                        message_sent=result.success,
                        success=result.success,
                        error=result.errors.get('telegram') if not result.success else None
                    )
                else:
                    # User doesn't exist - needs invitation via Telegram
                    logger.info(f"📞 Username @{username} needs Telegram invitation")
                    return NotificationRoute(
                        channel='telegram',
                        recipient=f"@{username}",
                        user_id=None,
                        needs_invitation=True,
                        message_sent=False,  # No message sent yet, needs invitation
                        success=True,  # Routing successful, invitation needed
                        error=None
                    )
                    
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"❌ USERNAME_ROUTING_ERROR: username={username}, error={e}")
            return NotificationRoute(
                channel='telegram',
                recipient=f"@{username}",
                user_id=None,
                needs_invitation=False,
                message_sent=False,
                success=False,
                error=str(e)
            )
    
    def _route_email_notification(self, email: str, message: str, subject: Optional[str]) -> NotificationRoute:
        """Route notification via email"""
        try:
            # Find existing user by email
            session = SessionLocal()
            try:
                existing_user = SellerInvitationService.find_user_by_email(email, session)
                
                if existing_user:
                    # User exists - send notification via email
                    result = self.unified_service.notify_user(
                        int(existing_user.id),
                        message,
                        subject=subject,
                        notification_type=NotificationType.EMAIL
                    )
                    
                    return NotificationRoute(
                        channel='email',
                        recipient=email,
                        user_id=int(existing_user.id),
                        needs_invitation=False,
                        message_sent=result.success,
                        success=result.success,
                        error=result.errors.get('email') if not result.success else None
                    )
                else:
                    # User doesn't exist - send invitation email
                    logger.info(f"📧 Email {email} needs invitation email")
                    # Send invitation email directly
                    try:
                        email_subject = subject or "LockBay Trading Invitation"
                        success = self.email_service.send_email(
                            to_email=email,
                            subject=email_subject,
                            body=message
                        )
                        
                        return NotificationRoute(
                            channel='email',
                            recipient=email,
                            user_id=None,
                            needs_invitation=True,
                            message_sent=success,
                            success=success,
                            error=None if success else "Failed to send invitation email"
                        )
                    except Exception as e:
                        logger.error(f"❌ EMAIL_INVITATION_ERROR: {e}")
                        return NotificationRoute(
                            channel='email',
                            recipient=email,
                            user_id=None,
                            needs_invitation=True,
                            message_sent=False,
                            success=False,
                            error=str(e)
                        )
                        
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"❌ EMAIL_ROUTING_ERROR: email={email}, error={e}")
            return NotificationRoute(
                channel='email',
                recipient=email,
                user_id=None,
                needs_invitation=False,
                message_sent=False,
                success=False,
                error=str(e)
            )
    
    def _send_sms(self, phone: str, message: str) -> bool:
        """Send SMS message using Twilio"""
        try:
            if not self.twilio_client or not self.twilio_phone:
                logger.error("❌ SMS_ERROR: Twilio not configured")
                return False
            
            # Send SMS via Twilio
            sms_message = self.twilio_client.messages.create(
                to=phone,
                from_=self.twilio_phone,
                body=message
            )
            
            logger.info(f"✅ SMS_SUCCESS: sent to {phone}, SID={sms_message.sid}")
            return True
            
        except Exception as e:
            logger.error(f"❌ SMS_ERROR: phone={phone}, error={e}")
            return False
    
    def _route_phone_notification(self, phone: str, message: str) -> NotificationRoute:
        """Route notification via SMS/phone"""
        try:
            # Find existing user by phone
            session = SessionLocal()
            try:
                existing_user = SellerInvitationService.find_user_by_phone(phone, session)
                
                if existing_user and existing_user.telegram_id:
                    # User exists with Telegram - prefer Telegram over SMS
                    result = self.unified_service.notify_user(
                        int(existing_user.id),
                        message,
                        notification_type=NotificationType.TELEGRAM
                    )
                    
                    username_str = str(existing_user.username) if existing_user.username else phone
                    return NotificationRoute(
                        channel='telegram',
                        recipient=f"@{username_str}",
                        user_id=int(existing_user.id),
                        needs_invitation=False,
                        message_sent=result.success,
                        success=result.success,
                        error=result.errors.get('telegram') if not result.success else None
                    )
                else:
                    # User doesn't exist - send SMS invitation
                    logger.info(f"📱 Sending SMS invitation to {phone}")
                    sms_sent = self._send_sms(phone, message)
                    
                    return NotificationRoute(
                        channel='sms',
                        recipient=phone,
                        user_id=None,
                        needs_invitation=True,
                        message_sent=sms_sent,
                        success=sms_sent,
                        error=None if sms_sent else "Failed to send SMS invitation"
                    )
                    
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"❌ PHONE_ROUTING_ERROR: phone={phone}, error={e}")
            return NotificationRoute(
                channel='sms',
                recipient=phone,
                user_id=None,
                needs_invitation=False,
                message_sent=False,
                success=False,
                error=str(e)
            )
    
    def send_escrow_notification(
        self,
        seller_contact_type: str,
        seller_contact_value: str,
        escrow_id: str,
        notification_type: str = "invitation"
    ) -> NotificationRoute:
        """
        Send escrow-specific notification to seller.
        
        Args:
            seller_contact_type: 'username', 'email', or 'phone'
            seller_contact_value: The actual contact value
            escrow_id: Escrow transaction ID
            notification_type: Type of notification ('invitation', 'payment', 'update')
            
        Returns:
            NotificationRoute with routing result
        """
        # Generate appropriate message based on notification type
        if notification_type == "invitation":
            message = f"""🤝 **LockBay Escrow Invitation**

You've been invited to participate in a secure escrow transaction.

**Transaction ID:** {escrow_id}

Visit @escrowprototype_bot to view details and accept the trade."""
            subject = f"LockBay Escrow Invitation - {escrow_id}"
            
        elif notification_type == "payment":
            message = f"""💰 **Payment Received - Action Required**

Payment has been received for your escrow transaction.

**Transaction ID:** {escrow_id}

Please visit @escrowprototype_bot to confirm receipt and release funds."""
            subject = f"LockBay Payment Received - {escrow_id}"
            
        elif notification_type == "update":
            message = f"""📋 **Escrow Update**

Your escrow transaction has been updated.

**Transaction ID:** {escrow_id}

Visit @escrowprototype_bot for the latest status."""
            subject = f"LockBay Escrow Update - {escrow_id}"
            
        else:
            message = f"""🔔 **LockBay Notification**

You have a new update for transaction {escrow_id}.

Visit @escrowprototype_bot for details."""
            subject = f"LockBay Notification - {escrow_id}"
        
        return self.route_seller_notification(
            seller_contact_type=seller_contact_type,
            seller_contact_value=seller_contact_value,
            message=message,
            subject=subject
        )


# Global instance for easy access
_notification_router = None

def get_notification_router() -> SellerNotificationRouter:
    """Get global notification router instance"""
    global _notification_router
    if _notification_router is None:
        _notification_router = SellerNotificationRouter()
    return _notification_router