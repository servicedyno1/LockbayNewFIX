"""
Refund State Transition Validator
=================================

Prevents invalid state transitions and ensures refund lifecycle integrity.
Validates all status changes to prevent invalid transitions like COMPLETED -> PENDING.
"""

import logging
from typing import Dict, Set, Optional, Tuple
from models import RefundStatus

logger = logging.getLogger(__name__)


class StateTransitionError(Exception):
    """Raised when an invalid state transition is attempted"""
    pass


class RefundStateValidator:
    """
    Validates refund state transitions to ensure business logic integrity.
    
    Prevents invalid transitions like:
    - COMPLETED -> PENDING (backwards transition)
    - FAILED -> COMPLETED (skipping resolution)
    """
    
    # Define valid state transitions as a mapping
    VALID_TRANSITIONS: Dict[RefundStatus, Set[RefundStatus]] = {
        # PENDING: Initial state, can progress to completion or failure
        RefundStatus.PENDING: {
            RefundStatus.COMPLETED,
            RefundStatus.FAILED
        },
        
        # COMPLETED: Refund completed successfully (TERMINAL STATE)
        RefundStatus.COMPLETED: set(),  # Terminal state, no transitions allowed
        
        # FAILED: Refund failed (TERMINAL STATE)
        RefundStatus.FAILED: {
            RefundStatus.PENDING  # Allow retry from failed state
        }
    }
    
    # Terminal states that generally don't allow transitions
    TERMINAL_STATES: Set[RefundStatus] = {
        RefundStatus.COMPLETED
    }
    
    # States that indicate refund is in progress
    ACTIVE_STATES: Set[RefundStatus] = {
        RefundStatus.PENDING
    }
    
    @classmethod
    def validate_transition(
        cls, 
        from_status: RefundStatus, 
        to_status: RefundStatus,
        refund_id: Optional[str] = None,
        force: bool = False
    ) -> Tuple[bool, str]:
        """
        Validate if a state transition is allowed.
        
        Args:
            from_status: Current refund status
            to_status: Desired new status
            refund_id: Refund ID for logging (optional)
            force: Admin force override (use with extreme caution)
            
        Returns:
            Tuple[bool, str]: (is_valid, reason)
        """
        refund_ref = f"Refund {refund_id}" if refund_id else "Refund"
        
        # Admin force override (log heavily for audit)
        if force:
            logger.critical(
                f"🚨 FORCED_TRANSITION: {refund_ref} {from_status.value} -> {to_status.value} "
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
                f"✅ VALID_TRANSITION: {refund_ref} {from_status.value} -> {to_status.value}"
            )
            return True, "Valid state transition"
        
        # Invalid transition
        error_msg = (
            f"Invalid transition: {from_status.value} -> {to_status.value}. "
            f"Valid transitions from {from_status.value}: "
            f"{[s.value for s in valid_next_states]}"
        )
        
        logger.error(
            f"❌ INVALID_TRANSITION: {refund_ref} {from_status.value} -> {to_status.value} "
            f"Valid options: {[s.value for s in valid_next_states]}"
        )
        
        return False, error_msg
    
    @classmethod
    def validate_and_transition(
        cls,
        refund,
        new_status: RefundStatus,
        refund_id: Optional[str] = None,
        force: bool = False
    ) -> bool:
        """
        Validate and apply state transition to a refund object.
        
        Args:
            refund: Refund database object
            new_status: Desired new status
            refund_id: Refund ID for logging (optional)
            force: Admin force override
            
        Returns:
            bool: True if transition was applied, False if invalid
            
        Raises:
            StateTransitionError: If transition is invalid and force=False
        """
        current_status = RefundStatus(refund.status) if isinstance(refund.status, str) else refund.status
        
        is_valid, reason = cls.validate_transition(
            current_status, new_status, refund_id, force
        )
        
        if is_valid:
            # Apply the transition
            refund.status = new_status.value if hasattr(new_status, 'value') else new_status
            logger.info(
                f"🔄 STATUS_UPDATED: {refund_id or 'Refund'} "
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
    def get_valid_next_states(cls, current_status: RefundStatus) -> Set[RefundStatus]:
        """Get all valid next states from the current status"""
        return cls.VALID_TRANSITIONS.get(current_status, set())
    
    @classmethod
    def is_terminal_state(cls, status: RefundStatus) -> bool:
        """Check if the status is a terminal state"""
        return status in cls.TERMINAL_STATES
    
    @classmethod
    def is_active_state(cls, status: RefundStatus) -> bool:
        """Check if the status indicates an active refund"""
        return status in cls.ACTIVE_STATES
    
    @classmethod
    def log_invalid_transition_attempt(
        cls,
        from_status: RefundStatus,
        to_status: RefundStatus,
        refund_id: Optional[str] = None,
        context: Optional[str] = None
    ) -> None:
        """Log an invalid transition attempt for monitoring and debugging"""
        refund_ref = f"Refund {refund_id}" if refund_id else "Refund"
        context_info = f" Context: {context}" if context else ""
        
        logger.error(
            f"🚨 INVALID_TRANSITION_ATTEMPT: {refund_ref} tried to transition "
            f"{from_status.value} -> {to_status.value}.{context_info}"
        )
        
        # Log valid options for debugging
        valid_options = cls.get_valid_next_states(from_status)
        logger.info(
            f"💡 VALID_OPTIONS: From {from_status.value}, valid transitions are: "
            f"{[s.value for s in valid_options]}"
        )