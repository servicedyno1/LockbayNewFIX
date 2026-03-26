"""
Silent API Failure Detection Service
Monitors API failures in background and provides admin alerts
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from enum import Enum
from collections import defaultdict, deque

from database import SessionLocal
from models import AuditLog
from services.email import EmailService
from services.circuit_breaker import circuit_breakers

logger = logging.getLogger(__name__)


class FailureSeverity(Enum):
    LOW = "low"           # Single API timeout
    MEDIUM = "medium"     # Multiple failures in sequence
    HIGH = "high"         # Service degradation
    CRITICAL = "critical" # Complete service outage


@dataclass
class APIFailureEvent:
    service_name: str
    failure_type: str
    error_message: str
    timestamp: datetime
    severity: FailureSeverity
    affected_operations: List[str]
    metadata: Dict[str, Any]


class SilentAPIFailureDetector:
    """
    Silent monitoring of API failures across all external services
    Provides admin alerts without disrupting user experience
    """
    
    def __init__(self):
        self.failure_history = defaultdict(lambda: deque(maxlen=100))
        self.alert_cooldown = {}  # Prevent spam
        self.email_service = EmailService()
        
        # Configuration
        self.failure_thresholds = {
            'binance': {'rate': 3, 'window': 300},      # 3 failures in 5 mins
            'fincra': {'rate': 3, 'window': 300},       # 3 failures in 5 mins
            'blockbee': {'rate': 5, 'window': 600},     # 5 failures in 10 mins
            'email': {'rate': 10, 'window': 1800},      # 10 failures in 30 mins
        }
        
        self.critical_operations = {
            'binance': ['process_cashout', 'process_approved_cashout'],
            'fincra': ['process_bank_transfer', 'check_transfer_status'],
            'blockbee': ['create_payment_address', 'check_payment_status'],
            'email': ['send_otp_email', 'send_admin_alert']
        }
        
        logger.info("🔍 Silent API failure detector initialized")

    async def record_failure(
        self, 
        service_name: str, 
        operation: str, 
        error: Exception,
        metadata: Dict[str, Any] = None
    ):
        """Record API failure for analysis"""
        try:
            failure_event = APIFailureEvent(
                service_name=service_name,
                failure_type=type(error).__name__,
                error_message=str(error)[:500],  # Truncate long errors
                timestamp=datetime.utcnow(),
                severity=self._assess_severity(service_name, operation, error),
                affected_operations=[operation],
                metadata=metadata or {}
            )
            
            # Store in memory for real-time analysis
            self.failure_history[service_name].append(failure_event)
            
            # Log to database for persistent tracking
            await self._log_to_database(failure_event)
            
            # Check if we need to send alerts
            await self._check_alert_conditions(service_name, failure_event)
            
            logger.warning(
                f"🚨 API Failure Detected: {service_name}.{operation} - "
                f"{failure_event.severity.value.upper()} - {str(error)[:100]}"
            )
            
        except Exception as e:
            logger.error(f"Error recording API failure: {e}")

    def _assess_severity(self, service_name: str, operation: str, error: Exception) -> FailureSeverity:
        """Assess failure severity based on context"""
        error_type = type(error).__name__
        
        # Critical operations get higher severity
        if operation in self.critical_operations.get(service_name, []):
            base_severity = FailureSeverity.MEDIUM
        else:
            base_severity = FailureSeverity.LOW
        
        # Circuit breaker open = high severity
        if "circuit breaker" in str(error).lower() or "circuit" in str(error).lower():
            return FailureSeverity.HIGH
        
        # Timeout errors are medium severity for critical ops
        if "timeout" in error_type.lower() or "timeout" in str(error).lower():
            return base_severity
        
        # Authentication/authorization errors are high severity
        if any(keyword in str(error).lower() for keyword in ['auth', 'unauthorized', 'forbidden', 'key']):
            return FailureSeverity.HIGH
        
        # Network errors are medium severity
        if any(keyword in error_type.lower() for keyword in ['connection', 'network', 'dns']):
            return FailureSeverity.MEDIUM
        
        return base_severity

    async def _log_to_database(self, failure_event: APIFailureEvent):
        """Log failure to database for persistence"""
        try:
            session = SessionLocal()
            try:
                log_entry = AuditLog(
                    user_id=None,  # System event
                    event_type="api_failure",
                    event_data={
                        "service": failure_event.service_name,
                        "failure_type": failure_event.failure_type,
                        "error_message": failure_event.error_message,
                        "severity": failure_event.severity.value,
                        "operations": failure_event.affected_operations,
                        **failure_event.metadata
                    },
                    severity="high" if failure_event.severity in [FailureSeverity.HIGH, FailureSeverity.CRITICAL] else "medium",
                    timestamp=failure_event.timestamp
                )
                session.add(log_entry)
                session.commit()
            finally:
                session.close()
        except Exception as e:
            logger.error(f"Failed to log API failure to database: {e}")

    async def _check_alert_conditions(self, service_name: str, latest_failure: APIFailureEvent):
        """Check if alert conditions are met"""
        now = datetime.utcnow()
        
        # Get threshold config for this service
        threshold_config = self.failure_thresholds.get(service_name, {'rate': 5, 'window': 600})
        
        # Count recent failures
        window_start = now - timedelta(seconds=threshold_config['window'])
        recent_failures = [
            f for f in self.failure_history[service_name] 
            if f.timestamp >= window_start
        ]
        
        # Check if we've exceeded the threshold
        if len(recent_failures) >= threshold_config['rate']:
            await self._send_failure_alert(service_name, recent_failures, threshold_config)
        
        # Critical severity always triggers immediate alert
        if latest_failure.severity == FailureSeverity.CRITICAL:
            await self._send_critical_alert(service_name, latest_failure)

    async def _send_failure_alert(self, service_name: str, failures: List[APIFailureEvent], threshold_config: Dict):
        """Send alert for repeated failures"""
        alert_key = f"{service_name}_failures"
        
        # Check cooldown to prevent spam
        if self._is_in_cooldown(alert_key, cooldown_minutes=30):
            return
        
        try:
            failure_summary = self._create_failure_summary(failures)
            
            subject = f"🚨 {service_name.title()} API Failure Alert - {len(failures)} failures"
            
            body = f"""
**API Failure Alert**

Service: {service_name.title()}
Failures: {len(failures)} in {threshold_config['window']/60:.1f} minutes
Threshold: {threshold_config['rate']} failures

**Recent Failures:**
{failure_summary}

**Circuit Breaker Status:**
{self._get_circuit_breaker_status(service_name)}

**Recommended Actions:**
1. Check service status and connectivity
2. Review error patterns for root cause
3. Consider manual service restart if needed
4. Monitor for recovery

System Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}
            """
            
            # Send to admin email
            await self.email_service.send_admin_alert(
                subject=subject,
                message=body,
                alert_type="api_failure"
            )
            
            # Record cooldown
            self.alert_cooldown[alert_key] = datetime.utcnow()
            
            logger.error(f"📧 API failure alert sent for {service_name}")
            
        except Exception as e:
            logger.error(f"Failed to send API failure alert: {e}")

    async def _send_critical_alert(self, service_name: str, failure: APIFailureEvent):
        """Send immediate alert for critical failures"""
        alert_key = f"{service_name}_critical"
        
        # Critical alerts have shorter cooldown
        if self._is_in_cooldown(alert_key, cooldown_minutes=5):
            return
        
        try:
            subject = f"🔥 CRITICAL: {service_name.title()} API Failure"
            
            body = f"""
**CRITICAL API FAILURE**

Service: {service_name.title()}
Operation: {', '.join(failure.affected_operations)}
Error: {failure.error_message}
Time: {failure.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}

**Circuit Breaker Status:**
{self._get_circuit_breaker_status(service_name)}

**IMMEDIATE ACTION REQUIRED:**
This is a critical failure that may impact user transactions.
Please investigate immediately.

System Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}
            """
            
            await self.email_service.send_admin_alert(
                subject=subject,
                message=body,
                alert_type="critical_failure",
                priority="high"
            )
            
            self.alert_cooldown[alert_key] = datetime.utcnow()
            logger.critical(f"🔥 Critical API failure alert sent for {service_name}")
            
        except Exception as e:
            logger.error(f"Failed to send critical failure alert: {e}")

    def _create_failure_summary(self, failures: List[APIFailureEvent]) -> str:
        """Create human-readable failure summary"""
        summary_lines = []
        
        for i, failure in enumerate(failures[-10:], 1):  # Last 10 failures
            time_str = failure.timestamp.strftime('%H:%M:%S')
            summary_lines.append(
                f"{i}. {time_str} - {failure.failure_type}: {failure.error_message[:100]}"
            )
        
        return '\n'.join(summary_lines)

    def _get_circuit_breaker_status(self, service_name: str) -> str:
        """Get formatted circuit breaker status"""
        breaker = circuit_breakers.get(service_name)
        if not breaker:
            return f"No circuit breaker configured for {service_name}"
        
        state = breaker.get_state()
        return f"""
State: {state['state'].upper()}
Failures: {state['failure_count']}
Success Rate: {state['stats']['successful_calls']}/{state['stats']['total_calls']}
        """.strip()

    def _is_in_cooldown(self, alert_key: str, cooldown_minutes: int) -> bool:
        """Check if alert is in cooldown period"""
        last_alert = self.alert_cooldown.get(alert_key)
        if not last_alert:
            return False
        
        cooldown_end = last_alert + timedelta(minutes=cooldown_minutes)
        return datetime.utcnow() < cooldown_end

    async def get_failure_statistics(self, hours: int = 24) -> Dict[str, Any]:
        """Get failure statistics for monitoring dashboard"""
        cutoff_time = datetime.utcnow() - timedelta(hours=hours)
        stats = {}
        
        for service_name, failures in self.failure_history.items():
            recent_failures = [f for f in failures if f.timestamp >= cutoff_time]
            
            if recent_failures:
                severity_counts = defaultdict(int)
                for failure in recent_failures:
                    severity_counts[failure.severity.value] += 1
                
                stats[service_name] = {
                    'total_failures': len(recent_failures),
                    'severity_breakdown': dict(severity_counts),
                    'latest_failure': recent_failures[-1].timestamp.isoformat(),
                    'circuit_breaker_state': circuit_breakers.get(service_name, {}).get('state', 'unknown')
                }
        
        return stats

    async def reset_failure_history(self, service_name: Optional[str] = None):
        """Reset failure history (admin function)"""
        if service_name:
            self.failure_history[service_name].clear()
            logger.info(f"Cleared failure history for {service_name}")
        else:
            self.failure_history.clear()
            logger.info("Cleared all failure history")


# Global instance
silent_failure_detector = SilentAPIFailureDetector()


# Convenience function for easy integration
async def record_api_failure(service_name: str, operation: str, error: Exception, metadata: Dict = None):
    """Record API failure for monitoring"""
    await silent_failure_detector.record_failure(service_name, operation, error, metadata)


# Context manager for automatic failure detection
class APIFailureContext:
    """Context manager for automatic API failure detection"""
    
    def __init__(self, service_name: str, operation: str, metadata: Dict = None):
        self.service_name = service_name
        self.operation = operation
        self.metadata = metadata or {}
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            await record_api_failure(self.service_name, self.operation, exc_val, self.metadata)
        return False  # Don't suppress exceptions