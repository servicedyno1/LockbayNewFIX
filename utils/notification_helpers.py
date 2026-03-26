"""
Notification Helper Functions
Provides unified functions for sending Telegram messages and notifications
"""

import logging

logger = logging.getLogger(__name__)


async def send_telegram_message(chat_id: int, text: str, parse_mode: str = "Markdown") -> bool:
    """
    Send a Telegram message to a specific chat ID
    
    Args:
        chat_id: Telegram chat ID to send message to
        text: Message text to send
        parse_mode: Parse mode for the message (default: Markdown)
        
    Returns:
        bool: True if message sent successfully, False otherwise
    """
    try:
        # Get the bot instance from the application
        from main import get_application_instance
        application = get_application_instance()
        
        if not application or not application.bot:
            logger.error("Bot application not available for sending message")
            return False
            
        await application.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=parse_mode
        )
        
        logger.info(f"✅ Telegram message sent successfully to chat {chat_id}")
        return True
        
    except Exception as e:
        logger.error(f"❌ Failed to send Telegram message to chat {chat_id}: {e}")
        return False


async def send_admin_alert(message: str, title: str = "System Alert") -> bool:
    """
    Send an alert message to all administrators
    
    Args:
        message: Alert message content
        title: Alert title (default: System Alert)
        
    Returns:
        bool: True if alert sent successfully, False otherwise
    """
    try:
        from config import Config
        
        # Get admin IDs from config
        admin_ids = getattr(Config, 'ADMIN_IDS', [])
        if not admin_ids:
            logger.warning("No admin IDs configured for alerts")
            return False
            
        alert_text = f"🚨 **{title}**\n\n{message}"
        
        success_count = 0
        for admin_id in admin_ids:
            if await send_telegram_message(admin_id, alert_text):
                success_count += 1
                
        logger.info(f"✅ Admin alert sent to {success_count}/{len(admin_ids)} administrators")
        return success_count > 0
        
    except Exception as e:
        logger.error(f"❌ Failed to send admin alert: {e}")
        return False


async def send_user_notification(user_telegram_id: int, message: str, notification_type: str = "info") -> bool:
    """
    Send a notification to a specific user
    
    Args:
        user_telegram_id: User's Telegram ID
        message: Notification message
        notification_type: Type of notification (info, success, warning, error)
        
    Returns:
        bool: True if notification sent successfully, False otherwise
    """
    try:
        # Add emoji prefix based on notification type
        emoji_map = {
            "info": "ℹ️",
            "success": "✅", 
            "warning": "⚠️",
            "error": "❌"
        }
        
        emoji = emoji_map.get(notification_type, "📬")
        formatted_message = f"{emoji} {message}"
        
        return await send_telegram_message(user_telegram_id, formatted_message)
        
    except Exception as e:
        logger.error(f"❌ Failed to send user notification: {e}")
        return False