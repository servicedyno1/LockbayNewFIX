"""
Comprehensive Monitoring Dashboard
Integrates performance baselines, health scoring, trend analysis, and real-time monitoring
"""

import logging
import asyncio
import time
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime
from dataclasses import dataclass, asdict
from enum import Enum
from collections import deque
import statistics

from utils.standardized_metrics_framework import standardized_metrics
from utils.performance_baselines_config import (
    performance_baselines, PerformanceLevel, create_performance_report, get_baselines_summary
)
from utils.central_metrics_aggregator import get_aggregation_summary
from utils.unified_activity_monitor import unified_monitor
from utils.system_health import SystemHealthMonitor

logger = logging.getLogger(__name__)


class HealthScore(Enum):
    """Overall system health score levels"""
    EXCELLENT = "excellent"
    GOOD = "good" 
    DEGRADED = "degraded"
    CRITICAL = "critical"
    EMERGENCY = "emergency"


@dataclass
class TrendData:
    """Performance trend analysis data"""
    metric_name: str
    current_value: float
    baseline_value: float
    trend_direction: str  # improving, degrading, stable
    change_percentage: float
    data_points: int
    analysis_period_hours: float
    confidence: float  # 0.0-1.0


@dataclass
class AlertStatus:
    """Current alert status for a metric"""
    metric_name: str
    alert_level: PerformanceLevel
    message: str
    first_triggered: datetime
    last_updated: datetime
    auto_remediated: bool = False
    escalation_count: int = 0


@dataclass
class SystemHealthSummary:
    """Comprehensive system health summary"""
    overall_score: HealthScore
    score_percentage: float
    healthy_metrics: int
    warning_metrics: int
    critical_metrics: int
    emergency_metrics: int
    total_metrics: int
    active_alerts: int
    trend_summary: str
    last_updated: datetime


class ComprehensiveMonitoringDashboard:
    """
    Central monitoring dashboard that provides real-time health assessment,
    baseline comparisons, trend analysis, and intelligent alerting
    """
    
    def __init__(self):
        self.is_active = False
        self.dashboard_task: Optional[asyncio.Task] = None
        
        # Health tracking
        self.health_history = deque(maxlen=288)  # 24 hours of 5-minute intervals
        self.current_health_summary = None
        
        # Alert tracking
        self.active_alerts: Dict[str, AlertStatus] = {}
        self.alert_history = deque(maxlen=1000)
        self.alert_suppression = {}  # For noise reduction
        
        # Trend analysis
        self.metric_trends: Dict[str, TrendData] = {}
        self.trend_history = deque(maxlen=100)  # Recent trend calculations
        
        # Performance tracking
        self.performance_snapshots = deque(maxlen=144)  # 12 hours of 5-minute intervals
        self.baseline_drift_alerts = {}
        
        # Dashboard data cache
        self.dashboard_cache = {}
        self.last_cache_update = datetime.min
        self.cache_ttl_seconds = 30
        
        logger.info("🔧 Comprehensive Monitoring Dashboard initialized")
    
    async def start_monitoring(self):
        """Start the comprehensive monitoring dashboard"""
        if self.is_active:
            logger.warning("Monitoring dashboard already active")
            return
        
        self.is_active = True
        self.dashboard_task = asyncio.create_task(self._monitoring_loop())
        
        logger.info("🚀 Started comprehensive monitoring dashboard")
    
    async def stop_monitoring(self):
        """Stop the comprehensive monitoring dashboard"""
        self.is_active = False
        
        if self.dashboard_task:
            self.dashboard_task.cancel()
            try:
                await self.dashboard_task
            except asyncio.CancelledError:
                pass
        
        logger.info("🛑 Stopped comprehensive monitoring dashboard")
    
    async def _monitoring_loop(self):
        """Main monitoring loop that updates all dashboard components"""
        logger.info("📊 Starting comprehensive monitoring dashboard loop")
        
        while self.is_active:
            try:
                start_time = time.time()
                
                # Collect current metrics and evaluate against baselines
                await self._collect_and_evaluate_metrics()
                
                # Update trend analysis
                await self._update_trend_analysis()
                
                # Process alerts and health scoring
                await self._process_alerts_and_health()
                
                # Update dashboard cache
                await self._update_dashboard_cache()
                
                # Log summary every 5 minutes
                if int(time.time()) % 300 == 0:  # Every 5 minutes
                    await self._log_health_summary()
                
                processing_time = time.time() - start_time
                
                # Log processing performance
                if processing_time > 2.0:
                    logger.warning(f"⏳ Dashboard processing took {processing_time:.2f}s")
                else:
                    logger.debug(f"📊 Dashboard updated in {processing_time:.2f}s")
                
            except Exception as e:
                logger.error(f"Error in monitoring dashboard loop: {e}")
            
            await asyncio.sleep(30)  # Update every 30 seconds
    
    async def _collect_and_evaluate_metrics(self):
        """Collect current metrics and evaluate against baselines"""
        try:
            # Get current metrics from standardized framework
            current_metrics = standardized_metrics.get_current_metrics()
            
            if not current_metrics:
                logger.debug("No current metrics available for evaluation")
                return
            
            # Extract metric values for baseline evaluation
            metric_values = {}
            for metric in current_metrics:
                metric_values[metric.name] = metric.value
            
            # Create performance evaluation report
            performance_report = create_performance_report(metric_values)
            
            # Store performance snapshot
            snapshot = {
                'timestamp': datetime.utcnow(),
                'metrics': metric_values,
                'performance_report': performance_report,
                'overall_status': performance_report.get('overall_status', 'unknown')
            }
            
            self.performance_snapshots.append(snapshot)
            
            # Update alerts based on baseline evaluations
            await self._update_alerts_from_evaluations(performance_report)
            
        except Exception as e:
            logger.error(f"Error collecting and evaluating metrics: {e}")
    
    async def _update_alerts_from_evaluations(self, performance_report: Dict[str, Any]):
        """Update alert status based on performance evaluations"""
        try:
            evaluations = performance_report.get('evaluations', {})
            current_time = datetime.utcnow()
            
            # Process each metric evaluation
            for metric_name, evaluation in evaluations.items():
                if not evaluation.get('baseline_available'):
                    continue
                
                performance_level = evaluation.get('performance_level')
                
                # Check if this is an alertable condition
                if performance_level in ['warning', 'critical', 'emergency']:
                    alert_level = PerformanceLevel(performance_level)
                    
                    # Check for existing alert
                    if metric_name in self.active_alerts:
                        # Update existing alert
                        alert = self.active_alerts[metric_name]
                        
                        # Check if alert level escalated
                        old_level = alert.alert_level
                        if self._is_escalation(old_level, alert_level):
                            alert.escalation_count += 1
                            logger.warning(f"🔺 Alert escalated for {metric_name}: {old_level.value} → {alert_level.value}")
                        
                        alert.alert_level = alert_level
                        alert.message = evaluation.get('message', f'{metric_name} performance issue')
                        alert.last_updated = current_time
                    else:
                        # Create new alert
                        alert = AlertStatus(
                            metric_name=metric_name,
                            alert_level=alert_level,
                            message=evaluation.get('message', f'{metric_name} performance issue'),
                            first_triggered=current_time,
                            last_updated=current_time
                        )
                        self.active_alerts[metric_name] = alert
                        
                        # Log new alert
                        status_icon = evaluation.get('status_icon', '⚠️')
                        logger.warning(f"{status_icon} NEW ALERT: {metric_name} - {alert.message}")
                
                else:
                    # Performance is good, clear any existing alert
                    if metric_name in self.active_alerts:
                        resolved_alert = self.active_alerts.pop(metric_name)
                        
                        # Add to alert history
                        alert_record = {
                            'metric_name': metric_name,
                            'resolved_at': current_time.isoformat(),
                            'duration_minutes': (current_time - resolved_alert.first_triggered).total_seconds() / 60,
                            'max_level': resolved_alert.alert_level.value,
                            'escalation_count': resolved_alert.escalation_count
                        }
                        self.alert_history.append(alert_record)
                        
                        logger.info(f"✅ Alert resolved for {metric_name} after {alert_record['duration_minutes']:.1f} minutes")
        
        except Exception as e:
            logger.error(f"Error updating alerts from evaluations: {e}")
    
    def _is_escalation(self, old_level: PerformanceLevel, new_level: PerformanceLevel) -> bool:
        """Check if the new alert level is an escalation from the old level"""
        level_order = {
            PerformanceLevel.WARNING: 1,
            PerformanceLevel.CRITICAL: 2,
            PerformanceLevel.EMERGENCY: 3
        }
        
        return level_order.get(new_level, 0) > level_order.get(old_level, 0)
    
    async def _update_trend_analysis(self):
        """Update trend analysis for all metrics"""
        try:
            if len(self.performance_snapshots) < 3:
                return  # Need at least 3 data points for trend analysis
            
            # Get recent snapshots for analysis
            recent_snapshots = list(self.performance_snapshots)[-12:]  # Last 6 minutes of data
            
            # Analyze trends for each metric
            all_metric_names = set()
            for snapshot in recent_snapshots:
                all_metric_names.update(snapshot['metrics'].keys())
            
            for metric_name in all_metric_names:
                await self._analyze_metric_trend(metric_name, recent_snapshots)
        
        except Exception as e:
            logger.error(f"Error updating trend analysis: {e}")
    
    async def _analyze_metric_trend(self, metric_name: str, snapshots: List[Dict]):
        """Analyze trend for a specific metric"""
        try:
            # Extract values for this metric
            values = []
            timestamps = []
            
            for snapshot in snapshots:
                if metric_name in snapshot['metrics']:
                    values.append(snapshot['metrics'][metric_name])
                    timestamps.append(snapshot['timestamp'])
            
            if len(values) < 3:
                return
            
            # Calculate trend
            current_value = values[-1]
            
            # Get baseline for comparison
            baseline = performance_baselines.get_baseline_for_metric(metric_name)
            baseline_value = baseline.excellent_max if baseline else None
            
            # Calculate trend direction using linear regression approximation
            trend_direction, change_percentage = self._calculate_trend_direction(values)
            
            # Calculate analysis period
            analysis_period_hours = (timestamps[-1] - timestamps[0]).total_seconds() / 3600
            
            # Calculate confidence based on data consistency
            confidence = min(1.0, len(values) / 10.0)  # Full confidence at 10+ data points
            
            # Create trend data
            trend_data = TrendData(
                metric_name=metric_name,
                current_value=current_value,
                baseline_value=baseline_value or 0.0,
                trend_direction=trend_direction,
                change_percentage=change_percentage,
                data_points=len(values),
                analysis_period_hours=analysis_period_hours,
                confidence=confidence
            )
            
            self.metric_trends[metric_name] = trend_data
            
            # Check for concerning trends
            if trend_direction == 'degrading' and abs(change_percentage) > 20 and confidence > 0.7:
                logger.warning(f"📉 Degrading trend detected: {metric_name} - {change_percentage:+.1f}% change")
        
        except Exception as e:
            logger.debug(f"Error analyzing trend for {metric_name}: {e}")
    
    def _calculate_trend_direction(self, values: List[float]) -> Tuple[str, float]:
        """Calculate trend direction and change percentage"""
        try:
            if len(values) < 2:
                return 'stable', 0.0
            
            # Simple trend: compare first half vs second half
            mid_point = len(values) // 2
            first_half_avg = statistics.mean(values[:mid_point])
            second_half_avg = statistics.mean(values[mid_point:])
            
            # Calculate percentage change
            if first_half_avg != 0:
                change_percentage = ((second_half_avg - first_half_avg) / first_half_avg) * 100
            else:
                change_percentage = 0.0
            
            # Determine direction
            if abs(change_percentage) < 5.0:
                return 'stable', change_percentage
            elif change_percentage > 0:
                return 'degrading', change_percentage  # Assuming higher values are worse
            else:
                return 'improving', change_percentage
        
        except Exception as e:
            logger.debug(f"Error calculating trend direction: {e}")
            return 'stable', 0.0
    
    async def _process_alerts_and_health(self):
        """Process current alerts and calculate overall health score"""
        try:
            datetime.utcnow()
            
            # Count alerts by severity
            warning_count = sum(1 for alert in self.active_alerts.values() 
                              if alert.alert_level == PerformanceLevel.WARNING)
            critical_count = sum(1 for alert in self.active_alerts.values() 
                               if alert.alert_level == PerformanceLevel.CRITICAL)
            emergency_count = sum(1 for alert in self.active_alerts.values() 
                                if alert.alert_level == PerformanceLevel.EMERGENCY)
            
            # Get latest performance report
            latest_performance = self.performance_snapshots[-1] if self.performance_snapshots else {}
            performance_report = latest_performance.get('performance_report', {})
            
            # Calculate health score
            health_summary = await self._calculate_health_score(
                performance_report, warning_count, critical_count, emergency_count
            )
            
            self.current_health_summary = health_summary
            self.health_history.append(health_summary)
            
        except Exception as e:
            logger.error(f"Error processing alerts and health: {e}")
    
    async def _calculate_health_score(self, performance_report: Dict, 
                                    warning_count: int, critical_count: int, 
                                    emergency_count: int) -> SystemHealthSummary:
        """Calculate comprehensive system health score"""
        try:
            # Get performance level counts from baseline evaluation
            summary = performance_report.get('summary', {})
            excellent_count = summary.get('excellent_count', 0)
            good_count = summary.get('good_count', 0)
            warning_metrics = summary.get('warning_count', 0)
            critical_metrics = summary.get('critical_count', 0)
            emergency_metrics = summary.get('emergency_count', 0)
            total_metrics = summary.get('total_metrics', 1)
            
            # Calculate base health score (0-100)
            healthy_metrics = excellent_count + good_count
            
            # Start with percentage of healthy metrics
            score_percentage = (healthy_metrics / total_metrics) * 100 if total_metrics > 0 else 100
            
            # Apply penalties for issues
            score_percentage -= warning_metrics * 5  # -5% per warning
            score_percentage -= critical_metrics * 15  # -15% per critical
            score_percentage -= emergency_metrics * 30  # -30% per emergency
            
            # Apply additional penalties for active alerts
            score_percentage -= warning_count * 3
            score_percentage -= critical_count * 10
            score_percentage -= emergency_count * 25
            
            # Ensure score stays within bounds
            score_percentage = max(0, min(100, score_percentage))
            
            # Determine overall health score level
            if score_percentage >= 90:
                overall_score = HealthScore.EXCELLENT
            elif score_percentage >= 75:
                overall_score = HealthScore.GOOD
            elif score_percentage >= 60:
                overall_score = HealthScore.DEGRADED
            elif score_percentage >= 30:
                overall_score = HealthScore.CRITICAL
            else:
                overall_score = HealthScore.EMERGENCY
            
            # Generate trend summary
            trend_summary = self._generate_trend_summary()
            
            return SystemHealthSummary(
                overall_score=overall_score,
                score_percentage=score_percentage,
                healthy_metrics=healthy_metrics,
                warning_metrics=warning_metrics,
                critical_metrics=critical_metrics,
                emergency_metrics=emergency_metrics,
                total_metrics=total_metrics,
                active_alerts=len(self.active_alerts),
                trend_summary=trend_summary,
                last_updated=datetime.utcnow()
            )
        
        except Exception as e:
            logger.error(f"Error calculating health score: {e}")
            return SystemHealthSummary(
                overall_score=HealthScore.CRITICAL,
                score_percentage=0.0,
                healthy_metrics=0,
                warning_metrics=0,
                critical_metrics=0,
                emergency_metrics=0,
                total_metrics=0,
                active_alerts=0,
                trend_summary="Error calculating trends",
                last_updated=datetime.utcnow()
            )
    
    def _generate_trend_summary(self) -> str:
        """Generate a summary of current trends"""
        try:
            if not self.metric_trends:
                return "No trend data available"
            
            degrading_trends = [t for t in self.metric_trends.values() 
                              if t.trend_direction == 'degrading' and t.confidence > 0.5]
            improving_trends = [t for t in self.metric_trends.values() 
                               if t.trend_direction == 'improving' and t.confidence > 0.5]
            
            if degrading_trends:
                worst_degrading = max(degrading_trends, key=lambda t: abs(t.change_percentage))
                return f"⚠️ {len(degrading_trends)} metrics degrading (worst: {worst_degrading.metric_name} {worst_degrading.change_percentage:+.1f}%)"
            elif improving_trends:
                best_improving = max(improving_trends, key=lambda t: abs(t.change_percentage))
                return f"✅ {len(improving_trends)} metrics improving (best: {best_improving.metric_name} {best_improving.change_percentage:+.1f}%)"
            else:
                return "📊 All metrics stable"
        
        except Exception as e:
            logger.debug(f"Error generating trend summary: {e}")
            return "Trend analysis unavailable"
    
    async def _update_dashboard_cache(self):
        """Update dashboard data cache"""
        try:
            current_time = datetime.utcnow()
            
            # Check if cache needs update
            if (current_time - self.last_cache_update).total_seconds() < self.cache_ttl_seconds:
                return
            
            # Get comprehensive dashboard data
            dashboard_data = await self.get_comprehensive_dashboard_data()
            
            self.dashboard_cache = dashboard_data
            self.last_cache_update = current_time
            
        except Exception as e:
            logger.error(f"Error updating dashboard cache: {e}")
    
    async def _log_health_summary(self):
        """Log comprehensive health summary"""
        try:
            if not self.current_health_summary:
                return
            
            health = self.current_health_summary
            
            # Get status icon
            status_icons = {
                HealthScore.EXCELLENT: "🟢",
                HealthScore.GOOD: "🟡",
                HealthScore.DEGRADED: "🟠",
                HealthScore.CRITICAL: "🔴",
                HealthScore.EMERGENCY: "🚨"
            }
            
            status_icon = status_icons.get(health.overall_score, "❓")
            
            logger.info(
                f"{status_icon} SYSTEM HEALTH: {health.overall_score.value.upper()} "
                f"({health.score_percentage:.1f}%) | "
                f"Metrics: {health.healthy_metrics} healthy, {health.warning_metrics} warning, "
                f"{health.critical_metrics} critical, {health.emergency_metrics} emergency | "
                f"Active Alerts: {health.active_alerts} | "
                f"Trends: {health.trend_summary}"
            )
            
        except Exception as e:
            logger.error(f"Error logging health summary: {e}")
    
    async def get_comprehensive_dashboard_data(self) -> Dict[str, Any]:
        """Get comprehensive dashboard data for display"""
        try:
            current_time = datetime.utcnow()
            
            # Use cached data if fresh
            if (current_time - self.last_cache_update).total_seconds() < self.cache_ttl_seconds:
                return self.dashboard_cache
            
            # Get current performance report
            current_metrics = standardized_metrics.get_current_metrics()
            metric_values = {metric.name: metric.value for metric in current_metrics}
            performance_report = create_performance_report(metric_values)
            
            # Get system health data
            system_health_data = SystemHealthMonitor.check_system_health()
            
            # Get unified activity monitor data
            activity_data = unified_monitor.get_live_dashboard_data()
            
            # Get aggregation summary
            aggregation_data = get_aggregation_summary()
            
            # Get baselines summary
            baselines_data = get_baselines_summary()
            
            # Compile comprehensive dashboard data
            dashboard_data = {
                'timestamp': current_time.isoformat(),
                'system_health_summary': asdict(self.current_health_summary) if self.current_health_summary else None,
                'performance_evaluation': performance_report,
                'current_metrics': [
                    {
                        'name': metric.name,
                        'value': metric.value,
                        'unit': metric.unit.value,
                        'category': metric.category.value,
                        'formatted_value': metric.format_value()
                    }
                    for metric in current_metrics
                ],
                'active_alerts': {
                    name: {
                        'level': alert.alert_level.value,
                        'message': alert.message,
                        'first_triggered': alert.first_triggered.isoformat(),
                        'duration_minutes': (current_time - alert.first_triggered).total_seconds() / 60,
                        'escalation_count': alert.escalation_count
                    }
                    for name, alert in self.active_alerts.items()
                },
                'metric_trends': {
                    name: asdict(trend) for name, trend in self.metric_trends.items()
                },
                'system_health': system_health_data,
                'activity_monitor': activity_data,
                'metrics_aggregation': aggregation_data,
                'baselines_configuration': baselines_data,
                'alert_statistics': {
                    'active_count': len(self.active_alerts),
                    'warning_count': sum(1 for a in self.active_alerts.values() if a.alert_level == PerformanceLevel.WARNING),
                    'critical_count': sum(1 for a in self.active_alerts.values() if a.alert_level == PerformanceLevel.CRITICAL),
                    'emergency_count': sum(1 for a in self.active_alerts.values() if a.alert_level == PerformanceLevel.EMERGENCY),
                    'recent_resolved': len(self.alert_history)
                },
                'performance_trends': {
                    'snapshots_count': len(self.performance_snapshots),
                    'health_history_count': len(self.health_history),
                    'trend_analysis_count': len(self.metric_trends),
                    'degrading_metrics': len([t for t in self.metric_trends.values() if t.trend_direction == 'degrading']),
                    'improving_metrics': len([t for t in self.metric_trends.values() if t.trend_direction == 'improving'])
                }
            }
            
            return dashboard_data
            
        except Exception as e:
            logger.error(f"Error getting comprehensive dashboard data: {e}")
            return {
                'timestamp': datetime.utcnow().isoformat(),
                'error': str(e),
                'system_health_summary': None
            }
    
    def get_health_score(self) -> Dict[str, Any]:
        """Get current health score summary"""
        if self.current_health_summary:
            return asdict(self.current_health_summary)
        else:
            return {'overall_score': 'unknown', 'score_percentage': 0.0}
    
    def get_active_alerts(self) -> Dict[str, Any]:
        """Get current active alerts"""
        return {
            name: {
                'level': alert.alert_level.value,
                'message': alert.message,
                'first_triggered': alert.first_triggered.isoformat(),
                'escalation_count': alert.escalation_count
            }
            for name, alert in self.active_alerts.items()
        }
    
    def get_metric_trends(self) -> Dict[str, Any]:
        """Get current metric trends"""
        return {
            name: asdict(trend) for name, trend in self.metric_trends.items()
        }


# Global instance for easy access
comprehensive_dashboard = ComprehensiveMonitoringDashboard()


# Convenience functions
async def start_comprehensive_monitoring():
    """Start comprehensive monitoring dashboard"""
    await comprehensive_dashboard.start_monitoring()


async def stop_comprehensive_monitoring():
    """Stop comprehensive monitoring dashboard"""
    await comprehensive_dashboard.stop_monitoring()


def get_dashboard_data() -> Dict[str, Any]:
    """Get comprehensive dashboard data"""
    return comprehensive_dashboard.dashboard_cache or {}


def get_current_health_score() -> Dict[str, Any]:
    """Get current system health score"""
    return comprehensive_dashboard.get_health_score()


def get_current_alerts() -> Dict[str, Any]:
    """Get current active alerts"""
    return comprehensive_dashboard.get_active_alerts()


# Auto-start monitoring when module is imported
try:
    asyncio.create_task(start_comprehensive_monitoring())
    logger.info("📊 Comprehensive Monitoring Dashboard scheduled for startup")
except RuntimeError:
    # Event loop not running yet, will be started later
    logger.info("📊 Comprehensive Monitoring Dashboard ready for manual startup")