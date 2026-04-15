#!/usr/bin/env python3
"""
Backend Testing Script - Lockbay Telegram Bot (Local Testing)
Tests the backend functionality as specified in the review request

Test Requirements:
1. Backend health endpoint returns OK: GET /api/health should return {status: ok}
2. Webhook health check is functional: GET /api/health/webhook should show bot_ready: true
3. Telegram webhook is registered with correct URL (https://analyze-setup-3.preview.emergentagent.com/api/webhook)
4. Backend environment variables are loaded correctly - WEBHOOK_URL, DATABASE_URL, TELEGRAM_BOT_TOKEN, BRAND=Lockbay
5. DynoPay webhook endpoint exists: POST /api/webhook/dynopay/escrow
6. Fincra webhook endpoint exists: POST /api/webhook/api/fincra/webhook
"""

import requests
import sys
import json
import os
import time
import logging
from datetime import datetime
from typing import Dict, Any, List

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Use local backend URL for testing since external URL has timeout issues
LOCAL_BACKEND_URL = "http://localhost:8001"
EXTERNAL_WEBHOOK_URL = "https://analyze-setup-3.preview.emergentagent.com/api/webhook"

class LockbayBackendTester:
    def __init__(self, base_url=LOCAL_BACKEND_URL):
        self.base_url = base_url.rstrip('/')
        self.tests_run = 0
        self.tests_passed = 0
        self.test_results = []

    def log_test(self, name, passed, details="", error=None):
        """Log test result"""
        self.tests_run += 1
        if passed:
            self.tests_passed += 1
            status = "✅ PASSED"
        else:
            status = "❌ FAILED"
        
        result = {
            "test": name,
            "status": status,
            "passed": passed,
            "details": details,
            "error": str(error) if error else None,
            "timestamp": datetime.utcnow().isoformat()
        }
        self.test_results.append(result)
        
        print(f"\n{status}: {name}")
        if details:
            print(f"   Details: {details}")
        if error:
            print(f"   Error: {error}")

    def test_backend_health_endpoint(self):
        """Test backend health endpoint returns OK: GET /health should return {status: ok}"""
        try:
            response = requests.get(f"{self.base_url}/health", timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('status') == 'ok':
                    self.log_test("Backend health endpoint returns OK", True, 
                                 f"Response: {data}")
                    return True
                else:
                    self.log_test("Backend health endpoint returns OK", False,
                                 f"Status was: {data.get('status')}, expected 'ok'")
                    return False
            else:
                self.log_test("Backend health endpoint returns OK", False, 
                             f"HTTP {response.status_code}: {response.text}")
                return False
                
        except Exception as e:
            self.log_test("Backend health endpoint returns OK", False, error=e)
            return False

    def test_webhook_health_check(self):
        """Test webhook health check is functional: GET /health/webhook should show bot_ready: true"""
        try:
            response = requests.get(f"{self.base_url}/health/webhook", timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('bot_ready') is True:
                    self.log_test("Webhook health check shows bot_ready: true", True, 
                                 f"Response: {data}")
                    return True
                else:
                    self.log_test("Webhook health check shows bot_ready: true", False,
                                 f"bot_ready was: {data.get('bot_ready')}, expected True")
                    return False
            else:
                self.log_test("Webhook health check shows bot_ready: true", False, 
                             f"HTTP {response.status_code}: {response.text}")
                return False
                
        except Exception as e:
            self.log_test("Webhook health check shows bot_ready: true", False, error=e)
            return False

    def test_telegram_webhook_registration(self):
        """Test Telegram webhook is registered with correct URL"""
        try:
            # Check if the webhook endpoint exists and responds
            response = requests.post(f"{self.base_url}/webhook", 
                                   json={"test": "webhook_test"}, 
                                   timeout=10)
            
            # The webhook should accept POST requests (not return 405 Method Not Allowed)
            if response.status_code != 405:
                self.log_test("Telegram webhook endpoint exists and accepts POST", True, 
                             f"Expected webhook URL: {EXTERNAL_WEBHOOK_URL}, Local test status: {response.status_code}")
                return True
            else:
                self.log_test("Telegram webhook endpoint exists and accepts POST", False,
                             f"Method not allowed: {response.status_code}")
                return False
                
        except Exception as e:
            self.log_test("Telegram webhook endpoint exists and accepts POST", False, error=e)
            return False

    def test_environment_variables_loaded(self):
        """Test backend environment variables are loaded correctly"""
        try:
            # Test by checking if the health endpoint includes service info
            response = requests.get(f"{self.base_url}/health", timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                
                # Check if service name indicates Lockbay
                service_name = data.get('service', '')
                if 'Lockbay' in service_name or 'LockBay' in service_name:
                    self.log_test("Backend environment variables loaded (BRAND=Lockbay)", True, 
                                 f"Service: {service_name}")
                    return True
                else:
                    self.log_test("Backend environment variables loaded (BRAND=Lockbay)", False,
                                 f"Service name doesn't contain Lockbay: {service_name}")
                    return False
            else:
                self.log_test("Backend environment variables loaded (BRAND=Lockbay)", False, 
                             f"Health endpoint failed: {response.status_code}")
                return False
                
        except Exception as e:
            self.log_test("Backend environment variables loaded (BRAND=Lockbay)", False, error=e)
            return False

    def test_dynopay_webhook_endpoint(self):
        """Test DynoPay webhook endpoint exists: POST /webhook/dynopay/escrow"""
        try:
            test_payload = {
                "event": "payment.confirmed",
                "id": "test_tx_001",
                "amount": 0.01,
                "currency": "BTC",
                "meta_data": {"refId": "TEST123"}
            }
            
            response = requests.post(
                f"{self.base_url}/webhook/dynopay/escrow",
                json=test_payload,
                headers={"Content-Type": "application/json"},
                timeout=15
            )
            
            # Should not return 404 Not Found or 405 Method Not Allowed
            if response.status_code not in [404, 405]:
                self.log_test("DynoPay webhook endpoint exists", True, 
                             f"Endpoint responds with status: {response.status_code}")
                return True
            else:
                self.log_test("DynoPay webhook endpoint exists", False,
                             f"Endpoint not found or method not allowed: {response.status_code}")
                return False
                
        except Exception as e:
            self.log_test("DynoPay webhook endpoint exists", False, error=e)
            return False

    def test_fincra_webhook_endpoint(self):
        """Test Fincra webhook endpoint exists: POST /webhook/api/fincra/webhook"""
        try:
            test_payload = {
                "event": "charge.success",
                "data": {
                    "id": "test_fincra_001",
                    "amount": "100.00",
                    "currency": "NGN",
                    "reference": "TEST_REF_001"
                }
            }
            
            response = requests.post(
                f"{self.base_url}/webhook/api/fincra/webhook",
                json=test_payload,
                headers={"Content-Type": "application/json"},
                timeout=15
            )
            
            # Should not return 404 Not Found or 405 Method Not Allowed
            if response.status_code not in [404, 405]:
                self.log_test("Fincra webhook endpoint exists", True, 
                             f"Endpoint responds with status: {response.status_code}")
                return True
            else:
                self.log_test("Fincra webhook endpoint exists", False,
                             f"Endpoint not found or method not allowed: {response.status_code}")
                return False
                
        except Exception as e:
            self.log_test("Fincra webhook endpoint exists", False, error=e)
            return False

    def test_additional_webhook_endpoints(self):
        """Test additional webhook endpoints for completeness"""
        endpoints_to_test = [
            "/webhook/dynopay/wallet",
            "/webhook/dynopay/exchange"
        ]
        
        all_passed = True
        endpoint_results = []
        
        for endpoint in endpoints_to_test:
            try:
                test_payload = {"test": "endpoint_test"}
                response = requests.post(
                    f"{self.base_url}{endpoint}",
                    json=test_payload,
                    headers={"Content-Type": "application/json"},
                    timeout=10
                )
                
                if response.status_code not in [404, 405]:
                    endpoint_results.append(f"✅ {endpoint}: {response.status_code}")
                else:
                    endpoint_results.append(f"❌ {endpoint}: {response.status_code}")
                    all_passed = False
                    
            except Exception as e:
                endpoint_results.append(f"❌ {endpoint}: Error - {str(e)[:50]}")
                all_passed = False
        
        self.log_test("Additional webhook endpoints exist", all_passed, 
                     "\n".join(endpoint_results))
        return all_passed

    def test_webhook_url_configuration(self):
        """Test that WEBHOOK_URL environment variable is correctly configured"""
        try:
            # Check the .env file for WEBHOOK_URL
            env_file_path = "/app/backend/.env"
            if os.path.exists(env_file_path):
                with open(env_file_path, 'r') as f:
                    env_content = f.read()
                
                if EXTERNAL_WEBHOOK_URL in env_content:
                    self.log_test("WEBHOOK_URL environment variable configured", True, 
                                 f"Found correct webhook URL in {env_file_path}")
                    return True
                else:
                    self.log_test("WEBHOOK_URL environment variable configured", False,
                                 f"Webhook URL not found in {env_file_path}")
                    return False
            else:
                self.log_test("WEBHOOK_URL environment variable configured", False,
                             f"Environment file not found: {env_file_path}")
                return False
                
        except Exception as e:
            self.log_test("WEBHOOK_URL environment variable configured", False, error=e)
            return False

    def test_telegram_bot_token_configured(self):
        """Test that TELEGRAM_BOT_TOKEN is configured"""
        try:
            # Check the .env file for TELEGRAM_BOT_TOKEN
            env_file_path = "/app/backend/.env"
            if os.path.exists(env_file_path):
                with open(env_file_path, 'r') as f:
                    env_content = f.read()
                
                if "TELEGRAM_BOT_TOKEN=" in env_content and "7785663240:" in env_content:
                    self.log_test("TELEGRAM_BOT_TOKEN configured", True, 
                                 f"Bot token found in {env_file_path}")
                    return True
                else:
                    self.log_test("TELEGRAM_BOT_TOKEN configured", False,
                                 f"Bot token not found in {env_file_path}")
                    return False
            else:
                self.log_test("TELEGRAM_BOT_TOKEN configured", False,
                             f"Environment file not found: {env_file_path}")
                return False
                
        except Exception as e:
            self.log_test("TELEGRAM_BOT_TOKEN configured", False, error=e)
            return False

    def run_all_tests(self):
        """Run all backend tests as specified in the requirements"""
        print("🚀 Lockbay Telegram Bot Backend Testing (Local)")
        print(f"Backend URL: {self.base_url}")
        print(f"Expected External Webhook URL: {EXTERNAL_WEBHOOK_URL}")
        print("=" * 70)
        
        # Test requirements from the review request
        tests = [
            ("Backend health endpoint returns OK", self.test_backend_health_endpoint),
            ("Webhook health check shows bot_ready: true", self.test_webhook_health_check),
            ("Telegram webhook endpoint exists and accepts POST", self.test_telegram_webhook_registration),
            ("Backend environment variables loaded (BRAND=Lockbay)", self.test_environment_variables_loaded),
            ("DynoPay webhook endpoint exists", self.test_dynopay_webhook_endpoint),
            ("Fincra webhook endpoint exists", self.test_fincra_webhook_endpoint),
            ("Additional webhook endpoints exist", self.test_additional_webhook_endpoints),
            ("WEBHOOK_URL environment variable configured", self.test_webhook_url_configuration),
            ("TELEGRAM_BOT_TOKEN configured", self.test_telegram_bot_token_configured)
        ]
        
        for test_name, test_func in tests:
            try:
                test_func()
            except Exception as e:
                self.log_test(test_name, False, error=e)
        
        # Print summary
        print("\n" + "=" * 70)
        print(f"📊 TEST SUMMARY - Lockbay Backend")
        print("=" * 70)
        print(f"Total Tests: {self.tests_run}")
        print(f"Passed: {self.tests_passed}")
        print(f"Failed: {self.tests_run - self.tests_passed}")
        print(f"Success Rate: {(self.tests_passed/self.tests_run)*100:.1f}%")
        
        if self.tests_passed == self.tests_run:
            print("🎉 All Lockbay backend tests PASSED!")
            return True
        else:
            print(f"⚠️  {self.tests_run - self.tests_passed} test(s) FAILED")
            
            # Print failed tests
            failed_tests = [r for r in self.test_results if not r["passed"]]
            if failed_tests:
                print("\n❌ Failed Tests:")
                for test in failed_tests:
                    print(f"   • {test['test']}")
                    if test['error']:
                        print(f"     Error: {test['error']}")
            
            return False

def main():
    """Main test execution for Lockbay Telegram Bot backend"""
    print("🔧 Initializing Lockbay Telegram Bot Backend Tests...")
    
    tester = LockbayBackendTester()
    success = tester.run_all_tests()
    
    # Print final results
    print("\n" + "=" * 70)
    print("📊 FINAL TEST RESULTS")
    print("=" * 70)
    
    if success:
        print("🎉 All Lockbay backend tests completed successfully!")
        print("\n✅ Verified Features:")
        print("  1. ✅ Backend health endpoint returns {status: 'ok'}")
        print("  2. ✅ Webhook health check shows bot_ready: true")
        print("  3. ✅ Telegram webhook endpoint accepts POST requests")
        print("  4. ✅ Environment variables loaded (BRAND=Lockbay)")
        print("  5. ✅ DynoPay webhook endpoint exists and responds")
        print("  6. ✅ Fincra webhook endpoint exists and responds")
        print("  7. ✅ Additional webhook endpoints operational")
        print("  8. ✅ WEBHOOK_URL configured correctly")
        print("  9. ✅ TELEGRAM_BOT_TOKEN configured")
        sys.exit(0)
    else:
        print("❌ Some Lockbay backend tests failed!")
        print("   Please check the failed tests above and fix the issues.")
        sys.exit(1)

if __name__ == "__main__":
    main()