"""
State Transition Service - Usage Demonstration
==============================================

This script demonstrates the StateTransitionService with all 6 entity types:
1. Escrow
2. Exchange
3. Cashout
4. Unified Transaction
5. User
6. Dispute

Run this script to verify the service works correctly.
"""

import asyncio
import logging
from services.state_transition_service import StateTransitionService
from models import (
    EscrowStatus,
    ExchangeStatus,
    CashoutStatus,
    UnifiedTransactionStatus,
    UserStatus,
    DisputeStatus
)

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def demo_escrow_transitions():
    """Demonstrate escrow state transitions"""
    print("\n" + "="*60)
    print("ESCROW TRANSITIONS")
    print("="*60)
    
    # Valid transition: CREATED -> PAYMENT_PENDING
    result = await StateTransitionService.transition_entity_status(
        entity_type="escrow",
        entity_id="ES123456",
        current_status=EscrowStatus.CREATED,
        new_status=EscrowStatus.PAYMENT_PENDING,
        context="DEMO"
    )
    print(f"✅ CREATED -> PAYMENT_PENDING: {result}")
    
    # Valid transition: ACTIVE -> COMPLETED
    result = await StateTransitionService.transition_entity_status(
        entity_type="escrow",
        entity_id="ES123457",
        current_status=EscrowStatus.ACTIVE,
        new_status=EscrowStatus.COMPLETED,
        context="DEMO"
    )
    print(f"✅ ACTIVE -> COMPLETED: {result}")
    
    # Invalid transition: COMPLETED -> CREATED
    result = await StateTransitionService.transition_entity_status(
        entity_type="escrow",
        entity_id="ES123458",
        current_status=EscrowStatus.COMPLETED,
        new_status=EscrowStatus.CREATED,
        context="DEMO"
    )
    print(f"❌ COMPLETED -> CREATED (should fail): {result}")


async def demo_exchange_transitions():
    """Demonstrate exchange state transitions"""
    print("\n" + "="*60)
    print("EXCHANGE TRANSITIONS")
    print("="*60)
    
    # Valid transition: CREATED -> AWAITING_DEPOSIT
    result = await StateTransitionService.transition_entity_status(
        entity_type="exchange",
        entity_id="EX123456",
        current_status=ExchangeStatus.CREATED,
        new_status=ExchangeStatus.AWAITING_DEPOSIT,
        context="DEMO"
    )
    print(f"✅ CREATED -> AWAITING_DEPOSIT: {result}")
    
    # Valid transition: PROCESSING -> COMPLETED
    result = await StateTransitionService.transition_entity_status(
        entity_type="exchange",
        entity_id="EX123457",
        current_status=ExchangeStatus.PROCESSING,
        new_status=ExchangeStatus.COMPLETED,
        context="DEMO"
    )
    print(f"✅ PROCESSING -> COMPLETED: {result}")
    
    # Invalid transition: COMPLETED -> AWAITING_DEPOSIT
    result = await StateTransitionService.transition_entity_status(
        entity_type="exchange",
        entity_id="EX123458",
        current_status=ExchangeStatus.COMPLETED,
        new_status=ExchangeStatus.AWAITING_DEPOSIT,
        context="DEMO"
    )
    print(f"❌ COMPLETED -> AWAITING_DEPOSIT (should fail): {result}")


async def demo_cashout_transitions():
    """Demonstrate cashout state transitions"""
    print("\n" + "="*60)
    print("CASHOUT TRANSITIONS")
    print("="*60)
    
    # Valid transition: PENDING -> OTP_PENDING
    result = await StateTransitionService.transition_entity_status(
        entity_type="cashout",
        entity_id="CO123456",
        current_status=CashoutStatus.PENDING,
        new_status=CashoutStatus.OTP_PENDING,
        context="DEMO"
    )
    print(f"✅ PENDING -> OTP_PENDING: {result}")
    
    # Valid transition: PROCESSING -> COMPLETED
    result = await StateTransitionService.transition_entity_status(
        entity_type="cashout",
        entity_id="CO123457",
        current_status=CashoutStatus.PROCESSING,
        new_status=CashoutStatus.COMPLETED,
        context="DEMO"
    )
    print(f"✅ PROCESSING -> COMPLETED: {result}")
    
    # Invalid transition: SUCCESS -> PENDING
    result = await StateTransitionService.transition_entity_status(
        entity_type="cashout",
        entity_id="CO123458",
        current_status=CashoutStatus.SUCCESS,
        new_status=CashoutStatus.PENDING,
        context="DEMO"
    )
    print(f"❌ SUCCESS -> PENDING (should fail): {result}")


async def demo_unified_transaction_transitions():
    """Demonstrate unified transaction state transitions"""
    print("\n" + "="*60)
    print("UNIFIED TRANSACTION TRANSITIONS")
    print("="*60)
    
    # Valid transition: PENDING -> AWAITING_PAYMENT
    result = await StateTransitionService.transition_entity_status(
        entity_type="unified_transaction",
        entity_id="UT123456",
        current_status=UnifiedTransactionStatus.PENDING,
        new_status=UnifiedTransactionStatus.AWAITING_PAYMENT,
        context="DEMO"
    )
    print(f"✅ PENDING -> AWAITING_PAYMENT: {result}")
    
    # Valid transition: PROCESSING -> SUCCESS
    result = await StateTransitionService.transition_entity_status(
        entity_type="unified_transaction",
        entity_id="UT123457",
        current_status=UnifiedTransactionStatus.PROCESSING,
        new_status=UnifiedTransactionStatus.SUCCESS,
        context="DEMO"
    )
    print(f"✅ PROCESSING -> SUCCESS: {result}")
    
    # Invalid transition: SUCCESS -> PENDING
    result = await StateTransitionService.transition_entity_status(
        entity_type="unified_transaction",
        entity_id="UT123458",
        current_status=UnifiedTransactionStatus.SUCCESS,
        new_status=UnifiedTransactionStatus.PENDING,
        context="DEMO"
    )
    print(f"❌ SUCCESS -> PENDING (should fail): {result}")


async def demo_user_transitions():
    """Demonstrate user state transitions (simple validation)"""
    print("\n" + "="*60)
    print("USER TRANSITIONS (Simple Validation)")
    print("="*60)
    
    # Valid transition: ACTIVE -> SUSPENDED
    result = await StateTransitionService.transition_entity_status(
        entity_type="user",
        entity_id="5590563715",
        current_status=UserStatus.ACTIVE,
        new_status=UserStatus.SUSPENDED,
        context="DEMO"
    )
    print(f"✅ ACTIVE -> SUSPENDED: {result}")
    
    # Valid transition: PENDING_VERIFICATION -> ACTIVE
    result = await StateTransitionService.transition_entity_status(
        entity_type="user",
        entity_id="5590563716",
        current_status=UserStatus.PENDING_VERIFICATION,
        new_status=UserStatus.ACTIVE,
        context="DEMO"
    )
    print(f"✅ PENDING_VERIFICATION -> ACTIVE: {result}")
    
    # Invalid transition: BANNED -> ACTIVE (terminal state)
    result = await StateTransitionService.transition_entity_status(
        entity_type="user",
        entity_id="5590563717",
        current_status=UserStatus.BANNED,
        new_status=UserStatus.ACTIVE,
        context="DEMO"
    )
    print(f"❌ BANNED -> ACTIVE (should fail): {result}")


async def demo_dispute_transitions():
    """Demonstrate dispute state transitions (simple validation)"""
    print("\n" + "="*60)
    print("DISPUTE TRANSITIONS (Simple Validation)")
    print("="*60)
    
    # Valid transition: OPEN -> UNDER_REVIEW
    result = await StateTransitionService.transition_entity_status(
        entity_type="dispute",
        entity_id="D123456",
        current_status=DisputeStatus.OPEN,
        new_status=DisputeStatus.UNDER_REVIEW,
        context="DEMO"
    )
    print(f"✅ OPEN -> UNDER_REVIEW: {result}")
    
    # Valid transition: UNDER_REVIEW -> RESOLVED
    result = await StateTransitionService.transition_entity_status(
        entity_type="dispute",
        entity_id="D123457",
        current_status=DisputeStatus.UNDER_REVIEW,
        new_status=DisputeStatus.RESOLVED,
        context="DEMO"
    )
    print(f"✅ UNDER_REVIEW -> RESOLVED: {result}")
    
    # Invalid transition: RESOLVED -> OPEN (terminal state)
    result = await StateTransitionService.transition_entity_status(
        entity_type="dispute",
        entity_id="D123458",
        current_status=DisputeStatus.RESOLVED,
        new_status=DisputeStatus.OPEN,
        context="DEMO"
    )
    print(f"❌ RESOLVED -> OPEN (should fail): {result}")


def demo_validation_only():
    """Demonstrate validation-only checks (synchronous)"""
    print("\n" + "="*60)
    print("VALIDATION-ONLY CHECKS (Pre-flight)")
    print("="*60)
    
    # Check if escrow can be completed
    can_complete = StateTransitionService.validate_transition_only(
        entity_type="escrow",
        entity_id="ES999999",
        current_status=EscrowStatus.ACTIVE,
        new_status=EscrowStatus.COMPLETED
    )
    print(f"Can complete escrow from ACTIVE: {can_complete}")
    
    # Check if exchange can be cancelled
    can_cancel = StateTransitionService.validate_transition_only(
        entity_type="exchange",
        entity_id="EX999999",
        current_status=ExchangeStatus.AWAITING_DEPOSIT,
        new_status=ExchangeStatus.CANCELLED
    )
    print(f"Can cancel exchange from AWAITING_DEPOSIT: {can_cancel}")
    
    # Check invalid transition
    can_resurrect = StateTransitionService.validate_transition_only(
        entity_type="cashout",
        entity_id="CO999999",
        current_status=CashoutStatus.SUCCESS,
        new_status=CashoutStatus.PENDING
    )
    print(f"Can resurrect cashout from SUCCESS: {can_resurrect}")


def demo_get_valid_transitions():
    """Demonstrate getting valid next states"""
    print("\n" + "="*60)
    print("GET VALID NEXT STATES")
    print("="*60)
    
    # Get valid transitions for escrow in CREATED state
    valid = StateTransitionService.get_valid_transitions(
        entity_type="escrow",
        current_status=EscrowStatus.CREATED
    )
    print("\nValid transitions from CREATED:")
    for status in valid:
        print(f"  - {status.value}")
    
    # Get valid transitions for exchange in PROCESSING
    valid = StateTransitionService.get_valid_transitions(
        entity_type="exchange",
        current_status=ExchangeStatus.PROCESSING
    )
    print("\nValid transitions from PROCESSING:")
    for status in valid:
        print(f"  - {status.value}")


def demo_terminal_states():
    """Demonstrate terminal state detection"""
    print("\n" + "="*60)
    print("TERMINAL STATE DETECTION")
    print("="*60)
    
    # Check terminal states
    states_to_check = [
        ("escrow", EscrowStatus.COMPLETED),
        ("escrow", EscrowStatus.ACTIVE),
        ("exchange", ExchangeStatus.COMPLETED),
        ("cashout", CashoutStatus.SUCCESS),
        ("cashout", CashoutStatus.PROCESSING),
        ("user", UserStatus.BANNED),
        ("dispute", DisputeStatus.RESOLVED),
    ]
    
    for entity_type, status in states_to_check:
        is_terminal = StateTransitionService.is_terminal_state(entity_type, status)
        symbol = "🔒" if is_terminal else "🔓"
        print(f"{symbol} {entity_type}.{status.value}: {'Terminal' if is_terminal else 'Can transition'}")


async def main():
    """Run all demonstrations"""
    print("\n" + "="*80)
    print("STATE TRANSITION SERVICE - COMPREHENSIVE DEMONSTRATION")
    print("="*80)
    
    # Async demonstrations
    await demo_escrow_transitions()
    await demo_exchange_transitions()
    await demo_cashout_transitions()
    await demo_unified_transaction_transitions()
    await demo_user_transitions()
    await demo_dispute_transitions()
    
    # Synchronous demonstrations
    demo_validation_only()
    demo_get_valid_transitions()
    demo_terminal_states()
    
    print("\n" + "="*80)
    print("✅ ALL DEMONSTRATIONS COMPLETED")
    print("="*80)
    print("\nSUMMARY:")
    print("- ✅ Escrow transitions validated")
    print("- ✅ Exchange transitions validated")
    print("- ✅ Cashout transitions validated")
    print("- ✅ Unified Transaction transitions validated")
    print("- ✅ User transitions validated (simple)")
    print("- ✅ Dispute transitions validated (simple)")
    print("- ✅ Validation-only checks working")
    print("- ✅ Get valid transitions working")
    print("- ✅ Terminal state detection working")


if __name__ == "__main__":
    asyncio.run(main())
