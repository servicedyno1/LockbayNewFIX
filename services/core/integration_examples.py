"""
Integration Examples for PaymentProcessor

Examples showing how to migrate from the old complex architecture 
to the new unified PaymentProcessor system.
"""

import asyncio
from decimal import Decimal

from .payment_processor import payment_processor
from .payment_data_structures import (
    PayinRequest, PayoutRequest, PaymentDestination,
    PaymentProvider
)


# Example 1: Escrow Payment (replaces complex escrow payment flow)
async def example_escrow_payment(user_id: int, amount: Decimal, currency: str, escrow_id: str):
    """Example: Process escrow payment using unified architecture"""
    
    # Old way: Multiple services, complex flows
    # Now: Single clean request
    request = PayinRequest(
        user_id=user_id,
        amount=amount,
        currency=currency,
        payment_type="escrow",
        reference_id=escrow_id,
        metadata={"escrow_id": escrow_id}
    )
    
    result = await payment_processor.process_payin(request)
    
    if result.success:
        print(f"✅ Escrow payment successful: {result.transaction_id}")
        print(f"   Status: {result.status.value}")
        print(f"   Payment details: {result.payment_details}")
        
        if result.requires_user_action:
            print(f"   Next action: {result.next_action}")
            
    else:
        print(f"❌ Escrow payment failed: {result.error_message}")
        
    return result


# Example 2: Crypto Withdrawal (replaces complex Kraken integration)
async def example_crypto_withdrawal(
    user_id: int, 
    amount: Decimal, 
    currency: str, 
    crypto_address: str
):
    """Example: Process crypto withdrawal using unified architecture"""
    
    # Create destination
    destination = PaymentDestination(
        type="crypto_address",
        address=crypto_address,
        currency=currency
    )
    
    # Old way: Multiple validation steps, complex Kraken integration
    # Now: Single clean request
    request = PayoutRequest(
        user_id=user_id,
        amount=amount,
        currency=currency,
        destination=destination,
        payment_type="cashout",
        requires_otp=True,
        metadata={"withdrawal_type": "crypto"}
    )
    
    result = await payment_processor.process_payout(request)
    
    if result.success:
        print(f"✅ Crypto withdrawal successful: {result.transaction_id}")
        print(f"   Provider: {result.provider.value}")
        print(f"   Status: {result.status.value}")
        
    elif result.requires_otp:
        print("🔐 OTP verification required")
        print(f"   Next action: {result.next_action}")
        
    else:
        print(f"❌ Crypto withdrawal failed: {result.error_message}")
        
    return result


# Example 3: NGN Bank Transfer (replaces complex Fincra integration)
async def example_ngn_withdrawal(
    user_id: int,
    amount: Decimal,
    bank_code: str,
    account_number: str,
    account_name: str
):
    """Example: Process NGN bank transfer using unified architecture"""
    
    # Create bank destination
    destination = PaymentDestination(
        type="bank_account",
        bank_code=bank_code,
        account_number=account_number,
        account_name=account_name,
        currency="NGN"
    )
    
    # Old way: Complex Fincra service calls, manual validation
    # Now: Single clean request
    request = PayoutRequest(
        user_id=user_id,
        amount=amount,
        currency="NGN",
        destination=destination,
        payment_type="cashout",
        priority="normal",
        metadata={"transfer_type": "bank"}
    )
    
    result = await payment_processor.process_payout(request)
    
    if result.success:
        print(f"✅ NGN transfer successful: {result.transaction_id}")
        print(f"   Amount: ₦{result.actual_amount}")
        print(f"   Fees: ₦{result.fees_charged}")
        
    else:
        print(f"❌ NGN transfer failed: {result.error_message}")
        
    return result


# Example 4: Multi-Currency Balance Check (replaces multiple balance services)
async def example_balance_check():
    """Example: Check balances across all providers"""
    
    # Old way: Multiple service calls, complex aggregation
    # Now: Single unified call
    balance_result = await payment_processor.check_balance()
    
    if balance_result.success:
        print("💰 Current Balances:")
        print(f"   Total USD Value: ${balance_result.total_usd_value:.2f}")
        print()
        
        for balance in balance_result.balances:
            print(f"   {balance.provider.value} - {balance.currency}:")
            print(f"     Available: {balance.available_balance}")
            print(f"     Total: {balance.total_balance}")
            print(f"     Locked: {balance.locked_balance}")
            print()
    else:
        print(f"❌ Balance check failed: {balance_result.error_message}")
        
    return balance_result


# Example 5: Migration Helper - Convert old service calls to new architecture
class LegacyMigrationHelper:
    """Helper class to migrate from old complex services to new unified architecture"""
    
    @staticmethod
    async def migrate_auto_cashout_call(old_params: dict):
        """Convert old auto_cashout service call to new PaymentProcessor"""
        
        # Extract parameters from old format
        user_id = old_params.get("user_id")
        amount = Decimal(str(old_params.get("amount", 0)))
        currency = old_params.get("currency", "USD")
        destination_data = old_params.get("destination", {})
        
        # Create new format destination
        destination = PaymentDestination(
            type=destination_data.get("type", "crypto_address"),
            address=destination_data.get("address"),
            bank_code=destination_data.get("bank_code"),
            account_number=destination_data.get("account_number"),
            account_name=destination_data.get("account_name"),
            currency=currency
        )
        
        # Create new format request
        request = PayoutRequest(
            user_id=user_id,
            amount=amount,
            currency=currency,
            destination=destination,
            payment_type="cashout",
            metadata=old_params.get("metadata", {})
        )
        
        # Process with new unified system
        result = await payment_processor.process_payout(request)
        
        # Convert result to old format for backward compatibility
        return {
            "success": result.success,
            "transaction_id": result.transaction_id,
            "status": result.status.value,
            "error": result.error_message,
            "provider": result.provider.value if result.provider else None,
            "requires_otp": result.requires_otp
        }
    
    @staticmethod
    async def migrate_blockbee_call(old_params: dict):
        """Convert old BlockBee service call to new PaymentProcessor"""
        
        request = PayinRequest(
            user_id=old_params.get("user_id"),
            amount=Decimal(str(old_params.get("amount", 0))),
            currency=old_params.get("currency"),
            payment_type=old_params.get("payment_type", "escrow"),
            reference_id=old_params.get("escrow_id"),
            preferred_provider=PaymentProvider.BLOCKBEE,
            metadata=old_params.get("callback_data", {})
        )
        
        result = await payment_processor.process_payin(request)
        
        # Convert to old BlockBee format
        return {
            "success": result.success,
            "address": result.payment_details.get("address") if result.payment_details else None,
            "qr_code_url": result.payment_details.get("qr_code_url") if result.payment_details else None,
            "minimum_amount": result.payment_details.get("minimum_amount") if result.payment_details else None,
            "error": result.error_message
        }


# Example usage of the new unified architecture
async def example_full_workflow():
    """Complete example showing the power of the unified architecture"""
    
    print("🚀 PaymentProcessor Unified Architecture Demo")
    print("=" * 50)
    
    # 1. Check current balances
    print("\n1. Checking balances...")
    await example_balance_check()
    
    # 2. Process escrow payment
    print("\n2. Processing escrow payment...")
    escrow_result = await example_escrow_payment(
        user_id=123,
        amount=Decimal("100.00"),
        currency="BTC",
        escrow_id="ESC123"
    )
    
    # 3. Process crypto withdrawal
    print("\n3. Processing crypto withdrawal...")
    crypto_result = await example_crypto_withdrawal(
        user_id=123,
        amount=Decimal("0.001"),
        currency="BTC",
        crypto_address="1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"
    )
    
    # 4. Process NGN withdrawal
    print("\n4. Processing NGN withdrawal...")
    ngn_result = await example_ngn_withdrawal(
        user_id=123,
        amount=Decimal("50000"),
        bank_code="044",
        account_number="1234567890",
        account_name="Test User"
    )
    
    print("\n✅ Demo completed!")
    print(f"Escrow: {escrow_result.status.value}")
    print(f"Crypto: {crypto_result.status.value}")
    print(f"NGN: {ngn_result.status.value}")


if __name__ == "__main__":
    # Run the demo
    asyncio.run(example_full_workflow())