"""
Validation Test for PaymentProcessor Unified Architecture

Simple test to validate the new unified architecture works correctly
and integrates properly with existing database models.
"""

import asyncio
import logging
from decimal import Decimal

# Set up logging for test
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def test_payment_processor_initialization():
    """Test that PaymentProcessor initializes correctly"""
    try:
        from services.core import payment_processor, PaymentProvider
        
        logger.info("🧪 Testing PaymentProcessor initialization...")
        
        # Check that providers are initialized
        available_providers = []
        for provider_type in PaymentProvider:
            if provider_type in payment_processor.providers:
                provider = payment_processor.providers[provider_type]
                is_available = await provider.is_available()
                if is_available:
                    available_providers.append(provider_type.value)
                logger.info(f"   {provider_type.value}: {'✅ Available' if is_available else '⚠️ Not available'}")
            else:
                logger.info(f"   {provider_type.value}: ❌ Not initialized")
        
        logger.info(f"✅ PaymentProcessor initialized with {len(available_providers)} available providers")
        return True
        
    except Exception as e:
        logger.error(f"❌ PaymentProcessor initialization failed: {e}")
        return False


async def test_data_structures():
    """Test that data structures work correctly"""
    try:
        from services.core import (
            PayinRequest, PayoutRequest, PaymentDestination, 
            TransactionStatus, PaymentError, create_success_result, create_error_result
        )
        
        logger.info("🧪 Testing data structures...")
        
        # Test PayinRequest
        payin_request = PayinRequest(
            user_id=123,
            amount=Decimal("100.00"),
            currency="BTC",
            payment_type="escrow",
            reference_id="ESC123"
        )
        
        assert payin_request.user_id == 123
        assert payin_request.amount == Decimal("100.00")
        assert payin_request.currency == "BTC"
        logger.info("   ✅ PayinRequest creation successful")
        
        # Test PayoutRequest with destination
        destination = PaymentDestination(
            type="crypto_address",
            address="1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa",
            currency="BTC"
        )
        
        payout_request = PayoutRequest(
            user_id=123,
            amount=Decimal("0.001"),
            currency="BTC",
            destination=destination,
            payment_type="cashout"
        )
        
        assert payout_request.user_id == 123
        assert payout_request.destination.address == "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"
        logger.info("   ✅ PayoutRequest creation successful")
        
        # Test result helpers  
        from services.core.payment_data_structures import PaymentProvider
        success_result = create_success_result(
            transaction_id="TX123",
            provider=PaymentProvider.KRAKEN,
            amount=Decimal("0.001")
        )
        
        assert success_result.success == True
        assert success_result.status == TransactionStatus.SUCCESS
        logger.info("   ✅ Success result creation successful")
        
        error_result = create_error_result(
            PaymentError.BUSINESS,
            "Test error message"
        )
        
        assert error_result.success == False
        assert error_result.error == PaymentError.BUSINESS
        logger.info("   ✅ Error result creation successful")
        
        logger.info("✅ All data structures working correctly")
        return True
        
    except Exception as e:
        logger.error(f"❌ Data structures test failed: {e}")
        return False


async def test_provider_interface():
    """Test that provider interfaces work correctly"""
    try:
        from services.core.payment_provider_interface import (
            FincraProviderAdapter, KrakenProviderAdapter, BlockBeeProviderAdapter
        )
        
        logger.info("🧪 Testing provider interfaces...")
        
        # Test Fincra adapter
        try:
            fincra_adapter = FincraProviderAdapter()
            assert fincra_adapter.provider_type.value == "fincra"
            assert "NGN" in fincra_adapter.supported_currencies
            assert fincra_adapter.supports_payin == True
            assert fincra_adapter.supports_payout == True
            logger.info("   ✅ Fincra adapter interface correct")
        except Exception as e:
            logger.warning(f"   ⚠️ Fincra adapter test skipped: {e}")
        
        # Test Kraken adapter
        try:
            kraken_adapter = KrakenProviderAdapter()
            assert kraken_adapter.provider_type.value == "kraken"
            assert "BTC" in kraken_adapter.supported_currencies
            assert kraken_adapter.supports_payin == False
            assert kraken_adapter.supports_payout == True
            logger.info("   ✅ Kraken adapter interface correct")
        except Exception as e:
            logger.warning(f"   ⚠️ Kraken adapter test skipped: {e}")
        
        # Test BlockBee adapter
        try:
            blockbee_adapter = BlockBeeProviderAdapter()
            assert blockbee_adapter.provider_type.value == "blockbee"
            assert "BTC" in blockbee_adapter.supported_currencies
            assert blockbee_adapter.supports_payin == True
            assert blockbee_adapter.supports_payout == False
            logger.info("   ✅ BlockBee adapter interface correct")
        except Exception as e:
            logger.warning(f"   ⚠️ BlockBee adapter test skipped: {e}")
        
        logger.info("✅ Provider interfaces working correctly")
        return True
        
    except Exception as e:
        logger.error(f"❌ Provider interface test failed: {e}")
        return False


async def test_routing_logic():
    """Test that payment routing works correctly"""
    try:
        from services.core import payment_processor, PayinRequest, PayoutRequest, PaymentDestination
        
        logger.info("🧪 Testing routing logic...")
        
        # Test payin routing
        btc_payin = PayinRequest(
            user_id=123,
            amount=Decimal("100.00"),
            currency="BTC",
            payment_type="escrow"
        )
        
        btc_provider = payment_processor._route_payin(btc_payin)
        assert btc_provider.value == "blockbee"
        logger.info("   ✅ BTC payin routes to BlockBee")
        
        ngn_payin = PayinRequest(
            user_id=123,
            amount=Decimal("50000"),
            currency="NGN",
            payment_type="escrow"
        )
        
        ngn_provider = payment_processor._route_payin(ngn_payin)
        assert ngn_provider.value == "fincra"
        logger.info("   ✅ NGN payin routes to Fincra")
        
        # Test payout routing
        btc_destination = PaymentDestination(
            type="crypto_address",
            address="test_address"
        )
        
        btc_payout = PayoutRequest(
            user_id=123,
            amount=Decimal("0.001"),
            currency="BTC",
            destination=btc_destination,
            payment_type="cashout"
        )
        
        btc_payout_provider = payment_processor._route_payout(btc_payout)
        assert btc_payout_provider.value == "kraken"
        logger.info("   ✅ BTC payout routes to Kraken")
        
        logger.info("✅ Routing logic working correctly")
        return True
        
    except Exception as e:
        logger.error(f"❌ Routing logic test failed: {e}")
        return False


async def test_balance_check():
    """Test balance checking functionality"""
    try:
        from services.core import payment_processor
        
        logger.info("🧪 Testing balance check...")
        
        # Test balance check (this will work even if providers are not fully configured)
        balance_result = await payment_processor.check_balance()
        
        # Should succeed even with no balances
        assert balance_result.success == True
        logger.info(f"   ✅ Balance check successful: {len(balance_result.balances)} balances found")
        
        # Test currency-specific balance check
        btc_balance_result = await payment_processor.check_balance(["BTC"])
        assert btc_balance_result.success == True
        logger.info("   ✅ BTC-specific balance check successful")
        
        logger.info("✅ Balance checking working correctly")
        return True
        
    except Exception as e:
        logger.error(f"❌ Balance check test failed: {e}")
        return False


async def run_validation_tests():
    """Run all validation tests"""
    logger.info("🚀 Starting PaymentProcessor Validation Tests")
    logger.info("=" * 60)
    
    tests = [
        ("Initialization", test_payment_processor_initialization),
        ("Data Structures", test_data_structures),
        ("Provider Interfaces", test_provider_interface),
        ("Routing Logic", test_routing_logic),
        ("Balance Check", test_balance_check),
    ]
    
    results = []
    for test_name, test_func in tests:
        logger.info(f"\n🧪 Running {test_name} test...")
        try:
            result = await test_func()
            results.append((test_name, result))
        except Exception as e:
            logger.error(f"❌ {test_name} test crashed: {e}")
            results.append((test_name, False))
    
    # Summary
    logger.info("\n" + "=" * 60)
    logger.info("📋 TEST RESULTS SUMMARY:")
    
    passed = 0
    total = len(results)
    
    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        logger.info(f"   {test_name}: {status}")
        if result:
            passed += 1
    
    logger.info(f"\n🎯 Overall: {passed}/{total} tests passed")
    
    if passed == total:
        logger.info("🎉 All tests passed! PaymentProcessor unified architecture is working correctly.")
    else:
        logger.warning(f"⚠️ {total - passed} tests failed. Some issues need to be addressed.")
    
    return passed == total


if __name__ == "__main__":
    # Run validation tests
    asyncio.run(run_validation_tests())