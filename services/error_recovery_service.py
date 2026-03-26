"""
Error Recovery Service - Comprehensive error handling and rollback mechanisms
Implements proper rollback for partial failures in cashout processing
"""

import logging
from datetime import datetime
from decimal import Decimal
from typing import Dict, Any
from sqlalchemy import and_

from models import (
    Wallet,
    Cashout,
    CashoutStatus,
    # CashoutApproval,  # TEMP: Commented out until model is properly defined
    Transaction,
    EmailVerification,
    UnifiedTransactionStatus,
)
from utils.cashout_state_validator import CashoutStateValidator
from utils.unified_transaction_state_validator import (
    UnifiedTransactionStateValidator,
    StateTransitionError
)
from database import SessionLocal

logger = logging.getLogger(__name__)


class ErrorRecoveryService:
    """Service for handling errors and implementing rollback mechanisms"""

    @classmethod
    async def rollback_failed_cashout(
        cls, cashout_id: str, failure_reason: str, rollback_funds: bool = True
    ) -> Dict[str, Any]:
        """
        Comprehensive rollback for failed cashout
        Returns: {'success': bool, 'rollback_actions': list, 'error': str}
        """
        rollback_actions = []

        try:
            with SessionLocal() as session:
                # Begin transaction
                session.begin()

                # Get cashout record
                cashout = (
                    session.query(Cashout)
                    .filter(Cashout.cashout_id == cashout_id)
                    .first()
                )

                if not cashout:
                    return {
                        "success": False,
                        "rollback_actions": [],
                        "error": "Cashout not found",
                    }

                # Step 1: Update cashout status using validated transition
                original_status = cashout.status
                CashoutStateValidator.validate_and_transition(
                    cashout,
                    CashoutStatus.FAILED,
                    cashout_id=cashout_id,
                    force=False
                )
                cashout.failed_at = datetime.utcnow()
                cashout.error_message = failure_reason
                rollback_actions.append(
                    f"Updated cashout status from {original_status} to failed"
                )

                # Step 2: Rollback locked funds if requested
                # CRITICAL FIX: Use original_status before state transition, not current status
                if rollback_funds and original_status in [
                    CashoutStatus.PENDING.value,
                    CashoutStatus.OTP_PENDING.value,
                    CashoutStatus.ADMIN_PENDING.value,
                    CashoutStatus.EXECUTING.value,
                    CashoutStatus.AWAITING_RESPONSE.value,
                ]:
                    wallet = (
                        session.query(Wallet)
                        .filter(
                            and_(
                                Wallet.user_id == cashout.user_id,
                                Wallet.currency == cashout.currency,
                            )
                        )
                        .first()
                    )

                    if wallet:
                        original_locked = wallet.locked_balance
                        wallet.locked_balance = max(
                            Decimal("0"), wallet.locked_balance - cashout.amount
                        )
                        rollback_actions.append(
                            f"Released locked funds: {cashout.amount} {cashout.currency} "
                            f"(locked balance: {original_locked} -> {wallet.locked_balance})"
                        )

                # Step 3: Update related transaction record with validation
                transaction = (
                    session.query(Transaction)
                    .filter(Transaction.tx_hash == cashout_id)
                    .first()
                )

                if transaction:
                    # Validate state transition to FAILED
                    try:
                        current_status = UnifiedTransactionStatus(transaction.status)
                        is_valid, reason = UnifiedTransactionStateValidator.validate_transition(
                            current_status,
                            UnifiedTransactionStatus.FAILED,
                            transaction_id=str(transaction.id)
                        )
                        
                        if is_valid:
                            transaction.status = "failed"
                            transaction.description = f"Failed: {failure_reason}"
                            rollback_actions.append(
                                "Updated transaction record to failed status"
                            )
                        else:
                            logger.warning(
                                f"🚫 RECOVERY_TX_FAIL_BLOCKED: {current_status.value}→FAILED "
                                f"for transaction {transaction.id}: {reason} (transaction already in terminal state)"
                            )
                            rollback_actions.append(
                                f"Transaction already in terminal state {current_status.value} - skipped status update"
                            )
                    except StateTransitionError as e:
                        logger.warning(
                            f"🚫 RECOVERY_TX_FAIL_BLOCKED: Invalid transition for "
                            f"transaction {transaction.id}: {e} (transaction already in terminal state)"
                        )
                        rollback_actions.append(
                            f"Transaction already in terminal state - skipped status update: {e}"
                        )
                    except Exception as e:
                        logger.error(
                            f"🚫 RECOVERY_TX_VALIDATION_ERROR: Error validating transition for "
                            f"transaction {transaction.id}: {e}"
                        )
                        # Fallback for unknown status values (legacy compatibility)
                        transaction.status = "failed"
                        transaction.description = f"Failed: {failure_reason}"
                        rollback_actions.append(
                            "Updated transaction record to failed status (validation skipped for legacy status)"
                        )

                # Step 4: Invalidate any pending OTP verification
                otp_verification = (
                    session.query(EmailVerification)
                    .filter(
                        and_(
                            EmailVerification.cashout_id == cashout_id,
                            not EmailVerification.verified,
                        )
                    )
                    .first()
                )

                if otp_verification:
                    otp_verification.verified = False  # Mark as invalid
                    # Note: Don't delete, keep for audit trail
                    rollback_actions.append("Invalidated pending OTP verification")

                # Step 5: Update approval record if exists
                # TEMP: Commented out until CashoutApproval model is properly defined in models.py
                # approval = (
                #     session.query(CashoutApproval)
                #     .filter(CashoutApproval.cashout_id == cashout_id)
                #     .first()
                # )
                #
                # if approval and approval.status != "rejected":
                #     approval.status = "failed"
                #     approval.rejection_reason = f"System failure: {failure_reason}"
                #     approval.rejected_at = datetime.utcnow()
                #     rollback_actions.append("Updated approval record to failed status")

                # Step 6: Log error recovery event
                from models import AuditLog

                audit_log = AuditLog(
                    user_id=cashout.user_id,
                    event_type="cashout_rollback",
                    event_category="error_recovery",
                    entity_type="cashout",
                    entity_id=cashout_id,
                    description=f"Cashout rollback: {failure_reason}",
                    after_data={
                        "rollback_actions": rollback_actions,
                        "failure_reason": failure_reason,
                        "funds_released": rollback_funds,
                    },
                    severity="medium",
                )
                session.add(audit_log)
                rollback_actions.append("Created audit log entry")

                # Commit all changes
                session.commit()

                logger.info(
                    f"Successfully rolled back cashout {cashout_id}: {failure_reason}"
                )

                return {
                    "success": True,
                    "rollback_actions": rollback_actions,
                    "funds_released": rollback_funds,
                    "cashout_status": cashout.status,
                }

        except Exception as e:
            logger.error(f"Error during cashout rollback {cashout_id}: {e}")
            try:
                session.rollback()
            except Exception as rollback_error:
                logger.error(f"Session rollback failed: {rollback_error}")
                pass

            return {
                "success": False,
                "rollback_actions": rollback_actions,
                "error": f"Rollback failed: {str(e)}",
            }

    @classmethod
    async def recover_stuck_cashouts(cls, max_age_hours: int = 24) -> Dict[str, Any]:
        """
        Recover cashouts stuck in processing states
        Returns: {'recovered': int, 'failed': int, 'details': list}
        """
        recovered = 0
        failed = 0
        details = []

        try:
            with SessionLocal() as session:
                # Find stuck cashouts
                from datetime import timedelta

                cutoff_time = datetime.utcnow() - timedelta(hours=max_age_hours)

                stuck_cashouts = (
                    session.query(Cashout)
                    .filter(
                        and_(
                            Cashout.status.in_(
                                [
                                    # Only include system processing states that can get stuck
                                    CashoutStatus.EXECUTING.value,
                                    CashoutStatus.AWAITING_RESPONSE.value,
                                    # REMOVED: OTP_PENDING - this is user action state, not system failure
                                ]
                            ),
                            Cashout.created_at < cutoff_time,
                        )
                    )
                    .all()
                )

                logger.info(
                    f"Found {len(stuck_cashouts)} stuck cashouts older than {max_age_hours} hours"
                )

                for cashout in stuck_cashouts:
                    try:
                        # Attempt recovery based on status
                        if cashout.status in [CashoutStatus.EXECUTING.value, CashoutStatus.AWAITING_RESPONSE.value]:
                            # Check if external transaction exists
                            if cashout.external_tx_id:
                                # Cashout was submitted to external provider
                                # Mark as failed and investigate manually
                                reason = f"Stuck in executing state for {max_age_hours}+ hours with external ID {cashout.external_tx_id}"
                            else:
                                # Cashout never reached external provider
                                reason = f"Stuck in executing state for {max_age_hours}+ hours without external submission"

                        # REMOVED: OTP_PENDING handling - no longer processed as stuck cashout

                        else:
                            reason = f"Generic timeout after {max_age_hours}+ hours"

                        # Perform rollback
                        rollback_result = await cls.rollback_failed_cashout(
                            cashout.cashout_id, reason, rollback_funds=True
                        )

                        if rollback_result["success"]:
                            recovered += 1
                            details.append(
                                {
                                    "cashout_id": cashout.cashout_id,
                                    "status": "recovered",
                                    "reason": reason,
                                    "actions": rollback_result["rollback_actions"],
                                }
                            )
                        else:
                            failed += 1
                            details.append(
                                {
                                    "cashout_id": cashout.cashout_id,
                                    "status": "recovery_failed",
                                    "reason": reason,
                                    "error": rollback_result["error"],
                                }
                            )

                    except Exception as e:
                        failed += 1
                        logger.error(
                            f"Error recovering cashout {cashout.cashout_id}: {e}"
                        )
                        details.append(
                            {
                                "cashout_id": cashout.cashout_id,
                                "status": "recovery_error",
                                "error": str(e),
                            }
                        )

                logger.info(
                    f"Cashout recovery completed: {recovered} recovered, {failed} failed"
                )

                return {
                    "recovered": recovered,
                    "failed": failed,
                    "details": details,
                    "total_stuck": len(stuck_cashouts),
                }

        except Exception as e:
            logger.error(f"Error in stuck cashout recovery: {e}")
            return {
                "recovered": recovered,
                "failed": failed,
                "error": str(e),
                "details": details,
            }

    @classmethod
    async def validate_wallet_integrity(cls, user_id: int = None) -> Dict[str, Any]:
        """
        Validate wallet balance integrity and fix inconsistencies
        Returns: {'valid': bool, 'issues': list, 'fixes_applied': list}
        """
        issues = []
        fixes_applied = []

        try:
            with SessionLocal() as session:
                # Query wallets
                if user_id:
                    # Get USD wallet for the user
                    wallets = (
                        session.query(Wallet).filter(Wallet.user_id == user_id, Wallet.currency == "USD").all()
                    )
                else:
                    # Limit to first 100 USD wallets for safety
                    wallets = session.query(Wallet).filter(Wallet.currency == "USD").limit(100).all()

                for wallet in wallets:
                    # Check 1: Balance consistency
                    if wallet.available_balance < 0:
                        issues.append(
                            f"Negative balance in wallet {wallet.id}: {wallet.available_balance}"
                        )

                    if wallet.frozen_balance < 0:
                        issues.append(
                            f"Negative frozen balance in wallet {wallet.id}: {wallet.frozen_balance}"
                        )
                        # Fix negative frozen balance
                        wallet.frozen_balance = Decimal("0")
                        fixes_applied.append(
                            f"Reset negative frozen balance to 0 for wallet {wallet.id}"
                        )

                    if wallet.locked_balance < 0:
                        issues.append(
                            f"Negative locked balance in wallet {wallet.id}: {wallet.locked_balance}"
                        )
                        # Fix negative locked balance
                        wallet.locked_balance = Decimal("0")
                        fixes_applied.append(
                            f"Reset negative locked balance to 0 for wallet {wallet.id}"
                        )

                    # Check 2: Frozen + locked > balance
                    total_reserved = wallet.frozen_balance + wallet.locked_balance
                    if total_reserved > wallet.available_balance:
                        issues.append(
                            f"Reserved funds exceed balance in wallet {wallet.id}: "
                            f"reserved {total_reserved} > balance {wallet.available_balance}"
                        )
                        # Don't auto-fix this as it indicates a serious problem

                    # Check 3: Validate against pending cashouts
                    pending_cashouts = (
                        session.query(Cashout)
                        .filter(
                            and_(
                                Cashout.user_id == wallet.user_id,
                                Cashout.currency == wallet.currency,
                                Cashout.status.in_(
                                    [
                                        CashoutStatus.PENDING.value,
                                        CashoutStatus.OTP_PENDING.value,
                                        CashoutStatus.ADMIN_PENDING.value,
                                        CashoutStatus.EXECUTING.value,
                                        CashoutStatus.AWAITING_RESPONSE.value,
                                    ]
                                ),
                            )
                        )
                        .all()
                    )

                    expected_locked = sum(w.amount for w in pending_cashouts)
                    if abs(wallet.locked_balance - expected_locked) > Decimal(
                        "0.01"
                    ):  # Allow for rounding
                        issues.append(
                            f"Locked balance mismatch in wallet {wallet.id}: "
                            f"wallet shows {wallet.locked_balance}, "
                            f"pending cashouts total {expected_locked}"
                        )

                # Commit any fixes
                if fixes_applied:
                    session.commit()
                    logger.info(f"Applied {len(fixes_applied)} wallet integrity fixes")

                return {
                    "valid": len(issues) == 0,
                    "issues": issues,
                    "fixes_applied": fixes_applied,
                    "wallets_checked": len(wallets),
                }

        except Exception as e:
            logger.error(f"Error validating wallet integrity: {e}")
            return {
                "valid": False,
                "error": str(e),
                "issues": issues,
                "fixes_applied": fixes_applied,
            }

    @classmethod
    async def emergency_shutdown_procedure(
        cls, reason: str, admin_id: int = None
    ) -> Dict[str, Any]:
        """
        Emergency shutdown procedure for critical system failures
        Returns: {'shutdown_actions': list, 'success': bool}
        """
        shutdown_actions = []

        try:
            with SessionLocal() as session:
                # 1. Cancel all pending cashouts in unsafe states
                unsafe_cashouts = (
                    session.query(Cashout)
                    .filter(
                        Cashout.status.in_(
                            [
                                CashoutStatus.PENDING.value,
                                CashoutStatus.EXECUTING.value,
                                CashoutStatus.AWAITING_RESPONSE.value,
                            ]
                        )
                    )
                    .all()
                )

                for cashout in unsafe_cashouts:
                    rollback_result = await cls.rollback_failed_cashout(
                        cashout.cashout_id,
                        f"Emergency shutdown: {reason}",
                        rollback_funds=True,
                    )
                    if rollback_result["success"]:
                        shutdown_actions.append(
                            f"Emergency cancelled cashout {cashout.cashout_id}"
                        )

                # 2. Log emergency event
                from models import AuditLog

                emergency_log = AuditLog(
                    user_id=admin_id,
                    event_type="emergency_shutdown",
                    event_category="system",
                    entity_type="system",
                    entity_id="emergency",
                    description=f"Emergency shutdown initiated: {reason}",
                    after_data={
                        "reason": reason,
                        "shutdown_actions": shutdown_actions,
                        "admin_id": admin_id,
                    },
                    severity="critical",
                )
                session.add(emergency_log)
                shutdown_actions.append("Logged emergency shutdown event")

                session.commit()

                logger.critical(f"Emergency shutdown completed: {reason}")

                return {
                    "shutdown_actions": shutdown_actions,
                    "success": True,
                    "reason": reason,
                }

        except Exception as e:
            logger.error(f"Error during emergency shutdown: {e}")
            return {
                "shutdown_actions": shutdown_actions,
                "success": False,
                "error": str(e),
            }
