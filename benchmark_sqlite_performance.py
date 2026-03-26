#!/usr/bin/env python3
"""
SQLite Webhook Queue Performance Benchmark
Tests optimized vs baseline performance
"""
import asyncio
import time
import sys
from statistics import mean, median, stdev

sys.path.insert(0, '.')

async def benchmark_fast_sqlite():
    """Benchmark optimized SQLite queue"""
    from webhook_queue.webhook_inbox.fast_sqlite_webhook_queue import fast_sqlite_webhook_queue
    from webhook_queue.webhook_inbox.fast_sqlite_webhook_queue import WebhookEventPriority
    
    print("\n" + "=" * 80)
    print("⚡ OPTIMIZED SQLITE QUEUE BENCHMARK")
    print("=" * 80)
    print()
    
    # Warm up
    print("🔥 Warming up connection pool...")
    for i in range(5):
        await fast_sqlite_webhook_queue.enqueue_webhook(
            provider="warmup",
            endpoint="test",
            payload={"warmup": i},
            headers={"X-Test": "true"},
            client_ip="127.0.0.1",
            priority=WebhookEventPriority.NORMAL
        )
    print("✅ Warm-up complete\n")
    
    # Run benchmark
    print("📊 Running 50 enqueue operations...")
    times = []
    
    for i in range(50):
        success, event_id, duration_ms = await fast_sqlite_webhook_queue.enqueue_webhook(
            provider="benchmark",
            endpoint="test",
            payload={
                "test_id": i,
                "data": "x" * 100,  # Small payload
                "timestamp": time.time()
            },
            headers={
                "X-Test": "true",
                "X-Request-ID": f"bench-{i}"
            },
            client_ip="127.0.0.1",
            priority=WebhookEventPriority.NORMAL,
            metadata={"iteration": i}
        )
        
        if success:
            times.append(duration_ms)
            if i < 3:
                print(f"   #{i+1}: {duration_ms:.2f}ms")
        else:
            print(f"   ❌ #{i+1}: FAILED")
    
    if times:
        avg_time = mean(times)
        median_time = median(times)
        min_time = min(times)
        max_time = max(times)
        std_dev = stdev(times) if len(times) > 1 else 0
        
        print()
        print("=" * 80)
        print("📊 RESULTS: OPTIMIZED SQLITE")
        print("=" * 80)
        print(f"   Successful operations: {len(times)}/50")
        print(f"   Average: {avg_time:.2f}ms")
        print(f"   Median: {median_time:.2f}ms")
        print(f"   Min: {min_time:.2f}ms")
        print(f"   Max: {max_time:.2f}ms")
        print(f"   Std Dev: {std_dev:.2f}ms")
        print()
        
        # Performance analysis
        print("=" * 80)
        print("🎯 PERFORMANCE ANALYSIS")
        print("=" * 80)
        print("   Baseline (old SQLite): 35-40ms")
        print(f"   Optimized (new SQLite): {avg_time:.2f}ms")
        
        if avg_time < 20:
            improvement = ((40 - avg_time) / 40) * 100
            print(f"   ✅ TARGET MET: {improvement:.1f}% faster than baseline!")
            print(f"   🎯 Target: <20ms | Actual: {avg_time:.2f}ms")
        elif avg_time < 30:
            improvement = ((40 - avg_time) / 40) * 100
            print(f"   ✅ GOOD: {improvement:.1f}% faster than baseline")
            print(f"   ⚠️  Close to target: <20ms | Actual: {avg_time:.2f}ms")
        else:
            print(f"   ⚠️  NOT YET: Still at {avg_time:.2f}ms (target: <20ms)")
        
        print()
        
        # Consistency check
        fast_ops = sum(1 for t in times if t < 20)
        fast_rate = (fast_ops / len(times)) * 100
        
        print("📈 CONSISTENCY")
        print(f"   Operations <20ms: {fast_ops}/{len(times)} ({fast_rate:.1f}%)")
        print(f"   Operations <30ms: {sum(1 for t in times if t < 30)}/{len(times)}")
        print(f"   Operations >50ms: {sum(1 for t in times if t > 50)}/{len(times)}")
        print()
        
        # Get queue stats
        stats = fast_sqlite_webhook_queue.get_queue_stats()
        opt_status = stats.get('optimization_status', {})
        
        print("=" * 80)
        print("🔧 OPTIMIZATIONS ENABLED")
        print("=" * 80)
        print(f"   ✅ Connection Pooling: {opt_status.get('connection_pooling', False)}")
        print(f"   ✅ WAL Mode: {opt_status.get('wal_mode', False)}")
        print(f"   ✅ Optimized PRAGMAs: {opt_status.get('optimized_pragmas', False)}")
        print(f"   Target: {opt_status.get('target_enqueue_time', 'N/A')}")
        print()
        
        print("=" * 80)
        print("🏁 VERDICT")
        print("=" * 80)
        
        if avg_time < 20:
            print("   ✅ EXCELLENT: Optimized SQLite is READY for production!")
            print("   ✅ Faster than baseline (35-40ms)")
            print("   ✅ Faster than Redis cross-cloud (94ms)")
            print("   ✅ SQLite is now the BEST primary queue choice")
        elif avg_time < 30:
            print("   ✅ GOOD: Significant improvement over baseline")
            print("   ✅ Faster than Redis cross-cloud (94ms)")
            print("   ⚠️  Almost at target (<20ms)")
        else:
            print("   ⚠️  NEEDS WORK: More optimization needed")
            print(f"   Current: {avg_time:.2f}ms | Target: <20ms")
        
        print("=" * 80)
        
        return avg_time
    else:
        print("\n❌ No successful operations!")
        return None

async def main():
    """Run benchmark"""
    try:
        result = await benchmark_fast_sqlite()
        
        if result and result < 20:
            print("\n🎉 SUCCESS: Optimized SQLite queue meets performance target!")
            print(f"   Average enqueue time: {result:.2f}ms (<20ms target)")
            print("   ✅ Ready to be primary webhook queue")
        elif result and result < 30:
            print("\n👍 GOOD: Significant improvement, close to target")
            print(f"   Average enqueue time: {result:.2f}ms")
        else:
            print("\n⚠️  More optimization needed to hit <20ms target")
            
    except Exception as e:
        print(f"\n❌ Benchmark failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
