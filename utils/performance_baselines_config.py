"""
Performance Baselines Configuration
Establishes standard performance thresholds, alert levels, and benchmarks for the system
"""

import logging
from typing import Dict, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
from collections import defaultdict

from utils.standardized_metrics_framework import MetricThreshold, MetricUnit

logger = logging.getLogger(__name__)


class PerformanceLevel(Enum):
    """Performance level classifications"""
    EXCELLENT = "excellent"
    GOOD = "good"
    WARNING = "warning"
    CRITICAL = "critical"
    EMERGENCY = "emergency"


class SystemProfile(Enum):
    """System deployment profiles with different baseline expectations"""
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"
    HIGH_TRAFFIC = "high_traffic"


@dataclass
class BaselineThreshold:
    """Enhanced threshold configuration with multiple levels"""
    excellent_max: float                 # Below this = excellent performance
    good_max: float                     # Below this = good performance
    warning_max: float                  # Below this = warning level
    critical_max: float                 # Below this = critical level
    # Above critical_max = emergency level
    
    unit: MetricUnit
    higher_is_worse: bool = True
    description: str = ""
    
    def get_performance_level(self, value: float) -> PerformanceLevel:
        """Determine performance level for a given value"""
        if self.higher_is_worse:
            if value <= self.excellent_max:
                return PerformanceLevel.EXCELLENT
            elif value <= self.good_max:
                return PerformanceLevel.GOOD
            elif value <= self.warning_max:
                return PerformanceLevel.WARNING
            elif value <= self.critical_max:
                return PerformanceLevel.CRITICAL
            else:
                return PerformanceLevel.EMERGENCY
        else:
            # Lower is worse (e.g., available memory)
            if value >= self.excellent_max:
                return PerformanceLevel.EXCELLENT
            elif value >= self.good_max:
                return PerformanceLevel.GOOD
            elif value >= self.warning_max:
                return PerformanceLevel.WARNING
            elif value >= self.critical_max:
                return PerformanceLevel.CRITICAL
            else:
                return PerformanceLevel.EMERGENCY
    
    def to_legacy_threshold(self) -> MetricThreshold:
        """Convert to legacy MetricThreshold format"""
        return MetricThreshold(
            warning_level=self.warning_max,
            critical_level=self.critical_max,
            unit=self.unit,
            higher_is_worse=self.higher_is_worse
        )


@dataclass
class SystemBaselines:
    """Comprehensive system performance baselines for different profiles"""
    
    # === SYSTEM RESOURCE BASELINES ===
    
    # Memory baselines (MB)
    process_memory_rss_mb: BaselineThreshold = field(default_factory=lambda: BaselineThreshold(
        excellent_max=100.0, good_max=150.0, warning_max=200.0, critical_max=300.0,
        unit=MetricUnit.MEGABYTES, description="Process memory usage (RSS)"
    ))
    
    system_memory_percent: BaselineThreshold = field(default_factory=lambda: BaselineThreshold(
        excellent_max=50.0, good_max=70.0, warning_max=80.0, critical_max=95.0,
        unit=MetricUnit.PERCENT, description="System memory usage percentage"
    ))
    
    system_memory_available_mb: BaselineThreshold = field(default_factory=lambda: BaselineThreshold(
        excellent_max=2048.0, good_max=1024.0, warning_max=512.0, critical_max=256.0,
        unit=MetricUnit.MEGABYTES, higher_is_worse=False, description="Available system memory"
    ))
    
    # CPU baselines (%)
    process_cpu_percent: BaselineThreshold = field(default_factory=lambda: BaselineThreshold(
        excellent_max=10.0, good_max=25.0, warning_max=50.0, critical_max=80.0,
        unit=MetricUnit.PERCENT, description="Process CPU usage"
    ))
    
    system_cpu_percent: BaselineThreshold = field(default_factory=lambda: BaselineThreshold(
        excellent_max=30.0, good_max=50.0, warning_max=70.0, critical_max=90.0,
        unit=MetricUnit.PERCENT, description="System CPU usage"
    ))
    
    # === APPLICATION PERFORMANCE BASELINES ===
    
    # Startup performance (seconds)
    app_startup_total_s: BaselineThreshold = field(default_factory=lambda: BaselineThreshold(
        excellent_max=10.0, good_max=20.0, warning_max=30.0, critical_max=120.0,
        unit=MetricUnit.SECONDS, description="Total application startup time"
    ))
    
    app_startup_stage_s: BaselineThreshold = field(default_factory=lambda: BaselineThreshold(
        excellent_max=2.0, good_max=5.0, warning_max=10.0, critical_max=30.0,
        unit=MetricUnit.SECONDS, description="Individual startup stage time"
    ))
    
    # Operation performance (milliseconds)
    app_operation_duration_ms: BaselineThreshold = field(default_factory=lambda: BaselineThreshold(
        excellent_max=100.0, good_max=500.0, warning_max=1000.0, critical_max=3000.0,
        unit=MetricUnit.MILLISECONDS, description="Application operation duration"
    ))
    
    # Error rates (per hour)
    app_error_rate_per_hour: BaselineThreshold = field(default_factory=lambda: BaselineThreshold(
        excellent_max=0.0, good_max=2.0, warning_max=10.0, critical_max=50.0,
        unit=MetricUnit.PER_HOUR, description="Application error rate"
    ))
    
    # === NETWORK & WEBHOOK BASELINES ===
    
    # Webhook performance (milliseconds)
    webhook_latency_ms: BaselineThreshold = field(default_factory=lambda: BaselineThreshold(
        excellent_max=50.0, good_max=100.0, warning_max=200.0, critical_max=1000.0,
        unit=MetricUnit.MILLISECONDS, description="Webhook request latency"
    ))
    
    # Cold starts (count)
    webhook_cold_starts: BaselineThreshold = field(default_factory=lambda: BaselineThreshold(
        excellent_max=0.0, good_max=1.0, warning_max=5.0, critical_max=20.0,
        unit=MetricUnit.COUNT, description="Webhook cold starts per hour"
    ))
    
    # Network response times (milliseconds)
    network_response_time_ms: BaselineThreshold = field(default_factory=lambda: BaselineThreshold(
        excellent_max=200.0, good_max=500.0, warning_max=1000.0, critical_max=5000.0,
        unit=MetricUnit.MILLISECONDS, description="External API response time"
    ))
    
    # === DATABASE BASELINES ===
    
    # Query performance (milliseconds)
    database_query_duration_ms: BaselineThreshold = field(default_factory=lambda: BaselineThreshold(
        excellent_max=50.0, good_max=200.0, warning_max=500.0, critical_max=2000.0,
        unit=MetricUnit.MILLISECONDS, description="Database query duration"
    ))
    
    # Connection performance (milliseconds)
    database_connection_time_ms: BaselineThreshold = field(default_factory=lambda: BaselineThreshold(
        excellent_max=100.0, good_max=300.0, warning_max=1000.0, critical_level=5000.0,
        unit=MetricUnit.MILLISECONDS, description="Database connection time"
    ))
    
    # Connection count
    database_connections: BaselineThreshold = field(default_factory=lambda: BaselineThreshold(
        excellent_max=10.0, good_max=25.0, warning_max=50.0, critical_max=100.0,
        unit=MetricUnit.COUNT, description="Active database connections"
    ))


class PerformanceBaselinesManager:
    """
    Manages performance baselines for different system profiles and environments
    """
    
    def __init__(self):
        self.baselines_by_profile: Dict[SystemProfile, SystemBaselines] = {}
        self.current_profile = SystemProfile.PRODUCTION
        self.custom_baselines: Dict[str, BaselineThreshold] = {}
        
        # Initialize default baselines for all profiles
        self._initialize_default_baselines()
        
        logger.info("🔧 Performance Baselines Manager initialized")
    
    def _initialize_default_baselines(self):
        """Initialize default baselines for all system profiles"""
        
        # Development profile - more lenient thresholds
        dev_baselines = SystemBaselines()
        # Increase all thresholds by 50% for development
        self._scale_baselines(dev_baselines, 1.5)
        self.baselines_by_profile[SystemProfile.DEVELOPMENT] = dev_baselines
        
        # Production profile - standard thresholds
        prod_baselines = SystemBaselines()
        self.baselines_by_profile[SystemProfile.PRODUCTION] = prod_baselines
        
        # Staging profile - slightly more lenient than production
        staging_baselines = SystemBaselines()
        self._scale_baselines(staging_baselines, 1.2)
        self.baselines_by_profile[SystemProfile.STAGING] = staging_baselines
        
        # High traffic profile - more stringent thresholds
        high_traffic_baselines = SystemBaselines()
        self._scale_baselines(high_traffic_baselines, 0.8)
        # But allow higher resource usage
        high_traffic_baselines.process_memory_rss_mb = BaselineThreshold(
            excellent_max=200.0, good_max=300.0, warning_max=400.0, critical_max=600.0,
            unit=MetricUnit.MEGABYTES, description="Process memory usage (high traffic)"
        )
        self.baselines_by_profile[SystemProfile.HIGH_TRAFFIC] = high_traffic_baselines
        
        logger.info(f"Initialized baselines for {len(self.baselines_by_profile)} system profiles")
    
    def _scale_baselines(self, baselines: SystemBaselines, scale_factor: float):
        """Scale all baseline thresholds by a factor"""
        for attr_name in dir(baselines):
            if not attr_name.startswith('_'):
                attr = getattr(baselines, attr_name)
                if isinstance(attr, BaselineThreshold):
                    # Scale thresholds, but respect higher_is_worse logic
                    if attr.higher_is_worse:
                        # For "higher is worse" metrics, increase thresholds (more lenient)
                        attr.excellent_max *= scale_factor
                        attr.good_max *= scale_factor
                        attr.warning_max *= scale_factor
                        attr.critical_max *= scale_factor
                    else:
                        # For "lower is worse" metrics, decrease thresholds (but don't go below 0)
                        attr.excellent_max = max(attr.excellent_max / scale_factor, 1.0)
                        attr.good_max = max(attr.good_max / scale_factor, 1.0)
                        attr.warning_max = max(attr.warning_max / scale_factor, 1.0)
                        attr.critical_max = max(attr.critical_max / scale_factor, 1.0)
    
    def set_system_profile(self, profile: SystemProfile):
        """Set the current system profile"""
        if profile in self.baselines_by_profile:
            self.current_profile = profile
            logger.info(f"Set system profile to: {profile.value}")
        else:
            logger.error(f"Unknown system profile: {profile}")
    
    def get_current_baselines(self) -> SystemBaselines:
        """Get baselines for the current system profile"""
        return self.baselines_by_profile[self.current_profile]
    
    def get_baseline_for_metric(self, metric_name: str) -> Optional[BaselineThreshold]:
        """Get baseline threshold for a specific metric"""
        current_baselines = self.get_current_baselines()
        
        # Map standard metric names to baseline attributes
        metric_mapping = {
            'process.memory.rss_mb': 'process_memory_rss_mb',
            'system.memory.percent_used': 'system_memory_percent',
            'system.memory.available_mb': 'system_memory_available_mb',
            'process.cpu.usage_percent': 'process_cpu_percent',
            'system.cpu.usage_percent': 'system_cpu_percent',
            'app.startup.total_time_s': 'app_startup_total_s',
            'app.startup.stage_time_s': 'app_startup_stage_s',
            'app.operation.duration_ms': 'app_operation_duration_ms',
            'app.error.rate_per_hour': 'app_error_rate_per_hour',
            'webhook.request.latency_ms': 'webhook_latency_ms',
            'webhook.cold_start.count': 'webhook_cold_starts',
            'network.response.time_ms': 'network_response_time_ms',
            'database.query.duration_ms': 'database_query_duration_ms',
            'database.connection.time_ms': 'database_connection_time_ms',
            'database.connection.count': 'database_connections'
        }
        
        # Check custom baselines first
        if metric_name in self.custom_baselines:
            return self.custom_baselines[metric_name]
        
        # Check standard mappings
        baseline_attr = metric_mapping.get(metric_name)
        if baseline_attr and hasattr(current_baselines, baseline_attr):
            return getattr(current_baselines, baseline_attr)
        
        return None
    
    def evaluate_performance(self, metric_name: str, value: float) -> Dict[str, Any]:
        """Evaluate performance level for a metric value"""
        baseline = self.get_baseline_for_metric(metric_name)
        if not baseline:
            return {
                'metric_name': metric_name,
                'value': value,
                'performance_level': None,
                'baseline_available': False,
                'message': f"No baseline configured for metric: {metric_name}"
            }
        
        performance_level = baseline.get_performance_level(value)
        
        # Determine status message
        if performance_level == PerformanceLevel.EXCELLENT:
            status_icon = "✅"
            message = f"Excellent performance: {value:.2f} {baseline.unit.value}"
        elif performance_level == PerformanceLevel.GOOD:
            status_icon = "🟢"
            message = f"Good performance: {value:.2f} {baseline.unit.value}"
        elif performance_level == PerformanceLevel.WARNING:
            status_icon = "⚠️"
            message = f"Warning level: {value:.2f} {baseline.unit.value} (threshold: {baseline.warning_max})"
        elif performance_level == PerformanceLevel.CRITICAL:
            status_icon = "🔴"
            message = f"Critical level: {value:.2f} {baseline.unit.value} (threshold: {baseline.critical_max})"
        else:  # EMERGENCY
            status_icon = "🚨"
            message = f"Emergency level: {value:.2f} {baseline.unit.value} (exceeds critical: {baseline.critical_max})"
        
        return {
            'metric_name': metric_name,
            'value': value,
            'performance_level': performance_level.value,
            'baseline_available': True,
            'status_icon': status_icon,
            'message': message,
            'thresholds': {
                'excellent_max': baseline.excellent_max,
                'good_max': baseline.good_max,
                'warning_max': baseline.warning_max,
                'critical_max': baseline.critical_max
            },
            'description': baseline.description
        }
    
    def set_custom_baseline(self, metric_name: str, baseline: BaselineThreshold):
        """Set custom baseline for a specific metric"""
        self.custom_baselines[metric_name] = baseline
        logger.info(f"Set custom baseline for metric: {metric_name}")
    
    def remove_custom_baseline(self, metric_name: str):
        """Remove custom baseline for a specific metric"""
        if metric_name in self.custom_baselines:
            del self.custom_baselines[metric_name]
            logger.info(f"Removed custom baseline for metric: {metric_name}")
    
    def get_all_baselines_summary(self) -> Dict[str, Any]:
        """Get comprehensive summary of all configured baselines"""
        current_baselines = self.get_current_baselines()
        
        baselines_info = {}
        
        # Get all baseline attributes
        for attr_name in dir(current_baselines):
            if not attr_name.startswith('_'):
                attr = getattr(current_baselines, attr_name)
                if isinstance(attr, BaselineThreshold):
                    baselines_info[attr_name] = {
                        'excellent_max': attr.excellent_max,
                        'good_max': attr.good_max,
                        'warning_max': attr.warning_max,
                        'critical_max': attr.critical_max,
                        'unit': attr.unit.value,
                        'higher_is_worse': attr.higher_is_worse,
                        'description': attr.description
                    }
        
        # Add custom baselines
        custom_info = {}
        for metric_name, baseline in self.custom_baselines.items():
            custom_info[metric_name] = {
                'excellent_max': baseline.excellent_max,
                'good_max': baseline.good_max,
                'warning_max': baseline.warning_max,
                'critical_max': baseline.critical_max,
                'unit': baseline.unit.value,
                'higher_is_worse': baseline.higher_is_worse,
                'description': baseline.description
            }
        
        return {
            'current_profile': self.current_profile.value,
            'available_profiles': [profile.value for profile in SystemProfile],
            'standard_baselines': baselines_info,
            'custom_baselines': custom_info,
            'total_configured_baselines': len(baselines_info) + len(custom_info)
        }
    
    def export_baselines_config(self) -> Dict[str, Any]:
        """Export baselines configuration for backup/sharing"""
        return {
            'current_profile': self.current_profile.value,
            'profiles': {
                profile.value: self._baselines_to_dict(baselines)
                for profile, baselines in self.baselines_by_profile.items()
            },
            'custom_baselines': {
                metric_name: self._baseline_to_dict(baseline)
                for metric_name, baseline in self.custom_baselines.items()
            },
            'export_timestamp': datetime.utcnow().isoformat()
        }
    
    def _baselines_to_dict(self, baselines: SystemBaselines) -> Dict[str, Any]:
        """Convert SystemBaselines to dictionary"""
        result = {}
        for attr_name in dir(baselines):
            if not attr_name.startswith('_'):
                attr = getattr(baselines, attr_name)
                if isinstance(attr, BaselineThreshold):
                    result[attr_name] = self._baseline_to_dict(attr)
        return result
    
    def _baseline_to_dict(self, baseline: BaselineThreshold) -> Dict[str, Any]:
        """Convert BaselineThreshold to dictionary"""
        return {
            'excellent_max': baseline.excellent_max,
            'good_max': baseline.good_max,
            'warning_max': baseline.warning_max,
            'critical_max': baseline.critical_max,
            'unit': baseline.unit.value,
            'higher_is_worse': baseline.higher_is_worse,
            'description': baseline.description
        }
    
    def create_performance_report(self, metrics: Dict[str, float]) -> Dict[str, Any]:
        """Create comprehensive performance report for multiple metrics"""
        report = {
            'timestamp': datetime.utcnow().isoformat(),
            'system_profile': self.current_profile.value,
            'metrics_evaluated': len(metrics),
            'performance_levels': defaultdict(int),
            'evaluations': {},
            'overall_status': PerformanceLevel.EXCELLENT.value
        }
        
        worst_level = PerformanceLevel.EXCELLENT
        
        for metric_name, value in metrics.items():
            evaluation = self.evaluate_performance(metric_name, value)
            report['evaluations'][metric_name] = evaluation
            
            if evaluation['baseline_available']:
                level = PerformanceLevel(evaluation['performance_level'])
                report['performance_levels'][level.value] += 1
                
                # Track worst performance level for overall status
                if level.value == PerformanceLevel.EMERGENCY.value:
                    worst_level = PerformanceLevel.EMERGENCY
                elif level.value == PerformanceLevel.CRITICAL.value and worst_level != PerformanceLevel.EMERGENCY:
                    worst_level = PerformanceLevel.CRITICAL
                elif level.value == PerformanceLevel.WARNING.value and worst_level not in [PerformanceLevel.EMERGENCY, PerformanceLevel.CRITICAL]:
                    worst_level = PerformanceLevel.WARNING
                elif level.value == PerformanceLevel.GOOD.value and worst_level == PerformanceLevel.EXCELLENT:
                    worst_level = PerformanceLevel.GOOD
        
        report['overall_status'] = worst_level.value
        
        # Add summary statistics
        report['summary'] = {
            'total_metrics': len(metrics),
            'metrics_with_baselines': sum(1 for eval in report['evaluations'].values() if eval['baseline_available']),
            'excellent_count': report['performance_levels'][PerformanceLevel.EXCELLENT.value],
            'good_count': report['performance_levels'][PerformanceLevel.GOOD.value],
            'warning_count': report['performance_levels'][PerformanceLevel.WARNING.value],
            'critical_count': report['performance_levels'][PerformanceLevel.CRITICAL.value],
            'emergency_count': report['performance_levels'][PerformanceLevel.EMERGENCY.value]
        }
        
        return report


# Global instance for easy access
performance_baselines = PerformanceBaselinesManager()


# Convenience functions
def set_system_profile(profile: SystemProfile):
    """Set the current system profile"""
    performance_baselines.set_system_profile(profile)


def evaluate_metric_performance(metric_name: str, value: float) -> Dict[str, Any]:
    """Evaluate performance for a single metric"""
    return performance_baselines.evaluate_performance(metric_name, value)


def get_baseline_for_metric(metric_name: str) -> Optional[BaselineThreshold]:
    """Get baseline threshold for a metric"""
    return performance_baselines.get_baseline_for_metric(metric_name)


def create_performance_report(metrics: Dict[str, float]) -> Dict[str, Any]:
    """Create performance report for multiple metrics"""
    return performance_baselines.create_performance_report(metrics)


def get_baselines_summary() -> Dict[str, Any]:
    """Get summary of all configured baselines"""
    return performance_baselines.get_all_baselines_summary()


# Auto-detect system profile based on environment
def auto_detect_system_profile() -> SystemProfile:
    """Auto-detect appropriate system profile based on environment"""
    import os
    
    # Check environment variables
    env = os.getenv('ENVIRONMENT', '').lower()
    if env in ['dev', 'development']:
        return SystemProfile.DEVELOPMENT
    elif env in ['staging', 'test']:
        return SystemProfile.STAGING
    elif env in ['prod', 'production']:
        return SystemProfile.PRODUCTION
    else:
        # Default to production for safety
        return SystemProfile.PRODUCTION


# Initialize with auto-detected profile
try:
    detected_profile = auto_detect_system_profile()
    performance_baselines.set_system_profile(detected_profile)
    logger.info(f"Auto-detected system profile: {detected_profile.value}")
except Exception as e:
    logger.warning(f"Could not auto-detect system profile: {e}")
    performance_baselines.set_system_profile(SystemProfile.PRODUCTION)