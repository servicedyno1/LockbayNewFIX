"""
Direct Email Notification Service - Simplified Architecture

Provides immediate email notifications for wallet credits and deposits.
Part of the architect-approved direct notification flow.
"""

import logging
from database import get_sync_db_session
from models import User
from services.email import EmailService
from sqlalchemy import select

logger = logging.getLogger(__name__)

# Initialize email service instance
email_service = EmailService()


def send_email_notification(user_id: int, subject: str, message: str) -> bool:
    """
    Send immediate email notification to user.
    
    Args:
        user_id: Database user ID
        subject: Email subject line
        message: Email message content
        
    Returns:
        True if sent successfully, False otherwise
    """
    try:
        # Get user email from database
        with get_sync_db_session() as session:
            user = session.execute(
                select(User).where(User.id == user_id)
            ).scalar_one_or_none()
            
            if not user or not user.email:
                logger.warning(f"⚠️ EMAIL_SKIP: user={user_id} has no email address")
                return False
        
        # Send email using existing EmailService
        success = email_service.send_email(
            to_email=user.email,
            subject=subject,
            text_content=message,
            html_content=_format_html_message(message)
        )
        
        if success:
            logger.info(f"✅ EMAIL_SENT: user={user_id}, email={user.email}")
        else:
            logger.error(f"❌ EMAIL_FAILED: user={user_id}, email={user.email}")
            
        return success
        
    except Exception as e:
        logger.error(f"❌ EMAIL_ERROR: user={user_id}, error={e}")
        return False


def _format_html_message(text_message: str) -> str:
    """
    Convert plain text message to simple HTML format.
    
    Args:
        text_message: Plain text message
        
    Returns:
        HTML formatted message
    """
    # Convert newlines to <br> tags and wrap in basic HTML
    html_message = text_message.replace('\n', '<br>')
    
    return f"""
    <html>
    <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
        <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
            <h2 style="color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 10px;">
                LockBay Notification
            </h2>
            <div style="background: #f8f9fa; padding: 15px; border-radius: 5px; margin: 20px 0;">
                {html_message}
            </div>
            <p style="color: #7f8c8d; font-size: 12px; margin-top: 30px;">
                This is an automated notification from LockBay. Please do not reply to this email.
            </p>
        </div>
    </body>
    </html>
    """


async def send_email_notification_async(user_id: int, subject: str, message: str) -> bool:
    """
    Async version of send_email_notification.
    
    Args:
        user_id: Database user ID
        subject: Email subject line
        message: Email message content
        
    Returns:
        True if sent successfully, False otherwise
    """
    # For now, just call the sync version
    # In the future, this could be enhanced with async database operations
    return send_email_notification(user_id, subject, message)