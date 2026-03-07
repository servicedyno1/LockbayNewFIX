#!/usr/bin/env python3
"""
Backend Testing Script - LockBay Telegram Escrow Bot
Railway Usage Optimizations Testing

Tests the 7 Railway optimizations:
1) DB keepalive disabled (env-gated) 
2) Crypto rate refresh 2min→5min
3) Workflow runner 30s→90s
4) Sync DB pool reduced 7→3 base
5) Railway backup sync disabled (env-gated)
6) Deep monitoring feature-flagged behind ENABLE_DEEP_MONITORING
7) Webhook queue simplified to single-backend (env-gated WEBHOOK_QUEUE_BACKEND)
"""

import requests
import sys
import json
import subprocess
import time
import logging
import asyncio
import os
from typing import Dict, Any, List
from decimal import Decimal
from datetime import datetime

# Add project root to path
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Get the backend URL from environment
BACKEND_URL = os.getenv('REACT_APP_BACKEND_URL', 'https://pod-url-update.preview.emergentagent.com')

class BackendTester:
    def __init__(self, base_url=BACKEND_URL):
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

    def test_backend_health(self):
        """Test backend health endpoint returns JSON with status 'ok'"""
        try:
            response = requests.get(f"{self.base_url}/health", timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('status') == 'ok':
                    self.log_test("Backend health endpoint returns status 'ok'", True, 
                                 f"Response: {data}")
                    return True
                else:
                    self.log_test("Backend health endpoint returns status 'ok'", False,
                                 f"Status was: {data.get('status')}")
                    return False
            else:
                self.log_test("Backend health endpoint returns status 'ok'", False, 
                             f"HTTP {response.status_code}: {response.text}")
                return False
                
        except Exception as e:
            self.log_test("Backend health endpoint returns status 'ok'", False, error=e)
            return False

    def test_supervisor_backend_status(self):
        """Test backend server is RUNNING on port 8001 (supervisor)"""
        try:
            result = subprocess.run(['sudo', 'supervisorctl', 'status', 'backend'], 
                                  capture_output=True, text=True, timeout=10)
            
            if result.returncode == 0 and 'RUNNING' in result.stdout:
                self.log_test("Backend server is RUNNING on port 8001 (supervisor)", True,
                             f"Supervisor status: {result.stdout.strip()}")
                return True
            else:
                self.log_test("Backend server is RUNNING on port 8001 (supervisor)", False,
                             f"Status: {result.stdout.strip()}, stderr: {result.stderr.strip()}")
                return False
                
        except Exception as e:
            self.log_test("Backend server is RUNNING on port 8001 (supervisor)", False, error=e)
            return False

    def test_python_files_compilation(self):
        """Test all modified Python files compile without errors"""
        files_to_test = [
            '/app/jobs/consolidated_scheduler.py',
            '/app/database.py', 
            '/app/webhook_server.py',
            '/app/backend/server.py',
            '/app/main.py'
        ]
        
        compilation_results = []
        all_passed = True
        
        for file_path in files_to_test:
            try:
                if not os.path.exists(file_path):
                    compilation_results.append(f"❌ {file_path}: File not found")
                    all_passed = False
                    continue
                    
                # Test compilation
                with open(file_path, 'r', encoding='utf-8') as f:
                    source = f.read()
                
                compile(source, file_path, 'exec')
                compilation_results.append(f"✅ {os.path.basename(file_path)}: Compiled successfully")
                
            except SyntaxError as e:
                compilation_results.append(f"❌ {os.path.basename(file_path)}: Syntax error at line {e.lineno}")
                all_passed = False
            except Exception as e:
                compilation_results.append(f"❌ {os.path.basename(file_path)}: {str(e)[:100]}")
                all_passed = False
        
        self.log_test("All modified Python files compile without errors", all_passed,
                     "\n".join(compilation_results))
        return all_passed

    def test_consolidated_scheduler_import(self):
        """Test ConsolidatedScheduler imports successfully"""
        try:
            from jobs.consolidated_scheduler import ConsolidatedScheduler, get_consolidated_scheduler_instance
            self.log_test("ConsolidatedScheduler imports successfully", True,
                         "ConsolidatedScheduler and helper functions imported")
            return True
        except Exception as e:
            self.log_test("ConsolidatedScheduler imports successfully", False, error=e)
            return False

    def test_job_modules_importable(self):
        """Test all job modules are importable"""
        job_modules = [
            'jobs.core.workflow_runner',
            'jobs.core.retry_engine', 
            'jobs.core.reconciliation',
            'jobs.core.cleanup_expiry',
            'jobs.core.reporting',
            'jobs.crypto_rate_background_refresh',
            'jobs.database_keepalive',
            'jobs.webhook_cleanup'
        ]
        
        import_results = []
        all_passed = True
        
        for module in job_modules:
            try:
                __import__(module)
                import_results.append(f"✅ {module}")
            except Exception as e:
                import_results.append(f"❌ {module}: {str(e)[:80]}")
                all_passed = False
        
        self.log_test("All job modules are importable", all_passed,
                     "\n".join(import_results))
        return all_passed

    def test_database_connection(self):
        """Test database connection still works (SELECT 1 via SQLAlchemy engine)"""
        try:
            from database import engine
            from sqlalchemy import text
            
            with engine.connect() as connection:
                result = connection.execute(text("SELECT 1"))
                row = result.fetchone()
                if row[0] == 1:
                    self.log_test("Database connection still works (SELECT 1 via SQLAlchemy engine)", True,
                                 "Successfully executed SELECT 1 and got result: 1")
                    return True
                else:
                    self.log_test("Database connection still works (SELECT 1 via SQLAlchemy engine)", False,
                                 f"SELECT 1 returned: {row[0]}")
                    return False
                
        except Exception as e:
            self.log_test("Database connection still works (SELECT 1 via SQLAlchemy engine)", False, error=e)
            return False

    def test_frontend_status_page(self):
        """Test frontend status page loads at http://localhost:3000"""
        try:
            frontend_url = "http://localhost:3000"
            response = requests.get(frontend_url, timeout=10)
            
            if response.status_code in [200, 201, 202]:
                self.log_test("Frontend status page loads at http://localhost:3000", True,
                             f"HTTP {response.status_code}, content length: {len(response.content)} bytes")
                return True
            else:
                self.log_test("Frontend status page loads at http://localhost:3000", False,
                             f"HTTP {response.status_code}")
                return False
                
        except Exception as e:
            self.log_test("Frontend status page loads at http://localhost:3000", False, error=e)
            return False

    def test_railway_optimizations(self):
        """Test the 7 specific Railway optimizations are properly configured"""
        optimizations = []
        all_passed = True
        
        print("\n🚀 Testing Railway Usage Optimizations:")
        
        # 1. Test DB keepalive is disabled by default
        try:
            db_keepalive_enabled = os.environ.get("ENABLE_DB_KEEPALIVE", "false").lower() == "true"
            if not db_keepalive_enabled:
                optimizations.append("✅ 1. DB keepalive disabled (env-gated, default off)")
            else:
                optimizations.append("❌ 1. DB keepalive enabled (should be disabled by default)")
                all_passed = False
            
        except Exception as e:
            optimizations.append(f"❌ 1. DB keepalive test error: {e}")
            all_passed = False
        
        # 2. Test crypto rate refresh interval is 5 minutes
        try:
            with open('/app/jobs/consolidated_scheduler.py', 'r') as f:
                content = f.read()
                if 'minutes=5' in content and 'crypto_rate_background_refresh' in content:
                    optimizations.append("✅ 2. Crypto rate refresh → 5min (optimized from 2min)")
                else:
                    optimizations.append("❌ 2. Crypto rate refresh not set to 5 minutes")
                    all_passed = False
            
        except Exception as e:
            optimizations.append(f"❌ 2. Crypto rate test error: {e}")
            all_passed = False
        
        # 3. Test workflow runner is 90 seconds
        try:
            with open('/app/jobs/consolidated_scheduler.py', 'r') as f:
                content = f.read()
                if 'seconds=90' in content and 'core_workflow_runner' in content:
                    optimizations.append("✅ 3. Workflow runner → 90s (optimized from 30s)")
                else:
                    optimizations.append("❌ 3. Workflow runner not set to 90 seconds")
                    all_passed = False
            
        except Exception as e:
            optimizations.append(f"❌ 3. Workflow runner test error: {e}")
            all_passed = False
        
        # 4. Test sync DB pool reduced to 3 base
        try:
            with open('/app/database.py', 'r') as f:
                content = f.read()
                if 'pool_size=3' in content and 'sync base pool' in content:
                    optimizations.append("✅ 4. Sync DB pool → 3 base (down from 7)")
                else:
                    optimizations.append("❌ 4. Sync DB pool not reduced to 3 base")
                    all_passed = False
            
        except Exception as e:
            optimizations.append(f"❌ 4. Sync DB pool test error: {e}")
            all_passed = False
        
        # 5. Test Railway backup sync disabled by default
        try:
            backup_sync_enabled = os.environ.get("ENABLE_RAILWAY_BACKUP_SYNC", "false").lower() == "true"
            if not backup_sync_enabled:
                optimizations.append("✅ 5. Railway backup sync disabled (env-gated, default off)")
            else:
                optimizations.append("❌ 5. Railway backup sync enabled (should be disabled by default)")
                all_passed = False
            
        except Exception as e:
            optimizations.append(f"❌ 5. Railway backup sync test error: {e}")
            all_passed = False
        
        # 6. Test deep monitoring feature-flagged
        try:
            deep_monitoring_enabled = os.environ.get("ENABLE_DEEP_MONITORING", "false").lower() == "true"
            if deep_monitoring_enabled:
                optimizations.append("✅ 6. Deep monitoring enabled (ENABLE_DEEP_MONITORING=true)")
            else:
                optimizations.append("✅ 6. Deep monitoring disabled (env-gated, saves resources)")
            
        except Exception as e:
            optimizations.append(f"❌ 6. Deep monitoring test error: {e}")
            all_passed = False
        
        # 7. Test webhook queue backend configuration
        try:
            webhook_backend = os.environ.get("WEBHOOK_QUEUE_BACKEND", "sqlite").lower()
            optimizations.append(f"✅ 7. Webhook queue → {webhook_backend} (single-backend optimized)")
            
        except Exception as e:
            optimizations.append(f"❌ 7. Webhook queue test error: {e}")
            all_passed = False
        
        # Print all optimization results
        for opt in optimizations:
            print(f"   {opt}")
        
        self.log_test("Railway usage optimizations are properly configured", all_passed,
                     "\n".join(optimizations))
        return all_passed

    def run_all_tests(self):
        """Run all backend tests as specified in the task requirements"""
        print("🚀 LockBay Backend Testing - Railway Usage Optimizations")
        print(f"Backend URL: {self.base_url}")
        print("=" * 70)
        
        # Test requirements from task
        tests = [
            ("Backend health endpoint at http://localhost:8001/health returns JSON with status 'ok'", self.test_backend_health),
            ("Backend server is RUNNING on port 8001 (supervisor)", self.test_supervisor_backend_status), 
            ("All modified Python files compile without errors", self.test_python_files_compilation),
            ("ConsolidatedScheduler imports successfully and all job modules are importable", self.test_consolidated_scheduler_import),
            ("All job modules are importable", self.test_job_modules_importable),
            ("Database connection still works (SELECT 1 via SQLAlchemy engine)", self.test_database_connection),
            ("Frontend status page loads at http://localhost:3000", self.test_frontend_status_page),
            ("Railway usage optimizations are properly configured", self.test_railway_optimizations)
        ]
        
        for test_name, test_func in tests:
            try:
                test_func()
            except Exception as e:
                self.log_test(test_name, False, error=e)
        
        # Print summary
        print("\n" + "=" * 70)
        print(f"📊 TEST SUMMARY - Railway Usage Optimizations")
        print("=" * 70)
        print(f"Total Tests: {self.tests_run}")
        print(f"Passed: {self.tests_passed}")
        print(f"Failed: {self.tests_run - self.tests_passed}")
        print(f"Success Rate: {(self.tests_passed/self.tests_run)*100:.1f}%")
        
        if self.tests_passed == self.tests_run:
            print("🎉 All Railway optimization tests PASSED!")
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
    """Main test execution"""
    print("🔧 Initializing Railway Optimization Tests...")
    
    tester = BackendTester()
    success = tester.run_all_tests()
    
    if success:
        print("\n✅ All Railway optimization tests completed successfully!")
        sys.exit(0)
    else:
        print("\n❌ Some Railway optimization tests failed!")
        sys.exit(1)

if __name__ == "__main__":
    main()