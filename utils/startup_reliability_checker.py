#!/usr/bin/env python3
"""
Startup Reliability Checker - Comprehensive system initialization validation
Ensures all critical components are operational before allowing bot to serve users
"""

import logging
import time
from typing import Dict, Any, List, Optional
from dataclasses import dataclass

from sqlalchemy import text
from database import sync_engine, SessionLocal

logger = logging.getLogger(__name__)


@dataclass
class ComponentStatus:
    """Status of a system component"""
    name: str
    healthy: bool
    response_time_ms: float
    error_message: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


@dataclass
class StartupReport:
    """Complete startup validation report"""
    overall_status: str  # "healthy", "degraded", "critical", "failed"
    total_checks: int
    passed_checks: int
    failed_checks: int
    startup_time_seconds: float
    components: List[ComponentStatus]
    recommendations: List[str]


class StartupReliabilityChecker:
    """
    Comprehensive startup validation for production reliability
    """
    
    def __init__(self):
        self.startup_time = time.time()
        self.components: List[ComponentStatus] = []
        self.critical_failures: List[str] = []
        
    async def check_database_connectivity(self) -> ComponentStatus:
        """Test database connection and basic operations"""
        start_time = time.time()
        
        try:
            with sync_engine.connect() as conn:
                # Test basic connectivity
                result = conn.execute(text("SELECT 1 as test"))
                test_value = result.scalar()
                
                if test_value != 1:
                    raise Exception("Database test query returned unexpected value")
                
                # Test transaction capability
                with conn.begin():
                    conn.execute(text("SELECT COUNT(*) FROM users"))
                
                response_time = (time.time() - start_time) * 1000
                
                return ComponentStatus(
                    name="Database Connectivity",
                    healthy=True,
                    response_time_ms=response_time,
                    details={"test_query": "passed", "transaction_test": "passed"}
                )
                
        except Exception as e:
            response_time = (time.time() - start_time) * 1000
            error_msg = str(e)
            
            return ComponentStatus(
                name="Database Connectivity",
                healthy=False,
                response_time_ms=response_time,
                error_message=error_msg,
                details={"error_type": type(e).__name__}
            )
    
    async def check_database_schema(self) -> ComponentStatus:
        """Verify critical database tables exist and have correct structure"""
        start_time = time.time()
        
        critical_tables = [
            "users", "escrows", "transactions", "wallets",
            "trade_ratings", "notifications", "email_verifications"
        ]
        
        try:
            with sync_engine.connect() as conn:
                missing_tables = []
                
                for table in critical_tables:
                    result = conn.execute(text(
                        "SELECT EXISTS (SELECT FROM information_schema.tables "
                        "WHERE table_schema = 'public' AND table_name = :table)"
                    ), {"table": table})
                    
                    exists = result.scalar()
                    if not exists:
                        missing_tables.append(table)
                
                response_time = (time.time() - start_time) * 1000
                
                if missing_tables:
                    return ComponentStatus(
                        name="Database Schema",
                        healthy=False,
                        response_time_ms=response_time,
                        error_message=f"Missing tables: {', '.join(missing_tables)}",
                        details={"missing_tables": missing_tables}
                    )
                else:
                    return ComponentStatus(
                        name="Database Schema",
                        healthy=True,
                        response_time_ms=response_time,
                        details={"verified_tables": len(critical_tables)}
                    )
                    
        except Exception as e:
            response_time = (time.time() - start_time) * 1000
            return ComponentStatus(
                name="Database Schema",
                healthy=False,
                response_time_ms=response_time,
                error_message=str(e)
            )
    
    async def check_connection_pool_health(self) -> ComponentStatus:
        """Verify connection pool is properly configured and responsive"""
        start_time = time.time()
        
        try:
            from utils.database_health_monitor import health_monitor
            
            # Get pool metrics
            pool_metrics = health_monitor.get_pool_metrics()
            
            # Test multiple concurrent connections
            sessions = []
            try:
                for i in range(3):  # Test 3 concurrent sessions
                    session = SessionLocal()
                    session.execute(text("SELECT :test_value"), {"test_value": i})
                    sessions.append(session)
                
                response_time = (time.time() - start_time) * 1000
                
                # Check for pool exhaustion warning
                pool_warning = pool_metrics.utilization_percent > 80
                
                return ComponentStatus(
                    name="Connection Pool",
                    healthy=not pool_metrics.is_exhausted,
                    response_time_ms=response_time,
                    error_message="Pool utilization high" if pool_warning else None,
                    details={
                        "pool_size": pool_metrics.size,
                        "utilization_percent": pool_metrics.utilization_percent,
                        "concurrent_sessions_test": "passed"
                    }
                )
                
            finally:
                # Clean up sessions
                for session in sessions:
                    try:
                        session.close()
                    except Exception as e:
                        logger.debug(f"Could not close session during cleanup: {e}")
                        pass
                    
        except Exception as e:
            response_time = (time.time() - start_time) * 1000
            return ComponentStatus(
                name="Connection Pool",
                healthy=False,
                response_time_ms=response_time,
                error_message=str(e)
            )
    
    async def check_ssl_connection(self) -> ComponentStatus:
        """Verify SSL connection is working properly"""
        start_time = time.time()
        
        try:
            with sync_engine.connect() as conn:
                # Try to get SSL information if available
                try:
                    result = conn.execute(text("SHOW ssl"))
                    ssl_status = result.scalar()
                except Exception as e:
                    logger.debug(f"Could not get SSL status: {e}")
                    ssl_status = "unknown"
                
                # Test a few rapid connections to check for SSL issues
                for i in range(3):
                    with sync_engine.connect() as test_conn:
                        test_conn.execute(text("SELECT 1"))
                
                response_time = (time.time() - start_time) * 1000
                
                return ComponentStatus(
                    name="SSL Connection",
                    healthy=True,
                    response_time_ms=response_time,
                    details={
                        "ssl_status": ssl_status,
                        "rapid_connection_test": "passed"
                    }
                )
                
        except Exception as e:
            response_time = (time.time() - start_time) * 1000
            error_msg = str(e)
            
            # Check if this is an SSL-specific error
            is_ssl_error = any(ssl_term in error_msg.lower() for ssl_term in [
                "ssl", "tls", "certificate", "handshake", "eof detected"
            ])
            
            return ComponentStatus(
                name="SSL Connection",
                healthy=False,
                response_time_ms=response_time,
                error_message=error_msg,
                details={
                    "is_ssl_error": is_ssl_error,
                    "error_type": type(e).__name__
                }
            )
    
    async def check_performance_baseline(self) -> ComponentStatus:
        """Establish performance baseline for monitoring"""
        start_time = time.time()
        
        try:
            query_times = []
            
            # Test several different query types
            test_queries = [
                ("SELECT COUNT(*) FROM users", "user_count"),
                ("SELECT COUNT(*) FROM escrows", "escrow_count"),
                ("SELECT COUNT(*) FROM transactions WHERE created_at > NOW() - INTERVAL '1 day'", "recent_transactions")
            ]
            
            with sync_engine.connect() as conn:
                for query, query_name in test_queries:
                    query_start = time.time()
                    try:
                        result = conn.execute(text(query))
                        result.fetchall()  # Ensure full execution
                        query_time = (time.time() - query_start) * 1000
                        query_times.append((query_name, query_time))
                    except Exception as query_error:
                        logger.warning(f"Performance test query failed ({query_name}): {query_error}")
                        query_times.append((query_name, 999999))  # Mark as very slow
            
            response_time = (time.time() - start_time) * 1000
            
            # Check if any queries are unusually slow
            slow_queries = [q for q in query_times if q[1] > 1000]  # > 1 second
            avg_query_time = sum(q[1] for q in query_times) / len(query_times)
            
            is_healthy = len(slow_queries) == 0 and avg_query_time < 500
            
            return ComponentStatus(
                name="Performance Baseline",
                healthy=is_healthy,
                response_time_ms=response_time,
                error_message=f"Slow queries detected: {slow_queries}" if slow_queries else None,
                details={
                    "average_query_time_ms": avg_query_time,
                    "query_results": dict(query_times),
                    "slow_query_count": len(slow_queries)
                }
            )
            
        except Exception as e:
            response_time = (time.time() - start_time) * 1000
            return ComponentStatus(
                name="Performance Baseline",
                healthy=False,
                response_time_ms=response_time,
                error_message=str(e)
            )
    
    async def run_comprehensive_check(self) -> StartupReport:
        """Run all startup validation checks"""
        logger.info("🔍 Starting comprehensive startup reliability check...")
        
        # Define all checks to run
        checks = [
            self.check_database_connectivity,
            self.check_database_schema,
            self.check_connection_pool_health,
            self.check_ssl_connection,
            self.check_performance_baseline
        ]
        
        # Run all checks
        for check in checks:
            try:
                component_status = await check()
                self.components.append(component_status)
                
                if not component_status.healthy:
                    self.critical_failures.append(component_status.name)
                    
                logger.info(
                    f"{'✅' if component_status.healthy else '❌'} "
                    f"{component_status.name}: {component_status.response_time_ms:.1f}ms"
                )
                
            except Exception as e:
                logger.error(f"Check failed for {check.__name__}: {e}")
                self.components.append(ComponentStatus(
                    name=check.__name__.replace("check_", "").replace("_", " ").title(),
                    healthy=False,
                    response_time_ms=0,
                    error_message=str(e)
                ))
                self.critical_failures.append(check.__name__)
        
        # Generate report
        total_startup_time = time.time() - self.startup_time
        passed_checks = sum(1 for c in self.components if c.healthy)
        failed_checks = len(self.components) - passed_checks
        
        # Determine overall status
        if failed_checks == 0:
            overall_status = "healthy"
        elif len(self.critical_failures) == 0:
            overall_status = "degraded"
        elif len(self.critical_failures) < len(self.components) / 2:
            overall_status = "critical"
        else:
            overall_status = "failed"
        
        # Generate recommendations
        recommendations = self._generate_recommendations()
        
        report = StartupReport(
            overall_status=overall_status,
            total_checks=len(self.components),
            passed_checks=passed_checks,
            failed_checks=failed_checks,
            startup_time_seconds=total_startup_time,
            components=self.components,
            recommendations=recommendations
        )
        
        # Log summary
        logger.info(f"🏁 Startup check complete: {overall_status.upper()}")
        logger.info(f"   Checks: {passed_checks}/{len(self.components)} passed")
        logger.info(f"   Time: {total_startup_time:.2f}s")
        
        if failed_checks > 0:
            logger.warning(f"   Failed components: {', '.join(self.critical_failures)}")
        
        if recommendations:
            logger.info("   Recommendations:")
            for rec in recommendations:
                logger.info(f"   - {rec}")
        
        return report
    
    def _generate_recommendations(self) -> List[str]:
        """Generate recommendations based on check results"""
        recommendations = []
        
        # Check for specific issues and generate recommendations
        for component in self.components:
            if not component.healthy:
                if component.name == "Database Connectivity":
                    recommendations.append("Check database connection string and network connectivity")
                elif component.name == "Database Schema":
                    recommendations.append("Run database migration to ensure all tables exist")
                elif component.name == "Connection Pool":
                    recommendations.append("Consider increasing connection pool size or reducing concurrent operations")
                elif component.name == "SSL Connection":
                    recommendations.append("Investigate SSL/TLS configuration and certificate validity")
                elif component.name == "Performance Baseline":
                    recommendations.append("Optimize database queries and consider indexing improvements")
        
        # General recommendations based on overall health
        unhealthy_count = len([c for c in self.components if not c.healthy])
        if unhealthy_count > len(self.components) / 2:
            recommendations.append("Multiple critical systems failing - consider postponing deployment")
        elif unhealthy_count > 0:
            recommendations.append("Monitor system closely and address failing components")
        
        if not recommendations:
            recommendations.append("All systems operational - ready for production")
        
        return recommendations


# Global instance for easy access
startup_checker = StartupReliabilityChecker()


async def perform_startup_validation() -> StartupReport:
    """Perform comprehensive startup validation"""
    return await startup_checker.run_comprehensive_check()


def is_system_ready() -> bool:
    """Quick check if system passed startup validation"""
    if not startup_checker.components:
        return False
    
    return all(component.healthy for component in startup_checker.components)