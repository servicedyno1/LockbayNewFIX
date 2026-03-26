"""
Entity-Specific State Machine Implementations
Concrete state machines for Escrow, Cashout, UnifiedTransaction, and Wallet operations

These state machines provide:
- Business rule validation for each entity type
- Financial integrity guarantees
- Atomic state transitions with proper locking
- Integration with existing database constraints
"""

import logging
from typing import Dict, Optional, Set, Any

from utils.state_machines import (
    BaseStateMachine, StateTransitionContext, StateTransitionResult
)
from models import (
    Escrow, Cashout, UnifiedTransaction, Wallet,
    EscrowStatus, CashoutStatus, 
    UnifiedTransactionStatus, WalletHoldStatus
)

logger = logging.getLogger(__name__)


class EscrowStateMachine(BaseStateMachine):
    """
    State machine for Escrow entities with business rule validation
    
    Manages escrow lifecycle from creation through completion/cancellation
    with proper financial safeguards and business rule enforcement
    """
    
    def __init__(self, escrow_id: str, lock_timeout: int = 30):
        super().__init__(escrow_id, Escrow, lock_timeout)
    
    @property
    def valid_transitions(self) -> Dict[Optional[str], Set[str]]:
        """Define valid escrow state transitions based on business rules"""
        return {
            # From None/Creation
            None: {EscrowStatus.CREATED.value},
            
            # Creation flow
            EscrowStatus.CREATED.value: {
                EscrowStatus.PAYMENT_PENDING.value,
                EscrowStatus.CANCELLED.value,
            },
            EscrowStatus.PAYMENT_PENDING.value: {
                EscrowStatus.PAYMENT_CONFIRMED.value,
                EscrowStatus.PARTIAL_PAYMENT.value,
                EscrowStatus.CANCELLED.value,
                EscrowStatus.EXPIRED.value,
            },
            EscrowStatus.PARTIAL_PAYMENT.value: {
                EscrowStatus.PAYMENT_CONFIRMED.value,
                EscrowStatus.CANCELLED.value,
                EscrowStatus.EXPIRED.value,
            },
            EscrowStatus.PAYMENT_CONFIRMED.value: {
                EscrowStatus.AWAITING_SELLER.value,
                EscrowStatus.CANCELLED.value,
            },
            EscrowStatus.AWAITING_SELLER.value: {
                EscrowStatus.PENDING_SELLER.value,  # Seller accepts
                EscrowStatus.PENDING_DEPOSIT.value,  # Direct to deposit
                EscrowStatus.CANCELLED.value,
                EscrowStatus.EXPIRED.value,
            },
            EscrowStatus.PENDING_SELLER.value: {
                EscrowStatus.PENDING_DEPOSIT.value,
                EscrowStatus.CANCELLED.value,
                EscrowStatus.EXPIRED.value,
            },
            EscrowStatus.PENDING_DEPOSIT.value: {
                EscrowStatus.ACTIVE.value,
                EscrowStatus.CANCELLED.value,
                EscrowStatus.EXPIRED.value,
            },
            
            # Active state transitions - CRITICAL: User cancellation blocked, admin override allowed
            EscrowStatus.ACTIVE.value: {
                EscrowStatus.COMPLETED.value,  # Release (buyer only)
                EscrowStatus.REFUNDED.value,   # Refund (admin resolution only)
                EscrowStatus.DISPUTED.value,   # Dispute (buyer/seller)
                EscrowStatus.CANCELLED.value,  # Admin override only
            },
            
            # Dispute resolution
            EscrowStatus.DISPUTED.value: {
                EscrowStatus.COMPLETED.value,  # Resolve to seller
                EscrowStatus.REFUNDED.value,   # Resolve to buyer
                EscrowStatus.CANCELLED.value,  # Admin decision
            },
            
            # Terminal states (no transitions allowed)
            EscrowStatus.COMPLETED.value: set(),
            EscrowStatus.REFUNDED.value: set(),
            EscrowStatus.CANCELLED.value: set(),
            EscrowStatus.EXPIRED.value: set(),
        }
    
    @property
    def terminal_states(self) -> Set[str]:
        """Terminal states that cannot transition further"""
        return {
            EscrowStatus.COMPLETED.value,
            EscrowStatus.REFUNDED.value,
            EscrowStatus.CANCELLED.value,
            EscrowStatus.EXPIRED.value,
        }
    
    @property
    def financial_states(self) -> Set[str]:
        """States that have financial impact"""
        return {
            EscrowStatus.ACTIVE.value,      # Funds held in escrow
            EscrowStatus.COMPLETED.value,   # Funds released to seller
            EscrowStatus.REFUNDED.value,    # Funds returned to buyer
            EscrowStatus.DISPUTED.value,    # Funds disputed
        }
    
    @property
    def state_field_name(self) -> str:
        """State field name in the Escrow model"""
        return "status"
    
    def get_primary_key_field(self) -> str:
        """Override for Escrow's primary key field"""
        return "escrow_id"
    
    def validate_business_rules(self, current_state: Optional[str], target_state: str, 
                              admin_override: bool = False) -> bool:
        """Validate escrow-specific business rules"""
        
        # BUSINESS RULE: User cancellation blocked for ACTIVE escrows
        if (current_state == EscrowStatus.ACTIVE.value and 
            target_state == EscrowStatus.CANCELLED.value and 
            not admin_override):
            logger.warning(
                f"⚠️ ESCROW_BUSINESS_RULE: User cancellation blocked for ACTIVE escrow {self.entity_id}"
            )
            return False
        
        # BUSINESS RULE: Only specific transitions allowed from DISPUTED
        if current_state == EscrowStatus.DISPUTED.value:
            allowed_from_disputed = {
                EscrowStatus.COMPLETED.value,
                EscrowStatus.REFUNDED.value,
                EscrowStatus.CANCELLED.value
            }
            if target_state not in allowed_from_disputed:
                logger.warning(
                    f"⚠️ ESCROW_BUSINESS_RULE: Invalid transition from DISPUTED to {target_state}"
                )
                return False
        
        return True
    
    def pre_transition_validation(self, entity: Escrow, current_state: Optional[str], 
                                target_state: str, context: StateTransitionContext) -> StateTransitionResult:
        """Pre-transition validation for escrow entities"""
        
        # Validate financial transitions
        if self.is_financial_state(target_state) or self.is_financial_state(current_state):
            if not context.amount or context.amount <= 0:
                return StateTransitionResult(
                    success=False,
                    error_message="Financial transitions require valid amount",
                    error_code="MISSING_AMOUNT"
                )
            
            if not context.currency:
                return StateTransitionResult(
                    success=False,
                    error_message="Financial transitions require currency",
                    error_code="MISSING_CURRENCY"
                )
        
        # Validate completion requirements
        if target_state == EscrowStatus.COMPLETED.value:
            if not entity.seller_id:
                return StateTransitionResult(
                    success=False,
                    error_message="Cannot complete escrow without seller",
                    error_code="MISSING_SELLER"
                )
        
        return StateTransitionResult(success=True, new_state=target_state)
    
    def post_transition_callback(self, entity: Escrow, old_state: Optional[str], 
                               new_state: str, context: StateTransitionContext) -> None:
        """Post-transition actions for escrow entities"""
        
        # Send notifications for key state changes
        if new_state in [EscrowStatus.COMPLETED.value, EscrowStatus.REFUNDED.value]:
            logger.info(f"📧 ESCROW_NOTIFICATION: {self.entity_id} transitioned to {new_state}")
            # Queue notification (implementation would depend on notification system)
        
        # Update related entities
        if new_state == EscrowStatus.ACTIVE.value:
            logger.info(f"💰 ESCROW_ACTIVATED: {self.entity_id} is now active with funds held")
        
        elif new_state == EscrowStatus.COMPLETED.value:
            logger.info(f"✅ ESCROW_COMPLETED: {self.entity_id} successfully completed")
        
        elif new_state == EscrowStatus.REFUNDED.value:
            logger.info(f"💸 ESCROW_REFUNDED: {self.entity_id} refunded to buyer")


class CashoutStateMachine(BaseStateMachine):
    """
    State machine for Cashout entities with external service integration
    
    Manages cashout lifecycle including OTP verification, admin approval,
    external service processing, and completion tracking
    """
    
    def __init__(self, cashout_id: str, lock_timeout: int = 30):
        super().__init__(cashout_id, Cashout, lock_timeout)
    
    @property
    def valid_transitions(self) -> Dict[Optional[str], Set[str]]:
        """Define valid cashout state transitions"""
        return {
            # Initial creation
            None: {CashoutStatus.PENDING.value},
            
            # Verification and approval flow
            CashoutStatus.PENDING.value: {
                CashoutStatus.OTP_PENDING.value,
                CashoutStatus.ADMIN_PENDING.value,
                CashoutStatus.CANCELLED.value,
                CashoutStatus.EXPIRED.value,
            },
            CashoutStatus.OTP_PENDING.value: {
                CashoutStatus.USER_CONFIRM_PENDING.value,
                CashoutStatus.ADMIN_PENDING.value,
                CashoutStatus.CANCELLED.value,
                CashoutStatus.EXPIRED.value,
            },
            CashoutStatus.USER_CONFIRM_PENDING.value: {
                CashoutStatus.ADMIN_PENDING.value,
                CashoutStatus.APPROVED.value,  # For immediate cashouts
                CashoutStatus.CANCELLED.value,
                CashoutStatus.EXPIRED.value,
            },
            CashoutStatus.ADMIN_PENDING.value: {
                CashoutStatus.APPROVED.value,
                CashoutStatus.PENDING_ADDRESS_CONFIG.value,
                CashoutStatus.PENDING_SERVICE_FUNDING.value,
                CashoutStatus.CANCELLED.value,
                CashoutStatus.EXPIRED.value,
            },
            
            # Admin configuration states
            CashoutStatus.PENDING_ADDRESS_CONFIG.value: {
                CashoutStatus.APPROVED.value,
                CashoutStatus.PENDING_SERVICE_FUNDING.value,
                CashoutStatus.CANCELLED.value,
            },
            CashoutStatus.PENDING_SERVICE_FUNDING.value: {
                CashoutStatus.APPROVED.value,
                CashoutStatus.CANCELLED.value,
            },
            
            # Processing states
            CashoutStatus.APPROVED.value: {
                CashoutStatus.EXECUTING.value,
                CashoutStatus.PROCESSING.value,  # For immediate cashouts
                CashoutStatus.CANCELLED.value,
            },
            CashoutStatus.EXECUTING.value: {
                CashoutStatus.PROCESSING.value,
                CashoutStatus.AWAITING_RESPONSE.value,
                CashoutStatus.FAILED.value,
                CashoutStatus.CANCELLED.value,
            },
            CashoutStatus.PROCESSING.value: {
                CashoutStatus.AWAITING_RESPONSE.value,
                CashoutStatus.SUCCESS.value,
                CashoutStatus.COMPLETED.value,  # Legacy support
                CashoutStatus.FAILED.value,
            },
            CashoutStatus.AWAITING_RESPONSE.value: {
                CashoutStatus.SUCCESS.value,
                CashoutStatus.COMPLETED.value,  # Legacy support
                CashoutStatus.FAILED.value,
            },
            
            # Terminal states
            CashoutStatus.SUCCESS.value: set(),
            CashoutStatus.COMPLETED.value: set(),  # Legacy
            CashoutStatus.FAILED.value: set(),
            CashoutStatus.CANCELLED.value: set(),
            CashoutStatus.EXPIRED.value: set(),
        }
    
    @property
    def terminal_states(self) -> Set[str]:
        """Terminal states that cannot transition further"""
        return {
            CashoutStatus.SUCCESS.value,
            CashoutStatus.COMPLETED.value,
            CashoutStatus.FAILED.value,
            CashoutStatus.CANCELLED.value,
            CashoutStatus.EXPIRED.value,
        }
    
    @property
    def financial_states(self) -> Set[str]:
        """States that have financial impact"""
        return {
            CashoutStatus.APPROVED.value,         # Funds locked
            CashoutStatus.EXECUTING.value,        # Funds in transit
            CashoutStatus.PROCESSING.value,       # Funds being processed
            CashoutStatus.AWAITING_RESPONSE.value, # Funds with external service
            CashoutStatus.SUCCESS.value,          # Funds successfully transferred
            CashoutStatus.COMPLETED.value,        # Legacy successful transfer
            CashoutStatus.FAILED.value,           # Funds need to be unlocked
        }
    
    @property
    def state_field_name(self) -> str:
        """State field name in the Cashout model"""
        return "status"
    
    def get_primary_key_field(self) -> str:
        """Override for Cashout's primary key field"""
        return "cashout_id"
    
    def validate_business_rules(self, current_state: Optional[str], target_state: str, 
                              admin_override: bool = False) -> bool:
        """Validate cashout-specific business rules"""
        
        # BUSINESS RULE: OTP verification required for user-initiated cashouts
        if (current_state == CashoutStatus.PENDING.value and 
            target_state == CashoutStatus.ADMIN_PENDING.value):
            # This transition should be validated at the handler level
            # to ensure OTP was verified if required
            pass
        
        # BUSINESS RULE: Admin approval required for large amounts
        if (current_state == CashoutStatus.USER_CONFIRM_PENDING.value and
            target_state == CashoutStatus.APPROVED.value and
            not admin_override):
            # This should be validated at the handler level based on amount thresholds
            pass
        
        # BUSINESS RULE: Cannot go from FAILED back to processing without admin intervention
        if (current_state == CashoutStatus.FAILED.value and 
            target_state in [CashoutStatus.EXECUTING.value, CashoutStatus.PROCESSING.value] and
            not admin_override):
            logger.warning(
                f"⚠️ CASHOUT_BUSINESS_RULE: Cannot retry failed cashout {self.entity_id} without admin approval"
            )
            return False
        
        return True
    
    def pre_transition_validation(self, entity: Cashout, current_state: Optional[str], 
                                target_state: str, context: StateTransitionContext) -> StateTransitionResult:
        """Pre-transition validation for cashout entities"""
        
        # Validate financial transitions require amounts
        if self.is_financial_state(target_state):
            if entity.amount <= 0:
                return StateTransitionResult(
                    success=False,
                    error_message="Invalid cashout amount",
                    error_code="INVALID_AMOUNT"
                )
        
        # Validate processing transitions have external reference
        if target_state in [CashoutStatus.PROCESSING.value, CashoutStatus.AWAITING_RESPONSE.value]:
            if not entity.external_tx_id:
                return StateTransitionResult(
                    success=False,
                    error_message="External transaction ID required for processing states",
                    error_code="MISSING_EXTERNAL_ID"
                )
        
        return StateTransitionResult(success=True, new_state=target_state)
    
    def post_transition_callback(self, entity: Cashout, old_state: Optional[str], 
                               new_state: str, context: StateTransitionContext) -> None:
        """Post-transition actions for cashout entities"""
        
        # Update timestamps based on state
        if new_state == CashoutStatus.PROCESSING.value:
            logger.info(f"⚡ CASHOUT_PROCESSING: {self.entity_id} started processing")
        
        elif new_state == CashoutStatus.SUCCESS.value:
            logger.info(f"✅ CASHOUT_SUCCESS: {self.entity_id} completed successfully")
        
        elif new_state == CashoutStatus.FAILED.value:
            logger.error(f"❌ CASHOUT_FAILED: {self.entity_id} processing failed")
        
        # Queue notifications for terminal states
        if new_state in self.terminal_states:
            logger.info(f"📧 CASHOUT_NOTIFICATION: {self.entity_id} reached terminal state {new_state}")


class UnifiedTransactionStateMachine(BaseStateMachine):
    """
    State machine for UnifiedTransaction entities
    
    Manages the complete lifecycle of unified transactions including
    creation, authorization, processing, and completion
    """
    
    def __init__(self, transaction_id: str, lock_timeout: int = 30):
        super().__init__(transaction_id, UnifiedTransaction, lock_timeout)
    
    @property
    def valid_transitions(self) -> Dict[Optional[str], Set[str]]:
        """Define valid unified transaction state transitions"""
        return {
            # Initial states
            None: {UnifiedTransactionStatus.CREATED.value},
            
            # Authorization flow
            UnifiedTransactionStatus.CREATED.value: {
                UnifiedTransactionStatus.VALIDATION_PENDING.value,
                UnifiedTransactionStatus.AUTHORIZATION_PENDING.value,
                UnifiedTransactionStatus.FUNDS_HELD.value,  # Direct for some transaction types
                UnifiedTransactionStatus.CANCELLED.value,
                UnifiedTransactionStatus.EXPIRED.value,
            },
            UnifiedTransactionStatus.VALIDATION_PENDING.value: {
                UnifiedTransactionStatus.AUTHORIZATION_PENDING.value,
                UnifiedTransactionStatus.VALIDATION_FAILED.value,
                UnifiedTransactionStatus.CANCELLED.value,
            },
            UnifiedTransactionStatus.AUTHORIZATION_PENDING.value: {
                UnifiedTransactionStatus.FUNDS_HELD.value,
                UnifiedTransactionStatus.AUTHORIZATION_FAILED.value,
                UnifiedTransactionStatus.CANCELLED.value,
                UnifiedTransactionStatus.EXPIRED.value,
            },
            
            # Processing flow
            UnifiedTransactionStatus.FUNDS_HELD.value: {
                UnifiedTransactionStatus.PROCESSING.value,
                UnifiedTransactionStatus.EXTERNAL_PROCESSING.value,
                UnifiedTransactionStatus.CANCELLED.value,
            },
            UnifiedTransactionStatus.PROCESSING.value: {
                UnifiedTransactionStatus.EXTERNAL_PROCESSING.value,
                UnifiedTransactionStatus.CONFIRMATION_PENDING.value,
                UnifiedTransactionStatus.SUCCESS.value,
                UnifiedTransactionStatus.PROCESSING_FAILED.value,
            },
            UnifiedTransactionStatus.EXTERNAL_PROCESSING.value: {
                UnifiedTransactionStatus.CONFIRMATION_PENDING.value,
                UnifiedTransactionStatus.SUCCESS.value,
                UnifiedTransactionStatus.PROCESSING_FAILED.value,
            },
            UnifiedTransactionStatus.CONFIRMATION_PENDING.value: {
                UnifiedTransactionStatus.SUCCESS.value,
                UnifiedTransactionStatus.PROCESSING_FAILED.value,
            },
            
            # Failure and retry states
            UnifiedTransactionStatus.VALIDATION_FAILED.value: {
                UnifiedTransactionStatus.VALIDATION_PENDING.value,  # Retry allowed
                UnifiedTransactionStatus.CANCELLED.value,
            },
            UnifiedTransactionStatus.AUTHORIZATION_FAILED.value: {
                UnifiedTransactionStatus.AUTHORIZATION_PENDING.value,  # Retry allowed
                UnifiedTransactionStatus.CANCELLED.value,
            },
            UnifiedTransactionStatus.PROCESSING_FAILED.value: {
                UnifiedTransactionStatus.PROCESSING.value,  # Retry allowed with admin approval
                UnifiedTransactionStatus.CANCELLED.value,
            },
            
            # Terminal states
            UnifiedTransactionStatus.SUCCESS.value: set(),
            UnifiedTransactionStatus.CANCELLED.value: set(),
            UnifiedTransactionStatus.EXPIRED.value: set(),
        }
    
    @property
    def terminal_states(self) -> Set[str]:
        """Terminal states that cannot transition further"""
        return {
            UnifiedTransactionStatus.SUCCESS.value,
            UnifiedTransactionStatus.CANCELLED.value,
            UnifiedTransactionStatus.EXPIRED.value,
        }
    
    @property
    def financial_states(self) -> Set[str]:
        """States that have financial impact"""
        return {
            UnifiedTransactionStatus.FUNDS_HELD.value,           # Funds locked
            UnifiedTransactionStatus.PROCESSING.value,          # Funds in transit
            UnifiedTransactionStatus.EXTERNAL_PROCESSING.value, # Funds with external service
            UnifiedTransactionStatus.CONFIRMATION_PENDING.value, # Funds pending confirmation
            UnifiedTransactionStatus.SUCCESS.value,             # Funds transferred
        }
    
    @property
    def state_field_name(self) -> str:
        """State field name in the UnifiedTransaction model"""
        return "status"
    
    def get_primary_key_field(self) -> str:
        """Override for UnifiedTransaction's primary key field"""
        return "transaction_id"
    
    def validate_business_rules(self, current_state: Optional[str], target_state: str, 
                              admin_override: bool = False) -> bool:
        """Validate unified transaction-specific business rules"""
        
        # BUSINESS RULE: Retry from failed states requires admin approval
        if (current_state in [
            UnifiedTransactionStatus.PROCESSING_FAILED.value,
            UnifiedTransactionStatus.AUTHORIZATION_FAILED.value
        ] and target_state in [
            UnifiedTransactionStatus.PROCESSING.value,
            UnifiedTransactionStatus.AUTHORIZATION_PENDING.value
        ] and not admin_override):
            logger.warning(
                f"⚠️ UNIFIED_TX_BUSINESS_RULE: Retry from {current_state} requires admin approval"
            )
            return False
        
        return True
    
    def pre_transition_validation(self, entity: UnifiedTransaction, current_state: Optional[str], 
                                target_state: str, context: StateTransitionContext) -> StateTransitionResult:
        """Pre-transition validation for unified transactions"""
        
        # Validate financial transitions
        if self.is_financial_state(target_state):
            if entity.amount <= 0:
                return StateTransitionResult(
                    success=False,
                    error_message="Invalid transaction amount",
                    error_code="INVALID_AMOUNT"
                )
        
        # Validate authorization requirements
        if target_state == UnifiedTransactionStatus.FUNDS_HELD.value:
            if entity.requires_otp and not entity.otp_verified:
                return StateTransitionResult(
                    success=False,
                    error_message="OTP verification required",
                    error_code="OTP_REQUIRED"
                )
            
            if entity.requires_admin_approval and not entity.admin_approved:
                return StateTransitionResult(
                    success=False,
                    error_message="Admin approval required",
                    error_code="ADMIN_APPROVAL_REQUIRED"
                )
        
        return StateTransitionResult(success=True, new_state=target_state)
    
    def post_transition_callback(self, entity: UnifiedTransaction, old_state: Optional[str], 
                               new_state: str, context: StateTransitionContext) -> None:
        """Post-transition actions for unified transactions"""
        
        # Update timestamps based on state changes
        if new_state == UnifiedTransactionStatus.FUNDS_HELD.value:
            logger.info(f"💰 UNIFIED_TX_FUNDS_HELD: {self.entity_id} funds locked")
        
        elif new_state == UnifiedTransactionStatus.PROCESSING.value:
            logger.info(f"⚡ UNIFIED_TX_PROCESSING: {self.entity_id} started processing")
        
        elif new_state == UnifiedTransactionStatus.SUCCESS.value:
            logger.info(f"✅ UNIFIED_TX_SUCCESS: {self.entity_id} completed successfully")


class WalletHoldStateMachine(BaseStateMachine):
    """
    State machine for Wallet Hold operations
    
    Manages the lifecycle of fund holds including creation, consumption,
    and release back to available balance
    """
    
    def __init__(self, hold_id: str, lock_timeout: int = 30):
        # Note: This would need a WalletHolds model if it doesn't exist
        # For now, using Wallet as placeholder
        super().__init__(hold_id, Wallet, lock_timeout)
    
    @property
    def valid_transitions(self) -> Dict[Optional[str], Set[str]]:
        """Define valid wallet hold state transitions"""
        return {
            # Initial state
            None: {WalletHoldStatus.HELD.value},
            
            # Active hold states
            WalletHoldStatus.HELD.value: {
                WalletHoldStatus.CONSUMED_SENT.value,    # Normal processing
                WalletHoldStatus.FAILED_HELD.value,      # Technical failure
                WalletHoldStatus.CANCELLED_HELD.value,   # User cancellation
                WalletHoldStatus.DISPUTED_HELD.value,    # Dispute raised
            },
            
            # Processing states
            WalletHoldStatus.CONSUMED_SENT.value: {
                WalletHoldStatus.SETTLED.value,          # Successful completion
                WalletHoldStatus.FAILED_HELD.value,      # External service failure
            },
            
            # Failed states - require admin intervention
            WalletHoldStatus.FAILED_HELD.value: {
                WalletHoldStatus.REFUND_APPROVED.value,  # Admin approves refund
                WalletHoldStatus.CONSUMED_SENT.value,    # Admin approves retry
            },
            WalletHoldStatus.CANCELLED_HELD.value: {
                WalletHoldStatus.REFUND_APPROVED.value,  # Admin approves refund
            },
            WalletHoldStatus.DISPUTED_HELD.value: {
                WalletHoldStatus.REFUND_APPROVED.value,  # Admin resolves dispute
                WalletHoldStatus.SETTLED.value,          # Admin resolves in favor of original transaction
            },
            
            # Admin resolution states
            WalletHoldStatus.REFUND_APPROVED.value: {
                WalletHoldStatus.RELEASED.value,         # Funds returned to available balance
            },
            
            # Terminal states
            WalletHoldStatus.SETTLED.value: set(),
            WalletHoldStatus.RELEASED.value: set(),
        }
    
    @property
    def terminal_states(self) -> Set[str]:
        """Terminal states that cannot transition further"""
        return {
            WalletHoldStatus.SETTLED.value,
            WalletHoldStatus.RELEASED.value,
        }
    
    @property
    def financial_states(self) -> Set[str]:
        """All wallet hold states have financial impact"""
        return {
            WalletHoldStatus.HELD.value,
            WalletHoldStatus.CONSUMED_SENT.value,
            WalletHoldStatus.SETTLED.value,
            WalletHoldStatus.FAILED_HELD.value,
            WalletHoldStatus.CANCELLED_HELD.value,
            WalletHoldStatus.DISPUTED_HELD.value,
            WalletHoldStatus.REFUND_APPROVED.value,
            WalletHoldStatus.RELEASED.value,
        }
    
    @property
    def state_field_name(self) -> str:
        """State field name (would be in WalletHolds model)"""
        return "status"
    
    def validate_business_rules(self, current_state: Optional[str], target_state: str, 
                              admin_override: bool = False) -> bool:
        """Validate wallet hold-specific business rules"""
        
        # CRITICAL BUSINESS RULE: Frozen funds NEVER auto-release to available
        if (current_state in [
            WalletHoldStatus.FAILED_HELD.value,
            WalletHoldStatus.CANCELLED_HELD.value,
            WalletHoldStatus.DISPUTED_HELD.value
        ] and target_state == WalletHoldStatus.RELEASED.value and not admin_override):
            logger.critical(
                f"🚨 WALLET_SECURITY_VIOLATION: Attempted auto-release of frozen funds {self.entity_id}"
            )
            return False
        
        # BUSINESS RULE: Only admin can approve refunds
        if (target_state == WalletHoldStatus.REFUND_APPROVED.value and not admin_override):
            logger.warning(
                f"⚠️ WALLET_BUSINESS_RULE: Only admin can approve refunds for hold {self.entity_id}"
            )
            return False
        
        return True
    
    def pre_transition_validation(self, entity: Any, current_state: Optional[str], 
                                target_state: str, context: StateTransitionContext) -> StateTransitionResult:
        """Pre-transition validation for wallet holds"""
        
        # All wallet hold transitions require amounts
        if not context.amount or context.amount <= 0:
            return StateTransitionResult(
                success=False,
                error_message="Wallet hold operations require valid amount",
                error_code="MISSING_AMOUNT"
            )
        
        return StateTransitionResult(success=True, new_state=target_state)
    
    def post_transition_callback(self, entity: Any, old_state: Optional[str], 
                               new_state: str, context: StateTransitionContext) -> None:
        """Post-transition actions for wallet holds"""
        
        # Log critical financial state changes
        if new_state == WalletHoldStatus.SETTLED.value:
            logger.info(f"💰 WALLET_HOLD_SETTLED: {self.entity_id} funds permanently consumed")
        
        elif new_state == WalletHoldStatus.RELEASED.value:
            logger.info(f"💸 WALLET_HOLD_RELEASED: {self.entity_id} funds returned to available balance")
        
        elif new_state in [
            WalletHoldStatus.FAILED_HELD.value,
            WalletHoldStatus.CANCELLED_HELD.value,
            WalletHoldStatus.DISPUTED_HELD.value
        ]:
            logger.critical(
                f"🚨 WALLET_HOLD_FROZEN: {self.entity_id} funds frozen in state {new_state} - ADMIN_REVIEW_REQUIRED"
            )


# Factory functions for easy state machine creation
def create_escrow_state_machine(escrow_id: str) -> EscrowStateMachine:
    """Create escrow state machine instance"""
    return EscrowStateMachine(escrow_id)


def create_cashout_state_machine(cashout_id: str) -> CashoutStateMachine:
    """Create cashout state machine instance"""
    return CashoutStateMachine(cashout_id)


def create_unified_transaction_state_machine(transaction_id: str) -> UnifiedTransactionStateMachine:
    """Create unified transaction state machine instance"""
    return UnifiedTransactionStateMachine(transaction_id)


def create_wallet_hold_state_machine(hold_id: str) -> WalletHoldStateMachine:
    """Create wallet hold state machine instance"""
    return WalletHoldStateMachine(hold_id)


# Export main classes and factory functions
__all__ = [
    "EscrowStateMachine",
    "CashoutStateMachine", 
    "UnifiedTransactionStateMachine",
    "WalletHoldStateMachine",
    "create_escrow_state_machine",
    "create_cashout_state_machine",
    "create_unified_transaction_state_machine",
    "create_wallet_hold_state_machine",
]