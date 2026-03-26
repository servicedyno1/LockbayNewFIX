"""
Unified Performance Reporting System
Provides consistent reporting formats and logging across all performance monitoring
"""

import logging
import asyncio
import json
from typing import Dict, List, Optional, Any, Union
from datetime import datetime
from dataclasses import dataclass, asdict
from enum import Enum
import time
from collections import defaultdict

from utils.standardized_metrics_framework import (
    standardized_metrics, StandardMetric
)
from utils.central_metrics_aggregator import get_aggregation_summary
from utils.performance_baselines_config import (
    performance_baselines, create_performance_report, get_baselines_summary
)
from utils.monitoring_systems_integration import get_integration_status

logger = logging.getLogger(__name__)


class ReportFormat(Enum):
    """Supported report formats"""
    JSON = "json"
    STRUCTURED_LOG = "structured_log"
    SUMMARY = "summary"
    DETAILED = "detailed"
    DASHBOARD = "dashboard"


class ReportFrequency(Enum):
    """Report generation frequencies"""
    REAL_TIME = "real_time"
    EVERY_MINUTE = "every_minute"
    EVERY_5_MINUTES = "every_5_minutes"
    EVERY_15_MINUTES = "every_15_minutes"
    HOURLY = "hourly"
    DAILY = "daily"


@dataclass
class PerformanceReport:
    """Comprehensive performance report structure"""
    timestamp: datetime
    report_id: str
    report_type: str
    summary: Dict[str, Any]
    metrics: Dict[str, Any]
    baselines_evaluation: Dict[str, Any]
    system_health: Dict[str, Any]
    trends: Dict[str, Any]
    alerts: List[Dict[str, Any]]
    recommendations: List[str]
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization"""
        result = asdict(self)
        result['timestamp'] = self.timestamp.isoformat()
        return result


class UnifiedPerformanceReporter:
    """
    Central performance reporting system that provides consistent
    reporting formats across all monitoring components
    """
    
    def __init__(self):
        self.is_active = False
        self.reporting_tasks: Dict[ReportFrequency, asyncio.Task] = {}
        self.report_history = defaultdict(lambda: [])  # Keep report history by type
        self.alert_history = []
        self.last_report_timestamp: Dict[str, datetime] = {}
        
        # Report generation configuration
        self.enabled_frequencies = {
            ReportFrequency.EVERY_5_MINUTES,
            ReportFrequency.EVERY_15_MINUTES,
            ReportFrequency.HOURLY
        }
        
        # Alert configuration
        self.alert_thresholds = {
            'critical_metrics_count': 3,
            'emergency_metrics_count': 1,
            'system_degradation_duration_minutes': 10
        }
        
        logger.info("🔧 Unified Performance Reporter initialized")
    
    async def start_reporting(self):
        """Start automated performance reporting"""
        if self.is_active:
            logger.warning("Performance reporting already active")
            return
        
        self.is_active = True
        
        # Start reporting tasks for each enabled frequency
        for frequency in self.enabled_frequencies:
            task = asyncio.create_task(self._reporting_loop(frequency))
            self.reporting_tasks[frequency] = task
        
        logger.info(f"🚀 Started unified performance reporting for {len(self.enabled_frequencies)} frequencies")
    
    async def stop_reporting(self):
        """Stop automated performance reporting"""
        self.is_active = False
        
        # Cancel all reporting tasks
        for task in self.reporting_tasks.values():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        
        self.reporting_tasks.clear()
        logger.info("🛑 Stopped unified performance reporting")
    
    async def _reporting_loop(self, frequency: ReportFrequency):
        """Main reporting loop for a specific frequency"""
        interval_seconds = self._get_interval_seconds(frequency)
        
        logger.info(f"📊 Starting {frequency.value} reporting loop (every {interval_seconds}s)")
        
        while self.is_active:
            try:
                # Generate and process report
                report = await self.generate_comprehensive_report(f"scheduled_{frequency.value}")
                
                # Log the report
                await self._log_report(report, ReportFormat.STRUCTURED_LOG)
                
                # Store in history
                self.report_history[frequency.value].append(report)
                
                # Keep only recent reports (last 50 for each frequency)
                if len(self.report_history[frequency.value]) > 50:
                    self.report_history[frequency.value] = self.report_history[frequency.value][-50:]
                
                # Check for alerts
                await self._check_and_generate_alerts(report)
                
                self.last_report_timestamp[frequency.value] = datetime.utcnow()
                
            except Exception as e:
                logger.error(f"Error in {frequency.value} reporting loop: {e}")
            
            await asyncio.sleep(interval_seconds)
    
    def _get_interval_seconds(self, frequency: ReportFrequency) -> int:
        """Get interval in seconds for a reporting frequency"""
        intervals = {
            ReportFrequency.REAL_TIME: 10,
            ReportFrequency.EVERY_MINUTE: 60,
            ReportFrequency.EVERY_5_MINUTES: 300,
            ReportFrequency.EVERY_15_MINUTES: 900,
            ReportFrequency.HOURLY: 3600,
            ReportFrequency.DAILY: 86400
        }
        return intervals.get(frequency, 300)
    
    async def generate_comprehensive_report(self, report_type: str = "on_demand") -> PerformanceReport:
        """Generate comprehensive performance report"""
        report_start_time = time.time()
        timestamp = datetime.utcnow()
        
        try:
            # Collect current metrics from standardized framework
            current_metrics = standardized_metrics.get_current_metrics()
            
            # Get aggregation summary
            aggregation_summary = get_aggregation_summary()
            
            # Get integration status
            integration_status = get_integration_status()
            
            # Get baselines summary
            baselines_summary = get_baselines_summary()
            
            # Extract metric values for evaluation
            metric_values = {}
            for metric in current_metrics:
                metric_values[metric.name] = metric.value
            
            # Create performance evaluation report
            baselines_evaluation = create_performance_report(metric_values)
            
            # Generate system health summary
            system_health = await self._generate_system_health_summary(current_metrics)
            
            # Analyze trends
            trends = await self._analyze_performance_trends()
            
            # Generate alerts
            alerts = await self._generate_alert_summary(baselines_evaluation, system_health)
            
            # Generate recommendations
            recommendations = await self._generate_recommendations(baselines_evaluation, trends, alerts)
            
            # Create comprehensive summary
            summary = {
                'report_generation_time_ms': (time.time() - report_start_time) * 1000,
                'total_metrics_collected': len(current_metrics),
                'metrics_with_baselines': baselines_evaluation.get('metrics_evaluated', 0),
                'overall_performance_level': baselines_evaluation.get('overall_status', 'unknown'),
                'system_health_status': system_health.get('overall_status', 'unknown'),
                'integration_success_rate': integration_status.get('success_rate', 0),
                'aggregation_active': aggregation_summary.get('total_collectors', 0) > 0,
                'alerts_count': len(alerts),
                'recommendations_count': len(recommendations)
            }
            
            # Create the report
            report = PerformanceReport(
                timestamp=timestamp,
                report_id=f"{report_type}_{int(timestamp.timestamp())}",
                report_type=report_type,
                summary=summary,
                metrics={
                    'current_metrics': [self._metric_to_dict(m) for m in current_metrics],
                    'aggregation_summary': aggregation_summary,
                    'integration_status': integration_status,
                    'baselines_summary': baselines_summary
                },
                baselines_evaluation=baselines_evaluation,
                system_health=system_health,
                trends=trends,
                alerts=alerts,
                recommendations=recommendations
            )
            
            return report
            
        except Exception as e:
            logger.error(f"Error generating comprehensive report: {e}")
            
            # Return minimal error report
            return PerformanceReport(
                timestamp=timestamp,
                report_id=f"error_{int(timestamp.timestamp())}",
                report_type=f"{report_type}_error",
                summary={'error': str(e), 'report_generation_failed': True},
                metrics={},
                baselines_evaluation={},
                system_health={'overall_status': 'error'},
                trends={},
                alerts=[],
                recommendations=['Fix report generation error']
            )
    
    def _metric_to_dict(self, metric: StandardMetric) -> Dict[str, Any]:
        """Convert StandardMetric to dictionary"""
        return {
            'name': metric.name,
            'value': metric.value,
            'unit': metric.unit.value,
            'metric_type': metric.metric_type.value,
            'category': metric.category.value,
            'labels': metric.labels,
            'source': metric.source,
            'timestamp': metric.timestamp.isoformat()
        }
    
    async def _generate_system_health_summary(self, current_metrics: List[StandardMetric]) -> Dict[str, Any]:
        """Generate comprehensive system health summary"""
        try:
            # Group metrics by category
            metrics_by_category = defaultdict(list)
            for metric in current_metrics:
                metrics_by_category[metric.category.value].append(metric)
            
            # Evaluate health for each category
            category_health = {}
            overall_issues = []
            
            for category, metrics in metrics_by_category.items():
                category_evaluation = {
                    'metrics_count': len(metrics),
                    'status': 'healthy',
                    'issues': []
                }
                
                for metric in metrics:
                    evaluation = performance_baselines.evaluate_performance(metric.name, metric.value)
                    if evaluation.get('baseline_available'):
                        performance_level = evaluation.get('performance_level')
                        
                        if performance_level in ['critical', 'emergency']:
                            category_evaluation['status'] = 'critical'
                            issue = f"{metric.name}: {evaluation.get('message', 'Performance issue')}"
                            category_evaluation['issues'].append(issue)
                            overall_issues.append(issue)
                        elif performance_level == 'warning' and category_evaluation['status'] == 'healthy':
                            category_evaluation['status'] = 'warning'
                
                category_health[category] = category_evaluation
            
            # Determine overall status
            if any(cat['status'] == 'critical' for cat in category_health.values()):
                overall_status = 'critical'
            elif any(cat['status'] == 'warning' for cat in category_health.values()):
                overall_status = 'warning'
            else:
                overall_status = 'healthy'
            
            return {
                'overall_status': overall_status,
                'category_health': category_health,
                'total_metrics': len(current_metrics),
                'total_issues': len(overall_issues),
                'issues_summary': overall_issues
            }
            
        except Exception as e:
            logger.error(f"Error generating system health summary: {e}")
            return {
                'overall_status': 'error',
                'error': str(e)
            }
    
    async def _analyze_performance_trends(self) -> Dict[str, Any]:
        """Analyze performance trends over time"""
        try:
            # Get recent reports for trend analysis
            recent_reports = []
            for frequency_reports in self.report_history.values():
                recent_reports.extend(frequency_reports[-10:])  # Last 10 reports per frequency
            
            # Sort by timestamp
            recent_reports.sort(key=lambda r: r.timestamp, reverse=True)
            recent_reports = recent_reports[:20]  # Keep only most recent 20
            
            trends = {
                'data_points': len(recent_reports),
                'analysis_period_hours': 0,
                'performance_trend': 'stable',
                'memory_trend': 'stable',
                'cpu_trend': 'stable',
                'alert_trend': 'stable'
            }
            
            if len(recent_reports) >= 3:
                # Calculate analysis period
                oldest_timestamp = min(r.timestamp for r in recent_reports)
                trends['analysis_period_hours'] = (datetime.utcnow() - oldest_timestamp).total_seconds() / 3600
                
                # Analyze overall performance levels
                performance_levels = [r.baselines_evaluation.get('overall_status', 'unknown') for r in recent_reports]
                trends['performance_trend'] = self._calculate_trend(performance_levels, 'performance')
                
                # Analyze alerts
                alert_counts = [len(r.alerts) for r in recent_reports]
                trends['alert_trend'] = self._calculate_numeric_trend(alert_counts, 'alerts')
            
            return trends
            
        except Exception as e:
            logger.error(f"Error analyzing performance trends: {e}")
            return {'error': str(e)}
    
    def _calculate_trend(self, values: List[str], trend_type: str) -> str:
        """Calculate trend for categorical values"""
        if len(values) < 3:
            return 'insufficient_data'
        
        # Map performance levels to numeric values
        level_values = {
            'excellent': 5,
            'good': 4,
            'warning': 3,
            'critical': 2,
            'emergency': 1,
            'unknown': 0
        }
        
        numeric_values = [level_values.get(v, 0) for v in values]
        return self._calculate_numeric_trend(numeric_values, trend_type)
    
    def _calculate_numeric_trend(self, values: List[Union[int, float]], trend_type: str) -> str:
        """Calculate trend for numeric values"""
        if len(values) < 3:
            return 'insufficient_data'
        
        # Simple trend analysis - compare recent half vs older half
        mid_point = len(values) // 2
        recent_avg = sum(values[:mid_point]) / mid_point
        older_avg = sum(values[mid_point:]) / (len(values) - mid_point)
        
        if abs(recent_avg - older_avg) < 0.1:
            return 'stable'
        elif recent_avg > older_avg:
            return 'improving' if trend_type == 'performance' else 'increasing'
        else:
            return 'degrading' if trend_type == 'performance' else 'decreasing'
    
    async def _generate_alert_summary(self, baselines_evaluation: Dict, system_health: Dict) -> List[Dict[str, Any]]:
        """Generate alert summary from evaluations"""
        alerts = []
        
        try:
            # Check overall performance status
            overall_status = baselines_evaluation.get('overall_status', 'unknown')
            if overall_status in ['critical', 'emergency']:
                alerts.append({
                    'type': 'performance_alert',
                    'severity': 'critical' if overall_status == 'critical' else 'emergency',
                    'title': f'Overall Performance: {overall_status.upper()}',
                    'description': f'System performance is at {overall_status} level',
                    'timestamp': datetime.utcnow().isoformat()
                })
            
            # Check system health
            health_status = system_health.get('overall_status', 'unknown')
            if health_status == 'critical':
                alerts.append({
                    'type': 'system_health_alert',
                    'severity': 'critical',
                    'title': 'Critical System Health Issues',
                    'description': f'System health issues detected: {system_health.get("total_issues", 0)} issues',
                    'timestamp': datetime.utcnow().isoformat()
                })
            
            # Check specific metric evaluations
            evaluations = baselines_evaluation.get('evaluations', {})
            for metric_name, evaluation in evaluations.items():
                if evaluation.get('performance_level') in ['critical', 'emergency']:
                    alerts.append({
                        'type': 'metric_alert',
                        'severity': evaluation.get('performance_level'),
                        'title': f'Metric Alert: {metric_name}',
                        'description': evaluation.get('message', f'{metric_name} performance issue'),
                        'metric_name': metric_name,
                        'metric_value': evaluation.get('value'),
                        'timestamp': datetime.utcnow().isoformat()
                    })
            
        except Exception as e:
            logger.error(f"Error generating alert summary: {e}")
            alerts.append({
                'type': 'system_error',
                'severity': 'warning',
                'title': 'Alert Generation Error',
                'description': str(e),
                'timestamp': datetime.utcnow().isoformat()
            })
        
        return alerts
    
    async def _generate_recommendations(self, baselines_evaluation: Dict, trends: Dict, alerts: List) -> List[str]:
        """Generate performance recommendations"""
        recommendations = []
        
        try:
            # Check overall performance
            overall_status = baselines_evaluation.get('overall_status', 'unknown')
            if overall_status in ['warning', 'critical', 'emergency']:
                recommendations.append(f"🔍 Investigate {overall_status} performance issues and optimize bottlenecks")
            
            # Check memory issues
            evaluations = baselines_evaluation.get('evaluations', {})
            memory_issues = [name for name, eval in evaluations.items() 
                           if 'memory' in name.lower() and eval.get('performance_level') in ['warning', 'critical', 'emergency']]
            
            if memory_issues:
                recommendations.append("🧠 Optimize memory usage - consider garbage collection or resource cleanup")
            
            # Check CPU issues
            cpu_issues = [name for name, eval in evaluations.items() 
                         if 'cpu' in name.lower() and eval.get('performance_level') in ['warning', 'critical', 'emergency']]
            
            if cpu_issues:
                recommendations.append("⚡ Optimize CPU usage - review intensive operations and add async processing")
            
            # Check startup performance
            startup_issues = [name for name, eval in evaluations.items() 
                            if 'startup' in name.lower() and eval.get('performance_level') in ['warning', 'critical']]
            
            if startup_issues:
                recommendations.append("🚀 Optimize startup time - implement lazy loading and reduce initialization overhead")
            
            # Check trends
            performance_trend = trends.get('performance_trend', 'stable')
            if performance_trend == 'degrading':
                recommendations.append("📉 Performance is degrading - monitor trends and implement preventive measures")
            
            alert_trend = trends.get('alert_trend', 'stable')
            if alert_trend == 'increasing':
                recommendations.append("🚨 Alert frequency is increasing - investigate recurring issues")
            
            # Check integration status
            integration_status = get_integration_status()
            if integration_status.get('success_rate', 100) < 90:
                recommendations.append("🔧 Some monitoring integrations failed - review integration logs and fix issues")
            
            # Default recommendation if system is healthy
            if not recommendations and overall_status in ['excellent', 'good']:
                recommendations.append("✅ System performance is healthy - continue monitoring and maintain current practices")
        
        except Exception as e:
            logger.error(f"Error generating recommendations: {e}")
            recommendations.append(f"❌ Error generating recommendations: {str(e)}")
        
        return recommendations
    
    async def _check_and_generate_alerts(self, report: PerformanceReport):
        """Check report for alert conditions and generate alerts"""
        try:
            critical_alerts = [a for a in report.alerts if a.get('severity') == 'critical']
            emergency_alerts = [a for a in report.alerts if a.get('severity') == 'emergency']
            
            # Check alert thresholds
            if len(emergency_alerts) >= self.alert_thresholds['emergency_metrics_count']:
                await self._send_emergency_alert(report, emergency_alerts)
            elif len(critical_alerts) >= self.alert_thresholds['critical_metrics_count']:
                await self._send_critical_alert(report, critical_alerts)
            
            # Store alerts in history
            self.alert_history.extend(report.alerts)
            
            # Keep only recent alerts (last 100)
            if len(self.alert_history) > 100:
                self.alert_history = self.alert_history[-100:]
        
        except Exception as e:
            logger.error(f"Error checking and generating alerts: {e}")
    
    async def _send_emergency_alert(self, report: PerformanceReport, emergency_alerts: List):
        """Send emergency performance alert"""
        logger.critical(f"🚨 EMERGENCY PERFORMANCE ALERT: {len(emergency_alerts)} emergency issues detected")
        
        alert_details = "\n".join([f"- {alert['title']}: {alert['description']}" for alert in emergency_alerts])
        logger.critical(f"Emergency issues:\n{alert_details}")
    
    async def _send_critical_alert(self, report: PerformanceReport, critical_alerts: List):
        """Send critical performance alert"""
        logger.error(f"🔴 CRITICAL PERFORMANCE ALERT: {len(critical_alerts)} critical issues detected")
        
        alert_details = "\n".join([f"- {alert['title']}: {alert['description']}" for alert in critical_alerts])
        logger.error(f"Critical issues:\n{alert_details}")
    
    async def _log_report(self, report: PerformanceReport, format_type: ReportFormat):
        """Log performance report in specified format"""
        try:
            if format_type == ReportFormat.STRUCTURED_LOG:
                await self._log_structured_report(report)
            elif format_type == ReportFormat.SUMMARY:
                await self._log_summary_report(report)
            elif format_type == ReportFormat.JSON:
                await self._log_json_report(report)
        
        except Exception as e:
            logger.error(f"Error logging report: {e}")
    
    async def _log_structured_report(self, report: PerformanceReport):
        """Log structured performance report"""
        summary = report.summary
        
        # Main performance summary
        logger.info(
            f"📊 PERFORMANCE REPORT [{report.report_type}]: "
            f"Status={summary.get('overall_performance_level', 'unknown').upper()} | "
            f"Health={report.system_health.get('overall_status', 'unknown').upper()} | "
            f"Metrics={summary.get('total_metrics_collected', 0)} | "
            f"Alerts={summary.get('alerts_count', 0)} | "
            f"Integration={summary.get('integration_success_rate', 0):.1f}%"
        )
        
        # Alert summary
        if report.alerts:
            alert_counts = defaultdict(int)
            for alert in report.alerts:
                alert_counts[alert.get('severity', 'unknown')] += 1
            
            alert_summary = ", ".join([f"{severity}={count}" for severity, count in alert_counts.items()])
            logger.warning(f"⚠️ Alerts: {alert_summary}")
        
        # Recommendations
        if report.recommendations:
            logger.info(f"💡 Recommendations ({len(report.recommendations)}):")
            for i, recommendation in enumerate(report.recommendations[:3], 1):  # Show top 3
                logger.info(f"   {i}. {recommendation}")
    
    async def _log_summary_report(self, report: PerformanceReport):
        """Log summary performance report"""
        logger.info(f"📋 Performance Summary: {report.summary}")
    
    async def _log_json_report(self, report: PerformanceReport):
        """Log JSON performance report"""
        logger.info(f"📄 Performance Report JSON: {json.dumps(report.to_dict(), indent=2)}")
    
    def get_latest_report(self, report_type: str = None) -> Optional[PerformanceReport]:
        """Get the latest report of a specific type"""
        if report_type:
            reports = self.report_history.get(report_type, [])
            return reports[-1] if reports else None
        else:
            # Get the most recent report across all types
            all_reports = []
            for reports in self.report_history.values():
                all_reports.extend(reports)
            
            if all_reports:
                return max(all_reports, key=lambda r: r.timestamp)
            return None
    
    def get_reporting_status(self) -> Dict[str, Any]:
        """Get status of the reporting system"""
        return {
            'is_active': self.is_active,
            'enabled_frequencies': [f.value for f in self.enabled_frequencies],
            'active_tasks': len(self.reporting_tasks),
            'total_reports_generated': sum(len(reports) for reports in self.report_history.values()),
            'last_report_timestamps': {freq: ts.isoformat() for freq, ts in self.last_report_timestamp.items()},
            'alert_history_count': len(self.alert_history)
        }


# Global reporter instance
unified_reporter = UnifiedPerformanceReporter()


# Convenience functions
async def start_unified_reporting():
    """Start unified performance reporting"""
    await unified_reporter.start_reporting()


async def stop_unified_reporting():
    """Stop unified performance reporting"""
    await unified_reporter.stop_reporting()


async def generate_performance_report(report_type: str = "on_demand") -> PerformanceReport:
    """Generate an on-demand performance report"""
    return await unified_reporter.generate_comprehensive_report(report_type)


def get_latest_performance_report() -> Optional[PerformanceReport]:
    """Get the latest performance report"""
    return unified_reporter.get_latest_report()


def get_unified_reporting_status() -> Dict[str, Any]:
    """Get unified reporting system status"""
    return unified_reporter.get_reporting_status()