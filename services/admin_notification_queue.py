"""
Admin Notification Queue Service
Database-backed queue for reliable admin email/Telegram notifications
"""

import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from database import SessionLocal
from models import AdminNotification
from services.email import EmailService
from services.admin_trade_notifications import AdminTradeNotificationService
from config import Config

logger = logging.getLogger(__name__)


class AdminNotificationQueueService:
    """
    Manages database-backed queue for admin notifications.
    Ensures notifications are never lost due to rapid state changes or system failures.
    """
    
    @classmethod
    def enqueue_notification(
        cls,
        notification_type: str,
        category: str,
        subject: str,
        html_content: Optional[str] = None,
        telegram_message: Optional[str] = None,
        entity_type: Optional[str] = None,
        entity_id: Optional[str] = None,
        notification_data: Optional[Dict[str, Any]] = None,
        priority: int = 2,
        send_email: bool = True,
        send_telegram: bool = True,
        idempotency_key: Optional[str] = None,
        session: Optional[Session] = None
    ) -> bool:
        """
        Add admin notification to database queue.
        
        TRANSACTIONAL SAFETY: Respects caller's session management.
        - If session is provided: Only adds notification, does NOT commit/rollback/close
        - If session is None: Creates own session and manages it (commit/rollback/close)
        
        Args:
            notification_type: Type of notification (e.g., 'escrow_created')
            category: Category (trade, cashout, dispute, system)
            subject: Email subject line
            html_content: HTML email content (optional)
            telegram_message: Telegram message text (optional)
            entity_type: Related entity type (e.g., 'escrow')
            entity_id: Related entity ID (e.g., 'ES110125TQD2')
            notification_data: Full context data (JSON)
            priority: 1=critical, 2=high, 3=normal, 4=low
            send_email: Whether to send email
            send_telegram: Whether to send Telegram
            idempotency_key: Unique key to prevent duplicates
            session: Existing database session (optional)
            
        Returns:
            bool: True if enqueued successfully
        """
        should_manage_session = False
        if session is None:
            session = SessionLocal()
            should_manage_session = True
            
        try:
            # Check for duplicate using idempotency key
            if idempotency_key:
                existing = session.query(AdminNotification).filter(
                    AdminNotification.idempotency_key == idempotency_key
                ).first()
                
                if existing:
                    logger.info(f"📧 Duplicate admin notification prevented: {idempotency_key} (existing: {existing.status})")
                    return True  # Already queued, consider it success
            
            # Create notification record
            notification = AdminNotification(
                notification_type=notification_type,
                category=category,
                priority=priority,
                send_email=send_email,
                send_telegram=send_telegram,
                subject=subject,
                html_content=html_content,
                telegram_message=telegram_message,
                entity_type=entity_type,
                entity_id=entity_id,
                notification_data=notification_data,
                status='pending',
                email_sent=False,
                telegram_sent=False,
                idempotency_key=idempotency_key,
                created_at=datetime.now(timezone.utc)
            )
            
            session.add(notification)
            
            # Only commit if we created the session
            if should_manage_session:
                session.commit()
            else:
                # Flush to get the notification ID but don't commit
                session.flush()
            
            logger.info(
                f"✅ Admin notification queued: {notification_type} for {entity_type}:{entity_id} "
                f"(ID: {notification.id}, priority: {priority})"
            )
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to enqueue admin notification: {e}", exc_info=True)
            # Only rollback if we created the session
            if should_manage_session:
                session.rollback()
            # Propagate exception so caller can handle their transaction
            raise
            
        finally:
            # Only close if we created the session
            if should_manage_session:
                session.close()
    
    @classmethod
    async def enqueue_and_send_immediately(
        cls,
        notification_type: str,
        category: str,
        subject: str,
        html_content: Optional[str] = None,
        telegram_message: Optional[str] = None,
        entity_type: Optional[str] = None,
        entity_id: Optional[str] = None,
        notification_data: Optional[Dict[str, Any]] = None,
        priority: int = 2,
        send_email: bool = True,
        send_telegram: bool = True,
        idempotency_key: Optional[str] = None
    ) -> bool:
        """
        IMMEDIATE SEND: Queue notification to database AND send email + Telegram immediately.
        If send fails, leaves as pending for background processor retry.
        
        This ensures admin notifications are delivered instantly while maintaining
        retry capability for reliability.
        
        NOTE: This is an async method - callers must await it.
        """
        session = SessionLocal()
        try:
            # First, enqueue to database (for reliability)
            cls.enqueue_notification(
                notification_type=notification_type,
                category=category,
                subject=subject,
                html_content=html_content,
                telegram_message=telegram_message,
                entity_type=entity_type,
                entity_id=entity_id,
                notification_data=notification_data,
                priority=priority,
                send_email=send_email,
                send_telegram=send_telegram,
                idempotency_key=idempotency_key,
                session=session
            )
            
            session.commit()
            
            # Get the notification we just created
            notification = session.query(AdminNotification).filter(
                AdminNotification.idempotency_key == idempotency_key
            ).first()
            
            if not notification:
                logger.error(f"❌ Could not find queued notification: {idempotency_key}")
                return False
            
            # Now send immediately
            email_sent = False
            telegram_sent = False
            
            # Send email immediately if requested
            if send_email and html_content and Config.ADMIN_EMAIL:
                try:
                    from services.email import EmailService
                    email_service = EmailService()
                    email_sent = email_service.send_email(
                        to_email=Config.ADMIN_EMAIL,
                        subject=subject,
                        html_content=html_content
                    )
                    
                    if email_sent:
                        notification.email_sent = True
                        logger.info(f"✅ IMMEDIATE: Admin email sent for {notification_type} ({entity_id})")
                    else:
                        logger.warning("⚠️ IMMEDIATE: Email send failed, will retry via background processor")
                except Exception as e:
                    logger.error(f"❌ IMMEDIATE: Email send error: {e}")
            
            # Send Telegram immediately if requested
            if send_telegram and telegram_message and Config.NOTIFICATION_GROUP_ID and Config.BOT_TOKEN:
                try:
                    from telegram import Bot
                    bot = Bot(Config.BOT_TOKEN)
                    
                    # ASYNC: Send Telegram message using await (respects existing event loop)
                    try:
                        await bot.send_message(
                            chat_id=Config.NOTIFICATION_GROUP_ID,
                            text=telegram_message,
                            parse_mode='HTML'
                        )
                        telegram_sent = True
                    except Exception as telegram_error:
                        logger.error(f"Telegram send error: {telegram_error}")
                        telegram_sent = False
                    
                    if telegram_sent:
                        notification.telegram_sent = True
                        logger.info(f"✅ IMMEDIATE: Admin Telegram sent for {notification_type} ({entity_id})")
                    else:
                        logger.warning("⚠️ IMMEDIATE: Telegram send failed, will retry via background processor")
                except Exception as e:
                    logger.error(f"❌ IMMEDIATE: Telegram send error: {e}")
            
            # Update notification status
            if email_sent or telegram_sent:
                # Mark as completed if all requested channels succeeded
                if (not send_email or email_sent) and (not send_telegram or telegram_sent):
                    notification.status = 'completed'
                session.commit()
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to send immediate admin notification: {e}", exc_info=True)
            session.rollback()
            return False
        finally:
            session.close()
    
    @classmethod
    async def process_pending_notifications(cls, batch_size: int = 20) -> Dict[str, int]:
        """
        Process pending admin notifications from the queue.
        
        Args:
            batch_size: Maximum number of notifications to process in one batch
            
        Returns:
            Dict with processing statistics
        """
        stats = {
            'processed': 0,
            'email_sent': 0,
            'telegram_sent': 0,
            'failed': 0,
            'skipped': 0
        }
        
        session = SessionLocal()
        try:
            # Get pending notifications ordered by priority and creation time
            pending = session.query(AdminNotification).filter(
                or_(
                    AdminNotification.status == 'pending',
                    and_(
                        AdminNotification.status == 'failed',
                        AdminNotification.retry_count < AdminNotification.max_retries,
                        or_(
                            AdminNotification.next_retry_at.is_(None),
                            AdminNotification.next_retry_at <= datetime.now(timezone.utc)
                        )
                    )
                )
            ).order_by(
                AdminNotification.priority.asc(),
                AdminNotification.created_at.asc()
            ).limit(batch_size).all()
            
            if not pending:
                return stats
            
            logger.info(f"🔄 Processing {len(pending)} pending admin notifications...")
            
            email_service = EmailService()
            admin_trade_service = AdminTradeNotificationService()
            
            for notification in pending:
                try:
                    stats['processed'] += 1
                    email_success = True
                    telegram_success = True
                    
                    # Send email if requested
                    if notification.send_email and not notification.email_sent:
                        if Config.ADMIN_EMAIL and notification.html_content:
                            email_success = email_service.send_email(
                                to_email=Config.ADMIN_EMAIL,
                                subject=notification.subject,
                                html_content=notification.html_content
                            )
                            
                            if email_success:
                                notification.email_sent = True
                                stats['email_sent'] += 1
                                logger.info(f"✅ Admin email sent: {notification.notification_type} (ID: {notification.id})")
                            else:
                                logger.error(f"❌ Failed to send admin email: {notification.notification_type} (ID: {notification.id})")
                        else:
                            logger.warning(f"⚠️ Skipping email: no admin email or content (ID: {notification.id})")
                            notification.email_sent = True  # Mark as sent to avoid retrying
                    
                    # Send Telegram if requested
                    if notification.send_telegram and not notification.telegram_sent:
                        if notification.telegram_message and notification.notification_data:
                            try:
                                await admin_trade_service.send_group_notification_escrow_created(
                                    notification.notification_data
                                )
                                notification.telegram_sent = True
                                stats['telegram_sent'] += 1
                                logger.info(f"✅ Admin Telegram sent: {notification.notification_type} (ID: {notification.id})")
                            except Exception as telegram_error:
                                logger.error(f"❌ Failed to send admin Telegram: {telegram_error}")
                                telegram_success = False
                        else:
                            logger.warning(f"⚠️ Skipping Telegram: no message or data (ID: {notification.id})")
                            notification.telegram_sent = True  # Mark as sent to avoid retrying
                    
                    # Update notification status
                    if (not notification.send_email or notification.email_sent) and \
                       (not notification.send_telegram or notification.telegram_sent):
                        notification.status = 'sent'
                        notification.sent_at = datetime.now(timezone.utc)
                        logger.info(f"✅ Admin notification completed: {notification.notification_type} (ID: {notification.id})")
                    else:
                        # Partial failure - retry later
                        notification.retry_count += 1
                        notification.status = 'failed'
                        notification.next_retry_at = datetime.now(timezone.utc) + timedelta(minutes=5 * notification.retry_count)
                        notification.last_error = f"Email: {email_success}, Telegram: {telegram_success}"
                        stats['failed'] += 1
                        logger.warning(
                            f"⚠️ Partial failure for notification {notification.id}, retry {notification.retry_count}/{notification.max_retries}"
                        )
                    
                    session.commit()
                    
                except Exception as notification_error:
                    logger.error(f"❌ Error processing notification {notification.id}: {notification_error}", exc_info=True)
                    notification.retry_count += 1
                    notification.status = 'failed'
                    notification.next_retry_at = datetime.now(timezone.utc) + timedelta(minutes=5 * notification.retry_count)
                    notification.last_error = str(notification_error)[:500]
                    stats['failed'] += 1
                    session.commit()
            
            logger.info(
                f"✅ Notification batch complete: {stats['processed']} processed, "
                f"{stats['email_sent']} emails sent, {stats['telegram_sent']} telegrams sent, "
                f"{stats['failed']} failed"
            )
            return stats
            
        except Exception as e:
            logger.error(f"❌ Error processing notification queue: {e}", exc_info=True)
            return stats
        finally:
            session.close()
    
    @classmethod
    def get_queue_stats(cls) -> Dict[str, int]:
        """Get statistics about the notification queue."""
        session = SessionLocal()
        try:
            stats = {
                'pending': session.query(AdminNotification).filter(AdminNotification.status == 'pending').count(),
                'sent': session.query(AdminNotification).filter(AdminNotification.status == 'sent').count(),
                'failed': session.query(AdminNotification).filter(AdminNotification.status == 'failed').count(),
                'total': session.query(AdminNotification).count()
            }
            return stats
        finally:
            session.close()


# Global instance
admin_notification_queue = AdminNotificationQueueService()
