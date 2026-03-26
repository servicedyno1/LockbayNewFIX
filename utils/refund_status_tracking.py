"""
Enhanced Refund Status Tracking System
Provides comprehensive status progression and user visibility
"""

import logging
from datetime import datetime
from enum import Enum
from typing import Dict, Any, Optional

from models import Refund
from database import SessionLocal

logger = logging.getLogger(__name__)


class RefundProgressStatus(Enum):
    """Enhanced refund status progression"""
    INITIATED = "initiated"              # Refund request received
    VALIDATING = "validating"           # Checking eligibility  
    PROCESSING = "processing"           # Actually processing refund
    COMPLETING = "completing"           # Finalizing transaction
    COMPLETED = "completed"             # Successfully completed
    FAILED = "failed"                   # Failed with error
    CANCELLED = "cancelled"             # Cancelled by user/admin


class RefundStatusTracker:
    """Service for tracking refund status progression and user notifications"""
    
    @staticmethod
    def update_refund_status(refund_id: str, new_status: RefundProgressStatus, 
                           details: Optional[str] = None) -> bool:
        """Update refund status with progression tracking"""
        try:
            with SessionLocal() as session:
                refund = session.query(Refund).filter(
                    Refund.refund_id == refund_id
                ).first()
                
                if not refund:
                    logger.error(f"Refund {refund_id} not found for status update")
                    return False
                
                old_status = refund.status
                refund.status = new_status.value
                
                # Add progression details
                if details:
                    if not refund.error_message:
                        refund.error_message = ""
                    refund.error_message += f"\n{datetime.utcnow().isoformat()}: {details}"
                
                # Update completion timestamps
                if new_status == RefundProgressStatus.COMPLETED:
                    refund.completed_at = datetime.utcnow()
                elif new_status == RefundProgressStatus.FAILED:
                    refund.failed_at = datetime.utcnow()
                
                session.commit()
                
                logger.info(
                    f"🔄 REFUND_STATUS_UPDATED: {refund_id} "
                    f"{old_status} → {new_status.value}"
                    + (f" ({details})" if details else "")
                )
                
                return True
                
        except Exception as e:
            logger.error(f"Error updating refund status for {refund_id}: {e}")
            return False
    
    @staticmethod
    def get_refund_progress(refund_id: str) -> Dict[str, Any]:
        """Get detailed refund progress information"""
        try:
            with SessionLocal() as session:
                refund = session.query(Refund).filter(
                    Refund.refund_id == refund_id
                ).first()
                
                if not refund:
                    return {"error": "Refund not found"}
                
                # Calculate progress percentage
                status_progress = {
                    "initiated": 10,
                    "validating": 25,
                    "processing": 50,
                    "completing": 85,
                    "completed": 100,
                    "failed": 0,
                    "cancelled": 0
                }
                
                progress_percent = status_progress.get(refund.status, 0)
                
                # Estimate completion time based on status
                estimated_completion = None
                if refund.status in ["initiated", "validating"]:
                    estimated_completion = "1-2 minutes"
                elif refund.status == "processing":
                    estimated_completion = "30-60 seconds"
                elif refund.status == "completing":
                    estimated_completion = "Few seconds"
                
                return {
                    "refund_id": refund_id,
                    "status": refund.status,
                    "progress_percent": progress_percent,
                    "amount": float(refund.amount),
                    "currency": refund.currency,
                    "refund_type": refund.refund_type,
                    "created_at": refund.created_at.isoformat() if refund.created_at else None,
                    "completed_at": refund.completed_at.isoformat() if refund.completed_at else None,
                    "estimated_completion": estimated_completion,
                    "details": refund.error_message or "Processing normally",
                    "user_friendly_status": RefundStatusTracker._get_user_friendly_status(refund.status)
                }
                
        except Exception as e:
            logger.error(f"Error getting refund progress for {refund_id}: {e}")
            return {"error": str(e)}
    
    @staticmethod
    def _get_user_friendly_status(status: str) -> str:
        """Convert technical status to user-friendly message"""
        status_messages = {
            "initiated": "🔄 Your refund request has been received",
            "validating": "🔍 Verifying refund eligibility", 
            "processing": "⚡ Processing your refund",
            "completing": "✨ Finalizing transaction",
            "completed": "✅ Refund completed successfully",
            "failed": "❌ Refund failed - contact support",
            "cancelled": "🚫 Refund was cancelled"
        }
        return status_messages.get(status, "🔄 Processing refund")
    
    @staticmethod
    def get_user_refunds_summary(user_id: int, limit: int = 10) -> Dict[str, Any]:
        """Get summary of user's recent refunds with status"""
        try:
            with SessionLocal() as session:
                refunds = (
                    session.query(Refund)
                    .filter(Refund.user_id == user_id)
                    .order_by(Refund.created_at.desc())
                    .limit(limit)
                    .all()
                )
                
                refund_summaries = []
                for refund in refunds:
                    refund_summaries.append({
                        "refund_id": refund.refund_id,
                        "amount": float(refund.amount),
                        "status": refund.status,
                        "user_friendly_status": RefundStatusTracker._get_user_friendly_status(refund.status),
                        "refund_type": refund.refund_type,
                        "created_at": refund.created_at.isoformat() if refund.created_at else None,
                        "completed_at": refund.completed_at.isoformat() if refund.completed_at else None
                    })
                
                # Calculate stats
                total_refunds = len(refund_summaries)
                completed_refunds = len([r for r in refund_summaries if r["status"] == "completed"])
                pending_refunds = len([r for r in refund_summaries if r["status"] not in ["completed", "failed", "cancelled"]])
                
                return {
                    "user_id": user_id,
                    "total_refunds": total_refunds,
                    "completed_refunds": completed_refunds,
                    "pending_refunds": pending_refunds,
                    "success_rate": (completed_refunds / total_refunds * 100) if total_refunds > 0 else 0,
                    "refunds": refund_summaries
                }
                
        except Exception as e:
            logger.error(f"Error getting user refunds summary for {user_id}: {e}")
            return {"error": str(e)}


# Global tracker instance
refund_status_tracker = RefundStatusTracker()