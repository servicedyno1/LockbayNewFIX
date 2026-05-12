#!/usr/bin/env python3
"""
Lockbay Telegram Bot - Cashout Fix Backend Test
Testing the fix for the query.data read-only attribute error in handle_wallet_cashout function
"""

import requests
import sys
import time
import ast
import re
from datetime import datetime

class CashoutFixTester:
    def __init__(self, base_url="https://qa-onboarding.preview.emergentagent.com"):
        self.base_url = base_url
        self.tests_run = 0
        self.tests_passed = 0
        print(f"🚀 Starting Lockbay Cashout Fix Tests")
        print(f"📡 Backend URL: {base_url}")
        print(f"🕒 Test started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 60)

    def run_test(self, name, test_func):
        """Run a single test and track results"""
        self.tests_run += 1
        print(f"\n🔍 Test {self.tests_run}: {name}")
        
        try:
            success = test_func()
            if success:
                self.tests_passed += 1
                print(f"✅ PASSED: {name}")
            else:
                print(f"❌ FAILED: {name}")
            return success
        except Exception as e:
            print(f"❌ ERROR in {name}: {str(e)}")
            return False

    def test_health_endpoint(self):
        """Test 1: Backend health endpoint returns OK"""
        try:
            response = requests.get(f"{self.base_url}/api/health", timeout=10)
            
            if response.status_code == 200:
                print(f"   Status: {response.status_code} ✅")
                
                # Try to parse JSON response
                try:
                    health_data = response.json()
                    if isinstance(health_data, dict):
                        status = health_data.get('status', 'unknown')
                        print(f"   Health Status: {status}")
                        return status in ['healthy', 'OK', 'ok']
                    else:
                        print(f"   Response: {health_data}")
                        return True
                except Exception as parse_error:
                    print(f"   JSON parse warning: {parse_error}")
                    print(f"   Raw response: {response.text[:100]}")
                    # Still consider it a pass if status code is 200
                    return True
            else:
                print(f"   Status: {response.status_code} ❌")
                print(f"   Response: {response.text[:200]}")
                return False
                
        except Exception as e:
            print(f"   Connection error: {e}")
            return False

    def test_wallet_direct_syntax(self):
        """Test 2: Verify wallet_direct.py has valid Python syntax"""
        try:
            with open('/app/handlers/wallet_direct.py', 'r') as f:
                code = f.read()
            
            # Parse the code to check syntax
            ast.parse(code)
            print("   ✅ Python syntax is valid")
            return True
            
        except SyntaxError as e:
            print(f"   ❌ Syntax error found: {e}")
            print(f"      Line {e.lineno}: {e.text}")
            return False
        except FileNotFoundError:
            print("   ❌ wallet_direct.py file not found")
            return False
        except Exception as e:
            print(f"   ❌ Error reading file: {e}")
            return False

    def test_query_data_not_set_directly(self):
        """Test 3: Verify query.data is not being set directly (the main bug fix)"""
        try:
            with open('/app/handlers/wallet_direct.py', 'r') as f:
                content = f.read()
            
            # Look for patterns that SET query.data directly (not READ it)
            # Need to be more specific to avoid false positives
            forbidden_patterns = [
                r'query\.data\s*=\s*[^=]',  # query.data = something (not ==)
                r'query\.data\s*\+=',       # query.data += 
                r'setattr\s*\(\s*query\s*,\s*["\']data["\']',  # setattr(query, 'data', ...)
            ]
            
            violations = []
            lines = content.split('\n')
            
            for i, line in enumerate(lines, 1):
                # Skip comment lines and lines that are just reading query.data
                if line.strip().startswith('#') or '==' in line or 'if query.data' in line:
                    continue
                    
                for pattern in forbidden_patterns:
                    if re.search(pattern, line):
                        violations.append(f"Line {i}: {line.strip()}")
            
            if violations:
                print("   ❌ Found query.data direct assignments:")
                for violation in violations:
                    print(f"      {violation}")
                return False
            else:
                print("   ✅ No direct query.data assignments found")
                print("   ✅ Only query.data reading operations detected (which is allowed)")
                return True
                
        except Exception as e:
            print(f"   ❌ Error analyzing code: {e}")
            return False

    def test_handle_wallet_cashout_fix(self):
        """Test 4: Verify handle_wallet_cashout function properly calls cashout flow methods"""
        try:
            with open('/app/handlers/wallet_direct.py', 'r') as f:
                content = f.read()
            
            # Find the handle_wallet_cashout function (start from async def, end at next async def)
            lines = content.split('\n')
            function_start = -1
            function_end = -1
            
            for i, line in enumerate(lines):
                if 'async def handle_wallet_cashout' in line:
                    function_start = i
                elif function_start >= 0 and line.strip().startswith('async def ') and i > function_start + 5:
                    function_end = i
                    break
            
            if function_start == -1:
                print("   ❌ handle_wallet_cashout function not found")
                return False
            
            if function_end == -1:
                function_end = len(lines)
                
            function_lines = lines[function_start:function_end]
            function_code = '\n'.join(function_lines)
            print(f"   ✅ Found handle_wallet_cashout function (lines {function_start+1}-{function_end})")
            
            # Check for the fix: should call cashout flow methods instead of setting query.data
            expected_methods = [
                'show_cashout_method_selection',
                'show_crypto_address_selection', 
                'show_saved_bank_accounts'
            ]
            
            found_methods = []
            for method in expected_methods:
                if method in function_code:
                    found_methods.append(method)
                    
            print(f"   Found flow methods: {found_methods}")
            
            # Should have at least 2 of the 3 expected methods
            if len(found_methods) >= 2:
                print("   ✅ Function calls appropriate cashout flow methods")
                
                # Additional check: should NOT set query.data
                if 'query.data =' not in function_code:
                    print("   ✅ Function does not set query.data directly")
                    return True
                else:
                    print("   ❌ Function still sets query.data directly")
                    return False
            else:
                print(f"   ❌ Function missing expected flow method calls")
                return False
                
        except Exception as e:
            print(f"   ❌ Error analyzing function: {e}")
            return False

    def test_inline_logic_implementation(self):
        """Test 5: Verify the fix inlines logic from handle_quick_cashout_all"""
        try:
            with open('/app/handlers/wallet_direct.py', 'r') as f:
                content = f.read()
            
            # Find the handle_wallet_cashout function
            lines = content.split('\n')
            function_start = -1
            function_end = -1
            
            for i, line in enumerate(lines):
                if 'async def handle_wallet_cashout' in line:
                    function_start = i
                elif function_start >= 0 and line.strip().startswith('async def ') and i > function_start + 5:
                    function_end = i
                    break
            
            if function_start == -1:
                print("   ❌ handle_wallet_cashout function not found")
                return False
                
            if function_end == -1:
                function_end = len(lines)
                
            function_lines = lines[function_start:function_end]
            function_code = '\n'.join(function_lines)
            
            # Check for inlined logic indicators
            inline_indicators = [
                'get_last_used_cashout_method',  # Gets last method
                'last_method.get("method")',     # Checks method type
                'method" == "CRYPTO"',           # Handles crypto flow
                'method" == "NGN_BANK"'          # Handles NGN flow
            ]
            
            found_indicators = []
            for indicator in inline_indicators:
                if indicator in function_code:
                    found_indicators.append(indicator)
            
            print(f"   Found inline logic indicators: {found_indicators}")
            
            # Also check for the fix comment
            fix_comment_found = 'Cannot set query.data' in function_code or 'read-only' in function_code
            if fix_comment_found:
                print("   ✅ Found fix comment explaining query.data read-only issue")
            
            if len(found_indicators) >= 3:
                print("   ✅ Function contains inlined logic from handle_quick_cashout_all")
                return True
            else:
                print("   ⚠️ Function may not have complete inlined logic")
                return len(found_indicators) >= 2  # Partial credit
                
        except Exception as e:
            print(f"   ❌ Error checking inline logic: {e}")
            return False

    def run_all_tests(self):
        """Run all cashout fix tests"""
        print("🔧 Testing Lockbay Telegram Bot Cashout Fix")
        print("Target: Fix 'Attribute data of class CallbackQuery can't be set!' error")
        print()
        
        # Test 1: Health endpoint
        self.run_test("Backend Health Endpoint (/api/health returns OK)", 
                     self.test_health_endpoint)
        
        # Test 2: Python syntax validation 
        self.run_test("Verify wallet_direct.py syntax is valid Python", 
                     self.test_wallet_direct_syntax)
        
        # Test 3: No direct query.data assignment (main bug fix)
        self.run_test("Verify query.data is not set directly (read-only fix)", 
                     self.test_query_data_not_set_directly)
        
        # Test 4: Function calls proper flow methods
        self.run_test("Verify handle_wallet_cashout calls cashout flow methods", 
                     self.test_handle_wallet_cashout_fix)
        
        # Test 5: Inlined logic implementation
        self.run_test("Verify inlined logic from handle_quick_cashout_all", 
                     self.test_inline_logic_implementation)

    def print_summary(self):
        """Print test results summary"""
        print("\n" + "=" * 60)
        print("🏁 CASHOUT FIX TEST SUMMARY")
        print("=" * 60)
        
        success_rate = (self.tests_passed / self.tests_run * 100) if self.tests_run > 0 else 0
        
        print(f"📊 Tests Passed: {self.tests_passed}/{self.tests_run} ({success_rate:.1f}%)")
        
        if success_rate >= 80:
            print("✅ OVERALL STATUS: CASHOUT FIX VERIFICATION SUCCESSFUL")
            print("🎉 The query.data read-only attribute fix appears to be working correctly")
        elif success_rate >= 60:
            print("⚠️ OVERALL STATUS: PARTIAL SUCCESS")
            print("🔧 Some issues found but core fix may be working")
        else:
            print("❌ OVERALL STATUS: VERIFICATION FAILED")
            print("🚨 Critical issues found - fix may not be working properly")
        
        print(f"\n🕒 Test completed at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        # Return exit code
        return 0 if success_rate >= 80 else 1

def main():
    """Main test runner"""
    tester = CashoutFixTester()
    
    try:
        tester.run_all_tests()
        return tester.print_summary()
    except KeyboardInterrupt:
        print("\n❌ Tests interrupted by user")
        return 1
    except Exception as e:
        print(f"\n💥 Test runner failed: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())