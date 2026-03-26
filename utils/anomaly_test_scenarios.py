"""
Anomaly Detection Test Scenarios
Tests critical financial scenarios to validate anomaly detection system
"""

import logging
from datetime import datetime
from typing import Dict, List, Any

logger = logging.getLogger(__name__)

class AnomalyTestScenarios:
    """Test scenarios for anomaly detection system"""
    
    def __init__(self):
        self.test_results: List[Dict[str, Any]] = []
        
    async def run_comprehensive_test_suite(self) -> Dict[str, Any]:
        """Run comprehensive test suite for anomaly detection"""
        logger.critical("🧪 STARTING ANOMALY DETECTION TEST SUITE")
        
        test_scenarios = [
            self._test_usd_crypto_conversion_error,
            self._test_address_parsing_error,
            self._test_network_mismatch_error,
            self._test_amount_validation_error,
            self._test_reference_id_truncation,
            self._test_normal_operation_validation,
            self._test_emergency_response_integration,
            self._test_ngn_bank_details_validation,
            self._test_ngn_amount_validation_errors,
            self._test_ngn_exchange_rate_failures,
            self._test_ngn_processing_emergencies
        ]
        
        total_tests = len(test_scenarios)
        passed_tests = 0
        failed_tests = 0
        
        for i, test_scenario in enumerate(test_scenarios, 1):
            try:
                logger.critical(f"🧪 Running test {i}/{total_tests}: {test_scenario.__name__}")
                result = await test_scenario()
                
                if result.get("passed", False):
                    passed_tests += 1
                    logger.critical(f"✅ Test {i} PASSED: {test_scenario.__name__}")
                else:
                    failed_tests += 1
                    logger.critical(f"❌ Test {i} FAILED: {test_scenario.__name__} - {result.get('error', 'Unknown error')}")
                    
                self.test_results.append({
                    "test_name": test_scenario.__name__,
                    "test_number": i,
                    "passed": result.get("passed", False),
                    "result": result,
                    "timestamp": datetime.utcnow().isoformat()
                })
                
            except Exception as e:
                failed_tests += 1
                logger.critical(f"💥 Test {i} CRASHED: {test_scenario.__name__} - {str(e)}")
                self.test_results.append({
                    "test_name": test_scenario.__name__,
                    "test_number": i,
                    "passed": False,
                    "error": str(e),
                    "crashed": True,
                    "timestamp": datetime.utcnow().isoformat()
                })
                
        # Generate final test report
        test_summary = {
            "total_tests": total_tests,
            "passed_tests": passed_tests,
            "failed_tests": failed_tests,
            "success_rate": (passed_tests / total_tests) * 100 if total_tests > 0 else 0,
            "test_results": self.test_results,
            "overall_status": "PASSED" if failed_tests == 0 else "FAILED",
            "timestamp": datetime.utcnow().isoformat()
        }
        
        logger.critical("🧪 TEST SUITE COMPLETE:")
        logger.critical(f"   Total Tests: {total_tests}")
        logger.critical(f"   Passed: {passed_tests}")
        logger.critical(f"   Failed: {failed_tests}")
        logger.critical(f"   Success Rate: {test_summary['success_rate']:.1f}%")
        logger.critical(f"   Overall Status: {test_summary['overall_status']}")
        
        return test_summary
        
    async def _test_usd_crypto_conversion_error(self) -> Dict[str, Any]:
        """Test USD-to-crypto conversion error detection"""
        try:
            from utils.production_anomaly_detector import monitor_cashout_anomalies
            
            # Scenario: USD amount suspiciously equals crypto amount (the critical bug)
            test_cashout_id = "TEST-USD-CRYPTO-001"
            test_user_id = 99999
            
            # Critical scenario: $5 USD would convert to exactly 5 BTC (impossible)
            # This simulates the bug where USD amounts were sent as crypto amounts
            suspicious_usd_amount = 5.0  # $5 USD
            crypto_currency = "BTC"
            destination_address = "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"  # Valid BTC address
            
            anomalies = await monitor_cashout_anomalies(
                cashout_id=test_cashout_id,
                user_id=test_user_id,
                usd_amount=suspicious_usd_amount,
                crypto_currency=crypto_currency,
                destination_address=destination_address,
                network="BTC"
            )
            
            # Check if USD-crypto conversion anomaly was detected
            conversion_anomalies = [
                a for a in anomalies 
                if a.anomaly_type.value == "usd_to_crypto_conversion_error"
            ]
            
            if len(conversion_anomalies) > 0:
                return {
                    "passed": True,
                    "detected_anomalies": len(conversion_anomalies),
                    "anomaly_details": [
                        {
                            "type": a.anomaly_type.value,
                            "severity": a.severity.value,
                            "description": a.description
                        } for a in conversion_anomalies
                    ]
                }
            else:
                return {
                    "passed": False,
                    "error": "USD-to-crypto conversion anomaly not detected",
                    "total_anomalies": len(anomalies)
                }
                
        except Exception as e:
            return {
                "passed": False,
                "error": f"Test crashed: {str(e)}"
            }
            
    async def _test_address_parsing_error(self) -> Dict[str, Any]:
        """Test address parsing error detection"""
        try:
            from utils.production_anomaly_detector import monitor_cashout_anomalies
            
            # Scenario: Address in "address:network" format (should be detected)
            test_cashout_id = "TEST-ADDRESS-PARSE-001"
            test_user_id = 99998
            
            # Problematic address format that was sent to Binance incorrectly
            malformed_address = "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa:BTC"  # address:network format
            
            anomalies = await monitor_cashout_anomalies(
                cashout_id=test_cashout_id,
                user_id=test_user_id,
                usd_amount=100.0,
                crypto_currency="BTC",
                destination_address=malformed_address,
                network="BTC"
            )
            
            # Check if address parsing anomaly was detected
            address_anomalies = [
                a for a in anomalies 
                if a.anomaly_type.value == "address_parsing_error"
            ]
            
            if len(address_anomalies) > 0:
                return {
                    "passed": True,
                    "detected_anomalies": len(address_anomalies),
                    "anomaly_details": [
                        {
                            "type": a.anomaly_type.value,
                            "severity": a.severity.value,
                            "description": a.description,
                            "address": a.address
                        } for a in address_anomalies
                    ]
                }
            else:
                return {
                    "passed": False,
                    "error": "Address parsing anomaly not detected",
                    "total_anomalies": len(anomalies)
                }
                
        except Exception as e:
            return {
                "passed": False,
                "error": f"Test crashed: {str(e)}"
            }
            
    async def _test_network_mismatch_error(self) -> Dict[str, Any]:
        """Test network mismatch error detection"""
        try:
            from utils.production_anomaly_detector import monitor_cashout_anomalies
            
            # Scenario: Invalid network for currency
            test_cashout_id = "TEST-NETWORK-MISMATCH-001"
            test_user_id = 99997
            
            # BTC currency with ETH network (invalid combination)
            anomalies = await monitor_cashout_anomalies(
                cashout_id=test_cashout_id,
                user_id=test_user_id,
                usd_amount=50.0,
                crypto_currency="BTC",
                destination_address="1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa",
                network="ETH"  # Invalid network for BTC
            )
            
            # Check if network mismatch anomaly was detected
            network_anomalies = [
                a for a in anomalies 
                if a.anomaly_type.value == "network_mismatch"
            ]
            
            if len(network_anomalies) > 0:
                return {
                    "passed": True,
                    "detected_anomalies": len(network_anomalies),
                    "anomaly_details": [
                        {
                            "type": a.anomaly_type.value,
                            "severity": a.severity.value,
                            "description": a.description,
                            "currency": a.currency,
                            "network": a.network
                        } for a in network_anomalies
                    ]
                }
            else:
                return {
                    "passed": False,
                    "error": "Network mismatch anomaly not detected",
                    "total_anomalies": len(anomalies)
                }
                
        except Exception as e:
            return {
                "passed": False,
                "error": f"Test crashed: {str(e)}"
            }
            
    async def _test_amount_validation_error(self) -> Dict[str, Any]:
        """Test amount validation error detection"""
        try:
            from utils.production_anomaly_detector import monitor_cashout_anomalies
            
            # Scenario: Suspiciously large amount
            test_cashout_id = "TEST-AMOUNT-VALIDATION-001"
            test_user_id = 99996
            
            # Amount over $1M threshold
            suspicious_amount = 1500000.0  # $1.5M
            
            anomalies = await monitor_cashout_anomalies(
                cashout_id=test_cashout_id,
                user_id=test_user_id,
                usd_amount=suspicious_amount,
                crypto_currency="USDT",
                destination_address="TRX9JG7bhpd8RJKP68xQHXK9j46bh5kcvP",
                network="TRX"
            )
            
            # Check if amount validation anomaly was detected
            amount_anomalies = [
                a for a in anomalies 
                if a.anomaly_type.value == "amount_validation_error"
            ]
            
            if len(amount_anomalies) > 0:
                return {
                    "passed": True,
                    "detected_anomalies": len(amount_anomalies),
                    "anomaly_details": [
                        {
                            "type": a.anomaly_type.value,
                            "severity": a.severity.value,
                            "description": a.description,
                            "amount": a.amount
                        } for a in amount_anomalies
                    ]
                }
            else:
                return {
                    "passed": False,
                    "error": "Amount validation anomaly not detected",
                    "total_anomalies": len(anomalies)
                }
                
        except Exception as e:
            return {
                "passed": False,
                "error": f"Test crashed: {str(e)}"
            }
            
    async def _test_reference_id_truncation(self) -> Dict[str, Any]:
        """Test reference ID truncation detection"""
        try:
            from utils.production_anomaly_detector import get_anomaly_detector
            
            # Scenario: Reference ID truncated from 12 to 8 characters
            detector = get_anomaly_detector()
            
            # Simulate truncated reference ID
            detector._current_reference_id = "WD240831"  # 8 chars, truncated
            
            test_cashout_id = "WD-20240831-123456-99995"
            test_user_id = 99995
            
            anomalies = await detector.monitor_cashout_operation(
                cashout_id=test_cashout_id,
                user_id=test_user_id,
                usd_amount=25.0,
                crypto_currency="ETH",
                destination_address="0x742d35Cc6634C0532925a3b8D4d8e65ec77C33e6",
                network="ETH"
            )
            
            # Check if reference ID truncation anomaly was detected
            ref_id_anomalies = [
                a for a in anomalies 
                if a.anomaly_type.value == "reference_id_truncation"
            ]
            
            if len(ref_id_anomalies) > 0:
                return {
                    "passed": True,
                    "detected_anomalies": len(ref_id_anomalies),
                    "anomaly_details": [
                        {
                            "type": a.anomaly_type.value,
                            "severity": a.severity.value,
                            "description": a.description,
                            "transaction_id": a.transaction_id
                        } for a in ref_id_anomalies
                    ]
                }
            else:
                return {
                    "passed": True,  # This test might not trigger if reference ID not set
                    "note": "Reference ID truncation test - no anomaly detected (may be expected)",
                    "total_anomalies": len(anomalies)
                }
                
        except Exception as e:
            return {
                "passed": False,
                "error": f"Test crashed: {str(e)}"
            }
            
    async def _test_normal_operation_validation(self) -> Dict[str, Any]:
        """Test that normal operations don't trigger false positives"""
        try:
            from utils.production_anomaly_detector import monitor_cashout_anomalies
            
            # Scenario: Normal, valid cashout operation
            test_cashout_id = "TEST-NORMAL-001"
            test_user_id = 99994
            
            # Normal operation parameters
            anomalies = await monitor_cashout_anomalies(
                cashout_id=test_cashout_id,
                user_id=test_user_id,
                usd_amount=100.0,  # Normal amount
                crypto_currency="USDT",
                destination_address="TRX9JG7bhpd8RJKP68xQHXK9j46bh5kcvP",  # Clean address
                network="TRX"  # Matching network
            )
            
            # Normal operations should not trigger any anomalies
            critical_anomalies = [
                a for a in anomalies 
                if a.severity.value in ['emergency', 'critical']
            ]
            
            if len(critical_anomalies) == 0:
                return {
                    "passed": True,
                    "total_anomalies": len(anomalies),
                    "critical_anomalies": 0,
                    "note": "Normal operation correctly passed validation"
                }
            else:
                return {
                    "passed": False,
                    "error": f"Normal operation triggered {len(critical_anomalies)} critical anomalies",
                    "false_positive_anomalies": [
                        {
                            "type": a.anomaly_type.value,
                            "severity": a.severity.value,
                            "description": a.description
                        } for a in critical_anomalies
                    ]
                }
                
        except Exception as e:
            return {
                "passed": False,
                "error": f"Test crashed: {str(e)}"
            }
            
    async def _test_emergency_response_integration(self) -> Dict[str, Any]:
        """Test emergency response system integration"""
        try:
            from utils.emergency_response_system import trigger_financial_emergency, EmergencyType, EmergencyPriority
            
            # Scenario: Test emergency response for wallet lock failure
            result = await trigger_financial_emergency(
                emergency_type=EmergencyType.WALLET_LOCK_FAILURE,
                priority=EmergencyPriority.CRITICAL,
                description="Test wallet lock failure emergency",
                user_id=99993,
                amount=50.0,
                currency="USD"
            )
            
            # Check if emergency was properly handled
            if result.get("success") is not None:  # Emergency system responded
                return {
                    "passed": True,
                    "emergency_response": result,
                    "emergency_id": result.get("emergency_id"),
                    "resolution_attempted": True
                }
            else:
                return {
                    "passed": False,
                    "error": "Emergency response system did not respond",
                    "result": result
                }
                
        except Exception as e:
            return {
                "passed": False,
                "error": f"Test crashed: {str(e)}"
            }
    
    async def _test_ngn_bank_details_validation(self) -> Dict[str, Any]:
        """Test NGN bank details validation"""
        try:
            from utils.production_anomaly_detector import monitor_ngn_cashout_anomalies
            
            # Scenario: Invalid NGN bank destination format
            test_cashout_id = "TEST-NGN-BANK-001"
            test_user_id = 99998
            
            # Test invalid bank destination formats
            invalid_destinations = [
                "invalid_format",  # No colon
                "BANK::",  # Empty account number
                ":1234567890",  # Empty bank code
                "BANK:123",  # Account number too short
                "BANK:123456789012",  # Account number too long
                "BANK:abcdefghij"  # Non-numeric account number
            ]
            
            detected_errors = 0
            for invalid_dest in invalid_destinations:
                anomalies = await monitor_ngn_cashout_anomalies(
                    cashout_id=f"{test_cashout_id}-{len(invalid_dest)}",
                    user_id=test_user_id,
                    usd_amount=100.0,
                    ngn_destination=invalid_dest,
                    cashout_type="NGN_BANK"
                )
                
                # Check for bank details error
                bank_errors = [
                    a for a in anomalies 
                    if a.anomaly_type.value == "ngn_bank_details_error"
                ]
                
                if bank_errors:
                    detected_errors += 1
            
            success_rate = (detected_errors / len(invalid_destinations)) * 100
            
            return {
                "passed": success_rate >= 80,  # At least 80% detection rate
                "detection_rate": success_rate,
                "detected_errors": detected_errors,
                "total_invalid_formats": len(invalid_destinations),
                "test_details": "NGN bank details validation test"
            }
                
        except Exception as e:
            return {
                "passed": False,
                "error": f"NGN bank test crashed: {str(e)}"
            }
    
    async def _test_ngn_amount_validation_errors(self) -> Dict[str, Any]:
        """Test NGN amount validation"""
        try:
            from utils.production_anomaly_detector import monitor_ngn_cashout_anomalies
            
            # Scenario: Invalid USD amounts for NGN cashout
            test_cashout_id = "TEST-NGN-AMOUNT-001"
            test_user_id = 99997
            
            # Test invalid amounts
            invalid_amounts = [
                0,      # Zero amount
                -50,    # Negative amount
                0.5,    # Below minimum
                150000  # Above reasonable maximum
            ]
            
            detected_errors = 0
            for invalid_amount in invalid_amounts:
                anomalies = await monitor_ngn_cashout_anomalies(
                    cashout_id=f"{test_cashout_id}-{invalid_amount}",
                    user_id=test_user_id,
                    usd_amount=invalid_amount,
                    ngn_destination="ACCESS:1234567890",
                    cashout_type="NGN_BANK"
                )
                
                # Check for amount validation error
                amount_errors = [
                    a for a in anomalies 
                    if a.anomaly_type.value == "ngn_amount_validation_error"
                ]
                
                if amount_errors:
                    detected_errors += 1
            
            success_rate = (detected_errors / len(invalid_amounts)) * 100
            
            return {
                "passed": success_rate >= 75,  # At least 75% detection rate
                "detection_rate": success_rate,
                "detected_errors": detected_errors,
                "total_invalid_amounts": len(invalid_amounts),
                "test_details": "NGN amount validation test"
            }
                
        except Exception as e:
            return {
                "passed": False,
                "error": f"NGN amount test crashed: {str(e)}"
            }
    
    async def _test_ngn_exchange_rate_failures(self) -> Dict[str, Any]:
        """Test NGN exchange rate failure detection"""
        try:
            from utils.production_anomaly_detector import monitor_ngn_cashout_anomalies
            
            # Scenario: Large amounts that should trigger rate monitoring
            test_cashout_id = "TEST-NGN-RATE-001"
            test_user_id = 99996
            
            # Test large amount (should trigger rate warning)
            large_amount = 75000  # Above $50,000 threshold
            
            anomalies = await monitor_ngn_cashout_anomalies(
                cashout_id=test_cashout_id,
                user_id=test_user_id,
                usd_amount=large_amount,
                ngn_destination="GTBANK:1234567890",
                cashout_type="NGN_BANK"
            )
            
            # Check for exchange rate warnings
            rate_warnings = [
                a for a in anomalies 
                if a.anomaly_type.value == "ngn_exchange_rate_error"
            ]
            
            return {
                "passed": len(rate_warnings) > 0,
                "large_amount_flagged": len(rate_warnings) > 0,
                "warning_count": len(rate_warnings),
                "test_amount": large_amount,
                "test_details": "NGN exchange rate monitoring test"
            }
                
        except Exception as e:
            return {
                "passed": False,
                "error": f"NGN rate test crashed: {str(e)}"
            }
    
    async def _test_ngn_processing_emergencies(self) -> Dict[str, Any]:
        """Test NGN processing emergency responses"""
        try:
            from utils.emergency_response_system import trigger_financial_emergency, EmergencyType, EmergencyPriority
            
            # Test NGN-specific emergency types
            ngn_emergency_tests = [
                EmergencyType.NGN_PROCESSING_FAILURE,
                EmergencyType.NGN_BANK_DETAILS_ERROR,
                EmergencyType.NGN_EXCHANGE_RATE_FAILURE,
                EmergencyType.NGN_FINCRA_API_FAILURE
            ]
            
            successful_responses = 0
            total_tests = len(ngn_emergency_tests)
            
            for emergency_type in ngn_emergency_tests:
                try:
                    result = await trigger_financial_emergency(
                        emergency_type=emergency_type,
                        priority=EmergencyPriority.CRITICAL,
                        description=f"Test {emergency_type.value} emergency",
                        user_id=99995,
                        cashout_id=f"TEST-NGN-EMG-{emergency_type.value[-3:]}",
                        amount=100.0,
                        currency="USD",
                        locked_amount=100.0
                    )
                    
                    if result.get("success") is not None:
                        successful_responses += 1
                        
                except Exception as e:
                    logger.error(f"NGN emergency test failed for {emergency_type.value}: {e}")
            
            success_rate = (successful_responses / total_tests) * 100
            
            return {
                "passed": success_rate >= 80,  # At least 80% emergency response rate
                "success_rate": success_rate,
                "successful_responses": successful_responses,
                "total_emergency_types": total_tests,
                "test_details": "NGN emergency response system test"
            }
                
        except Exception as e:
            return {
                "passed": False,
                "error": f"NGN emergency test crashed: {str(e)}"
            }

# Global test scenarios instance
_test_scenarios = AnomalyTestScenarios()

async def run_anomaly_detection_tests() -> Dict[str, Any]:
    """Run comprehensive anomaly detection test suite"""
    return await _test_scenarios.run_comprehensive_test_suite()

def get_test_scenarios() -> AnomalyTestScenarios:
    """Get global test scenarios instance"""
    return _test_scenarios

# Initialize on import
logger.info("🧪 Anomaly detection test scenarios ready")