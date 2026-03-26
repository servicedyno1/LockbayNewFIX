"""
Transactional State Machine Framework
Base infrastructure for managing entity state transitions with financial integrity guarantees

This framework provides:
- Atomic state transitions with database locking
- Integration with optimistic locking and version columns
- Comprehensive audit logging
- Database-level constraint validation
- Rollback and error recovery
- Financial integrity for money-related operations
"""

import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Dict, List, Optional, Set, Any, Type, Callable
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from decimal import Decimal

from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy import text

from database import SessionLocal
from utils.database_locking import DatabaseLockingService
from models import Base

logger = logging.getLogger(__name__)


@dataclass
class StateTransitionContext:
    """Context information for state transitions"""
    entity_id: str
    entity_type: str
    current_state: Optional[str]
    target_state: str
    transition_name: str
    metadata: Dict[str, Any]
    user_id: Optional[int] = None
    admin_override: bool = False
    financial_impact: bool = False
    amount: Optional[Decimal] = None
    currency: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for logging"""
        result = asdict(self)
        # Convert Decimal to string for JSON serialization
        if result.get('amount'):
            result['amount'] = str(result['amount'])
        return result


@dataclass 
class StateTransitionResult:
    """Result of a state transition attempt"""
    success: bool
    new_state: Optional[str]
    error_message: Optional[str] = None
    error_code: Optional[str] = None
    rollback_performed: bool = False
    side_effects: List[str] = None
    duration_ms: float = 0.0
    version_before: Optional[int] = None
    version_after: Optional[int] = None
    
    def __post_init__(self):
        if self.side_effects is None:
            self.side_effects = []


class StateTransitionError(Exception):
    """Raised when state transition fails"""
    def __init__(self, message: str, error_code: str = None, context: StateTransitionContext = None):
        super().__init__(message)
        self.error_code = error_code
        self.context = context


class InvalidStateTransitionError(StateTransitionError):
    """Raised when attempting invalid state transition"""
    pass


class StateTransitionAuditLogger:
    """Comprehensive audit logging for state transitions"""
    
    @staticmethod
    def log_transition_attempt(context: StateTransitionContext) -> None:
        """Log state transition attempt"""
        logger.info(
            f"🔄 STATE_TRANSITION_ATTEMPT: {context.entity_type}#{context.entity_id} "
            f"{context.current_state} → {context.target_state} "
            f"(transition: {context.transition_name})"
        )
        
        if context.financial_impact:
            logger.info(
                f"💰 FINANCIAL_TRANSITION: {context.entity_type}#{context.entity_id} "
                f"amount={context.amount} {context.currency}"
            )
    
    @staticmethod
    def log_transition_success(context: StateTransitionContext, result: StateTransitionResult) -> None:
        """Log successful state transition"""
        logger.info(
            f"✅ STATE_TRANSITION_SUCCESS: {context.entity_type}#{context.entity_id} "
            f"{context.current_state} → {result.new_state} "
            f"(v{result.version_before} → v{result.version_after}) "
            f"duration={result.duration_ms:.2f}ms"
        )
        
        if result.side_effects:
            logger.info(
                f"🔧 SIDE_EFFECTS: {context.entity_type}#{context.entity_id} "
                f"effects={result.side_effects}"
            )
    
    @staticmethod
    def log_transition_failure(context: StateTransitionContext, result: StateTransitionResult) -> None:
        """Log failed state transition"""
        logger.error(
            f"❌ STATE_TRANSITION_FAILED: {context.entity_type}#{context.entity_id} "
            f"{context.current_state} → {context.target_state} "
            f"error={result.error_message} code={result.error_code} "
            f"rollback={result.rollback_performed}"
        )
        
        if context.financial_impact:
            logger.critical(
                f"💥 FINANCIAL_TRANSITION_FAILED: {context.entity_type}#{context.entity_id} "
                f"amount={context.amount} {context.currency} "
                f"REQUIRES_MANUAL_REVIEW"
            )


class BaseStateMachine(ABC):
    """
    Base class for all state machines with comprehensive transition management
    
    Provides:
    - State transition validation
    - Atomic operations with locking
    - Optimistic locking integration
    - Audit logging
    - Financial integrity guarantees
    - Error handling and rollback
    """
    
    def __init__(self, entity_id: str, model_class: Type[Base], lock_timeout: int = 30):
        self.entity_id = entity_id
        self.model_class = model_class
        self.lock_timeout = lock_timeout
        self.audit_logger = StateTransitionAuditLogger()
        
        # Get entity type name from model class
        self.entity_type = model_class.__name__
        
        # Initialize components
        self._lock_service = DatabaseLockingService()
        
    @property
    @abstractmethod
    def valid_transitions(self) -> Dict[Optional[str], Set[str]]:
        """Define valid state transitions for this entity type"""
        pass
    
    @property
    @abstractmethod
    def terminal_states(self) -> Set[str]:
        """Define terminal states that cannot transition further"""
        pass
    
    @property
    @abstractmethod
    def financial_states(self) -> Set[str]:
        """Define states that have financial impact"""
        pass
    
    @property
    @abstractmethod
    def state_field_name(self) -> str:
        """Name of the state field in the model (e.g., 'status')"""
        pass
    
    def get_primary_key_field(self) -> str:
        """Get the primary key field name for entity lookup"""
        # Most entities use '{entity_type.lower()}_id' pattern
        return f"{self.entity_type.lower()}_id"
    
    def is_valid_transition(self, current_state: Optional[str], target_state: str, 
                          admin_override: bool = False) -> bool:
        """Check if state transition is valid"""
        valid_next_states = self.valid_transitions.get(current_state, set())
        
        # Check basic transition validity
        if target_state not in valid_next_states:
            return False
        
        # Check terminal state protection
        if current_state in self.terminal_states and not admin_override:
            logger.warning(
                f"⚠️ TERMINAL_STATE_PROTECTION: {self.entity_type}#{self.entity_id} "
                f"attempted transition from terminal state {current_state}"
            )
            return False
        
        # Subclass can override for additional validation
        return self.validate_business_rules(current_state, target_state, admin_override)
    
    def validate_business_rules(self, current_state: Optional[str], target_state: str, 
                              admin_override: bool = False) -> bool:
        """Override in subclasses for additional business rule validation"""
        return True
    
    def get_valid_next_states(self, current_state: Optional[str]) -> Set[str]:
        """Get all valid next states for current state"""
        return self.valid_transitions.get(current_state, set())
    
    def is_terminal_state(self, state: str) -> bool:
        """Check if state is terminal"""
        return state in self.terminal_states
    
    def is_financial_state(self, state: str) -> bool:
        """Check if state has financial impact"""
        return state in self.financial_states
    
    @contextmanager
    def locked_entity_operation(self, session: Session, skip_locked: bool = False):
        """Context manager for locked entity operations"""
        primary_key_field = self.get_primary_key_field()
        
        try:
            # Set lock timeout
            session.execute(text(f"SET LOCAL lock_timeout = '{self.lock_timeout}s'"))
            
            # Build lock query
            lock_clause = "FOR UPDATE"
            if skip_locked:
                lock_clause += " SKIP LOCKED"
            
            table_name = self.model_class.__tablename__
            query = text(f"""
                SELECT * FROM {table_name} 
                WHERE {primary_key_field} = :entity_id 
                {lock_clause}
            """)
            
            result = session.execute(query, {"entity_id": self.entity_id}).fetchone()
            
            if not result and not skip_locked:
                raise StateTransitionError(
                    f"{self.entity_type} {self.entity_id} not found",
                    error_code="ENTITY_NOT_FOUND"
                )
            elif not result and skip_locked:
                logger.info(f"🔒 SKIP_LOCKED: {self.entity_type} {self.entity_id} is locked")
                yield None
                return
            
            # Get the full entity object
            entity = session.query(self.model_class).filter(
                getattr(self.model_class, primary_key_field) == self.entity_id
            ).first()
            
            if not entity:
                raise StateTransitionError(
                    f"{self.entity_type} {self.entity_id} not found after lock",
                    error_code="ENTITY_DISAPPEARED"
                )
            
            logger.debug(f"🔒 LOCKED: {self.entity_type} {self.entity_id}")
            yield entity
            
        except OperationalError as e:
            if "lock_timeout" in str(e).lower():
                logger.error(
                    f"🕐 LOCK_TIMEOUT: Failed to lock {self.entity_type} {self.entity_id} "
                    f"within {self.lock_timeout}s"
                )
                raise StateTransitionError(
                    f"Lock timeout for {self.entity_type} {self.entity_id}",
                    error_code="LOCK_TIMEOUT"
                )
            else:
                logger.error(f"❌ LOCK_ERROR: Database error: {e}")
                raise StateTransitionError(f"Database lock error: {e}", error_code="DATABASE_ERROR")
        finally:
            try:
                session.execute(text("SET LOCAL lock_timeout = DEFAULT"))
            except Exception as e:
                logger.debug(f"Could not reset lock timeout: {e}")
                pass
    
    def transition_state(self, target_state: str, transition_name: str, 
                        user_id: Optional[int] = None, admin_override: bool = False,
                        metadata: Optional[Dict[str, Any]] = None,
                        amount: Optional[Decimal] = None, currency: Optional[str] = None,
                        side_effect_callback: Optional[Callable] = None,
                        **update_fields) -> StateTransitionResult:
        """
        Perform atomic state transition with comprehensive validation and logging
        
        Args:
            target_state: Target state to transition to
            transition_name: Name of the transition for logging
            user_id: User ID performing the transition
            admin_override: Whether this is an admin override
            metadata: Additional metadata for the transition
            amount: Financial amount if this is a financial transition
            currency: Currency for financial transitions
            side_effect_callback: Callback function for side effects (receives entity, session)
            **update_fields: Additional fields to update during transition
            
        Returns:
            StateTransitionResult with success status and details
        """
        start_time = datetime.utcnow()
        result = StateTransitionResult(success=False, new_state=None)
        
        if metadata is None:
            metadata = {}
        
        try:
            with SessionLocal() as session:
                with self.locked_entity_operation(session) as entity:
                    if entity is None:  # Skip locked
                        return StateTransitionResult(
                            success=False,
                            new_state=None,
                            error_message="Entity is locked by another process",
                            error_code="ENTITY_LOCKED"
                        )
                    
                    # Get current state
                    current_state = getattr(entity, self.state_field_name)
                    current_version = getattr(entity, 'version', None)
                    
                    # Create transition context
                    context = StateTransitionContext(
                        entity_id=self.entity_id,
                        entity_type=self.entity_type,
                        current_state=current_state,
                        target_state=target_state,
                        transition_name=transition_name,
                        metadata=metadata,
                        user_id=user_id,
                        admin_override=admin_override,
                        financial_impact=self.is_financial_state(target_state) or 
                                       self.is_financial_state(current_state),
                        amount=amount,
                        currency=currency
                    )
                    
                    # Log transition attempt
                    self.audit_logger.log_transition_attempt(context)
                    
                    # Validate transition
                    if not self.is_valid_transition(current_state, target_state, admin_override):
                        result.error_message = (
                            f"Invalid transition: {current_state} → {target_state} "
                            f"for {self.entity_type} {self.entity_id}"
                        )
                        result.error_code = "INVALID_TRANSITION"
                        self.audit_logger.log_transition_failure(context, result)
                        return result
                    
                    # Perform pre-transition validation
                    validation_result = self.pre_transition_validation(
                        entity, current_state, target_state, context
                    )
                    if not validation_result.success:
                        result.error_message = validation_result.error_message
                        result.error_code = validation_result.error_code
                        self.audit_logger.log_transition_failure(context, result)
                        return result
                    
                    # Update entity state
                    setattr(entity, self.state_field_name, target_state)
                    setattr(entity, 'updated_at', datetime.utcnow())
                    
                    # Update version for optimistic locking if available
                    if hasattr(entity, 'version'):
                        setattr(entity, 'version', (current_version or 0) + 1)
                    
                    # Apply additional field updates
                    for field, value in update_fields.items():
                        if hasattr(entity, field):
                            setattr(entity, field, value)
                        else:
                            logger.warning(f"Unknown field {field} for {self.entity_type}")
                    
                    # Execute side effects within the same transaction
                    side_effects = []
                    if side_effect_callback:
                        try:
                            side_effect_result = side_effect_callback(entity, session)
                            if isinstance(side_effect_result, list):
                                side_effects.extend(side_effect_result)
                            elif side_effect_result:
                                side_effects.append(str(side_effect_result))
                        except Exception as e:
                            logger.error(f"Side effect failed: {e}")
                            result.error_message = f"Side effect failed: {e}"
                            result.error_code = "SIDE_EFFECT_FAILED"
                            result.rollback_performed = True
                            session.rollback()
                            self.audit_logger.log_transition_failure(context, result)
                            return result
                    
                    # Commit the transaction
                    try:
                        session.commit()
                        
                        # Calculate duration
                        duration = (datetime.utcnow() - start_time).total_seconds() * 1000
                        
                        # Success!
                        result.success = True
                        result.new_state = target_state
                        result.side_effects = side_effects
                        result.duration_ms = duration
                        result.version_before = current_version
                        result.version_after = getattr(entity, 'version', None)
                        
                        self.audit_logger.log_transition_success(context, result)
                        
                        # Execute post-transition callback (outside transaction)
                        self.post_transition_callback(entity, current_state, target_state, context)
                        
                        return result
                        
                    except IntegrityError as e:
                        session.rollback()
                        result.error_message = f"Database constraint violation: {e}"
                        result.error_code = "CONSTRAINT_VIOLATION" 
                        result.rollback_performed = True
                        self.audit_logger.log_transition_failure(context, result)
                        return result
                        
        except Exception as e:
            logger.error(f"Unexpected error in state transition: {e}")
            result.error_message = f"Unexpected error: {e}"
            result.error_code = "UNEXPECTED_ERROR"
            result.rollback_performed = True
            return result
    
    def pre_transition_validation(self, entity: Base, current_state: Optional[str], 
                                target_state: str, context: StateTransitionContext) -> StateTransitionResult:
        """Override in subclasses for pre-transition validation"""
        return StateTransitionResult(success=True, new_state=target_state)
    
    def post_transition_callback(self, entity: Base, old_state: Optional[str], 
                               new_state: str, context: StateTransitionContext) -> None:
        """Override in subclasses for post-transition actions (outside transaction)"""
        pass
    
    def get_current_state(self) -> Optional[str]:
        """Get current state of the entity without locking"""
        try:
            with SessionLocal() as session:
                primary_key_field = self.get_primary_key_field()
                entity = session.query(self.model_class).filter(
                    getattr(self.model_class, primary_key_field) == self.entity_id
                ).first()
                
                if entity:
                    return getattr(entity, self.state_field_name)
                return None
                
        except Exception as e:
            logger.error(f"Error getting current state for {self.entity_type} {self.entity_id}: {e}")
            return None
    
    def can_transition_to(self, target_state: str, admin_override: bool = False) -> bool:
        """Check if entity can transition to target state"""
        current_state = self.get_current_state()
        return self.is_valid_transition(current_state, target_state, admin_override)
    
    def get_state_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get state transition history from audit logs (implement in subclasses if needed)"""
        # This would typically query an audit log table
        # For now, return empty list - subclasses can override
        return []


# Export main classes
__all__ = [
    "BaseStateMachine",
    "StateTransitionContext", 
    "StateTransitionResult",
    "StateTransitionError",
    "InvalidStateTransitionError",
    "StateTransitionAuditLogger"
]