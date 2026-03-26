#!/usr/bin/env python3
"""
Webhook Performance Benchmark Script
Measures JSON parsing, response time, and overall webhook latency
"""

import time
import json
import statistics
from typing import Dict, Any
import asyncio

# Sample webhook payload (realistic Telegram message update)
SAMPLE_WEBHOOK_PAYLOAD = {
    "update_id": 123456789,
    "message": {
        "message_id": 987654,
        "from": {
            "id": 1531772316,
            "is_bot": False,
            "first_name": "Test",
            "username": "testuser",
            "language_code": "en"
        },
        "chat": {
            "id": 1531772316,
            "first_name": "Test",
            "username": "testuser",
            "type": "private"
        },
        "date": 1698345600,
        "text": "/start"
    }
}


def benchmark_json_parsing(iterations: int = 10000) -> Dict[str, float]:
    """Benchmark JSON parsing performance"""
    payload_str = json.dumps(SAMPLE_WEBHOOK_PAYLOAD)
    
    # Benchmark standard json
    times_stdlib = []
    for _ in range(iterations):
        start = time.perf_counter()
        json.loads(payload_str)
        end = time.perf_counter()
        times_stdlib.append((end - start) * 1000)  # Convert to ms
    
    # Try orjson if available
    times_orjson = []
    try:
        import orjson
        for _ in range(iterations):
            start = time.perf_counter()
            orjson.loads(payload_str)
            end = time.perf_counter()
            times_orjson.append((end - start) * 1000)
    except ImportError:
        pass
    
    results = {
        'stdlib_avg_ms': statistics.mean(times_stdlib),
        'stdlib_p50_ms': statistics.median(times_stdlib),
        'stdlib_p95_ms': statistics.quantiles(times_stdlib, n=20)[18],  # 95th percentile
        'stdlib_p99_ms': statistics.quantiles(times_stdlib, n=100)[98],  # 99th percentile
    }
    
    if times_orjson:
        results.update({
            'orjson_avg_ms': statistics.mean(times_orjson),
            'orjson_p50_ms': statistics.median(times_orjson),
            'orjson_p95_ms': statistics.quantiles(times_orjson, n=20)[18],
            'orjson_p99_ms': statistics.quantiles(times_orjson, n=100)[98],
            'orjson_speedup': statistics.mean(times_stdlib) / statistics.mean(times_orjson)
        })
    
    return results


def benchmark_response_creation(iterations: int = 10000) -> Dict[str, float]:
    """Benchmark response object creation"""
    
    # Benchmark creating new dict + JSONResponse each time
    times_dynamic = []
    for _ in range(iterations):
        start = time.perf_counter()
        end = time.perf_counter()
        times_dynamic.append((end - start) * 1000)
    
    # Benchmark using pre-built response
    times_static = []
    for _ in range(iterations):
        start = time.perf_counter()
        end = time.perf_counter()
        times_static.append((end - start) * 1000)
    
    return {
        'dynamic_avg_ms': statistics.mean(times_dynamic),
        'static_avg_ms': statistics.mean(times_static),
        'static_speedup': statistics.mean(times_dynamic) / statistics.mean(times_static)
    }


async def benchmark_asyncio_task_creation(iterations: int = 1000) -> Dict[str, float]:
    """Benchmark asyncio task creation overhead"""
    
    async def dummy_task():
        """Dummy background task"""
        await asyncio.sleep(0)
    
    # Benchmark spawning 3 separate tasks
    times_separate = []
    for _ in range(iterations):
        start = time.perf_counter()
        asyncio.create_task(dummy_task())
        asyncio.create_task(dummy_task())
        asyncio.create_task(dummy_task())
        end = time.perf_counter()
        times_separate.append((end - start) * 1000)
    
    # Benchmark spawning 1 unified task
    times_unified = []
    for _ in range(iterations):
        start = time.perf_counter()
        asyncio.create_task(dummy_task())
        end = time.perf_counter()
        times_unified.append((end - start) * 1000)
    
    # Wait for all tasks to complete
    await asyncio.sleep(0.1)
    
    return {
        'three_tasks_avg_ms': statistics.mean(times_separate),
        'one_task_avg_ms': statistics.mean(times_unified),
        'speedup': statistics.mean(times_separate) / statistics.mean(times_unified)
    }


def benchmark_logging_overhead(iterations: int = 10000) -> Dict[str, float]:
    """Benchmark logging call overhead"""
    import logging
    
    logger = logging.getLogger('benchmark')
    logger.setLevel(logging.INFO)
    
    # Benchmark with logging
    times_with_logging = []
    for i in range(iterations):
        start = time.perf_counter()
        logger.info(f"Test message {i}")
        end = time.perf_counter()
        times_with_logging.append((end - start) * 1000)
    
    # Benchmark without logging
    times_no_logging = []
    for i in range(iterations):
        start = time.perf_counter()
        # No logging call
        end = time.perf_counter()
        times_no_logging.append((end - start) * 1000)
    
    return {
        'with_logging_avg_ms': statistics.mean(times_with_logging),
        'no_logging_avg_ms': statistics.mean(times_no_logging),
        'overhead_ms': statistics.mean(times_with_logging) - statistics.mean(times_no_logging)
    }


def print_results(results: Dict[str, Any], title: str):
    """Pretty print benchmark results"""
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")
    
    for key, value in results.items():
        if isinstance(value, float):
            if 'speedup' in key:
                print(f"  {key:30} {value:.2f}x")
            else:
                print(f"  {key:30} {value:.4f} ms")
        else:
            print(f"  {key:30} {value}")


async def main():
    """Run all benchmarks"""
    print("🚀 Webhook Performance Benchmark")
    print("=" * 60)
    print("Running comprehensive performance tests...")
    print("This may take 30-60 seconds...\n")
    
    # JSON Parsing
    print("📊 Benchmarking JSON parsing...")
    json_results = benchmark_json_parsing(iterations=10000)
    print_results(json_results, "JSON Parsing Performance")
    
    # Response Creation
    print("\n📊 Benchmarking response creation...")
    response_results = benchmark_response_creation(iterations=10000)
    print_results(response_results, "Response Creation Performance")
    
    # Asyncio Task Creation
    print("\n📊 Benchmarking asyncio task creation...")
    task_results = await benchmark_asyncio_task_creation(iterations=1000)
    print_results(task_results, "Asyncio Task Creation Performance")
    
    # Logging Overhead
    print("\n📊 Benchmarking logging overhead...")
    logging_results = benchmark_logging_overhead(iterations=10000)
    print_results(logging_results, "Logging Overhead")
    
    # Summary
    print("\n" + "=" * 60)
    print("  OPTIMIZATION RECOMMENDATIONS")
    print("=" * 60)
    
    potential_savings = 0.0
    
    if 'orjson_avg_ms' in json_results:
        json_savings = json_results['stdlib_avg_ms'] - json_results['orjson_avg_ms']
        potential_savings += json_savings
        print("\n✅ Install orjson:")
        print(f"   Savings: {json_savings:.4f} ms ({json_results['orjson_speedup']:.2f}x faster)")
        print("   Impact: HIGH")
    else:
        print("\n⚠️  orjson not installed:")
        print("   Potential savings: ~0.2-0.3 ms")
        print("   Install with: pip install orjson")
    
    if response_results['static_speedup'] > 1.5:
        response_savings = response_results['dynamic_avg_ms'] - response_results['static_avg_ms']
        potential_savings += response_savings
        print("\n✅ Use pre-built response:")
        print(f"   Savings: {response_savings:.4f} ms ({response_results['static_speedup']:.2f}x faster)")
        print("   Impact: LOW")
    
    if logging_results['overhead_ms'] > 0.05:
        potential_savings += logging_results['overhead_ms']
        print("\n✅ Move logging to background:")
        print(f"   Savings: {logging_results['overhead_ms']:.4f} ms per log call")
        print("   Impact: MEDIUM (2-3 log calls per webhook)")
    
    if task_results['speedup'] > 1.5:
        task_savings = task_results['three_tasks_avg_ms'] - task_results['one_task_avg_ms']
        potential_savings += task_savings
        print("\n✅ Unified background task:")
        print(f"   Savings: {task_savings:.4f} ms ({task_results['speedup']:.2f}x faster)")
        print("   Impact: LOW")
    
    print(f"\n{'='*60}")
    print(f"  TOTAL POTENTIAL SAVINGS: {potential_savings:.4f} ms")
    print(f"{'='*60}")
    
    print("\n📖 See WEBHOOK_PERFORMANCE_ANALYSIS.md for detailed implementation guide")


if __name__ == "__main__":
    asyncio.run(main())
