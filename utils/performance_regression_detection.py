"""
Performance Regression Detection System
Automatically detects performance degradation and baseline drift with ML-based analysis
"""

import logging
import asyncio
import time
import statistics
from typing import Dict, List, Optional, Any, NamedTuple
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict, field
from enum import Enum
from collections import defaultdict, deque

from utils.standardized_metrics_framework import standardized_metrics
from utils.performance_baselines_config import (
    performance_baselines, PerformanceLevel
)
from utils.enhanced_alert_correlation import alert_correlation, AlertSeverity

logger = logging.getLogger(__name__)


class RegressionSeverity(Enum):
    """Performance regression severity levels"""
    MINOR = "minor"
    MODERATE = "moderate"
    MAJOR = "major"
    CRITICAL = "critical"


class RegressionType(Enum):
    """Types of performance regressions"""
    GRADUAL_DEGRADATION = "gradual_degradation"
    SUDDEN_SPIKE = "sudden_spike"
    BASELINE_DRIFT = "baseline_drift"
    PATTERN_ANOMALY = "pattern_anomaly"
    THRESHOLD_CREEP = "threshold_creep"


@dataclass
class MetricHistory:
    """Historical data for a metric"""
    metric_name: str
    values: deque = field(default_factory=lambda: deque(maxlen=1000))
    timestamps: deque = field(default_factory=lambda: deque(maxlen=1000))
    baselines: deque = field(default_factory=lambda: deque(maxlen=100))  # Baseline history
    last_baseline_update: datetime = field(default_factory=datetime.utcnow)


@dataclass
class RegressionDetection:
    """Performance regression detection result"""
    detection_id: str
    metric_name: str
    regression_type: RegressionType
    severity: RegressionSeverity
    confidence: float  # 0.0 - 1.0
    detected_at: datetime
    start_time: datetime  # When regression likely started
    current_value: float
    baseline_value: float
    degradation_percentage: float
    trend_direction: str
    statistical_significance: float
    description: str
    impact_assessment: str
    recommended_actions: List[str] = field(default_factory=list)
    affected_components: List[str] = field(default_factory=list)


class StatisticalAnalysis(NamedTuple):
    """Statistical analysis result"""
    mean: float
    std_dev: float
    median: float
    percentile_95: float
    percentile_99: float
    trend_slope: float
    variance: float
    is_statistically_significant: bool


class PerformanceRegressionDetector:
    """
    Advanced performance regression detection system using statistical analysis,
    trend detection, and baseline comparison
    """
    
    def __init__(self):
        self.is_active = False
        self.detection_task: Optional[asyncio.Task] = None
        
        # Metric history tracking
        self.metric_histories: Dict[str, MetricHistory] = {}
        
        # Regression tracking
        self.active_regressions: Dict[str, RegressionDetection] = {}
        self.regression_history = deque(maxlen=1000)
        
        # Detection configuration
        self.detection_config = {
            'min_data_points': 10,  # Minimum data points for detection
            'statistical_confidence': 0.95,  # Statistical confidence level
            'trend_window_minutes': 60,  # Window for trend analysis
            'baseline_update_hours': 24,  # How often to update baselines
            'degradation_threshold': 0.15,  # 15% degradation threshold
            'sudden_spike_threshold': 2.5,  # Standard deviations for sudden spike
            'baseline_drift_threshold': 0.30,  # 30% drift from original baseline
        }
        
        # Analysis windows for different types of detection
        self.analysis_windows = {
            'sudden_spike': timedelta(minutes=5),
            'gradual_degradation': timedelta(hours=2),
            'baseline_drift': timedelta(days=7),
            'pattern_anomaly': timedelta(hours=6),
        }
        
        # Statistics tracking
        self.detection_stats = {
            'total_metrics_analyzed': 0,
            'regressions_detected': 0,
            'false_positives': 0,
            'baselines_updated': 0,
            'trend_analyses_performed': 0
        }
        
        logger.info("🔧 Performance Regression Detection System initialized")
    
    async def start_detection(self):
        """Start the performance regression detection system"""
        if self.is_active:
            logger.warning("Regression detection already active")
            return
        
        self.is_active = True
        self.detection_task = asyncio.create_task(self._detection_loop())
        
        logger.info("🚀 Started performance regression detection system")
    
    async def stop_detection(self):
        """Stop the performance regression detection system"""
        self.is_active = False
        
        if self.detection_task:
            self.detection_task.cancel()
            try:
                await self.detection_task
            except asyncio.CancelledError:
                pass
        
        logger.info("🛑 Stopped performance regression detection system")
    
    async def _detection_loop(self):
        """Main regression detection processing loop"""
        logger.info("🔍 Starting performance regression detection loop")
        
        while self.is_active:
            try:
                start_time = time.time()
                
                # Collect current metrics and update histories
                await self._update_metric_histories()
                
                # Perform regression detection analysis
                await self._analyze_for_regressions()
                
                # Update baselines if needed
                await self._update_baselines()
                
                # Clean up old data
                await self._cleanup_old_data()
                
                processing_time = time.time() - start_time
                
                if processing_time > 3.0:
                    logger.warning(f"⏳ Regression detection took {processing_time:.2f}s")
                
            except Exception as e:
                logger.error(f"Error in regression detection loop: {e}")
            
            await asyncio.sleep(60)  # Analyze every minute
    
    async def _update_metric_histories(self):
        """Update metric histories with current values"""
        try:
            current_time = datetime.utcnow()
            current_metrics = standardized_metrics.get_current_metrics()
            
            if not current_metrics:
                return
            
            for metric in current_metrics:
                metric_name = metric.name
                
                # Initialize history if not exists
                if metric_name not in self.metric_histories:
                    self.metric_histories[metric_name] = MetricHistory(metric_name=metric_name)
                
                history = self.metric_histories[metric_name]
                
                # Add current value to history
                history.values.append(metric.value)
                history.timestamps.append(current_time)
                
                # Update baseline if it's time
                if self._should_update_baseline(history, current_time):
                    await self._update_metric_baseline(history)
            
            self.detection_stats['total_metrics_analyzed'] = len(self.metric_histories)
            
        except Exception as e:
            logger.error(f"Error updating metric histories: {e}")
    
    def _should_update_baseline(self, history: MetricHistory, current_time: datetime) -> bool:
        """Check if baseline should be updated"""
        time_since_last_update = current_time - history.last_baseline_update
        update_interval = timedelta(hours=self.detection_config['baseline_update_hours'])
        
        return (time_since_last_update > update_interval and 
                len(history.values) >= self.detection_config['min_data_points'])
    
    async def _update_metric_baseline(self, history: MetricHistory):
        """Update baseline for a metric based on recent stable performance"""
        try:
            if len(history.values) < self.detection_config['min_data_points']:
                return
            
            # Get recent stable values (exclude outliers)
            recent_values = list(history.values)[-200:]  # Last 200 data points
            
            # Remove outliers using IQR method
            stable_values = self._remove_outliers(recent_values)
            
            if len(stable_values) < 5:
                return  # Not enough stable data
            
            # Calculate new baseline
            new_baseline = statistics.median(stable_values)
            
            # Add to baseline history
            history.baselines.append(new_baseline)
            history.last_baseline_update = datetime.utcnow()
            
            self.detection_stats['baselines_updated'] += 1
            
            logger.info(f"📊 Updated baseline for {history.metric_name}: {new_baseline:.2f}")
            
        except Exception as e:
            logger.error(f"Error updating baseline for {history.metric_name}: {e}")
    
    def _remove_outliers(self, values: List[float]) -> List[float]:
        """Remove outliers using IQR method"""
        try:
            if len(values) < 4:
                return values
            
            sorted_values = sorted(values)
            q1_idx = len(sorted_values) // 4
            q3_idx = 3 * len(sorted_values) // 4
            
            q1 = sorted_values[q1_idx]
            q3 = sorted_values[q3_idx]
            iqr = q3 - q1
            
            if iqr == 0:
                return values  # All values are the same
            
            lower_bound = q1 - 1.5 * iqr
            upper_bound = q3 + 1.5 * iqr
            
            return [v for v in values if lower_bound <= v <= upper_bound]
            
        except Exception as e:
            logger.debug(f"Error removing outliers: {e}")
            return values
    
    async def _analyze_for_regressions(self):
        """Analyze all metrics for performance regressions"""
        try:
            current_time = datetime.utcnow()
            
            for metric_name, history in self.metric_histories.items():
                if len(history.values) < self.detection_config['min_data_points']:
                    continue
                
                # Skip if already has active regression
                if metric_name in self.active_regressions:
                    await self._check_regression_resolution(metric_name, history)
                    continue
                
                # Perform different types of regression analysis
                await self._detect_sudden_spike(metric_name, history, current_time)
                await self._detect_gradual_degradation(metric_name, history, current_time)
                await self._detect_baseline_drift(metric_name, history, current_time)
                await self._detect_pattern_anomaly(metric_name, history, current_time)
            
            self.detection_stats['trend_analyses_performed'] += 1
            
        except Exception as e:
            logger.error(f"Error analyzing for regressions: {e}")
    
    async def _detect_sudden_spike(self, metric_name: str, history: MetricHistory, current_time: datetime):
        """Detect sudden performance spikes"""
        try:
            window = self.analysis_windows['sudden_spike']
            threshold = self.detection_config['sudden_spike_threshold']
            
            # Get recent values within window
            cutoff_time = current_time - window
            recent_data = [
                (value, timestamp) for value, timestamp in 
                zip(history.values, history.timestamps)
                if timestamp >= cutoff_time
            ]
            
            if len(recent_data) < 3:
                return
            
            recent_values = [value for value, _ in recent_data]
            current_value = recent_values[-1]
            
            # Calculate statistical baseline from older data
            older_values = [
                value for value, timestamp in 
                zip(history.values, history.timestamps)
                if timestamp < cutoff_time
            ]
            
            if len(older_values) < 10:
                return
            
            baseline_stats = self._calculate_statistical_analysis(older_values)
            
            # Check for sudden spike
            z_score = abs(current_value - baseline_stats.mean) / baseline_stats.std_dev if baseline_stats.std_dev > 0 else 0
            
            if z_score > threshold:
                # Calculate degradation percentage
                degradation_pct = ((current_value - baseline_stats.mean) / baseline_stats.mean) * 100 if baseline_stats.mean > 0 else 0
                
                # Determine severity
                severity = self._determine_regression_severity(degradation_pct, z_score)
                
                # Create regression detection
                regression = RegressionDetection(
                    detection_id=f"spike_{metric_name}_{int(current_time.timestamp())}",
                    metric_name=metric_name,
                    regression_type=RegressionType.SUDDEN_SPIKE,
                    severity=severity,
                    confidence=min(0.95, z_score / 5.0),  # Higher z-score = higher confidence
                    detected_at=current_time,
                    start_time=recent_data[0][1],  # Start of the window
                    current_value=current_value,
                    baseline_value=baseline_stats.mean,
                    degradation_percentage=degradation_pct,
                    trend_direction='degrading',
                    statistical_significance=z_score,
                    description=f"Sudden spike detected: {z_score:.1f} standard deviations above baseline",
                    impact_assessment=self._assess_impact(metric_name, degradation_pct),
                    recommended_actions=self._get_regression_recommendations(metric_name, RegressionType.SUDDEN_SPIKE),
                    affected_components=self._identify_affected_components(metric_name)
                )
                
                await self._register_regression(regression)
        
        except Exception as e:
            logger.debug(f"Error detecting sudden spike for {metric_name}: {e}")
    
    async def _detect_gradual_degradation(self, metric_name: str, history: MetricHistory, current_time: datetime):
        """Detect gradual performance degradation"""
        try:
            window = self.analysis_windows['gradual_degradation']
            threshold = self.detection_config['degradation_threshold']
            
            # Get values within window
            cutoff_time = current_time - window
            window_data = [
                (value, timestamp) for value, timestamp in 
                zip(history.values, history.timestamps)
                if timestamp >= cutoff_time
            ]
            
            if len(window_data) < 20:  # Need more data for trend analysis
                return
            
            window_values = [value for value, _ in window_data]
            
            # Calculate trend
            trend_analysis = self._calculate_trend_analysis(window_values)
            
            if trend_analysis['direction'] != 'degrading':
                return
            
            # Get baseline for comparison
            baseline = self._get_current_baseline(history)
            if baseline is None:
                return
            
            current_value = window_values[-1]
            degradation_pct = ((current_value - baseline) / baseline) * 100 if baseline > 0 else 0
            
            # Check if degradation exceeds threshold
            if abs(degradation_pct) > threshold * 100:  # Convert to percentage
                
                # Determine severity
                severity = self._determine_regression_severity(abs(degradation_pct), trend_analysis['confidence'])
                
                # Create regression detection
                regression = RegressionDetection(
                    detection_id=f"gradual_{metric_name}_{int(current_time.timestamp())}",
                    metric_name=metric_name,
                    regression_type=RegressionType.GRADUAL_DEGRADATION,
                    severity=severity,
                    confidence=trend_analysis['confidence'],
                    detected_at=current_time,
                    start_time=cutoff_time,
                    current_value=current_value,
                    baseline_value=baseline,
                    degradation_percentage=degradation_pct,
                    trend_direction='degrading',
                    statistical_significance=trend_analysis['slope'],
                    description=f"Gradual degradation detected: {abs(degradation_pct):.1f}% worse than baseline over {window.total_seconds()/3600:.1f}h",
                    impact_assessment=self._assess_impact(metric_name, abs(degradation_pct)),
                    recommended_actions=self._get_regression_recommendations(metric_name, RegressionType.GRADUAL_DEGRADATION),
                    affected_components=self._identify_affected_components(metric_name)
                )
                
                await self._register_regression(regression)
        
        except Exception as e:
            logger.debug(f"Error detecting gradual degradation for {metric_name}: {e}")
    
    async def _detect_baseline_drift(self, metric_name: str, history: MetricHistory, current_time: datetime):
        """Detect baseline drift over time"""
        try:
            if len(history.baselines) < 3:
                return  # Need multiple baselines to detect drift
            
            window = self.analysis_windows['baseline_drift']
            threshold = self.detection_config['baseline_drift_threshold']
            
            # Compare first and latest baselines
            original_baseline = history.baselines[0]
            current_baseline = history.baselines[-1]
            
            drift_percentage = ((current_baseline - original_baseline) / original_baseline) * 100 if original_baseline > 0 else 0
            
            if abs(drift_percentage) > threshold * 100:  # Convert to percentage
                
                # Calculate trend in baselines
                baseline_values = list(history.baselines)
                trend_analysis = self._calculate_trend_analysis(baseline_values)
                
                # Determine severity
                severity = self._determine_regression_severity(abs(drift_percentage), trend_analysis['confidence'])
                
                # Create regression detection
                regression = RegressionDetection(
                    detection_id=f"drift_{metric_name}_{int(current_time.timestamp())}",
                    metric_name=metric_name,
                    regression_type=RegressionType.BASELINE_DRIFT,
                    severity=severity,
                    confidence=trend_analysis['confidence'],
                    detected_at=current_time,
                    start_time=current_time - window,
                    current_value=current_baseline,
                    baseline_value=original_baseline,
                    degradation_percentage=drift_percentage,
                    trend_direction=trend_analysis['direction'],
                    statistical_significance=trend_analysis['slope'],
                    description=f"Baseline drift detected: {abs(drift_percentage):.1f}% drift from original baseline",
                    impact_assessment=self._assess_impact(metric_name, abs(drift_percentage)),
                    recommended_actions=self._get_regression_recommendations(metric_name, RegressionType.BASELINE_DRIFT),
                    affected_components=self._identify_affected_components(metric_name)
                )
                
                await self._register_regression(regression)
        
        except Exception as e:
            logger.debug(f"Error detecting baseline drift for {metric_name}: {e}")
    
    async def _detect_pattern_anomaly(self, metric_name: str, history: MetricHistory, current_time: datetime):
        """Detect pattern anomalies in performance"""
        try:
            window = self.analysis_windows['pattern_anomaly']
            
            # Get recent pattern
            cutoff_time = current_time - window
            recent_data = [
                value for value, timestamp in 
                zip(history.values, history.timestamps)
                if timestamp >= cutoff_time
            ]
            
            if len(recent_data) < 30:  # Need enough data for pattern analysis
                return
            
            # Get historical pattern for comparison
            historical_data = [
                value for value, timestamp in 
                zip(history.values, history.timestamps)
                if timestamp < cutoff_time
            ]
            
            if len(historical_data) < 100:
                return
            
            # Calculate pattern statistics
            recent_stats = self._calculate_statistical_analysis(recent_data)
            historical_stats = self._calculate_statistical_analysis(historical_data)
            
            # Check for significant pattern change
            variance_change = abs(recent_stats.variance - historical_stats.variance) / historical_stats.variance if historical_stats.variance > 0 else 0
            mean_change = abs(recent_stats.mean - historical_stats.mean) / historical_stats.mean if historical_stats.mean > 0 else 0
            
            # Detect anomaly if significant change in pattern
            if variance_change > 0.5 or mean_change > 0.3:  # 50% variance change or 30% mean change
                
                degradation_pct = mean_change * 100
                confidence = min(0.9, max(variance_change, mean_change))
                
                # Determine severity
                severity = self._determine_regression_severity(degradation_pct, confidence)
                
                # Create regression detection
                regression = RegressionDetection(
                    detection_id=f"pattern_{metric_name}_{int(current_time.timestamp())}",
                    metric_name=metric_name,
                    regression_type=RegressionType.PATTERN_ANOMALY,
                    severity=severity,
                    confidence=confidence,
                    detected_at=current_time,
                    start_time=cutoff_time,
                    current_value=recent_stats.mean,
                    baseline_value=historical_stats.mean,
                    degradation_percentage=degradation_pct,
                    trend_direction='anomaly',
                    statistical_significance=variance_change,
                    description=f"Pattern anomaly detected: {variance_change:.1%} variance change, {mean_change:.1%} mean change",
                    impact_assessment=self._assess_impact(metric_name, degradation_pct),
                    recommended_actions=self._get_regression_recommendations(metric_name, RegressionType.PATTERN_ANOMALY),
                    affected_components=self._identify_affected_components(metric_name)
                )
                
                await self._register_regression(regression)
        
        except Exception as e:
            logger.debug(f"Error detecting pattern anomaly for {metric_name}: {e}")
    
    def _calculate_statistical_analysis(self, values: List[float]) -> StatisticalAnalysis:
        """Calculate comprehensive statistical analysis"""
        try:
            if not values:
                return StatisticalAnalysis(0, 0, 0, 0, 0, 0, 0, False)
            
            mean_val = statistics.mean(values)
            std_dev = statistics.stdev(values) if len(values) > 1 else 0
            median_val = statistics.median(values)
            
            sorted_values = sorted(values)
            p95_idx = int(0.95 * len(sorted_values))
            p99_idx = int(0.99 * len(sorted_values))
            
            percentile_95 = sorted_values[min(p95_idx, len(sorted_values) - 1)]
            percentile_99 = sorted_values[min(p99_idx, len(sorted_values) - 1)]
            
            # Calculate trend slope (simple linear regression)
            n = len(values)
            x_values = list(range(n))
            
            sum_x = sum(x_values)
            sum_y = sum(values)
            sum_xy = sum(x * y for x, y in zip(x_values, values))
            sum_x_squared = sum(x * x for x in x_values)
            
            if n * sum_x_squared - sum_x * sum_x != 0:
                slope = (n * sum_xy - sum_x * sum_y) / (n * sum_x_squared - sum_x * sum_x)
            else:
                slope = 0
            
            variance = std_dev ** 2
            
            # Statistical significance (basic check)
            is_significant = std_dev > 0 and len(values) >= 10
            
            return StatisticalAnalysis(
                mean=mean_val,
                std_dev=std_dev,
                median=median_val,
                percentile_95=percentile_95,
                percentile_99=percentile_99,
                trend_slope=slope,
                variance=variance,
                is_statistically_significant=is_significant
            )
        
        except Exception as e:
            logger.debug(f"Error calculating statistical analysis: {e}")
            return StatisticalAnalysis(0, 0, 0, 0, 0, 0, 0, False)
    
    def _calculate_trend_analysis(self, values: List[float]) -> Dict[str, Any]:
        """Calculate trend analysis for values"""
        try:
            if len(values) < 3:
                return {'direction': 'stable', 'slope': 0, 'confidence': 0}
            
            stats = self._calculate_statistical_analysis(values)
            
            # Determine trend direction
            if abs(stats.trend_slope) < 0.001:
                direction = 'stable'
            elif stats.trend_slope > 0:
                direction = 'degrading' if self._is_higher_worse(values) else 'improving'
            else:
                direction = 'improving' if self._is_higher_worse(values) else 'degrading'
            
            # Calculate confidence based on R-squared approximation
            mean_val = stats.mean
            residual_sum = sum((value - mean_val) ** 2 for value in values)
            total_variance = residual_sum / len(values) if len(values) > 0 else 0
            
            confidence = min(0.95, abs(stats.trend_slope) / (stats.std_dev + 0.001))
            
            return {
                'direction': direction,
                'slope': stats.trend_slope,
                'confidence': confidence,
                'r_squared': 1 - (total_variance / (stats.variance + 0.001)) if stats.variance > 0 else 0
            }
        
        except Exception as e:
            logger.debug(f"Error calculating trend analysis: {e}")
            return {'direction': 'stable', 'slope': 0, 'confidence': 0}
    
    def _is_higher_worse(self, values: List[float]) -> bool:
        """Determine if higher values indicate worse performance (heuristic)"""
        # For most performance metrics, higher is worse (latency, memory, CPU, etc.)
        # This is a simple heuristic - could be made more sophisticated
        return True
    
    def _get_current_baseline(self, history: MetricHistory) -> Optional[float]:
        """Get current baseline for a metric"""
        if history.baselines:
            return history.baselines[-1]
        
        # Fallback to baseline from configuration
        baseline_config = performance_baselines.get_baseline_for_metric(history.metric_name)
        if baseline_config:
            return baseline_config.excellent_max
        
        return None
    
    def _determine_regression_severity(self, degradation_percentage: float, confidence: float) -> RegressionSeverity:
        """Determine regression severity based on degradation and confidence"""
        try:
            # Adjust degradation by confidence
            weighted_degradation = degradation_percentage * confidence
            
            if weighted_degradation >= 50:  # 50%+ degradation
                return RegressionSeverity.CRITICAL
            elif weighted_degradation >= 30:  # 30-50% degradation
                return RegressionSeverity.MAJOR
            elif weighted_degradation >= 15:  # 15-30% degradation
                return RegressionSeverity.MODERATE
            else:  # < 15% degradation
                return RegressionSeverity.MINOR
        
        except Exception as e:
            logger.debug(f"Error determining regression severity: {e}")
            return RegressionSeverity.MINOR
    
    def _assess_impact(self, metric_name: str, degradation_percentage: float) -> str:
        """Assess the impact of a performance regression"""
        try:
            metric_lower = metric_name.lower()
            
            # High-impact metrics
            if any(keyword in metric_lower for keyword in ['memory', 'cpu', 'database', 'response_time']):
                if degradation_percentage >= 30:
                    return "High impact: Critical system performance affected"
                elif degradation_percentage >= 15:
                    return "Medium impact: System performance degraded"
                else:
                    return "Low impact: Minor performance degradation"
            
            # Medium-impact metrics
            elif any(keyword in metric_lower for keyword in ['webhook', 'operation', 'startup']):
                if degradation_percentage >= 50:
                    return "High impact: Application functionality affected"
                elif degradation_percentage >= 25:
                    return "Medium impact: Application performance degraded"
                else:
                    return "Low impact: Minor application impact"
            
            # Low-impact metrics
            else:
                if degradation_percentage >= 100:
                    return "Medium impact: Significant metric degradation"
                else:
                    return "Low impact: Metric performance degraded"
        
        except Exception as e:
            logger.debug(f"Error assessing impact: {e}")
            return "Unknown impact: Performance regression detected"
    
    def _get_regression_recommendations(self, metric_name: str, regression_type: RegressionType) -> List[str]:
        """Get recommendations for addressing a regression"""
        try:
            recommendations = []
            metric_lower = metric_name.lower()
            
            # Metric-specific recommendations
            if 'memory' in metric_lower:
                recommendations.extend([
                    "🧠 Investigate memory leaks and optimize memory usage",
                    "📊 Analyze garbage collection patterns and frequency",
                    "🔄 Consider restarting services to reclaim memory"
                ])
            elif 'cpu' in metric_lower:
                recommendations.extend([
                    "⚡ Profile CPU usage and identify bottlenecks",
                    "🔧 Optimize algorithm efficiency and reduce CPU load",
                    "⚖️ Consider load balancing or resource scaling"
                ])
            elif 'database' in metric_lower:
                recommendations.extend([
                    "🗄️ Analyze slow queries and optimize database performance",
                    "📊 Review database connection pool configuration",
                    "🔧 Consider database maintenance and index optimization"
                ])
            elif 'webhook' in metric_lower:
                recommendations.extend([
                    "🌐 Check external service health and connectivity",
                    "🔄 Review webhook retry and timeout configuration",
                    "📊 Analyze webhook failure patterns and rates"
                ])
            
            # Regression type-specific recommendations
            if regression_type == RegressionType.SUDDEN_SPIKE:
                recommendations.append("🚨 Investigate recent changes or deployments that may have caused the spike")
            elif regression_type == RegressionType.GRADUAL_DEGRADATION:
                recommendations.append("📈 Analyze trends over time to identify root cause of gradual degradation")
            elif regression_type == RegressionType.BASELINE_DRIFT:
                recommendations.append("🎯 Review and potentially update performance baselines if justified")
            elif regression_type == RegressionType.PATTERN_ANOMALY:
                recommendations.append("🔍 Investigate unusual patterns in system behavior and usage")
            
            # General recommendations
            recommendations.extend([
                "📊 Monitor related metrics for correlation and impact",
                "🔍 Review system logs for additional context and errors"
            ])
            
            return recommendations[:4]  # Limit to top 4 recommendations
        
        except Exception as e:
            logger.debug(f"Error getting recommendations: {e}")
            return ["📊 Investigate performance regression and monitor related metrics"]
    
    def _identify_affected_components(self, metric_name: str) -> List[str]:
        """Identify components affected by the regression"""
        try:
            affected = []
            metric_lower = metric_name.lower()
            
            # Map metrics to components
            if 'memory' in metric_lower or 'cpu' in metric_lower:
                affected.extend(['System Resources', 'Application Performance'])
            
            if 'database' in metric_lower:
                affected.extend(['Database Layer', 'Data Operations'])
            
            if 'webhook' in metric_lower:
                affected.extend(['External Integrations', 'API Performance'])
            
            if 'app' in metric_lower:
                affected.extend(['Application Layer', 'User Experience'])
            
            if 'process' in metric_lower:
                affected.extend(['Process Management', 'System Stability'])
            
            if 'startup' in metric_lower:
                affected.extend(['System Startup', 'Service Availability'])
            
            # Default if no specific mapping
            if not affected:
                affected.append('System Performance')
            
            return affected
        
        except Exception as e:
            logger.debug(f"Error identifying affected components: {e}")
            return ['System Performance']
    
    async def _register_regression(self, regression: RegressionDetection):
        """Register a new performance regression"""
        try:
            self.active_regressions[regression.metric_name] = regression
            self.regression_history.append(asdict(regression))
            self.detection_stats['regressions_detected'] += 1
            
            # Log the regression
            severity_icons = {
                RegressionSeverity.MINOR: "🟡",
                RegressionSeverity.MODERATE: "🟠",
                RegressionSeverity.MAJOR: "🔴",
                RegressionSeverity.CRITICAL: "🚨"
            }
            
            icon = severity_icons.get(regression.severity, "⚠️")
            
            logger.warning(
                f"{icon} PERFORMANCE REGRESSION: {regression.metric_name} - "
                f"{regression.regression_type.value} ({regression.severity.value}) - "
                f"{regression.degradation_percentage:+.1f}% degradation"
            )
            
            # Send to alert correlation system
            try:
                # Map regression severity to alert severity
                alert_severity_map = {
                    RegressionSeverity.MINOR: AlertSeverity.WARNING,
                    RegressionSeverity.MODERATE: AlertSeverity.WARNING,
                    RegressionSeverity.MAJOR: AlertSeverity.CRITICAL,
                    RegressionSeverity.CRITICAL: AlertSeverity.EMERGENCY
                }
                
                alert_severity_map.get(regression.severity, AlertSeverity.WARNING)
                
                # Create a performance evaluation for the alert system
                performance_evaluation = {
                    'baseline_available': True,
                    'performance_level': self._map_severity_to_performance_level(regression.severity).value,
                    'message': regression.description,
                    'thresholds': {
                        'excellent_max': regression.baseline_value,
                        f'{self._map_severity_to_performance_level(regression.severity).value}_max': regression.current_value
                    }
                }
                
                # Send to alert correlation
                await alert_correlation.process_alert(
                    regression.metric_name,
                    regression.current_value,
                    performance_evaluation
                )
                
            except Exception as e:
                logger.debug(f"Error sending regression to alert system: {e}")
            
        except Exception as e:
            logger.error(f"Error registering regression: {e}")
    
    def _map_severity_to_performance_level(self, severity: RegressionSeverity) -> PerformanceLevel:
        """Map regression severity to performance level"""
        mapping = {
            RegressionSeverity.MINOR: PerformanceLevel.WARNING,
            RegressionSeverity.MODERATE: PerformanceLevel.WARNING,
            RegressionSeverity.MAJOR: PerformanceLevel.CRITICAL,
            RegressionSeverity.CRITICAL: PerformanceLevel.EMERGENCY
        }
        return mapping.get(severity, PerformanceLevel.WARNING)
    
    async def _check_regression_resolution(self, metric_name: str, history: MetricHistory):
        """Check if an active regression has been resolved"""
        try:
            regression = self.active_regressions[metric_name]
            
            if len(history.values) < 5:
                return
            
            # Get recent values
            recent_values = list(history.values)[-10:]
            current_value = recent_values[-1]
            baseline = self._get_current_baseline(history)
            
            if baseline is None:
                return
            
            # Check if performance has returned to acceptable levels
            current_degradation = ((current_value - baseline) / baseline) * 100 if baseline > 0 else 0
            
            # Consider resolved if degradation is less than 10% or performance improved significantly
            if abs(current_degradation) < 10 or current_degradation < regression.degradation_percentage * 0.5:
                
                # Mark as resolved
                del self.active_regressions[metric_name]
                
                duration = datetime.utcnow() - regression.detected_at
                
                logger.info(
                    f"✅ REGRESSION RESOLVED: {metric_name} - "
                    f"recovered after {duration.total_seconds()/60:.1f} minutes"
                )
        
        except Exception as e:
            logger.debug(f"Error checking regression resolution for {metric_name}: {e}")
    
    async def _update_baselines(self):
        """Update baselines for all metrics if needed"""
        try:
            current_time = datetime.utcnow()
            
            for history in self.metric_histories.values():
                if self._should_update_baseline(history, current_time):
                    await self._update_metric_baseline(history)
        
        except Exception as e:
            logger.error(f"Error updating baselines: {e}")
    
    async def _cleanup_old_data(self):
        """Clean up old data to manage memory usage"""
        try:
            current_time = datetime.utcnow()
            cleanup_threshold = timedelta(days=7)
            
            # Clean up old regression history
            cutoff_time = current_time - cleanup_threshold
            
            # Keep only recent regression history
            self.regression_history = deque(
                [r for r in self.regression_history 
                 if datetime.fromisoformat(r['detected_at'].replace('Z', '+00:00')) > cutoff_time],
                maxlen=1000
            )
            
        except Exception as e:
            logger.error(f"Error cleaning up old data: {e}")
    
    def get_regression_summary(self) -> Dict[str, Any]:
        """Get regression detection system summary"""
        try:
            current_time = datetime.utcnow()
            
            # Calculate detection rates
            total_metrics = len(self.metric_histories)
            active_regressions_count = len(self.active_regressions)
            
            # Categorize active regressions by severity
            severity_counts = defaultdict(int)
            for regression in self.active_regressions.values():
                severity_counts[regression.severity.value] += 1
            
            return {
                'timestamp': current_time.isoformat(),
                'system_status': 'active' if self.is_active else 'inactive',
                'statistics': self.detection_stats,
                'metrics_monitored': total_metrics,
                'active_regressions': active_regressions_count,
                'regression_by_severity': dict(severity_counts),
                'total_regression_history': len(self.regression_history),
                'detection_config': self.detection_config,
                'baseline_coverage': sum(1 for h in self.metric_histories.values() if h.baselines),
                'data_quality': {
                    'metrics_with_sufficient_data': sum(1 for h in self.metric_histories.values() 
                                                      if len(h.values) >= self.detection_config['min_data_points']),
                    'average_data_points': statistics.mean([len(h.values) for h in self.metric_histories.values()]) 
                                         if self.metric_histories else 0
                }
            }
        
        except Exception as e:
            logger.error(f"Error getting regression summary: {e}")
            return {'error': str(e)}
    
    def get_active_regressions(self) -> Dict[str, Any]:
        """Get all active regressions"""
        return {
            metric_name: asdict(regression) 
            for metric_name, regression in self.active_regressions.items()
        }
    
    def get_metric_history_summary(self) -> Dict[str, Any]:
        """Get summary of metric histories"""
        return {
            metric_name: {
                'data_points': len(history.values),
                'baselines_count': len(history.baselines),
                'last_baseline_update': history.last_baseline_update.isoformat(),
                'current_baseline': history.baselines[-1] if history.baselines else None,
                'recent_value': history.values[-1] if history.values else None
            }
            for metric_name, history in self.metric_histories.items()
        }


# Global instance for easy access
regression_detector = PerformanceRegressionDetector()


# Convenience functions
async def start_regression_detection():
    """Start regression detection system"""
    await regression_detector.start_detection()


async def stop_regression_detection():
    """Stop regression detection system"""
    await regression_detector.stop_detection()


def get_regression_status() -> Dict[str, Any]:
    """Get current regression detection status"""
    return regression_detector.get_regression_summary()


def get_active_performance_regressions() -> Dict[str, Any]:
    """Get all active performance regressions"""
    return regression_detector.get_active_regressions()


# Auto-start regression detection when module is imported
try:
    asyncio.create_task(start_regression_detection())
    logger.info("🔍 Performance Regression Detection System scheduled for startup")
except RuntimeError:
    # Event loop not running yet, will be started later
    logger.info("🔍 Performance Regression Detection System ready for manual startup")