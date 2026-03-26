#!/usr/bin/env python3
"""
Behavioral Anomaly Detection Service
Machine learning-based pattern recognition for transaction anomaly detection
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Tuple
from dataclasses import dataclass
from decimal import Decimal
import statistics
from collections import defaultdict

from sqlalchemy import func, and_
from database import SessionLocal
from models import Transaction
from services.adaptive_security_service import AdaptiveSecurityService

logger = logging.getLogger(__name__)


@dataclass
class TransactionPattern:
    """Individual transaction pattern analysis"""
    
    user_id: int
    timestamp: datetime
    amount_usd: Decimal
    transaction_type: str
    hour_of_day: int
    day_of_week: int
    frequency_score: float  # How often user transacts at this time
    amount_score: float     # How typical this amount is for user
    velocity_score: float   # How rapid compared to normal pace
    overall_anomaly_score: float  # 0-100


@dataclass
class BehaviorBaseline:
    """User's behavioral baseline for anomaly detection"""
    
    user_id: int
    typical_amounts: List[float]  # Historical amounts
    preferred_hours: List[int]    # Preferred transaction hours
    preferred_days: List[int]     # Preferred days of week
    normal_frequency: float       # Transactions per day
    amount_variance: float        # Standard deviation of amounts
    temporal_patterns: Dict[str, float]  # Time-based pattern scores
    last_updated: datetime


@dataclass
class AnomalyAlert:
    """Anomaly detection alert"""
    
    user_id: int
    transaction_type: str
    anomaly_score: float
    risk_factors: List[str]
    recommended_actions: List[str]
    confidence_level: float
    detected_at: datetime


class BehavioralAnomalyDetection:
    """Advanced behavioral anomaly detection using statistical analysis"""

    def __init__(self):
        self.adaptive_security = AdaptiveSecurityService()
        
        # Anomaly detection thresholds
        self.anomaly_thresholds = {
            "low": 30.0,       # 30+ anomaly score
            "medium": 50.0,    # 50+ anomaly score
            "high": 70.0,      # 70+ anomaly score
            "critical": 85.0,  # 85+ anomaly score
        }
        
        # Statistical confidence levels
        self.confidence_levels = {
            "low": 0.6,        # 60% confidence
            "medium": 0.75,    # 75% confidence
            "high": 0.85,      # 85% confidence
            "critical": 0.95,  # 95% confidence
        }
        
        logger.info("Behavioral anomaly detection service initialized")

    async def build_user_baseline(self, user_id: int, days_back: int = 90) -> BehaviorBaseline:
        """Build comprehensive behavioral baseline for user"""
        session = SessionLocal()
        try:
            # Get historical data
            end_date = datetime.utcnow()
            start_date = end_date - timedelta(days=days_back)
            
            transactions = session.query(Transaction).filter(
                and_(
                    Transaction.user_id == user_id,
                    Transaction.created_at >= start_date,
                    Transaction.status == "completed"
                )
            ).all()
            
            if not transactions:
                # Return minimal baseline for new users
                return BehaviorBaseline(
                    user_id=user_id,
                    typical_amounts=[25.0, 50.0, 100.0],
                    preferred_hours=list(range(9, 17)),  # Business hours
                    preferred_days=list(range(1, 6)),    # Weekdays
                    normal_frequency=0.1,  # Once per 10 days
                    amount_variance=25.0,
                    temporal_patterns={},
                    last_updated=datetime.utcnow()
                )
            
            # Analyze amounts
            amounts = [float(getattr(tx, 'amount_usd', getattr(tx, 'amount', 0))) for tx in transactions]
            amount_variance = statistics.stdev(amounts) if len(amounts) > 1 else 25.0
            
            # Analyze temporal patterns
            hour_counts = defaultdict(int)
            day_counts = defaultdict(int)
            
            for tx in transactions:
                hour_counts[tx.created_at.hour] += 1
                day_counts[tx.created_at.weekday()] += 1
            
            # Find preferred hours (top 40% of activity)
            total_tx = len(transactions)
            preferred_hours = [
                hour for hour, count in hour_counts.items()
                if count >= total_tx * 0.1  # At least 10% of activity
            ]
            
            # Find preferred days (top activity days)
            preferred_days = [
                day for day, count in day_counts.items()
                if count >= total_tx * 0.15  # At least 15% of activity
            ]
            
            # Calculate normal frequency
            normal_frequency = len(transactions) / days_back
            
            # Build temporal patterns
            temporal_patterns = {}
            for hour in range(24):
                temporal_patterns[f"hour_{hour}"] = hour_counts[hour] / total_tx if total_tx > 0 else 0
            
            for day in range(7):
                temporal_patterns[f"day_{day}"] = day_counts[day] / total_tx if total_tx > 0 else 0
            
            return BehaviorBaseline(
                user_id=user_id,
                typical_amounts=amounts[-50:] if len(amounts) > 50 else amounts,  # Last 50 or all
                preferred_hours=preferred_hours or list(range(9, 17)),
                preferred_days=preferred_days or list(range(1, 6)),
                normal_frequency=normal_frequency,
                amount_variance=amount_variance,
                temporal_patterns=temporal_patterns,
                last_updated=datetime.utcnow()
            )
            
        except Exception as e:
            logger.error(f"Error building baseline for user {user_id}: {e}")
            # Return safe defaults
            return BehaviorBaseline(
                user_id=user_id,
                typical_amounts=[50.0],
                preferred_hours=list(range(9, 17)),
                preferred_days=list(range(1, 6)),
                normal_frequency=0.1,
                amount_variance=25.0,
                temporal_patterns={},
                last_updated=datetime.utcnow()
            )
        finally:
            session.close()

    async def analyze_transaction_pattern(self, user_id: int, amount_usd: Decimal, transaction_type: str = "transaction") -> TransactionPattern:
        """Analyze individual transaction for anomalies"""
        try:
            now = datetime.utcnow()
            baseline = await self.build_user_baseline(user_id)
            
            # Calculate amount score (how typical this amount is)
            if baseline.typical_amounts:
                # Z-score based analysis
                mean_amount = statistics.mean(baseline.typical_amounts)
                std_amount = baseline.amount_variance
                
                if std_amount > 0:
                    z_score = abs(float(amount_usd) - mean_amount) / std_amount
                    # Convert Z-score to 0-100 scale (3+ std devs = high anomaly)
                    amount_score = min(100.0, (z_score / 3.0) * 100.0)
                else:
                    amount_score = 0.0
            else:
                amount_score = 50.0  # Unknown baseline
            
            # Calculate temporal frequency score
            hour = now.hour
            day = now.weekday()
            
            hour_pattern = baseline.temporal_patterns.get(f"hour_{hour}", 0.0)
            day_pattern = baseline.temporal_patterns.get(f"day_{day}", 0.0)
            
            # Low pattern scores = high anomaly
            frequency_score = 100.0 - ((hour_pattern + day_pattern) * 100.0)
            
            # Calculate velocity score (recent transaction frequency)
            velocity_score = await self._calculate_velocity_anomaly(user_id, baseline)
            
            # Combine scores with weights
            overall_score = (
                amount_score * 0.4 +      # 40% weight on amount
                frequency_score * 0.3 +   # 30% weight on timing
                velocity_score * 0.3      # 30% weight on velocity
            )
            
            return TransactionPattern(
                user_id=user_id,
                timestamp=now,
                amount_usd=amount_usd,
                transaction_type=transaction_type,
                hour_of_day=hour,
                day_of_week=day,
                frequency_score=frequency_score,
                amount_score=amount_score,
                velocity_score=velocity_score,
                overall_anomaly_score=min(100.0, overall_score)
            )
            
        except Exception as e:
            logger.error(f"Error analyzing transaction pattern for user {user_id}: {e}")
            # Return conservative analysis
            return TransactionPattern(
                user_id=user_id,
                timestamp=datetime.utcnow(),
                amount_usd=amount_usd,
                transaction_type=transaction_type,
                hour_of_day=12,
                day_of_week=1,
                frequency_score=25.0,
                amount_score=25.0,
                velocity_score=25.0,
                overall_anomaly_score=25.0
            )

    async def _calculate_velocity_anomaly(self, user_id: int, baseline: BehaviorBaseline) -> float:
        """Calculate velocity-based anomaly score"""
        session = SessionLocal()
        try:
            # Check recent transaction count (last 24 hours)
            recent_cutoff = datetime.utcnow() - timedelta(hours=24)
            
            recent_count = session.query(func.count(Transaction.id)).filter(
                and_(
                    Transaction.user_id == user_id,
                    Transaction.created_at >= recent_cutoff
                )
            ).scalar() or 0
            
            # Expected count based on normal frequency
            expected_daily_count = baseline.normal_frequency
            
            if expected_daily_count > 0:
                velocity_ratio = recent_count / expected_daily_count
                # 2x normal frequency = 50% score, 5x = 100% score
                velocity_score = min(100.0, max(0.0, (velocity_ratio - 1.0) / 4.0 * 100.0))
            else:
                # New user or no baseline - any activity is somewhat anomalous
                velocity_score = min(100.0, recent_count * 20.0)
            
            return velocity_score
            
        except Exception as e:
            logger.error(f"Error calculating velocity anomaly: {e}")
            return 0.0
        finally:
            session.close()

    async def detect_anomaly(self, user_id: int, amount_usd: Decimal, transaction_type: str = "transaction") -> AnomalyAlert:
        """Comprehensive anomaly detection"""
        try:
            # Analyze transaction pattern
            pattern = await self.analyze_transaction_pattern(user_id, amount_usd, transaction_type)
            
            # Determine severity level
            anomaly_score = pattern.overall_anomaly_score
            confidence = self.confidence_levels["low"]
            
            for level, threshold in self.anomaly_thresholds.items():
                if anomaly_score >= threshold:
                    confidence = self.confidence_levels[level]
            
            # Build risk factors list
            risk_factors = []
            if pattern.amount_score > 50:
                risk_factors.append(f"Unusual amount: ${amount_usd} (score: {pattern.amount_score:.1f})")
            
            if pattern.frequency_score > 40:
                risk_factors.append(f"Unusual timing: {pattern.hour_of_day}:00 on weekday {pattern.day_of_week} (score: {pattern.frequency_score:.1f})")
            
            if pattern.velocity_score > 40:
                risk_factors.append(f"High transaction velocity (score: {pattern.velocity_score:.1f})")
            
            # Generate recommended actions
            recommended_actions = []
            if anomaly_score >= self.anomaly_thresholds["critical"]:
                recommended_actions.extend([
                    "Immediate manual review required",
                    "Consider temporary account restrictions",
                    "Verify user identity"
                ])
            elif anomaly_score >= self.anomaly_thresholds["high"]:
                recommended_actions.extend([
                    "Flag for manual review",
                    "Monitor subsequent transactions closely",
                    "Consider additional verification"
                ])
            elif anomaly_score >= self.anomaly_thresholds["medium"]:
                recommended_actions.extend([
                    "Enhanced monitoring",
                    "Log for pattern analysis"
                ])
            else:
                recommended_actions.append("Continue normal monitoring")
            
            return AnomalyAlert(
                user_id=user_id,
                transaction_type=transaction_type,
                anomaly_score=anomaly_score,
                risk_factors=risk_factors,
                recommended_actions=recommended_actions,
                confidence_level=confidence,
                detected_at=datetime.utcnow()
            )
            
        except Exception as e:
            logger.error(f"Error detecting anomaly for user {user_id}: {e}")
            # Return safe default alert
            return AnomalyAlert(
                user_id=user_id,
                transaction_type=transaction_type,
                anomaly_score=20.0,
                risk_factors=["Error in anomaly analysis"],
                recommended_actions=["Manual review recommended due to analysis error"],
                confidence_level=0.5,
                detected_at=datetime.utcnow()
            )

    async def is_behavior_anomalous(self, user_id: int, amount_usd: Decimal, transaction_type: str = "transaction") -> Tuple[bool, str, float]:
        """Simple anomaly check (returns is_anomalous, reason, confidence)"""
        try:
            alert = await self.detect_anomaly(user_id, amount_usd, transaction_type)
            
            is_anomalous = alert.anomaly_score >= self.anomaly_thresholds["medium"]
            reason = "; ".join(alert.risk_factors) if alert.risk_factors else "Normal behavior pattern"
            
            return is_anomalous, reason, alert.confidence_level
            
        except Exception as e:
            logger.error(f"Error checking behavior anomaly for user {user_id}: {e}")
            return False, "Error in anomaly analysis", 0.5

    async def get_user_behavior_summary(self, user_id: int) -> Dict:
        """Get comprehensive behavior analysis summary"""
        try:
            baseline = await self.build_user_baseline(user_id)
            
            # Recent pattern analysis
            await self.analyze_transaction_pattern(user_id, Decimal("100.00"))  # Sample amount
            
            return {
                "user_id": user_id,
                "baseline": {
                    "typical_amount_range": f"${min(baseline.typical_amounts):.2f} - ${max(baseline.typical_amounts):.2f}" if baseline.typical_amounts else "No data",
                    "average_amount": f"${statistics.mean(baseline.typical_amounts):.2f}" if baseline.typical_amounts else "No data",
                    "transaction_frequency": f"{baseline.normal_frequency:.2f} per day",
                    "preferred_hours": baseline.preferred_hours,
                    "preferred_days": baseline.preferred_days,
                    "amount_variance": f"${baseline.amount_variance:.2f}",
                    "last_updated": baseline.last_updated.isoformat()
                },
                "anomaly_thresholds": {
                    "medium_alert": f"{self.anomaly_thresholds['medium']}+ anomaly score",
                    "high_alert": f"{self.anomaly_thresholds['high']}+ anomaly score",
                    "critical_alert": f"{self.anomaly_thresholds['critical']}+ anomaly score"
                },
                "generated_at": datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error generating behavior summary for user {user_id}: {e}")
            return {"error": str(e), "user_id": user_id}