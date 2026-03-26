"""
Escrow Cancellation Abuse Prevention Service
Prevents malicious users from abusing the cancellation system
"""

import logging
from datetime import datetime, timedelta
from typing import Optional, List, Tuple
from enum import Enum
from dataclasses import dataclass
from decimal import Decimal

logger = logging.getLogger(__name__)


class AbusePattern(Enum):
    """Types of cancellation abuse patterns"""
    EXCESSIVE_CANCELLATIONS = "excessive_cancellations"
    RAPID_FIRE_CANCELLATION = "rapid_fire_cancellation"
    LATE_STAGE_CANCELLATION = "late_stage_cancellation"
    PATTERN_GAMING = "pattern_gaming"
    SELLER_GRIEFING = "seller_griefing"
    AMOUNT_MANIPULATION = "amount_manipulation"
    REPEAT_OFFENDER = "repeat_offender"


class AbuseSeverity(Enum):
    """Severity levels for abuse detection"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class PreventionAction(Enum):
    """Actions to take for abuse prevention"""
    WARN_USER = "warn_user"
    DELAY_CANCELLATION = "delay_cancellation"
    REQUIRE_REASON = "require_reason"
    ESCALATE_ADMIN = "escalate_admin"
    BLOCK_CANCELLATION = "block_cancellation"
    TEMPORARY_RESTRICTION = "temporary_restriction"
    PERMANENT_RESTRICTION = "permanent_restriction"


@dataclass
class CancellationAttempt:
    """Represents a cancellation attempt"""
    user_id: int
    escrow_id: str
    escrow_amount: Decimal
    escrow_status: str
    time_since_creation: timedelta
    time_since_last_activity: timedelta
    seller_has_responded: bool
    payment_confirmed: bool
    reason: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None


@dataclass
class AbuseDetectionResult:
    """Result of abuse detection analysis"""
    is_abuse: bool
    abuse_patterns: List[AbusePattern]
    severity: AbuseSeverity
    prevention_actions: List[PreventionAction]
    message: str
    should_block: bool
    delay_seconds: int = 0
    restriction_hours: int = 0


@dataclass
class UserCancellationHistory:
    """User's cancellation history for pattern analysis"""
    user_id: int
    total_escrows: int
    total_cancellations: int
    cancellations_24h: int
    cancellations_7d: int
    cancellations_30d: int
    last_cancellation_at: Optional[datetime]
    late_stage_cancellations: int
    average_cancellation_time: float  # hours from creation
    seller_complaint_count: int


class EscrowCancellationAbusePreventor:
    """Advanced abuse prevention for escrow cancellations"""
    
    # Abuse detection thresholds
    ABUSE_THRESHOLDS = {
        # Excessive cancellations (percentage of total trades)
        "max_cancellation_rate_24h": 0.6,  # 60% of trades in 24h
        "max_cancellation_rate_7d": 0.4,   # 40% of trades in 7 days
        "max_cancellation_rate_30d": 0.3,  # 30% of trades in 30 days
        
        # Rapid fire cancellations
        "max_cancellations_per_hour": 3,
        "max_cancellations_per_day": 8,
        
        # Late stage cancellation patterns
        "late_stage_threshold_hours": 24,   # Cancel after 24+ hours
        "max_late_stage_cancellations_week": 2,
        
        # Pattern gaming detection
        "min_time_between_cancellations_minutes": 10,
        "suspicious_pattern_count": 5,
        
        # Amount-based abuse
        "high_value_threshold": 1000,  # USD
        "max_high_value_cancellations_week": 2,
    }
    
    # Restriction durations (in hours)
    RESTRICTION_DURATIONS = {
        AbuseSeverity.LOW: 2,
        AbuseSeverity.MEDIUM: 24,
        AbuseSeverity.HIGH: 72,
        AbuseSeverity.CRITICAL: 168  # 1 week
    }
    
    @classmethod
    async def check_cancellation_attempt(
        cls,
        cancellation_attempt: CancellationAttempt
    ) -> AbuseDetectionResult:
        """
        Check if a cancellation attempt shows signs of abuse
        
        Args:
            cancellation_attempt: Details of the cancellation attempt
            
        Returns:
            AbuseDetectionResult with recommended actions
        """
        try:
            # Get user's cancellation history
            user_history = await cls._get_user_cancellation_history(cancellation_attempt.user_id)
            
            # Check for active restrictions
            active_restriction = await cls._check_active_restrictions(cancellation_attempt.user_id)
            if active_restriction:
                return AbuseDetectionResult(
                    is_abuse=True,
                    abuse_patterns=[AbusePattern.REPEAT_OFFENDER],
                    severity=AbuseSeverity.HIGH,
                    prevention_actions=[PreventionAction.BLOCK_CANCELLATION],
                    message=f"User is currently restricted from cancellations until {active_restriction}",
                    should_block=True
                )
            
            # Analyze abuse patterns
            detected_patterns = []
            max_severity = AbuseSeverity.LOW
            
            # 1. Check excessive cancellation rate
            patterns, severity = cls._check_excessive_cancellations(user_history)
            detected_patterns.extend(patterns)
            if severity.value > max_severity.value:
                max_severity = severity
            
            # 2. Check rapid fire cancellations
            patterns, severity = cls._check_rapid_fire_cancellation(user_history)
            detected_patterns.extend(patterns)
            if severity.value > max_severity.value:
                max_severity = severity
            
            # 3. Check late stage cancellation abuse
            patterns, severity = cls._check_late_stage_cancellation(cancellation_attempt, user_history)
            detected_patterns.extend(patterns)
            if severity.value > max_severity.value:
                max_severity = severity
            
            # 4. Check pattern gaming
            patterns, severity = cls._check_pattern_gaming(user_history)
            detected_patterns.extend(patterns)
            if severity.value > max_severity.value:
                max_severity = severity
            
            # 5. Check seller griefing patterns
            patterns, severity = cls._check_seller_griefing(cancellation_attempt, user_history)
            detected_patterns.extend(patterns)
            if severity.value > max_severity.value:
                max_severity = severity
            
            # 6. Check amount manipulation
            patterns, severity = cls._check_amount_manipulation(cancellation_attempt, user_history)
            detected_patterns.extend(patterns)
            if severity.value > max_severity.value:
                max_severity = severity
            
            # Determine prevention actions
            prevention_actions = cls._determine_prevention_actions(detected_patterns, max_severity)
            
            # Create result
            is_abuse = len(detected_patterns) > 0
            should_block = PreventionAction.BLOCK_CANCELLATION in prevention_actions
            delay_seconds = cls._calculate_delay(max_severity)
            restriction_hours = cls._calculate_restriction_duration(max_severity, detected_patterns)
            
            message = cls._generate_prevention_message(detected_patterns, prevention_actions)
            
            # Log abuse detection
            if is_abuse:
                await cls._log_abuse_detection(cancellation_attempt, detected_patterns, max_severity)
            
            return AbuseDetectionResult(
                is_abuse=is_abuse,
                abuse_patterns=detected_patterns,
                severity=max_severity,
                prevention_actions=prevention_actions,
                message=message,
                should_block=should_block,
                delay_seconds=delay_seconds,
                restriction_hours=restriction_hours
            )
            
        except Exception as e:
            logger.error(f"Error checking cancellation abuse: {e}")
            # Fail safe - allow cancellation but log error
            return AbuseDetectionResult(
                is_abuse=False,
                abuse_patterns=[],
                severity=AbuseSeverity.LOW,
                prevention_actions=[],
                message="Abuse check failed - cancellation allowed",
                should_block=False
            )
    
    @classmethod
    async def _get_user_cancellation_history(cls, user_id: int) -> UserCancellationHistory:
        """Get user's cancellation history for analysis"""
        try:
            from database import SessionLocal
            from models import Escrow
            from sqlalchemy import func, and_
            
            session = SessionLocal()
            
            try:
                now = datetime.utcnow()
                
                # Get total escrow counts
                total_escrows = session.query(func.count(Escrow.id)).filter(
                    Escrow.buyer_id == user_id
                ).scalar() or 0
                
                # Get cancellation counts by timeframe
                total_cancellations = session.query(func.count(Escrow.id)).filter(
                    and_(
                        Escrow.buyer_id == user_id,
                        Escrow.status == "cancelled"
                    )
                ).scalar() or 0
                
                cancellations_24h = session.query(func.count(Escrow.id)).filter(
                    and_(
                        Escrow.buyer_id == user_id,
                        Escrow.status == "cancelled",
                        Escrow.updated_at >= now - timedelta(hours=24)
                    )
                ).scalar() or 0
                
                cancellations_7d = session.query(func.count(Escrow.id)).filter(
                    and_(
                        Escrow.buyer_id == user_id,
                        Escrow.status == "cancelled",
                        Escrow.updated_at >= now - timedelta(days=7)
                    )
                ).scalar() or 0
                
                cancellations_30d = session.query(func.count(Escrow.id)).filter(
                    and_(
                        Escrow.buyer_id == user_id,
                        Escrow.status == "cancelled",
                        Escrow.updated_at >= now - timedelta(days=30)
                    )
                ).scalar() or 0
                
                # Get last cancellation time
                last_cancellation = session.query(Escrow.updated_at).filter(
                    and_(
                        Escrow.buyer_id == user_id,
                        Escrow.status == "cancelled"
                    )
                ).order_by(Escrow.updated_at.desc()).first()
                
                last_cancellation_at = last_cancellation[0] if last_cancellation else None
                
                # Calculate late stage cancellations (cancelled after 24+ hours)
                late_stage_cancellations = session.query(func.count(Escrow.id)).filter(
                    and_(
                        Escrow.buyer_id == user_id,
                        Escrow.status == "cancelled",
                        Escrow.updated_at >= now - timedelta(days=7),
                        func.extract('epoch', Escrow.updated_at - Escrow.created_at) >= 24 * 3600
                    )
                ).scalar() or 0
                
                # Calculate average cancellation time
                avg_cancellation_time_result = session.query(
                    func.avg(func.extract('epoch', Escrow.updated_at - Escrow.created_at) / 3600)
                ).filter(
                    and_(
                        Escrow.buyer_id == user_id,
                        Escrow.status == "cancelled",
                        Escrow.updated_at >= now - timedelta(days=30)
                    )
                ).scalar()
                
                average_cancellation_time = float(avg_cancellation_time_result or 0)
                
                # TODO: Add seller complaint count from a complaints table if it exists
                seller_complaint_count = 0
                
                return UserCancellationHistory(
                    user_id=user_id,
                    total_escrows=total_escrows,
                    total_cancellations=total_cancellations,
                    cancellations_24h=cancellations_24h,
                    cancellations_7d=cancellations_7d,
                    cancellations_30d=cancellations_30d,
                    last_cancellation_at=last_cancellation_at,
                    late_stage_cancellations=late_stage_cancellations,
                    average_cancellation_time=average_cancellation_time,
                    seller_complaint_count=seller_complaint_count
                )
                
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"Error getting user cancellation history: {e}")
            # Return empty history if error
            return UserCancellationHistory(
                user_id=user_id,
                total_escrows=0,
                total_cancellations=0,
                cancellations_24h=0,
                cancellations_7d=0,
                cancellations_30d=0,
                last_cancellation_at=None,
                late_stage_cancellations=0,
                average_cancellation_time=0,
                seller_complaint_count=0
            )
    
    @classmethod
    async def _check_active_restrictions(cls, user_id: int) -> Optional[datetime]:
        """Check if user has active cancellation restrictions"""
        try:
            from database import SessionLocal
            
            session = SessionLocal()
            
            try:
                # Check if user has cancellation restriction until a certain time
                # This would require adding a cancellation_restricted_until field to User model
                # For now, return None (no restrictions)
                # TODO: Implement this when adding restriction fields to User model
                return None
                
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"Error checking active restrictions: {e}")
            return None
    
    @classmethod
    def _check_excessive_cancellations(
        cls,
        user_history: UserCancellationHistory
    ) -> Tuple[List[AbusePattern], AbuseSeverity]:
        """Check for excessive cancellation patterns"""
        patterns = []
        severity = AbuseSeverity.LOW
        
        # Calculate cancellation rates
        if user_history.total_escrows > 0:
            rate_24h = user_history.cancellations_24h / max(user_history.total_escrows, 1)
            rate_7d = user_history.cancellations_7d / max(user_history.total_escrows, 1)
            rate_30d = user_history.cancellations_30d / max(user_history.total_escrows, 1)
            
            # Check 24-hour rate
            if rate_24h > cls.ABUSE_THRESHOLDS["max_cancellation_rate_24h"]:
                patterns.append(AbusePattern.EXCESSIVE_CANCELLATIONS)
                severity = AbuseSeverity.HIGH
            
            # Check 7-day rate
            elif rate_7d > cls.ABUSE_THRESHOLDS["max_cancellation_rate_7d"]:
                patterns.append(AbusePattern.EXCESSIVE_CANCELLATIONS)
                severity = AbuseSeverity.MEDIUM
            
            # Check 30-day rate
            elif rate_30d > cls.ABUSE_THRESHOLDS["max_cancellation_rate_30d"]:
                patterns.append(AbusePattern.EXCESSIVE_CANCELLATIONS)
                severity = AbuseSeverity.LOW
        
        return patterns, severity
    
    @classmethod
    def _check_rapid_fire_cancellation(
        cls,
        user_history: UserCancellationHistory
    ) -> Tuple[List[AbusePattern], AbuseSeverity]:
        """Check for rapid fire cancellation patterns"""
        patterns = []
        severity = AbuseSeverity.LOW
        
        # Check daily cancellation count
        if user_history.cancellations_24h > cls.ABUSE_THRESHOLDS["max_cancellations_per_day"]:
            patterns.append(AbusePattern.RAPID_FIRE_CANCELLATION)
            severity = AbuseSeverity.HIGH
        
        # Check hourly rate based on recent activity
        elif (user_history.last_cancellation_at and 
              user_history.last_cancellation_at > datetime.utcnow() - timedelta(hours=1) and
              user_history.cancellations_24h > cls.ABUSE_THRESHOLDS["max_cancellations_per_hour"]):
            patterns.append(AbusePattern.RAPID_FIRE_CANCELLATION)
            severity = AbuseSeverity.MEDIUM
        
        return patterns, severity
    
    @classmethod
    def _check_late_stage_cancellation(
        cls,
        cancellation_attempt: CancellationAttempt,
        user_history: UserCancellationHistory
    ) -> Tuple[List[AbusePattern], AbuseSeverity]:
        """Check for late stage cancellation abuse"""
        patterns = []
        severity = AbuseSeverity.LOW
        
        # Check if current cancellation is late stage
        hours_since_creation = cancellation_attempt.time_since_creation.total_seconds() / 3600
        is_late_stage = hours_since_creation > cls.ABUSE_THRESHOLDS["late_stage_threshold_hours"]
        
        # Check if seller has responded (makes cancellation more problematic)
        if (is_late_stage and 
            cancellation_attempt.seller_has_responded and
            user_history.late_stage_cancellations >= cls.ABUSE_THRESHOLDS["max_late_stage_cancellations_week"]):
            patterns.append(AbusePattern.LATE_STAGE_CANCELLATION)
            severity = AbuseSeverity.HIGH
        
        elif is_late_stage and cancellation_attempt.payment_confirmed:
            patterns.append(AbusePattern.LATE_STAGE_CANCELLATION)
            severity = AbuseSeverity.MEDIUM
        
        return patterns, severity
    
    @classmethod
    def _check_pattern_gaming(
        cls,
        user_history: UserCancellationHistory
    ) -> Tuple[List[AbusePattern], AbuseSeverity]:
        """Check for pattern gaming (creating trades just to cancel them)"""
        patterns = []
        severity = AbuseSeverity.LOW
        
        # Check if user is creating trades in suspicious patterns
        if (user_history.total_cancellations > cls.ABUSE_THRESHOLDS["suspicious_pattern_count"] and
            user_history.average_cancellation_time < 2.0):  # Average less than 2 hours
            patterns.append(AbusePattern.PATTERN_GAMING)
            severity = AbuseSeverity.MEDIUM
        
        # Check for very rapid cancellation pattern
        if (user_history.last_cancellation_at and
            user_history.last_cancellation_at > datetime.utcnow() - timedelta(minutes=cls.ABUSE_THRESHOLDS["min_time_between_cancellations_minutes"])):
            patterns.append(AbusePattern.PATTERN_GAMING)
            severity = AbuseSeverity.HIGH
        
        return patterns, severity
    
    @classmethod
    def _check_seller_griefing(
        cls,
        cancellation_attempt: CancellationAttempt,
        user_history: UserCancellationHistory
    ) -> Tuple[List[AbusePattern], AbuseSeverity]:
        """Check for seller griefing patterns"""
        patterns = []
        severity = AbuseSeverity.LOW
        
        # Check if cancelling after seller has invested significant time
        if (cancellation_attempt.seller_has_responded and
            cancellation_attempt.time_since_creation > timedelta(hours=12) and
            user_history.seller_complaint_count > 2):
            patterns.append(AbusePattern.SELLER_GRIEFING)
            severity = AbuseSeverity.HIGH
        
        return patterns, severity
    
    @classmethod
    def _check_amount_manipulation(
        cls,
        cancellation_attempt: CancellationAttempt,
        user_history: UserCancellationHistory
    ) -> Tuple[List[AbusePattern], AbuseSeverity]:
        """Check for amount manipulation patterns"""
        patterns = []
        severity = AbuseSeverity.LOW
        
        # Check for high-value cancellation abuse
        if (cancellation_attempt.escrow_amount >= cls.ABUSE_THRESHOLDS["high_value_threshold"] and
            user_history.cancellations_7d > cls.ABUSE_THRESHOLDS["max_high_value_cancellations_week"]):
            patterns.append(AbusePattern.AMOUNT_MANIPULATION)
            severity = AbuseSeverity.HIGH
        
        return patterns, severity
    
    @classmethod
    def _determine_prevention_actions(
        cls,
        patterns: List[AbusePattern],
        severity: AbuseSeverity
    ) -> List[PreventionAction]:
        """Determine appropriate prevention actions based on detected patterns"""
        actions = []
        
        if not patterns:
            return actions
        
        # Critical severity - strong actions
        if severity == AbuseSeverity.CRITICAL:
            actions.extend([
                PreventionAction.BLOCK_CANCELLATION,
                PreventionAction.PERMANENT_RESTRICTION,
                PreventionAction.ESCALATE_ADMIN
            ])
        
        # High severity - significant restrictions
        elif severity == AbuseSeverity.HIGH:
            if AbusePattern.SELLER_GRIEFING in patterns:
                actions.extend([
                    PreventionAction.REQUIRE_REASON,
                    PreventionAction.DELAY_CANCELLATION,
                    PreventionAction.TEMPORARY_RESTRICTION,
                    PreventionAction.ESCALATE_ADMIN
                ])
            else:
                actions.extend([
                    PreventionAction.DELAY_CANCELLATION,
                    PreventionAction.TEMPORARY_RESTRICTION,
                    PreventionAction.ESCALATE_ADMIN
                ])
        
        # Medium severity - moderate restrictions
        elif severity == AbuseSeverity.MEDIUM:
            if AbusePattern.LATE_STAGE_CANCELLATION in patterns:
                actions.extend([
                    PreventionAction.REQUIRE_REASON,
                    PreventionAction.DELAY_CANCELLATION
                ])
            else:
                actions.extend([
                    PreventionAction.DELAY_CANCELLATION,
                    PreventionAction.WARN_USER
                ])
        
        # Low severity - warnings and minor delays
        elif severity == AbuseSeverity.LOW:
            actions.append(PreventionAction.WARN_USER)
        
        return actions
    
    @classmethod
    def _calculate_delay(cls, severity: AbuseSeverity) -> int:
        """Calculate cancellation delay in seconds"""
        delays = {
            AbuseSeverity.LOW: 0,
            AbuseSeverity.MEDIUM: 300,    # 5 minutes
            AbuseSeverity.HIGH: 1800,     # 30 minutes
            AbuseSeverity.CRITICAL: 3600  # 1 hour
        }
        return delays.get(severity, 0)
    
    @classmethod
    def _calculate_restriction_duration(
        cls,
        severity: AbuseSeverity,
        patterns: List[AbusePattern]
    ) -> int:
        """Calculate restriction duration in hours"""
        base_duration = cls.RESTRICTION_DURATIONS.get(severity, 0)
        
        # Add extra time for certain patterns
        if AbusePattern.SELLER_GRIEFING in patterns:
            base_duration *= 2
        
        if AbusePattern.PATTERN_GAMING in patterns:
            base_duration += 24
        
        return base_duration
    
    @classmethod
    def _generate_prevention_message(
        cls,
        patterns: List[AbusePattern],
        actions: List[PreventionAction]
    ) -> str:
        """Generate user-friendly prevention message"""
        if not patterns:
            return "Cancellation approved"
        
        if PreventionAction.BLOCK_CANCELLATION in actions:
            return "Cancellation blocked due to suspicious activity. Please contact support."
        
        if PreventionAction.DELAY_CANCELLATION in actions:
            return "Cancellation will be processed after a brief delay for verification."
        
        if PreventionAction.REQUIRE_REASON in actions:
            return "Please provide a detailed reason for this cancellation."
        
        if PreventionAction.WARN_USER in actions:
            return "Warning: Excessive cancellations may result in account restrictions."
        
        return "Cancellation requires additional verification."
    
    @classmethod
    async def _log_abuse_detection(
        cls,
        cancellation_attempt: CancellationAttempt,
        patterns: List[AbusePattern],
        severity: AbuseSeverity
    ):
        """Log abuse detection for monitoring"""
        try:
            
            # Log the abuse detection (would need an abuse_log table)
            logger.warning(f"🚨 ESCROW_CANCELLATION_ABUSE_DETECTED: "
                         f"User: {cancellation_attempt.user_id}, "
                         f"Escrow: {cancellation_attempt.escrow_id}, "
                         f"Patterns: {[p.value for p in patterns]}, "
                         f"Severity: {severity.value}")
            
            # Send admin alert for high severity
            if severity in [AbuseSeverity.HIGH, AbuseSeverity.CRITICAL]:
                from services.consolidated_notification_service import consolidated_notification_service
                
                await consolidated_notification_service.send_admin_alert(
                    f"🚨 HIGH_SEVERITY_CANCELLATION_ABUSE\n"
                    f"User: {cancellation_attempt.user_id}\n"
                    f"Escrow: {cancellation_attempt.escrow_id}\n"
                    f"Amount: ${cancellation_attempt.escrow_amount}\n"
                    f"Patterns: {', '.join([p.value for p in patterns])}\n"
                    f"Severity: {severity.value}\n"
                    f"Status: {cancellation_attempt.escrow_status}\n"
                    f"Payment Confirmed: {cancellation_attempt.payment_confirmed}\n"
                    f"Seller Responded: {cancellation_attempt.seller_has_responded}\n\n"
                    f"Review and take appropriate action!"
                )
            
        except Exception as e:
            logger.error(f"Error logging abuse detection: {e}")
    
    @classmethod
    async def apply_restriction(
        cls,
        user_id: int,
        restriction_hours: int,
        reason: str
    ) -> bool:
        """Apply cancellation restriction to user"""
        try:
            from database import SessionLocal
            from models import User
            
            session = SessionLocal()
            
            try:
                user = session.query(User).filter(User.id == user_id).first()
                if not user:
                    return False
                
                # Set restriction end time
                restriction_until = datetime.utcnow() + timedelta(hours=restriction_hours)
                
                # This would require adding a cancellation_restricted_until field to User model
                # For now, just log the restriction
                logger.warning(f"🚫 CANCELLATION_RESTRICTION_APPLIED: "
                             f"User {user_id} restricted until {restriction_until} - {reason}")
                
                # TODO: Implement actual restriction in User model
                # user.cancellation_restricted_until = restriction_until
                # session.commit()
                
                return True
                
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"Error applying restriction: {e}")
            return False


# Convenience function for easy integration
async def check_cancellation_abuse(
    user_id: int,
    escrow_id: str,
    escrow_amount: float,
    escrow_status: str,
    time_since_creation_hours: float,
    time_since_last_activity_hours: float,
    seller_has_responded: bool = False,
    payment_confirmed: bool = False,
    reason: str = None,
    ip_address: str = None,
    user_agent: str = None
) -> AbuseDetectionResult:
    """
    Convenience function to check for cancellation abuse
    """
    cancellation_attempt = CancellationAttempt(
        user_id=user_id,
        escrow_id=escrow_id,
        escrow_amount=Decimal(str(escrow_amount)),
        escrow_status=escrow_status,
        time_since_creation=timedelta(hours=time_since_creation_hours),
        time_since_last_activity=timedelta(hours=time_since_last_activity_hours),
        seller_has_responded=seller_has_responded,
        payment_confirmed=payment_confirmed,
        reason=reason,
        ip_address=ip_address,
        user_agent=user_agent
    )
    
    return await EscrowCancellationAbusePreventor.check_cancellation_attempt(cancellation_attempt)