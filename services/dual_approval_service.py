"""
Dual Approval Service for High-Value Cashouts
Implements multi-admin approval mechanism for enhanced security
"""

import logging
from decimal import Decimal
from typing import Dict, Any, List
from sqlalchemy import and_, or_

from models import Cashout, CashoutStatus
from config import Config
from database import SessionLocal

logger = logging.getLogger(__name__)


class DualApprovalService:
    """Service for managing dual approval requirements for first-time cashouts"""

    # Only first-time cashout requires dual approval
    FIRST_TIME_CASHOUT_REQUIRES_DUAL = getattr(
        Config, "FIRST_TIME_DUAL_APPROVAL", True
    )

    @classmethod
    def requires_dual_approval(
        cls, cashout_amount: Decimal, user_id: int, risk_score: float = 0.0
    ) -> Dict[str, Any]:
        """
        Determine if cashout requires dual approval (only first-time cashouts)
        Returns: {'required': bool, 'reasons': List[str], 'threshold_info': dict}
        """
        try:
            reasons = []
            required = False

            # Only check if first-time cashout
            if cls.FIRST_TIME_CASHOUT_REQUIRES_DUAL:
                with SessionLocal() as session:
                    # Check for both SUCCESS and COMPLETED statuses for backward compatibility
                    completed_cashouts = (
                        session.query(Cashout)
                        .filter(
                            and_(
                                Cashout.user_id == user_id,
                                or_(
                                    Cashout.status == CashoutStatus.SUCCESS.value,
                                    Cashout.status == CashoutStatus.COMPLETED.value
                                ),
                            )
                        )
                        .count()
                    )

                    if completed_cashouts == 0:
                        required = True
                        reasons.append("First-time cashout requires dual approval")

            return {
                "required": required,
                "reasons": reasons,
                "threshold_info": {
                    "first_time_check": cls.FIRST_TIME_CASHOUT_REQUIRES_DUAL,
                    "current_amount": float(cashout_amount),
                    "risk_score": risk_score,
                },
            }

        except Exception as e:
            logger.error(f"Error checking dual approval requirements: {e}")
            return {
                "required": True,
                "reasons": ["Error in approval check - defaulting to dual approval"],
            }

    @classmethod
    async def submit_for_approval(
        cls,
        cashout_id: str,
        first_approver_id: int,
        approval_reason: str,
        requires_dual: bool = True,
    ) -> Dict[str, Any]:
        """
        Submit cashout for admin approval
        Returns: {'success': bool, 'approval_id': str, 'next_step': str}
        """
        try:
            with SessionLocal() as session:
                cashout = (
                    session.query(Cashout)
                    .filter(Cashout.cashout_id == cashout_id)
                    .first()
                )

                if not cashout:
                    return {"success": False, "error": "Cashout not found"}

                if cashout.status != CashoutStatus.OTP_PENDING.value:
                    return {
                        "success": False,
                        "error": f"Invalid status for approval: {cashout.status}",
                    }

                # Create approval record
                # TEMP: Commented out until CashoutApproval model is properly defined in models.py
                # from models import (
                #     CashoutApproval,
                # )  # Import here to avoid circular dependency
                
                # approval = CashoutApproval(
                #     cashout_id=cashout_id,
                #     first_approver_id=first_approver_id,
                #     approval_reason=approval_reason,
                #     requires_dual_approval=requires_dual,
                #     first_approved_at=datetime.utcnow(),
                #     status="pending_second" if requires_dual else "approved",
                # )

                # session.add(approval)

                # TEMP: Dual approval functionality is disabled until CashoutApproval model is implemented
                logger.warning(
                    f"Dual approval not implemented - cashout {cashout_id} approval by admin {first_approver_id} cannot be processed"
                )
                
                return {
                    "success": False,
                    "error": "Dual approval functionality is temporarily disabled - CashoutApproval model not implemented",
                    "next_step": "Manual processing required"
                }

        except Exception as e:
            logger.error(f"Error submitting cashout for approval: {e}")
            return {"success": False, "error": f"Approval submission failed: {str(e)}"}

    @classmethod
    async def provide_second_approval(
        cls, cashout_id: str, second_approver_id: int, approval_reason: str
    ) -> Dict[str, Any]:
        """
        Provide second approval for dual approval workflow
        Returns: {'success': bool, 'fully_approved': bool}
        """
        # TEMP: Dual approval functionality is disabled until CashoutApproval model is implemented
        logger.warning(
            f"Second approval not implemented - cashout {cashout_id} second approval by admin {second_approver_id} cannot be processed"
        )
        
        return {
            "success": False,
            "error": "Dual approval functionality is temporarily disabled - CashoutApproval model not implemented",
            "fully_approved": False
        }

    @classmethod
    def get_pending_approvals(
        cls, admin_id: int = None, include_completed: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Get list of cashouts pending approval
        Returns: List of cashout approval records
        """
        # TEMP: Dual approval functionality is disabled until CashoutApproval model is implemented
        logger.warning(
            f"Get pending approvals not implemented - admin {admin_id} cannot view pending approvals"
        )
        
        # Return empty list since approval system is not implemented
        return []

    @classmethod
    async def reject_cashout(
        cls, cashout_id: str, admin_id: int, rejection_reason: str
    ) -> Dict[str, Any]:
        """
        Reject cashout and release locked funds
        Returns: {'success': bool, 'funds_released': bool}
        """
        try:
            # Use the atomic service to cancel the cashout
            from services.auto_cashout import AutoCashoutService

            result = await AutoCashoutService.cancel_cashout(
                cashout_id=cashout_id,
                reason=f"Admin rejection: {rejection_reason}",
            )

            if result["success"]:
                # TEMP: Approval system recording is disabled until CashoutApproval model is implemented
                logger.info(
                    f"Cashout {cashout_id} rejected by admin {admin_id}: {rejection_reason} (approval system disabled)"
                )

            return result

        except Exception as e:
            logger.error(f"Error rejecting cashout: {e}")
            return {"success": False, "error": f"Rejection failed: {str(e)}"}


# Add the new approval model to track dual approvals
class CashoutApproval:
    """Model for tracking cashout approvals (this would be added to models.py)"""

    # This is a reference implementation - would need to be added to models.py
    # __tablename__ = 'cashout_approvals'
    #
    # id = Column(Integer, primary_key=True)
    # cashout_id = Column(String(20), ForeignKey('cashouts.cashout_id'), nullable=False)
    #
    # # First approval
    # first_approver_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    # approval_reason = Column(Text, nullable=False)
    # first_approved_at = Column(DateTime, nullable=False)
    #
    # # Dual approval requirements
    # requires_dual_approval = Column(Boolean, default=False, nullable=False)
    #
    # # Second approval (if required)
    # second_approver_id = Column(Integer, ForeignKey('users.id'), nullable=True)
    # second_approval_reason = Column(Text, nullable=True)
    # second_approved_at = Column(DateTime, nullable=True)
    #
    # # Status tracking
    # status = Column(String(20), default='pending_second', nullable=False)  # pending_second, approved, rejected
    #
    # # Rejection handling
    # rejected_by = Column(Integer, ForeignKey('users.id'), nullable=True)
    # rejection_reason = Column(Text, nullable=True)
    # rejected_at = Column(DateTime, nullable=True)
    #
    # # Timestamps
    # created_at = Column(DateTime, default=func.now(), nullable=False)
    # updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)
    pass
