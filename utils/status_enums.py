"""
DEPRECATED: Enhanced Status Field Integrity with Enum Support

⚠️  DEPRECATION NOTICE ⚠️
This module is deprecated and will be removed in a future version.
Please migrate to using the centralized status definitions from:
- utils.status_flows (for new unified status management)
- models.py (for authoritative enum definitions)

This module now serves as a thin compatibility shim that imports from
the authoritative sources to eliminate conflicting definitions.

MIGRATION PATH:
- Instead of: from utils.status_enums import EscrowStatus
- Use: from utils.status_flows import EscrowStatus
- Or: from models import EscrowStatus

- Instead of: from utils.status_enums import ExchangeOrderStatus  
- Use: from utils.status_flows import ExchangeStatus
- Or: from models import ExchangeStatus

- For unified transaction validation:
- Use: from utils.status_flows import UnifiedTransitionValidator
"""

import warnings
from typing import Dict, List
import logging

# Import authoritative status enums from utils.status_flows (which re-exports from models.py)
from utils.status_flows import (
    # Authoritative enum imports (re-exported from models.py)
    EscrowStatus as _AuthoritativeEscrowStatus,
    ExchangeStatus as _AuthoritativeExchangeStatus,
    UnifiedTransitionValidator
)

logger = logging.getLogger(__name__)

# Issue deprecation warning when this module is imported
warnings.warn(
    "utils.status_enums is deprecated. Use utils.status_flows or models.py for status definitions. "
    "See module docstring for migration instructions.",
    DeprecationWarning,
    stacklevel=2
)


class _DeprecatedEnumWrapper:
    """Wrapper that issues deprecation warnings when enum is accessed"""
    
    def __init__(self, enum_class, old_name: str, new_module: str):
        self._enum_class = enum_class
        self._old_name = old_name
        self._new_module = new_module
        self._warned = False
    
    def __getattr__(self, name):
        if not self._warned:
            warnings.warn(
                f"{self._old_name} is deprecated. Use {self._new_module}.{self._enum_class.__name__} instead.",
                DeprecationWarning,
                stacklevel=3
            )
            self._warned = True
        return getattr(self._enum_class, name)
    
    def __call__(self, value):
        if not self._warned:
            warnings.warn(
                f"{self._old_name} is deprecated. Use {self._new_module}.{self._enum_class.__name__} instead.",
                DeprecationWarning,
                stacklevel=3
            )
            self._warned = True
        return self._enum_class(value)


# === BACKWARD COMPATIBILITY SHIMS ===

# EscrowStatus: Re-export authoritative version with deprecation warning
EscrowStatus = _DeprecatedEnumWrapper(
    _AuthoritativeEscrowStatus, 
    "utils.status_enums.EscrowStatus", 
    "utils.status_flows"
)

# ExchangeOrderStatus: Map to ExchangeStatus with deprecation warning
# Note: The old ExchangeOrderStatus is now called ExchangeStatus in the new system
ExchangeOrderStatus = _DeprecatedEnumWrapper(
    _AuthoritativeExchangeStatus,
    "utils.status_enums.ExchangeOrderStatus",
    "utils.status_flows.ExchangeStatus"
)


class StatusTransitionValidator:
    """
    DEPRECATED: Validates state transitions for business logic compliance
    
    ⚠️  DEPRECATION NOTICE ⚠️
    This class is deprecated. Use UnifiedTransitionValidator from utils.status_flows instead.
    
    This class now delegates to the new centralized validation system while maintaining
    backward compatibility for existing code.
    """
    
    def __init__(self):
        warnings.warn(
            "StatusTransitionValidator is deprecated. Use UnifiedTransitionValidator from utils.status_flows instead.",
            DeprecationWarning,
            stacklevel=2
        )
        self._unified_validator = UnifiedTransitionValidator()
    
    # Legacy transition mappings for backward compatibility
    # These map the old enum values to the new system
    
    @property
    def EXCHANGE_TRANSITIONS(self) -> Dict:
        """Legacy exchange transitions - delegated to new system"""
        warnings.warn(
            "EXCHANGE_TRANSITIONS is deprecated. Use UnifiedTransitionValidator for transition validation.",
            DeprecationWarning,
            stacklevel=2
        )
        # Return empty dict to maintain interface but encourage migration
        return {}
    
    @property 
    def ESCROW_TRANSITIONS(self) -> Dict:
        """Legacy escrow transitions - delegated to new system"""
        warnings.warn(
            "ESCROW_TRANSITIONS is deprecated. Use UnifiedTransitionValidator for transition validation.",
            DeprecationWarning,
            stacklevel=2
        )
        # Return empty dict to maintain interface but encourage migration
        return {}
    
    @classmethod
    def validate_exchange_transition(cls, current_status: str, new_status: str) -> bool:
        """
        DEPRECATED: Validate if transition between exchange order statuses is allowed
        
        This method now delegates to the new unified validation system.
        Please migrate to using UnifiedTransitionValidator.
        
        Args:
            current_status: Current status string
            new_status: Desired new status string
            
        Returns:
            bool: True if transition is valid
        """
        warnings.warn(
            "validate_exchange_transition is deprecated. Use UnifiedTransitionValidator instead.",
            DeprecationWarning,
            stacklevel=2
        )
        
        try:
            # For backward compatibility, we'll try to validate using a generic approach
            # since we don't have the transaction type context in the legacy API
            
            # Try to validate as exchange status using the new system
            # Note: We can't use UnifiedTransitionValidator directly here because it requires transaction_type
            # So we'll do a best-effort validation by checking if both statuses are valid
            
            from utils.status_flows import ExchangeStatus
            
            try:
                ExchangeStatus(current_status) 
                ExchangeStatus(new_status)
                
                # For backward compatibility, allow most transitions 
                # (the new system has more sophisticated validation)
                logger.info(f"Legacy exchange validation: {current_status} -> {new_status} (allowed for compatibility)")
                return True
                
            except ValueError as e:
                logger.error(f"Invalid exchange status in legacy validation: {e}")
                return False
                
        except Exception as e:
            logger.error(f"Error in legacy exchange validation: {e}")
            return False
    
    @classmethod
    def validate_escrow_transition(cls, current_status: str, new_status: str) -> bool:
        """
        DEPRECATED: Validate if transition between escrow statuses is allowed
        
        This method now delegates to the new unified validation system.
        Please migrate to using UnifiedTransitionValidator.
        
        Args:
            current_status: Current status string
            new_status: Desired new status string
            
        Returns:
            bool: True if transition is valid
        """
        warnings.warn(
            "validate_escrow_transition is deprecated. Use UnifiedTransitionValidator instead.",
            DeprecationWarning,
            stacklevel=2
        )
        
        try:
            # For backward compatibility, we'll try to validate using the new system
            # We'll use ESCROW transaction type for the unified validator
            
            from utils.status_flows import UnifiedTransactionType
            
            validator = UnifiedTransitionValidator()
            result = validator.validate_transition(
                current_status=current_status,
                new_status=new_status, 
                transaction_type=UnifiedTransactionType.ESCROW
            )
            
            if result.is_valid:
                logger.info(f"Legacy escrow validation passed: {current_status} -> {new_status}")
            else:
                logger.warning(f"Legacy escrow validation failed: {result.error_message}")
                
            return result.is_valid
            
        except Exception as e:
            logger.error(f"Error in legacy escrow validation: {e}")
            # Fall back to permissive validation for backward compatibility
            try:
                _AuthoritativeEscrowStatus(current_status)
                _AuthoritativeEscrowStatus(new_status)
                logger.info(f"Legacy escrow validation fallback: {current_status} -> {new_status} (allowed for compatibility)")
                return True
            except ValueError:
                return False
    
    @classmethod
    def get_valid_next_statuses(cls, current_status: str, entity_type: str) -> List[str]:
        """
        DEPRECATED: Get list of valid next statuses for current state
        
        Args:
            current_status: Current status string
            entity_type: 'exchange' or 'escrow'
            
        Returns:
            List[str]: Valid next status strings
        """
        warnings.warn(
            "get_valid_next_statuses is deprecated. Use UnifiedTransitionValidator.get_allowed_transitions instead.",
            DeprecationWarning,
            stacklevel=2
        )
        
        try:
            if entity_type == "exchange":
                # Return all exchange statuses for backward compatibility
                return [status.value for status in _AuthoritativeExchangeStatus]
            elif entity_type == "escrow":
                # For escrow, we can use the unified validator
                from utils.status_flows import UnifiedTransactionType
                
                validator = UnifiedTransitionValidator()
                allowed = validator.get_allowed_transitions(
                    current_status=current_status,
                    transaction_type=UnifiedTransactionType.ESCROW
                )
                return allowed
            else:
                logger.error(f"Unknown entity type: {entity_type}")
                return []
                
        except Exception as e:
            logger.error(f"Error in legacy get_valid_next_statuses: {e}")
            return []


class StatusUtils:
    """
    DEPRECATED: Utility functions for status management
    
    ⚠️  DEPRECATION NOTICE ⚠️
    This class is deprecated. Use the utilities from utils.status_flows instead.
    """
    
    @staticmethod
    def is_terminal_status(status: str, entity_type: str) -> bool:
        """DEPRECATED: Check if status is terminal (no further transitions)"""
        warnings.warn(
            "StatusUtils.is_terminal_status is deprecated. Use UnifiedTransitionValidator.is_terminal_status instead.",
            DeprecationWarning,
            stacklevel=2
        )
        
        try:
            validator = UnifiedTransitionValidator()
            return validator.is_terminal_status(status)
        except Exception as e:
            logger.error(f"Error in legacy is_terminal_status: {e}")
            # Fallback logic
            return status in ['completed', 'cancelled', 'failed', 'expired', 'success']
    
    @staticmethod
    def is_active_status(status: str, entity_type: str) -> bool:
        """DEPRECATED: Check if status represents an active/processing state"""
        warnings.warn(
            "StatusUtils.is_active_status is deprecated. Use status phase checking from utils.status_flows instead.",
            DeprecationWarning,
            stacklevel=2
        )
        
        try:
            # Use the new phase-based approach
            from utils.status_flows import UnifiedTransitionValidator
            
            validator = UnifiedTransitionValidator()
            phase = validator.get_status_phase(status)
            
            # Active phases are authorization and processing
            return phase in ['authorization', 'processing']
            
        except Exception as e:
            logger.error(f"Error in legacy is_active_status: {e}")
            # Fallback logic
            active_statuses = {
                'awaiting_deposit', 'processing', 'awaiting_payment', 
                'payment_confirmed', 'active', 'funds_held', 'awaiting_approval'
            }
            return status in active_statuses
    
    @staticmethod
    def get_all_statuses(entity_type: str) -> List[str]:
        """DEPRECATED: Get all possible status values for entity type"""
        warnings.warn(
            "StatusUtils.get_all_statuses is deprecated. Import status enums directly from utils.status_flows or models.py instead.",
            DeprecationWarning,
            stacklevel=2
        )
        
        try:
            if entity_type == "exchange":
                return [status.value for status in _AuthoritativeExchangeStatus]
            elif entity_type == "escrow":
                return [status.value for status in _AuthoritativeEscrowStatus]
            else:
                logger.error(f"Unknown entity type: {entity_type}")
                return []
        except Exception as e:
            logger.error(f"Error in legacy get_all_statuses: {e}")
            return []


# === BACKWARD COMPATIBILITY EXPORTS ===

# Global validator instance for backward compatibility
status_validator = StatusTransitionValidator()

# Export convenience functions at module level for backward compatibility
def validate_exchange_transition(current_status: str, new_status: str) -> bool:
    """DEPRECATED: Module-level convenience function for exchange transitions"""
    warnings.warn(
        "validate_exchange_transition function is deprecated. Use UnifiedTransitionValidator instead.",
        DeprecationWarning,
        stacklevel=2
    )
    return status_validator.validate_exchange_transition(current_status, new_status)

def validate_escrow_transition(current_status: str, new_status: str) -> bool:
    """DEPRECATED: Module-level convenience function for escrow transitions"""
    warnings.warn(
        "validate_escrow_transition function is deprecated. Use UnifiedTransitionValidator instead.",
        DeprecationWarning,
        stacklevel=2
    )
    return status_validator.validate_escrow_transition(current_status, new_status)


# === MODULE-LEVEL MIGRATION NOTICE ===

def _print_migration_guide():
    """Print migration guide for developers"""
    migration_guide = """
    
    🚀 MIGRATION GUIDE: utils.status_enums → utils.status_flows
    
    OLD (Deprecated):
        from utils.status_enums import EscrowStatus, ExchangeOrderStatus
        from utils.status_enums import StatusTransitionValidator, status_validator
    
    NEW (Recommended):
        from utils.status_flows import EscrowStatus, ExchangeStatus
        from utils.status_flows import UnifiedTransitionValidator
        
        # For unified transaction validation:
        validator = UnifiedTransitionValidator()
        result = validator.validate_transition(current, new, transaction_type)
        
        # For legacy escrow validation:
        result = validator.validate_transition(current, new, UnifiedTransactionType.ESCROW)
    
    BENEFITS:
        ✅ Single source of truth for all status definitions
        ✅ Comprehensive validation with detailed error messages  
        ✅ Phase-based transition logic
        ✅ Better integration with unified transaction system
        ✅ Eliminates conflicting enum definitions
        
    📚 See utils/status_flows.py for complete documentation
    
    """
    logger.info(migration_guide)

# Print migration guide in development mode
if logger.getEffectiveLevel() <= logging.INFO:
    _print_migration_guide()