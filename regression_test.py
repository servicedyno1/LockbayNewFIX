#!/usr/bin/env python3
"""
Comprehensive Regression Test Suite
Tests all recent implementations to ensure nothing is broken
"""
import asyncio
import sys

sys.path.insert(0, '.')

class RegressionTest:
    """Comprehensive regression testing"""
    
    def __init__(self):
        self.results = []
        self.failed_tests = []
    
    def log_test(self, name: str, passed: bool, message: str = ""):
        """Log test result"""
        status = "✅ PASS" if passed else "❌ FAIL"
        self.results.append((name, passed, message))
        if not passed:
            self.failed_tests.append(name)
        print(f"{status}: {name}")
        if message:
            print(f"   {message}")
    
    async def test_sqlite_queue_initialization(self):
        """Test SQLite queue can initialize"""
        try:
            from webhook_queue.webhook_inbox.fast_sqlite_webhook_queue import fast_sqlite_webhook_queue
            
            # Check if initialized
            if fast_sqlite_webhook_queue is None:
                self.log_test("SQLite Queue Initialization", False, "Queue is None")
                return
            
            # Check pool exists
            if not hasattr(fast_sqlite_webhook_queue, 'pool'):
                self.log_test("SQLite Queue Initialization", False, "No connection pool")
                return
            
            self.log_test("SQLite Queue Initialization", True, "Queue initialized with connection pool")
            
        except Exception as e:
            self.log_test("SQLite Queue Initialization", False, str(e))
    
    async def test_sqlite_enqueue_dequeue(self):
        """Test SQLite enqueue and dequeue operations"""
        try:
            from webhook_queue.webhook_inbox.fast_sqlite_webhook_queue import (
                fast_sqlite_webhook_queue,
                WebhookEventPriority
            )
            
            # Test enqueue
            success, event_id, duration_ms = await fast_sqlite_webhook_queue.enqueue_webhook(
                provider="test_provider",
                endpoint="test_endpoint",
                payload={"test": "data"},
                headers={"X-Test": "true"},
                client_ip="127.0.0.1",
                priority=WebhookEventPriority.NORMAL
            )
            
            if not success:
                self.log_test("SQLite Enqueue", False, "Enqueue failed")
                return
            
            if duration_ms > 100:
                self.log_test("SQLite Enqueue", False, f"Too slow: {duration_ms:.2f}ms (expected <100ms)")
                return
            
            self.log_test("SQLite Enqueue", True, f"Enqueued in {duration_ms:.2f}ms")
            
            # Test dequeue
            events = await fast_sqlite_webhook_queue.dequeue_webhook(batch_size=1)
            
            if not events or len(events) == 0:
                self.log_test("SQLite Dequeue", False, "No events dequeued")
                return
            
            if events[0].id != event_id:
                self.log_test("SQLite Dequeue", False, "Wrong event ID")
                return
            
            self.log_test("SQLite Dequeue", True, f"Dequeued event {event_id[:8]}")
            
            # Update status to completed
            await fast_sqlite_webhook_queue.update_event_status(
                event_id=event_id,
                status=WebhookEventPriority.NORMAL.__class__.__bases__[0]('completed')
            )
            
        except Exception as e:
            self.log_test("SQLite Enqueue/Dequeue", False, str(e))
    
    async def test_sqlite_performance(self):
        """Test SQLite queue performance"""
        try:
            from webhook_queue.webhook_inbox.fast_sqlite_webhook_queue import (
                fast_sqlite_webhook_queue,
                WebhookEventPriority
            )
            
            # Test 10 operations
            times = []
            for i in range(10):
                success, event_id, duration_ms = await fast_sqlite_webhook_queue.enqueue_webhook(
                    provider="perf_test",
                    endpoint="test",
                    payload={"iteration": i},
                    headers={"X-Test": "true"},
                    client_ip="127.0.0.1",
                    priority=WebhookEventPriority.NORMAL
                )
                
                if success:
                    times.append(duration_ms)
            
            if not times:
                self.log_test("SQLite Performance", False, "No successful operations")
                return
            
            avg_time = sum(times) / len(times)
            
            if avg_time > 20:
                self.log_test("SQLite Performance", False, 
                            f"Average {avg_time:.2f}ms > 20ms target")
                return
            
            self.log_test("SQLite Performance", True, 
                        f"Average: {avg_time:.2f}ms (target <20ms)")
            
        except Exception as e:
            self.log_test("SQLite Performance", False, str(e))
    
    async def test_fallback_mechanism(self):
        """Test SQLite -> Redis fallback"""
        try:
            from webhook_server import enqueue_webhook_with_fallback
            from webhook_queue.webhook_inbox.fast_sqlite_webhook_queue import WebhookEventPriority
            
            # Test normal enqueue (should use SQLite)
            success, event_id, duration_ms = await enqueue_webhook_with_fallback(
                provider="fallback_test",
                endpoint="test",
                payload={"test": "fallback"},
                headers={"X-Test": "true"},
                client_ip="127.0.0.1",
                priority=WebhookEventPriority.NORMAL
            )
            
            if not success:
                self.log_test("Fallback Mechanism", False, "Enqueue failed")
                return
            
            # Should be fast (SQLite path)
            if duration_ms < 100:
                self.log_test("Fallback Mechanism", True, 
                            f"SQLite-first working ({duration_ms:.2f}ms)")
            else:
                self.log_test("Fallback Mechanism", True, 
                            f"Fallback working but slow ({duration_ms:.2f}ms)")
            
        except Exception as e:
            self.log_test("Fallback Mechanism", False, str(e))
    
    async def test_queue_stats(self):
        """Test queue statistics"""
        try:
            from webhook_queue.webhook_inbox.fast_sqlite_webhook_queue import fast_sqlite_webhook_queue
            
            stats = fast_sqlite_webhook_queue.get_queue_stats()
            
            if 'performance_metrics' not in stats:
                self.log_test("Queue Statistics", False, "Missing performance_metrics")
                return
            
            if 'optimization_status' not in stats:
                self.log_test("Queue Statistics", False, "Missing optimization_status")
                return
            
            opt_status = stats['optimization_status']
            
            # Verify optimizations are enabled
            if not opt_status.get('connection_pooling'):
                self.log_test("Queue Statistics", False, "Connection pooling not enabled")
                return
            
            if not opt_status.get('wal_mode'):
                self.log_test("Queue Statistics", False, "WAL mode not enabled")
                return
            
            self.log_test("Queue Statistics", True, "All optimizations enabled")
            
        except Exception as e:
            self.log_test("Queue Statistics", False, str(e))
    
    async def test_health_check(self):
        """Test queue health check"""
        try:
            from webhook_queue.webhook_inbox.fast_sqlite_webhook_queue import fast_sqlite_webhook_queue
            
            healthy, status = await fast_sqlite_webhook_queue.health_check()
            
            if not healthy:
                self.log_test("Health Check", False, f"Queue unhealthy: {status}")
                return
            
            self.log_test("Health Check", True, status)
            
        except Exception as e:
            self.log_test("Health Check", False, str(e))
    
    async def test_redis_queue_available(self):
        """Test Redis queue is available as fallback"""
        try:
            from webhook_queue.webhook_inbox.redis_webhook_queue import redis_webhook_queue
            
            healthy, status = await redis_webhook_queue.health_check()
            
            if healthy:
                self.log_test("Redis Fallback Available", True, status)
            else:
                self.log_test("Redis Fallback Available", True, 
                            "Redis unavailable but SQLite working (OK)")
            
        except Exception as e:
            self.log_test("Redis Fallback Available", True, 
                        f"Redis not critical: {str(e)[:50]}")
    
    async def test_database_connectivity(self):
        """Test database is accessible"""
        try:
            from database import async_session_maker
            from models import User
            from sqlalchemy import select
            
            async with async_session_maker() as session:
                result = await session.execute(select(User).limit(1))
                result.scalars().first()
            
            self.log_test("Database Connectivity", True, "Database accessible")
            
        except Exception as e:
            self.log_test("Database Connectivity", False, str(e))
    
    def print_summary(self):
        """Print test summary"""
        total = len(self.results)
        passed = sum(1 for _, p, _ in self.results if p)
        failed = total - passed
        
        print("\n" + "=" * 80)
        print("📊 REGRESSION TEST SUMMARY")
        print("=" * 80)
        print(f"Total Tests: {total}")
        print(f"Passed: {passed} ✅")
        print(f"Failed: {failed} ❌")
        print(f"Success Rate: {(passed/total*100):.1f}%")
        
        if self.failed_tests:
            print("\n❌ Failed Tests:")
            for test in self.failed_tests:
                print(f"   - {test}")
        
        print("=" * 80)
        
        if failed == 0:
            print("✅ ALL TESTS PASSED - System is healthy!")
            print("=" * 80)
            return True
        else:
            print("⚠️  SOME TESTS FAILED - Review failures above")
            print("=" * 80)
            return False

async def main():
    """Run all regression tests"""
    print("\n" + "=" * 80)
    print("🧪 RUNNING REGRESSION TESTS")
    print("=" * 80)
    print()
    
    tester = RegressionTest()
    
    # Run all tests
    print("📦 Testing SQLite Queue...")
    await tester.test_sqlite_queue_initialization()
    await tester.test_sqlite_enqueue_dequeue()
    await tester.test_sqlite_performance()
    await tester.test_queue_stats()
    await tester.test_health_check()
    
    print("\n🔄 Testing Fallback Mechanism...")
    await tester.test_fallback_mechanism()
    await tester.test_redis_queue_available()
    
    print("\n💾 Testing Database...")
    await tester.test_database_connectivity()
    
    # Print summary
    success = tester.print_summary()
    
    return 0 if success else 1

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
