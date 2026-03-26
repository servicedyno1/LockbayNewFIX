"""
Comprehensive Intrusion Detection System for Telegram Escrow Bot
Monitors suspicious patterns, anomalies, and potential security threats
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from enum import Enum
import json
from collections import defaultdict, deque
from telegram import Update
from telegram.ext import ContextTypes
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text, func
from database import async_managed_session
from utils.callback_utils import safe_answer_callback_query
import re

logger = logging.getLogger(__name__)


class ThreatLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ThreatCategory(Enum):
    BRUTE_FORCE = "brute_force"
    RATE_LIMITING = "rate_limiting"
    INJECTION_ATTEMPT = "injection_attempt"
    PRIVILEGE_ESCALATION = "privilege_escalation"
    SUSPICIOUS_BEHAVIOR = "suspicious_behavior"
    ACCOUNT_TAKEOVER = "account_takeover"
    FINANCIAL_FRAUD = "financial_fraud"
    DATA_BREACH = "data_breach"


@dataclass
class SecurityEvent:
    """Represents a security event detected by the IDS"""

    event_id: str
    timestamp: datetime
    user_id: Optional[int]
    threat_category: ThreatCategory
    threat_level: ThreatLevel
    description: str
    source_ip: Optional[str] = None
    user_agent: Optional[str] = None
    additional_data: Dict[str, Any] = field(default_factory=dict)
    action_taken: Optional[str] = None
    false_positive: bool = False


class IntrusionDetectionSystem:
    """Advanced Intrusion Detection System with real-time monitoring"""

    def __init__(self):
        self.failed_logins: Dict[int, List[datetime]] = defaultdict(list)
        self.rate_violations: Dict[int, int] = defaultdict(int)
        self.suspicious_commands: Dict[int, List[Tuple[str, datetime]]] = defaultdict(
            list
        )
        self.unusual_amounts: Dict[int, List[float]] = defaultdict(list)
        self.admin_access_attempts: List[Tuple[int, datetime, str]] = []
        self.recent_events: deque = deque(maxlen=1000)  # Store last 1000 events

        # Threat patterns
        self.injection_patterns = [
            r"<script.*?>.*?</script>",
            r"javascript:",
            r"eval\s*\(",
            r"document\.cookie",
            r"onload\s*=",
            r"onerror\s*=",
            r"union\s+select",
            r"drop\s+table",
            r"insert\s+into",
            r"delete\s+from",
            r"--\s*$",
            r"/\*.*?\*/",
            r"xp_cmdshell",
            r"sp_executesql",
        ]

        # Suspicious filename patterns
        self.malicious_files = [
            r"\.php$",
            r"\.asp$",
            r"\.jsp$",
            r"\.exe$",
            r"\.bat$",
            r"\.cmd$",
            r"\.sh$",
            r"\.py$",
            r"\.js$",
            r"\.vbs$",
        ]

        # Financial thresholds for monitoring
        self.max_daily_volume = 50000.0  # $50k per day
        self.max_single_transaction = 10000.0  # $10k per transaction
        self.max_cashout_frequency = 10  # 10 per day

    async def analyze_request(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        command: str,
        session: AsyncSession,
    ) -> Optional[SecurityEvent]:
        """Analyze incoming request for security threats"""
        try:
            user_id = update.effective_user.id if update.effective_user else None
            current_time = datetime.utcnow()

            # Check multiple threat vectors
            threats = []

            # 1. Rate limiting violations
            threat = await self._check_rate_violations(user_id, command, current_time)
            if threat:
                threats.append(threat)

            # 2. Injection attempts
            threat = await self._check_injection_attempts(update, command)
            if threat:
                threats.append(threat)

            # 3. Privilege escalation attempts
            threat = await self._check_privilege_escalation(user_id, command, session)
            if threat:
                threats.append(threat)

            # 4. Suspicious behavioral patterns
            threat = await self._check_suspicious_behavior(
                user_id, command, current_time, session
            )
            if threat:
                threats.append(threat)

            # 5. Financial fraud indicators
            threat = await self._check_financial_anomalies(user_id, command, session)
            if threat:
                threats.append(threat)

            # Return highest priority threat
            if threats:
                return max(
                    threats, key=lambda t: self._get_threat_priority(t.threat_level)
                )

            return None

        except Exception as e:
            logger.error(f"Error in intrusion detection analysis: {e}")
            return None

    async def _check_rate_violations(
        self, user_id: Optional[int], command: str, current_time: datetime
    ) -> Optional[SecurityEvent]:
        """Check for rate limiting violations"""
        if not user_id:
            return None

        # Track command frequency
        user_commands = self.suspicious_commands[user_id]

        # Clean old entries (last hour)
        cutoff = current_time - timedelta(hours=1)
        user_commands[:] = [(cmd, ts) for cmd, ts in user_commands if ts > cutoff]

        # Add current command
        user_commands.append((command, current_time))

        # Check for excessive rate
        recent_commands = [
            cmd for cmd, ts in user_commands if ts > current_time - timedelta(minutes=5)
        ]

        if len(recent_commands) > 50:  # More than 50 commands in 5 minutes
            return SecurityEvent(
                event_id=f"rate_{user_id}_{int(current_time.timestamp())}",
                timestamp=current_time,
                user_id=user_id,
                threat_category=ThreatCategory.RATE_LIMITING,
                threat_level=ThreatLevel.HIGH,
                description=f"Excessive command rate: {len(recent_commands)} commands in 5 minutes",
                additional_data={
                    "commands": recent_commands,
                    "command_count": len(recent_commands),
                },
            )

        return None

    async def _check_injection_attempts(
        self, update: Update, command: str
    ) -> Optional[SecurityEvent]:
        """Check for injection attack attempts"""
        text_to_check = []

        # Collect text from various sources
        if update.message and update.message.text:
            text_to_check.append(update.message.text)
        if update.callback_query and update.callback_query.data:
            text_to_check.append(update.callback_query.data)

        # Check for injection patterns
        for check_text in text_to_check:
            for pattern in self.injection_patterns:
                if re.search(pattern, check_text, re.IGNORECASE):
                    return SecurityEvent(
                        event_id=f"injection_{int(datetime.utcnow().timestamp())}",
                        timestamp=datetime.utcnow(),
                        user_id=(
                            update.effective_user.id if update.effective_user else None
                        ),
                        threat_category=ThreatCategory.INJECTION_ATTEMPT,
                        threat_level=ThreatLevel.CRITICAL,
                        description=f"Injection pattern detected: {pattern}",
                        additional_data={
                            "pattern": pattern,
                            "text": text[:100],
                            "command": command,
                        },
                    )

        return None

    async def _check_privilege_escalation(
        self, user_id: Optional[int], command: str, session: AsyncSession
    ) -> Optional[SecurityEvent]:
        """Check for privilege escalation attempts"""
        if not user_id:
            return None

        # Check for admin command attempts by non-admin users
        admin_commands = [
            "admin_",
            "approve_",
            "reject_",
            "force_",
            "override_",
            "delete_user",
            "ban_user",
            "system_",
            "debug_",
        ]

        if any(cmd in command.lower() for cmd in admin_commands):
            # Check if user is actually an admin
            try:
                from config import Config

                admin_ids = getattr(Config, "ADMIN_USER_IDS", [])

                if user_id not in admin_ids:
                    return SecurityEvent(
                        event_id=f"privesc_{user_id}_{int(datetime.utcnow().timestamp())}",
                        timestamp=datetime.utcnow(),
                        user_id=user_id,
                        threat_category=ThreatCategory.PRIVILEGE_ESCALATION,
                        threat_level=ThreatLevel.HIGH,
                        description=f"Non-admin user attempted admin command: {command}",
                        additional_data={"command": command, "is_admin": False},
                    )

            except Exception as e:
                logger.warning(f"Error checking admin status: {e}")

        return None

    async def _check_suspicious_behavior(
        self,
        user_id: Optional[int],
        command: str,
        current_time: datetime,
        session: AsyncSession,
    ) -> Optional[SecurityEvent]:
        """Check for suspicious behavioral patterns"""
        if not user_id:
            return None

        try:
            # Check for rapid account creation and immediate high-value operations
            from models import User, Escrow
            from sqlalchemy import select

            stmt = select(User).where(User.id == user_id)
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()

            if user and user.created_at is not None:
                account_age = current_time - user.created_at

                # New account (< 24 hours) attempting high-value operations
                if account_age.total_seconds() < timedelta(hours=24).total_seconds():
                    high_value_commands = ["create_escrow", "withdraw", "admin_"]
                    if any(cmd in command for cmd in high_value_commands):
                        return SecurityEvent(
                            event_id=f"newacct_{user_id}_{int(current_time.timestamp())}",
                            timestamp=current_time,
                            user_id=user_id,
                            threat_category=ThreatCategory.SUSPICIOUS_BEHAVIOR,
                            threat_level=ThreatLevel.MEDIUM,
                            description=f"New account ({account_age.hours}h old) attempting high-value operation",
                            additional_data={
                                "account_age_hours": account_age.total_seconds() / 3600,
                                "command": command,
                            },
                        )

                # Check for unusual escrow patterns
                from sqlalchemy import func, or_
                escrow_stmt = select(func.count()).select_from(Escrow).where(
                    or_(Escrow.buyer_id == user_id, Escrow.seller_id == user_id),
                    Escrow.created_at > current_time - timedelta(hours=24)
                )
                escrow_result = await session.execute(escrow_stmt)
                recent_escrows = escrow_result.scalar_one_or_none()

                if recent_escrows is not None and recent_escrows > 20:  # More than 20 escrows in 24 hours
                    return SecurityEvent(
                        event_id=f"highfreq_{user_id}_{int(current_time.timestamp())}",
                        timestamp=current_time,
                        user_id=user_id,
                        threat_category=ThreatCategory.SUSPICIOUS_BEHAVIOR,
                        threat_level=ThreatLevel.MEDIUM,
                        description=f"Unusually high escrow frequency: {recent_escrows} in 24h",
                        additional_data={"escrow_count_24h": recent_escrows},
                    )

        except Exception as e:
            logger.error(f"Error checking suspicious behavior: {e}")

        return None

    async def _check_financial_anomalies(
        self, user_id: Optional[int], command: str, session: AsyncSession
    ) -> Optional[SecurityEvent]:
        """Check for financial fraud indicators"""
        if not user_id or "withdraw" not in command:
            return None

        try:
            from models import Cashout

            current_time = datetime.utcnow()

            # Check daily cashout volume
            from sqlalchemy import select
            
            daily_stmt = select(func.sum(Cashout.amount)).where(
                Cashout.user_id == user_id,
                Cashout.created_at > current_time - timedelta(days=1)
            )
            daily_result = await session.execute(daily_stmt)
            daily_cashouts = daily_result.scalar_one_or_none() or 0

            if daily_cashouts > self.max_daily_volume:
                return SecurityEvent(
                    event_id=f"highvol_{user_id}_{int(current_time.timestamp())}",
                    timestamp=current_time,
                    user_id=user_id,
                    threat_category=ThreatCategory.FINANCIAL_FRAUD,
                    threat_level=ThreatLevel.HIGH,
                    description=f"Excessive daily cashout volume: ${daily_cashouts:,.2f}",
                    additional_data={
                        "daily_volume": daily_cashouts,
                        "threshold": self.max_daily_volume,
                    },
                )

            # Check cashout frequency
            count_stmt = select(func.count()).select_from(Cashout).where(
                Cashout.user_id == user_id,
                Cashout.created_at > current_time - timedelta(days=1)
            )
            count_result = await session.execute(count_stmt)
            cashout_count = count_result.scalar_one_or_none() or 0

            if cashout_count > self.max_cashout_frequency:
                return SecurityEvent(
                    event_id=f"freqwd_{user_id}_{int(current_time.timestamp())}",
                    timestamp=current_time,
                    user_id=user_id,
                    threat_category=ThreatCategory.FINANCIAL_FRAUD,
                    threat_level=ThreatLevel.MEDIUM,
                    description=f"Excessive cashout frequency: {cashout_count} cashouts in 24h",
                    additional_data={
                        "cashout_count": cashout_count,
                        "threshold": self.max_cashout_frequency,
                    },
                )

        except Exception as e:
            logger.error(f"Error checking financial anomalies: {e}")

        return None

    def _get_threat_priority(self, level: ThreatLevel) -> int:
        """Get numeric priority for threat level"""
        priorities = {
            ThreatLevel.LOW: 1,
            ThreatLevel.MEDIUM: 2,
            ThreatLevel.HIGH: 3,
            ThreatLevel.CRITICAL: 4,
        }
        return priorities.get(level, 0)

    async def log_security_event(self, event: SecurityEvent, session: AsyncSession):
        """Log security event to database and take appropriate action"""
        try:
            # Store event in recent events
            self.recent_events.append(event)

            # Log to database
            await self._store_security_event(event, session)

            # Take automated action based on threat level
            await self._take_automated_action(event, session)

            # Alert administrators for high/critical threats
            if event.threat_level in [ThreatLevel.HIGH, ThreatLevel.CRITICAL]:
                await self._alert_administrators(event)

        except Exception as e:
            logger.error(f"Error logging security event: {e}")

    async def _store_security_event(self, event: SecurityEvent, session: AsyncSession):
        """Store security event in database"""
        try:
            await session.execute(
                text(
                    """
                INSERT INTO security_events 
                (event_id, timestamp, user_id, threat_category, threat_level, 
                 description, source_ip, user_agent, additional_data, action_taken)
                VALUES 
                (:event_id, :timestamp, :user_id, :threat_category, :threat_level,
                 :description, :source_ip, :user_agent, :additional_data, :action_taken)
            """
                ),
                {
                    "event_id": event.event_id,
                    "timestamp": event.timestamp,
                    "user_id": event.user_id,
                    "threat_category": event.threat_category.value,
                    "threat_level": event.threat_level.value,
                    "description": event.description,
                    "source_ip": event.source_ip,
                    "user_agent": event.user_agent,
                    "additional_data": json.dumps(event.additional_data),
                    "action_taken": event.action_taken,
                },
            )
            await session.commit()

        except Exception as e:
            logger.error(f"Error storing security event in database: {e}")
            await session.rollback()

    async def _take_automated_action(self, event: SecurityEvent, session: AsyncSession):
        """Take automated action based on threat level"""
        try:
            if event.threat_level == ThreatLevel.CRITICAL:
                # Temporarily disable user account
                if event.user_id:
                    await self._temporarily_disable_user(event.user_id, session)
                    event.action_taken = "User temporarily disabled"

            elif event.threat_level == ThreatLevel.HIGH:
                # Increase rate limiting for user
                if event.user_id:
                    self.rate_violations[event.user_id] += 5
                    event.action_taken = "Rate limiting increased"

            elif event.threat_level == ThreatLevel.MEDIUM:
                # Log for review
                event.action_taken = "Logged for review"

        except Exception as e:
            logger.error(f"Error taking automated action: {e}")

    async def _temporarily_disable_user(self, user_id: int, session: AsyncSession):
        """Temporarily disable user account for security"""
        try:
            await session.execute(
                text(
                    """
                UPDATE users 
                SET is_temporarily_disabled = true, 
                    disabled_until = :disabled_until,
                    disabled_reason = 'Security threat detected'
                WHERE id = :user_id
            """
                ),
                {
                    "user_id": user_id,
                    "disabled_until": datetime.utcnow() + timedelta(hours=24),
                },
            )
            await session.commit()

        except Exception as e:
            logger.error(f"Error disabling user {user_id}: {e}")
            await session.rollback()

    async def _alert_administrators(self, event: SecurityEvent):
        """Alert administrators about high-priority security events"""
        try:
            from config import Config
            from telegram import Bot

            if not hasattr(Config, "BOT_TOKEN") or not hasattr(
                Config, "ADMIN_USER_IDS"
            ):
                return

            bot_token = getattr(Config, "BOT_TOKEN", None)
            admin_ids = getattr(Config, "ADMIN_USER_IDS", [])
            
            if not bot_token or not admin_ids:
                return
            
            bot = Bot(bot_token)
            admin_message = f"""
🚨 SECURITY ALERT

Threat Level: {event.threat_level.value.upper()}
Category: {event.threat_category.value.replace('_', ' ').title()}
Time: {event.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}
User ID: {event.user_id or 'Unknown'}

Description: {event.description}

Action Taken: {event.action_taken or 'None'}

Event ID: {event.event_id}
"""

            for admin_id in admin_ids:
                try:
                    await bot.send_message(
                        chat_id=admin_id, text=admin_message, parse_mode="Markdown"
                    )
                except Exception as e:
                    logger.error(f"Error sending alert to admin {admin_id}: {e}")

        except Exception as e:
            logger.error(f"Error alerting administrators: {e}")

    def get_threat_summary(self, hours: int = 24) -> Dict[str, Any]:
        """Get summary of threats detected in specified time period"""
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        recent_threats = [e for e in self.recent_events if e.timestamp > cutoff]

        summary = {
            "total_events": len(recent_threats),
            "by_level": defaultdict(int),
            "by_category": defaultdict(int),
            "by_user": defaultdict(int),
            "critical_events": [],
        }

        for event in recent_threats:
            summary["by_level"][event.threat_level.value] += 1
            summary["by_category"][event.threat_category.value] += 1
            if event.user_id:
                summary["by_user"][event.user_id] += 1

            if event.threat_level == ThreatLevel.CRITICAL:
                summary["critical_events"].append(
                    {
                        "event_id": event.event_id,
                        "timestamp": event.timestamp.isoformat(),
                        "description": event.description,
                        "user_id": event.user_id,
                    }
                )

        return dict(summary)


# Global IDS instance
intrusion_detection_system = IntrusionDetectionSystem()


# Decorator for handlers to enable IDS monitoring
def monitor_security(func):
    """Decorator to enable security monitoring for handlers"""
    from functools import wraps

    @wraps(func)
    async def wrapper(
        update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs
    ):
        session = None
        try:
            async with async_managed_session() as session:
                # Analyze request for threats
                command = func.__name__
                threat = await intrusion_detection_system.analyze_request(
                    update, context, command, session
                )

                if threat:
                    # Log the security event
                    await intrusion_detection_system.log_security_event(threat, session)

                    # Block critical threats
                    if threat.threat_level == ThreatLevel.CRITICAL:
                        logger.warning(f"Blocking critical threat: {threat.description}")
                        if update.message:
                            await update.message.reply_text(
                                "🚨 Security violation detected. Access denied."
                            )
                        elif update.callback_query:
                            await safe_answer_callback_query(
                                update.callback_query,
                                "Security violation detected",
                                show_alert=True
                            )
                        return

                # Execute original handler
                return await func(update, context, *args, **kwargs)

        except Exception as e:
            logger.error(f"Error in security monitoring wrapper: {e}")
            # Continue with original handler on monitoring errors
            return await func(update, context, *args, **kwargs)
        finally:
            if session:
                await session.close()

    return wrapper
