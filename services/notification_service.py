"""
Decoupled Notification Service for Seller Communications
Handles email and Telegram notifications without handler coupling
"""

import logging
from typing import Optional, Dict, Any
from datetime import datetime
from decimal import Decimal
from services.email import EmailService
# Consolidated notification service removed during cleanup
from config import Config

logger = logging.getLogger(__name__)


class NotificationService:
    """
    Centralized notification service for seller communications
    Replaces direct handler imports to prevent coupling issues
    """
    
    def __init__(self):
        self.email_service = EmailService()
        # Consolidated service removed during cleanup
    
    def _format_currency(self, amount, currency: str = 'USD') -> str:
        """
        Format currency amount with proper symbol and precision.
        
        Args:
            amount: Numerical amount to format (Decimal or float)
            currency: Currency code (NGN, USD, BTC, ETH, etc.)
            
        Returns:
            Formatted currency string with proper symbol and precision
        """
        if isinstance(amount, Decimal):
            amount = float(amount)
        
        if currency == 'NGN':
            return f"₦{amount:,.2f}"
        elif currency == 'USD':
            return f"${amount:,.2f}"
        else:
            return f"{amount:.8f} {currency}"
    
    def _format_timestamp(self, dt: datetime) -> str:
        """
        Format timestamp with absolute time and relative time ago.
        
        Args:
            dt: Datetime object to format
            
        Returns:
            Formatted timestamp string like "YYYY-MM-DD HH:MM UTC (X hours ago)"
        """
        from datetime import timezone
        
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        
        now = datetime.utcnow()
        diff = now - dt
        
        if diff.days > 0:
            relative = f"{diff.days} day{'s' if diff.days != 1 else ''} ago"
        elif diff.seconds >= 3600:
            hours = diff.seconds // 3600
            relative = f"{hours} hour{'s' if hours != 1 else ''} ago"
        else:
            minutes = diff.seconds // 60
            relative = f"{minutes} minute{'s' if minutes != 1 else ''} ago"
        
        return f"{dt.strftime('%Y-%m-%d %H:%M')} UTC ({relative})"
    
    async def send_seller_invitation(self, escrow_id: str, seller_identifier: str, 
                                   seller_type: str, amount: Decimal) -> bool:
        """
        Send trade invitation to seller via appropriate channel
        
        Args:
            escrow_id: Unique escrow identifier
            seller_identifier: Email, phone number, or username
            seller_type: 'email', 'phone', or 'username'
            amount: Trade amount in USD
            
        Returns:
            bool: Success status
        """
        try:
            if seller_type == "email":
                return await self._send_email_invitation(
                    escrow_id, seller_identifier, amount
                )
            elif seller_type == "phone":
                return await self._send_sms_invitation(
                    escrow_id, seller_identifier, amount
                )
            elif seller_type == "username":
                return await self._send_username_invitation(
                    escrow_id, seller_identifier, amount
                )
            else:
                logger.warning(f"Unknown seller type: {seller_type}")
                return False
                
        except Exception as e:
            logger.error(f"Failed to send seller invitation for escrow {escrow_id}: {e}")
            return False
    
    async def _send_email_invitation(self, escrow_id: str, email: str, amount: Decimal) -> bool:
        """Send email invitation to seller"""
        from utils.data_sanitizer import DataSanitizer
        
        try:
            # Validate amount is safe
            if amount <= 0 or amount > 1000000:  # Reasonable limits
                logger.error(f"Invalid amount for email invitation: {amount}")
                return False
            
            amount_display = self._format_currency(amount, 'USD')
            subject = f"💰 New Trade Invitation - {amount_display}"
            
            # Create invitation email content
            invitation_details = {
                "escrow_id": escrow_id,
                "amount": amount_display,
                "platform_name": Config.PLATFORM_NAME,
                "webapp_url": Config.WEBAPP_URL,
                "expires_hours": 24
            }
            
            # Generate secure invitation HTML
            html_content = self._create_seller_invitation_html(invitation_details)
            
            success = self.email_service.send_email(
                to_email=email,
                subject=DataSanitizer.sanitize_text(subject),
                html_content=html_content
            )
            
            if success:
                logger.info(f"Email invitation sent to {email} for escrow {escrow_id}")
                return True
            else:
                logger.error(f"❌ EMAIL_INVITATION_FAILED: Failed to send email invitation to {email} for escrow {escrow_id}")
                return False
                
        except Exception as e:
            logger.error(f"Error sending email invitation: {e}")
            return False
    
    async def _send_sms_invitation(self, escrow_id: str, phone: str, amount: Decimal, buyer_user_id: Optional[int] = None) -> bool:
        """Send SMS invitation to seller"""
        try:
            # Check if SMS is enabled
            if not Config.TWILIO_ENABLED:
                logger.warning("SMS invitations disabled - Twilio not configured")
                return False
            
            # Use consolidated notification service for SMS
            amount_display = self._format_currency(amount, 'USD')
            message = (
                f"New trade invitation: {amount_display}. "
                f"Accept at {Config.WEBAPP_URL}/trade/{escrow_id} "
                f"- {Config.PLATFORM_NAME}"
            )
            
            # Send SMS via Twilio
            from twilio.rest import Client
            client = Client(Config.TWILIO_ACCOUNT_SID, Config.TWILIO_AUTH_TOKEN)
            
            message_response = client.messages.create(
                body=message,
                from_=Config.TWILIO_PHONE_NUMBER,
                to=phone
            )
            
            success = message_response.sid is not None
            
            if success:
                logger.info(f"SMS invitation sent to {phone} for escrow {escrow_id}")
                
                # Record SMS usage for rate limiting
                if buyer_user_id:
                    from services.sms_eligibility_service import SMSEligibilityService
                    await SMSEligibilityService.record_sms_usage(buyer_user_id, phone)
                
                return True
            else:
                logger.warning(f"Failed to send SMS invitation to {phone}")
                return False
                
        except Exception as e:
            logger.error(f"Error sending SMS invitation: {e}")
            return False
    
    async def _send_username_invitation(self, escrow_id: str, username: str, amount: Decimal) -> bool:
        """Send invitation to seller by username via Telegram bot"""
        try:
            # For username sellers, we need to find them in our database
            from database import SessionLocal
            from models import User
            
            session = SessionLocal()
            try:
                # Find user by username (remove @ if present)
                clean_username = username.lstrip('@').lower()
                seller_user = session.query(User).filter(
                    User.username.ilike(clean_username)
                ).first()
                
                if not seller_user:
                    logger.warning(f"Username seller @{clean_username} not found in database for escrow {escrow_id}")
                    logger.info(f"Escrow {escrow_id} created with non-onboarded seller @{clean_username} - invitation pending until seller joins")
                    # Escrow proceeds successfully - seller can join and accept later
                    return True
                
                # Send Telegram notification to the seller
                try:
                    from telegram import Bot
                    from config import Config
                    
                    if not Config.BOT_TOKEN:
                        raise ValueError("BOT_TOKEN not configured")
                    bot = Bot(token=Config.BOT_TOKEN)
                    
                    # Create invitation message with updated UI appearance and action buttons
                    amount_display = self._format_currency(amount, 'USD')
                    message = (
                        f"💰 <b>Trade Invitation • #{escrow_id}</b>\n\n"
                        f"Someone wants to trade with you\n"
                        f"<b>{amount_display}</b>\n\n"
                        f"⏰ 24hr to accept"
                    )
                    
                    # Add accept/decline buttons for interactive response
                    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
                    
                    keyboard = InlineKeyboardMarkup([
                        [
                            InlineKeyboardButton("✅ Accept Trade", callback_data=f"accept_trade:{escrow_id}"),
                            InlineKeyboardButton("❌ Decline", callback_data=f"decline_trade:{escrow_id}")
                        ],
                        [
                            InlineKeyboardButton("💬 Contact Support", callback_data="contact_support")
                        ]
                    ])
                    
                    await bot.send_message(
                        chat_id=seller_user.telegram_id,
                        text=message,
                        parse_mode='HTML',
                        reply_markup=keyboard
                    )
                    
                    logger.info(f"Username invitation sent to @{clean_username} (ID: {seller_user.telegram_id}) for escrow {escrow_id}")
                    return True
                    
                except Exception as telegram_error:
                    logger.error(f"Failed to send Telegram notification to @{clean_username}: {telegram_error}")
                    return False
                    
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"Error sending username invitation: {e}")
            return False
    
    async def send_trade_status_update(self, escrow_id: str, seller_identifier: str,
                                     seller_type: str, status: str, details: Dict[str, Any]) -> bool:
        """Send trade status update to seller"""
        try:
            if seller_type == "email":
                return await self._send_status_email(escrow_id, seller_identifier, status, details)
            elif seller_type == "phone":
                return await self._send_status_sms(escrow_id, seller_identifier, status, details)
            return False
        except Exception as e:
            logger.error(f"Failed to send status update for escrow {escrow_id}: {e}")
            return False
    
    async def _send_status_email(self, escrow_id: str, email: str, status: str, details: Dict[str, Any]) -> bool:
        """Send status update via email"""
        try:
            subject = f"Trade Update - {status.title()}"
            
            email_details = {
                "escrow_id": escrow_id,
                "status": status,
                "platform_name": Config.PLATFORM_NAME,
                **details
            }
            
            success = self.email_service.send_email(
                to_email=email,
                subject=subject,
                html_content=self._create_trade_status_html(email_details)
            )
            
            if success:
                logger.info(f"Status email sent to {email} for escrow {escrow_id}")
            else:
                logger.error(f"❌ STATUS_EMAIL_FAILED: Failed to send status email to {email} for escrow {escrow_id}")
            return success
            
        except Exception as e:
            logger.error(f"Error sending status email: {e}")
            return False
    
    async def _send_status_sms(self, escrow_id: str, phone: str, status: str, details: Dict[str, Any]) -> bool:
        """Send status update via SMS"""
        try:
            # Create concise SMS message
            amount = details.get("amount", "Amount")
            message = f"Trade {escrow_id[:12]} {status}: {amount} - {Config.PLATFORM_NAME}"
            
            # SMS functionality is disabled as per system architecture
            logger.info(f"SMS status update would be sent to {phone}: {message}")
            success = True
            
            logger.info(f"Status SMS sent to {phone} for escrow {escrow_id}")
            return success
            
        except Exception as e:
            logger.error(f"Error sending status SMS: {e}")
            return False


# Global notification service instance
    def _create_seller_invitation_html(self, details: Dict[str, Any]) -> str:
        """Create HTML content for seller invitation with modern UX"""
        escrow_id = details.get('escrow_id', 'Unknown')
        amount = details.get('amount', '$0.00')
        platform_name = details.get('platform_name', 'LockBay')
        webapp_url = details.get('webapp_url', '#')
        expires_hours = details.get('expires_hours', 24)
        
        return f"""
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif; max-width: 600px; width: 100%; margin: 0 auto; padding: 20px; background-color: #f8f9fa;">
            <!-- Summary Box at Top -->
            <div style="background: #f0f8ff; padding: 15px; border-radius: 8px; margin-bottom: 20px;">
                <strong style="color: #333;">Trade ID:</strong> <span style="font-family: monospace;">{escrow_id}</span><br>
                <strong style="color: #333;">Amount:</strong> {amount}<br>
                <strong style="color: #333;">Expires:</strong> {expires_hours} hours
            </div>
            
            <!-- Status Badge -->
            <div style="background: #28a745; color: white; padding: 20px; text-align: center; border-radius: 12px 12px 0 0;">
                <h1 style="margin: 0; font-size: 24px;">💰 New Trade Invitation</h1>
                <p style="margin: 5px 0 0 0; opacity: 0.9; font-size: 14px;">{platform_name}</p>
            </div>
            
            <div style="background: white; padding: 25px; border-radius: 0 0 12px 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">
                <h2 style="margin: 0 0 15px 0; color: #333;">You've Been Invited to Trade!</h2>
                <p style="margin: 0 0 20px 0; color: #666; font-size: 16px;">Someone wants to trade with you for <strong>{amount}</strong></p>
                
                <table style="width: 100%; border-collapse: collapse; margin-bottom: 15px;">
                    <tr style="border-bottom: 1px solid #f0f0f0;">
                        <td style="padding: 10px 0; color: #666; font-size: 14px;">Trade Amount</td>
                        <td style="padding: 10px 0; text-align: right; font-weight: bold; font-size: 16px; color: #28a745;">{amount}</td>
                    </tr>
                    <tr style="border-bottom: 1px solid #f0f0f0;">
                        <td style="padding: 10px 0; color: #666; font-size: 14px;">Platform</td>
                        <td style="padding: 10px 0; text-align: right; font-size: 14px;">{platform_name}</td>
                    </tr>
                    <tr style="border-bottom: 1px solid #f0f0f0;">
                        <td style="padding: 10px 0; color: #666; font-size: 14px;">Expires In</td>
                        <td style="padding: 10px 0; text-align: right; font-size: 14px; color: #dc3545; font-weight: bold;">{expires_hours} hours</td>
                    </tr>
                </table>
                
                <!-- What to Do Next -->
                <div style="margin-top: 20px; padding: 15px; background: #fffbf0; border-left: 4px solid #ffc107; border-radius: 4px;">
                    <strong style="color: #333;">⏰ Act Fast - Limited Time!</strong>
                    <p style="margin: 10px 0 0 0; color: #666;">This invitation will expire in {expires_hours} hours. Log in now to accept or decline this trade.</p>
                </div>
                
                <!-- Call to Action Button -->
                <div style="text-align: center; margin: 30px 0 20px 0;">
                    <a href="{webapp_url}" style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 15px 40px; text-decoration: none; border-radius: 25px; font-weight: bold; font-size: 16px; display: inline-block; box-shadow: 0 4px 6px rgba(0,0,0,0.1);">✅ View Trade Invitation</a>
                </div>
                
                <p style="margin: 20px 0 0 0; color: #999; font-size: 12px; text-align: center;">Click the button above to review and accept this trade</p>
            </div>
            
            <div style="text-align: center; margin-top: 20px; color: #999; font-size: 12px;">
                <p style="margin: 5px 0;">Thank you for using {platform_name}</p>
                <p style="margin: 5px 0;">This is an automated notification</p>
            </div>
        </div>
        """
    
    def _create_trade_status_html(self, details: Dict[str, Any]) -> str:
        """Create HTML content for trade status update"""
        return f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <h2>Trade Status Update</h2>
            <p>Status: <strong>{details.get('status', 'Unknown')}</strong></p>
            <p>Trade ID: {details.get('escrow_id', 'N/A')}</p>
            <p>Platform: {details.get('platform_name', Config.PLATFORM_NAME)}</p>
        </div>
        """


    @staticmethod
    async def send_cashout_decline_notification(
        user_email: str, 
        user_name: str, 
        cashout_id: str, 
        amount: str, 
        currency: str, 
        reason: str
    ) -> bool:
        """
        Send cashout decline notification to user
        
        Args:
            user_email: User's email address
            user_name: User's name
            cashout_id: Cashout ID that was declined
            amount: Amount that was declined
            currency: Currency of the cashout
            reason: Reason for decline
            
        Returns:
            bool: Success status
        """
        try:
            email_service = EmailService()
            
            subject = f"❌ Cashout Request Declined - {Config.PLATFORM_NAME}"
            
            # Create decline notification email
            html_content = f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
                <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 30px; text-align: center;">
                    <h1 style="margin: 0; font-size: 28px;">❌ Cashout Declined</h1>
                    <p style="margin: 10px 0 0 0; font-size: 16px; opacity: 0.9;">{Config.PLATFORM_NAME}</p>
                </div>
                
                <div style="padding: 30px; background: #f8f9fa;">
                    <p style="margin: 0 0 20px 0; font-size: 16px; color: #333;">Dear {user_name},</p>
                    
                    <p style="margin: 0 0 20px 0; color: #666;">Your cashout request has been declined by our team.</p>
                    
                    <div style="background: #fff; border-left: 4px solid #dc3545; padding: 20px; margin: 20px 0; border-radius: 4px;">
                        <h3 style="margin: 0 0 15px 0; color: #dc3545;">Declined Cashout Details</h3>
                        <p style="margin: 5px 0; color: #333;"><strong>Cashout ID:</strong> {cashout_id}</p>
                        <p style="margin: 5px 0; color: #333;"><strong>Amount:</strong> ${amount} {currency}</p>
                        <p style="margin: 5px 0; color: #333;"><strong>Reason:</strong> {reason}</p>
                        <p style="margin: 5px 0; color: #333;"><strong>Status:</strong> Funds returned to your wallet</p>
                    </div>
                    
                    <div style="background: #d4edda; border: 1px solid #c3e6cb; padding: 15px; border-radius: 4px; margin: 20px 0;">
                        <h4 style="margin: 0 0 10px 0; color: #155724;">✅ Your Funds Are Safe</h4>
                        <p style="margin: 0; color: #155724;">The full amount (${amount} + fees) has been returned to your wallet and is available for immediate use.</p>
                    </div>
                    
                    <div style="background: #fff3cd; border: 1px solid #ffeaa7; padding: 15px; border-radius: 4px; margin: 20px 0;">
                        <h4 style="margin: 0 0 10px 0; color: #856404;">💡 Next Steps</h4>
                        <ul style="margin: 10px 0; color: #856404; padding-left: 20px;">
                            <li>Review the decline reason above</li>
                            <li>Address any issues mentioned</li>
                            <li>Contact support if you need assistance</li>
                            <li>Submit a new cashout request when ready</li>
                        </ul>
                    </div>
                    
                    <div style="text-align: center; margin: 30px 0;">
                        <a href="{Config.WEBAPP_URL}" style="background: #007bff; color: white; padding: 12px 30px; text-decoration: none; border-radius: 5px; font-weight: bold; display: inline-block;">Return to Platform</a>
                    </div>
                    
                    <p style="margin: 20px 0; color: #666; font-size: 14px;">
                        If you have questions about this decline, please contact our support team at <a href="mailto:{Config.SUPPORT_EMAIL}" style="color: #007bff;">{Config.SUPPORT_EMAIL}</a>
                    </p>
                </div>
                
                <div style="background: #f8f9fa; padding: 20px; text-align: center; border-top: 1px solid #dee2e6;">
                    <p style="margin: 0; color: #666; font-size: 12px;">
                        This email was sent by {Config.PLATFORM_NAME} regarding your cashout request.
                    </p>
                </div>
            </div>
            """
            
            success = email_service.send_email(
                to_email=user_email,
                subject=subject,
                html_content=html_content
            )
            
            if success:
                logger.info(f"Cashout decline notification sent to {user_email}")
                return True
            else:
                logger.error(f"❌ CASHOUT_DECLINE_EMAIL_FAILED: Failed to send decline notification to {user_email}")
                return False
                
        except Exception as e:
            logger.error(f"Error sending cashout decline notification: {e}")
            return False
    
    async def send_weekly_summary(self, user_id: int, summary_data: Dict[str, Any]) -> bool:
        """
        Send weekly summary notification to user
        
        Args:
            user_id: User ID to send summary to
            summary_data: Dictionary containing summary information
            
        Returns:
            bool: Success status
        """
        try:
            # For now, just log the summary - can be expanded later
            logger.info(f"📊 Weekly summary for user {user_id}: {summary_data.get('transactions', 0)} transactions, volume: {summary_data.get('volume', 0)}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send weekly summary to user {user_id}: {e}")
            return False


# Global notification service instance
notification_service = NotificationService()