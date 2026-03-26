"""
Transaction State Transition Validator
======================================

Prevents invalid state transitions and ensures transaction lifecycle integrity.
Validates all status changes to prevent invalid transitions like CONFIRMED -> PENDING.
"""

import logging
from typing import Dict, Set, Optional, Tuple
from models import TransactionStatus

logger = logging.getLogger(__name__)


class StateTransitionError(Exception):
    """Raised when an invalid state transition is attempted"""
    pass


class TransactionStateValidator:
    """
    Validates transaction state transitions to ensure business logic integrity.
    
    Prevents invalid transitions like:
    - CONFIRMED -> PENDING (backwards transition)
    - CANCELLED -> CONFIRMED (resurrection)
    - FAILED -> PENDING (without proper retry flow)
    """
    
    # Define valid state transitions as a mapping
    VALID_TRANSITIONS: Dict[TransactionStatus, Set[TransactionStatus]] = {
        # PENDING: Initial state, can progress or fail
        TransactionStatus.PENDING: {
            TransactionStatus.CONFIRMED,
            TransactionStatus.FAILED,
            TransactionStatus.CANCELLED
        },
        
        # CONFIRMED: Transaction confirmed (TERMINAL STATE)
        TransactionStatus.CONFIRMED: set(),  # Terminal state, no transitions allowed
        
        # FAILED: Transaction failed (TERMINAL STATE)
        TransactionStatus.FAILED: {
            TransactionStatus.PENDING,  # Allow retry from failed state
            TransactionStatus.CANCELLED
        },
        
        # CANCELLED: Transaction cancelled (TERMINAL STATE)
        TransactionStatus.CANCELLED: set()  # Terminal state, no transitions allowed
    }
    
    # Terminal states that generally don't allow transitions
    TERMINAL_STATES: Set[TransactionStatus] = {
        TransactionStatus.CONFIRMED,
        TransactionStatus.CANCELLED
    }
    
    # States that indicate transaction is in progress
    ACTIVE_STATES: Set[TransactionStatus] = {
        TransactionStatus.PENDING
    }
    
    @classmethod
    def validate_transition(
        cls, 
        from_status: TransactionStatus, 
        to_status: TransactionStatus,
        transaction_id: Optional[str] = None,
        force: bool = False
    ) -> Tuple[bool, str]:
        """
        Validate if a state transition is allowed.
        
        Args:
            from_status: Current transaction status
            to_status: Desired new status
            transaction_id: Transaction ID for logging (optional)
            force: Admin force override (use with extreme caution)
            
        Returns:
            Tuple[bool, str]: (is_valid, reason)
        """
        transaction_ref = f"Transaction {transaction_id}" if transaction_id else "Transaction"
        
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
        transaction,
        new_status: TransactionStatus,
        transaction_id: Optional[str] = None,
        force: bool = False
    ) -> bool:
        """
        Validate and apply state transition to a transaction object.
        
        Args:
            transaction: Transaction database object
            new_status: Desired new status
            transaction_id: Transaction ID for logging (optional)
            force: Admin force override
            
        Returns:
            bool: True if transition was applied, False if invalid
            
        Raises:
            StateTransitionError: If transition is invalid and force=False
        """
        current_status = TransactionStatus(transaction.status) if isinstance(transaction.status, str) else transaction.status
        
        is_valid, reason = cls.validate_transition(
            current_status, new_status, transaction_id, force
        )
        
        if is_valid:
            # Apply the transition
            transaction.status = new_status.value if hasattr(new_status, 'value') else new_status
            logger.info(
                f"🔄 STATUS_UPDATED: {transaction_id or 'Transaction'} "
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
    def get_valid_next_states(cls, current_status: TransactionStatus) -> Set[TransactionStatus]:
        """Get all valid next states from the current status"""
        return cls.VALID_TRANSITIONS.get(current_status, set())
    
    @classmethod
    def is_terminal_state(cls, status: TransactionStatus) -> bool:
        """Check if the status is a terminal state"""
        return status in cls.TERMINAL_STATES
    
    @classmethod
    def is_active_state(cls, status: TransactionStatus) -> bool:
        """Check if the status indicates an active transaction"""
        return status in cls.ACTIVE_STATES
    
    @classmethod
    def log_invalid_transition_attempt(
        cls,
        from_status: TransactionStatus,
        to_status: TransactionStatus,
        transaction_id: Optional[str] = None,
        context: Optional[str] = None
    ) -> None:
        """Log an invalid transition attempt for monitoring and debugging"""
        transaction_ref = f"Transaction {transaction_id}" if transaction_id else "Transaction"
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