"""
Admin Alert System
Monitors unauthorized access attempts and security events
"""

import logging
from typing import Dict, List
from datetime import datetime, timedelta
from dataclasses import dataclass, field
import asyncio
from collections import defaultdict

logger = logging.getLogger(__name__)

@dataclass
class SecurityAlert:
    """Represents a security alert"""
    user_id: int
    alert_type: str
    message: str
    timestamp: datetime
    severity: str = "medium"
    context: Dict = field(default_factory=dict)

class AdminAlertSystem:
    """
    Monitor and alert on security events and unauthorized access attempts
    """
    
    def __init__(self):
        # Track security events
        self._security_events: Dict[int, List[SecurityAlert]] = defaultdict(list)
        self._alert_thresholds = {
            'admin_access_attempts': 3,  # Alert after 3 failed attempts
            'rapid_requests': 10,        # Alert after 10 requests in window
            'suspicious_patterns': 5     # Alert after 5 suspicious events
        }
        self._alert_windows = {
            'admin_access_attempts': timedelta(minutes=15),
            'rapid_requests': timedelta(minutes=5),
            'suspicious_patterns': timedelta(hours=1)
        }
        
        # Start cleanup task
        asyncio.create_task(self._cleanup_task())
    
    async def log_admin_access_attempt(self, user_id: int, success: bool, context: Dict = None):
        """Log admin access attempt"""
        
        if success:
            alert_type = "admin_access_success"
            message = f"Successful admin access by user {user_id}"
            severity = "info"
        else:
            alert_type = "admin_access_failure"
            message = f"Failed admin access attempt by user {user_id}"
            severity = "warning"
        
        alert = SecurityAlert(
            user_id=user_id,
            alert_type=alert_type,
            message=message,
            timestamp=datetime.now(),
            severity=severity,
            context=context or {}
        )
        
        self._security_events[user_id].append(alert)
        
        # Check if we should trigger high-severity alerts
        if not success:
            await self._check_admin_access_threshold(user_id)
        
        logger.info(f"Security event logged: {message}")
    
    async def log_rapid_requests(self, user_id: int, request_count: int, timeframe: str):
        """Log rapid request patterns"""
        
        alert = SecurityAlert(
            user_id=user_id,
            alert_type="rapid_requests",
            message=f"User {user_id} made {request_count} requests in {timeframe}",
            timestamp=datetime.now(),
            severity="warning",
            context={"request_count": request_count, "timeframe": timeframe}
        )
        
        self._security_events[user_id].append(alert)
        await self._check_rapid_request_threshold(user_id)
    
    async def log_suspicious_activity(self, user_id: int, activity_type: str, details: Dict = None):
        """Log suspicious user activity"""
        
        alert = SecurityAlert(
            user_id=user_id,
            alert_type="suspicious_activity",
            message=f"Suspicious activity detected: {activity_type} by user {user_id}",
            timestamp=datetime.now(),
            severity="medium",
            context=details or {}
        )
        
        self._security_events[user_id].append(alert)
        await self._check_suspicious_pattern_threshold(user_id)
    
    async def _check_admin_access_threshold(self, user_id: int):
        """Check if user has exceeded admin access attempt threshold"""
        
        window = self._alert_windows['admin_access_attempts']
        threshold = self._alert_thresholds['admin_access_attempts']
        cutoff_time = datetime.now() - window
        
        recent_failures = [
            event for event in self._security_events[user_id]
            if event.alert_type == "admin_access_failure" and event.timestamp > cutoff_time
        ]
        
        if len(recent_failures) >= threshold:
            await self._trigger_high_priority_alert(
                user_id=user_id,
                alert_type="admin_access_threshold_exceeded",
                message=f"User {user_id} exceeded admin access attempt threshold ({len(recent_failures)} attempts in {window})",
                context={"attempt_count": len(recent_failures), "window": str(window)}
            )
    
    async def _check_rapid_request_threshold(self, user_id: int):
        """Check if user has exceeded rapid request threshold"""
        
        window = self._alert_windows['rapid_requests']
        threshold = self._alert_thresholds['rapid_requests']
        cutoff_time = datetime.now() - window
        
        recent_requests = [
            event for event in self._security_events[user_id]
            if event.alert_type == "rapid_requests" and event.timestamp > cutoff_time
        ]
        
        if len(recent_requests) >= threshold:
            await self._trigger_high_priority_alert(
                user_id=user_id,
                alert_type="rapid_request_threshold_exceeded",
                message=f"User {user_id} exceeded rapid request threshold",
                context={"request_events": len(recent_requests)}
            )
    
    async def _check_suspicious_pattern_threshold(self, user_id: int):
        """Check if user has exceeded suspicious activity threshold"""
        
        window = self._alert_windows['suspicious_patterns']
        threshold = self._alert_thresholds['suspicious_patterns']
        cutoff_time = datetime.now() - window
        
        recent_suspicious = [
            event for event in self._security_events[user_id]
            if event.alert_type == "suspicious_activity" and event.timestamp > cutoff_time
        ]
        
        if len(recent_suspicious) >= threshold:
            await self._trigger_high_priority_alert(
                user_id=user_id,
                alert_type="suspicious_pattern_detected",
                message=f"Suspicious behavior pattern detected for user {user_id}",
                context={"suspicious_events": len(recent_suspicious)}
            )
    
    async def _trigger_high_priority_alert(self, user_id: int, alert_type: str, message: str, context: Dict = None):
        """Trigger high-priority security alert"""
        
        alert = SecurityAlert(
            user_id=user_id,
            alert_type=alert_type,
            message=message,
            timestamp=datetime.now(),
            severity="high",
            context=context or {}
        )
        
        self._security_events[user_id].append(alert)
        
        # Log the high-priority alert
        logger.critical(f"🚨 HIGH PRIORITY SECURITY ALERT: {message}")
        
        # Here you could integrate with external alerting systems:
        # - Send email alerts to admins
        # - Send Telegram notifications to admin chat
        # - Integrate with monitoring systems (Datadog, New Relic, etc.)
        
        await self._send_admin_notification(alert)
    
    async def _send_admin_notification(self, alert: SecurityAlert):
        """Send notification to administrators"""
        try:
            # Import here to avoid circular imports
            
            # You could implement various notification methods:
            
            # Method 1: Log to admin monitoring channel
            admin_message = (
                f"🚨 Security Alert\n"
                f"Type: {alert.alert_type}\n"
                f"User: {alert.user_id}\n"
                f"Message: {alert.message}\n"
                f"Time: {alert.timestamp}\n"
                f"Severity: {alert.severity}"
            )
            
            logger.critical(f"ADMIN NOTIFICATION: {admin_message}")
            
            # Method 2: Could send to admin Telegram chat if configured
            # if hasattr(Config, 'ADMIN_CHAT_ID'):
            #     await send_telegram_message(Config.ADMIN_CHAT_ID, admin_message)
            
        except Exception as e:
            logger.error(f"Failed to send admin notification: {e}")
    
    def get_user_security_summary(self, user_id: int, hours: int = 24) -> Dict:
        """Get security summary for a specific user"""
        cutoff_time = datetime.now() - timedelta(hours=hours)
        
        user_events = [
            event for event in self._security_events.get(user_id, [])
            if event.timestamp > cutoff_time
        ]
        
        summary = {
            'user_id': user_id,
            'total_events': len(user_events),
            'events_by_type': {},
            'events_by_severity': {},
            'latest_event': None
        }
        
        for event in user_events:
            # Count by type
            summary['events_by_type'][event.alert_type] = \
                summary['events_by_type'].get(event.alert_type, 0) + 1
            
            # Count by severity
            summary['events_by_severity'][event.severity] = \
                summary['events_by_severity'].get(event.severity, 0) + 1
        
        if user_events:
            summary['latest_event'] = max(user_events, key=lambda e: e.timestamp)
        
        return summary
    
    async def _cleanup_task(self):
        """Background task to clean up old security events"""
        while True:
            try:
                await asyncio.sleep(3600)  # Run every hour
                await self._cleanup_old_events()
            except Exception as e:
                logger.error(f"Error in admin alert cleanup task: {e}")
    
    async def _cleanup_old_events(self):
        """Remove old security events to prevent memory bloat"""
        cutoff_time = datetime.now() - timedelta(days=7)  # Keep 7 days of history
        
        for user_id in list(self._security_events.keys()):
            # Keep only recent events
            recent_events = [
                event for event in self._security_events[user_id]
                if event.timestamp > cutoff_time
            ]
            
            if recent_events:
                self._security_events[user_id] = recent_events
            else:
                del self._security_events[user_id]
        
        logger.debug(f"Security events cleanup completed - monitoring {len(self._security_events)} users")

# Global admin alert system instance
admin_alert_system = AdminAlertSystem()