"""
Admin Notification Queue Processor
Processes pending admin notifications from database queue every 2 minutes.
Prevents notification loss during rapid escrow state changes.
"""

import logging
from services.admin_notification_queue import AdminNotificationQueueService

logger = logging.getLogger(__name__)


async def run_admin_notification_processor():
    """
    Process pending admin notifications from database queue.
    Prevents notification loss during rapid escrow state changes.
    """
    try:
        stats = await AdminNotificationQueueService.process_pending_notifications(batch_size=20)
        
        if stats['processed'] > 0:
            logger.info(
                f"📧 Admin notification queue processed: {stats['processed']} notifications, "
                f"{stats['email_sent']} emails sent, {stats['telegram_sent']} telegrams sent, "
                f"{stats['failed']} failed"
            )
        
    except Exception as e:
        logger.error(f"Error processing admin notification queue: {e}", exc_info=True)
