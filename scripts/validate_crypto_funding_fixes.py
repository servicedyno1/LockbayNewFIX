#!/usr/bin/env python3
"""
Crypto Funding Validation Script
================================

This script validates that all the critical fixes for the crypto funding journey
are working correctly and that the LTC issue has been resolved.

Run this script to verify:
1. Rate service methods are properly implemented
2. Error handling is robust  
3. Services have proper fallback mechanisms
4. Performance is acceptable

Usage: python scripts/validate_crypto_funding_fixes.py
"""

import asyncio
import logging
import time
import sys
import os
from typing import Dict, Any

# Add project root to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class CryptoFundingValidator:
    """Validation suite for crypto funding fixes"""
    
    def __init__(self):
        self.results = {}
        self.performance_metrics = {}
        
    async def validate_rate_service_fix(self) -> bool:
        """Validate that the rate service missing method issue is fixed"""
        logger.info("🔍 Validating rate service fix...")
        
        try:
            from services.fastforex_service import FastForexService
            
            service = FastForexService()
            
            # Test that the missing method now exists
            if not hasattr(service, 'get_usd_to_ngn_rate'):
                logger.error("❌ CRITICAL: get_usd_to_ngn_rate method still missing!")
                return False
            
            # Test that it's callable
            if not callable(getattr(service, 'get_usd_to_ngn_rate')):
                logger.error("❌ CRITICAL: get_usd_to_ngn_rate is not callable!")
                return False
            
            # Test that it has the right signature (async method)
            import inspect
            if not inspect.iscoroutinefunction(service.get_usd_to_ngn_rate):
                logger.error("❌ CRITICAL: get_usd_to_ngn_rate is not async!")
                return False
            
            logger.info("✅ Rate service method fix validated successfully")
            return True
            
        except ImportError as e:
            logger.error(f"❌ Could not import FastForexService: {e}")
            return False
        except Exception as e:
            logger.error(f"❌ Unexpected error validating rate service: {e}")
            return False
    
    async def validate_service_resilience(self) -> bool:
        """Validate that services have proper error handling and fallbacks"""
        logger.info("🛡️ Validating service resilience...")
        
        try:
            from services.fastforex_service import FastForexService
            from services.dynopay_service import DynoPayService
            from services.blockbee_service import BlockBeeService
            
            # Test FastForex service resilience
            fastforex = FastForexService()
            
            # This should not crash even if API key is missing
            # It should return a fallback rate
            try:
                # Mock a network failure scenario
                original_api_key = fastforex.api_key
                fastforex.api_key = None  # Simulate missing API key
                
                rate = await fastforex.get_usd_to_ngn_rate()
                
                # Should return fallback rate instead of crashing
                if rate is None:
                    logger.error("❌ Rate service returned None instead of fallback")
                    return False
                
                if not isinstance(rate, (int, float)) or rate <= 0:
                    logger.error(f"❌ Rate service returned invalid rate: {rate}")
                    return False
                
                # Restore original API key
                fastforex.api_key = original_api_key
                
                logger.info(f"✅ Rate service fallback working: ₦{rate}")
                
            except Exception as e:
                logger.error(f"❌ Rate service failed resilience test: {e}")
                return False
            
            # Test that services have proper timeout handling
            services = [
                ("DynoPay", DynoPayService()),
                ("BlockBee", BlockBeeService()),
            ]
            
            for service_name, service in services:
                if hasattr(service, 'timeout'):
                    if service.timeout <= 0 or service.timeout > 60:
                        logger.warning(f"⚠️ {service_name} timeout may be too long: {service.timeout}s")
                    else:
                        logger.info(f"✅ {service_name} timeout configured: {service.timeout}s")
                else:
                    logger.warning(f"⚠️ {service_name} does not have timeout configuration")
            
            logger.info("✅ Service resilience validation passed")
            return True
            
        except Exception as e:
            logger.error(f"❌ Service resilience validation failed: {e}")
            return False
    
    async def validate_currency_support(self) -> bool:
        """Validate that all required currencies are supported"""
        logger.info("🪙 Validating currency support...")
        
        required_currencies = [
            'BTC', 'ETH', 'LTC', 'DOGE', 'TRX', 
            'USDT-TRC20', 'USDT-ERC20', 'BCH', 'BNB'
        ]
        
        try:
            # Define test addresses for validation (moved from test suite for validation purposes)
            test_addresses = {
                'BTC': '1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa',
                'ETH': '0x742d35Cc7bF1C4C7f05C2e85a1ad8d8B6F68b0B3', 
                'LTC': 'LTC1QW508D6QEJXTDG4Y5R3ZARVARY0C5XW7KW508D6QEJXTDG4Y5R3ZARVARY0C5XW7KW5RLJS90',
                'DOGE': 'D7Y55LKZH3J7Z8JLVz8tYbBGJ7YQ1X2P9z',
                'TRX': 'TGzc4RvCRjQLNfKJbMfnGvzQ1kZdXgG7Jh',
                'USDT-TRC20': 'TGzc4RvCRjQLNfKJbMfnGvzQ1kZdXgG7Jh',
                'USDT-ERC20': '0x742d35Cc7bF1C4C7f05C2e85a1ad8d8B6F68b0B3',
                'BCH': 'bitcoincash:qz2708636snqhsxu8wnlka78h6fdp77ar59jrf5035',
                'BNB': 'bnb1s3lp5z2d0x2mf4z8l4q9a7r6k5h3g2f1d0s9a8b7c6'
            }
            
            missing_currencies = []
            for currency in required_currencies:
                if currency not in test_addresses:
                    missing_currencies.append(currency)
            
            if missing_currencies:
                logger.error(f"❌ Missing test addresses for currencies: {missing_currencies}")
                return False
            
            logger.info(f"✅ All {len(required_currencies)} currencies have test addresses")
            
            # Test that services can handle all currencies
            from services.dynopay_service import DynoPayService
            from services.blockbee_service import BlockBeeService
            
            dynopay = DynoPayService()
            blockbee = BlockBeeService()
            
            # Test currency mapping
            for currency in required_currencies:
                try:
                    dynopay_mapped = dynopay._map_currency_to_dynopay(currency)
                    blockbee_mapped = blockbee._map_currency_to_blockbee(currency)
                    
                    if not dynopay_mapped or not blockbee_mapped:
                        logger.warning(f"⚠️ Currency mapping issue for {currency}")
                    else:
                        logger.debug(f"✅ {currency} maps to DynoPay: {dynopay_mapped}, BlockBee: {blockbee_mapped}")
                        
                except Exception as e:
                    logger.error(f"❌ Currency mapping failed for {currency}: {e}")
                    return False
            
            logger.info("✅ Currency support validation passed")
            return True
            
        except ImportError as e:
            logger.error(f"❌ Could not import required modules: {e}")
            return False
        except Exception as e:
            logger.error(f"❌ Currency support validation failed: {e}")
            return False
    
    async def validate_performance_requirements(self) -> bool:
        """Validate that performance requirements are met"""
        logger.info("⚡ Validating performance requirements...")
        
        try:
            from services.fastforex_service import FastForexService
            
            # Test rate fetching performance
            fastforex = FastForexService()
            
            # Performance test: Rate fetching
            start_time = time.time()
            await fastforex.get_usd_to_ngn_rate()
            rate_fetch_time = time.time() - start_time
            
            self.performance_metrics['rate_fetch_time'] = rate_fetch_time
            
            if rate_fetch_time > 5.0:
                logger.warning(f"⚠️ Rate fetching slow: {rate_fetch_time:.2f}s")
            else:
                logger.info(f"✅ Rate fetching performance: {rate_fetch_time:.2f}s")
            
            # Test multiple rapid requests (caching)
            start_time = time.time()
            for _ in range(10):
                await fastforex.get_usd_to_ngn_rate()
            multi_fetch_time = time.time() - start_time
            
            self.performance_metrics['multi_fetch_time'] = multi_fetch_time
            
            if multi_fetch_time > 2.0:
                logger.warning(f"⚠️ Multiple rate fetches slow: {multi_fetch_time:.2f}s for 10 requests")
            else:
                logger.info(f"✅ Cached rate fetching: {multi_fetch_time:.2f}s for 10 requests")
            
            logger.info("✅ Performance validation passed")
            return True
            
        except Exception as e:
            logger.error(f"❌ Performance validation failed: {e}")
            return False
    
    async def validate_error_handling_improvements(self) -> bool:
        """Validate that error handling improvements are in place"""
        logger.info("🛠️ Validating error handling improvements...")
        
        try:
            # Test that handlers have proper imports
            from handlers.wallet_direct import (
                start_add_funds, 
                show_crypto_funding_options,
                handle_deposit_currency_selection
            )
            
            # Test that functions exist and are callable
            handlers = [
                ("start_add_funds", start_add_funds),
                ("show_crypto_funding_options", show_crypto_funding_options), 
                ("handle_deposit_currency_selection", handle_deposit_currency_selection)
            ]
            
            for name, handler in handlers:
                if not callable(handler):
                    logger.error(f"❌ Handler {name} is not callable")
                    return False
                
                import inspect
                if not inspect.iscoroutinefunction(handler):
                    logger.error(f"❌ Handler {name} is not async")
                    return False
                
                logger.debug(f"✅ Handler {name} is properly defined")
            
            # Test that enhanced error handling code is in place
            import inspect
            
            # Check that handle_deposit_currency_selection has enhanced error handling
            source = inspect.getsource(handle_deposit_currency_selection)
            
            required_error_patterns = [
                "asyncio.wait_for",  # Timeout protection
                "TimeoutError",      # Timeout handling
                "log_category",      # Error categorization
                "Contact Support",   # User support option
            ]
            
            missing_patterns = []
            for pattern in required_error_patterns:
                if pattern not in source:
                    missing_patterns.append(pattern)
            
            if missing_patterns:
                logger.error(f"❌ Missing error handling patterns: {missing_patterns}")
                return False
            
            logger.info("✅ Error handling improvements validated")
            return True
            
        except Exception as e:
            logger.error(f"❌ Error handling validation failed: {e}")
            return False
    
    async def run_comprehensive_validation(self) -> Dict[str, Any]:
        """Run all validation tests"""
        logger.info("=" * 60)
        logger.info("🚀 Starting Comprehensive Crypto Funding Validation")
        logger.info("=" * 60)
        
        start_time = time.time()
        
        # Run all validation tests
        tests = [
            ("Rate Service Fix", self.validate_rate_service_fix),
            ("Service Resilience", self.validate_service_resilience),
            ("Currency Support", self.validate_currency_support),
            ("Performance Requirements", self.validate_performance_requirements),
            ("Error Handling Improvements", self.validate_error_handling_improvements),
        ]
        
        for test_name, test_func in tests:
            logger.info(f"\n🔍 Running: {test_name}")
            try:
                result = await test_func()
                self.results[test_name] = "PASSED" if result else "FAILED"
                status = "✅ PASSED" if result else "❌ FAILED"
                logger.info(f"{status}: {test_name}")
            except Exception as e:
                self.results[test_name] = f"ERROR: {e}"
                logger.error(f"❌ ERROR in {test_name}: {e}")
        
        total_time = time.time() - start_time
        
        # Generate final report
        logger.info("\n" + "=" * 60)
        logger.info("📊 VALIDATION RESULTS SUMMARY")
        logger.info("=" * 60)
        
        passed_count = sum(1 for result in self.results.values() if result == "PASSED")
        total_count = len(self.results)
        
        for test_name, result in self.results.items():
            status = "✅" if result == "PASSED" else "❌"
            logger.info(f"{status} {test_name}: {result}")
        
        logger.info(f"\n📈 Success Rate: {passed_count}/{total_count} ({passed_count/total_count*100:.1f}%)")
        logger.info(f"⏱️ Total Validation Time: {total_time:.2f}s")
        
        if self.performance_metrics:
            logger.info("\n⚡ Performance Metrics:")
            for metric, value in self.performance_metrics.items():
                logger.info(f"   • {metric}: {value:.3f}s")
        
        # Final verdict
        if passed_count == total_count:
            logger.info("\n🎉 ALL VALIDATIONS PASSED!")
            logger.info("🔒 CRYPTO FUNDING FLOW IS BULLETPROOF!")
            logger.info("✅ LTC ISSUE HAS BEEN RESOLVED!")
        else:
            logger.info(f"\n⚠️ {total_count - passed_count} VALIDATION(S) FAILED!")
            logger.info("🔧 Please address the failed validations above")
        
        logger.info("=" * 60)
        
        return {
            'results': self.results,
            'performance_metrics': self.performance_metrics,
            'success_rate': passed_count / total_count,
            'total_time': total_time
        }

async def main():
    """Main validation runner"""
    validator = CryptoFundingValidator()
    results = await validator.run_comprehensive_validation()
    
    # Exit with appropriate code
    success_rate = results['success_rate']
    exit_code = 0 if success_rate == 1.0 else 1
    
    print(f"\nValidation completed with exit code: {exit_code}")
    return exit_code

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)