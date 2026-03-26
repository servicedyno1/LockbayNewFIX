"""
Idempotent Refund Service - Prevents double refunds and provides audit trail
"""

import hashlib
import logging
from datetime import datetime
from decimal import Decimal
from typing import Optional, Dict, Any

from sqlalchemy.orm import Session
from sqlalchemy import and_

from models import (
    Cashout, Refund, RefundStatus
)
from services.wallet_service import WalletService

logger = logging.getLogger(__name__)


class IdempotentRefundService:
    """Service for handling refunds with idempotency guarantees"""
    
    def __init__(self, db: Session):
        self.db = db
        self.wallet_service = WalletService(db)

    def generate_idempotency_key(self, 
                                cashout_id: str, 
                                user_id: int, 
                                amount: Decimal, 
                                refund_type: str,
                                source_module: str) -> str:
        """Generate unique idempotency key for refund operation"""
        data = f"{cashout_id}-{user_id}-{amount}-{refund_type}-{source_module}"
        return hashlib.sha256(data.encode()).hexdigest()[:32]

    def check_existing_refund(self, idempotency_key: str) -> Optional[Refund]:
        """Check if refund already exists with this idempotency key"""
        return self.db.query(Refund).filter(
            Refund.idempotency_key == idempotency_key
        ).first()

    def process_cashout_refund(self, 
                              cashout_id: str,
                              reason: str,
                              source_module: str = "atomic_cashout_service",
                              force_process: bool = False) -> Dict[str, Any]:
        """
        🔒 SECURITY: AUTOMATIC CASHOUT REFUNDS DISABLED
        
        This function has been disabled due to security policy:
        "frozen funds should never automatically return to available balance"
        
        Instead, this logs the request and sends admin notification for manual review.
        
        Args:
            cashout_id: ID of the failed cashout
            reason: Reason for refund
            source_module: Module requesting the refund
            force_process: Skip idempotency check (admin override)
        
        Returns:
            Dict with security policy response
        """
        # 🔒 SECURITY: AUTOMATIC REFUNDS DISABLED - SEND ADMIN NOTIFICATION INSTEAD
        logger.warning(f"🔒 FROZEN_FUND_REFUND_BLOCKED: Cashout {cashout_id} requires admin review - automatic refunds DISABLED")
        
        try:
            # Get cashout record for admin notification
            cashout = self.db.query(Cashout).filter(
                Cashout.cashout_id == cashout_id
            ).first()
            
            if not cashout:
                return {
                    "success": False,
                    "error": f"Cashout {cashout_id} not found",
                    "refund_id": None
                }
            
            # Send admin notification instead of processing refund
            try:
                from services.consolidated_notification_service import consolidated_notification_service
                import asyncio
                
                admin_message = (
                    f"🔒 FROZEN FUND REQUIRING ADMIN REVIEW\n\n"
                    f"Cashout ID: {cashout_id}\n"
                    f"User ID: {cashout.user_id}\n"
                    f"Amount: {cashout.amount} {cashout.currency}\n"
                    f"Reason: {reason}\n"
                    f"Source: {source_module}\n\n"
                    f"🔒 SECURITY POLICY: Automatic refunds disabled.\n"
                    f"Please review and process manually if appropriate."
                )
                
                # Send admin notification async
                asyncio.create_task(
                    consolidated_notification_service.send_admin_alert(admin_message)
                )
                
                logger.warning(f"🔒 ADMIN_NOTIFICATION_SENT: Cashout {cashout_id} flagged for manual review")
                
            except Exception as notification_error:
                logger.error(f"Failed to send admin notification for cashout {cashout_id}: {notification_error}")
            
            return {
                "success": False,
                "security_blocked": True,
                "requires_admin_review": True,
                "cashout_id": cashout_id,
                "message": "🔒 SECURITY: Automatic refunds disabled. Admin notification sent for manual review.",
                "refund_id": None
            }
            
        except Exception as e:
            logger.error(f"🔒 SECURITY_ERROR: Failed to process admin notification for cashout {cashout_id}: {e}")
            return {
                "success": False,
                "security_blocked": True,
                "error": f"Security policy enforcement failed: {str(e)}",
                "refund_id": None
            }

    def get_refunds_for_cashout(self, cashout_id: str) -> list:
        """Get all refunds associated with a cashout"""
        return self.db.query(Refund).filter(
            Refund.cashout_id == cashout_id
        ).all()

    def validate_refund_integrity(self, refund_id: str) -> Dict[str, Any]:
        """Validate that refund was processed correctly"""
        refund = self.db.query(Refund).filter(
            Refund.refund_id == refund_id
        ).first()
        
        if not refund:
            return {"valid": False, "error": "Refund not found"}
        
        if refund.status != RefundStatus.COMPLETED.value:
            return {"valid": False, "error": f"Refund status is {refund.status}"}
        
        # Check balance calculation
        expected_after = refund.balance_before + refund.amount
        if abs(expected_after - refund.balance_after) > Decimal('0.00000001'):
            return {
                "valid": False, 
                "error": f"Balance mismatch: expected {expected_after}, got {refund.balance_after}"
            }
        
        return {"valid": True, "refund": refund}

    def cleanup_failed_refunds(self, max_age_hours: int = 168) -> int:  # CRITICAL FIX: Extended to 7 days (168 hours)
        """
        ENHANCED: Archive failed refunds instead of deleting for audit trail preservation
        Only archives refunds older than specified hours (default 7 days)
        """
        from datetime import timedelta
        
        cutoff_time = datetime.utcnow() - timedelta(hours=max_age_hours)
        
        failed_refunds = self.db.query(Refund).filter(
            and_(
                Refund.status == RefundStatus.FAILED.value,
                Refund.created_at < cutoff_time,
                Refund.archived_at.is_(None)  # Only process non-archived refunds
            )
        ).all()
        
        count = 0
        for refund in failed_refunds:
            # CRITICAL FIX: Archive instead of delete to preserve audit trail
            try:
                # Create audit archive record before cleanup
                self._archive_refund_for_audit(refund)
                
                # Mark as archived instead of deleting
                refund.archived_at = datetime.utcnow()
                refund.archive_reason = "automated_cleanup_failed_refund"
                
                count += 1
                logger.info(f"Archived failed refund {refund.refund_id} for audit preservation")
                
            except Exception as e:
                logger.error(f"Failed to archive refund {refund.refund_id}: {e}")
                # Continue with other refunds even if one fails
        
        self.db.commit()
        logger.info(f"Archived {count} failed refunds older than {max_age_hours} hours for audit trail preservation")
        return count
    
    def _archive_refund_for_audit(self, refund: Refund) -> None:
        """
        CRITICAL FIX: Create permanent audit archive of refund before cleanup
        Ensures refund data is preserved for compliance and investigation
        """
        try:
            # Create audit log entry with all refund details
            audit_data = {
                "refund_id": refund.refund_id,
                "user_id": refund.user_id,
                "amount": str(refund.amount),
                "currency": refund.currency,
                "refund_type": refund.refund_type,
                "status": refund.status,
                "reason": refund.reason,
                "cashout_id": refund.cashout_id,
                "idempotency_key": refund.idempotency_key,
                "processed_by": refund.processed_by,
                "balance_before": str(refund.balance_before) if refund.balance_before else None,
                "balance_after": str(refund.balance_after) if refund.balance_after else None,
                "error_message": refund.error_message,
                "created_at": refund.created_at.isoformat() if refund.created_at else None,
                "failed_at": refund.failed_at.isoformat() if refund.failed_at else None,
                "archive_reason": "audit_trail_preservation",
                "archived_at": datetime.utcnow().isoformat()
            }
            
            # Store in audit log table (if it exists) or log for external archival
            logger.critical(
                f"REFUND_AUDIT_ARCHIVE: {audit_data}"
            )
            
            # TODO: Could also store in dedicated RefundAuditArchive table if created
            
        except Exception as e:
            logger.error(f"Failed to create audit archive for refund {refund.refund_id}: {e}")
            raise  # Re-raise to prevent cleanup if archival fails