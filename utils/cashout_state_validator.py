"""
Cashout State Transition Validator
==================================

Prevents invalid state transitions and ensures cashout lifecycle integrity.
Validates all status changes to prevent invalid transitions like SUCCESS -> PENDING.
"""

import logging
from typing import Dict, Set, Optional, Tuple
from models import CashoutStatus

logger = logging.getLogger(__name__)


class StateTransitionError(Exception):
    """Raised when an invalid state transition is attempted"""
    pass


class CashoutStateValidator:
    """
    Validates cashout state transitions to ensure business logic integrity.
    
    Prevents invalid transitions like:
    - SUCCESS -> PENDING (backwards transition)
    - CANCELLED -> PROCESSING (resurrection)
    - COMPLETED -> OTP_PENDING (backwards flow)
    """
    
    # Define valid state transitions as a mapping
    VALID_TRANSITIONS: Dict[CashoutStatus, Set[CashoutStatus]] = {
        # PENDING: Initial state, can go to approval flows
        CashoutStatus.PENDING: {
            CashoutStatus.OTP_PENDING,
            CashoutStatus.USER_CONFIRM_PENDING,
            CashoutStatus.ADMIN_PENDING,
            CashoutStatus.APPROVED,
            CashoutStatus.CANCELLED,
            CashoutStatus.FAILED
        },
        
        # OTP_PENDING: Waiting for user OTP verification
        CashoutStatus.OTP_PENDING: {
            CashoutStatus.USER_CONFIRM_PENDING,
            CashoutStatus.ADMIN_PENDING,
            CashoutStatus.APPROVED,
            CashoutStatus.CANCELLED,
            CashoutStatus.EXPIRED,
            CashoutStatus.FAILED
        },
        
        # USER_CONFIRM_PENDING: Waiting for user confirmation
        CashoutStatus.USER_CONFIRM_PENDING: {
            CashoutStatus.ADMIN_PENDING,
            CashoutStatus.APPROVED,
            CashoutStatus.CANCELLED,
            CashoutStatus.EXPIRED,
            CashoutStatus.FAILED
        },
        
        # ADMIN_PENDING: Waiting for admin approval
        CashoutStatus.ADMIN_PENDING: {
            CashoutStatus.APPROVED,
            CashoutStatus.ADMIN_APPROVED,
            CashoutStatus.PENDING_ADDRESS_CONFIG,
            CashoutStatus.CANCELLED,
            CashoutStatus.FAILED
        },
        
        # APPROVED: User approved, ready for processing
        CashoutStatus.APPROVED: {
            CashoutStatus.ADMIN_APPROVED,
            CashoutStatus.PENDING_ADDRESS_CONFIG,
            CashoutStatus.PENDING_SERVICE_FUNDING,
            CashoutStatus.EXECUTING,
            CashoutStatus.PROCESSING,
            CashoutStatus.CANCELLED,
            CashoutStatus.FAILED
        },
        
        # ADMIN_APPROVED: Admin approved, ready for execution
        CashoutStatus.ADMIN_APPROVED: {
            CashoutStatus.PENDING_ADDRESS_CONFIG,
            CashoutStatus.PENDING_SERVICE_FUNDING,
            CashoutStatus.EXECUTING,
            CashoutStatus.PROCESSING,
            CashoutStatus.CANCELLED,
            CashoutStatus.FAILED
        },
        
        # AWAITING_RESPONSE: Waiting for external system response
        CashoutStatus.AWAITING_RESPONSE: {
            CashoutStatus.PENDING_SERVICE_FUNDING,
            CashoutStatus.EXECUTING,
            CashoutStatus.PROCESSING,
            CashoutStatus.COMPLETED,
            CashoutStatus.SUCCESS,
            CashoutStatus.FAILED
        },
        
        # PENDING_ADDRESS_CONFIG: Waiting for address configuration
        CashoutStatus.PENDING_ADDRESS_CONFIG: {
            CashoutStatus.PENDING_SERVICE_FUNDING,
            CashoutStatus.EXECUTING,
            CashoutStatus.PROCESSING,
            CashoutStatus.CANCELLED,
            CashoutStatus.FAILED
        },
        
        # PENDING_SERVICE_FUNDING: Waiting for service funding
        CashoutStatus.PENDING_SERVICE_FUNDING: {
            CashoutStatus.EXECUTING,
            CashoutStatus.PROCESSING,
            CashoutStatus.AWAITING_RESPONSE,
            CashoutStatus.CANCELLED,
            CashoutStatus.FAILED
        },
        
        # EXECUTING: Cashout being executed
        CashoutStatus.EXECUTING: {
            CashoutStatus.PROCESSING,
            CashoutStatus.AWAITING_RESPONSE,
            CashoutStatus.COMPLETED,
            CashoutStatus.SUCCESS,
            CashoutStatus.FAILED
        },
        
        # PROCESSING: Cashout being processed
        CashoutStatus.PROCESSING: {
            CashoutStatus.AWAITING_RESPONSE,
            CashoutStatus.COMPLETED,
            CashoutStatus.SUCCESS,
            CashoutStatus.FAILED
        },
        
        # COMPLETED: Cashout completed successfully
        CashoutStatus.COMPLETED: {
            CashoutStatus.SUCCESS  # Allow transition to SUCCESS for final confirmation
        },
        
        # SUCCESS: Cashout successful (TERMINAL STATE)
        CashoutStatus.SUCCESS: set(),  # Terminal state, no transitions allowed
        
        # FAILED: Cashout failed (TERMINAL STATE)
        CashoutStatus.FAILED: {
            CashoutStatus.PENDING,  # Allow retry from failed state
            CashoutStatus.CANCELLED
        },
        
        # EXPIRED: Cashout expired
        CashoutStatus.EXPIRED: {
            CashoutStatus.CANCELLED,
            CashoutStatus.FAILED
        },
        
        # CANCELLED: Cashout cancelled (TERMINAL STATE)
        CashoutStatus.CANCELLED: set()  # Terminal state, no transitions allowed
    }
    
    # Terminal states that generally don't allow transitions
    TERMINAL_STATES: Set[CashoutStatus] = {
        CashoutStatus.SUCCESS,
        CashoutStatus.CANCELLED
    }
    
    # States that indicate funds are locked and require careful handling
    FUNDS_LOCKED_STATES: Set[CashoutStatus] = {
        CashoutStatus.APPROVED,
        CashoutStatus.ADMIN_APPROVED,
        CashoutStatus.EXECUTING,
        CashoutStatus.PROCESSING,
        CashoutStatus.AWAITING_RESPONSE
    }
    
    # States requiring OTP verification
    OTP_STATES: Set[CashoutStatus] = {
        CashoutStatus.OTP_PENDING
    }
    
    @classmethod
    def validate_transition(
        cls, 
        from_status: CashoutStatus, 
        to_status: CashoutStatus,
        cashout_id: Optional[str] = None,
        force: bool = False
    ) -> Tuple[bool, str]:
        """
        Validate if a state transition is allowed.
        
        Args:
            from_status: Current cashout status
            to_status: Desired new status
            cashout_id: Cashout ID for logging (optional)
            force: Admin force override (use with extreme caution)
            
        Returns:
            Tuple[bool, str]: (is_valid, reason)
        """
        cashout_ref = f"Cashout {cashout_id}" if cashout_id else "Cashout"
        
        # Admin force override (log heavily for audit)
        if force:
            logger.critical(
                f"🚨 FORCED_TRANSITION: {cashout_ref} {from_status.value} -> {to_status.value} "
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
                f"✅ VALID_TRANSITION: {cashout_ref} {from_status.value} -> {to_status.value}"
            )
            return True, "Valid state transition"
        
        # Invalid transition
        error_msg = (
            f"Invalid transition: {from_status.value} -> {to_status.value}. "
            f"Valid transitions from {from_status.value}: "
            f"{[s.value for s in valid_next_states]}"
        )
        
        logger.error(
            f"❌ INVALID_TRANSITION: {cashout_ref} {from_status.value} -> {to_status.value} "
            f"Valid options: {[s.value for s in valid_next_states]}"
        )
        
        return False, error_msg
    
    @classmethod
    def validate_and_transition(
        cls,
        cashout,
        new_status: CashoutStatus,
        cashout_id: Optional[str] = None,
        force: bool = False
    ) -> bool:
        """
        Validate and apply state transition to a cashout object.
        
        Args:
            cashout: Cashout database object
            new_status: Desired new status
            cashout_id: Cashout ID for logging (optional)
            force: Admin force override
            
        Returns:
            bool: True if transition was applied, False if invalid
            
        Raises:
            StateTransitionError: If transition is invalid and force=False
        """
        current_status = CashoutStatus(cashout.status) if isinstance(cashout.status, str) else cashout.status
        
        is_valid, reason = cls.validate_transition(
            current_status, new_status, cashout_id, force
        )
        
        if is_valid:
            # Apply the transition
            cashout.status = new_status.value if hasattr(new_status, 'value') else new_status
            logger.info(
                f"🔄 STATUS_UPDATED: {cashout_id or 'Cashout'} "
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
    def get_valid_next_states(cls, current_status: CashoutStatus) -> Set[CashoutStatus]:
        """Get all valid next states from the current status"""
        return cls.VALID_TRANSITIONS.get(current_status, set())
    
    @classmethod
    def is_terminal_state(cls, status: CashoutStatus) -> bool:
        """Check if the status is a terminal state"""
        return status in cls.TERMINAL_STATES
    
    @classmethod
    def has_funds_locked(cls, status: CashoutStatus) -> bool:
        """Check if the status indicates funds are locked"""
        return status in cls.FUNDS_LOCKED_STATES
    
    @classmethod
    def requires_otp(cls, status: CashoutStatus) -> bool:
        """Check if the status requires OTP verification"""
        return status in cls.OTP_STATES
    
    @classmethod
    def log_invalid_transition_attempt(
        cls,
        from_status: CashoutStatus,
        to_status: CashoutStatus,
        cashout_id: Optional[str] = None,
        context: Optional[str] = None
    ) -> None:
        """Log an invalid transition attempt for monitoring and debugging"""
        cashout_ref = f"Cashout {cashout_id}" if cashout_id else "Cashout"
        context_info = f" Context: {context}" if context else ""
        
        logger.error(
            f"🚨 INVALID_TRANSITION_ATTEMPT: {cashout_ref} tried to transition "
            f"{from_status.value} -> {to_status.value}.{context_info}"
        )
        
        # Log valid options for debugging
        valid_options = cls.get_valid_next_states(from_status)
        logger.info(
            f"💡 VALID_OPTIONS: From {from_status.value}, valid transitions are: "
            f"{[s.value for s in valid_options]}"
        )