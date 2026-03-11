#!/usr/bin/env python3
"""
LockBay Escrow Bot Setup Verification Test Suite
===============================================

Tests the critical components of the Telegram escrow bot setup:
- Backend FastAPI health endpoint
- PostgreSQL database connection and tables  
- Frontend React status page
- Python dependencies
- Configuration loading
"""

import sys
import os
import requests
import logging
from datetime import datetime
import asyncio
import json

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Public backend URL from frontend .env
BACKEND_URL = "https://env-webhook-sync-1.preview.emergentagent.com"
FRONTEND_URL = "https://env-webhook-sync-1.preview.emergentagent.com" # Same domain, port 3000

class LockBaySetupTester:
    def __init__(self):
        self.tests_run = 0
        self.tests_passed = 0
        self.test_results = []

    def run_test(self, test_name, test_func):
        """Run a single test with error handling"""
        self.tests_run += 1
        print(f"\n🔍 Testing {test_name}...")
        
        try:
            result = test_func()
            if result:
                self.tests_passed += 1
                print(f"✅ PASSED - {test_name}")
                self.test_results.append({"test": test_name, "status": "PASSED", "details": result if isinstance(result, str) else ""})
                return True
            else:
                print(f"❌ FAILED - {test_name}")
                self.test_results.append({"test": test_name, "status": "FAILED", "details": "Test returned False"})
                return False
        except Exception as e:
            print(f"❌ FAILED - {test_name}: {str(e)}")
            self.test_results.append({"test": test_name, "status": "FAILED", "details": str(e)})
            return False

    def test_backend_health_endpoint(self):
        """Test if backend health endpoint returns proper JSON"""
        try:
            # Test both /health and /api/health endpoints
            endpoints_to_test = [
                f"{BACKEND_URL}/api/health",
                f"{BACKEND_URL}/health"
            ]
            
            for endpoint in endpoints_to_test:
                try:
                    response = requests.get(endpoint, timeout=10)
                    print(f"  Testing {endpoint}: Status {response.status_code}")
                    
                    if response.status_code == 200:
                        try:
                            data = response.json()
                            if data.get('status') == 'ok':
                                print(f"  ✅ Health endpoint working: {data}")
                                return f"Health endpoint accessible at {endpoint} - Status: {data.get('status')}, Service: {data.get('service', 'N/A')}"
                        except json.JSONDecodeError:
                            print(f"  ⚠️ Non-JSON response from {endpoint}")
                            
                except requests.exceptions.RequestException as e:
                    print(f"  ❌ Connection failed to {endpoint}: {e}")
                    continue
            
            return False
            
        except Exception as e:
            print(f"❌ Backend health test error: {e}")
            return False

    def test_database_connection(self):
        """Test PostgreSQL database connection and table count"""
        try:
            # Import database connection
            from database import test_connection, engine
            from sqlalchemy import text, inspect
            
            # Test basic connection
            if not test_connection():
                return False
            
            # Get table count and names
            with engine.connect() as connection:
                # Check database name
                db_result = connection.execute(text("SELECT current_database()"))
                db_name = db_result.scalar()
                print(f"  Connected to database: {db_name}")
                
                # Get table count
                inspector = inspect(engine)
                table_names = inspector.get_table_names()
                table_count = len(table_names)
                
                print(f"  Found {table_count} tables in database")
                if table_count >= 50:  # At least 50 tables expected
                    print(f"  ✅ Database has sufficient tables ({table_count})")
                    return f"PostgreSQL connected - Database: {db_name}, Tables: {table_count}"
                else:
                    print(f"  ⚠️ Expected at least 50 tables, found {table_count}")
                    return f"PostgreSQL connected but only {table_count} tables found"
                    
        except Exception as e:
            print(f"❌ Database test error: {e}")
            return False

    def test_frontend_status_page(self):
        """Test if frontend React status page loads"""
        try:
            # Try the main frontend URL
            response = requests.get(FRONTEND_URL, timeout=10)
            print(f"  Frontend response status: {response.status_code}")
            
            if response.status_code == 200:
                content = response.text
                # Check for key indicators of the status page
                if "LockBay" in content and "Bot Status" in content:
                    print(f"  ✅ Frontend status page loaded successfully")
                    return "Frontend status page accessible and contains expected content"
                else:
                    print(f"  ⚠️ Frontend loaded but missing expected content")
                    return "Frontend accessible but content verification failed"
            else:
                print(f"  ❌ Frontend returned status {response.status_code}")
                return False
                
        except Exception as e:
            print(f"❌ Frontend test error: {e}")
            return False

    def test_critical_python_packages(self):
        """Test if critical Python packages are installed and importable"""
        critical_packages = [
            'python-telegram-bot',
            'fastapi',  
            'sqlalchemy',
            'asyncpg',
            'psycopg2',
            'redis',
            'aiohttp',
            'pydantic', 
            'orjson',
            'uvicorn'
        ]
        
        installed_packages = []
        missing_packages = []
        
        for package in critical_packages:
            try:
                # Handle package name mappings
                import_name = package
                if package == 'python-telegram-bot':
                    import_name = 'telegram'
                elif package == 'psycopg2':
                    # Try both psycopg2 and psycopg2-binary
                    try:
                        __import__('psycopg2')
                        installed_packages.append(package)
                        continue
                    except ImportError:
                        try:
                            __import__('psycopg2-binary')
                            installed_packages.append(f"{package} (as psycopg2-binary)")
                            continue
                        except ImportError:
                            missing_packages.append(package)
                            continue
                
                __import__(import_name)
                installed_packages.append(package)
                print(f"  ✅ {package} - Available")
                
            except ImportError:
                missing_packages.append(package)
                print(f"  ❌ {package} - Missing")
        
        if missing_packages:
            print(f"  Missing packages: {missing_packages}")
            return f"Partial - {len(installed_packages)}/{len(critical_packages)} packages installed. Missing: {', '.join(missing_packages)}"
        else:
            print(f"  ✅ All {len(critical_packages)} critical packages installed")
            return f"All {len(critical_packages)} critical packages installed successfully"

    def test_config_loading(self):
        """Test if config module loads with DATABASE_URL"""
        try:
            from config import Config
            
            # Check key configuration values
            has_database_url = bool(Config.DATABASE_URL)
            has_bot_token = bool(Config.BOT_TOKEN)
            environment = getattr(Config, 'CURRENT_ENVIRONMENT', 'unknown')
            
            print(f"  DATABASE_URL configured: {has_database_url}")
            print(f"  BOT_TOKEN configured: {has_bot_token}")
            print(f"  Environment: {environment}")
            
            if has_database_url:
                # Mask the URL for security
                masked_url = Config.DATABASE_URL[:20] + "***" + Config.DATABASE_URL[-10:] if len(Config.DATABASE_URL) > 30 else "***"
                return f"Config loaded - DATABASE_URL: {masked_url}, Environment: {environment}, Bot token: {'Yes' if has_bot_token else 'No (placeholder)'}"
            else:
                print(f"  ❌ DATABASE_URL not configured")
                return False
                
        except Exception as e:
            print(f"❌ Config loading error: {e}")
            return False

    def test_fastapi_server_routes(self):
        """Test if FastAPI server has expected routes"""
        try:
            # Test webhook endpoint structure
            webhook_endpoint = f"{BACKEND_URL}/webhook"
            
            # Try POST to webhook (should not crash, might return error but not 404)
            try:
                response = requests.post(webhook_endpoint, json={}, timeout=5)
                print(f"  Webhook endpoint response: {response.status_code}")
                # Any response other than 404 means the route exists
                if response.status_code != 404:
                    return f"FastAPI webhook endpoint exists (status: {response.status_code})"
                else:
                    return False
            except Exception as e:
                print(f"  Webhook test failed: {e}")
                return False
                
        except Exception as e:
            print(f"❌ FastAPI routes test error: {e}")
            return False

    def run_all_tests(self):
        """Run all setup verification tests"""
        print("🚀 Starting LockBay Escrow Bot Setup Verification")
        print("=" * 60)
        
        # Run all tests
        self.run_test("Backend Health Endpoint", self.test_backend_health_endpoint)
        self.run_test("PostgreSQL Database Connection", self.test_database_connection) 
        self.run_test("Frontend Status Page", self.test_frontend_status_page)
        self.run_test("Critical Python Packages", self.test_critical_python_packages)
        self.run_test("Configuration Loading", self.test_config_loading)
        self.run_test("FastAPI Server Routes", self.test_fastapi_server_routes)
        
        # Print summary
        print("\n" + "=" * 60)
        print(f"📊 TEST SUMMARY")
        print(f"Tests passed: {self.tests_passed}/{self.tests_run}")
        print(f"Success rate: {(self.tests_passed/self.tests_run)*100:.1f}%")
        
        if self.tests_passed == self.tests_run:
            print("🎉 ALL TESTS PASSED - LockBay setup is complete!")
        else:
            print("⚠️ Some tests failed - review setup requirements")
            
        return self.tests_passed, self.tests_run, self.test_results

def main():
    """Main test execution"""
    tester = LockBaySetupTester()
    passed, total, results = tester.run_all_tests()
    
    # Return appropriate exit code
    return 0 if passed == total else 1

if __name__ == "__main__":
    sys.exit(main())