"""
Health Check Service
Provides health check endpoints for all critical services
"""

import logging
import asyncio
from datetime import datetime
from typing import Dict
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class HealthCheckResult:
    """Result of a health check"""
    service: str
    status: str  # 'healthy', 'warning', 'critical'
    message: str
    response_time_ms: float = 0.0
    timestamp: datetime = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.utcnow()

class HealthCheckService:
    """Service for running health checks on all critical components"""
    
    @classmethod
    async def check_database(cls) -> HealthCheckResult:
        """Check database connectivity and performance"""
        start_time = datetime.utcnow()
        try:
            from database import SessionLocal
            
            with SessionLocal() as session:
                # Simple query to test connectivity
                result = session.execute("SELECT 1 as test").fetchone()
                
                if result and result[0] == 1:
                    response_time = (datetime.utcnow() - start_time).total_seconds() * 1000
                    
                    if response_time < 100:
                        status = "healthy"
                        message = f"Database responsive ({response_time:.1f}ms)"
                    elif response_time < 500:
                        status = "warning"
                        message = f"Database slow ({response_time:.1f}ms)"
                    else:
                        status = "critical"
                        message = f"Database very slow ({response_time:.1f}ms)"
                    
                    return HealthCheckResult(
                        service="database",
                        status=status,
                        message=message,
                        response_time_ms=response_time
                    )
                else:
                    return HealthCheckResult(
                        service="database",
                        status="critical",
                        message="Database query returned unexpected result"
                    )
                    
        except Exception as e:
            return HealthCheckResult(
                service="database",
                status="critical",
                message=f"Database connection failed: {str(e)}"
            )
    
    @classmethod
    async def check_telegram_bot(cls) -> HealthCheckResult:
        """Check Telegram bot service"""
        start_time = datetime.utcnow()
        try:
            from telegram import Bot
            from config import Config
            
            if not Config.BOT_TOKEN:
                return HealthCheckResult(
                    service="telegram_bot",
                    status="critical",
                    message="BOT_TOKEN not configured"
                )
            
            bot = Bot(Config.BOT_TOKEN)
            me = await bot.get_me()
            
            if me and me.username:
                response_time = (datetime.utcnow() - start_time).total_seconds() * 1000
                return HealthCheckResult(
                    service="telegram_bot",
                    status="healthy",
                    message=f"Bot @{me.username} responsive ({response_time:.1f}ms)",
                    response_time_ms=response_time
                )
            else:
                return HealthCheckResult(
                    service="telegram_bot",
                    status="critical",
                    message="Bot API returned invalid response"
                )
                
        except Exception as e:
            return HealthCheckResult(
                service="telegram_bot",
                status="critical",
                message=f"Telegram API error: {str(e)}"
            )
    
    @classmethod
    async def check_email_service(cls) -> HealthCheckResult:
        """Check email service (Brevo)"""
        try:
            import os
            
            brevo_key = os.getenv('BREVO_API_KEY')
            if not brevo_key:
                return HealthCheckResult(
                    service="email_service",
                    status="critical",
                    message="BREVO_API_KEY not configured"
                )
            
            # Test email service initialization
            from services.email import EmailService
            email_service = EmailService()
            
            if hasattr(email_service, '_brevo_client') or hasattr(email_service, 'brevo_client'):
                return HealthCheckResult(
                    service="email_service",
                    status="healthy",
                    message=f"Email service configured (API key: {len(brevo_key)} chars)"
                )
            else:
                return HealthCheckResult(
                    service="email_service",
                    status="warning",
                    message="Email service configured but client status unclear"
                )
                
        except Exception as e:
            return HealthCheckResult(
                service="email_service",
                status="critical",
                message=f"Email service error: {str(e)}"
            )
    
    @classmethod
    async def check_blockbee_service(cls) -> HealthCheckResult:
        """Check BlockBee API service"""
        try:
            from config import Config
            
            if not Config.BLOCKBEE_API_KEY:
                return HealthCheckResult(
                    service="blockbee_service",
                    status="critical",
                    message="BLOCKBEE_API_KEY not configured"
                )
            
            # Basic configuration check
            from services.blockbee_service import blockbee_service
            if blockbee_service:
                return HealthCheckResult(
                    service="blockbee_service",
                    status="healthy",
                    message="BlockBee service configured and ready"
                )
            else:
                return HealthCheckResult(
                    service="blockbee_service",
                    status="critical",
                    message="BlockBee service not initialized"
                )
                
        except Exception as e:
            return HealthCheckResult(
                service="blockbee_service",
                status="critical",
                message=f"BlockBee service error: {str(e)}"
            )
    
    @classmethod
    async def check_notification_monitor(cls) -> HealthCheckResult:
        """Check notification monitoring service"""
        try:
            from services.notification_monitor import notification_monitor
            
            health_summary = notification_monitor.get_health_summary()
            status = health_summary["status"]
            success_rate = health_summary["overall_success_rate"]
            
            return HealthCheckResult(
                service="notification_monitor",
                status=status,
                message=f"Success rate: {success_rate:.1f}%, Recent failures: {health_summary['recent_failures']['total_failures']}"
            )
            
        except Exception as e:
            return HealthCheckResult(
                service="notification_monitor",
                status="critical",
                message=f"Notification monitor error: {str(e)}"
            )
    
    @classmethod
    async def run_all_checks(cls) -> Dict[str, HealthCheckResult]:
        """Run all health checks in parallel"""
        try:
            checks = await asyncio.gather(
                cls.check_database(),
                cls.check_telegram_bot(),
                cls.check_email_service(),
                cls.check_blockbee_service(),
                cls.check_notification_monitor(),
                return_exceptions=True
            )
            
            results = {}
            check_names = ["database", "telegram_bot", "email_service", "blockbee_service", "notification_monitor"]
            
            for i, check in enumerate(checks):
                if isinstance(check, Exception):
                    results[check_names[i]] = HealthCheckResult(
                        service=check_names[i],
                        status="critical",
                        message=f"Health check failed: {str(check)}"
                    )
                else:
                    results[check_names[i]] = check
            
            return results
            
        except Exception as e:
            logger.error(f"Error running health checks: {e}")
            return {
                "error": HealthCheckResult(
                    service="health_check_system",
                    status="critical",
                    message=f"Health check system error: {str(e)}"
                )
            }
    
    @classmethod
    def get_overall_status(cls, results: Dict[str, HealthCheckResult]) -> str:
        """Determine overall system status from individual checks"""
        if not results:
            return "unknown"
        
        statuses = [result.status for result in results.values()]
        
        if "critical" in statuses:
            return "critical"
        elif "warning" in statuses:
            return "warning"
        elif all(status == "healthy" for status in statuses):
            return "healthy"
        else:
            return "unknown"

# Global instance
health_service = HealthCheckService()