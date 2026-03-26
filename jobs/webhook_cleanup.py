"""
Webhook Queue Cleanup Job
FIXES ISSUE #7: Scheduled cleanup of old webhook events
"""

import logging
from webhook_queue.webhook_inbox.postgres_async_queue import postgres_async_webhook_queue

logger = logging.getLogger(__name__)


async def cleanup_old_webhook_events():
    """
    Clean up old completed/failed webhook events.
    Runs every 24 hours to prevent database bloat.
    """
    try:
        logger.info("🧹 WEBHOOK_CLEANUP: Starting scheduled cleanup...")
        
        # Clean up events older than 7 days (168 hours)
        deleted_count = await postgres_async_webhook_queue.cleanup_old_events(retention_hours=168)
        
        if deleted_count > 0:
            logger.info(f"✅ WEBHOOK_CLEANUP: Cleaned up {deleted_count} old webhook events")
        else:
            logger.info("✅ WEBHOOK_CLEANUP: No old events to clean up")
            
    except Exception as e:
        logger.error(f"❌ WEBHOOK_CLEANUP: Cleanup failed - {e}")


def schedule_webhook_cleanup(scheduler):
    """
    Schedule webhook cleanup job to run daily.
    Call this from consolidated_scheduler.py
    """
    try:
        # Schedule daily cleanup at 3 AM UTC
        scheduler.add_job(
            cleanup_old_webhook_events,
            trigger='cron',
            hour=3,
            minute=0,
            id='webhook_queue_cleanup',
            name='🧹 Webhook Queue Cleanup - Remove Old Events',
            replace_existing=True,
            max_instances=1
        )
        logger.info("✅ Scheduled webhook queue cleanup job (daily at 3 AM UTC)")
    except Exception as e:
        logger.error(f"❌ Failed to schedule webhook cleanup job: {e}")
