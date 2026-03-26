"""
User Cancellation Notification Service
Sends email notifications to users when their escrows are cancelled
"""

import logging
from typing import Dict, Any
from datetime import datetime
from services.email import EmailService
from config import Config

logger = logging.getLogger(__name__)


class UserCancellationNotificationService:
    """Service to send email notifications to users for escrow cancellations"""
    
    def __init__(self):
        self.email_service = EmailService()
        self.platform_name = getattr(Config, 'PLATFORM_NAME', 'LockBay')
    
    def _format_currency(self, amount: float, currency: str = 'USD') -> str:
        """
        Format currency amount with proper symbol and precision.
        
        Args:
            amount: Numerical amount to format
            currency: Currency code (NGN, USD, BTC, ETH, etc.)
            
        Returns:
            Formatted currency string with proper symbol and precision
        """
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
        
    async def notify_user_escrow_cancelled(self, user_email: str, escrow_data: Dict[str, Any]) -> bool:
        """
        Send email notification to user when their escrow is cancelled
        
        Args:
            user_email: The user's email address
            escrow_data: Dictionary containing escrow information
            
        Returns:
            bool: True if email sent successfully, False otherwise
        """
        try:
            if not user_email:
                logger.debug("No user email provided - skipping user cancellation notification")
                return False
                
            # Extract escrow information
            escrow_id = escrow_data.get('escrow_id', 'Unknown')
            amount = escrow_data.get('amount', 0)
            currency = escrow_data.get('currency', 'USD')
            seller_info = escrow_data.get('seller_info', 'Unknown Seller')
            escrow_data.get('cancellation_reason', 'Trade cancelled')
            cancelled_at = escrow_data.get('cancelled_at', datetime.utcnow())
            
            # Format using centralized helpers
            amount_display = self._format_currency(amount, currency)
            timestamp_display = self._format_timestamp(cancelled_at)
            escrow_display = f"#{escrow_id[-6:] if len(escrow_id) >= 6 else escrow_id}"
            
            subject = f"❌ Trade Cancelled: {amount_display} - {self.platform_name}"
            
            html_content = f"""
            <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif; max-width: 600px; width: 100%; margin: 0 auto; padding: 20px; background-color: #f8f9fa;">
                <!-- Summary Box at Top -->
                <div style="background: #f0f8ff; padding: 15px; border-radius: 8px; margin-bottom: 20px;">
                    <strong style="color: #333;">Trade ID:</strong> <span style="font-family: monospace;">{escrow_display}</span><br>
                    <strong style="color: #333;">Amount:</strong> {amount_display}<br>
                    <strong style="color: #333;">Cancelled:</strong> {timestamp_display}
                </div>
                
                <!-- Status Badge -->
                <div style="background: #6c757d; color: white; padding: 20px; text-align: center; border-radius: 12px 12px 0 0;">
                    <h1 style="margin: 0; font-size: 24px;">❌ Trade Cancelled</h1>
                    <p style="margin: 5px 0 0 0; opacity: 0.9; font-size: 14px;">{self.platform_name}</p>
                </div>
                
                <div style="background: white; padding: 25px; border-radius: 0 0 12px 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">
                    <h2 style="margin: 0 0 15px 0; color: #333;">Cancellation Details</h2>
                    
                    <table style="width: 100%; border-collapse: collapse; margin-bottom: 15px;">
                        <tr style="border-bottom: 1px solid #f0f0f0;">
                            <td style="padding: 10px 0; color: #666; font-size: 14px;">Amount</td>
                            <td style="padding: 10px 0; text-align: right; font-weight: bold; font-size: 16px;">{amount_display}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #f0f0f0;">
                            <td style="padding: 10px 0; color: #666; font-size: 14px;">Seller</td>
                            <td style="padding: 10px 0; text-align: right; font-size: 14px;">{seller_info}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #f0f0f0;">
                            <td style="padding: 10px 0; color: #666; font-size: 14px;">Status</td>
                            <td style="padding: 10px 0; text-align: right; font-size: 14px; color: #6c757d; font-weight: bold;">Cancelled</td>
                        </tr>
                    </table>
                    
                    <div style="margin-top: 20px; padding: 15px; background-color: #f8e8e8; border-left: 4px solid #dc3545; border-radius: 4px;">
                        <p style="margin: 0; font-weight: bold; color: #333;">⚠️ Status: Trade Cancelled</p>
                        <p style="margin: 5px 0 0 0; color: #666;">No funds have been processed for this trade. Your wallet was not affected.</p>
                    </div>
                    
                    <!-- What to Do Next -->
                    <div style="margin-top: 20px; padding: 15px; background: #fffbf0; border-left: 4px solid #ffc107; border-radius: 4px;">
                        <strong style="color: #333;">📋 What to Do Next:</strong>
                        <p style="margin: 10px 0 0 0; color: #666;">You can start a new trade anytime by creating a new escrow. Your account is ready for trading.</p>
                    </div>
                </div>
                
                <div style="text-align: center; margin-top: 20px; color: #999; font-size: 12px;">
                    <p style="margin: 5px 0;">Thank you for using {self.platform_name}</p>
                    <p style="margin: 5px 0;">This is an automated notification</p>
                </div>
            </div>
            """
            
            success = self.email_service.send_email(
                to_email=user_email,
                subject=subject,
                html_content=html_content
            )
            
            if success:
                logger.info(f"✅ User notified of escrow cancellation: {user_email} (Escrow: {escrow_id})")
            else:
                logger.error(f"❌ Failed to notify user of escrow cancellation: {user_email} (Escrow: {escrow_id})")
                
            return success
            
        except Exception as e:
            logger.error(f"Error sending user escrow cancellation notification: {e}")
            return False


# Singleton instance
user_cancellation_notifications = UserCancellationNotificationService()