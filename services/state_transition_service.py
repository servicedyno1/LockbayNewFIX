"""
State Transition Service
========================

Centralized service for validating and executing state transitions across all entity types.
This service provides a unified interface for state validation, eliminating duplicated
validator wiring and ensuring consistency across the platform.

## Architecture

**Validator Registry Pattern**: Maps entity types to their validators:
- escrow → EscrowStateValidator
- exchange → ExchangeStateValidator  
- cashout → CashoutStateValidator
- unified_transaction → UnifiedTransactionStateValidator
- user → Gracefully handled (basic validation)
- dispute → Gracefully handled (basic validation)

## Benefits

1. **Single Point of Change**: All validation logic centralized
2. **Consistent Interface**: Same API for all entity types
3. **Better Testability**: Mock single service instead of multiple validators
4. **Extensibility**: Easy to add new entity types or features
5. **Context Awareness**: Different error handling for webhooks, admin, automated flows

## Usage Examples

### Basic Validation
```python
from services.state_transition_service import StateTransitionService
from models import ExchangeStatus

# Simple validation check
is_valid = await StateTransitionService.transition_entity_status(
    entity_type="exchange",
    entity_id="EX123456",
    current_status=ExchangeStatus.AWAITING_DEPOSIT,
    new_status=ExchangeStatus.COMPLETED,
    context="WEBHOOK_PROCESSING"
)

if is_valid:
    exchange.status = ExchangeStatus.COMPLETED.value
    await session.commit()
else:
    logger.warning("Transition blocked, see logs")
```

### Pre-flight Validation
```python
# Validate without executing (for pre-checks)
can_transition = StateTransitionService.validate_transition_only(
    entity_type="escrow",
    entity_id="ES123456",
    current_status=EscrowStatus.ACTIVE,
    new_status=EscrowStatus.COMPLETED
)

if can_transition:
    # Proceed with business logic
    pass
```

### Error Handling
```python
from utils.escrow_state_validator import StateTransitionError

try:
    await StateTransitionService.transition_entity_status(
        entity_type="cashout",
        entity_id="CO123456",
        current_status=CashoutStatus.SUCCESS,
        new_status=CashoutStatus.PENDING,  # Invalid: backwards transition
        context="ADMIN_RETRY"
    )
except StateTransitionError as e:
    logger.error(f"Invalid transition: {e}")
    # Handle error gracefully
```

## Migration Guide

### Before (Current Pattern)
```python
try:
    current_status = ExchangeStatus(exchange.status)
    ExchangeStateValidator.validate_transition(current_status, new_status, exchange_id)
    exchange.status = new_status.value
except Exception as e:
    logger.error(f"🚫 TRANSITION_ERROR: {e}")
```

### After (With Service)
```python
if await StateTransitionService.transition_entity_status(
    entity_type="exchange",
    entity_id=exchange_id,
    current_status=ExchangeStatus(exchange.status),
    new_status=new_status,
    context="FINCRA_WEBHOOK"
):
    exchange.status = new_status.value
else:
    logger.warning("Transition validation failed")
```

## Future Extensibility

This service is designed for future enhancements:
- Database audit logging of all transitions
- Webhook notifications on state changes
- Metrics collection for transition patterns
- Rate limiting for specific transitions
- Business rule enforcement (time-based, user-based)
"""

import logging
from typing import Optional, Dict, Type
from enum import Enum

# Import validators
from utils.escrow_state_validator import EscrowStateValidator, StateTransitionError
from utils.exchange_state_validator import ExchangeStateValidator
from utils.cashout_state_validator import CashoutStateValidator
from utils.unified_transaction_state_validator import UnifiedTransactionStateValidator

# Import enums
from models import (
    UserStatus,
    DisputeStatus
)

logger = logging.getLogger(__name__)


class StateTransitionService:
    """
    Centralized service for validating and executing state transitions.
    
    This service acts as a facade over individual entity validators, providing:
    - Unified interface for all entity types
    - Consistent error handling and logging
    - Context-aware validation
    - Graceful handling of entities without formal validators
    
    The service does NOT modify entity state directly - callers are responsible
    for applying the validated transition to their entities.
    """
    
    # Validator Registry: Maps entity types to their validator classes
    VALIDATOR_REGISTRY: Dict[str, Type] = {
        "escrow": EscrowStateValidator,
        "exchange": ExchangeStateValidator,
        "cashout": CashoutStateValidator,
        "unified_transaction": UnifiedTransactionStateValidator,
        # Note: User and Dispute don't have formal state machines yet
        # They are handled with basic validation in _validate_simple_transition
    }
    
    # Parameter name mapping for validators (each validator uses different parameter names)
    VALIDATOR_PARAM_NAMES: Dict[str, str] = {
        "escrow": "escrow_id",
        "exchange": "exchange_id",
        "cashout": "cashout_id",
        "unified_transaction": "transaction_id",  # Note: uses 'transaction_id' not 'unified_transaction_id'
    }
    
    # Entities without formal validators (basic validation only)
    SIMPLE_VALIDATION_ENTITIES = {"user", "dispute"}
    
    # Valid transitions for entities without formal validators
    SIMPLE_TRANSITIONS: Dict[str, Dict[Enum, set]] = {
        "user": {
            UserStatus.ACTIVE: {UserStatus.SUSPENDED, UserStatus.BANNED},
            UserStatus.SUSPENDED: {UserStatus.ACTIVE, UserStatus.BANNED},
            UserStatus.BANNED: set(),  # Terminal state
            UserStatus.PENDING_VERIFICATION: {UserStatus.ACTIVE, UserStatus.SUSPENDED}
        },
        "dispute": {
            DisputeStatus.OPEN: {DisputeStatus.UNDER_REVIEW, DisputeStatus.RESOLVED},
            DisputeStatus.UNDER_REVIEW: {DisputeStatus.RESOLVED, DisputeStatus.OPEN},
            DisputeStatus.RESOLVED: set()  # Terminal state
        }
    }
    
    @staticmethod
    async def transition_entity_status(
        entity_type: str,
        entity_id: str,
        current_status: Enum,
        new_status: Enum,
        context: Optional[str] = None,
        force: bool = False
    ) -> bool:
        """
        Validate and execute state transition for any entity type.
        
        This is the primary method for validating state transitions. It delegates
        to the appropriate validator based on entity_type and handles errors gracefully.
        
        Args:
            entity_type: Type of entity ("escrow", "exchange", "cashout", 
                        "unified_transaction", "user", "dispute")
            entity_id: Unique identifier for the entity (for logging)
            current_status: Current status enum value
            new_status: Desired new status enum value
            context: Context tag for logging (e.g., "WEBHOOK", "ADMIN_ACTION")
            force: Admin force override (use with extreme caution, logs heavily)
            
        Returns:
            bool: True if transition is valid and can proceed
                  False if transition is invalid (error is logged)
        
        Raises:
            ValueError: If entity_type is not recognized
            StateTransitionError: If transition is invalid and caller wants to catch it
        
        Example:
            >>> is_valid = await StateTransitionService.transition_entity_status(
            ...     entity_type="exchange",
            ...     entity_id="EX123456",
            ...     current_status=ExchangeStatus.AWAITING_DEPOSIT,
            ...     new_status=ExchangeStatus.COMPLETED,
            ...     context="WEBHOOK_PROCESSING"
            ... )
            >>> if is_valid:
            ...     exchange.status = new_status.value
        """
        entity_type = entity_type.lower()
        context_tag = f"[{context}]" if context else ""
        
        # Validate entity_type
        if entity_type not in StateTransitionService.VALIDATOR_REGISTRY and \
           entity_type not in StateTransitionService.SIMPLE_VALIDATION_ENTITIES:
            raise ValueError(
                f"Unknown entity_type: {entity_type}. "
                f"Valid types: {list(StateTransitionService.VALIDATOR_REGISTRY.keys())} "
                f"or {list(StateTransitionService.SIMPLE_VALIDATION_ENTITIES)}"
            )
        
        # Log the transition attempt
        logger.info(
            f"🔄 STATE_TRANSITION {context_tag}: {entity_type} {entity_id} "
            f"{current_status.value} → {new_status.value}"
        )
        
        try:
            # Route to appropriate validator
            if entity_type in StateTransitionService.VALIDATOR_REGISTRY:
                is_valid = await StateTransitionService._validate_with_validator(
                    entity_type=entity_type,
                    entity_id=entity_id,
                    current_status=current_status,
                    new_status=new_status,
                    force=force
                )
            else:
                # Simple validation for User/Dispute
                is_valid = StateTransitionService._validate_simple_transition(
                    entity_type=entity_type,
                    entity_id=entity_id,
                    current_status=current_status,
                    new_status=new_status
                )
            
            if is_valid:
                logger.info(
                    f"✅ TRANSITION_VALID {context_tag}: {entity_type} {entity_id} "
                    f"{current_status.value} → {new_status.value}"
                )
            else:
                logger.warning(
                    f"🚫 TRANSITION_BLOCKED {context_tag}: {entity_type} {entity_id} "
                    f"{current_status.value} → {new_status.value}"
                )
            
            return is_valid
            
        except StateTransitionError as e:
            logger.error(
                f"❌ TRANSITION_ERROR {context_tag}: {entity_type} {entity_id} "
                f"{current_status.value} → {new_status.value} - {str(e)}"
            )
            # Re-raise so caller can catch if needed
            raise
        except Exception as e:
            logger.error(
                f"💥 TRANSITION_EXCEPTION {context_tag}: {entity_type} {entity_id} "
                f"{current_status.value} → {new_status.value} - {str(e)}",
                exc_info=True
            )
            return False
    
    @staticmethod
    async def _validate_with_validator(
        entity_type: str,
        entity_id: str,
        current_status: Enum,
        new_status: Enum,
        force: bool = False
    ) -> bool:
        """
        Validate transition using formal validator class.
        
        Args:
            entity_type: Type of entity
            entity_id: Entity identifier
            current_status: Current status
            new_status: Desired new status
            force: Force override flag
            
        Returns:
            bool: True if valid, False otherwise
        """
        validator_class = StateTransitionService.VALIDATOR_REGISTRY[entity_type]
        param_name = StateTransitionService.VALIDATOR_PARAM_NAMES.get(entity_type)
        
        # Call validator's validate_transition method with correct parameter name
        is_valid, reason = validator_class.validate_transition(
            from_status=current_status,
            to_status=new_status,
            **{param_name: entity_id},  # Use mapped parameter name
            force=force
        )
        
        if not is_valid:
            logger.warning(
                f"Validator rejected: {entity_type} {entity_id} - {reason}"
            )
        
        return is_valid
    
    @staticmethod
    def _validate_simple_transition(
        entity_type: str,
        entity_id: str,
        current_status: Enum,
        new_status: Enum
    ) -> bool:
        """
        Basic validation for entities without formal state machines.
        
        Handles User and Dispute entities with simple transition rules.
        
        Args:
            entity_type: Type of entity (user or dispute)
            entity_id: Entity identifier
            current_status: Current status
            new_status: Desired new status
            
        Returns:
            bool: True if valid, False otherwise
        """
        # Same status (no-op)
        if current_status == new_status:
            logger.debug(f"No status change for {entity_type} {entity_id}")
            return True
        
        # Get valid transitions for this entity type
        transitions = StateTransitionService.SIMPLE_TRANSITIONS.get(entity_type, {})
        valid_next_states = transitions.get(current_status, set())
        
        if new_status in valid_next_states:
            return True
        
        logger.warning(
            f"Invalid simple transition for {entity_type} {entity_id}: "
            f"{current_status.value} → {new_status.value}. "
            f"Valid options: {[s.value for s in valid_next_states]}"
        )
        return False
    
    @staticmethod
    def validate_transition_only(
        entity_type: str,
        entity_id: str,
        current_status: Enum,
        new_status: Enum
    ) -> bool:
        """
        Validate transition without executing (for pre-flight checks).
        
        This is a synchronous method for quick validation checks without
        async overhead. Use this when you just need to know if a transition
        is valid before doing other operations.
        
        Args:
            entity_type: Type of entity
            entity_id: Entity identifier
            current_status: Current status
            new_status: Desired new status
            
        Returns:
            bool: True if transition would be valid, False otherwise
        
        Example:
            >>> can_complete = StateTransitionService.validate_transition_only(
            ...     entity_type="escrow",
            ...     entity_id="ES123456",
            ...     current_status=EscrowStatus.ACTIVE,
            ...     new_status=EscrowStatus.COMPLETED
            ... )
            >>> if can_complete:
            ...     # Show "Complete Trade" button
            ...     pass
        """
        entity_type = entity_type.lower()
        
        try:
            # Route to appropriate validator
            if entity_type in StateTransitionService.VALIDATOR_REGISTRY:
                validator_class = StateTransitionService.VALIDATOR_REGISTRY[entity_type]
                param_name = StateTransitionService.VALIDATOR_PARAM_NAMES.get(entity_type)
                is_valid, _ = validator_class.validate_transition(
                    from_status=current_status,
                    to_status=new_status,
                    **{param_name: entity_id},  # Use mapped parameter name
                    force=False
                )
                return is_valid
            elif entity_type in StateTransitionService.SIMPLE_VALIDATION_ENTITIES:
                return StateTransitionService._validate_simple_transition(
                    entity_type=entity_type,
                    entity_id=entity_id,
                    current_status=current_status,
                    new_status=new_status
                )
            else:
                logger.warning(f"Unknown entity_type for validation: {entity_type}")
                return False
        except Exception as e:
            logger.error(f"Validation error: {e}", exc_info=True)
            return False
    
    @staticmethod
    def get_valid_transitions(
        entity_type: str,
        current_status: Enum
    ) -> set:
        """
        Get all valid next states for a given entity and status.
        
        Useful for UI/UX to show valid actions or for business logic
        that needs to know what transitions are possible.
        
        Args:
            entity_type: Type of entity
            current_status: Current status
            
        Returns:
            set: Set of valid next status enum values
        
        Example:
            >>> valid_next = StateTransitionService.get_valid_transitions(
            ...     entity_type="exchange",
            ...     current_status=ExchangeStatus.AWAITING_DEPOSIT
            ... )
            >>> # Returns: {ExchangeStatus.RATE_LOCKED, ExchangeStatus.PAYMENT_RECEIVED, ...}
        """
        entity_type = entity_type.lower()
        
        try:
            if entity_type in StateTransitionService.VALIDATOR_REGISTRY:
                validator_class = StateTransitionService.VALIDATOR_REGISTRY[entity_type]
                return validator_class.VALID_TRANSITIONS.get(current_status, set())
            elif entity_type in StateTransitionService.SIMPLE_VALIDATION_ENTITIES:
                transitions = StateTransitionService.SIMPLE_TRANSITIONS.get(entity_type, {})
                return transitions.get(current_status, set())
            else:
                logger.warning(f"Unknown entity_type: {entity_type}")
                return set()
        except Exception as e:
            logger.error(f"Error getting valid transitions: {e}", exc_info=True)
            return set()
    
    @staticmethod
    def is_terminal_state(
        entity_type: str,
        status: Enum
    ) -> bool:
        """
        Check if a status is a terminal state (no further transitions allowed).
        
        Args:
            entity_type: Type of entity
            status: Status to check
            
        Returns:
            bool: True if terminal state, False otherwise
        
        Example:
            >>> is_done = StateTransitionService.is_terminal_state(
            ...     entity_type="cashout",
            ...     status=CashoutStatus.SUCCESS
            ... )
            >>> # Returns: True
        """
        valid_next = StateTransitionService.get_valid_transitions(entity_type, status)
        return len(valid_next) == 0
