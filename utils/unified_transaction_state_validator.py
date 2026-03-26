"""
Unified Transaction State Transition Validator
==============================================

Prevents invalid state transitions and ensures unified transaction lifecycle integrity.
Validates all status changes across the 16-status unified transaction model.
"""

import logging
from typing import Dict, Set, Optional, Tuple
from models import UnifiedTransactionStatus

logger = logging.getLogger(__name__)


class StateTransitionError(Exception):
    """Raised when an invalid state transition is attempted"""
    pass


class UnifiedTransactionStateValidator:
    """
    Validates unified transaction state transitions across all 4 lifecycle phases.
    
    Prevents invalid transitions like:
    - SUCCESS -> PENDING (backwards transition)
    - COMPLETED -> AWAITING_PAYMENT (phase regression)
    - REFUNDED -> PROCESSING (resurrection)
    """
    
    # Define valid state transitions as a mapping across 4 phases
    VALID_TRANSITIONS: Dict[UnifiedTransactionStatus, Set[UnifiedTransactionStatus]] = {
        # === INITIATION PHASE ===
        
        # PENDING: Initial state
        UnifiedTransactionStatus.PENDING: {
            UnifiedTransactionStatus.AWAITING_PAYMENT,
            UnifiedTransactionStatus.PAYMENT_CONFIRMED,
            UnifiedTransactionStatus.FUNDS_HELD,
            UnifiedTransactionStatus.AWAITING_APPROVAL,
            UnifiedTransactionStatus.PROCESSING,
            UnifiedTransactionStatus.FAILED,
            UnifiedTransactionStatus.CANCELLED
        },
        
        # AWAITING_PAYMENT: Waiting for payment/deposit
        UnifiedTransactionStatus.AWAITING_PAYMENT: {
            UnifiedTransactionStatus.PARTIAL_PAYMENT,
            UnifiedTransactionStatus.PAYMENT_CONFIRMED,
            UnifiedTransactionStatus.EXPIRED,
            UnifiedTransactionStatus.CANCELLED,
            UnifiedTransactionStatus.FAILED
        },
        
        # PARTIAL_PAYMENT: Partial payment received
        UnifiedTransactionStatus.PARTIAL_PAYMENT: {
            UnifiedTransactionStatus.PAYMENT_CONFIRMED,
            UnifiedTransactionStatus.EXPIRED,
            UnifiedTransactionStatus.CANCELLED,
            UnifiedTransactionStatus.FAILED
        },
        
        # PAYMENT_CONFIRMED: Payment received and confirmed
        UnifiedTransactionStatus.PAYMENT_CONFIRMED: {
            UnifiedTransactionStatus.FUNDS_HELD,
            UnifiedTransactionStatus.AWAITING_APPROVAL,
            UnifiedTransactionStatus.OTP_PENDING,
            UnifiedTransactionStatus.ADMIN_PENDING,
            UnifiedTransactionStatus.PROCESSING,
            UnifiedTransactionStatus.CANCELLED,
            UnifiedTransactionStatus.FAILED
        },
        
        # === AUTHORIZATION PHASE ===
        
        # FUNDS_HELD: Funds secured in frozen balance
        UnifiedTransactionStatus.FUNDS_HELD: {
            UnifiedTransactionStatus.AWAITING_APPROVAL,
            UnifiedTransactionStatus.OTP_PENDING,
            UnifiedTransactionStatus.ADMIN_PENDING,
            UnifiedTransactionStatus.PROCESSING,
            UnifiedTransactionStatus.CANCELLED,
            UnifiedTransactionStatus.REFUNDED,
            UnifiedTransactionStatus.FAILED
        },
        
        # AWAITING_APPROVAL: Waiting for user/admin approval
        UnifiedTransactionStatus.AWAITING_APPROVAL: {
            UnifiedTransactionStatus.OTP_PENDING,
            UnifiedTransactionStatus.ADMIN_PENDING,
            UnifiedTransactionStatus.PROCESSING,
            UnifiedTransactionStatus.CANCELLED,
            UnifiedTransactionStatus.EXPIRED,
            UnifiedTransactionStatus.FAILED
        },
        
        # OTP_PENDING: OTP verification required
        UnifiedTransactionStatus.OTP_PENDING: {
            UnifiedTransactionStatus.ADMIN_PENDING,
            UnifiedTransactionStatus.PROCESSING,
            UnifiedTransactionStatus.CANCELLED,
            UnifiedTransactionStatus.EXPIRED,
            UnifiedTransactionStatus.FAILED
        },
        
        # ADMIN_PENDING: Admin review/funding required
        UnifiedTransactionStatus.ADMIN_PENDING: {
            UnifiedTransactionStatus.PROCESSING,
            UnifiedTransactionStatus.EXTERNAL_PENDING,
            UnifiedTransactionStatus.CANCELLED,
            UnifiedTransactionStatus.FAILED
        },
        
        # === EXECUTION PHASE ===
        
        # PROCESSING: System executing transaction
        UnifiedTransactionStatus.PROCESSING: {
            UnifiedTransactionStatus.EXTERNAL_PENDING,
            UnifiedTransactionStatus.AWAITING_RESPONSE,
            UnifiedTransactionStatus.RELEASE_PENDING,
            UnifiedTransactionStatus.FUNDS_RELEASED,
            UnifiedTransactionStatus.COMPLETED,
            UnifiedTransactionStatus.SUCCESS,
            UnifiedTransactionStatus.FAILED
        },
        
        # EXTERNAL_PENDING: Waiting for external API response
        UnifiedTransactionStatus.EXTERNAL_PENDING: {
            UnifiedTransactionStatus.AWAITING_RESPONSE,
            UnifiedTransactionStatus.PROCESSING,
            UnifiedTransactionStatus.RELEASE_PENDING,
            UnifiedTransactionStatus.FUNDS_RELEASED,
            UnifiedTransactionStatus.COMPLETED,
            UnifiedTransactionStatus.SUCCESS,
            UnifiedTransactionStatus.FAILED
        },
        
        # AWAITING_RESPONSE: Waiting for external system response
        UnifiedTransactionStatus.AWAITING_RESPONSE: {
            UnifiedTransactionStatus.PROCESSING,
            UnifiedTransactionStatus.RELEASE_PENDING,
            UnifiedTransactionStatus.FUNDS_RELEASED,
            UnifiedTransactionStatus.COMPLETED,
            UnifiedTransactionStatus.SUCCESS,
            UnifiedTransactionStatus.FAILED
        },
        
        # RELEASE_PENDING: Pending release to buyer/seller
        UnifiedTransactionStatus.RELEASE_PENDING: {
            UnifiedTransactionStatus.FUNDS_RELEASED,
            UnifiedTransactionStatus.COMPLETED,
            UnifiedTransactionStatus.SUCCESS,
            UnifiedTransactionStatus.DELIVERED,
            UnifiedTransactionStatus.DISPUTED,
            UnifiedTransactionStatus.FAILED
        },
        
        # FUNDS_RELEASED: Funds moved to recipient
        UnifiedTransactionStatus.FUNDS_RELEASED: {
            UnifiedTransactionStatus.COMPLETED,
            UnifiedTransactionStatus.SUCCESS,
            UnifiedTransactionStatus.DELIVERED,
            UnifiedTransactionStatus.DISPUTED
        },
        
        # COMPLETED: Transaction successfully finished
        UnifiedTransactionStatus.COMPLETED: {
            UnifiedTransactionStatus.SUCCESS,
            UnifiedTransactionStatus.DELIVERED,
            UnifiedTransactionStatus.DISPUTED  # Allow disputes on completed transactions
        },
        
        # SUCCESS: Transaction success confirmation
        UnifiedTransactionStatus.SUCCESS: {
            UnifiedTransactionStatus.DELIVERED,
            UnifiedTransactionStatus.DISPUTED  # Allow disputes on successful transactions
        },
        
        # DELIVERED: Final confirmation of delivery
        UnifiedTransactionStatus.DELIVERED: {
            UnifiedTransactionStatus.DISPUTED  # Only disputes can reopen delivered transactions
        },
        
        # === TERMINAL PHASE ===
        
        # FAILED: Transaction failed (TERMINAL STATE)
        UnifiedTransactionStatus.FAILED: {
            UnifiedTransactionStatus.PENDING,  # Allow retry from failed state
            UnifiedTransactionStatus.CANCELLED,
            UnifiedTransactionStatus.REFUNDED
        },
        
        # CANCELLED: User/admin cancelled (TERMINAL STATE)
        UnifiedTransactionStatus.CANCELLED: {
            UnifiedTransactionStatus.REFUNDED  # Allow refund of cancelled transactions
        },
        
        # DISPUTED: Under dispute resolution
        UnifiedTransactionStatus.DISPUTED: {
            UnifiedTransactionStatus.COMPLETED,
            UnifiedTransactionStatus.SUCCESS,
            UnifiedTransactionStatus.REFUNDED,
            UnifiedTransactionStatus.CANCELLED
        },
        
        # REFUNDED: Funds returned to sender (TERMINAL STATE)
        UnifiedTransactionStatus.REFUNDED: set(),  # Terminal state, no transitions allowed
        
        # EXPIRED: Transaction expired due to timeout (TERMINAL STATE)
        UnifiedTransactionStatus.EXPIRED: {
            UnifiedTransactionStatus.CANCELLED,
            UnifiedTransactionStatus.REFUNDED
        }
    }
    
    # Terminal states that generally don't allow transitions
    TERMINAL_STATES: Set[UnifiedTransactionStatus] = {
        UnifiedTransactionStatus.REFUNDED
    }
    
    # States that indicate funds are involved
    FUNDS_INVOLVED_STATES: Set[UnifiedTransactionStatus] = {
        UnifiedTransactionStatus.PAYMENT_CONFIRMED,
        UnifiedTransactionStatus.FUNDS_HELD,
        UnifiedTransactionStatus.PROCESSING,
        UnifiedTransactionStatus.EXTERNAL_PENDING,
        UnifiedTransactionStatus.AWAITING_RESPONSE,
        UnifiedTransactionStatus.RELEASE_PENDING,
        UnifiedTransactionStatus.FUNDS_RELEASED
    }
    
    # States requiring user interaction
    USER_INTERACTION_STATES: Set[UnifiedTransactionStatus] = {
        UnifiedTransactionStatus.AWAITING_PAYMENT,
        UnifiedTransactionStatus.AWAITING_APPROVAL,
        UnifiedTransactionStatus.OTP_PENDING
    }
    
    @classmethod
    def validate_transition(
        cls, 
        from_status: UnifiedTransactionStatus, 
        to_status: UnifiedTransactionStatus,
        transaction_id: Optional[str] = None,
        force: bool = False
    ) -> Tuple[bool, str]:
        """
        Validate if a state transition is allowed.
        
        Args:
            from_status: Current unified transaction status
            to_status: Desired new status
            transaction_id: Transaction ID for logging (optional)
            force: Admin force override (use with extreme caution)
            
        Returns:
            Tuple[bool, str]: (is_valid, reason)
        """
        transaction_ref = f"UnifiedTransaction {transaction_id}" if transaction_id else "UnifiedTransaction"
        
        # Admin force override (log heavily for audit)
        if force:
            logger.critical(
                f"🚨 FORCED_TRANSITION: {transaction_ref} {from_status.value} -> {to_status.value} "
                f"(ADMIN OVERRIDE - BYPASSED VALIDATION)"
            )
            return True, "Admin force override applied"
        
        # Same status (no-op)
        if from_status == to_status:
            return True, "No status change required"
        
        # Check if transition is in valid transitions map
        valid_next_states = cls.VALID_TRANSITIONS.get(from_status, set())
        
        if to_status in valid_next_states:
            logger.info(
                f"✅ VALID_TRANSITION: {transaction_ref} {from_status.value} -> {to_status.value}"
            )
            return True, "Valid state transition"
        
        # Invalid transition
        error_msg = (
            f"Invalid transition: {from_status.value} -> {to_status.value}. "
            f"Valid transitions from {from_status.value}: "
            f"{[s.value for s in valid_next_states]}"
        )
        
        logger.error(
            f"❌ INVALID_TRANSITION: {transaction_ref} {from_status.value} -> {to_status.value} "
            f"Valid options: {[s.value for s in valid_next_states]}"
        )
        
        return False, error_msg
    
    @classmethod
    def validate_and_transition(
        cls,
        unified_transaction,
        new_status: UnifiedTransactionStatus,
        transaction_id: Optional[str] = None,
        force: bool = False
    ) -> bool:
        """
        Validate and apply state transition to a unified transaction object.
        
        Args:
            unified_transaction: UnifiedTransaction database object
            new_status: Desired new status
            transaction_id: Transaction ID for logging (optional)
            force: Admin force override
            
        Returns:
            bool: True if transition was applied, False if invalid
            
        Raises:
            StateTransitionError: If transition is invalid and force=False
        """
        current_status = UnifiedTransactionStatus(unified_transaction.status) if isinstance(unified_transaction.status, str) else unified_transaction.status
        
        is_valid, reason = cls.validate_transition(
            current_status, new_status, transaction_id, force
        )
        
        if is_valid:
            # Apply the transition
            unified_transaction.status = new_status.value if hasattr(new_status, 'value') else new_status
            logger.info(
                f"🔄 STATUS_UPDATED: {transaction_id or 'UnifiedTransaction'} "
                f"{current_status.value} -> {new_status.value}"
            )
            return True
        else:
            # Invalid transition
            error_msg = f"State transition validation failed: {reason}"
            logger.error(f"❌ TRANSITION_BLOCKED: {error_msg}")
            
            if not force:
                raise StateTransitionError(error_msg)
            
            return False
    
    @classmethod
    def get_valid_next_states(cls, current_status: UnifiedTransactionStatus) -> Set[UnifiedTransactionStatus]:
        """Get all valid next states from the current status"""
        return cls.VALID_TRANSITIONS.get(current_status, set())
    
    @classmethod
    def is_terminal_state(cls, status: UnifiedTransactionStatus) -> bool:
        """Check if the status is a terminal state"""
        return status in cls.TERMINAL_STATES
    
    @classmethod
    def has_funds_involved(cls, status: UnifiedTransactionStatus) -> bool:
        """Check if the status indicates funds are involved"""
        return status in cls.FUNDS_INVOLVED_STATES
    
    @classmethod
    def requires_user_interaction(cls, status: UnifiedTransactionStatus) -> bool:
        """Check if the status requires user interaction"""
        return status in cls.USER_INTERACTION_STATES
    
    @classmethod
    def log_invalid_transition_attempt(
        cls,
        from_status: UnifiedTransactionStatus,
        to_status: UnifiedTransactionStatus,
        transaction_id: Optional[str] = None,
        context: Optional[str] = None
    ) -> None:
        """Log an invalid transition attempt for monitoring and debugging"""
        transaction_ref = f"UnifiedTransaction {transaction_id}" if transaction_id else "UnifiedTransaction"
        context_info = f" Context: {context}" if context else ""
        
        logger.error(
            f"🚨 INVALID_TRANSITION_ATTEMPT: {transaction_ref} tried to transition "
            f"{from_status.value} -> {to_status.value}.{context_info}"
        )
        
        # Log valid options for debugging
        valid_options = cls.get_valid_next_states(from_status)
        logger.info(
            f"💡 VALID_OPTIONS: From {from_status.value}, valid transitions are: "
            f"{[s.value for s in valid_options]}"
        )