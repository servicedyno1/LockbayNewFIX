"""
Standalone Auto-Release Service for LockBay Telegram Bot

This service handles:
1. Delivery deadline warnings (24h, 8h, 2h, 30min before deadline)
2. Auto-release processing (after delivery deadline + 24h grace period)

Can be run independently or called from the main application.
"""

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal

# Database imports
from models import Escrow, EscrowStatus
from utils.atomic_transactions import atomic_transaction
from utils.atomic_transactions import locked_escrow_operation

# Services imports
from services.crypto import CryptoServiceAtomic
from services.consolidated_notification_service import (
    consolidated_notification_service,
    NotificationRequest,
    NotificationCategory,
    NotificationPriority
)

logger = logging.getLogger(__name__)

class StandaloneAutoReleaseService:
    """Standalone service for auto-release and delivery deadline warnings"""
    
    def __init__(self):
        self.name = "StandaloneAutoReleaseService"
        
    async def send_delivery_deadline_warnings(self) -> int:
        """Send warnings before delivery deadlines expire"""
        warnings_sent = 0
        
        try:
            with atomic_transaction() as session:
                current_time = datetime.now(timezone.utc)
                
                # Find active escrows approaching their delivery deadline
                # Use SELECT FOR UPDATE to prevent concurrent processing of same escrow
                active_escrows = (
                    session.query(Escrow)
                    .filter(
                        Escrow.status == str(EscrowStatus.ACTIVE.value),
                        Escrow.delivery_deadline.isnot(None),
                        Escrow.delivery_deadline > current_time,  # Not expired yet
                    )
                    .with_for_update()  # Lock rows to prevent race conditions
                    .all()
                )
                
                for escrow in active_escrows:
                    try:
                        # Calculate time remaining until delivery deadline
                        time_remaining = escrow.delivery_deadline - current_time
                        hours_remaining = time_remaining.total_seconds() / 3600
                        
                        # Send warning messages at specific intervals (with duplicate prevention)
                        should_send_warning = False
                        warning_type = ""
                        warning_flag_attr = None
                        
                        # 24 hours warning (for longer deliveries)
                        if 23.5 <= hours_remaining <= 24.5:
                            if not escrow.warning_24h_sent:
                                should_send_warning = True
                                warning_type = "24 hours"
                                warning_flag_attr = "warning_24h_sent"
                            else:
                                logger.debug(f"Skipping 24h warning for {escrow.escrow_id} - already sent")
                        # 8 hours warning  
                        elif 7.5 <= hours_remaining <= 8.5:
                            if not escrow.warning_8h_sent:
                                should_send_warning = True
                                warning_type = "8 hours"
                                warning_flag_attr = "warning_8h_sent"
                            else:
                                logger.debug(f"Skipping 8h warning for {escrow.escrow_id} - already sent")
                        # 2 hours warning
                        elif 1.5 <= hours_remaining <= 2.5:
                            if not escrow.warning_2h_sent:
                                should_send_warning = True
                                warning_type = "2 hours"
                                warning_flag_attr = "warning_2h_sent"
                            else:
                                logger.debug(f"Skipping 2h warning for {escrow.escrow_id} - already sent")
                        # 30 minutes warning
                        elif 0.25 <= hours_remaining <= 0.75:
                            if not escrow.warning_30m_sent:
                                should_send_warning = True
                                warning_type = "30 minutes"
                                warning_flag_attr = "warning_30m_sent"
                            else:
                                logger.debug(f"Skipping 30m warning for {escrow.escrow_id} - already sent")
                            
                        if should_send_warning and warning_flag_attr:
                            # Extract values for type safety
                            buyer_id = int(escrow.buyer_id)  # type: ignore
                            seller_id = int(escrow.seller_id)  # type: ignore
                            amount = float(escrow.amount)  # type: ignore
                            escrow_id_str = str(escrow.escrow_id)  # type: ignore
                            
                            # CRITICAL: Mark warning as sent BEFORE sending notifications to prevent race conditions
                            # This ensures concurrent scheduler runs won't both send the same warning
                            setattr(escrow, warning_flag_attr, True)
                            session.flush()  # Persist the flag BEFORE external I/O
                            
                            try:
                                # Send warning to buyer (dual-channel: Telegram + Email)
                                buyer_request = NotificationRequest(
                                    user_id=buyer_id,
                                    category=NotificationCategory.ESCROW_UPDATES,
                                    priority=NotificationPriority.HIGH,
                                    title="Delivery Deadline Warning",
                                    message=f"⏰ #{escrow_id_str} • ${amount:.2f} - {warning_type} left\n\n"
                                           f"Not delivered? Dispute before deadline",
                                    broadcast_mode=True  # Force dual-channel delivery (Telegram + Email)
                                )
                                await consolidated_notification_service.send_notification(buyer_request)
                                
                                # Send warning to seller (dual-channel: Telegram + Email)
                                seller_request = NotificationRequest(
                                    user_id=seller_id,
                                    category=NotificationCategory.ESCROW_UPDATES,
                                    priority=NotificationPriority.HIGH,
                                    title="Delivery Deadline Warning",
                                    message=f"⏰ #{escrow_id_str} • ${amount:.2f} - {warning_type} left\n\n"
                                           f"📦 Deliver soon - auto-release after deadline",
                                    broadcast_mode=True  # Force dual-channel delivery (Telegram + Email)
                                )
                                await consolidated_notification_service.send_notification(seller_request)
                                
                                warnings_sent += 1
                                logger.info(f"✅ Sent {warning_type} delivery warning for escrow {escrow.escrow_id} - marked as sent")
                                
                            except Exception as notification_error:
                                # If notification fails, flag is already set so we won't retry (prevents spam)
                                # Admin can manually reset flag if needed
                                logger.error(f"❌ Failed to send {warning_type} warning for escrow {escrow.escrow_id}: {notification_error}")
                                logger.warning(f"⚠️ Warning flag already set for {escrow.escrow_id} - manual reset required if retry needed")
                            
                    except Exception as e:
                        logger.error(f"Error sending delivery warning for escrow {escrow.escrow_id}: {e}")
                        continue
                
                if warnings_sent > 0:
                    logger.info(f"✅ Sent {warnings_sent} delivery deadline warnings")
                else:
                    logger.debug("No delivery deadline warnings needed at this time")
                    
        except Exception as e:
            logger.error(f"Error in send_delivery_deadline_warnings: {e}")
            
        return warnings_sent

    async def process_auto_release(self) -> int:
        """Process escrows eligible for auto-release with atomic operations"""
        auto_releases_processed = 0
        
        try:
            with atomic_transaction() as session:
                # Get escrows eligible for auto-release (based on auto_release_at field)
                current_time = datetime.now(timezone.utc)
                auto_release_escrows = (
                    session.query(Escrow)
                    .filter(
                        Escrow.status == str(EscrowStatus.ACTIVE.value),
                        Escrow.auto_release_at.isnot(None),
                        Escrow.auto_release_at < current_time,
                    )
                    .with_for_update()
                    .all()
                )

                for escrow in auto_release_escrows:
                    try:
                        with locked_escrow_operation(
                            str(escrow.escrow_id), session
                        ) as locked_escrow:
                            # Double-check status (already filtered by auto_release_at field)
                            if locked_escrow.status != str(EscrowStatus.ACTIVE.value):
                                continue

                            # Calculate release amount (escrow amount minus seller fees)
                            escrow_amount = Decimal(str(locked_escrow.amount))
                            seller_fee = Decimal(str(locked_escrow.seller_fee_amount)) if locked_escrow.seller_fee_amount else Decimal("0.0")
                            release_amount = escrow_amount - seller_fee
                            
                            logger.info(f"💰 Auto-release {locked_escrow.escrow_id}: amount=${escrow_amount}, seller_fee=${seller_fee}, release_amount=${release_amount}")

                            # Release funds to seller using SYNC method (auto-release runs in sync context)
                            # CRITICAL FIX: credit_user_wallet_atomic is async but we're in sync context
                            release_success = CryptoServiceAtomic.credit_user_wallet_simple(
                                user_id=locked_escrow.seller_id,
                                amount=float(release_amount),  # FIX: Use release_amount, not full amount
                                description=f"Auto-release payment for escrow {locked_escrow.escrow_id} (Fee: ${seller_fee})"
                            )

                            if release_success:
                                locked_escrow.status = str(EscrowStatus.COMPLETED.value)
                                locked_escrow.completed_at = datetime.now(timezone.utc)
                                locked_escrow.auto_released_at = datetime.now(timezone.utc)  # Track auto-release timestamp

                                # Send completion notifications with auto-release context
                                buyer_autorelease_request = NotificationRequest(
                                    user_id=locked_escrow.buyer_id,
                                    category=NotificationCategory.ESCROW_UPDATES,
                                    priority=NotificationPriority.HIGH,
                                    title="Trade Auto-Completed",
                                    message=f"🕐 Trade Auto-Completed\n\n"
                                           f"🔒 Trade #{locked_escrow.escrow_id}\n"
                                           f"💰 Amount: ${float(locked_escrow.amount):.2f} USD\n\n"
                                           f"✅ Delivery deadline has passed - funds have been automatically released to the seller.\n\n"
                                           f"💡 If you have any issues with this trade, please contact support immediately."
                                )
                                await consolidated_notification_service.send_notification(buyer_autorelease_request)
                                seller_autorelease_request = NotificationRequest(
                                    user_id=locked_escrow.seller_id,
                                    category=NotificationCategory.ESCROW_UPDATES,
                                    priority=NotificationPriority.HIGH,
                                    title="Payment Received - Auto-Released",
                                    message=f"💰 Payment Received - Auto-Released!\n\n"
                                           f"🔒 Trade #{locked_escrow.escrow_id}\n"
                                           f"💰 Amount: ${float(locked_escrow.amount):.2f} USD\n\n"
                                           f"✅ Funds released to your wallet after delivery deadline passed.\n\n"
                                           f"🎉 Great job completing this trade!"
                                )
                                await consolidated_notification_service.send_notification(seller_autorelease_request)
                                
                                auto_releases_processed += 1
                                logger.info(
                                    f"✅ Auto-released escrow {locked_escrow.escrow_id} - ${float(locked_escrow.amount):.2f} to seller {locked_escrow.seller_id}"
                                )
                            else:
                                logger.error(
                                    f"❌ Failed to auto-release escrow {locked_escrow.escrow_id}"
                                )

                    except Exception as e:
                        logger.error(
                            f"Error processing auto-release for escrow {escrow.escrow_id}: {e}"
                        )
                        continue
                        
                if auto_releases_processed > 0:
                    logger.info(f"✅ Processed {auto_releases_processed} auto-releases")
                else:
                    logger.debug("No auto-releases needed at this time")

        except Exception as e:
            logger.error(f"Error in process_auto_release: {e}")
            
        return auto_releases_processed

    async def run_full_check(self) -> dict:
        """Run both delivery warnings and auto-release checks"""
        try:
            logger.info("🔄 Starting auto-release service full check...")
            
            # Send delivery deadline warnings
            warnings_sent = await self.send_delivery_deadline_warnings()
            
            # Process auto-releases
            auto_releases = await self.process_auto_release()
            
            results = {
                "warnings_sent": warnings_sent,
                "auto_releases_processed": auto_releases,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "success": True
            }
            
            logger.info(f"✅ Auto-release service check completed: {warnings_sent} warnings, {auto_releases} auto-releases")
            return results
            
        except Exception as e:
            logger.error(f"❌ Error in auto-release service full check: {e}")
            return {
                "warnings_sent": 0,
                "auto_releases_processed": 0,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "success": False,
                "error": str(e)
            }

# Global service instance
auto_release_service = StandaloneAutoReleaseService()

async def run_auto_release_check():
    """Convenience function to run auto-release check"""
    return await auto_release_service.run_full_check()

async def run_delivery_warnings():
    """Convenience function to run delivery warnings only"""
    return await auto_release_service.send_delivery_deadline_warnings()

async def run_auto_release_only():
    """Convenience function to run auto-release only"""
    return await auto_release_service.process_auto_release()

if __name__ == "__main__":
    """Can be run as standalone script"""
    import sys
    
    async def main():
        """Main function for standalone execution"""
        logger.info("🚀 Starting standalone auto-release service...")
        results = await auto_release_service.run_full_check()
        
        if results["success"]:
            print(f"✅ Success: {results['warnings_sent']} warnings, {results['auto_releases_processed']} auto-releases")
            sys.exit(0)
        else:
            print(f"❌ Error: {results.get('error', 'Unknown error')}")
            sys.exit(1)
    
    asyncio.run(main())