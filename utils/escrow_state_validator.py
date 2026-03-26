"""
Escrow State Transition Validator
================================

Prevents invalid state transitions and ensures escrow lifecycle integrity.
Validates all status changes to prevent invalid transitions like COMPLETED -> CREATED.
"""

import logging
from typing import Dict, Set, Optional, Tuple
from models import EscrowStatus

logger = logging.getLogger(__name__)


class StateTransitionError(Exception):
    """Raised when an invalid state transition is attempted"""
    pass


class EscrowStateValidator:
    """
    Validates escrow state transitions to ensure business logic integrity.
    
    Prevents invalid transitions like:
    - COMPLETED -> CREATED (backwards transition)
    - DISPUTED -> PAYMENT_PENDING (skipping resolution)
    - CANCELLED -> ACTIVE (resurrection)
    """
    
    # Define valid state transitions as a mapping
    VALID_TRANSITIONS: Dict[EscrowStatus, Set[EscrowStatus]] = {
        # CREATED: Initial state, can go to payment states or cancellation
        EscrowStatus.CREATED: {
            EscrowStatus.PAYMENT_PENDING,
            EscrowStatus.PENDING_DEPOSIT,
            EscrowStatus.AWAITING_SELLER,
            EscrowStatus.CANCELLED,
            EscrowStatus.EXPIRED
        },
        
        # PAYMENT_PENDING: Waiting for buyer payment
        EscrowStatus.PAYMENT_PENDING: {
            EscrowStatus.PAYMENT_CONFIRMED,
            EscrowStatus.PARTIAL_PAYMENT,
            EscrowStatus.PAYMENT_FAILED,
            EscrowStatus.CANCELLED,
            EscrowStatus.EXPIRED
        },
        
        # PENDING_DEPOSIT: Waiting for cryptocurrency deposit
        EscrowStatus.PENDING_DEPOSIT: {
            EscrowStatus.PAYMENT_CONFIRMED,
            EscrowStatus.PARTIAL_PAYMENT,
            EscrowStatus.PAYMENT_FAILED,
            EscrowStatus.CANCELLED,
            EscrowStatus.EXPIRED
        },
        
        # PAYMENT_CONFIRMED: Payment received, waiting for seller
        EscrowStatus.PAYMENT_CONFIRMED: {
            EscrowStatus.AWAITING_SELLER,
            EscrowStatus.PENDING_SELLER,
            EscrowStatus.ACTIVE,
            EscrowStatus.DISPUTED,
            EscrowStatus.CANCELLED
        },
        
        # PARTIAL_PAYMENT: Partial payment received
        EscrowStatus.PARTIAL_PAYMENT: {
            EscrowStatus.PAYMENT_CONFIRMED,
            EscrowStatus.PAYMENT_FAILED,
            EscrowStatus.CANCELLED,
            EscrowStatus.EXPIRED
        },
        
        # PAYMENT_FAILED: Payment failed
        EscrowStatus.PAYMENT_FAILED: {
            EscrowStatus.PAYMENT_PENDING,
            EscrowStatus.PENDING_DEPOSIT,
            EscrowStatus.CANCELLED,
            EscrowStatus.REFUNDED
        },
        
        # AWAITING_SELLER: Waiting for seller response
        EscrowStatus.AWAITING_SELLER: {
            EscrowStatus.PENDING_SELLER,
            EscrowStatus.ACTIVE,
            EscrowStatus.CANCELLED,
            EscrowStatus.EXPIRED,
            EscrowStatus.REFUNDED
        },
        
        # PENDING_SELLER: Seller notified, waiting for acceptance
        EscrowStatus.PENDING_SELLER: {
            EscrowStatus.ACTIVE,
            EscrowStatus.CANCELLED,
            EscrowStatus.EXPIRED,
            EscrowStatus.REFUNDED
        },
        
        # ACTIVE: Trade is active, goods being delivered
        EscrowStatus.ACTIVE: {
            EscrowStatus.COMPLETED,
            EscrowStatus.DISPUTED,
            EscrowStatus.CANCELLED
        },
        
        # COMPLETED: Trade successfully completed (TERMINAL STATE)
        EscrowStatus.COMPLETED: {
            EscrowStatus.DISPUTED  # Only disputes can reopen completed trades
        },
        
        # DISPUTED: Trade under dispute resolution
        EscrowStatus.DISPUTED: {
            EscrowStatus.COMPLETED,
            EscrowStatus.REFUNDED,
            EscrowStatus.CANCELLED
        },
        
        # REFUNDED: Funds returned to buyer (TERMINAL STATE)
        EscrowStatus.REFUNDED: set(),  # Terminal state, no transitions allowed
        
        # CANCELLED: Trade cancelled (TERMINAL STATE)
        EscrowStatus.CANCELLED: set(),  # Terminal state, no transitions allowed
        
        # EXPIRED: Trade expired (TERMINAL STATE)
        EscrowStatus.EXPIRED: {
            EscrowStatus.REFUNDED  # Can refund expired trades
        }
    }
    
    # Terminal states that generally don't allow transitions
    TERMINAL_STATES: Set[EscrowStatus] = {
        EscrowStatus.COMPLETED,
        EscrowStatus.REFUNDED,
        EscrowStatus.CANCELLED
    }
    
    # States that indicate funds are held and require careful handling
    FUNDS_HELD_STATES: Set[EscrowStatus] = {
        EscrowStatus.PAYMENT_CONFIRMED,
        EscrowStatus.AWAITING_SELLER,
        EscrowStatus.PENDING_SELLER,
        EscrowStatus.ACTIVE,
        EscrowStatus.DISPUTED
    }
    
    def is_valid_transition(
        self,
        from_status: str,
        to_status: str
    ) -> bool:
        """
        Convenience instance method to check if a transition is valid.
        Returns just a boolean for simple validation checks.
        
        Args:
            from_status: Current escrow status (string value)
            to_status: Desired new status (string value)
            
        Returns:
            bool: True if transition is valid, False otherwise
        """
        try:
            # Convert string values to EscrowStatus enums
            from_enum = EscrowStatus(from_status) if isinstance(from_status, str) else from_status
            to_enum = EscrowStatus(to_status) if isinstance(to_status, str) else to_status
            
            # Use the classmethod validate_transition
            is_valid, _ = self.validate_transition(from_enum, to_enum)
            return is_valid
        except (ValueError, KeyError):
            # Invalid status value
            return False
    
    @classmethod
    def validate_transition(
        cls, 
        from_status: EscrowStatus, 
        to_status: EscrowStatus,
        escrow_id: Optional[str] = None,
        force: bool = False
    ) -> Tuple[bool, str]:
        """
        Validate if a state transition is allowed.
        
        Args:
            from_status: Current escrow status
            to_status: Desired new status
            escrow_id: Escrow ID for logging (optional)
            force: Admin force override (use with extreme caution)
            
        Returns:
            Tuple[bool, str]: (is_valid, reason)
        """
        escrow_ref = f"Escrow {escrow_id}" if escrow_id else "Escrow"
        
        # Admin force override (log heavily for audit)
        if force:
            logger.critical(
                f"🚨 FORCED_TRANSITION: {escrow_ref} {from_status.value} -> {to_status.value} "
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
                f"✅ VALID_TRANSITION: {escrow_ref} {from_status.value} -> {to_status.value}"
            )
            return True, "Valid state transition"
        
        # Invalid transition
        error_msg = (
            f"Invalid transition: {from_status.value} -> {to_status.value}. "
            f"Valid transitions from {from_status.value}: "
            f"{[s.value for s in valid_next_states]}"
        )
        
        logger.error(
            f"❌ INVALID_TRANSITION: {escrow_ref} {from_status.value} -> {to_status.value} "
            f"Valid options: {[s.value for s in valid_next_states]}"
        )
        
        return False, error_msg
    
    @classmethod
    def validate_and_transition(
        cls,
        escrow,
        new_status: EscrowStatus,
        escrow_id: Optional[str] = None,
        force: bool = False
    ) -> bool:
        """
        Validate and apply state transition to an escrow object.
        
        Args:
            escrow: Escrow database object
            new_status: Desired new status
            escrow_id: Escrow ID for logging (optional)
            force: Admin force override
            
        Returns:
            bool: True if transition was applied, False if invalid
            
        Raises:
            StateTransitionError: If transition is invalid and force=False
        """
        current_status = EscrowStatus(escrow.status) if isinstance(escrow.status, str) else escrow.status
        
        is_valid, reason = cls.validate_transition(
            current_status, new_status, escrow_id, force
        )
        
        if is_valid:
            # Apply the transition
            escrow.status = new_status.value if hasattr(new_status, 'value') else new_status
            logger.info(
                f"🔄 STATUS_UPDATED: {escrow_id or 'Escrow'} "
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
    def get_valid_next_states(cls, current_status: EscrowStatus) -> Set[EscrowStatus]:
        """Get all valid next states from the current status"""
        return cls.VALID_TRANSITIONS.get(current_status, set())
    
    @classmethod
    def is_terminal_state(cls, status: EscrowStatus) -> bool:
        """Check if the status is a terminal state"""
        return status in cls.TERMINAL_STATES
    
    @classmethod
    def has_funds_held(cls, status: EscrowStatus) -> bool:
        """Check if the status indicates funds are held in escrow"""
        return status in cls.FUNDS_HELD_STATES
    
    @classmethod
    def log_invalid_transition_attempt(
        cls,
        from_status: EscrowStatus,
        to_status: EscrowStatus,
        escrow_id: Optional[str] = None,
        context: Optional[str] = None
    ) -> None:
        """Log an invalid transition attempt for monitoring and debugging"""
        escrow_ref = f"Escrow {escrow_id}" if escrow_id else "Escrow"
        context_info = f" Context: {context}" if context else ""
        
        logger.error(
            f"🚨 INVALID_TRANSITION_ATTEMPT: {escrow_ref} tried to transition "
            f"{from_status.value} -> {to_status.value}.{context_info}"
        )
        
        # Log valid options for debugging
        valid_options = cls.get_valid_next_states(from_status)
        logger.info(
            f"💡 VALID_OPTIONS: From {from_status.value}, valid transitions are: "
            f"{[s.value for s in valid_options]}"
        )