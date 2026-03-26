"""
Session Monitoring and Timeout Management for Onboarding Sessions

This module provides comprehensive monitoring of onboarding session lifecycle,
timeout detection, and proactive alerting for session-related issues.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from database import async_engine
from models.onboarding import OnboardingSession
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_ as sql_and
from services.consolidated_notification_service import ConsolidatedNotificationService

logger = logging.getLogger(__name__)

@dataclass
class SessionMetrics:
    """Session lifecycle metrics for monitoring"""
    total_sessions: int = 0
    active_sessions: int = 0
    expired_sessions: int = 0
    expiring_soon: int = 0  # Sessions expiring within next hour
    avg_session_duration_hours: float = 0.0
    sessions_by_step: Optional[Dict[str, int]] = None
    oldest_active_session_age_hours: float = 0.0
    
    def __post_init__(self):
        if self.sessions_by_step is None:
            self.sessions_by_step = {}

@dataclass
class SessionAlert:
    """Session-related alerts for proactive monitoring"""
    alert_type: str  # 'timeout_warning', 'mass_expiry', 'stuck_sessions', 'zero_sessions'
    severity: str   # 'low', 'medium', 'high', 'critical'
    message: str
    user_count: int = 0
    session_count: int = 0
    affected_users: Optional[List[int]] = None
    timestamp: Optional[datetime] = None
    
    def __post_init__(self):
        if self.affected_users is None:
            self.affected_users = []
        if self.timestamp is None:
            self.timestamp = datetime.utcnow()

class SessionMonitoringService:
    """Comprehensive session monitoring and timeout management"""
    
    DEFAULT_WARNING_THRESHOLD_HOURS = 2  # Alert when sessions expire in 2 hours
    MASS_EXPIRY_THRESHOLD = 10  # Alert when >10 sessions expire soon
    STUCK_SESSION_THRESHOLD_HOURS = 20  # Alert when sessions are stuck for >20 hours
    
    @classmethod
    async def get_session_metrics(cls) -> SessionMetrics:
        """Get comprehensive session metrics for monitoring dashboard"""
        try:
            async with AsyncSession(async_engine) as session:
                now = datetime.utcnow()
                one_hour_from_now = now + timedelta(hours=1)
                
                # Get all onboarding sessions
                result = await session.execute(select(OnboardingSession))
                all_sessions = list(result.scalars())
                
                if not all_sessions:
                    logger.info("📊 SESSION METRICS: No onboarding sessions found")
                    return SessionMetrics()
                
                metrics = SessionMetrics(total_sessions=len(all_sessions))
                
                session_durations = []
                active_session_ages = []
                
                for onb_session in all_sessions:
                    # Categorize by expiry status
                    if onb_session.expires_at > now:
                        metrics.active_sessions += 1
                        
                        # Check if expiring soon
                        if onb_session.expires_at <= one_hour_from_now:
                            metrics.expiring_soon += 1
                        
                        # Track active session age
                        if onb_session.created_at:
                            age_hours = (now - onb_session.created_at).total_seconds() / 3600
                            active_session_ages.append(age_hours)
                    else:
                        metrics.expired_sessions += 1
                    
                    # Track session duration for expired sessions
                    if onb_session.created_at and onb_session.expires_at <= now:
                        duration = (onb_session.expires_at - onb_session.created_at).total_seconds() / 3600
                        session_durations.append(duration)
                    
                    # Count by step
                    step = onb_session.current_step
                    metrics.sessions_by_step[step] = metrics.sessions_by_step.get(step, 0) + 1
                
                # Calculate averages
                if session_durations:
                    metrics.avg_session_duration_hours = sum(session_durations) / len(session_durations)
                
                if active_session_ages:
                    metrics.oldest_active_session_age_hours = max(active_session_ages)
                
                logger.info(f"📊 SESSION METRICS: {metrics.active_sessions} active, {metrics.expired_sessions} expired, {metrics.expiring_soon} expiring soon")
                return metrics
                
        except Exception as e:
            logger.error(f"Error getting session metrics: {e}")
            return SessionMetrics()
    
    @classmethod
    async def check_session_health(cls) -> List[SessionAlert]:
        """Check session health and generate alerts for issues"""
        alerts = []
        
        try:
            metrics = await cls.get_session_metrics()
            datetime.utcnow()
            
            # Alert 1: Sessions expiring soon (timeout warning)
            if metrics.expiring_soon > 0:
                severity = "medium" if metrics.expiring_soon < cls.MASS_EXPIRY_THRESHOLD else "high"
                alerts.append(SessionAlert(
                    alert_type="timeout_warning",
                    severity=severity,
                    message=f"{metrics.expiring_soon} onboarding sessions expiring within 1 hour",
                    session_count=metrics.expiring_soon
                ))
            
            # Alert 2: Mass expiry warning
            if metrics.expiring_soon >= cls.MASS_EXPIRY_THRESHOLD:
                alerts.append(SessionAlert(
                    alert_type="mass_expiry",
                    severity="high",
                    message=f"MASS EXPIRY WARNING: {metrics.expiring_soon} sessions expiring soon",
                    session_count=metrics.expiring_soon
                ))
            
            # Alert 3: Stuck sessions (sessions that are very old but still active)
            if metrics.oldest_active_session_age_hours > cls.STUCK_SESSION_THRESHOLD_HOURS:
                alerts.append(SessionAlert(
                    alert_type="stuck_sessions",
                    severity="medium",
                    message=f"Sessions stuck for {metrics.oldest_active_session_age_hours:.1f} hours",
                    session_count=1
                ))
            
            # Alert 4: Zero active sessions (could indicate system issue)
            if metrics.active_sessions == 0 and metrics.total_sessions > 0:
                alerts.append(SessionAlert(
                    alert_type="zero_sessions",
                    severity="medium",
                    message="No active onboarding sessions found (all expired)",
                    session_count=metrics.total_sessions
                ))
            
            if alerts:
                logger.warning(f"🚨 SESSION HEALTH: Generated {len(alerts)} alerts")
            else:
                logger.info("✅ SESSION HEALTH: All session metrics healthy")
                
            return alerts
            
        except Exception as e:
            logger.error(f"Error checking session health: {e}")
            return []
    
    @classmethod
    async def get_expiring_sessions(cls, hours_ahead: int = 2) -> List[Dict[str, Any]]:
        """Get sessions that will expire within specified hours"""
        try:
            async with AsyncSession(async_engine) as session:
                now = datetime.utcnow()
                expiry_threshold = now + timedelta(hours=hours_ahead)
                
                result = await session.execute(
                    select(OnboardingSession).where(
                        sql_and(
                            OnboardingSession.expires_at > now,
                            OnboardingSession.expires_at <= expiry_threshold
                        )
                    )
                )
                
                expiring_sessions = []
                for onb_session in list(result.scalars()):
                    time_until_expiry = onb_session.expires_at - now
                    hours_remaining = time_until_expiry.total_seconds() / 3600
                    
                    expiring_sessions.append({
                        'session_id': onb_session.id,
                        'user_id': onb_session.user_id,
                        'current_step': onb_session.current_step,
                        'created_at': onb_session.created_at.isoformat() if onb_session.created_at else None,
                        'expires_at': onb_session.expires_at.isoformat(),
                        'hours_remaining': round(hours_remaining, 2),
                        'email': onb_session.email,
                        'has_email': bool(onb_session.email)
                    })
                
                logger.info(f"⏰ Found {len(expiring_sessions)} sessions expiring within {hours_ahead} hours")
                return expiring_sessions
                
        except Exception as e:
            logger.error(f"Error getting expiring sessions: {e}")
            return []
    
    @classmethod
    async def send_session_alert(cls, alert: SessionAlert) -> bool:
        """Send session alert to administrators"""
        try:
            # Format alert message with details
            alert_message = f"""
🚨 **Session Alert: {alert.alert_type.replace('_', ' ').title()}**

**Severity:** {alert.severity.upper()}
**Message:** {alert.message}
**Timestamp:** {alert.timestamp.isoformat()}

**Details:**
- Session Count: {alert.session_count}
- User Count: {alert.user_count}
- Affected Users: {', '.join(map(str, alert.affected_users[:5]))}{'...' if len(alert.affected_users) > 5 else ''}

**Action Required:**
{cls._get_alert_action_recommendation(alert)}
            """.strip()
            
            # Send notification to administrators - simplified version
            try:
                notification_service = ConsolidatedNotificationService()
                result = await notification_service.send_admin_email_notification(
                    category="session_monitoring",
                    title=f"Session Alert: {alert.alert_type.replace('_', ' ').title()}",
                    message=alert_message
                )
                
                if result:
                    logger.info(f"✅ Session alert sent successfully: {alert.alert_type}")
                    return True
                else:
                    logger.error(f"❌ Failed to send session alert: {alert.alert_type}")
                    return False
            except Exception as e:
                logger.error(f"Error sending admin notification: {e}")
                return False
                
        except Exception as e:
            logger.error(f"Error sending session alert: {e}")
            return False
    
    @classmethod
    def _get_alert_action_recommendation(cls, alert: SessionAlert) -> str:
        """Get action recommendations based on alert type"""
        recommendations = {
            "timeout_warning": "Monitor user activity and consider extending sessions if users are actively engaging.",
            "mass_expiry": "Investigate potential system issues causing mass session expiry. Consider server restarts or database connectivity issues.",
            "stuck_sessions": "Review sessions stuck in intermediate steps. Users may need assistance completing onboarding.",
            "zero_sessions": "Check if new user registration is working correctly. This could indicate a system-wide onboarding issue."
        }
        
        return recommendations.get(alert.alert_type, "Review session logs and consider manual intervention if needed.")
    
    @classmethod
    async def cleanup_expired_sessions(cls, days_old: int = 7) -> int:
        """Clean up expired sessions older than specified days"""
        try:
            async with AsyncSession(async_engine) as db_session:
                cutoff_date = datetime.utcnow() - timedelta(days=days_old)
                
                # Find expired sessions older than cutoff
                result = await db_session.execute(
                    select(OnboardingSession).where(
                        sql_and(
                            OnboardingSession.expires_at <= cutoff_date,
                            OnboardingSession.expires_at < datetime.utcnow()
                        )
                    )
                )
                
                sessions_to_delete = list(result.scalars())
                deleted_count = len(sessions_to_delete)
                
                if deleted_count > 0:
                    # Delete the sessions
                    for session_obj in sessions_to_delete:
                        await db_session.delete(session_obj)
                    
                    await db_session.commit()
                    logger.info(f"🧹 Cleaned up {deleted_count} expired sessions older than {days_old} days")
                else:
                    logger.info(f"🧹 No expired sessions found older than {days_old} days")
                
                return deleted_count
                
        except Exception as e:
            logger.error(f"Error cleaning up expired sessions: {e}")
            return 0

# Monitoring task runner
class SessionMonitoringTask:
    """Background task for continuous session monitoring"""
    
    def __init__(self, check_interval_minutes: int = 30):
        self.check_interval_minutes = check_interval_minutes
        self.running = False
        self.task = None
    
    async def start(self):
        """Start the session monitoring task"""
        if self.running:
            logger.warning("Session monitoring task already running")
            return
        
        self.running = True
        self.task = asyncio.create_task(self._monitoring_loop())
        logger.info(f"🔄 Started session monitoring task (check every {self.check_interval_minutes} minutes)")
    
    async def stop(self):
        """Stop the session monitoring task"""
        if not self.running:
            return
        
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        
        logger.info("🛑 Stopped session monitoring task")
    
    async def _monitoring_loop(self):
        """Main monitoring loop"""
        try:
            while self.running:
                try:
                    # Check session health and send alerts
                    alerts = await SessionMonitoringService.check_session_health()
                    
                    # Send any critical or high-severity alerts
                    for alert in alerts:
                        if alert.severity in ["high", "critical"]:
                            await SessionMonitoringService.send_session_alert(alert)
                    
                    # Get and log current metrics
                    metrics = await SessionMonitoringService.get_session_metrics()
                    logger.info(f"📊 SESSION MONITOR: {metrics.active_sessions} active, {metrics.expiring_soon} expiring soon")
                    
                except Exception as e:
                    logger.error(f"Error in session monitoring loop: {e}")
                
                # Wait for next check
                await asyncio.sleep(self.check_interval_minutes * 60)
                
        except asyncio.CancelledError:
            logger.info("Session monitoring task cancelled")
        except Exception as e:
            logger.error(f"Fatal error in session monitoring: {e}")

# Global monitoring task instance
session_monitor = SessionMonitoringTask()