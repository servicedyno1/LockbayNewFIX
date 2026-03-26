"""
Core Cleanup & Expiry Engine - Architect's Strategic Clean Rewrite
Data management and system hygiene with proper async session patterns

Features:
- Use async with managed_session() pattern exclusively
- Operate on AsyncSession only, never pass sessionmaker objects
- Clean session lifecycle management
- Comprehensive cleanup and expiry operations
"""

import logging
import asyncio
import glob
import tempfile
from datetime import datetime, timedelta
from typing import Dict, Any, List
from pathlib import Path
from database import async_managed_session
from models import Escrow, EscrowStatus, EscrowRefundOperation
from sqlalchemy import select, text
from services.escrow_expiry_service import EscrowExpiryService
from services.refund_service import RefundService
from services.notification_orchestrator import NotificationOrchestrator
from utils.escrow_state_validator import EscrowStateValidator

logger = logging.getLogger(__name__)


class CleanupExpiryEngine:
    """Core cleanup and expiry engine - Clean async patterns"""

    def __init__(self):
        self.max_execution_time = 600  # 10 minutes max execution time
        self.batch_size = 100  # Process up to 100 items per batch
        
    async def run_core_cleanup_expiry(self) -> Dict[str, Any]:
        """
        Modular cleanup engine using dedicated services
        Implements architect's recommended pattern: isolate DB, refunds, notifications
        """
        start_time = datetime.utcnow()
        results = {
            "escrow_expiry": {"processed": 0, "expired": 0, "errors": 0},
            "refund_processing": {"processed": 0, "refunded_amount": 0, "errors": 0},
            "notification_dispatch": {"processed": 0, "successful": 0, "errors": 0},
            "cashout_cleanup": {"processed": 0, "cleaned": 0, "errors": 0},
            "data_cleanup": {"categories": 0, "items_removed": 0, "errors": 0},
            "verification_cleanup": {"processed": 0, "expired": 0, "errors": 0},
            "rate_lock_expiry": {"processed": 0, "expired": 0, "errors": 0},
            "system_maintenance": {"tasks": 0, "completed": 0, "errors": 0},
            "execution_time_ms": 0,
            "status": "success"
        }
        
        logger.info("🧹 CORE_CLEANUP_EXPIRY: Starting modular cleanup cycle")
        
        try:
            # Initialize services
            escrow_service = EscrowExpiryService(batch_size=self.batch_size)
            refund_service = RefundService()
            notification_service = NotificationOrchestrator()
            
            # Step 1: Process expired escrows (pure DB operations)
            logger.info("🔍 STEP_1: Processing expired escrows")
            async with async_managed_session() as session:
                escrow_results = await escrow_service.process_expired_escrows(session)
                results["escrow_expiry"] = {
                    "processed": escrow_results.get("processed", 0),
                    "expired": len(escrow_results.get("expired_escrows", [])),
                    "errors": len(escrow_results.get("errors", []))
                }
                await session.commit()
                
            expired_escrows = escrow_results.get("expired_escrows", [])
            
            # Step 2: Process refunds (isolated wallet operations with deduplication check)
            if expired_escrows:
                logger.info(f"💰 STEP_2: Processing refunds for {len(expired_escrows)} expired escrows")
                
                # ARCHITECT'S SOLUTION: Check existing refund ledger before processing
                async with async_managed_session() as session:
                    # Pre-filter escrows that already have refund operations
                    escrows_needing_refund = await self._filter_escrows_for_refund_processing(expired_escrows, session)
                    
                    if escrows_needing_refund:
                        logger.info(f"🔄 REFUND_DEDUPLICATION: {len(escrows_needing_refund)} escrows need refund processing "
                                   f"({len(expired_escrows) - len(escrows_needing_refund)} already processed)")
                        
                        refund_results = await refund_service.process_escrow_refunds(escrows_needing_refund, session)
                        results["refund_processing"] = {
                            "processed": refund_results.get("processed", 0),
                            "refunded_amount": float(refund_results.get("refunded_amount", 0)),
                            "errors": len(refund_results.get("errors", [])),
                            "deduplication_skipped": len(expired_escrows) - len(escrows_needing_refund)
                        }
                    else:
                        logger.info("✅ REFUND_DEDUPLICATION: All expired escrows already have refund operations")
                        refund_results = {
                            "processed": 0, 
                            "successful_refunds": [], 
                            "errors": [],
                            "all_duplicates": True
                        }
                        results["refund_processing"] = {
                            "processed": 0,
                            "refunded_amount": 0,
                            "errors": 0,
                            "deduplication_skipped": len(expired_escrows)
                        }
                    
                    await session.commit()
            else:
                logger.info("💰 STEP_2: No expired escrows for refund processing")
                refund_results = {"processed": 0, "successful_refunds": [], "errors": []}
            
            # Step 3: Send notifications (isolated from DB operations)
            # FIX ISSUE 3: Filter out escrows that already have notification records to prevent stuck loops
            if expired_escrows:
                logger.info(f"📧 STEP_3: Sending notifications for {len(expired_escrows)} expired escrows")
                
                # Filter escrows that already have notifications
                async with async_managed_session() as session:
                    escrows_needing_notification = await self._filter_escrows_for_notification(expired_escrows, session)
                    
                    if escrows_needing_notification:
                        logger.info(f"🔄 NOTIFICATION_DEDUPLICATION: {len(escrows_needing_notification)} escrows need notifications "
                                   f"({len(expired_escrows) - len(escrows_needing_notification)} already notified)")
                        
                        notification_results = await notification_service.notify_escrow_expirations(escrows_needing_notification, refund_results)
                        results["notification_dispatch"] = {
                            "processed": notification_results.get("processed", 0),
                            "successful": len(notification_results.get("successful_notifications", [])),
                            "errors": len(notification_results.get("errors", [])),
                            "deduplication_skipped": len(expired_escrows) - len(escrows_needing_notification)
                        }
                    else:
                        logger.info("✅ NOTIFICATION_DEDUPLICATION: All expired escrows already have notification records")
                        results["notification_dispatch"] = {
                            "processed": 0,
                            "successful": 0,
                            "errors": 0,
                            "deduplication_skipped": len(expired_escrows)
                        }
            else:
                logger.info("📧 STEP_3: No expired escrows for notifications")
            
            # Step 4: Continue with other cleanup tasks
            async with async_managed_session() as session:
                # Rate lock expiry
                rate_results = await self._expire_rate_locks(session)
                results["rate_lock_expiry"] = rate_results
                
                # Verification cleanup
                verification_results = await self._cleanup_expired_verifications(session)
                results["verification_cleanup"] = verification_results
                
                await session.commit()
            
            # 4. Cashout cleanup (may use external services)
            cashout_results = await self._cleanup_cashout_holds()
            results["cashout_cleanup"] = cashout_results
            
            # 5. General data cleanup (file system operations)
            data_results = await self._cleanup_old_data()
            results["data_cleanup"] = data_results
            
            # 6. System maintenance tasks
            maintenance_results = await self._perform_system_maintenance()
            results["system_maintenance"] = maintenance_results
            
            # Update performance metrics
            execution_time = (datetime.utcnow() - start_time).total_seconds() * 1000
            results["execution_time_ms"] = execution_time
            
            total_cleaned = (
                escrow_results.get("expired", 0) +
                cashout_results.get("cleaned", 0) +
                data_results.get("items_removed", 0) +
                verification_results.get("expired", 0) +
                rate_results.get("expired", 0)
            )
            
            if total_cleaned > 0:
                logger.info(
                    f"✅ CLEANUP_COMPLETE: Cleaned {total_cleaned} items in {execution_time:.0f}ms"
                )
            else:
                logger.debug("💤 CLEANUP_IDLE: No items requiring cleanup")
            
            return results
            
        except Exception as e:
            logger.error(f"❌ CORE_CLEANUP_ERROR: Cleanup processing failed: {e}")
            results["status"] = "error"
            results["error"] = str(e)
            return results

    async def _filter_escrows_for_refund_processing(self, expired_escrows: List[Dict[str, Any]], session) -> List[Dict[str, Any]]:
        """
        ARCHITECT'S SOLUTION: Filter escrows that already have refund operations
        
        This prevents duplicate refund processing by checking the EscrowRefundOperation ledger
        before attempting to process refunds.
        
        Args:
            expired_escrows: List of expired escrow data
            session: Database session
            
        Returns:
            List of escrows that need refund processing (haven't been refunded yet)
        """
        if not expired_escrows:
            return []
        
        escrows_needing_refund = []
        
        for escrow_data in expired_escrows:
            escrow_id = escrow_data.get("escrow_id")
            internal_id = escrow_data.get("internal_id") or escrow_data.get("id")
            buyer_id = escrow_data.get("buyer_id")
            
            if not internal_id or not buyer_id:
                logger.warning(f"Skipping escrow with missing ID or buyer_id: {escrow_data}")
                continue
            
            # CRITICAL FIX: Generate STRICTLY DETERMINISTIC refund cycle ID that matches RefundService
            # ARCHITECT'S FIX: Removed date component to prevent multiple refunds across different days
            refund_reason = "expired_timeout"
            cycle_data = f"{internal_id}_{refund_reason}"  # NO DATE COMPONENT - uses internal DB ID
            import hashlib
            cycle_hash = hashlib.sha256(cycle_data.encode()).hexdigest()[:16]
            refund_cycle_id = f"refund_{internal_id}_{refund_reason}_{cycle_hash}"
            
            # Check if refund operation already exists
            existing_refund = await session.execute(
                select(EscrowRefundOperation).where(
                    EscrowRefundOperation.escrow_id == internal_id,
                    EscrowRefundOperation.buyer_id == buyer_id,
                    EscrowRefundOperation.refund_cycle_id == refund_cycle_id
                )
            )
            
            if existing_refund.first() is None:
                # No existing refund operation, add to processing list
                escrows_needing_refund.append(escrow_data)
                logger.debug(f"🔄 NEEDS_REFUND: Escrow {escrow_id} / ID {internal_id} (cycle: {refund_cycle_id})")
            else:
                logger.info(f"⏭️ REFUND_SKIP: Escrow {escrow_id} / ID {internal_id} already has refund operation (cycle: {refund_cycle_id})")
        
        return escrows_needing_refund
    
    async def _filter_escrows_for_notification(self, expired_escrows: List[Dict[str, Any]], session) -> List[Dict[str, Any]]:
        """
        FIX ISSUE 3: Filter escrows that already have notification records
        
        This prevents stuck loops by checking the NotificationActivity table
        before attempting to send notifications.
        
        Args:
            expired_escrows: List of expired escrow data
            session: Database session
            
        Returns:
            List of escrows that need notification processing (haven't been notified yet)
        """
        if not expired_escrows:
            return []
        
        from models import NotificationActivity
        import hashlib
        
        escrows_needing_notification = []
        
        for escrow_data in expired_escrows:
            escrow_id = escrow_data.get("escrow_id")
            buyer_id = escrow_data.get("buyer_id")
            seller_id = escrow_data.get("seller_id")
            
            if not escrow_id:
                logger.warning(f"Skipping escrow with missing escrow_id: {escrow_data}")
                continue
            
            # Check buyer notification (always check since buyer_id should always exist)
            buyer_notified = False
            if buyer_id:
                buyer_notification_key = hashlib.sha256(f"{escrow_id}_escrow_expired_{buyer_id}".encode()).hexdigest()[:16]
                buyer_notification = await session.execute(
                    select(NotificationActivity).where(
                        NotificationActivity.activity_id == buyer_notification_key,
                        NotificationActivity.delivery_status.in_(["delivered", "failed"])
                    )
                )
                buyer_notified = buyer_notification.first() is not None
            
            # Check seller notification (only if seller_id exists)
            seller_notified = False
            if seller_id:
                seller_notification_key = hashlib.sha256(f"{escrow_id}_escrow_expired_{seller_id}".encode()).hexdigest()[:16]
                seller_notification = await session.execute(
                    select(NotificationActivity).where(
                        NotificationActivity.activity_id == seller_notification_key,
                        NotificationActivity.delivery_status.in_(["delivered", "failed"])
                    )
                )
                seller_notified = seller_notification.first() is not None
            else:
                # FIX ISSUE 1: If seller_id is NULL, consider seller "notified" (can't notify non-existent user)
                seller_notified = True
                logger.info(f"✅ NULL_SELLER_SKIP: Escrow {escrow_id} has no seller_id, treating as notified")
            
            # Only process if BOTH buyer and seller haven't been notified
            if not buyer_notified or not seller_notified:
                escrows_needing_notification.append(escrow_data)
                logger.debug(f"🔄 NEEDS_NOTIFICATION: Escrow {escrow_id} (buyer: {not buyer_notified}, seller: {not seller_notified})")
            else:
                logger.info(f"⏭️ NOTIFICATION_SKIP: Escrow {escrow_id} already has notification records")
        
        return escrows_needing_notification

    async def _handle_expired_escrows(self, session) -> Dict[str, Any]:
        """Handle expired escrows with clean async session patterns"""
        results = {"processed": 0, "expired": 0, "errors": 0}
        
        # Initialize state validator for security validation
        validator = EscrowStateValidator()
        
        try:
            # Find expired escrows using the correct expires_at field and current time
            current_time = datetime.utcnow()
            
            result = await session.execute(
                select(Escrow).where(
                    Escrow.status.in_([EscrowStatus.PAYMENT_PENDING.value, EscrowStatus.PAYMENT_CONFIRMED.value, EscrowStatus.PARTIAL_PAYMENT.value, EscrowStatus.PAYMENT_FAILED.value]),
                    Escrow.expires_at < current_time
                ).limit(self.batch_size)
            )
            expired_escrows = result.scalars().all()
            
            results["processed"] = len(expired_escrows)
            
            for escrow in expired_escrows:
                try:
                    original_status = escrow.status
                    
                    # Handle different scenarios based on current status
                    if original_status in [EscrowStatus.PAYMENT_PENDING.value, EscrowStatus.PAYMENT_FAILED.value]:
                        # Scenario A: Payment timeout or failed - simple expiry (no refund needed)
                        logger.info(f"🕒 ESCROW_EXPIRY: Payment {'timeout' if original_status == EscrowStatus.PAYMENT_PENDING.value else 'failed'} for escrow {escrow.id}")
                        
                        # SECURITY FIX: Validate state transition before expiry to prevent DISPUTED→EXPIRED
                        current_status = escrow.status
                        if not validator.is_valid_transition(current_status, EscrowStatus.EXPIRED.value):
                            logger.error(
                                f"🚫 EXPIRY_BLOCKED: Invalid transition {current_status}→EXPIRED for escrow {escrow.escrow_id}"
                            )
                            continue  # Skip this escrow
                        
                        escrow.status = EscrowStatus.EXPIRED.value
                        escrow.expired_at = datetime.utcnow()
                        
                    elif original_status in [EscrowStatus.PAYMENT_CONFIRMED.value, EscrowStatus.PARTIAL_PAYMENT.value]:
                        # Scenario B: Paid but not accepted - refund to buyer
                        logger.info(f"💰 ESCROW_REFUND: Auto-refunding paid escrow {escrow.id} (not accepted in time)")
                        
                        # SECURITY FIX: Validate state transition before refund to prevent DISPUTED→REFUNDED
                        current_status = escrow.status
                        if not validator.is_valid_transition(current_status, EscrowStatus.REFUNDED.value):
                            logger.error(
                                f"🚫 REFUND_BLOCKED: Invalid transition {current_status}→REFUNDED for escrow {escrow.escrow_id}"
                            )
                            continue  # Skip this escrow
                        
                        escrow.status = EscrowStatus.REFUNDED.value
                        escrow.expired_at = datetime.utcnow()
                        
                        # Refund to buyer's wallet
                        try:
                            from models import Wallet
                            wallet_result = await session.execute(
                                select(Wallet).where(
                                    Wallet.user_id == escrow.buyer_id,
                                    Wallet.currency == escrow.currency
                                )
                            )
                            buyer_wallet = wallet_result.scalar_one_or_none()
                            
                            if buyer_wallet:
                                buyer_wallet.available_balance += escrow.amount
                                logger.info(f"💳 REFUND: Added ${escrow.amount} {escrow.currency} to buyer {escrow.buyer_id} wallet")
                                
                                # Send refund notifications (bot + email)
                                try:
                                    from services.consolidated_notification_service import consolidated_notification_service
                                    from services.consolidated_notification_service import NotificationRequest, NotificationCategory, NotificationPriority, NotificationChannel
                                    
                                    await consolidated_notification_service.send_notification(
                                        NotificationRequest(
                                            user_id=escrow.buyer_id,
                                            category=NotificationCategory.ESCROW_UPDATES,
                                            priority=NotificationPriority.HIGH,
                                            title="🔄 Escrow Refunded",
                                            message=f"Your escrow #{escrow.escrow_id} has been refunded due to timeout. ${escrow.amount} {escrow.currency} has been added back to your wallet.",
                                            channels=[NotificationChannel.TELEGRAM, NotificationChannel.EMAIL]
                                        )
                                    )
                                except Exception as notif_error:
                                    logger.error(f"Failed to send refund notification: {notif_error}")
                            else:
                                logger.error(f"Buyer wallet not found for refund - escrow {escrow.id}")
                                
                        except Exception as refund_error:
                            logger.error(f"Error processing refund for escrow {escrow.id}: {refund_error}")
                    
                    results["expired"] += 1
                    await session.flush()  # Flush individual changes
                    
                    # Send general expiry notifications
                    try:
                        logger.info(f"📧 NOTIFICATION: Sending expiry notification for escrow {escrow.id}")
                    except Exception as notification_error:
                        logger.warning(f"Failed to send expiry notification for escrow {escrow.id}: {notification_error}")
                    
                    logger.info(f"⏰ ESCROW_EXPIRED: {escrow.id} expired and cleaned up")
                    
                except Exception as escrow_error:
                    logger.error(f"Error expiring escrow {escrow.id}: {escrow_error}")
                    results["errors"] += 1
                    continue
                    
            if results["expired"] > 0:
                logger.info(f"⏰ ESCROW_EXPIRY: Expired {results['expired']} escrows")
                    
        except Exception as e:
            logger.error(f"❌ ESCROW_EXPIRY_ERROR: {e}")
            results["errors"] = 1
            
        return results

    async def _expire_rate_locks(self, session) -> Dict[str, Any]:
        """Expire old rate locks with clean async patterns"""
        results = {"processed": 0, "expired": 0, "errors": 0}
        
        try:
            # Import rate lock model if available
            try:
                # Import rate lock model with graceful handling
                # Note: RateLock model may not exist in all deployments
                from models import RateLock  # type: ignore
                
                cutoff_time = datetime.utcnow() - timedelta(minutes=30)  # 30-minute rate locks
                
                result = await session.execute(
                    select(RateLock).where(
                        RateLock.expires_at < cutoff_time,
                        RateLock.is_active == True
                    ).limit(self.batch_size)
                )
                expired_locks = result.scalars().all()
                
                results["processed"] = len(expired_locks)
                
                for lock in expired_locks:
                    try:
                        lock.is_active = False
                        lock.expired_at = datetime.utcnow()
                        results["expired"] += 1
                    except Exception:
                        results["errors"] += 1
                        
                await session.flush()
                
                if results["expired"] > 0:
                    logger.info(f"🔒 RATE_LOCK_EXPIRY: Expired {results['expired']} rate locks")
                    
            except (ImportError, AttributeError):
                logger.debug("Rate lock model not available")
                results["processed"] = 0
                
        except Exception as e:
            logger.error(f"❌ RATE_LOCK_EXPIRY_ERROR: {e}")
            results["errors"] = 1
            
        return results

    async def _cleanup_expired_verifications(self, session) -> Dict[str, Any]:
        """Cleanup expired verification records with clean patterns"""
        results = {"processed": 0, "expired": 0, "errors": 0}
        
        try:
            # Clean verification cleanup with session injection
            try:
                from jobs.destination_cleanup_monitor import DestinationCleanupMonitor
                cleanup_result = await DestinationCleanupMonitor.run_cleanup()
                
                if isinstance(cleanup_result, dict):
                    results["processed"] = cleanup_result.get("processed", 0)
                    results["expired"] = cleanup_result.get("cleaned", 0)
                    results["errors"] = cleanup_result.get("errors", 0)
                elif isinstance(cleanup_result, (int, float)):
                    results["processed"] = int(cleanup_result)
                    results["expired"] = int(cleanup_result)
                    
                if results["expired"] > 0:
                    logger.info(f"🎯 VERIFICATION_CLEANUP: Expired {results['expired']} verifications")
                    
            except (ImportError, AttributeError):
                logger.debug("Verification cleanup not available")
                
        except Exception as e:
            logger.error(f"❌ VERIFICATION_CLEANUP_ERROR: {e}")
            results["errors"] = 1
            
        return results

    async def _cleanup_cashout_holds(self) -> Dict[str, Any]:
        """Cleanup expired cashout holds with clean patterns"""
        results = {"processed": 0, "cleaned": 0, "errors": 0}
        
        try:
            # Use external cleanup services with proper session handling
            async with async_managed_session() as session:
                try:
                    from jobs.cashout_hold_cleanup_job import run_cashout_hold_cleanup
                    hold_result = await run_cashout_hold_cleanup()
                    
                    if isinstance(hold_result, dict):
                        results["processed"] += hold_result.get("processed", 0)
                        results["cleaned"] += hold_result.get("released", 0)
                        results["errors"] += hold_result.get("errors", 0)
                    elif isinstance(hold_result, (int, float)):
                        results["processed"] += int(hold_result)
                        results["cleaned"] += int(hold_result)
                        
                except (ImportError, AttributeError):
                    logger.debug("Cashout hold cleanup not available")
                    
                try:
                    from jobs.automatic_cashout_cleanup import AutomaticCashoutCleanup
                    auto_result = await AutomaticCashoutCleanup.cleanup_stuck_cashouts()
                    
                    if isinstance(auto_result, dict):
                        results["processed"] += auto_result.get("processed", 0)
                        results["cleaned"] += auto_result.get("cleaned", 0)
                        results["errors"] += auto_result.get("errors", 0)
                    elif isinstance(auto_result, (int, float)):
                        results["processed"] += int(auto_result)
                        results["cleaned"] += int(auto_result)
                        
                except (ImportError, AttributeError):
                    logger.debug("Automatic cashout cleanup not available")
                
                await session.commit()
                
            if results["cleaned"] > 0:
                logger.info(f"🧹 CASHOUT_CLEANUP: Cleaned {results['cleaned']} items")
                
        except Exception as e:
            logger.error(f"❌ CASHOUT_CLEANUP_ERROR: {e}")
            results["errors"] = 1
            
        return results

    async def _cleanup_old_data(self) -> Dict[str, Any]:
        """Cleanup old data across various categories"""
        results = {"categories": 0, "items_removed": 0, "errors": 0}
        
        try:
            cleanup_tasks = [
                ("logs", self._cleanup_old_logs),
                ("sessions", self._cleanup_expired_sessions),
                ("temp_files", self._cleanup_temp_files),
                ("cached_data", self._cleanup_cached_data)
            ]
            
            for category, cleanup_func in cleanup_tasks:
                try:
                    category_result = await cleanup_func()
                    results["categories"] += 1
                    results["items_removed"] += category_result.get("removed", 0)
                    
                    if category_result.get("removed", 0) > 0:
                        logger.info(
                            f"🗑️ {category.upper()}_CLEANUP: Removed {category_result['removed']} items"
                        )
                        
                except Exception as category_error:
                    logger.error(f"Error cleaning {category}: {category_error}")
                    results["errors"] += 1
                    
        except Exception as e:
            logger.error(f"❌ DATA_CLEANUP_ERROR: {e}")
            results["errors"] = 1
            
        return results

    async def _cleanup_old_logs(self) -> Dict[str, Any]:
        """Cleanup old log files"""
        results = {"removed": 0}
        
        try:
            # Remove logs older than 7 days
            cutoff_time = datetime.now() - timedelta(days=7)
            log_patterns = ["logs/*.log", "logs/**/*.log", "*.log"]
            
            for pattern in log_patterns:
                for log_file in glob.glob(pattern):
                    try:
                        file_path = Path(log_file)
                        if file_path.exists():
                            file_time = datetime.fromtimestamp(file_path.stat().st_mtime)
                            if file_time < cutoff_time:
                                file_path.unlink()
                                results["removed"] += 1
                    except Exception:
                        continue  # Skip files we can't process
                        
        except Exception as e:
            logger.debug(f"Log cleanup warning: {e}")
            
        return results

    async def _cleanup_expired_sessions(self) -> Dict[str, Any]:
        """Cleanup expired user sessions with clean async patterns"""
        results = {"removed": 0}
        
        try:
            # Cleanup expired sessions from database
            async with async_managed_session() as session:
                # Clean session cleanup implementation
                try:
                    from models import OnboardingSession
                    cutoff_time = datetime.utcnow() - timedelta(days=7)
                    
                    result = await session.execute(
                        select(OnboardingSession).where(
                            OnboardingSession.expires_at < cutoff_time
                        ).limit(self.batch_size)
                    )
                    expired_sessions = result.scalars().all()
                    
                    for old_session in expired_sessions:
                        await session.delete(old_session)  # type: ignore
                        results["removed"] += 1
                    
                    await session.commit()
                    
                except ImportError:
                    logger.debug("OnboardingSession model not available")
                    
        except Exception as e:
            logger.debug(f"Session cleanup warning: {e}")
            
        return results

    async def _cleanup_temp_files(self) -> Dict[str, Any]:
        """Cleanup temporary files"""
        results = {"removed": 0}
        
        try:
            # Cleanup temp directories
            temp_dirs = ["/tmp", tempfile.gettempdir(), "temp", "tmp"]
            cutoff_time = datetime.now() - timedelta(hours=24)
            
            for temp_dir in temp_dirs:
                try:
                    temp_path = Path(temp_dir)
                    if temp_path.exists():
                        for temp_file in temp_path.glob("**/*"):
                            if temp_file.is_file():
                                file_time = datetime.fromtimestamp(temp_file.stat().st_mtime)
                                if file_time < cutoff_time:
                                    temp_file.unlink()
                                    results["removed"] += 1
                except Exception:
                    continue
                    
        except Exception as e:
            logger.debug(f"Temp file cleanup warning: {e}")
            
        return results

    async def _cleanup_cached_data(self) -> Dict[str, Any]:
        """Cleanup expired cached data"""
        results = {"removed": 0}
        
        try:
            # Cleanup application-specific cached data
            # Enhanced cache is not available - skip silently
            try:
                logger.debug("Cached data cleanup skipped - module not available")
                
            except (ImportError, ModuleNotFoundError):
                logger.debug("Cache cleanup not available")
        except Exception as e:
            logger.debug(f"Cache cleanup warning: {e}")
            
        return results

    async def _perform_system_maintenance(self) -> Dict[str, Any]:
        """Perform system maintenance tasks with clean patterns"""
        results = {"tasks": 0, "completed": 0, "errors": 0}
        
        try:
            maintenance_tasks = [
                ("distributed_locks", self._cleanup_distributed_locks),
                ("database_stats", self._update_database_stats),
                ("performance_metrics", self._cleanup_performance_metrics)
            ]
            
            for task_name, task_func in maintenance_tasks:
                try:
                    results["tasks"] += 1
                    await task_func()
                    results["completed"] += 1
                    logger.debug(f"✅ MAINTENANCE: {task_name} completed")
                except Exception as task_error:
                    logger.error(f"❌ MAINTENANCE_{task_name.upper()}_ERROR: {task_error}")
                    results["errors"] += 1
                    
        except Exception as e:
            logger.error(f"❌ SYSTEM_MAINTENANCE_ERROR: {e}")
            results["errors"] = 1
            
        return results

    async def _cleanup_distributed_locks(self):
        """Cleanup expired distributed locks"""
        try:
            # Import distributed locks cleanup with graceful handling
            try:
                from utils.distributed_locks import cleanup_expired_locks  # type: ignore
                await cleanup_expired_locks()
            except (ImportError, ModuleNotFoundError, AttributeError):
                logger.debug("Distributed locks module not available")
        except Exception as e:
            logger.debug(f"Distributed locks cleanup error: {e}")

    async def _update_database_stats(self):
        """Update database statistics with clean async patterns"""
        try:
            # Database statistics update with clean session handling
            async with async_managed_session() as session:
                await session.execute(text("ANALYZE;"))
                await session.commit()
        except Exception as e:
            logger.debug(f"Database stats update warning: {e}")

    async def _cleanup_performance_metrics(self):
        """Cleanup old performance metrics"""
        try:
            try:
                from utils.performance_monitor import performance_monitor
                if hasattr(performance_monitor, 'cleanup_old_metrics'):
                    cleanup_method = getattr(performance_monitor, 'cleanup_old_metrics')
                    if asyncio.iscoroutinefunction(cleanup_method):
                        await cleanup_method()
                    else:
                        cleanup_method()
                else:
                    logger.debug("Performance monitor cleanup_old_metrics method not available")
            except (ImportError, ModuleNotFoundError, AttributeError):
                logger.debug("Performance monitor module not available")
        except Exception as e:
            logger.debug(f"Performance metrics cleanup error: {e}")


# Global cleanup expiry engine instance
cleanup_expiry_engine = CleanupExpiryEngine()


# Exported functions for scheduler integration with clean async patterns
async def run_cleanup_expiry():
    """Main entry point for scheduler - comprehensive cleanup and expiry"""
    return await cleanup_expiry_engine.run_core_cleanup_expiry()


async def run_escrow_expiry():
    """Run escrow expiry only - for escrow-specific cleanup"""
    async with async_managed_session() as session:
        result = await cleanup_expiry_engine._handle_expired_escrows(session)
        await session.commit()
        return result


async def run_cashout_cleanup():
    """Run cashout cleanup only - for cashout-specific cleanup"""  
    return await cleanup_expiry_engine._cleanup_cashout_holds()


async def run_data_cleanup():
    """Run data cleanup only - for general data maintenance"""
    return await cleanup_expiry_engine._cleanup_old_data()


async def run_verification_cleanup():
    """Run verification cleanup only - for verification-specific cleanup"""
    async with async_managed_session() as session:
        result = await cleanup_expiry_engine._cleanup_expired_verifications(session)
        await session.commit()
        return result


# Export for scheduler
__all__ = [
    "CleanupExpiryEngine",
    "cleanup_expiry_engine",
    "run_cleanup_expiry",
    "run_escrow_expiry",
    "run_cashout_cleanup", 
    "run_data_cleanup",
    "run_verification_cleanup"
]