"""
Core Payment Data Structures

Simplified, standardized data structures for the unified PaymentProcessor architecture.
Replaces the complex overlapping data structures from 100+ services with clean, simple interfaces.
"""

from typing import Dict, Any, Optional, Union, List
from dataclasses import dataclass, field
from decimal import Decimal
from datetime import datetime
from enum import Enum


class TransactionStatus(Enum):
    """Simplified transaction status with only 5 states"""
    PENDING = "pending"         # Initial state, waiting to be processed
    PROCESSING = "processing"   # Currently being processed by external provider
    AWAITING = "awaiting"       # Waiting for external confirmation/user action
    SUCCESS = "success"         # Completed successfully
    FAILED = "failed"          # Failed with error


class PaymentError(Enum):
    """Simple payment error classification"""
    TECHNICAL = "technical"     # Temporary technical issues (retryable)
    BUSINESS = "business"       # Business logic issues (user fixable)
    PERMANENT = "permanent"     # Permanent failures (not retryable)


class PaymentDirection(Enum):
    """Payment direction for routing"""
    PAYIN = "payin"    # Money coming in (deposits, escrow payments)
    PAYOUT = "payout"  # Money going out (withdrawals, cashouts)


class PaymentProvider(Enum):
    """Supported payment providers"""
    FINCRA = "fincra"      # NGN payments
    KRAKEN = "kraken"      # Crypto withdrawals
    BLOCKBEE = "blockbee"  # Crypto deposits


@dataclass
class PaymentDestination:
    """Unified destination for payouts"""
    type: str  # "crypto_address", "bank_account", "saved_address"
    address: Optional[str] = None
    bank_code: Optional[str] = None
    account_number: Optional[str] = None
    account_name: Optional[str] = None
    currency: Optional[str] = None
    network: Optional[str] = None
    memo: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class PayinRequest:
    """Standardized request for incoming payments"""
    # Core fields
    user_id: int
    amount: Union[Decimal, float]
    currency: str
    
    # Payment context
    payment_type: str  # "escrow", "exchange_buy", "wallet_deposit"
    reference_id: Optional[str] = None  # escrow_id, exchange_id, etc.
    
    # Optional metadata
    metadata: Dict[str, Any] = field(default_factory=dict)
    callback_url: Optional[str] = None
    return_url: Optional[str] = None
    
    # Provider preferences
    preferred_provider: Optional[PaymentProvider] = None
    
    def __post_init__(self):
        """Convert amount to Decimal for precision"""
        if isinstance(self.amount, (int, float)):
            self.amount = Decimal(str(self.amount))


@dataclass  
class PayoutRequest:
    """Standardized request for outgoing payments"""
    # Core fields
    user_id: int
    amount: Union[Decimal, float]
    currency: str
    destination: PaymentDestination
    
    # Payment context
    payment_type: str  # "cashout", "refund", "escrow_release"
    reference_id: Optional[str] = None  # cashout_id, escrow_id, etc.
    
    # Optional fields
    metadata: Dict[str, Any] = field(default_factory=dict)
    priority: str = "normal"  # "normal", "high", "urgent"
    requires_otp: bool = True  # Default to requiring OTP for security
    
    # Provider preferences
    preferred_provider: Optional[PaymentProvider] = None
    
    def __post_init__(self):
        """Convert amount to Decimal for precision"""
        if isinstance(self.amount, (int, float)):
            self.amount = Decimal(str(self.amount))


@dataclass
class PaymentResult:
    """Standardized result for all payment operations"""
    # Core result
    success: bool
    status: TransactionStatus
    transaction_id: Optional[str] = None
    provider_transaction_id: Optional[str] = None
    
    # Error handling
    error: Optional[PaymentError] = None
    error_message: Optional[str] = None
    error_code: Optional[str] = None
    
    # Status information
    requires_otp: bool = False
    requires_user_action: bool = False
    next_action: Optional[str] = None
    
    # Payment details
    actual_amount: Optional[Decimal] = None
    fees_charged: Optional[Decimal] = None
    exchange_rate: Optional[Decimal] = None
    
    # Provider information
    provider: Optional[PaymentProvider] = None
    provider_reference: Optional[str] = None
    estimated_completion: Optional[datetime] = None
    
    # Additional data
    payment_details: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization"""
        result = {}
        for key, value in self.__dict__.items():
            if value is not None:
                if isinstance(value, (TransactionStatus, PaymentError, PaymentProvider)):
                    result[key] = value.value
                elif isinstance(value, Decimal):
                    result[key] = float(value)
                elif isinstance(value, datetime):
                    result[key] = value.isoformat()
                else:
                    result[key] = value
        return result


@dataclass
class BalanceSnapshot:
    """Simplified balance information"""
    provider: PaymentProvider
    currency: str
    available_balance: Decimal
    total_balance: Decimal
    locked_balance: Decimal = field(default=Decimal('0'))
    last_updated: datetime = field(default_factory=datetime.utcnow)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'provider': self.provider.value,
            'currency': self.currency,
            'available_balance': float(self.available_balance),
            'total_balance': float(self.total_balance),
            'locked_balance': float(self.locked_balance),
            'last_updated': self.last_updated.isoformat()
        }


@dataclass
class BalanceCheckResult:
    """Result of balance checking operation"""
    success: bool
    balances: List[BalanceSnapshot] = field(default_factory=list)
    error_message: Optional[str] = None
    total_usd_value: Optional[Decimal] = None
    
    def get_balance_by_currency(self, currency: str) -> List[BalanceSnapshot]:
        """Get all balances for a specific currency"""
        return [b for b in self.balances if b.currency.upper() == currency.upper()]
    
    def get_balance_by_provider(self, provider: PaymentProvider) -> List[BalanceSnapshot]:
        """Get all balances for a specific provider"""
        return [b for b in self.balances if b.provider == provider]


# State transition rules - defines what state transitions are valid
VALID_STATE_TRANSITIONS = {
    TransactionStatus.PENDING: {
        TransactionStatus.PROCESSING,
        TransactionStatus.AWAITING, 
        TransactionStatus.SUCCESS,
        TransactionStatus.FAILED
    },
    TransactionStatus.PROCESSING: {
        TransactionStatus.AWAITING,
        TransactionStatus.SUCCESS,
        TransactionStatus.FAILED,
        TransactionStatus.PENDING  # For retry scenarios
    },
    TransactionStatus.AWAITING: {
        TransactionStatus.PROCESSING,
        TransactionStatus.SUCCESS,
        TransactionStatus.FAILED,
        TransactionStatus.PENDING  # For timeout/retry scenarios
    },
    TransactionStatus.SUCCESS: set(),  # Terminal state
    TransactionStatus.FAILED: {
        TransactionStatus.PENDING  # For manual retry/reprocessing
    }
}

# Comprehensive legacy status mappings for all existing state systems
LEGACY_STATUS_MAPPING = {
    # === UnifiedTransactionStatus mappings (16 states → 5) ===
    "pending": TransactionStatus.PENDING,
    "awaiting_payment": TransactionStatus.AWAITING,
    "payment_confirmed": TransactionStatus.PROCESSING,
    "funds_held": TransactionStatus.PROCESSING,
    "awaiting_approval": TransactionStatus.AWAITING,
    "otp_pending": TransactionStatus.AWAITING,
    "admin_pending": TransactionStatus.AWAITING,
    "processing": TransactionStatus.PROCESSING,
    "awaiting_response": TransactionStatus.AWAITING,
    "release_pending": TransactionStatus.PROCESSING,
    "success": TransactionStatus.SUCCESS,
    "failed": TransactionStatus.FAILED,
    "cancelled": TransactionStatus.FAILED,
    "disputed": TransactionStatus.AWAITING,  # Under review
    "expired": TransactionStatus.FAILED,
    "partial_payment": TransactionStatus.AWAITING,
    
    # === CashoutStatus mappings (15 states → 5) ===
    "user_confirm_pending": TransactionStatus.AWAITING,
    "pending_config": TransactionStatus.AWAITING,
    "pending_address_config": TransactionStatus.AWAITING,
    "pending_funding": TransactionStatus.AWAITING,
    "pending_service_funding": TransactionStatus.AWAITING,
    "approved": TransactionStatus.PROCESSING,
    "executing": TransactionStatus.PROCESSING,
    "completed": TransactionStatus.SUCCESS,
    
    # === EscrowStatus mappings (13 states → 5) ===
    "created": TransactionStatus.PENDING,
    "payment_pending": TransactionStatus.AWAITING,
    "awaiting_seller": TransactionStatus.AWAITING,
    "pending_seller": TransactionStatus.AWAITING,
    "pending_deposit": TransactionStatus.AWAITING,
    "active": TransactionStatus.PROCESSING,
    "refunded": TransactionStatus.SUCCESS,  # Successful refund completion
    
    # === ExchangeStatus mappings (11 states → 5) ===
    "awaiting_deposit": TransactionStatus.AWAITING,
    "rate_locked": TransactionStatus.PROCESSING,
    "payment_received": TransactionStatus.PROCESSING,
    "address_generation_failed": TransactionStatus.FAILED,
    "pending_approval": TransactionStatus.AWAITING,
    
    # === WalletHoldStatus mappings (9 states → 5) ===
    "held": TransactionStatus.PROCESSING,
    "consumed_sent": TransactionStatus.PROCESSING,
    "settled": TransactionStatus.SUCCESS,
    "failed_held": TransactionStatus.FAILED,
    "cancelled_held": TransactionStatus.FAILED,
    "disputed_held": TransactionStatus.AWAITING,
    "refund_approved": TransactionStatus.PROCESSING,
    "released": TransactionStatus.SUCCESS,
    "failed_refunded": TransactionStatus.FAILED,
    
    # === Generic mappings ===
    "requested": TransactionStatus.PENDING,
    "confirmed": TransactionStatus.SUCCESS,
    "rejected": TransactionStatus.FAILED,
    "timeout": TransactionStatus.FAILED,
    "retrying": TransactionStatus.PROCESSING,
    "queued": TransactionStatus.PENDING,
    "submitted": TransactionStatus.PROCESSING,
}


def map_legacy_status(legacy_status: str) -> TransactionStatus:
    """
    Map legacy status values to new simplified 5-state system
    
    Args:
        legacy_status: Status from any legacy system
        
    Returns:
        TransactionStatus: Corresponding simplified state
    """
    if not legacy_status:
        return TransactionStatus.PENDING
    
    return LEGACY_STATUS_MAPPING.get(legacy_status.lower(), TransactionStatus.PENDING)


def is_valid_transition(from_status: TransactionStatus, to_status: TransactionStatus) -> bool:
    """
    Check if a state transition is valid according to business rules
    
    Args:
        from_status: Current transaction status
        to_status: Target transaction status
        
    Returns:
        bool: True if transition is valid, False otherwise
    """
    if from_status == to_status:
        return True  # Same state is always valid (idempotent)
    
    valid_targets = VALID_STATE_TRANSITIONS.get(from_status, set())
    return to_status in valid_targets


def get_valid_transitions(current_status: TransactionStatus) -> List[TransactionStatus]:
    """
    Get list of valid state transitions from current status
    
    Args:
        current_status: Current transaction status
        
    Returns:
        List[TransactionStatus]: Valid target states
    """
    return list(VALID_STATE_TRANSITIONS.get(current_status, set()))


def is_terminal_state(status: TransactionStatus) -> bool:
    """
    Check if a status is a terminal state (no further transitions allowed)
    
    Args:
        status: Transaction status to check
        
    Returns:
        bool: True if terminal state, False otherwise
    """
    return len(VALID_STATE_TRANSITIONS.get(status, set())) == 0


def is_error_state(status: TransactionStatus) -> bool:
    """
    Check if a status represents an error condition
    
    Args:
        status: Transaction status to check
        
    Returns:
        bool: True if error state, False otherwise
    """
    return status == TransactionStatus.FAILED


def is_waiting_state(status: TransactionStatus) -> bool:
    """
    Check if a status represents waiting for external action
    
    Args:
        status: Transaction status to check
        
    Returns:
        bool: True if waiting state, False otherwise
    """
    return status == TransactionStatus.AWAITING


def is_active_processing_state(status: TransactionStatus) -> bool:
    """
    Check if a status represents active processing
    
    Args:
        status: Transaction status to check
        
    Returns:
        bool: True if actively processing, False otherwise
    """
    return status == TransactionStatus.PROCESSING


def get_status_category(status: TransactionStatus) -> str:
    """
    Get human-readable category for a status
    
    Args:
        status: Transaction status
        
    Returns:
        str: Category description
    """
    if status == TransactionStatus.PENDING:
        return "Queued for Processing"
    elif status == TransactionStatus.PROCESSING:
        return "Processing"
    elif status == TransactionStatus.AWAITING:
        return "Waiting for Action"
    elif status == TransactionStatus.SUCCESS:
        return "Completed Successfully"
    elif status == TransactionStatus.FAILED:
        return "Failed"
    else:
        return "Unknown Status"


def map_provider_status_to_unified(provider: PaymentProvider, provider_status: str) -> TransactionStatus:
    """
    Map provider-specific status to unified 5-state system
    
    Args:
        provider: Payment provider
        provider_status: Status from the provider
        
    Returns:
        TransactionStatus: Unified status
    """
    # Provider-specific mappings
    provider_mappings = {
        PaymentProvider.FINCRA: {
            "pending": TransactionStatus.PENDING,
            "successful": TransactionStatus.SUCCESS,
            "failed": TransactionStatus.FAILED,
            "cancelled": TransactionStatus.FAILED,
            "processing": TransactionStatus.PROCESSING,
            "initiated": TransactionStatus.PROCESSING,
        },
        PaymentProvider.KRAKEN: {
            "pending": TransactionStatus.PENDING,
            "success": TransactionStatus.SUCCESS,
            "failure": TransactionStatus.FAILED,
            "canceled": TransactionStatus.FAILED,
            "settled": TransactionStatus.SUCCESS,
            "initial": TransactionStatus.PENDING,
        },
        PaymentProvider.BLOCKBEE: {
            "pending": TransactionStatus.PENDING,
            "confirmed": TransactionStatus.SUCCESS,
            "unconfirmed": TransactionStatus.AWAITING,
            "expired": TransactionStatus.FAILED,
            "cancelled": TransactionStatus.FAILED,
        }
    }
    
    provider_map = provider_mappings.get(provider, {})
    return provider_map.get(provider_status.lower(), map_legacy_status(provider_status))


class StateTransitionError(Exception):
    """Exception raised for invalid state transitions"""
    def __init__(self, from_status: TransactionStatus, to_status: TransactionStatus, reason: str = ""):
        self.from_status = from_status
        self.to_status = to_status
        self.reason = reason
        message = f"Invalid transition from {from_status.value} to {to_status.value}"
        if reason:
            message += f": {reason}"
        super().__init__(message)


def validate_state_transition(from_status: TransactionStatus, to_status: TransactionStatus) -> None:
    """
    Validate a state transition and raise exception if invalid
    
    Args:
        from_status: Current status
        to_status: Target status
        
    Raises:
        StateTransitionError: If transition is invalid
    """
    if not is_valid_transition(from_status, to_status):
        valid_transitions = get_valid_transitions(from_status)
        reason = f"Valid transitions from {from_status.value}: {[s.value for s in valid_transitions]}"
        raise StateTransitionError(from_status, to_status, reason)


def create_success_result(
    transaction_id: str,
    provider: PaymentProvider,
    amount: Decimal,
    **kwargs
) -> PaymentResult:
    """Helper to create successful payment result"""
    return PaymentResult(
        success=True,
        status=TransactionStatus.SUCCESS,
        transaction_id=transaction_id,
        provider=provider,
        actual_amount=amount,
        **kwargs
    )


def create_error_result(
    error: PaymentError,
    message: str,
    status: TransactionStatus = TransactionStatus.FAILED,
    **kwargs
) -> PaymentResult:
    """Helper to create error payment result"""
    return PaymentResult(
        success=False,
        status=status,
        error=error,
        error_message=message,
        **kwargs
    )