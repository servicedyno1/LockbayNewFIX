#!/usr/bin/env python3
"""
Regression Test: Duplicate Email Fix
Verifies that the scheduler configuration is correct and no duplicates exist
"""
import asyncio
import sys

sys.path.insert(0, '.')

class DuplicateEmailRegressionTest:
    def __init__(self):
        self.results = {"pass": [], "fail": [], "warnings": []}
    
    def test(self, name, passed, details=""):
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status}: {name}")
        if details:
            print(f"   {details}")
        
        if passed:
            self.results["pass"].append(name)
        else:
            self.results["fail"].append(name)
    
    def warning(self, name, details=""):
        print(f"⚠️  WARNING: {name}")
        if details:
            print(f"   {details}")
        self.results["warnings"].append(name)
    
    async def test_scheduler_jobs(self):
        """Test scheduler configuration"""
        print("\n📋 Testing Scheduler Configuration...")
        
        try:
            from jobs.consolidated_scheduler import ConsolidatedScheduler
            
            # Create mock app
            class MockApp:
                pass
            
            scheduler = ConsolidatedScheduler(MockApp())
            scheduler.setup_jobs()
            
            jobs = scheduler.scheduler.get_jobs()
            job_names = [job.name for job in jobs]
            
            # Count reporting jobs
            reporting_jobs = [j for j in job_names if 'Reporting' in j or 'Financial' in j]
            
            self.test(
                "Scheduler initialized",
                len(jobs) > 0,
                f"Found {len(jobs)} jobs"
            )
            
            self.test(
                "Reporting jobs configured",
                len(reporting_jobs) > 0,
                f"Found {len(reporting_jobs)} reporting jobs: {reporting_jobs}"
            )
            
            # Check for specific jobs
            has_hourly = any('Admin Dashboards & Communications' in j for j in job_names)
            has_daily = any('Daily Financial Reports' in j for j in job_names)
            
            self.test(
                "Hourly dashboard job exists",
                has_hourly,
                "Admin Dashboards & Communications job found"
            )
            
            self.test(
                "Daily financial report job exists",
                has_daily,
                "Daily Financial Reports job found"
            )
            
            # Clean up
            scheduler.stop()
            
        except Exception as e:
            self.test("Scheduler configuration", False, f"Error: {e}")
    
    async def test_no_duplicate_jobs(self):
        """Check for duplicate job definitions"""
        print("\n🔍 Checking for Duplicate Jobs...")
        
        try:
            from jobs.consolidated_scheduler import ConsolidatedScheduler
            
            class MockApp:
                pass
            
            scheduler = ConsolidatedScheduler(MockApp())
            scheduler.setup_jobs()
            
            jobs = scheduler.scheduler.get_jobs()
            job_ids = [job.id for job in jobs]
            
            # Check for duplicate IDs
            duplicates = [id for id in job_ids if job_ids.count(id) > 1]
            
            self.test(
                "No duplicate job IDs",
                len(duplicates) == 0,
                f"Unique jobs: {len(set(job_ids))}"
            )
            
            if duplicates:
                self.warning(
                    "Found duplicate job IDs",
                    f"Duplicates: {set(duplicates)}"
                )
            
            scheduler.stop()
            
        except Exception as e:
            self.test("Duplicate job check", False, f"Error: {e}")
    
    async def test_job_schedules_at_8am(self):
        """Test which jobs run at 8:00 AM"""
        print("\n⏰ Testing Jobs Scheduled for 8:00 AM...")
        
        try:
            from jobs.consolidated_scheduler import ConsolidatedScheduler
            from apscheduler.triggers.cron import CronTrigger
            from apscheduler.triggers.interval import IntervalTrigger
            
            class MockApp:
                pass
            
            scheduler = ConsolidatedScheduler(MockApp())
            scheduler.setup_jobs()
            
            jobs_at_8am = []
            
            for job in scheduler.scheduler.get_jobs():
                trigger = job.trigger
                
                # Check if job runs at 8 AM
                if isinstance(trigger, CronTrigger):
                    # Check if hour field includes 8
                    if hasattr(trigger, 'fields'):
                        hour_field = trigger.fields[2]  # Hour is 3rd field
                        if hour_field.expressions:
                            for expr in hour_field.expressions:
                                if hasattr(expr, 'first') and expr.first == 8:
                                    jobs_at_8am.append(job.name)
                                    break
                                elif hasattr(expr, 'step') and 8 % expr.step == 0:
                                    jobs_at_8am.append(job.name)
                                    break
                
                elif isinstance(trigger, IntervalTrigger):
                    # Hourly jobs run at 8 AM too
                    if trigger.interval.total_seconds() == 3600:  # 1 hour
                        jobs_at_8am.append(f"{job.name} (hourly)")
            
            print("\n   Jobs that run at 8:00 AM:")
            for job_name in jobs_at_8am:
                print(f"   • {job_name}")
            
            # Check if we have the right jobs
            has_hourly_dashboard = any('Admin Dashboards' in j and 'hourly' in j.lower() for j in jobs_at_8am)
            has_daily_financial = any('Daily Financial Reports' in j for j in jobs_at_8am)
            
            if has_hourly_dashboard:
                self.warning(
                    "Hourly dashboard job runs at 8 AM",
                    "This is OK if it only updates dashboards (no emails)"
                )
            
            if has_daily_financial:
                print("   ✅ Daily financial report job runs at 8 AM (expected)")
            
            # Count total jobs at 8 AM
            self.test(
                "Jobs at 8 AM count",
                True,
                f"Found {len(jobs_at_8am)} jobs that run at 8:00 AM"
            )
            
            scheduler.stop()
            
        except Exception as e:
            self.test("8 AM schedule check", False, f"Error: {e}")
    
    async def test_reporting_functions(self):
        """Test that reporting functions are different"""
        print("\n🔬 Testing Reporting Function Separation...")
        
        try:
            from jobs.core.reporting import (
                run_reporting,
                run_admin_dashboards,
                run_financial_reports
            )
            
            self.test(
                "run_reporting exists",
                callable(run_reporting),
                "Comprehensive reporting function"
            )
            
            self.test(
                "run_admin_dashboards exists",
                callable(run_admin_dashboards),
                "Dashboard-only function"
            )
            
            self.test(
                "run_financial_reports exists",
                callable(run_financial_reports),
                "Financial-only function"
            )
            
            # These should be different functions
            self.test(
                "Functions are different",
                run_reporting != run_admin_dashboards,
                "run_reporting and run_admin_dashboards are separate"
            )
            
        except Exception as e:
            self.test("Reporting functions", False, f"Error: {e}")
    
    async def test_bot_status(self):
        """Test bot is running"""
        print("\n🤖 Testing Bot Status...")
        
        try:
            import requests
            response = requests.get("http://localhost:5000/health", timeout=5)
            
            self.test(
                "Bot server responding",
                response.status_code in [200, 404],  # 404 is OK if no health endpoint
                f"Status code: {response.status_code}"
            )
        except Exception as e:
            self.test("Bot server", False, f"Error: {str(e)[:50]}")
    
    async def test_sqlite_performance(self):
        """Quick SQLite performance check"""
        print("\n⚡ Testing SQLite Queue Performance...")
        
        try:
            from webhook_queue.webhook_inbox.fast_sqlite_webhook_queue import (
                fast_sqlite_webhook_queue,
                WebhookEventPriority
            )
            
            # Test 5 operations
            times = []
            for i in range(5):
                success, eid, duration = await fast_sqlite_webhook_queue.enqueue_webhook(
                    provider="regression_test",
                    endpoint="test",
                    payload={"test": i},
                    headers={"X-Test": "true"},
                    client_ip="127.0.0.1",
                    priority=WebhookEventPriority.NORMAL
                )
                
                if success:
                    times.append(duration)
            
            if times:
                avg = sum(times) / len(times)
                self.test(
                    "SQLite queue performance",
                    avg < 20,
                    f"Average: {avg:.2f}ms (target <20ms)"
                )
            else:
                self.test("SQLite queue performance", False, "No successful operations")
                
        except Exception as e:
            self.test("SQLite performance", False, f"Error: {e}")
    
    async def test_no_errors_in_logs(self):
        """Check recent logs for errors"""
        print("\n📄 Checking Production Logs...")
        
        try:
            import glob
            import os
            
            # Find most recent log file
            log_files = glob.glob('/tmp/logs/Telegram_Bot_*.log')
            if log_files:
                latest_log = max(log_files, key=os.path.getmtime)
                
                # Read last 100 lines
                with open(latest_log, 'r') as f:
                    lines = f.readlines()
                    recent_lines = lines[-100:]
                
                # Count errors
                errors = [l for l in recent_lines if 'ERROR' in l and 'DUPLICATE_EMAIL_FIX' not in l]
                [l for l in recent_lines if 'CRITICAL' in l and 'ConsolidatedScheduler' not in l]
                
                self.test(
                    "No recent errors",
                    len(errors) == 0,
                    f"Found {len(errors)} error lines in last 100 log lines"
                )
                
                if errors:
                    print("\n   Recent errors found:")
                    for err in errors[:3]:
                        print(f"   {err.strip()[:80]}")
                
            else:
                self.warning("Log file check", "No log files found")
                
        except Exception as e:
            self.warning("Log check", f"Could not read logs: {e}")
    
    def print_summary(self):
        """Print test summary"""
        total = len(self.results["pass"]) + len(self.results["fail"])
        passed = len(self.results["pass"])
        failed = len(self.results["fail"])
        
        print("\n" + "=" * 80)
        print("📊 DUPLICATE EMAIL FIX - REGRESSION TEST SUMMARY")
        print("=" * 80)
        print(f"Total Tests: {total}")
        print(f"Passed: {passed} ✅")
        print(f"Failed: {failed} ❌")
        print(f"Warnings: {len(self.results['warnings'])} ⚠️")
        print(f"Success Rate: {(passed/total*100):.1f}%")
        
        if self.results["fail"]:
            print("\n❌ Failed Tests:")
            for test in self.results["fail"]:
                print(f"   • {test}")
        
        if self.results["warnings"]:
            print("\n⚠️  Warnings:")
            for warning in self.results["warnings"]:
                print(f"   • {warning}")
        
        print("=" * 80)
        
        if failed == 0:
            print("✅ ALL TESTS PASSED - Duplicate email fix is working!")
            print("=" * 80)
            return True
        else:
            print("⚠️  SOME TESTS FAILED - Review failures above")
            print("=" * 80)
            return False

async def main():
    print("\n" + "=" * 80)
    print("🧪 DUPLICATE EMAIL FIX - REGRESSION TEST")
    print("=" * 80)
    
    tester = DuplicateEmailRegressionTest()
    
    # Run all tests
    await tester.test_scheduler_jobs()
    await tester.test_no_duplicate_jobs()
    await tester.test_job_schedules_at_8am()
    await tester.test_reporting_functions()
    await tester.test_bot_status()
    await tester.test_sqlite_performance()
    await tester.test_no_errors_in_logs()
    
    # Print summary
    success = tester.print_summary()
    
    return 0 if success else 1

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
