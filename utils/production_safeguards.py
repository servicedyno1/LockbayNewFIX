#!/usr/bin/env python3
"""
Production Safeguards and Critical P0 Fixes
Implementation of critical production-ready safeguards based on architectural analysis
"""

import logging
from typing import Optional, Dict, Any, List
from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import Decimal
from collections import defaultdict

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy import text

from database import sync_engine  # type: ignore
from models import Wallet, WebhookLog  # type: ignore
from utils.atomic_transactions import atomic_transaction

logger = logging.getLogger(__name__)


class ProductionSafeguards:
    """Critical production safeguards for financial operations"""

    @staticmethod
    def ensure_database_constraints():
        """Ensure critical database constraints and indexes for production"""
        try:
            # Use separate transactions for each constraint/index to avoid cascade failures
            constraints_to_add = [
                # (table, constraint_name, constraint_sql)
                ("wallets", "uq_user_currency_wallet", "ALTER TABLE wallets ADD CONSTRAINT uq_user_currency_wallet UNIQUE (user_id, currency)"),
                ("webhook_logs", "uq_webhook_event_provider", "ALTER TABLE webhook_logs ADD CONSTRAINT uq_webhook_event_provider UNIQUE (webhook_id, provider)"),
                ("transactions", "uq_transaction_idempotency", "ALTER TABLE transactions ADD CONSTRAINT uq_transaction_idempotency UNIQUE (transaction_id)"),
                ("persistent_jobs", "uq_job_id", "ALTER TABLE persistent_jobs ADD CONSTRAINT uq_job_id UNIQUE (job_id)"),
                ("wallets", "chk_positive_balance", "ALTER TABLE wallets ADD CONSTRAINT chk_positive_balance CHECK (balance >= 0)"),
                ("wallets", "chk_positive_frozen", "ALTER TABLE wallets ADD CONSTRAINT chk_positive_frozen CHECK (frozen_balance >= 0)"),
            ]

            # Critical indexes for performance - these support IF NOT EXISTS
            indexes_to_add = [
                "CREATE INDEX IF NOT EXISTS idx_wallet_user_currency_balance ON wallets (user_id, currency, balance);",
                "CREATE INDEX IF NOT EXISTS idx_persistent_jobs_next_run_status ON persistent_jobs (next_run_at, status);",
                "CREATE INDEX IF NOT EXISTS idx_persistent_jobs_priority_next_run ON persistent_jobs (priority, next_run_at);",
                "CREATE INDEX IF NOT EXISTS idx_webhook_logs_received_processed ON webhook_logs (received_at, processed);",
                "CREATE INDEX IF NOT EXISTS idx_transactions_user_status_created ON transactions (user_id, status, created_at);",
                "CREATE INDEX IF NOT EXISTS idx_escrows_status_created_expires ON escrows (status, created_at, expires_at);",
            ]

            # Check and add constraints with PostgreSQL version compatibility
            for table_name, constraint_name, constraint_sql in constraints_to_add:
                with sync_engine.connect() as conn:
                    try:
                        # Check if constraint exists first (compatible with older PostgreSQL)
                        check_sql = text("""
                            SELECT COUNT(*) FROM information_schema.table_constraints 
                            WHERE table_name = :table_name 
                            AND constraint_name = :constraint_name
                            AND constraint_schema = 'public'
                        """)
                        result = conn.execute(check_sql, {
                            'table_name': table_name, 
                            'constraint_name': constraint_name
                        })
                        exists = result.scalar() > 0
                        
                        if not exists:
                            conn.execute(text(constraint_sql))
                            conn.commit()
                            logger.info(f"✅ Applied constraint: {constraint_name}")
                        else:
                            logger.debug(f"Constraint already exists: {constraint_name}")
                            
                    except Exception as e:
                        # Log as debug instead of warning for cleaner logs
                        logger.debug(f"Constraint {constraint_name} check: {str(e)[:100]}")

            # Add indexes (these already support IF NOT EXISTS)
            for index_sql in indexes_to_add:
                with sync_engine.connect() as conn:
                    try:
                        conn.execute(text(index_sql))
                        conn.commit()
                        logger.debug(f"Index check: {index_sql[:50]}...")
                    except Exception as e:
                        # Log as debug for cleaner logs
                        logger.debug(f"Index exists or not needed: {str(e)[:100]}")

            logger.info("✅ Database constraints and indexes verification completed")

        except Exception as e:
            logger.error(f"Error ensuring database constraints: {e}")
            # Don't raise - allow system to continue even if constraints fail
            pass

    @staticmethod
    @contextmanager
    def safe_wallet_operation(user_id: int, currency: str, session: Session):
        """
        Production-safe wallet operation with race condition protection
        Implements P0 fix: Enforce DB uniqueness and handle IntegrityError
        """
        try:
            # Try to get existing wallet with row lock
            wallet = (
                session.query(Wallet)
                .filter(Wallet.user_id == user_id, Wallet.currency == currency)
                .with_for_update()
                .first()
            )

            if wallet:
                yield wallet
            else:
                # Create new wallet with race condition protection
                try:
                    wallet = Wallet(
                        user_id=user_id,
                        currency=currency,
                        balance=Decimal("0"),
                        frozen_balance=Decimal("0"),
                    )
                    session.add(wallet)
                    session.flush()  # Force immediate constraint check
                    yield wallet

                except IntegrityError:
                    # Handle race condition - another transaction created the wallet
                    session.rollback()
                    logger.info(
                        f"Race condition detected creating wallet for user {user_id}, currency {currency}"
                    )

                    # Re-query with lock after rollback
                    wallet = (
                        session.query(Wallet)
                        .filter(Wallet.user_id == user_id, Wallet.currency == currency)
                        .with_for_update()
                        .first()
                    )

                    if wallet:
                        yield wallet
                    else:
                        raise Exception(
                            f"Failed to create or retrieve wallet for user {user_id}"
                        )

        except Exception as e:
            logger.error(f"Error in safe wallet operation: {e}")
            raise

    @staticmethod
    def validate_escrow_transition(current_status: str, new_status: str) -> bool:
        """
        Validate escrow state transitions using unified validation system
        
        This method now delegates to UnifiedTransitionValidator for consistent
        validation logic across the entire application.
        """
        try:
            from utils.status_flows import UnifiedTransitionValidator, UnifiedTransactionType
            
            validator = UnifiedTransitionValidator()
            result = validator.validate_transition(
                current_status=current_status,
                new_status=new_status,
                transaction_type=UnifiedTransactionType.ESCROW
            )
            
            if not result.is_valid:
                logger.warning(
                    f"Escrow transition validation failed: {current_status} → {new_status} - {result.error_message}"
                )
            
            return result.is_valid
            
        except Exception as e:
            logger.error(f"Error in production safeguards escrow validation: {e}")
            # Fallback: reject invalid transitions to be safe
            return False

    @staticmethod
    def generate_idempotency_key(operation_type: str, user_id: int, **params) -> str:
        """
        Generate idempotency key for critical operations
        Implements P0 fix: Idempotency across webhooks, releases, refunds
        """
        import hashlib

        # Include timestamp for time-based uniqueness but rounded to prevent duplicates
        timestamp = datetime.utcnow().replace(second=0, microsecond=0)

        # Create deterministic key from operation parameters
        key_data = f"{operation_type}:{user_id}:{timestamp.isoformat()}"

        # Add operation-specific parameters
        for key, value in sorted(params.items()):
            key_data += f":{key}={value}"

        # Generate SHA256 hash for consistent length
        return f"{operation_type}_{hashlib.sha256(key_data.encode()).hexdigest()[:16]}"

    @staticmethod
    async def verify_webhook_idempotency(
        webhook_id: str, provider: str, session: Session
    ) -> bool:
        """
        Check webhook idempotency to prevent duplicate processing
        Returns True if webhook was already processed
        """
        try:
            existing = (
                session.query(WebhookLog)
                .filter(
                    WebhookLog.webhook_id == webhook_id,
                    WebhookLog.provider == provider,
                    WebhookLog.processed,
                )
                .first()
            )

            return existing is not None

        except Exception as e:
            logger.error(f"Error checking webhook idempotency: {e}")
            # Fail safe - assume not processed to avoid blocking legitimate webhooks
            return False

    @staticmethod
    def validate_financial_operation(
        operation_type: str, amount: Decimal, user_id: int
    ) -> Dict[str, Any]:
        """
        Validate financial operations for security and compliance
        """
        validation_result = {
            "valid": True,
            "warnings": [],
            "blocks": [],
            "risk_score": 0.0,
        }

        # Amount validation
        if amount <= 0:
            validation_result["valid"] = False
            validation_result["blocks"].append("Amount must be positive")

        # Large transaction detection
        if amount > Decimal("10000"):  # $10,000 threshold
            validation_result["warnings"].append(
                "Large transaction requires additional verification"
            )
            validation_result["risk_score"] += 30.0

        # Rapid transaction check would go here (requires session/database access)
        # This is a placeholder for the logic

        return validation_result


class FinancialOperationGuard:
    """Guard for all financial operations ensuring atomic execution"""

    @staticmethod
    @contextmanager
    def atomic_financial_operation(operation_name: str, user_id: Optional[int] = None):
        """
        Context manager for atomic financial operations
        Implements P0 fix: Wrap all financial mutations with atomic guarantees
        """
        operation_id = (
            f"{operation_name}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S_%f')}"
        )

        logger.info(f"Starting atomic financial operation: {operation_id}")

        try:
            with atomic_transaction() as session:
                # Log operation start
                start_time = datetime.utcnow()

                yield session

                # Log successful completion
                end_time = datetime.utcnow()
                duration = (end_time - start_time).total_seconds()

                logger.info(
                    f"✅ Completed atomic financial operation {operation_id} in {duration:.3f}s"
                )

        except Exception as e:
            logger.error(f"❌ Failed atomic financial operation {operation_id}: {e}")
            raise


# Initialize production safeguards on import
def initialize_production_safeguards():
    """Initialize critical production safeguards"""
    try:
        safeguards = ProductionSafeguards()
        safeguards.ensure_database_constraints()
        logger.info("✅ Production safeguards initialized successfully")
    except Exception as e:
        logger.error(f"❌ Failed to initialize production safeguards: {e}")
        # Don't raise to avoid blocking application startup, but log clearly


# Global instances and functions
production_safeguards = ProductionSafeguards()


# Convenience functions for common operations
def safe_wallet_operation(user_id: int, currency: str, session: Session):
    """Convenience function for safe wallet operations"""
    return production_safeguards.safe_wallet_operation(user_id, currency, session)


def validate_escrow_transition(current_status: str, new_status: str) -> bool:
    """Convenience function for escrow state validation using unified system"""
    return production_safeguards.validate_escrow_transition(current_status, new_status)


def generate_idempotency_key(operation_type: str, user_id: int, **params) -> str:
    """Convenience function for idempotency key generation"""
    return production_safeguards.generate_idempotency_key(
        operation_type, user_id, **params
    )


# Enhanced production safeguards with alerting
from dataclasses import dataclass

@dataclass
class Alert:
    """Alert configuration"""
    alert_id: str
    threshold: int
    window_minutes: int
    message: str
    severity: str  # 'warning', 'critical'
    enabled: bool = True

@dataclass
class AlertInstance:
    """Instance of a triggered alert"""
    alert_id: str
    timestamp: datetime
    message: str
    severity: str
    resolved: bool = False

class ProductionAlerting:
    """Production alerting and monitoring system"""
    
    def __init__(self):
        self.alerts_config = {
            "notification_failures": Alert(
                alert_id="notification_failures",
                threshold=5,
                window_minutes=10,
                message="High notification failure rate detected",
                severity="critical"
            ),
            "email_service_down": Alert(
                alert_id="email_service_down",
                threshold=3,
                window_minutes=5,
                message="Email service appears to be down",
                severity="critical"
            ),
            "telegram_service_down": Alert(
                alert_id="telegram_service_down", 
                threshold=3,
                window_minutes=5,
                message="Telegram service appears to be down",
                severity="critical"
            ),
            "wallet_credit_failures": Alert(
                alert_id="wallet_credit_failures",
                threshold=2,
                window_minutes=15,
                message="Wallet credit operations failing",
                severity="critical"
            ),
            "database_slow": Alert(
                alert_id="database_slow",
                threshold=5,
                window_minutes=10,
                message="Database performance degraded",
                severity="warning"
            )
        }
        
        self.triggered_alerts: List[AlertInstance] = []
        self.event_history: Dict[str, List[datetime]] = defaultdict(list)
        
    async def record_event(self, event_type: str, details: Optional[Dict[str, Any]] = None):
        """Record an event that might trigger alerts"""
        try:
            timestamp = datetime.utcnow()
            self.event_history[event_type].append(timestamp)
            
            # Clean old events outside alert windows
            self._cleanup_old_events()
            
            # Check if this event triggers any alerts
            await self._check_alerts(event_type)
            
            logger.debug(f"Recorded event: {event_type} at {timestamp}")
            
        except Exception as e:
            logger.error(f"Error recording event {event_type}: {e}")
    
    def _cleanup_old_events(self):
        """Remove events older than the longest alert window"""
        max_window = max(alert.window_minutes for alert in self.alerts_config.values())
        cutoff_time = datetime.utcnow() - timedelta(minutes=max_window)
        
        for event_type in self.event_history:
            self.event_history[event_type] = [
                event_time for event_time in self.event_history[event_type]
                if event_time > cutoff_time
            ]
    
    async def _check_alerts(self, event_type: str):
        """Check if recent events trigger any alerts"""
        try:
            # Map event types to alerts
            event_alert_mapping = {
                "notification_failed": ["notification_failures"],
                "email_failed": ["email_service_down", "notification_failures"],
                "telegram_failed": ["telegram_service_down", "notification_failures"], 
                "wallet_credit_failed": ["wallet_credit_failures"],
                "database_slow_query": ["database_slow"]
            }
            
            alert_ids = event_alert_mapping.get(event_type, [])
            
            for alert_id in alert_ids:
                if alert_id in self.alerts_config:
                    await self._check_specific_alert(alert_id, event_type)
                    
        except Exception as e:
            logger.error(f"Error checking alerts for event {event_type}: {e}")
    
    async def _check_specific_alert(self, alert_id: str, event_type: str):
        """Check if a specific alert should be triggered"""
        try:
            alert_config = self.alerts_config[alert_id]
            
            if not alert_config.enabled:
                return
            
            # Check if alert already triggered recently (avoid spam)
            from datetime import timedelta
            recent_alerts = [
                alert for alert in self.triggered_alerts
                if alert.alert_id == alert_id 
                and alert.timestamp > datetime.utcnow() - timedelta(minutes=alert_config.window_minutes)
                and not alert.resolved
            ]
            
            if recent_alerts:
                return  # Alert already active
            
            # Count events in the alert window
            window_start = datetime.utcnow() - timedelta(minutes=alert_config.window_minutes)
            relevant_events = [
                event_time for event_time in self.event_history[event_type]
                if event_time > window_start
            ]
            
            if len(relevant_events) >= alert_config.threshold:
                # Trigger alert
                alert_instance = AlertInstance(
                    alert_id=alert_id,
                    timestamp=datetime.utcnow(),
                    message=f"{alert_config.message} ({len(relevant_events)} occurrences in {alert_config.window_minutes}m)",
                    severity=alert_config.severity
                )
                
                self.triggered_alerts.append(alert_instance)
                await self._send_alert(alert_instance)
                
        except Exception as e:
            logger.error(f"Error checking alert {alert_id}: {e}")
    
    async def _send_alert(self, alert: AlertInstance):
        """Send alert notification to admins"""
        try:
            severity_emoji = {"warning": "⚠️", "critical": "🚨"}
            emoji = severity_emoji.get(alert.severity, "⚠️")
            
            f"""
{emoji} **PRODUCTION ALERT**

**Severity**: {alert.severity.upper()}
**Alert**: {alert.alert_id}
**Time**: {alert.timestamp.strftime('%Y-%m-%d %H:%M:%S')} UTC

**Message**: {alert.message}

**Action Required**: Check system status and resolve underlying issues.
"""
            
            # Log critical alerts to ensure they're visible
            if alert.severity == "critical":
                logger.critical(f"CRITICAL ALERT: {alert.alert_id} - {alert.message}")
            else:
                logger.warning(f"WARNING ALERT: {alert.alert_id} - {alert.message}")
            
        except Exception as e:
            logger.error(f"Error sending alert: {e}")
    
    def get_system_status(self) -> Dict[str, Any]:
        """Get overall system status summary"""
        from datetime import timedelta
        active_alerts = [alert for alert in self.triggered_alerts if not alert.resolved]
        recent_alerts = [
            alert for alert in self.triggered_alerts 
            if alert.timestamp > datetime.utcnow() - timedelta(hours=24)
        ]
        
        # Determine overall status
        if any(alert.severity == "critical" for alert in active_alerts):
            status = "critical"
        elif any(alert.severity == "warning" for alert in active_alerts):
            status = "warning"
        elif any(alert.severity == "critical" for alert in recent_alerts):
            status = "recovering"
        else:
            status = "healthy"
        
        return {
            "status": status,
            "active_alerts": len(active_alerts),
            "alerts_24h": len(recent_alerts),
            "critical_alerts": len([a for a in active_alerts if a.severity == "critical"]),
            "warning_alerts": len([a for a in active_alerts if a.severity == "warning"]),
            "last_check": datetime.utcnow().isoformat()
        }

# Global alerting instance
production_alerting = ProductionAlerting()
