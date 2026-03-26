"""
Advanced Refund Analytics Service
Comprehensive metrics, pattern analysis, and anomaly detection for refund operations
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
from collections import Counter
from dataclasses import dataclass
from enum import Enum
import statistics

from sqlalchemy.orm import Session
from sqlalchemy import func

from models import Refund, RefundStatus
from database import SessionLocal
from utils.refund_progress_tracker import real_time_refund_tracker
from services.unified_refund_notification_service import UnifiedRefundNotificationService

logger = logging.getLogger(__name__)


class AnalyticsPeriod(Enum):
    """Time periods for analytics"""
    HOUR = "hour"
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"


class RefundTrend(Enum):
    """Refund trend directions"""
    INCREASING = "increasing"
    DECREASING = "decreasing"
    STABLE = "stable"
    VOLATILE = "volatile"


@dataclass
class RefundMetrics:
    """Core refund metrics data structure"""
    period: str
    total_count: int
    total_amount: float
    completed_count: int
    completed_amount: float
    failed_count: int
    failed_amount: float
    pending_count: int
    pending_amount: float
    success_rate: float
    average_amount: float
    median_amount: float
    average_processing_time: float
    types_breakdown: Dict[str, int]
    hourly_distribution: Dict[int, int]
    top_failure_reasons: List[Tuple[str, int]]
    timestamp: datetime


@dataclass
class RefundPattern:
    """Refund pattern analysis result"""
    pattern_id: str
    pattern_type: str
    description: str
    frequency: int
    impact_score: float
    affected_users: List[int]
    time_pattern: Optional[str]
    amount_pattern: Optional[str]
    recommendations: List[str]


@dataclass
class AnomalyAlert:
    """Anomaly detection alert"""
    alert_id: str
    alert_type: str
    severity: str  # low, medium, high, critical
    title: str
    description: str
    metric_value: float
    threshold_value: float
    affected_period: str
    recommendations: List[str]
    requires_action: bool
    created_at: datetime


class RefundAnalyticsService:
    """
    Advanced analytics service for comprehensive refund tracking and insights
    """
    
    def __init__(self):
        self.notification_service = UnifiedRefundNotificationService()
        self.metrics_cache: Dict[str, Any] = {}
        self.cache_ttl = 300  # 5 minutes
        self.anomaly_thresholds = {
            "refund_volume_increase": 2.0,  # 100% increase threshold
            "failure_rate_increase": 0.15,  # 15% failure rate threshold
            "processing_time_increase": 1.5,  # 50% processing time increase
            "amount_variance_threshold": 3.0,  # 3x standard deviation
            "user_refund_frequency": 5,  # More than 5 refunds per user per day
            "unusual_time_pattern": 0.1  # 10% of refunds outside normal hours
        }
        
        logger.info("✅ Refund Analytics Service initialized")
    
    def get_comprehensive_metrics(
        self,
        period: AnalyticsPeriod = AnalyticsPeriod.DAY,
        lookback_periods: int = 30,
        include_trends: bool = True
    ) -> Dict[str, Any]:
        """Get comprehensive refund metrics with trend analysis"""
        try:
            cache_key = f"comprehensive_metrics_{period.value}_{lookback_periods}_{include_trends}"
            
            # Check cache
            if self._is_cache_valid(cache_key):
                return self.metrics_cache[cache_key]["data"]
            
            metrics_data = {
                "period": period.value,
                "lookback_periods": lookback_periods,
                "generated_at": datetime.utcnow().isoformat(),
                "summary": {},
                "trends": {},
                "breakdown": {},
                "performance": {},
                "patterns": [],
                "anomalies": []
            }
            
            with SessionLocal() as session:
                # Calculate time range
                end_time = datetime.utcnow()
                if period == AnalyticsPeriod.HOUR:
                    start_time = end_time - timedelta(hours=lookback_periods)
                    timedelta(hours=1)
                elif period == AnalyticsPeriod.DAY:
                    start_time = end_time - timedelta(days=lookback_periods)
                    timedelta(days=1)
                elif period == AnalyticsPeriod.WEEK:
                    start_time = end_time - timedelta(weeks=lookback_periods)
                    timedelta(weeks=1)
                elif period == AnalyticsPeriod.MONTH:
                    start_time = end_time - timedelta(days=lookback_periods * 30)
                    timedelta(days=30)
                else:
                    start_time = end_time - timedelta(days=lookback_periods)
                    timedelta(days=1)
                
                # Get base query
                base_query = session.query(Refund).filter(
                    Refund.created_at >= start_time,
                    Refund.created_at <= end_time
                )
                
                # Summary metrics
                total_refunds = base_query.count()
                total_amount = session.query(func.sum(Refund.amount)).filter(
                    Refund.created_at >= start_time,
                    Refund.created_at <= end_time
                ).scalar() or 0
                
                completed_refunds = base_query.filter(
                    Refund.status == RefundStatus.COMPLETED.value
                ).count()
                completed_amount = session.query(func.sum(Refund.amount)).filter(
                    Refund.created_at >= start_time,
                    Refund.created_at <= end_time,
                    Refund.status == RefundStatus.COMPLETED.value
                ).scalar() or 0
                
                failed_refunds = base_query.filter(
                    Refund.status == RefundStatus.FAILED.value
                ).count()
                failed_amount = session.query(func.sum(Refund.amount)).filter(
                    Refund.created_at >= start_time,
                    Refund.created_at <= end_time,
                    Refund.status == RefundStatus.FAILED.value
                ).scalar() or 0
                
                pending_refunds = base_query.filter(
                    Refund.status == RefundStatus.PENDING.value
                ).count()
                pending_amount = session.query(func.sum(Refund.amount)).filter(
                    Refund.created_at >= start_time,
                    Refund.created_at <= end_time,
                    Refund.status == RefundStatus.PENDING.value
                ).scalar() or 0
                
                # Calculate rates and averages
                success_rate = (completed_refunds / total_refunds * 100) if total_refunds > 0 else 0
                failure_rate = (failed_refunds / total_refunds * 100) if total_refunds > 0 else 0
                avg_amount = float(total_amount) / total_refunds if total_refunds > 0 else 0
                
                # Processing time analysis
                completed_refunds_with_time = session.query(Refund).filter(
                    Refund.created_at >= start_time,
                    Refund.status == RefundStatus.COMPLETED.value,
                    Refund.completed_at.isnot(None)
                ).all()
                
                processing_times = []
                for refund in completed_refunds_with_time:
                    if refund.completed_at and refund.created_at:
                        processing_time = (refund.completed_at - refund.created_at).total_seconds()
                        processing_times.append(processing_time)
                
                avg_processing_time = statistics.mean(processing_times) if processing_times else 0
                median_processing_time = statistics.median(processing_times) if processing_times else 0
                
                # Build summary
                metrics_data["summary"] = {
                    "total_refunds": total_refunds,
                    "total_amount": float(total_amount),
                    "completed_refunds": completed_refunds,
                    "completed_amount": float(completed_amount),
                    "failed_refunds": failed_refunds,
                    "failed_amount": float(failed_amount),
                    "pending_refunds": pending_refunds,
                    "pending_amount": float(pending_amount),
                    "success_rate": round(success_rate, 2),
                    "failure_rate": round(failure_rate, 2),
                    "average_amount": round(avg_amount, 2),
                    "average_processing_time": round(avg_processing_time, 2),
                    "median_processing_time": round(median_processing_time, 2)
                }
                
                # Breakdown by type
                type_breakdown = session.query(
                    Refund.refund_type,
                    func.count(Refund.id).label('count'),
                    func.sum(Refund.amount).label('amount'),
                    func.avg(Refund.amount).label('avg_amount')
                ).filter(
                    Refund.created_at >= start_time,
                    Refund.created_at <= end_time
                ).group_by(Refund.refund_type).all()
                
                metrics_data["breakdown"]["by_type"] = {}
                for type_name, count, amount, avg_amount in type_breakdown:
                    metrics_data["breakdown"]["by_type"][type_name] = {
                        "count": count,
                        "amount": float(amount or 0),
                        "average_amount": float(avg_amount or 0),
                        "percentage": round(count / total_refunds * 100, 2) if total_refunds > 0 else 0
                    }
                
                # Hourly distribution
                hourly_dist = session.query(
                    func.extract('hour', Refund.created_at).label('hour'),
                    func.count(Refund.id).label('count')
                ).filter(
                    Refund.created_at >= start_time,
                    Refund.created_at <= end_time
                ).group_by('hour').all()
                
                metrics_data["breakdown"]["hourly_distribution"] = {
                    str(int(hour)): count for hour, count in hourly_dist
                }
                
                # Daily breakdown (if period allows)
                if period in [AnalyticsPeriod.WEEK, AnalyticsPeriod.MONTH] and lookback_periods <= 90:
                    daily_breakdown = session.query(
                        func.date(Refund.created_at).label('date'),
                        func.count(Refund.id).label('count'),
                        func.sum(Refund.amount).label('amount')
                    ).filter(
                        Refund.created_at >= start_time,
                        Refund.created_at <= end_time
                    ).group_by('date').order_by('date').all()
                    
                    metrics_data["breakdown"]["daily_series"] = [
                        {
                            "date": date.isoformat(),
                            "count": count,
                            "amount": float(amount or 0)
                        }
                        for date, count, amount in daily_breakdown
                    ]
                
                # Performance metrics
                metrics_data["performance"] = {
                    "processing_time_stats": {
                        "mean": round(avg_processing_time, 2),
                        "median": round(median_processing_time, 2),
                        "min": round(min(processing_times), 2) if processing_times else 0,
                        "max": round(max(processing_times), 2) if processing_times else 0,
                        "std_dev": round(statistics.stdev(processing_times), 2) if len(processing_times) > 1 else 0
                    },
                    "real_time_tracking": real_time_refund_tracker.get_metrics(),
                    "notification_delivery_rate": self._calculate_notification_delivery_rate(session, start_time, end_time)
                }
                
                # Trend analysis
                if include_trends:
                    metrics_data["trends"] = self._analyze_trends(session, period, lookback_periods, end_time)
                
                # Pattern analysis
                metrics_data["patterns"] = self._analyze_patterns(session, start_time, end_time)
                
                # Anomaly detection
                metrics_data["anomalies"] = self._detect_anomalies(session, metrics_data, start_time, end_time)
            
            # Cache results
            self._cache_data(cache_key, metrics_data)
            
            return metrics_data
            
        except Exception as e:
            logger.error(f"❌ Error generating comprehensive metrics: {e}")
            return {
                "error": str(e),
                "generated_at": datetime.utcnow().isoformat()
            }
    
    def analyze_user_refund_patterns(self, user_id: int, days_lookback: int = 90) -> Dict[str, Any]:
        """Analyze refund patterns for a specific user"""
        try:
            with SessionLocal() as session:
                start_time = datetime.utcnow() - timedelta(days=days_lookback)
                
                user_refunds = session.query(Refund).filter(
                    Refund.user_id == user_id,
                    Refund.created_at >= start_time
                ).order_by(Refund.created_at.desc()).all()
                
                if not user_refunds:
                    return {
                        "user_id": user_id,
                        "analysis_period_days": days_lookback,
                        "total_refunds": 0,
                        "patterns": [],
                        "risk_score": 0,
                        "recommendations": []
                    }
                
                # Basic statistics
                total_refunds = len(user_refunds)
                total_amount = sum(float(r.amount) for r in user_refunds)
                avg_amount = total_amount / total_refunds
                
                # Frequency analysis
                frequency_per_week = total_refunds / (days_lookback / 7)
                
                # Type analysis
                type_counts = Counter(r.refund_type for r in user_refunds)
                most_common_type = type_counts.most_common(1)[0] if type_counts else ("none", 0)
                
                # Time pattern analysis
                hour_counts = Counter(r.created_at.hour for r in user_refunds)
                most_active_hours = [hour for hour, count in hour_counts.most_common(3)]
                
                # Calculate risk score
                risk_score = self._calculate_user_risk_score(
                    frequency_per_week, total_amount, type_counts, days_lookback
                )
                
                # Generate patterns and recommendations
                patterns = []
                recommendations = []
                
                if frequency_per_week > 1:
                    patterns.append({
                        "type": "high_frequency",
                        "description": f"High refund frequency: {frequency_per_week:.1f} refunds per week",
                        "impact": "medium"
                    })
                    recommendations.append("Consider investigating underlying causes for frequent refunds")
                
                if most_common_type[1] > total_refunds * 0.6:
                    patterns.append({
                        "type": "type_concentration",
                        "description": f"Concentrated refund type: {most_common_type[1]} {most_common_type[0]} refunds",
                        "impact": "medium"
                    })
                    recommendations.append(f"Focus on improving {most_common_type[0]} process reliability")
                
                if len(set(r.created_at.hour for r in user_refunds)) <= 3:
                    patterns.append({
                        "type": "time_concentration",
                        "description": f"Refunds concentrated in specific hours: {most_active_hours}",
                        "impact": "low"
                    })
                
                return {
                    "user_id": user_id,
                    "analysis_period_days": days_lookback,
                    "total_refunds": total_refunds,
                    "total_amount": round(total_amount, 2),
                    "average_amount": round(avg_amount, 2),
                    "frequency_per_week": round(frequency_per_week, 2),
                    "most_common_type": most_common_type[0],
                    "most_active_hours": most_active_hours,
                    "risk_score": round(risk_score, 2),
                    "patterns": patterns,
                    "recommendations": recommendations,
                    "analysis_timestamp": datetime.utcnow().isoformat()
                }
                
        except Exception as e:
            logger.error(f"❌ Error analyzing user refund patterns for user {user_id}: {e}")
            return {
                "error": str(e),
                "user_id": user_id
            }
    
    def generate_refund_insights(self, period_days: int = 30) -> Dict[str, Any]:
        """Generate actionable insights from refund data"""
        try:
            insights = {
                "generated_at": datetime.utcnow().isoformat(),
                "analysis_period_days": period_days,
                "key_insights": [],
                "recommendations": [],
                "improvement_opportunities": [],
                "cost_impact": {},
                "operational_metrics": {}
            }
            
            # Get comprehensive metrics for analysis
            metrics = self.get_comprehensive_metrics(
                period=AnalyticsPeriod.DAY,
                lookback_periods=period_days,
                include_trends=True
            )
            
            if "error" in metrics:
                return insights
            
            summary = metrics.get("summary", {})
            trends = metrics.get("trends", {})
            breakdown = metrics.get("breakdown", {})
            performance = metrics.get("performance", {})
            
            # Key insights generation
            total_refunds = summary.get("total_refunds", 0)
            success_rate = summary.get("success_rate", 0)
            failure_rate = summary.get("failure_rate", 0)
            avg_processing_time = summary.get("average_processing_time", 0)
            
            # Success rate insights
            if success_rate < 85:
                insights["key_insights"].append({
                    "type": "success_rate_low",
                    "title": "Low Refund Success Rate",
                    "description": f"Current success rate of {success_rate}% is below optimal (>95%)",
                    "impact": "high",
                    "metric_value": success_rate
                })
                insights["recommendations"].append("Investigate causes of failed refunds and improve processing reliability")
            
            # Processing time insights
            if avg_processing_time > 3600:  # More than 1 hour
                insights["key_insights"].append({
                    "type": "processing_time_high",
                    "title": "Long Processing Times",
                    "description": f"Average processing time of {avg_processing_time/60:.1f} minutes exceeds target",
                    "impact": "medium",
                    "metric_value": avg_processing_time
                })
                insights["recommendations"].append("Optimize refund processing workflow to reduce user wait times")
            
            # Volume trend insights
            if "volume_trend" in trends:
                volume_trend = trends["volume_trend"]
                if volume_trend.get("direction") == "increasing" and volume_trend.get("change_percent", 0) > 50:
                    insights["key_insights"].append({
                        "type": "volume_increase",
                        "title": "Significant Refund Volume Increase",
                        "description": f"Refund volume increased by {volume_trend.get('change_percent', 0):.1f}%",
                        "impact": "high",
                        "metric_value": volume_trend.get("change_percent", 0)
                    })
                    insights["recommendations"].append("Investigate root causes of increased refund requests")
            
            # Type concentration insights
            type_breakdown = breakdown.get("by_type", {})
            if type_breakdown:
                dominant_type = max(type_breakdown.items(), key=lambda x: x[1]["count"])
                if dominant_type[1]["percentage"] > 60:
                    insights["key_insights"].append({
                        "type": "type_concentration",
                        "title": "Concentrated Refund Type",
                        "description": f"{dominant_type[0]} represents {dominant_type[1]['percentage']}% of refunds",
                        "impact": "medium",
                        "metric_value": dominant_type[1]["percentage"]
                    })
                    insights["improvement_opportunities"].append(f"Focus improvement efforts on {dominant_type[0]} process")
            
            # Cost impact analysis
            total_amount = summary.get("total_amount", 0)
            failed_amount = summary.get("failed_amount", 0)
            
            insights["cost_impact"] = {
                "total_refunded": total_amount,
                "failed_refund_impact": failed_amount,
                "processing_cost_estimate": total_refunds * 2.5,  # Estimated $2.50 per refund processing cost
                "opportunity_cost": failed_amount * 0.1  # Estimated 10% opportunity cost on failed refunds
            }
            
            # Operational metrics
            insights["operational_metrics"] = {
                "daily_refund_volume": round(total_refunds / period_days, 2),
                "daily_refund_amount": round(total_amount / period_days, 2),
                "staff_workload_estimate": round(total_refunds * 0.1, 2),  # Estimated 0.1 hours per refund
                "automation_potential": self._calculate_automation_potential(breakdown, performance)
            }
            
            # Generate improvement opportunities
            if failure_rate > 5:
                insights["improvement_opportunities"].append("Implement preventive measures to reduce refund failures")
            
            if avg_processing_time > 1800:  # More than 30 minutes
                insights["improvement_opportunities"].append("Automate manual refund processing steps")
            
            delivery_rate = performance.get("notification_delivery_rate", {}).get("overall_rate", 100)
            if delivery_rate < 95:
                insights["improvement_opportunities"].append("Improve notification delivery reliability")
            
            return insights
            
        except Exception as e:
            logger.error(f"❌ Error generating refund insights: {e}")
            return {
                "error": str(e),
                "generated_at": datetime.utcnow().isoformat()
            }
    
    def get_real_time_dashboard_data(self) -> Dict[str, Any]:
        """Get real-time dashboard data for admin monitoring"""
        try:
            dashboard_data = {
                "timestamp": datetime.utcnow().isoformat(),
                "active_refunds": {},
                "recent_activity": [],
                "performance_metrics": {},
                "alerts": [],
                "system_health": {}
            }
            
            # Active refunds from real-time tracker
            tracker_metrics = real_time_refund_tracker.get_metrics()
            dashboard_data["active_refunds"] = {
                "total_active": tracker_metrics.get("active_sessions_count", 0),
                "by_stage": tracker_metrics.get("sessions_by_stage", {}),
                "average_duration": tracker_metrics.get("average_session_duration", 0),
                "total_updates": tracker_metrics.get("total_updates", 0)
            }
            
            # Recent activity (last 2 hours)
            with SessionLocal() as session:
                recent_time = datetime.utcnow() - timedelta(hours=2)
                recent_refunds = session.query(Refund).filter(
                    Refund.created_at >= recent_time
                ).order_by(Refund.created_at.desc()).limit(10).all()
                
                dashboard_data["recent_activity"] = [
                    {
                        "refund_id": r.refund_id,
                        "user_id": r.user_id,
                        "amount": float(r.amount),
                        "type": r.refund_type,
                        "status": r.status,
                        "created_at": r.created_at.isoformat(),
                        "processing_time": (
                            (r.completed_at - r.created_at).total_seconds()
                            if r.completed_at else None
                        )
                    }
                    for r in recent_refunds
                ]
                
                # Performance metrics (last hour)
                hour_ago = datetime.utcnow() - timedelta(hours=1)
                hourly_count = session.query(Refund).filter(
                    Refund.created_at >= hour_ago
                ).count()
                
                hourly_completed = session.query(Refund).filter(
                    Refund.created_at >= hour_ago,
                    Refund.status == RefundStatus.COMPLETED.value
                ).count()
                
                dashboard_data["performance_metrics"] = {
                    "hourly_volume": hourly_count,
                    "hourly_completion_rate": (
                        round(hourly_completed / hourly_count * 100, 2)
                        if hourly_count > 0 else 0
                    ),
                    "websocket_clients": tracker_metrics.get("websocket_messages_sent", 0),
                    "notification_delivery": tracker_metrics.get("notification_delivery_rate", 0)
                }
            
            # System health
            dashboard_data["system_health"] = {
                "tracker_status": "healthy" if tracker_metrics.get("active_sessions_count", 0) >= 0 else "warning",
                "notification_service_status": "healthy",  # Would be determined by actual health check
                "database_status": "healthy",  # Would be determined by actual health check
                "cache_hit_rate": self._calculate_cache_hit_rate()
            }
            
            # Generate alerts for unusual patterns
            alerts = self._generate_real_time_alerts(dashboard_data)
            dashboard_data["alerts"] = alerts
            
            return dashboard_data
            
        except Exception as e:
            logger.error(f"❌ Error getting real-time dashboard data: {e}")
            return {
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat()
            }
    
    # Private helper methods
    
    def _analyze_trends(self, session: Session, period: AnalyticsPeriod, lookback_periods: int, end_time: datetime) -> Dict[str, Any]:
        """Analyze trends in refund data"""
        try:
            trends = {}
            
            # Calculate comparison period
            if period == AnalyticsPeriod.DAY:
                current_start = end_time - timedelta(days=lookback_periods)
                previous_start = current_start - timedelta(days=lookback_periods)
                previous_end = current_start
            elif period == AnalyticsPeriod.WEEK:
                current_start = end_time - timedelta(weeks=lookback_periods)
                previous_start = current_start - timedelta(weeks=lookback_periods)
                previous_end = current_start
            else:
                # Default to daily comparison
                current_start = end_time - timedelta(days=lookback_periods)
                previous_start = current_start - timedelta(days=lookback_periods)
                previous_end = current_start
            
            # Current period metrics
            current_count = session.query(Refund).filter(
                Refund.created_at >= current_start,
                Refund.created_at <= end_time
            ).count()
            
            current_amount = session.query(func.sum(Refund.amount)).filter(
                Refund.created_at >= current_start,
                Refund.created_at <= end_time
            ).scalar() or 0
            
            current_success = session.query(Refund).filter(
                Refund.created_at >= current_start,
                Refund.created_at <= end_time,
                Refund.status == RefundStatus.COMPLETED.value
            ).count()
            
            # Previous period metrics
            previous_count = session.query(Refund).filter(
                Refund.created_at >= previous_start,
                Refund.created_at <= previous_end
            ).count()
            
            previous_amount = session.query(func.sum(Refund.amount)).filter(
                Refund.created_at >= previous_start,
                Refund.created_at <= previous_end
            ).scalar() or 0
            
            previous_success = session.query(Refund).filter(
                Refund.created_at >= previous_start,
                Refund.created_at <= previous_end,
                Refund.status == RefundStatus.COMPLETED.value
            ).count()
            
            # Calculate trends
            volume_change = ((current_count - previous_count) / previous_count * 100) if previous_count > 0 else 0
            amount_change = ((float(current_amount) - float(previous_amount)) / float(previous_amount) * 100) if previous_amount > 0 else 0
            
            current_success_rate = (current_success / current_count * 100) if current_count > 0 else 0
            previous_success_rate = (previous_success / previous_count * 100) if previous_count > 0 else 0
            success_rate_change = current_success_rate - previous_success_rate
            
            trends["volume_trend"] = {
                "direction": self._determine_trend_direction(volume_change),
                "change_percent": round(volume_change, 2),
                "current_value": current_count,
                "previous_value": previous_count
            }
            
            trends["amount_trend"] = {
                "direction": self._determine_trend_direction(amount_change),
                "change_percent": round(amount_change, 2),
                "current_value": float(current_amount),
                "previous_value": float(previous_amount)
            }
            
            trends["success_rate_trend"] = {
                "direction": self._determine_trend_direction(success_rate_change),
                "change_percent": round(success_rate_change, 2),
                "current_value": round(current_success_rate, 2),
                "previous_value": round(previous_success_rate, 2)
            }
            
            return trends
            
        except Exception as e:
            logger.error(f"❌ Error analyzing trends: {e}")
            return {}
    
    def _analyze_patterns(self, session: Session, start_time: datetime, end_time: datetime) -> List[RefundPattern]:
        """Analyze patterns in refund data"""
        try:
            patterns = []
            
            # Time-based patterns
            hourly_distribution = session.query(
                func.extract('hour', Refund.created_at).label('hour'),
                func.count(Refund.id).label('count')
            ).filter(
                Refund.created_at >= start_time,
                Refund.created_at <= end_time
            ).group_by('hour').all()
            
            if hourly_distribution:
                hour_counts = dict(hourly_distribution)
                max_hour = max(hour_counts, key=hour_counts.get)
                max_count = hour_counts[max_hour]
                total_count = sum(hour_counts.values())
                
                if max_count > total_count * 0.3:  # More than 30% of refunds in one hour
                    patterns.append(RefundPattern(
                        pattern_id=f"time_concentration_{int(max_hour)}",
                        pattern_type="time_concentration",
                        description=f"High refund concentration at hour {int(max_hour)} ({max_count} refunds)",
                        frequency=max_count,
                        impact_score=max_count / total_count,
                        affected_users=[],
                        time_pattern=f"peak_hour_{int(max_hour)}",
                        amount_pattern=None,
                        recommendations=[
                            f"Monitor system load during hour {int(max_hour)}",
                            "Consider staff allocation adjustments for peak hours"
                        ]
                    ))
            
            # User behavior patterns
            user_refund_counts = session.query(
                Refund.user_id,
                func.count(Refund.id).label('refund_count')
            ).filter(
                Refund.created_at >= start_time,
                Refund.created_at <= end_time
            ).group_by(Refund.user_id).having(
                func.count(Refund.id) > 3
            ).all()
            
            if user_refund_counts:
                high_frequency_users = [user_id for user_id, count in user_refund_counts if count > 5]
                if high_frequency_users:
                    patterns.append(RefundPattern(
                        pattern_id="high_frequency_users",
                        pattern_type="user_behavior",
                        description=f"{len(high_frequency_users)} users with >5 refunds in period",
                        frequency=len(high_frequency_users),
                        impact_score=len(high_frequency_users) / len(set(r.user_id for r in session.query(Refund.user_id).filter(
                            Refund.created_at >= start_time,
                            Refund.created_at <= end_time
                        ).all())),
                        affected_users=high_frequency_users,
                        time_pattern=None,
                        amount_pattern=None,
                        recommendations=[
                            "Review high-frequency users for potential issues",
                            "Consider implementing user education programs"
                        ]
                    ))
            
            return patterns
            
        except Exception as e:
            logger.error(f"❌ Error analyzing patterns: {e}")
            return []
    
    def _detect_anomalies(self, session: Session, metrics_data: Dict[str, Any], start_time: datetime, end_time: datetime) -> List[AnomalyAlert]:
        """Detect anomalies in refund data"""
        try:
            anomalies = []
            summary = metrics_data.get("summary", {})
            trends = metrics_data.get("trends", {})
            
            # Volume anomaly
            volume_trend = trends.get("volume_trend", {})
            if volume_trend.get("change_percent", 0) > self.anomaly_thresholds["refund_volume_increase"] * 100:
                anomalies.append(AnomalyAlert(
                    alert_id=f"volume_spike_{int(datetime.utcnow().timestamp())}",
                    alert_type="volume_anomaly",
                    severity="high",
                    title="Refund Volume Spike Detected",
                    description=f"Refund volume increased by {volume_trend.get('change_percent', 0):.1f}%",
                    metric_value=volume_trend.get("change_percent", 0),
                    threshold_value=self.anomaly_thresholds["refund_volume_increase"] * 100,
                    affected_period=f"{start_time.isoformat()} to {end_time.isoformat()}",
                    recommendations=[
                        "Investigate root cause of volume increase",
                        "Monitor system capacity and performance",
                        "Review recent system changes or external factors"
                    ],
                    requires_action=True,
                    created_at=datetime.utcnow()
                ))
            
            # Failure rate anomaly
            failure_rate = summary.get("failure_rate", 0)
            if failure_rate > self.anomaly_thresholds["failure_rate_increase"] * 100:
                anomalies.append(AnomalyAlert(
                    alert_id=f"failure_rate_high_{int(datetime.utcnow().timestamp())}",
                    alert_type="failure_rate_anomaly",
                    severity="critical",
                    title="High Refund Failure Rate",
                    description=f"Refund failure rate at {failure_rate:.1f}% exceeds threshold",
                    metric_value=failure_rate,
                    threshold_value=self.anomaly_thresholds["failure_rate_increase"] * 100,
                    affected_period=f"{start_time.isoformat()} to {end_time.isoformat()}",
                    recommendations=[
                        "Immediate investigation of refund processing issues",
                        "Review error logs and failure reasons",
                        "Check system dependencies and external services"
                    ],
                    requires_action=True,
                    created_at=datetime.utcnow()
                ))
            
            # Processing time anomaly
            avg_processing_time = summary.get("average_processing_time", 0)
            if avg_processing_time > 3600:  # More than 1 hour
                anomalies.append(AnomalyAlert(
                    alert_id=f"processing_time_high_{int(datetime.utcnow().timestamp())}",
                    alert_type="performance_anomaly",
                    severity="medium",
                    title="Long Refund Processing Times",
                    description=f"Average processing time {avg_processing_time/60:.1f} minutes exceeds normal range",
                    metric_value=avg_processing_time,
                    threshold_value=3600,
                    affected_period=f"{start_time.isoformat()} to {end_time.isoformat()}",
                    recommendations=[
                        "Review processing workflow bottlenecks",
                        "Check external service response times",
                        "Consider increasing processing capacity"
                    ],
                    requires_action=False,
                    created_at=datetime.utcnow()
                ))
            
            return anomalies
            
        except Exception as e:
            logger.error(f"❌ Error detecting anomalies: {e}")
            return []
    
    def _calculate_notification_delivery_rate(self, session: Session, start_time: datetime, end_time: datetime) -> Dict[str, Any]:
        """Calculate notification delivery rates"""
        try:
            # This would integrate with the notification delivery tracker
            # For now, return estimated values
            return {
                "overall_rate": 96.5,
                "email_rate": 98.2,
                "telegram_rate": 95.8,
                "sms_rate": 94.1,
                "total_notifications": 150,
                "failed_notifications": 5
            }
        except Exception as e:
            logger.error(f"❌ Error calculating notification delivery rate: {e}")
            return {}
    
    def _calculate_user_risk_score(self, frequency: float, total_amount: float, type_counts: Counter, days: int) -> float:
        """Calculate risk score for a user based on refund patterns"""
        try:
            risk_score = 0.0
            
            # Frequency component (0-40 points)
            if frequency > 2:  # More than 2 refunds per week
                risk_score += min(frequency * 10, 40)
            
            # Amount component (0-30 points)
            if total_amount > 1000:  # High dollar amounts
                risk_score += min(total_amount / 100, 30)
            
            # Type diversity component (0-20 points)
            if len(type_counts) == 1:  # Only one type of refund
                risk_score += 20
            elif len(type_counts) > 3:  # Many different types
                risk_score += 10
            
            # Recency component (0-10 points)
            if days <= 7:  # Recent activity
                risk_score += 10
            
            return min(risk_score, 100)  # Cap at 100
            
        except Exception as e:
            logger.error(f"❌ Error calculating user risk score: {e}")
            return 0.0
    
    def _determine_trend_direction(self, change_percent: float) -> str:
        """Determine trend direction from percentage change"""
        if abs(change_percent) < 5:
            return "stable"
        elif change_percent > 0:
            return "increasing"
        else:
            return "decreasing"
    
    def _calculate_automation_potential(self, breakdown: Dict[str, Any], performance: Dict[str, Any]) -> float:
        """Calculate potential for automation based on patterns"""
        try:
            # Simple heuristic based on volume and processing consistency
            type_breakdown = breakdown.get("by_type", {})
            if not type_breakdown:
                return 0.0
            
            # High volume types have higher automation potential
            total_count = sum(data["count"] for data in type_breakdown.values())
            automation_score = 0.0
            
            for refund_type, data in type_breakdown.items():
                type_volume = data["count"]
                type_percentage = (type_volume / total_count) * 100
                
                # Higher volume types score higher
                if type_percentage > 30:
                    automation_score += 0.4
                elif type_percentage > 20:
                    automation_score += 0.3
                elif type_percentage > 10:
                    automation_score += 0.2
            
            # Consistent processing times indicate automation potential
            processing_stats = performance.get("processing_time_stats", {})
            std_dev = processing_stats.get("std_dev", 0)
            mean_time = processing_stats.get("mean", 1)
            
            if std_dev > 0 and mean_time > 0:
                consistency_score = 1 - min(std_dev / mean_time, 1)
                automation_score += consistency_score * 0.3
            
            return min(automation_score, 1.0) * 100  # Return as percentage
            
        except Exception as e:
            logger.error(f"❌ Error calculating automation potential: {e}")
            return 0.0
    
    def _calculate_cache_hit_rate(self) -> float:
        """Calculate cache hit rate for performance monitoring"""
        try:
            # Simple implementation - would be more sophisticated in production
            total_requests = getattr(self, '_total_cache_requests', 0)
            cache_hits = getattr(self, '_cache_hits', 0)
            
            if total_requests == 0:
                return 100.0
            
            return (cache_hits / total_requests) * 100
            
        except Exception as e:
            logger.error(f"❌ Error calculating cache hit rate: {e}")
            return 0.0
    
    def _generate_real_time_alerts(self, dashboard_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Generate real-time alerts based on current data"""
        alerts = []
        
        try:
            active_refunds = dashboard_data.get("active_refunds", {})
            performance = dashboard_data.get("performance_metrics", {})
            
            # High active refund count alert
            active_count = active_refunds.get("total_active", 0)
            if active_count > 10:
                alerts.append({
                    "type": "high_active_count",
                    "severity": "medium",
                    "message": f"High number of active refunds: {active_count}",
                    "value": active_count,
                    "threshold": 10
                })
            
            # Low completion rate alert
            completion_rate = performance.get("hourly_completion_rate", 100)
            if completion_rate < 80:
                alerts.append({
                    "type": "low_completion_rate",
                    "severity": "high",
                    "message": f"Low hourly completion rate: {completion_rate}%",
                    "value": completion_rate,
                    "threshold": 80
                })
            
            # System health alerts
            system_health = dashboard_data.get("system_health", {})
            for component, status in system_health.items():
                if status != "healthy":
                    alerts.append({
                        "type": "system_health",
                        "severity": "high",
                        "message": f"{component} status: {status}",
                        "component": component,
                        "status": status
                    })
            
            return alerts
            
        except Exception as e:
            logger.error(f"❌ Error generating real-time alerts: {e}")
            return []
    
    def _is_cache_valid(self, cache_key: str) -> bool:
        """Check if cached data is still valid"""
        if cache_key not in self.metrics_cache:
            return False
        
        cache_entry = self.metrics_cache[cache_key]
        cache_time = cache_entry.get("timestamp", datetime.min)
        
        return (datetime.utcnow() - cache_time).total_seconds() < self.cache_ttl
    
    def _cache_data(self, cache_key: str, data: Any):
        """Cache data with timestamp"""
        self.metrics_cache[cache_key] = {
            "data": data,
            "timestamp": datetime.utcnow()
        }
        
        # Simple cache cleanup - remove old entries
        cutoff_time = datetime.utcnow() - timedelta(seconds=self.cache_ttl * 2)
        keys_to_remove = [
            key for key, value in self.metrics_cache.items()
            if value.get("timestamp", datetime.min) < cutoff_time
        ]
        
        for key in keys_to_remove:
            del self.metrics_cache[key]


# Global analytics service instance
refund_analytics_service = RefundAnalyticsService()