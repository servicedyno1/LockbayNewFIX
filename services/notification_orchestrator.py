"""
Notification Orchestrator - Fan-out with per-channel error capture
Handles notification dispatch for escrow events with proper error isolation
"""

import logging
from typing import Dict, Any, List, Optional
import asyncio
from datetime import datetime
from services.consolidated_notification_service import ConsolidatedNotificationService, NotificationRequest, NotificationCategory, NotificationPriority, NotificationChannel
from database import managed_session
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from models import NotificationActivity
import hashlib

logger = logging.getLogger(__name__)


class NotificationOrchestrator:
    """Clean separation of notification logic with per-channel error handling and deduplication"""
    
    def __init__(self):
        self.notification_service = ConsolidatedNotificationService()
    
    def _generate_notification_key(self, escrow_id: str, event_type: str, user_id: int) -> str:
        """Generate unique key for notification deduplication"""
        # Create a hash of escrow_id + event_type + user_id for unique notification identification
        key_data = f"{escrow_id}_{event_type}_{user_id}"
        return hashlib.sha256(key_data.encode()).hexdigest()[:16]  # Use first 16 chars for readability
    
    async def _atomic_claim_notification_slot(self, escrow_id: str, event_type: str, user_id: int) -> bool:
        """
        Atomically claim notification slot using INSERT...ON CONFLICT DO NOTHING pattern.
        FIX ISSUE 2: Add claim expiry - claims older than 5 minutes are considered expired and can be overwritten.
        """
        notification_key = self._generate_notification_key(escrow_id, event_type, user_id)
        CLAIM_EXPIRY_MINUTES = 5
        
        try:
            with managed_session() as session:
                # FIX ISSUE 2: Check for existing claims and their age
                existing_stmt = select(NotificationActivity).where(
                    NotificationActivity.activity_id == notification_key
                )
                existing_result = session.execute(existing_stmt)
                existing_claim = existing_result.scalar_one_or_none()
                
                if existing_claim:
                    # Check if claim has expired (older than 5 minutes)
                    claim_age_minutes = (datetime.utcnow() - existing_claim.created_at).total_seconds() / 60
                    
                    if existing_claim.delivery_status == "claimed" and claim_age_minutes > CLAIM_EXPIRY_MINUTES:
                        # Expired claim - update it to allow retry
                        logger.info(f"🔄 CLAIM_EXPIRED: {escrow_id} to user {user_id} - claim expired ({claim_age_minutes:.1f} min old), allowing retry (key: {notification_key})")
                        update_stmt = update(NotificationActivity).where(
                            NotificationActivity.activity_id == notification_key
                        ).values(
                            delivery_status="expired_claim",
                            engagement_level="retry_allowed",
                            delivered_at=datetime.utcnow()
                        )
                        session.execute(update_stmt)
                        session.commit()
                        # Now proceed to create new claim below
                    elif existing_claim.delivery_status in ["delivered", "failed"]:
                        # Already processed successfully or failed permanently - don't retry
                        logger.info(f"✅ NOTIFICATION_ALREADY_PROCESSED: {escrow_id} to user {user_id} - status: {existing_claim.delivery_status} (key: {notification_key})")
                        return False
                    else:
                        # Recent claim still active
                        logger.info(f"🚫 NOTIFICATION_CLAIM_BLOCKED: {escrow_id} to user {user_id} - claim active ({claim_age_minutes:.1f} min old) (key: {notification_key})")
                        return False
                
                # ATOMIC CLAIM: Try to insert notification record with "claimed" status
                # Uses PostgreSQL's INSERT...ON CONFLICT DO UPDATE for claim renewal
                insert_stmt = pg_insert(NotificationActivity).values(
                    activity_id=notification_key,
                    user_id=user_id,
                    notification_type=event_type,
                    channel_type="pending",
                    channel_value=f"escrow_{escrow_id}",
                    delivery_status="claimed",
                    engagement_level="pending",
                    priority_score=1.0,
                    device_type="system",
                    location_context=f"escrow_expiry_{escrow_id}"
                )
                
                # Update expired claims or insert new ones
                from sqlalchemy import and_ as sql_and
                upsert_stmt = insert_stmt.on_conflict_do_update(
                    index_elements=['activity_id'],
                    set_=dict(
                        delivery_status="claimed",
                        engagement_level="pending",
                        created_at=datetime.utcnow()
                    ),
                    where=sql_and(NotificationActivity.delivery_status == "expired_claim")
                )
                
                result = session.execute(upsert_stmt)
                session.commit()
                
                # Successfully claimed
                claimed = result.rowcount > 0
                
                if claimed:
                    logger.info(f"✅ NOTIFICATION_CLAIM_SUCCESS: {escrow_id} to user {user_id} (key: {notification_key})")
                else:
                    logger.info(f"🚫 NOTIFICATION_CLAIM_BLOCKED: {escrow_id} to user {user_id} - already claimed by another process (key: {notification_key})")
                
                return claimed
                
        except Exception as e:
            logger.error(f"❌ NOTIFICATION_CLAIM_ERROR: {escrow_id} to user {user_id}: {e}")
            # If claim fails, don't send notification to avoid duplicates
            return False
    
    async def _update_notification_status(self, escrow_id: str, event_type: str, user_id: int, success: bool, channels: List[str]):
        """Update the claimed notification record with final delivery status"""
        notification_key = self._generate_notification_key(escrow_id, event_type, user_id)
        
        try:
            with managed_session() as session:
                # Update the already-claimed record with final delivery status
                update_stmt = update(NotificationActivity).where(
                    NotificationActivity.activity_id == notification_key
                ).values(
                    channel_type=channels[0] if channels else "none",
                    delivery_status="delivered" if success else "failed",
                    engagement_level="sent" if success else "failed",
                    delivered_at=datetime.utcnow() if success else None
                )
                
                result = session.execute(update_stmt)
                session.commit()
                
                if result.rowcount > 0:
                    logger.info(f"📝 NOTIFICATION_STATUS_UPDATED: {escrow_id} to user {user_id} - success: {success} (key: {notification_key})")
                else:
                    logger.warning(f"⚠️ NOTIFICATION_STATUS_UPDATE_FAILED: {escrow_id} to user {user_id} - record not found (key: {notification_key})")
                
        except Exception as e:
            logger.error(f"❌ FAILED_TO_UPDATE_NOTIFICATION_STATUS: {escrow_id} to user {user_id}: {e}")
    
    async def notify_escrow_expirations(self, expired_escrows: List[Dict[str, Any]], refund_results: Dict[str, Any]) -> Dict[str, Any]:
        """
        Send notifications for expired escrows with isolated error handling
        """
        results = {
            "processed": 0,
            "successful_notifications": [],
            "failed_notifications": [],
            "errors": []
        }
        
        if not expired_escrows:
            logger.info("📧 NOTIFICATION_ORCHESTRATOR: No expired escrows to notify")
            return results
        
        logger.info(f"📧 NOTIFICATION_ORCHESTRATOR: Processing notifications for {len(expired_escrows)} expired escrows")
        
        # Process notifications in parallel with error isolation
        notification_tasks = []
        for escrow_data in expired_escrows:
            task = self._notify_escrow_expiration(escrow_data, refund_results)
            notification_tasks.append(task)
        
        # Execute all notifications concurrently
        notification_results = await asyncio.gather(*notification_tasks, return_exceptions=True)
        
        # Process results
        for i, result in enumerate(notification_results):
            escrow_data = expired_escrows[i]
            escrow_id = escrow_data["escrow_id"]
            
            if isinstance(result, Exception):
                logger.error(f"❌ NOTIFICATION_ERROR: {escrow_id}: {result}")
                results["failed_notifications"].append({
                    "escrow_id": escrow_id,
                    "error": str(result)
                })
                results["errors"].append(f"Notification {escrow_id}: {str(result)}")
            else:
                # Ensure result is a dict before calling .get()
                result_dict = result if isinstance(result, dict) else {}
                results["successful_notifications"].append({
                    "escrow_id": escrow_id,
                    "channels": result_dict.get("channels", [])
                })
                results["processed"] += 1
        
        logger.info(f"✅ NOTIFICATION_ORCHESTRATOR_COMPLETE: Sent {results['processed']} notifications")
        
        return results
    
    async def _notify_escrow_expiration(self, escrow_data: Dict[str, Any], refund_results: Dict[str, Any]) -> Dict[str, Any]:
        """Send notification for a single expired escrow with deduplication"""
        escrow_id = escrow_data["escrow_id"]
        buyer_id = escrow_data["buyer_id"]
        seller_id = escrow_data["seller_id"]
        escrow_data["amount"]
        escrow_data["currency"]
        
        # VALIDATION: Check if user IDs are valid before attempting to claim notification slots
        buyer_claimed = False
        seller_claimed = False
        
        if buyer_id is not None:
            # ATOMIC CLAIM: Attempt to claim notification slot for buyer
            buyer_claimed = await self._atomic_claim_notification_slot(escrow_id, "escrow_expired", buyer_id)
        else:
            logger.warning(f"⚠️ NULL_USER_ID_SKIP: {escrow_id} - buyer_id is None, skipping buyer notification to prevent constraint violation")
        
        if seller_id is not None:
            # ATOMIC CLAIM: Attempt to claim notification slot for seller
            seller_claimed = await self._atomic_claim_notification_slot(escrow_id, "escrow_expired", seller_id)
        else:
            logger.warning(f"⚠️ NULL_USER_ID_SKIP: {escrow_id} - seller_id is None, skipping seller notification to prevent constraint violation")
        
        if not buyer_claimed and not seller_claimed:
            logger.info(f"🚫 NOTIFICATION_SKIP_ALL: {escrow_id} - both buyer and seller slots already claimed by other processes")
            return {"channels": [], "deduplication_skipped": True}
        elif not buyer_claimed:
            logger.info(f"🚫 NOTIFICATION_SKIP_BUYER: {escrow_id} - buyer slot already claimed")
        elif not seller_claimed:
            logger.info(f"🚫 NOTIFICATION_SKIP_SELLER: {escrow_id} - seller slot already claimed")
        
        # Find refund information
        refund_info = None
        successful_refunds = refund_results.get("successful_refunds", [])
        logger.info(f"🔍 DEBUG: Looking for refund info for escrow {escrow_id} in {len(successful_refunds)} successful refunds")
        for refund in successful_refunds:
            logger.info(f"🔍 DEBUG: Checking refund {refund.get('escrow_id')} == {escrow_id}")
            if refund["escrow_id"] == escrow_id:
                refund_info = refund
                logger.info(f"✅ DEBUG: Found refund info for escrow {escrow_id}: ${refund_info.get('amount')} {refund_info.get('currency')}")
                break
        
        if not refund_info:
            logger.info(f"ℹ️ REFUND_INFO: No refund processed for escrow {escrow_id} - this is normal for unfunded or non-refundable escrows")
        
        notification_results = {"channels": []}
        
        try:
            # ARCHITECT'S SOLUTION: Handle per-channel failures as soft outcomes
            overall_success = False
            channel_successes = []
            channel_failures = []
            
            # Notify buyer about expiration and refund (only if successfully claimed)
            if buyer_claimed:
                buyer_message = self._format_buyer_message(escrow_data, refund_info)
                buyer_request = NotificationRequest(
                    user_id=buyer_id,
                    category=NotificationCategory.ESCROW_UPDATES,
                    priority=NotificationPriority.HIGH,
                    title="Escrow Expired",
                    message=buyer_message,
                    channels=[NotificationChannel.TELEGRAM, NotificationChannel.EMAIL]
                )
                buyer_result = await self.notification_service.send_notification(buyer_request)
            else:
                buyer_result = {"deduplication_skipped": {"status": "blocked_by_preferences"}}
            
            # CRITICAL FIX: Properly analyze channel results from Dict[str, DeliveryResult] format
            # buyer_result is a dict mapping channel names to DeliveryResult objects
            buyer_success_channels = []
            if buyer_claimed:
                for channel_name, delivery_result in buyer_result.items():
                    prefixed_channel = f"buyer_{channel_name}"
                    
                    # Handle both object and dict format for delivery_result
                    if hasattr(delivery_result, 'status'):
                        status = delivery_result.status.value if hasattr(delivery_result.status, 'value') else str(delivery_result.status)
                        error_msg = delivery_result.error if hasattr(delivery_result, 'error') else "Unknown error"
                    elif isinstance(delivery_result, dict):
                        status = delivery_result.get('status', 'unknown')
                        error_msg = delivery_result.get('error', 'Unknown error')
                    else:
                        status = str(delivery_result)
                        error_msg = "Unknown error"
                    
                    if status in ["sent", "delivered"]:
                        channel_successes.append(prefixed_channel)
                        buyer_success_channels.append(channel_name)
                        notification_results["channels"].append(prefixed_channel)
                        overall_success = True  # At least one channel succeeded
                    elif status == "blocked_by_preferences":
                        # SOFT OUTCOME: User preference blocked, not a failure
                        logger.info(f"📧 SOFT_OUTCOME: {escrow_id} buyer notification blocked by preferences for {prefixed_channel}")
                    elif status in ["failed", "retrying"]:
                        # HARD FAILURE: Actual delivery failure
                        channel_failures.append({
                            "channel": prefixed_channel,
                            "error": error_msg,
                            "status": status
                        })
                
                # Update buyer notification status after successful send
                if buyer_success_channels:
                    await self._update_notification_status(escrow_id, "escrow_expired", buyer_id, True, buyer_success_channels)
                elif buyer_claimed:
                    # If claimed but no successful channels, mark as failed
                    await self._update_notification_status(escrow_id, "escrow_expired", buyer_id, False, [])
            else:
                # Handle deduplication skip case - treat as soft success for counting
                logger.info(f"📊 CHANNEL_COUNT: {escrow_id} buyer notification skipped due to slot already claimed")
            
            # Notify seller about expiration (only if successfully claimed)
            if seller_claimed:
                seller_message = self._format_seller_message(escrow_data)
                seller_request = NotificationRequest(
                    user_id=seller_id,
                    category=NotificationCategory.ESCROW_UPDATES,
                    priority=NotificationPriority.HIGH,
                    title="Escrow Expired",
                    message=seller_message,
                    channels=[NotificationChannel.TELEGRAM, NotificationChannel.EMAIL]
                )
                seller_result = await self.notification_service.send_notification(seller_request)
            else:
                seller_result = {"deduplication_skipped": {"status": "blocked_by_preferences"}}
            
            # CRITICAL FIX: Properly analyze channel results from Dict[str, DeliveryResult] format
            # seller_result is a dict mapping channel names to DeliveryResult objects  
            seller_success_channels = []
            if seller_claimed:
                for channel_name, delivery_result in seller_result.items():
                    prefixed_channel = f"seller_{channel_name}"
                    
                    # Handle both object and dict format for delivery_result
                    if hasattr(delivery_result, 'status'):
                        status = delivery_result.status.value if hasattr(delivery_result.status, 'value') else str(delivery_result.status)
                        error_msg = delivery_result.error if hasattr(delivery_result, 'error') else "Unknown error"
                    elif isinstance(delivery_result, dict):
                        status = delivery_result.get('status', 'unknown')
                        error_msg = delivery_result.get('error', 'Unknown error')
                    else:
                        status = str(delivery_result)
                        error_msg = "Unknown error"
                    
                    if status in ["sent", "delivered"]:
                        channel_successes.append(prefixed_channel)
                        seller_success_channels.append(channel_name)
                        notification_results["channels"].append(prefixed_channel)
                        overall_success = True  # At least one channel succeeded
                    elif status == "blocked_by_preferences":
                        # SOFT OUTCOME: User preference blocked, not a failure
                        logger.info(f"📧 SOFT_OUTCOME: {escrow_id} seller notification blocked by preferences for {prefixed_channel}")
                    elif status in ["failed", "retrying"]:
                        # HARD FAILURE: Actual delivery failure
                        channel_failures.append({
                            "channel": prefixed_channel,
                            "error": error_msg,
                            "status": status
                        })
                
                # Update seller notification status after successful send
                if seller_success_channels:
                    await self._update_notification_status(escrow_id, "escrow_expired", seller_id, True, seller_success_channels)
                elif seller_claimed:
                    # If claimed but no successful channels, mark as failed
                    await self._update_notification_status(escrow_id, "escrow_expired", seller_id, False, [])
            else:
                # Handle deduplication skip case - treat as soft success for counting
                logger.info(f"📊 CHANNEL_COUNT: {escrow_id} seller notification skipped due to slot already claimed")
            
            # Send admin alert for monitoring
            admin_message = self._format_admin_message(escrow_data, refund_info)
            admin_request = NotificationRequest(
                user_id=0,  # Use 0 for admin notifications
                category=NotificationCategory.ADMIN_ALERTS,
                priority=NotificationPriority.NORMAL,
                title="Escrow Expired - Admin Alert",
                message=admin_message,
                channels=[NotificationChannel.ADMIN_ALERT],
                admin_notification=True
            )
            admin_result = await self.notification_service.send_notification(admin_request)
            
            # Admin notifications are always considered successful if they don't throw
            if admin_result.get("success", True):
                notification_results["channels"].append("admin_alert")
                overall_success = True
            
            # ARCHITECT'S SOLUTION: Proper success/failure reporting
            if overall_success:
                logger.info(
                    f"✅ NOTIFICATION_SUCCESS: {escrow_id} - delivered to {len(channel_successes)} channels "
                    f"({len(channel_failures)} hard failures, soft outcomes treated appropriately)"
                )
            else:
                # ONLY log as failure if ALL channels had hard failures
                if channel_failures and not channel_successes:
                    logger.error(
                        f"❌ NOTIFICATION_TOTAL_FAILURE: {escrow_id} - ALL channels failed: {channel_failures}"
                    )
                    # Mark this as a real failure for the orchestrator
                    notification_results["total_failure"] = True
                    notification_results["failure_details"] = channel_failures
                else:
                    logger.info(
                        f"📧 NOTIFICATION_PARTIAL: {escrow_id} - some channels successful, treating as success"
                    )
            
        except Exception as e:
            logger.error(f"❌ NOTIFICATION_SEND_ERROR: {escrow_id}: {e}")
            raise e
        
        return notification_results
    
    def _format_buyer_message(self, escrow_data: Dict[str, Any], refund_info: Optional[Dict[str, Any]]) -> str:
        """Format notification message for buyer - optimized for mobile"""
        escrow_id = escrow_data["escrow_id"]
        amount = escrow_data["amount"]
        currency = escrow_data["currency"]
        payment_confirmed_at = escrow_data.get("payment_confirmed_at")
        
        message = f"Escrow {escrow_id} has expired.\n"
        message += f"Amount: ${amount} {currency}\n"
        
        if refund_info:
            # Successful refund processed
            message += f"✅ Refund: ${refund_info['amount']} {refund_info['currency']} credited to wallet\n"
            message += f"Transaction: {refund_info['transaction_id']}\n"
        elif payment_confirmed_at:
            # Payment was made but refund failed - this is an error
            message += "⚠️ Refund failed - contact support\n"
        else:
            # No payment was made - this is normal behavior
            message += "No payment made - no refund needed.\n"
        
        message += "\nCreate a new trade anytime."
        
        return message
    
    def _format_seller_message(self, escrow_data: Dict[str, Any]) -> str:
        """Format notification message for seller"""
        escrow_id = escrow_data["escrow_id"]
        amount = escrow_data["amount"]
        currency = escrow_data["currency"]
        payment_confirmed_at = escrow_data.get("payment_confirmed_at")
        
        message = f"⏰ Escrow {escrow_id} has expired\n\n"
        message += f"Amount: ${amount} {currency}\n"
        
        # Differentiate between seller failure to accept vs buyer failure to pay
        if payment_confirmed_at:
            # Seller failed to accept in time
            message += "⚠️ You didn't accept the trade in time.\n"
            message += "The buyer has been refunded."
        else:
            # Buyer never paid
            message += "The buyer didn't pay before the deadline."
        
        return message
    
    def _format_admin_message(self, escrow_data: Dict[str, Any], refund_info: Optional[Dict[str, Any]]) -> str:
        """Format notification message for admin monitoring"""
        escrow_id = escrow_data["escrow_id"]
        amount = escrow_data["amount"]
        currency = escrow_data["currency"]
        buyer_id = escrow_data["buyer_id"]
        seller_id = escrow_data["seller_id"]
        
        message = f"🔔 ESCROW EXPIRED: {escrow_id}\n"
        message += f"Amount: ${amount} {currency}\n"
        message += f"Buyer: {buyer_id}, Seller: {seller_id}\n"
        
        if refund_info:
            message += f"✅ Refund: ${refund_info['amount']} {refund_info['currency']} (ID: {refund_info['transaction_id']})\n"
        else:
            message += "❌ Refund failed - manual intervention required\n"
        
        return message