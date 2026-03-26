"""
Exchange State Transition Validator
===================================

Prevents invalid state transitions and ensures exchange lifecycle integrity.
Validates all status changes to prevent invalid transitions like COMPLETED -> CREATED.
"""

import logging
from typing import Dict, Set, Optional, Tuple
from models import ExchangeStatus

logger = logging.getLogger(__name__)


class StateTransitionError(Exception):
    """Raised when an invalid state transition is attempted"""
    pass


class ExchangeStateValidator:
    """
    Validates exchange state transitions to ensure business logic integrity.
    
    Prevents invalid transitions like:
    - COMPLETED -> CREATED (backwards transition)
    - CANCELLED -> PROCESSING (resurrection)
    - FAILED -> AWAITING_DEPOSIT (skipping recovery)
    """
    
    # Define valid state transitions as a mapping
    VALID_TRANSITIONS: Dict[ExchangeStatus, Set[ExchangeStatus]] = {
        # CREATED: Initial state, can go to deposit states or cancellation
        ExchangeStatus.CREATED: {
            ExchangeStatus.AWAITING_DEPOSIT,
            ExchangeStatus.PENDING_APPROVAL,
            ExchangeStatus.CANCELLED,
            ExchangeStatus.ADDRESS_GENERATION_FAILED
        },
        
        # AWAITING_DEPOSIT: Waiting for user deposit
        ExchangeStatus.AWAITING_DEPOSIT: {
            ExchangeStatus.RATE_LOCKED,
            ExchangeStatus.PAYMENT_RECEIVED,
            ExchangeStatus.CANCELLED,
            ExchangeStatus.FAILED
        },
        
        # RATE_LOCKED: Exchange rate locked, awaiting payment
        ExchangeStatus.RATE_LOCKED: {
            ExchangeStatus.PAYMENT_RECEIVED,
            ExchangeStatus.PAYMENT_CONFIRMED,
            ExchangeStatus.CANCELLED,
            ExchangeStatus.FAILED
        },
        
        # PAYMENT_RECEIVED: Payment received but not confirmed
        ExchangeStatus.PAYMENT_RECEIVED: {
            ExchangeStatus.PAYMENT_CONFIRMED,
            ExchangeStatus.PROCESSING,
            ExchangeStatus.FAILED
        },
        
        # PAYMENT_CONFIRMED: Payment confirmed, ready for processing
        ExchangeStatus.PAYMENT_CONFIRMED: {
            ExchangeStatus.PROCESSING,
            ExchangeStatus.COMPLETED,
            ExchangeStatus.FAILED
        },
        
        # PENDING_APPROVAL: Waiting for admin approval
        ExchangeStatus.PENDING_APPROVAL: {
            ExchangeStatus.AWAITING_DEPOSIT,
            ExchangeStatus.PROCESSING,
            ExchangeStatus.CANCELLED,
            ExchangeStatus.FAILED
        },
        
        # PROCESSING: Exchange being processed
        ExchangeStatus.PROCESSING: {
            ExchangeStatus.COMPLETED,
            ExchangeStatus.FAILED
        },
        
        # COMPLETED: Exchange successfully completed (TERMINAL STATE)
        ExchangeStatus.COMPLETED: set(),  # Terminal state, no transitions allowed
        
        # FAILED: Exchange failed (TERMINAL STATE)
        ExchangeStatus.FAILED: {
            ExchangeStatus.AWAITING_DEPOSIT,  # Allow retry from failed state
            ExchangeStatus.CANCELLED
        },
        
        # CANCELLED: Exchange cancelled (TERMINAL STATE)
        ExchangeStatus.CANCELLED: set(),  # Terminal state, no transitions allowed
        
        # ADDRESS_GENERATION_FAILED: Address generation failed
        ExchangeStatus.ADDRESS_GENERATION_FAILED: {
            ExchangeStatus.AWAITING_DEPOSIT,  # Retry after fixing address generation
            ExchangeStatus.CANCELLED,
            ExchangeStatus.FAILED
        }
    }
    
    # Terminal states that generally don't allow transitions
    TERMINAL_STATES: Set[ExchangeStatus] = {
        ExchangeStatus.COMPLETED,
        ExchangeStatus.CANCELLED
    }
    
    # States that indicate funds are involved and require careful handling
    FUNDS_INVOLVED_STATES: Set[ExchangeStatus] = {
        ExchangeStatus.PAYMENT_RECEIVED,
        ExchangeStatus.PAYMENT_CONFIRMED,
        ExchangeStatus.PROCESSING
    }
    
    @classmethod
    def validate_transition(
        cls, 
        from_status: ExchangeStatus, 
        to_status: ExchangeStatus,
        exchange_id: Optional[str] = None,
        force: bool = False
    ) -> Tuple[bool, str]:
        """
        Validate if a state transition is allowed.
        
        Args:
            from_status: Current exchange status
            to_status: Desired new status
            exchange_id: Exchange ID for logging (optional)
            force: Admin force override (use with extreme caution)
            
        Returns:
            Tuple[bool, str]: (is_valid, reason)
        """
        exchange_ref = f"Exchange {exchange_id}" if exchange_id else "Exchange"
        
        # Admin force override (log heavily for audit)
        if force:
            logger.critical(
                f"🚨 FORCED_TRANSITION: {exchange_ref} {from_status.value} -> {to_status.value} "
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
                f"✅ VALID_TRANSITION: {exchange_ref} {from_status.value} -> {to_status.value}"
            )
            return True, "Valid state transition"
        
        # Invalid transition
        error_msg = (
            f"Invalid transition: {from_status.value} -> {to_status.value}. "
            f"Valid transitions from {from_status.value}: "
            f"{[s.value for s in valid_next_states]}"
        )
        
        logger.error(
            f"❌ INVALID_TRANSITION: {exchange_ref} {from_status.value} -> {to_status.value} "
            f"Valid options: {[s.value for s in valid_next_states]}"
        )
        
        return False, error_msg
    
    @classmethod
    def validate_and_transition(
        cls,
        exchange,
        new_status: ExchangeStatus,
        exchange_id: Optional[str] = None,
        force: bool = False
    ) -> bool:
        """
        Validate and apply state transition to an exchange object.
        
        Args:
            exchange: Exchange database object
            new_status: Desired new status
            exchange_id: Exchange ID for logging (optional)
            force: Admin force override
            
        Returns:
            bool: True if transition was applied, False if invalid
            
        Raises:
            StateTransitionError: If transition is invalid and force=False
        """
        current_status = ExchangeStatus(exchange.status) if isinstance(exchange.status, str) else exchange.status
        
        is_valid, reason = cls.validate_transition(
            current_status, new_status, exchange_id, force
        )
        
        if is_valid:
            # Apply the transition
            exchange.status = new_status.value if hasattr(new_status, 'value') else new_status
            logger.info(
                f"🔄 STATUS_UPDATED: {exchange_id or 'Exchange'} "
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
    def get_valid_next_states(cls, current_status: ExchangeStatus) -> Set[ExchangeStatus]:
        """Get all valid next states from the current status"""
        return cls.VALID_TRANSITIONS.get(current_status, set())
    
    @classmethod
    def is_terminal_state(cls, status: ExchangeStatus) -> bool:
        """Check if the status is a terminal state"""
        return status in cls.TERMINAL_STATES
    
    @classmethod
    def has_funds_involved(cls, status: ExchangeStatus) -> bool:
        """Check if the status indicates funds are involved in the exchange"""
        return status in cls.FUNDS_INVOLVED_STATES
    
    @classmethod
    def log_invalid_transition_attempt(
        cls,
        from_status: ExchangeStatus,
        to_status: ExchangeStatus,
        exchange_id: Optional[str] = None,
        context: Optional[str] = None
    ) -> None:
        """Log an invalid transition attempt for monitoring and debugging"""
        exchange_ref = f"Exchange {exchange_id}" if exchange_id else "Exchange"
        context_info = f" Context: {context}" if context else ""
        
        logger.error(
            f"🚨 INVALID_TRANSITION_ATTEMPT: {exchange_ref} tried to transition "
            f"{from_status.value} -> {to_status.value}.{context_info}"
        )
        
        # Log valid options for debugging
        valid_options = cls.get_valid_next_states(from_status)
        logger.info(
            f"💡 VALID_OPTIONS: From {from_status.value}, valid transitions are: "
            f"{[s.value for s in valid_options]}"
        )