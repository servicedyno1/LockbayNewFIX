"""
Database Performance Diagnostic Tool
Compares Railway (production) vs Neon (development) performance
"""

import os
import time
import asyncio
import logging
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine
from typing import Dict, Any

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class DatabasePerformanceDiagnostics:
    """Comprehensive database performance testing"""
    
    def __init__(self):
        self.railway_url = os.getenv("RAILWAY_DATABASE_URL")
        self.neon_url = os.getenv("DATABASE_URL")
        
        if not self.railway_url:
            raise ValueError("RAILWAY_DATABASE_URL not configured")
        if not self.neon_url:
            raise ValueError("DATABASE_URL (Neon) not configured")
    
    def test_sync_connection(self, db_url: str, db_name: str) -> Dict[str, Any]:
        """Test synchronous connection performance"""
        results = {
            "db_name": db_name,
            "connection_time_ms": 0,
            "query_time_ms": 0,
            "total_time_ms": 0,
            "success": False,
            "error": None
        }
        
        try:
            # Test connection establishment
            start = time.time()
            engine = create_engine(db_url, pool_pre_ping=True, pool_size=1)
            connection_time = (time.time() - start) * 1000
            results["connection_time_ms"] = round(connection_time, 2)
            
            # Test simple query
            start = time.time()
            with engine.connect() as conn:
                result = conn.execute(text("SELECT COUNT(*) FROM users"))
                row = result.fetchone()
                user_count = row[0] if row else 0
            query_time = (time.time() - start) * 1000
            results["query_time_ms"] = round(query_time, 2)
            results["total_time_ms"] = round(connection_time + query_time, 2)
            results["user_count"] = user_count
            results["success"] = True
            
            engine.dispose()
            
        except Exception as e:
            results["error"] = str(e)
            logger.error(f"❌ {db_name} sync test failed: {e}")
        
        return results
    
    async def test_async_connection(self, db_url: str, db_name: str) -> Dict[str, Any]:
        """Test asynchronous connection performance"""
        results = {
            "db_name": db_name,
            "connection_time_ms": 0,
            "query_time_ms": 0,
            "total_time_ms": 0,
            "success": False,
            "error": None
        }
        
        try:
            # Convert to async URL
            async_url = db_url.replace('postgresql://', 'postgresql+asyncpg://')
            async_url = async_url.replace('sslmode=require', 'ssl=require')
            async_url = async_url.replace('sslmode=prefer', 'ssl=prefer')
            
            # Test connection establishment
            start = time.time()
            engine = create_async_engine(async_url, pool_pre_ping=True, pool_size=1)
            connection_time = (time.time() - start) * 1000
            results["connection_time_ms"] = round(connection_time, 2)
            
            # Test simple query
            start = time.time()
            async with engine.connect() as conn:
                result = await conn.execute(text("SELECT COUNT(*) FROM users"))
                row = result.fetchone()
                user_count = row[0] if row else 0
            query_time = (time.time() - start) * 1000
            results["query_time_ms"] = round(query_time, 2)
            results["total_time_ms"] = round(connection_time + query_time, 2)
            results["user_count"] = user_count
            results["success"] = True
            
            await engine.dispose()
            
        except Exception as e:
            results["error"] = str(e)
            logger.error(f"❌ {db_name} async test failed: {e}")
        
        return results
    
    def test_complex_query(self, db_url: str, db_name: str) -> Dict[str, Any]:
        """Test complex query with joins"""
        results = {
            "db_name": db_name,
            "query_time_ms": 0,
            "success": False,
            "error": None
        }
        
        try:
            engine = create_engine(db_url, pool_pre_ping=True)
            
            start = time.time()
            with engine.connect() as conn:
                # Complex query similar to dispute chat
                result = conn.execute(text("""
                    SELECT 
                        u.id,
                        u.first_name,
                        COUNT(e.id) as escrow_count,
                        COUNT(d.id) as dispute_count
                    FROM users u
                    LEFT JOIN escrows e ON u.id = e.buyer_id OR u.id = e.seller_id
                    LEFT JOIN disputes d ON d.escrow_id = e.id
                    GROUP BY u.id, u.first_name
                    LIMIT 10
                """))
                rows = result.fetchall()
                row_count = len(rows)
            
            query_time = (time.time() - start) * 1000
            results["query_time_ms"] = round(query_time, 2)
            results["row_count"] = row_count
            results["success"] = True
            
            engine.dispose()
            
        except Exception as e:
            results["error"] = str(e)
            logger.error(f"❌ {db_name} complex query failed: {e}")
        
        return results
    
    def test_network_latency(self, db_url: str, db_name: str, iterations: int = 5) -> Dict[str, Any]:
        """Test network latency with multiple small queries"""
        results = {
            "db_name": db_name,
            "avg_latency_ms": 0,
            "min_latency_ms": 0,
            "max_latency_ms": 0,
            "success": False,
            "error": None
        }
        
        try:
            engine = create_engine(db_url, pool_pre_ping=True, pool_size=1)
            latencies = []
            
            for i in range(iterations):
                start = time.time()
                with engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
                latency = (time.time() - start) * 1000
                latencies.append(latency)
            
            results["avg_latency_ms"] = round(sum(latencies) / len(latencies), 2)
            results["min_latency_ms"] = round(min(latencies), 2)
            results["max_latency_ms"] = round(max(latencies), 2)
            results["success"] = True
            
            engine.dispose()
            
        except Exception as e:
            results["error"] = str(e)
            logger.error(f"❌ {db_name} latency test failed: {e}")
        
        return results
    
    def run_diagnostics(self):
        """Run all diagnostic tests"""
        print("=" * 80)
        print("🔍 DATABASE PERFORMANCE DIAGNOSTICS")
        print("=" * 80)
        print()
        
        # Test 1: Sync Connection Performance
        print("📊 Test 1: Synchronous Connection & Simple Query")
        print("-" * 80)
        railway_sync = self.test_sync_connection(self.railway_url, "Railway")
        neon_sync = self.test_sync_connection(self.neon_url, "Neon")
        
        print("Railway (Production):")
        print(f"  • Connection: {railway_sync['connection_time_ms']}ms")
        print(f"  • Query: {railway_sync['query_time_ms']}ms")
        print(f"  • Total: {railway_sync['total_time_ms']}ms")
        print(f"  • Users: {railway_sync.get('user_count', 'N/A')}")
        print()
        print("Neon (Development):")
        print(f"  • Connection: {neon_sync['connection_time_ms']}ms")
        print(f"  • Query: {neon_sync['query_time_ms']}ms")
        print(f"  • Total: {neon_sync['total_time_ms']}ms")
        print(f"  • Users: {neon_sync.get('user_count', 'N/A')}")
        print()
        
        if railway_sync['total_time_ms'] > neon_sync['total_time_ms']:
            diff = railway_sync['total_time_ms'] - neon_sync['total_time_ms']
            pct = (diff / neon_sync['total_time_ms']) * 100
            print(f"⚠️  Railway is {diff:.0f}ms ({pct:.0f}%) SLOWER than Neon")
        else:
            diff = neon_sync['total_time_ms'] - railway_sync['total_time_ms']
            pct = (diff / railway_sync['total_time_ms']) * 100
            print(f"✅ Railway is {diff:.0f}ms ({pct:.0f}%) FASTER than Neon")
        print()
        
        # Test 2: Async Connection Performance
        print("📊 Test 2: Asynchronous Connection & Query")
        print("-" * 80)
        loop = asyncio.get_event_loop()
        railway_async = loop.run_until_complete(self.test_async_connection(self.railway_url, "Railway"))
        neon_async = loop.run_until_complete(self.test_async_connection(self.neon_url, "Neon"))
        
        print("Railway (Production):")
        print(f"  • Connection: {railway_async['connection_time_ms']}ms")
        print(f"  • Query: {railway_async['query_time_ms']}ms")
        print(f"  • Total: {railway_async['total_time_ms']}ms")
        print()
        print("Neon (Development):")
        print(f"  • Connection: {neon_async['connection_time_ms']}ms")
        print(f"  • Query: {neon_async['query_time_ms']}ms")
        print(f"  • Total: {neon_async['total_time_ms']}ms")
        print()
        
        # Test 3: Complex Query Performance
        print("📊 Test 3: Complex Query with Joins")
        print("-" * 80)
        railway_complex = self.test_complex_query(self.railway_url, "Railway")
        neon_complex = self.test_complex_query(self.neon_url, "Neon")
        
        print(f"Railway (Production): {railway_complex['query_time_ms']}ms")
        print(f"Neon (Development): {neon_complex['query_time_ms']}ms")
        print()
        
        # Test 4: Network Latency
        print("📊 Test 4: Network Latency (5 iterations)")
        print("-" * 80)
        railway_latency = self.test_network_latency(self.railway_url, "Railway", 5)
        neon_latency = self.test_network_latency(self.neon_url, "Neon", 5)
        
        print("Railway (Production):")
        print(f"  • Average: {railway_latency['avg_latency_ms']}ms")
        print(f"  • Min: {railway_latency['min_latency_ms']}ms")
        print(f"  • Max: {railway_latency['max_latency_ms']}ms")
        print()
        print("Neon (Development):")
        print(f"  • Average: {neon_latency['avg_latency_ms']}ms")
        print(f"  • Min: {neon_latency['min_latency_ms']}ms")
        print(f"  • Max: {neon_latency['max_latency_ms']}ms")
        print()
        
        # Summary Analysis
        print("=" * 80)
        print("📋 PERFORMANCE ANALYSIS SUMMARY")
        print("=" * 80)
        
        # Calculate average slowdown
        total_railway = (
            railway_sync['total_time_ms'] +
            railway_async['total_time_ms'] +
            railway_complex['query_time_ms'] +
            railway_latency['avg_latency_ms']
        )
        total_neon = (
            neon_sync['total_time_ms'] +
            neon_async['total_time_ms'] +
            neon_complex['query_time_ms'] +
            neon_latency['avg_latency_ms']
        )
        
        slowdown = ((total_railway - total_neon) / total_neon) * 100
        
        print("Overall Performance:")
        print(f"  • Railway Total: {total_railway:.0f}ms")
        print(f"  • Neon Total: {total_neon:.0f}ms")
        print(f"  • Railway is {abs(slowdown):.0f}% {'SLOWER' if slowdown > 0 else 'FASTER'}")
        print()
        
        # Identify primary bottleneck
        print("Primary Bottlenecks:")
        if railway_latency['avg_latency_ms'] > neon_latency['avg_latency_ms'] * 1.5:
            print("  🔴 Network Latency: Railway database is geographically distant")
        if railway_sync['connection_time_ms'] > neon_sync['connection_time_ms'] * 1.5:
            print("  🔴 Connection Time: Railway takes longer to establish connections")
        if railway_complex['query_time_ms'] > neon_complex['query_time_ms'] * 1.5:
            print("  🔴 Query Execution: Railway has slower query processing")
        
        print()
        print("=" * 80)


if __name__ == "__main__":
    try:
        diagnostics = DatabasePerformanceDiagnostics()
        diagnostics.run_diagnostics()
    except Exception as e:
        print(f"❌ Diagnostic failed: {e}")
        import traceback
        traceback.print_exc()
