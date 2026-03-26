"""
Database Connection Pool Monitor
Tracks connection pool health and performance
"""

import logging
import time
from typing import Dict, Any
from datetime import datetime
import threading
from database import engine

logger = logging.getLogger(__name__)


class ConnectionPoolMonitor:
    """Monitor database connection pool performance and health"""
    
    def __init__(self):
        self.lock = threading.Lock()
        self.stats = {
            "total_connections_created": 0,
            "active_connections": 0,
            "pool_exhaustions": 0,
            "slow_connections": 0,
            "connection_errors": 0,
            "last_exhaustion": None,
            "average_connection_time": 0.0
        }
        self.connection_times = []
        self.max_history = 100
        
    def record_connection_acquisition(self, duration: float, success: bool = True):
        """Record connection acquisition timing"""
        with self.lock:
            if success:
                self.stats["total_connections_created"] += 1
                self.connection_times.append(duration)
                
                # Maintain history size
                if len(self.connection_times) > self.max_history:
                    self.connection_times.pop(0)
                
                # Update average
                self.stats["average_connection_time"] = sum(self.connection_times) / len(self.connection_times)
                
                # Track slow connections
                if duration > 0.5:  # > 500ms
                    self.stats["slow_connections"] += 1
            else:
                self.stats["connection_errors"] += 1
    
    def record_pool_exhaustion(self):
        """Record when connection pool is exhausted"""
        with self.lock:
            self.stats["pool_exhaustions"] += 1
            self.stats["last_exhaustion"] = datetime.utcnow()
            logger.error("🚨 Database connection pool exhausted!")
    
    def get_pool_status(self) -> Dict[str, Any]:
        """Get current connection pool status"""
        try:
            pool = engine.pool
            return {
                "pool_size": pool.size(),
                "checked_in": pool.checkedin(),
                "checked_out": pool.checkedout(),
                "overflow": pool.overflow(),
                "invalid": getattr(pool, 'invalidated', 0),  # Use invalidated count instead
                "status": "healthy" if pool.checkedin() > 0 else "warning"
            }
        except Exception as e:
            logger.error(f"Error getting pool status: {e}")
            return {"status": "error", "error": str(e)}
    
    def get_health_report(self) -> Dict[str, Any]:
        """Get comprehensive health report"""
        with self.lock:
            pool_status = self.get_pool_status()
            
            health_status = "healthy"
            if self.stats["pool_exhaustions"] > 0:
                health_status = "critical"
            elif self.stats["slow_connections"] > self.stats["total_connections_created"] * 0.1:
                health_status = "warning"
            
            return {
                "health": health_status,
                "pool_status": pool_status,
                "stats": self.stats.copy(),
                "recommendations": self._get_recommendations()
            }
    
    def _get_recommendations(self) -> list:
        """Get optimization recommendations based on stats"""
        recommendations = []
        
        if self.stats["pool_exhaustions"] > 0:
            recommendations.append("Consider increasing pool_size or max_overflow")
        
        if self.stats["slow_connections"] > 10:
            recommendations.append("Investigate network latency or database performance")
        
        if self.stats["average_connection_time"] > 0.1:
            recommendations.append("Connection acquisition is slow - check database load")
        
        return recommendations
    
    def log_health_summary(self):
        """Log a health summary"""
        report = self.get_health_report()
        pool_status = report["pool_status"]
        
        logger.info(f"🏊 Connection Pool Health: {report['health'].upper()}")
        logger.info(f"   📊 Pool: {pool_status.get('checked_out', 0)}/{pool_status.get('pool_size', 0)} active")
        logger.info(f"   ⏱️  Avg connection time: {self.stats['average_connection_time']:.3f}s")
        logger.info(f"   🐌 Slow connections: {self.stats['slow_connections']}")
        logger.info(f"   ❌ Pool exhaustions: {self.stats['pool_exhaustions']}")


# Global monitor instance
pool_monitor = ConnectionPoolMonitor()


def monitor_connection_acquisition(operation_name: str = "unknown"):
    """Decorator to monitor connection acquisition"""
    def decorator(func):
        def wrapper(*args, **kwargs):
            start_time = time.time()
            success = True
            
            try:
                result = func(*args, **kwargs)
                return result
            except Exception as e:
                success = False
                logger.error(f"Connection acquisition failed for {operation_name}: {e}")
                pool_monitor.record_pool_exhaustion()
                raise
            finally:
                duration = time.time() - start_time
                pool_monitor.record_connection_acquisition(duration, success)
                
        return wrapper
    return decorator