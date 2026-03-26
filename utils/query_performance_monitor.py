"""
Query Performance Monitoring System
Tracks slow queries and provides optimization insights
"""

import time
import logging
from typing import Dict, Optional, Any
from functools import wraps
from datetime import datetime
import threading
from collections import defaultdict, deque

logger = logging.getLogger(__name__)


class QueryPerformanceMonitor:
    """Monitor database query performance and detect slow queries"""

    def __init__(self):
        self.query_times = defaultdict(deque)  # Store recent query times
        self.slow_query_threshold = 0.15  # Queries > 150ms are considered slow (REALISTIC for remote DB)
        self.alert_threshold = 0.5       # Queries > 500ms trigger alerts (REALISTIC threshold)
        self.max_history = 100           # Keep last 100 queries per type
        self.lock = threading.Lock()
        
    def time_query(self, query_type: str = "unknown"):
        """Decorator to time database queries"""
        def decorator(func):
            @wraps(func)
            def wrapper(*args, **kwargs):
                start_time = time.time()
                try:
                    result = func(*args, **kwargs)
                    return result
                finally:
                    elapsed = time.time() - start_time
                    self._record_query_time(query_type, elapsed)
            return wrapper
        return decorator

    def _record_query_time(self, query_type: str, elapsed_time: float, context: Optional[Dict] = None):
        """Record query execution time with optional context"""
        with self.lock:
            # Add to history (keep only recent ones)
            query_record = {
                'time': elapsed_time,
                'timestamp': datetime.utcnow(),
                'context': context or {}
            }
            self.query_times[query_type].append(query_record)
            
            # Limit history size
            if len(self.query_times[query_type]) > self.max_history:
                self.query_times[query_type].popleft()
            
            # Enhanced logging with context
            context_str = ""
            if context:
                context_items = [f"{k}={v}" for k, v in context.items()]
                context_str = f" ({', '.join(context_items)})"
            
            # Log slow queries
            if elapsed_time > self.slow_query_threshold:
                if elapsed_time > self.alert_threshold:
                    logger.error(f"🚨 CRITICAL SLOW QUERY: {query_type} took {elapsed_time:.3f}s{context_str}")
                    # Add troubleshooting suggestions
                    self._log_troubleshooting_suggestions(query_type, elapsed_time)
                else:
                    logger.warning(f"⚠️ SLOW QUERY: {query_type} took {elapsed_time:.3f}s{context_str}")
            else:
                logger.debug(f"✅ Query {query_type}: {elapsed_time:.3f}s{context_str}")
                
    def _log_troubleshooting_suggestions(self, query_type: str, elapsed_time: float):
        """Log troubleshooting suggestions for slow queries"""
        suggestions = []
        
        if "user_lookup" in query_type.lower():
            suggestions.extend([
                "Check if telegram_id index is being used",
                "Verify VACUUM ANALYZE has been run recently",
                "Check for table lock contention"
            ])
        
        if "conn_" in query_type:
            suggestions.extend([
                "Check connection pool exhaustion",
                "Verify database server connectivity",
                "Review concurrent operation load"
            ])
        
        if elapsed_time > 5.0:
            suggestions.append("Consider database server resource monitoring")
            
        if suggestions:
            logger.error("💡 Troubleshooting suggestions:")
            for suggestion in suggestions:
                logger.error(f"   • {suggestion}")

    def get_query_stats(self, query_type: Optional[str] = None) -> Dict[str, Any]:
        """Get performance statistics for queries"""
        with self.lock:
            if query_type:
                return self._get_stats_for_type(query_type)
            
            stats = {}
            for qtype in self.query_times:
                stats[qtype] = self._get_stats_for_type(qtype)
            return stats

    def _get_stats_for_type(self, query_type: str) -> Dict[str, Any]:
        """Get statistics for a specific query type"""
        times = [q['time'] for q in self.query_times[query_type]]
        if not times:
            return {"count": 0, "avg": 0, "max": 0, "min": 0, "slow_count": 0}
        
        slow_count = sum(1 for t in times if t > self.slow_query_threshold)
        
        return {
            "count": len(times),
            "avg": round(sum(times) / len(times), 3),
            "max": round(max(times), 3),
            "min": round(min(times), 3),
            "slow_count": slow_count,
            "slow_percentage": round((slow_count / len(times)) * 100, 1)
        }

    def check_performance_health(self) -> Dict[str, Any]:
        """Check overall query performance health"""
        with self.lock:
            total_queries = sum(len(times) for times in self.query_times.values())
            slow_queries = 0
            critical_queries = 0
            
            for query_type, times in self.query_times.items():
                for query_data in times:
                    if query_data['time'] > self.alert_threshold:
                        critical_queries += 1
                    elif query_data['time'] > self.slow_query_threshold:
                        slow_queries += 1
            
            health_status = "healthy"
            if critical_queries > 0:
                health_status = "critical"
            elif slow_queries > total_queries * 0.1:  # > 10% slow queries
                health_status = "warning"
            
            return {
                "status": health_status,
                "total_queries": total_queries,
                "slow_queries": slow_queries,
                "critical_queries": critical_queries,
                "query_types": list(self.query_times.keys())
            }


# Global instance
query_monitor = QueryPerformanceMonitor()


def time_database_query(query_type: str = "unknown"):
    """Decorator to time database queries"""
    return query_monitor.time_query(query_type)


# Context manager for manual timing
class QueryTimer:
    """Context manager for timing queries manually"""
    
    def __init__(self, query_type: str):
        self.query_type = query_type
        self.start_time = None
        
    def __enter__(self):
        self.start_time = time.time()
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.start_time:
            elapsed = time.time() - self.start_time
            query_monitor._record_query_time(self.query_type, elapsed)


# Connection acquisition timer
class ConnectionTimer:
    """Context manager for timing database connection acquisition"""
    
    def __init__(self, operation_name: str = "connection_acquisition"):
        self.operation_name = operation_name
        self.start_time = None
        
    def __enter__(self):
        self.start_time = time.time()
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.start_time:
            elapsed = time.time() - self.start_time
            query_monitor._record_query_time(f"conn_{self.operation_name}", elapsed)
            
            # Log connection timing separately
            if elapsed > 0.5:  # > 500ms for connection is concerning
                logger.warning(f"🐌 Slow connection acquisition for {self.operation_name}: {elapsed:.3f}s")
            else:
                logger.debug(f"⚡ Connection acquired for {self.operation_name}: {elapsed:.3f}s")