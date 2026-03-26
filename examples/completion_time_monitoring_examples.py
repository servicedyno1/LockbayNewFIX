"""
Completion Time Monitoring Integration Examples
Demonstrates how to integrate completion time monitoring with existing services
"""

import asyncio
import time
import logging
from typing import Optional, Dict, Any

from utils.completion_time_integration import (
    track_onboarding_step,
    track_webhook_processing, 
    track_database_operation,
    track_transaction_processing,
    track_system_health_check,
    track_onboarding_operation,
    track_api_call_operation,
    record_onboarding_completion_time,
    completion_time_integration
)

logger = logging.getLogger(__name__)


class OnboardingServiceWithMonitoring:
    """Example of integrating completion time monitoring with onboarding service"""
    
    @track_onboarding_step("email_verification")
    async def verify_email(self, user_id: int, email: str, otp: str) -> bool:
        """Verify email with completion time tracking"""
        # Simulate email verification processing
        await asyncio.sleep(0.5)  # Database lookup
        await asyncio.sleep(0.3)  # Email service API call
        await asyncio.sleep(0.1)  # Validation logic
        
        logger.info(f"Email verified for user {user_id}")
        return True
    
    @track_onboarding_step("terms_acceptance")
    async def accept_terms(self, user_id: int) -> bool:
        """Accept terms with completion time tracking"""
        await asyncio.sleep(0.2)  # Quick database update
        logger.info(f"Terms accepted for user {user_id}")
        return True
    
    async def complete_onboarding_flow(self, user_id: int, email: str, otp: str):
        """Complete onboarding flow with step-by-step monitoring"""
        
        # Track the entire onboarding flow
        async with track_onboarding_operation("complete_flow", user_id=user_id):
            # Step 1: Verify email (tracked individually)
            email_verified = await self.verify_email(user_id, email, otp)
            if not email_verified:
                raise ValueError("Email verification failed")
            
            # Step 2: Accept terms (tracked individually) 
            terms_accepted = await self.accept_terms(user_id)
            if not terms_accepted:
                raise ValueError("Terms acceptance failed")
            
            # Step 3: Setup wallet (manual tracking example)
            wallet_start = time.time()
            await asyncio.sleep(0.8)  # Wallet creation
            wallet_time = (time.time() - wallet_start) * 1000
            
            # Record manually
            record_onboarding_completion_time(
                "wallet_creation", 
                wallet_time, 
                user_id=user_id, 
                success=True
            )
            
            logger.info(f"Onboarding completed for user {user_id}")


class WebhookHandlerWithMonitoring:
    """Example of webhook processing with completion time monitoring"""
    
    @track_webhook_processing("payment_confirmation")
    async def handle_payment_webhook(self, webhook_data: Dict[str, Any], user_id: Optional[int] = None):
        """Handle payment webhook with monitoring"""
        # Simulate webhook processing
        await asyncio.sleep(0.1)  # Parse webhook data
        await asyncio.sleep(0.3)  # Database updates
        await asyncio.sleep(0.2)  # Send notifications
        
        logger.info(f"Payment webhook processed: {webhook_data.get('transaction_id')}")
        return {"status": "success", "processed_at": time.time()}
    
    @track_webhook_processing("escrow_status_update")
    async def handle_escrow_webhook(self, webhook_data: Dict[str, Any], user_id: Optional[int] = None):
        """Handle escrow webhook with monitoring"""
        await asyncio.sleep(0.05)  # Quick processing
        logger.info(f"Escrow webhook processed: {webhook_data.get('escrow_id')}")
        return {"status": "success"}
    
    async def process_webhook(self, webhook_type: str, data: Dict[str, Any], user_id: Optional[int] = None):
        """Generic webhook processor with dynamic monitoring"""
        
        # Track using context manager for dynamic operation names
        async with track_api_call_operation(f"webhook_{webhook_type}", user_id=user_id):
            if webhook_type == "payment":
                return await self.handle_payment_webhook(data, user_id)
            elif webhook_type == "escrow":
                return await self.handle_escrow_webhook(data, user_id)
            else:
                # Handle unknown webhook types
                await asyncio.sleep(0.1)
                return {"status": "unknown_type"}


class DatabaseServiceWithMonitoring:
    """Example of database operations with completion time monitoring"""
    
    @track_database_operation("user_lookup", "users")
    async def find_user_by_id(self, user_id: int):
        """Find user with database monitoring"""
        await asyncio.sleep(0.05)  # Simulate database query
        return {"id": user_id, "email": f"user{user_id}@example.com"}
    
    @track_database_operation("complex_analytics", "transactions") 
    async def generate_analytics_report(self, date_range: str):
        """Generate analytics with database monitoring"""
        await asyncio.sleep(2.5)  # Simulate complex query
        return {"report": "analytics_data", "date_range": date_range}
    
    @track_database_operation("bulk_update", "users")
    async def bulk_update_users(self, user_ids: list, data: Dict[str, Any]):
        """Bulk update with monitoring"""
        # Simulate bulk operations
        for _ in user_ids:
            await asyncio.sleep(0.02)  # Per-user update
        
        return {"updated_count": len(user_ids)}


class TransactionServiceWithMonitoring:
    """Example of transaction processing with completion time monitoring"""
    
    @track_transaction_processing("crypto_withdrawal")
    async def process_crypto_withdrawal(self, user_id: int, amount: float, currency: str):
        """Process crypto withdrawal with monitoring"""
        # Simulate withdrawal processing
        await asyncio.sleep(1.2)  # Blockchain interaction
        await asyncio.sleep(0.3)  # Database updates
        await asyncio.sleep(0.1)  # Notifications
        
        logger.info(f"Crypto withdrawal processed: {amount} {currency} for user {user_id}")
        return {"transaction_id": f"tx_{user_id}_{int(time.time())}", "status": "completed"}
    
    @track_transaction_processing("fiat_deposit")
    async def process_fiat_deposit(self, user_id: int, amount: float, currency: str):
        """Process fiat deposit with monitoring"""
        await asyncio.sleep(0.8)  # Payment gateway interaction
        await asyncio.sleep(0.2)  # Account credit
        
        logger.info(f"Fiat deposit processed: {amount} {currency} for user {user_id}")
        return {"status": "completed"}


class SystemHealthMonitor:
    """Example of system health checks with monitoring"""
    
    @track_system_health_check("database_connectivity")
    async def check_database_health(self):
        """Check database health"""
        await asyncio.sleep(0.1)  # Connection test
        return {"status": "healthy", "response_time_ms": 50}
    
    @track_system_health_check("api_endpoints")
    async def check_api_health(self):
        """Check API endpoints health"""
        await asyncio.sleep(0.3)  # Multiple endpoint checks
        return {"status": "healthy", "endpoints_checked": 5}
    
    @track_system_health_check("external_services")
    async def check_external_services(self):
        """Check external services health"""
        await asyncio.sleep(1.0)  # Multiple service checks
        return {"status": "partial", "services_healthy": 3, "services_degraded": 1}
    
    async def comprehensive_health_check(self):
        """Run comprehensive health check with monitoring"""
        results = {}
        
        # Run individual health checks (each tracked separately)
        results["database"] = await self.check_database_health()
        results["apis"] = await self.check_api_health() 
        results["external"] = await self.check_external_services()
        
        return results


# Usage examples and demonstrations
async def demonstrate_monitoring():
    """Demonstrate the monitoring system in action"""
    
    # Initialize monitoring integration
    await completion_time_integration.start_integration()
    
    # Create service instances
    onboarding_service = OnboardingServiceWithMonitoring()
    webhook_handler = WebhookHandlerWithMonitoring()
    db_service = DatabaseServiceWithMonitoring()
    transaction_service = TransactionServiceWithMonitoring()
    health_monitor = SystemHealthMonitor()
    
    logger.info("🚀 Starting completion time monitoring demonstrations...")
    
    # Demonstrate onboarding monitoring
    try:
        await onboarding_service.complete_onboarding_flow(12345, "test@example.com", "123456")
    except Exception as e:
        logger.error(f"Onboarding failed: {e}")
    
    # Demonstrate webhook monitoring
    await webhook_handler.process_webhook("payment", {"transaction_id": "tx123"}, user_id=12345)
    await webhook_handler.process_webhook("escrow", {"escrow_id": "esc456"}, user_id=12345)
    
    # Demonstrate database monitoring
    await db_service.find_user_by_id(12345)
    await db_service.bulk_update_users([1, 2, 3, 4, 5], {"status": "active"})
    
    # Demonstrate transaction monitoring
    await transaction_service.process_crypto_withdrawal(12345, 0.5, "BTC")
    await transaction_service.process_fiat_deposit(12345, 100.0, "USD")
    
    # Demonstrate health check monitoring
    await health_monitor.comprehensive_health_check()
    
    # Let monitoring collect some data
    await asyncio.sleep(2)
    
    # Demonstrate slow operation (should trigger warning)
    start_time = time.time()
    await asyncio.sleep(5.5)  # Simulate slow operation
    slow_time = (time.time() - start_time) * 1000
    
    record_onboarding_completion_time("slow_operation", slow_time, user_id=12345, success=True)
    
    logger.info("✅ Monitoring demonstrations completed")


# Performance regression simulation
async def simulate_performance_regression():
    """Simulate performance regression to test trend detection"""
    
    logger.info("📉 Simulating performance regression...")
    
    # Create baseline performance (good)
    for i in range(10):
        record_onboarding_completion_time(
            "performance_test", 
            1000 + (i * 50),  # 1-1.5 seconds
            user_id=i, 
            success=True
        )
        await asyncio.sleep(0.1)
    
    await asyncio.sleep(2)  # Let trends calculate
    
    # Simulate regression (much slower)
    for i in range(10):
        record_onboarding_completion_time(
            "performance_test",
            3000 + (i * 100),  # 3-4 seconds (significant regression)
            user_id=i + 100,
            success=True
        )
        await asyncio.sleep(0.1)
    
    logger.info("📈 Performance regression simulation completed")


async def demonstrate_trend_analysis():
    """Demonstrate trend analysis and reporting"""
    
    from utils.completion_time_integration import (
        get_onboarding_trends_report,
        get_webhook_trends_report,
        get_system_performance_overview
    )
    
    logger.info("📊 Generating trend analysis reports...")
    
    # Get trend reports
    onboarding_trends = await get_onboarding_trends_report()
    webhook_trends = await get_webhook_trends_report()
    system_overview = await get_system_performance_overview()
    
    logger.info(f"Onboarding trends: {onboarding_trends['summary']}")
    logger.info(f"Webhook trends: {webhook_trends['summary']}")
    logger.info(f"System overview: {system_overview}")


if __name__ == "__main__":
    async def main():
        """Run all demonstrations"""
        await demonstrate_monitoring()
        await asyncio.sleep(5)  # Let trends collect
        await simulate_performance_regression()
        await asyncio.sleep(5)  # Let trends analyze
        await demonstrate_trend_analysis()
    
    asyncio.run(main())