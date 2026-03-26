"""
Centralized Status and Flow Management System

This module serves as the authoritative source for all status definitions and transition validation
across the entire application. It consolidates status enums and flow rules to eliminate conflicts
and provide a single source of truth for status management.

Key Features:
- Re-exports all status enums from models.py (authoritative source)
- Centralized status flow definitions moved from UnifiedTransactionService
- Unified transition validation for UnifiedTransactionStatus
- Phase-based transition logic (Initiation → Authorization → Processing → Terminal)
- Comprehensive logging and validation
- Compatibility with LegacyStatusMapper

This replaces the conflicting definitions that previously existed in:
- utils/status_enums.py (had different EscrowStatus values)
- services/unified_transaction_service.py (had scattered flow rules)
"""

import logging
from typing import Dict, List, Optional, Any, Union
from enum import Enum
from datetime import datetime

# === STATUS ENUM RE-EXPORTS FROM models.py (AUTHORITATIVE SOURCE) ===
# Re-export all status enums from models.py to serve as the single source of truth
# This eliminates conflicts with utils/status_enums.py which had different definitions

from models import (
    # Core transaction statuses
    UnifiedTransactionStatus,
    UnifiedTransactionType
)

logger = logging.getLogger(__name__)


class StatusPhase(Enum):
    """
    Lifecycle phases for unified transaction statuses
    Used to organize and validate transitions between logical phases
    """
    INITIATION = "initiation"        # pending, awaiting_payment, payment_confirmed
    AUTHORIZATION = "authorization"   # funds_held, awaiting_approval, otp_pending, admin_pending
    PROCESSING = "processing"        # processing, awaiting_response, release_pending
    TERMINAL = "terminal"           # success, failed, cancelled, disputed, expired, partial_payment


class TransitionValidationResult:
    """Result of status transition validation with detailed context"""
    
    def __init__(self, 
                 is_valid: bool, 
                 current_status: str, 
                 new_status: str,
                 transaction_type: Optional[str] = None,
                 error_message: Optional[str] = None,
                 current_phase: Optional[str] = None,
                 new_phase: Optional[str] = None,
                 allowed_transitions: Optional[List[str]] = None):
        self.is_valid = is_valid
        self.current_status = current_status
        self.new_status = new_status
        self.transaction_type = transaction_type
        self.error_message = error_message
        self.current_phase = current_phase
        self.new_phase = new_phase
        self.allowed_transitions = allowed_transitions or []
        
    def __bool__(self) -> bool:
        """Allow boolean evaluation of validation result"""
        return self.is_valid
        
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for logging/serialization"""
        return {
            'is_valid': self.is_valid,
            'current_status': self.current_status,
            'new_status': self.new_status,
            'transaction_type': self.transaction_type,
            'error_message': self.error_message,
            'current_phase': self.current_phase,
            'new_phase': self.new_phase,
            'allowed_transitions': self.allowed_transitions
        }


class UnifiedStatusFlows:
    """
    Centralized status flow definitions for unified transaction system
    
    Contains the authoritative status transition rules moved from UnifiedTransactionService
    to eliminate duplication and provide a single source of truth.
    """
    
    # === PHASE MAPPING FOR UNIFIED TRANSACTION STATUSES ===
    STATUS_PHASE_MAP = {
        # INITIATION PHASE - Transaction setup and payment
        UnifiedTransactionStatus.PENDING: StatusPhase.INITIATION,
        UnifiedTransactionStatus.AWAITING_PAYMENT: StatusPhase.INITIATION,
        UnifiedTransactionStatus.PAYMENT_CONFIRMED: StatusPhase.INITIATION,
        
        # AUTHORIZATION PHASE - Approvals and verification
        UnifiedTransactionStatus.FUNDS_HELD: StatusPhase.AUTHORIZATION,
        UnifiedTransactionStatus.AWAITING_APPROVAL: StatusPhase.AUTHORIZATION,
        UnifiedTransactionStatus.OTP_PENDING: StatusPhase.AUTHORIZATION,
        UnifiedTransactionStatus.ADMIN_PENDING: StatusPhase.AUTHORIZATION,
        
        # PROCESSING PHASE - External operations
        UnifiedTransactionStatus.PROCESSING: StatusPhase.PROCESSING,
        UnifiedTransactionStatus.AWAITING_RESPONSE: StatusPhase.PROCESSING,
        UnifiedTransactionStatus.RELEASE_PENDING: StatusPhase.PROCESSING,
        
        # TERMINAL PHASE - Final states
        UnifiedTransactionStatus.SUCCESS: StatusPhase.TERMINAL,
        UnifiedTransactionStatus.FAILED: StatusPhase.TERMINAL,
        UnifiedTransactionStatus.CANCELLED: StatusPhase.TERMINAL,
        UnifiedTransactionStatus.DISPUTED: StatusPhase.TERMINAL,
        UnifiedTransactionStatus.EXPIRED: StatusPhase.TERMINAL,
        UnifiedTransactionStatus.PARTIAL_PAYMENT: StatusPhase.TERMINAL,
    }
    
    # === STATUS FLOW RULES (MOVED FROM UnifiedTransactionService) ===
    # Comprehensive flow definitions for all 4 unified transaction types
    
    UNIFIED_TRANSACTION_FLOWS = {
        # 1. WALLET CASHOUT: pending → [OTP] → processing → awaiting_response → success/failed
        UnifiedTransactionType.WALLET_CASHOUT: {
            UnifiedTransactionStatus.PENDING: [
                UnifiedTransactionStatus.PROCESSING,    # OTP verified, start processing
                UnifiedTransactionStatus.CANCELLED      # User cancels before OTP
            ],
            UnifiedTransactionStatus.PROCESSING: [
                UnifiedTransactionStatus.AWAITING_RESPONSE,  # API call made
                UnifiedTransactionStatus.FAILED              # Processing error
            ],
            UnifiedTransactionStatus.AWAITING_RESPONSE: [
                UnifiedTransactionStatus.SUCCESS,            # API success
                UnifiedTransactionStatus.FAILED              # API failure
            ]
        },
        
        # 2. EXCHANGE_SELL_CRYPTO: pending → awaiting_payment → payment_confirmed → processing → success
        UnifiedTransactionType.EXCHANGE_SELL_CRYPTO: {
            UnifiedTransactionStatus.PENDING: [
                UnifiedTransactionStatus.AWAITING_PAYMENT,   # Waiting for NGN payment
                UnifiedTransactionStatus.CANCELLED           # User cancels
            ],
            UnifiedTransactionStatus.AWAITING_PAYMENT: [
                UnifiedTransactionStatus.PAYMENT_CONFIRMED,  # Payment received
                UnifiedTransactionStatus.CANCELLED           # Expired or cancelled
            ],
            UnifiedTransactionStatus.PAYMENT_CONFIRMED: [
                UnifiedTransactionStatus.PROCESSING,         # Start crypto transfer
                UnifiedTransactionStatus.FAILED              # Validation error
            ],
            UnifiedTransactionStatus.PROCESSING: [
                UnifiedTransactionStatus.SUCCESS,            # Crypto sent to user
                UnifiedTransactionStatus.FAILED              # Transfer error
            ]
        },
        
        # 3. EXCHANGE_BUY_CRYPTO: pending → awaiting_payment → payment_confirmed → processing → success
        UnifiedTransactionType.EXCHANGE_BUY_CRYPTO: {
            UnifiedTransactionStatus.PENDING: [
                UnifiedTransactionStatus.AWAITING_PAYMENT,   # Waiting for crypto deposit
                UnifiedTransactionStatus.CANCELLED           # User cancels
            ],
            UnifiedTransactionStatus.AWAITING_PAYMENT: [
                UnifiedTransactionStatus.PAYMENT_CONFIRMED,  # Crypto received
                UnifiedTransactionStatus.CANCELLED           # Expired or cancelled
            ],
            UnifiedTransactionStatus.PAYMENT_CONFIRMED: [
                UnifiedTransactionStatus.PROCESSING,         # Start NGN transfer
                UnifiedTransactionStatus.FAILED              # Validation error
            ],
            UnifiedTransactionStatus.PROCESSING: [
                UnifiedTransactionStatus.SUCCESS,            # NGN sent to user
                UnifiedTransactionStatus.FAILED              # Transfer error
            ]
        },
        
        # 4. ESCROW: pending → payment_confirmed → awaiting_approval → funds_held → release_pending → success
        UnifiedTransactionType.ESCROW: {
            UnifiedTransactionStatus.PENDING: [
                UnifiedTransactionStatus.PAYMENT_CONFIRMED,  # Payment received
                UnifiedTransactionStatus.CANCELLED           # Escrow cancelled
            ],
            UnifiedTransactionStatus.PAYMENT_CONFIRMED: [
                UnifiedTransactionStatus.AWAITING_APPROVAL,  # Waiting for seller
                UnifiedTransactionStatus.CANCELLED           # Buyer cancels
            ],
            UnifiedTransactionStatus.AWAITING_APPROVAL: [
                UnifiedTransactionStatus.FUNDS_HELD,         # Seller accepts
                UnifiedTransactionStatus.CANCELLED           # Seller declines/timeout
            ],
            UnifiedTransactionStatus.FUNDS_HELD: [
                UnifiedTransactionStatus.RELEASE_PENDING,    # Buyer confirms delivery
                UnifiedTransactionStatus.DISPUTED,           # Dispute raised
                UnifiedTransactionStatus.CANCELLED           # Auto-refund timeout
            ],
            UnifiedTransactionStatus.RELEASE_PENDING: [
                UnifiedTransactionStatus.SUCCESS,            # Funds released to seller
                UnifiedTransactionStatus.DISPUTED            # Last-minute dispute
            ],
            UnifiedTransactionStatus.DISPUTED: [
                UnifiedTransactionStatus.SUCCESS,            # Admin releases to seller
                UnifiedTransactionStatus.CANCELLED           # Admin refunds to buyer
            ]
        }
    }


class UnifiedTransitionValidator:
    """
    Unified transition validator for UnifiedTransactionStatus flows
    
    Provides comprehensive validation with phase-based logic, detailed error messages,
    and comprehensive logging. This replaces the scattered validation logic that
    previously existed across multiple files.
    """
    
    def __init__(self):
        self.flows = UnifiedStatusFlows.UNIFIED_TRANSACTION_FLOWS
        self.phase_map = UnifiedStatusFlows.STATUS_PHASE_MAP
        
    def validate_transition(self, 
                          current_status: Union[str, UnifiedTransactionStatus],
                          new_status: Union[str, UnifiedTransactionStatus], 
                          transaction_type: Union[str, UnifiedTransactionType],
                          context: Optional[Dict[str, Any]] = None) -> TransitionValidationResult:
        """
        Validate status transition for unified transaction system
        
        Args:
            current_status: Current status (string or enum)
            new_status: Desired new status (string or enum)
            transaction_type: Transaction type (string or enum)
            context: Optional context for enhanced validation
            
        Returns:
            TransitionValidationResult with detailed validation info
        """
        start_time = datetime.utcnow()
        
        # Normalize inputs to strings
        current_str = current_status.value if isinstance(current_status, UnifiedTransactionStatus) else current_status
        new_str = new_status.value if isinstance(new_status, UnifiedTransactionStatus) else new_status
        type_str = transaction_type.value if isinstance(transaction_type, UnifiedTransactionType) else transaction_type
        
        logger.info(f"🔄 TRANSITION_VALIDATION: {current_str} → {new_str} for {type_str}")
        
        try:
            # Validate status enums
            try:
                current_enum = UnifiedTransactionStatus(current_str)
                new_enum = UnifiedTransactionStatus(new_str)
                type_enum = UnifiedTransactionType(type_str)
            except ValueError as e:
                error_msg = f"Invalid status or transaction type: {e}"
                logger.error(f"❌ TRANSITION_VALIDATION_FAILED: {error_msg}")
                return TransitionValidationResult(
                    is_valid=False,
                    current_status=current_str,
                    new_status=new_str,
                    transaction_type=type_str,
                    error_message=error_msg
                )
            
            # Get current and new phases
            current_phase = self.phase_map.get(current_enum)
            new_phase = self.phase_map.get(new_enum)
            
            # Check if transaction type has flow rules
            if type_enum not in self.flows:
                error_msg = f"No flow rules defined for transaction type: {type_str}"
                logger.error(f"❌ TRANSITION_VALIDATION_FAILED: {error_msg}")
                return TransitionValidationResult(
                    is_valid=False,
                    current_status=current_str,
                    new_status=new_str,
                    transaction_type=type_str,
                    error_message=error_msg,
                    current_phase=current_phase.value if current_phase else None,
                    new_phase=new_phase.value if new_phase else None
                )
            
            # Get allowed transitions for current status
            flow_rules = self.flows[type_enum]
            allowed_transitions = flow_rules.get(current_enum, [])
            allowed_strs = [status.value for status in allowed_transitions]
            
            # Validate transition
            is_valid = new_enum in allowed_transitions
            
            if is_valid:
                logger.info(f"✅ TRANSITION_VALID: {current_str} → {new_str} for {type_str} "
                           f"({current_phase.value if current_phase else 'unknown'} → "
                           f"{new_phase.value if new_phase else 'unknown'})")
                
                return TransitionValidationResult(
                    is_valid=True,
                    current_status=current_str,
                    new_status=new_str,
                    transaction_type=type_str,
                    current_phase=current_phase.value if current_phase else None,
                    new_phase=new_phase.value if new_phase else None,
                    allowed_transitions=allowed_strs
                )
            else:
                error_msg = f"Invalid transition {current_str} → {new_str} for {type_str}. Allowed: {allowed_strs}"
                logger.warning(f"⚠️ TRANSITION_INVALID: {error_msg}")
                
                return TransitionValidationResult(
                    is_valid=False,
                    current_status=current_str,
                    new_status=new_str,
                    transaction_type=type_str,
                    error_message=error_msg,
                    current_phase=current_phase.value if current_phase else None,
                    new_phase=new_phase.value if new_phase else None,
                    allowed_transitions=allowed_strs
                )
                
        except Exception as e:
            error_msg = f"Unexpected error during transition validation: {str(e)}"
            logger.error(f"💥 TRANSITION_VALIDATION_ERROR: {error_msg}", exc_info=True)
            
            return TransitionValidationResult(
                is_valid=False,
                current_status=current_str,
                new_status=new_str,
                transaction_type=type_str,
                error_message=error_msg
            )
        
        finally:
            duration_ms = (datetime.utcnow() - start_time).total_seconds() * 1000
            logger.debug(f"🏁 TRANSITION_VALIDATION_COMPLETE: {duration_ms:.2f}ms")
    
    def get_allowed_transitions(self, 
                              current_status: Union[str, UnifiedTransactionStatus],
                              transaction_type: Union[str, UnifiedTransactionType]) -> List[str]:
        """
        Get list of allowed next statuses for current state
        
        Args:
            current_status: Current status (string or enum)
            transaction_type: Transaction type (string or enum)
            
        Returns:
            List of allowed next status strings
        """
        try:
            # Normalize inputs
            current_enum = UnifiedTransactionStatus(current_status) if isinstance(current_status, str) else current_status
            type_enum = UnifiedTransactionType(transaction_type) if isinstance(transaction_type, str) else transaction_type
            
            # Get flow rules
            if type_enum not in self.flows:
                logger.warning(f"No flow rules for transaction type: {transaction_type}")
                return []
                
            flow_rules = self.flows[type_enum]
            allowed_transitions = flow_rules.get(current_enum, [])
            
            return [status.value for status in allowed_transitions]
            
        except ValueError as e:
            logger.error(f"Invalid status or transaction type in get_allowed_transitions: {e}")
            return []
    
    def is_terminal_status(self, status: Union[str, UnifiedTransactionStatus]) -> bool:
        """Check if status is terminal (no further transitions possible)"""
        try:
            status_enum = UnifiedTransactionStatus(status) if isinstance(status, str) else status
            phase = self.phase_map.get(status_enum)
            return phase == StatusPhase.TERMINAL
        except ValueError:
            return False
    
    def get_status_phase(self, status: Union[str, UnifiedTransactionStatus]) -> Optional[str]:
        """Get the lifecycle phase for a given status"""
        try:
            status_enum = UnifiedTransactionStatus(status) if isinstance(status, str) else status
            phase = self.phase_map.get(status_enum)
            return phase.value if phase else None
        except ValueError:
            return None
    
    def validate_phase_progression(self, 
                                 current_status: Union[str, UnifiedTransactionStatus],
                                 new_status: Union[str, UnifiedTransactionStatus]) -> bool:
        """
        Validate that phase progression follows logical order
        (Initiation → Authorization → Processing → Terminal)
        """
        try:
            current_phase = self.get_status_phase(current_status)
            new_phase = self.get_status_phase(new_status)
            
            if not current_phase or not new_phase:
                return False
                
            # Define phase order
            phase_order = {
                StatusPhase.INITIATION.value: 0,
                StatusPhase.AUTHORIZATION.value: 1,
                StatusPhase.PROCESSING.value: 2,
                StatusPhase.TERMINAL.value: 3
            }
            
            current_order = phase_order.get(current_phase, -1)
            new_order = phase_order.get(new_phase, -1)
            
            # Allow same phase transitions and forward progression
            # Terminal states can be reached from any phase
            return (new_order >= current_order or new_phase == StatusPhase.TERMINAL.value)
            
        except Exception as e:
            logger.error(f"Error validating phase progression: {e}")
            return False


# === GLOBAL INSTANCES ===
# Provide singleton instances for easy import and use across the application

# Primary validator for unified transaction status flows
unified_transition_validator = UnifiedTransitionValidator()

# Status flows configuration
unified_status_flows = UnifiedStatusFlows()


# === CONVENIENCE FUNCTIONS ===
# Module-level convenience functions for common operations

def validate_unified_transition(current_status: Union[str, UnifiedTransactionStatus],
                              new_status: Union[str, UnifiedTransactionStatus], 
                              transaction_type: Union[str, UnifiedTransactionType],
                              context: Optional[Dict[str, Any]] = None) -> TransitionValidationResult:
    """
    Module-level convenience function for unified transition validation
    
    This is the primary function that external modules should use for status validation.
    """
    return unified_transition_validator.validate_transition(
        current_status, new_status, transaction_type, context
    )


def get_allowed_next_statuses(current_status: Union[str, UnifiedTransactionStatus],
                            transaction_type: Union[str, UnifiedTransactionType]) -> List[str]:
    """
    Module-level convenience function to get allowed next statuses
    """
    return unified_transition_validator.get_allowed_transitions(current_status, transaction_type)


def is_terminal_transaction_status(status: Union[str, UnifiedTransactionStatus]) -> bool:
    """
    Module-level convenience function to check if status is terminal
    """
    return unified_transition_validator.is_terminal_status(status)


def get_transaction_status_phase(status: Union[str, UnifiedTransactionStatus]) -> Optional[str]:
    """
    Module-level convenience function to get status phase
    """
    return unified_transition_validator.get_status_phase(status)


# === COMPATIBILITY LAYER ===
# Provide backwards compatibility with existing code that expects certain functions

# For compatibility with existing LegacyStatusMapper
def get_unified_status_flows() -> Dict[UnifiedTransactionType, Dict[UnifiedTransactionStatus, List[UnifiedTransactionStatus]]]:
    """
    Get the complete unified status flows for use by other modules (e.g., LegacyStatusMapper)
    """
    return UnifiedStatusFlows.UNIFIED_TRANSACTION_FLOWS


def get_status_phase_mapping() -> Dict[UnifiedTransactionStatus, StatusPhase]:
    """
    Get the status to phase mapping for use by other modules
    """
    return UnifiedStatusFlows.STATUS_PHASE_MAP


# === LOGGING AND MONITORING ===

def log_status_transition_metrics(validation_result: TransitionValidationResult) -> None:
    """
    Log metrics for status transitions for monitoring and debugging
    """
    metrics = {
        'is_valid': validation_result.is_valid,
        'transaction_type': validation_result.transaction_type,
        'status_transition': f"{validation_result.current_status}→{validation_result.new_status}",
        'phase_transition': f"{validation_result.current_phase}→{validation_result.new_phase}" if validation_result.current_phase and validation_result.new_phase else None,
        'error': validation_result.error_message if not validation_result.is_valid else None
    }
    
    logger.info(f"📊 STATUS_TRANSITION_METRICS: {metrics}")


# === MODULE INITIALIZATION ===

logger.info("✅ STATUS_FLOWS_MODULE_INITIALIZED: Centralized status management system ready")
logger.info(f"📋 AVAILABLE_TRANSACTION_TYPES: {[t.value for t in UnifiedTransactionType]}")
logger.info(f"📋 AVAILABLE_STATUSES: {[s.value for s in UnifiedTransactionStatus]}")
logger.info(f"📋 AVAILABLE_PHASES: {[p.value for p in StatusPhase]}")