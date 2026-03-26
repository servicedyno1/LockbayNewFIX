"""
UnifiedTransactionService Usage Examples

This file demonstrates how to use the UnifiedTransactionService for all transaction types
according to the document specification.
"""

import asyncio
from decimal import Decimal

from services.unified_transaction_service import (
    TransactionRequest, 
    create_unified_transaction_service
)
from services.dual_write_adapter import DualWriteMode
from models import UnifiedTransactionType, UnifiedTransactionStatus, UnifiedTransactionPriority


async def example_wallet_cashout_flow():
    """
    Example: Wallet Balance Cashout Flow
    pending → [OTP] → processing → awaiting_response → success/failed
    
    This demonstrates external API processing with OTP verification
    """
    print("=== WALLET CASHOUT EXAMPLE ===")
    
    # Create service instance
    service = create_unified_transaction_service(DualWriteMode.DUAL_WRITE_LEGACY_PRIMARY)
    
    # Step 1: Create wallet cashout transaction
    request = TransactionRequest(
        transaction_type=UnifiedTransactionType.WALLET_CASHOUT,
        user_id=12345,
        amount=Decimal("100.00"),
        currency="USD",
        priority=UnifiedTransactionPriority.NORMAL,
        destination_address="1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa",  # Bitcoin address
        metadata={"payout_method": "crypto", "requested_by": "user"}
    )
    
    # Create transaction
    result = await service.create_transaction(request)
    print(f"1. Transaction Creation: {result.success}")
    print(f"   Transaction ID: {result.transaction_id}")
    print(f"   Status: {result.status}")
    print(f"   Requires OTP: {result.requires_otp}")
    print(f"   Next Action: {result.next_action}")
    
    if not result.success:
        return
    
    transaction_id = result.transaction_id
    
    # Step 2: Handle OTP flow (if required)
    if result.requires_otp:
        print("\n2. OTP Verification Required")
        
        # Simulate OTP verification success
        otp_result = await service.transition_status(
            transaction_id=transaction_id,
            new_status=UnifiedTransactionStatus.PROCESSING,
            reason="OTP verified successfully",
            user_id=12345
        )
        print(f"   OTP Transition: {otp_result.success}")
        print(f"   Status: {otp_result.old_status} → {otp_result.new_status}")
    
    # Step 3: Process external payout
    print("\n3. Processing External Payout")
    payout_result = await service.process_external_payout(transaction_id)
    print(f"   Payout Result: {payout_result.get('success')}")
    
    if payout_result.get('success'):
        print(f"   Provider: {payout_result.get('provider')}")
        print(f"   External Reference: {payout_result.get('external_reference')}")
    else:
        print(f"   Error: {payout_result.get('error')}")
        print(f"   Will Retry: {payout_result.get('will_retry', False)}")
    
    # Step 4: Check final status
    final_tx = await service.get_transaction(transaction_id)
    if final_tx:
        print(f"\n4. Final Status: {final_tx.status}")
        print(f"   Amount: {final_tx.amount} {final_tx.currency}")
        print(f"   Created: {final_tx.created_at}")
    
    return transaction_id


async def example_exchange_sell_crypto_flow():
    """
    Example: Exchange Sell Crypto Flow  
    pending → awaiting_payment → payment_confirmed → processing → success
    
    This demonstrates internal transfer processing (no external API)
    """
    print("\n\n=== EXCHANGE SELL CRYPTO EXAMPLE ===")
    
    service = create_unified_transaction_service(DualWriteMode.DUAL_WRITE_LEGACY_PRIMARY)
    
    # Step 1: Create exchange transaction
    request = TransactionRequest(
        transaction_type=UnifiedTransactionType.EXCHANGE_SELL_CRYPTO,
        user_id=12345,
        amount=Decimal("0.001"),  # 0.001 BTC
        currency="BTC",
        exchange_rate=Decimal("50000.00"),  # $50,000 per BTC
        metadata={
            "target_currency": "USD",
            "exchange_type": "sell",
            "rate_locked": True
        }
    )
    
    result = await service.create_transaction(request)
    print(f"1. Exchange Creation: {result.success}")
    print(f"   Transaction ID: {result.transaction_id}")
    print(f"   Status: {result.status}")
    print(f"   Next Action: {result.next_action}")
    
    if not result.success:
        return
    
    transaction_id = result.transaction_id
    
    # Step 2: Simulate payment confirmation
    print("\n2. Payment Confirmation")
    payment_result = await service.transition_status(
        transaction_id=transaction_id,
        new_status=UnifiedTransactionStatus.PAYMENT_CONFIRMED,
        reason="Crypto payment confirmed on blockchain",
        metadata={
            "tx_hash": "0x1234567890abcdef",
            "confirmations": 6,
            "received_amount": "0.001"
        }
    )
    print(f"   Payment Transition: {payment_result.success}")
    print(f"   Status: {payment_result.old_status} → {payment_result.new_status}")
    
    # Step 3: Process internal transfer (exchange completion)
    print("\n3. Processing Exchange Completion")
    
    # First transition to processing
    await service.transition_status(
        transaction_id=transaction_id,
        new_status=UnifiedTransactionStatus.PROCESSING,
        reason="Exchange processing started"
    )
    
    # Then execute internal transfer
    transfer_result = await service.process_internal_transfer(transaction_id)
    print(f"   Transfer Result: {transfer_result.get('success')}")
    
    if transfer_result.get('success'):
        print(f"   Transfer Type: {transfer_result.get('transfer_type')}")
        print(f"   Amount: {transfer_result.get('amount')} {transfer_result.get('currency')}")
        print(f"   To User: {transfer_result.get('to_user_id')}")
    else:
        print(f"   Error: {transfer_result.get('error')}")
    
    # Step 4: Check final status
    final_tx = await service.get_transaction(transaction_id)
    if final_tx:
        print(f"\n4. Final Status: {final_tx.status}")
        print(f"   Exchange Rate: {final_tx.exchange_rate}")
    
    return transaction_id


async def example_escrow_flow():
    """
    Example: Full Escrow Flow
    pending → payment_confirmed → awaiting_approval → funds_held → release_pending → success
    
    This demonstrates the complete escrow lifecycle with internal transfers
    """
    print("\n\n=== ESCROW FLOW EXAMPLE ===")
    
    service = create_unified_transaction_service(DualWriteMode.DUAL_WRITE_LEGACY_PRIMARY)
    
    # Step 1: Create escrow transaction
    request = TransactionRequest(
        transaction_type=UnifiedTransactionType.ESCROW,
        user_id=12345,  # Buyer
        amount=Decimal("500.00"),
        currency="USD",
        escrow_details={
            "seller_id": 67890,
            "description": "Web development services",
            "deadline": "2025-10-01"
        },
        metadata={
            "service_type": "development",
            "milestone": "final_delivery"
        }
    )
    
    result = await service.create_transaction(request)
    print(f"1. Escrow Creation: {result.success}")
    print(f"   Transaction ID: {result.transaction_id}")
    print(f"   Status: {result.status}")
    print(f"   Next Action: {result.next_action}")
    
    if not result.success:
        return
    
    transaction_id = result.transaction_id
    
    # Step 2: Payment confirmation
    print("\n2. Payment Confirmation")
    payment_result = await service.transition_status(
        transaction_id=transaction_id,
        new_status=UnifiedTransactionStatus.PAYMENT_CONFIRMED,
        reason="Buyer payment confirmed",
        user_id=12345
    )
    print(f"   Payment Transition: {payment_result.success}")
    
    # Step 3: Seller acceptance
    print("\n3. Seller Acceptance")
    acceptance_result = await service.transition_status(
        transaction_id=transaction_id,
        new_status=UnifiedTransactionStatus.AWAITING_APPROVAL,
        reason="Awaiting seller acceptance"
    )
    print(f"   Awaiting Approval: {acceptance_result.success}")
    
    # Simulate seller accepts
    funds_held_result = await service.transition_status(
        transaction_id=transaction_id,
        new_status=UnifiedTransactionStatus.FUNDS_HELD,
        reason="Seller accepted escrow terms",
        user_id=67890  # Seller ID
    )
    print(f"   Funds Held: {funds_held_result.success}")
    
    # Step 4: Work completion and release
    print("\n4. Escrow Release Process")
    release_pending_result = await service.transition_status(
        transaction_id=transaction_id,
        new_status=UnifiedTransactionStatus.RELEASE_PENDING,
        reason="Buyer approved work completion",
        user_id=12345  # Buyer ID
    )
    print(f"   Release Pending: {release_pending_result.success}")
    
    # Step 5: Execute release (internal transfer to seller)
    print("\n5. Executing Release Transfer")
    release_result = await service.process_internal_transfer(transaction_id)
    print(f"   Release Result: {release_result.get('success')}")
    
    if release_result.get('success'):
        print(f"   Transferred to seller: {release_result.get('amount')} {release_result.get('currency')}")
        print(f"   Seller ID: {release_result.get('to_user_id')}")
    else:
        print(f"   Error: {release_result.get('error')}")
    
    # Step 6: Check final status
    final_tx = await service.get_transaction(transaction_id)
    if final_tx:
        print(f"\n6. Final Status: {final_tx.status}")
    
    return transaction_id


async def example_transaction_queries():
    """
    Example: Query transactions and history
    """
    print("\n\n=== TRANSACTION QUERIES EXAMPLE ===")
    
    service = create_unified_transaction_service()
    
    # Get user transactions
    user_txs = await service.get_user_transactions(
        user_id=12345,
        limit=10
    )
    print(f"1. User Transactions: {len(user_txs)} found")
    
    for tx in user_txs:
        print(f"   {tx.transaction_id}: {tx.transaction_type} - {tx.status}")
    
    # Get specific transaction
    if user_txs:
        tx = user_txs[0]
        print(f"\n2. Transaction Details: {tx.transaction_id}")
        print(f"   Type: {tx.transaction_type}")
        print(f"   Amount: {tx.amount} {tx.currency}")
        print(f"   Status: {tx.status}")
        print(f"   Created: {tx.created_at}")
        
        # Get status history
        history = await service.get_transaction_history(tx.transaction_id)
        print(f"\n3. Status History: {len(history)} entries")
        
        for entry in history:
            print(f"   {entry.timestamp}: {entry.old_status} → {entry.new_status}")
            if entry.reason:
                print(f"      Reason: {entry.reason}")


async def example_retry_handling():
    """
    Example: Retry handling for failed external API calls
    """
    print("\n\n=== RETRY HANDLING EXAMPLE ===")
    
    service = create_unified_transaction_service()
    
    # Create a wallet cashout that will fail
    request = TransactionRequest(
        transaction_type=UnifiedTransactionType.WALLET_CASHOUT,
        user_id=12345,
        amount=Decimal("50.00"),
        currency="USD",
        destination_bank_account="invalid_account"  # Will cause failure
    )
    
    result = await service.create_transaction(request)
    if not result.success:
        return
    
    transaction_id = result.transaction_id
    
    # Simulate failure and retry
    print("1. Simulating External API Failure")
    
    # Transition to failed status
    await service.transition_status(
        transaction_id=transaction_id,
        new_status=UnifiedTransactionStatus.FAILED,
        reason="External API call failed - connection timeout"
    )
    
    # Attempt retry
    print("\n2. Attempting Retry")
    retry_result = await service.handle_failure_retry(transaction_id)
    print(f"   Retry Executed: {retry_result.get('success')}")
    
    if retry_result.get('success'):
        print(f"   Retry Attempt: {retry_result.get('retry_attempt')}")
        print(f"   Retry Result: {retry_result.get('retry_result', {}).get('success')}")
    
    return transaction_id


async def example_validation_and_errors():
    """
    Example: Validation and error handling scenarios
    """
    print("\n\n=== VALIDATION AND ERROR EXAMPLES ===")
    
    service = create_unified_transaction_service()
    
    # 1. Invalid user
    print("1. Invalid User Test")
    invalid_user_request = TransactionRequest(
        transaction_type=UnifiedTransactionType.WALLET_CASHOUT,
        user_id=999999,  # Non-existent user
        amount=Decimal("100.00")
    )
    
    result = await service.create_transaction(invalid_user_request)
    print(f"   Success: {result.success}")
    print(f"   Error: {result.error}")
    
    # 2. Insufficient balance
    print("\n2. Insufficient Balance Test")
    insufficient_request = TransactionRequest(
        transaction_type=UnifiedTransactionType.WALLET_CASHOUT,
        user_id=12345,
        amount=Decimal("999999.00"),  # More than user has
        currency="USD"
    )
    
    result = await service.create_transaction(insufficient_request)
    print(f"   Success: {result.success}")
    print(f"   Error: {result.error}")
    
    # 3. Invalid status transition
    print("\n3. Invalid Status Transition Test")
    valid_request = TransactionRequest(
        transaction_type=UnifiedTransactionType.EXCHANGE_SELL_CRYPTO,
        user_id=12345,
        amount=Decimal("0.001")
    )
    
    result = await service.create_transaction(valid_request)
    if result.success:
        # Try invalid transition
        invalid_transition = await service.transition_status(
            transaction_id=result.transaction_id,
            new_status=UnifiedTransactionStatus.SUCCESS,  # Can't go directly from pending to success
            reason="Invalid direct transition test"
        )
        print(f"   Transition Success: {invalid_transition.success}")
        print(f"   Error: {invalid_transition.error}")


async def main():
    """Run all examples"""
    print("🚀 UnifiedTransactionService Examples")
    print("=====================================")
    
    try:
        # Run all examples
        await example_wallet_cashout_flow()
        await example_exchange_sell_crypto_flow()
        await example_escrow_flow()
        await example_transaction_queries()
        await example_retry_handling()
        await example_validation_and_errors()
        
        print("\n\n✅ All examples completed successfully!")
        
    except Exception as e:
        print(f"\n❌ Example execution failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    # Run examples
    asyncio.run(main())