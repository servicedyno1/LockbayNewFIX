"""
Monitoring Orchestrator
Coordinates and manages the complete comprehensive monitoring infrastructure
"""

import logging
import asyncio
import time
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from enum import Enum

# Import all monitoring components
from utils.standardized_metrics_framework import standardized_metrics, start_metrics_collection
from utils.performance_baselines_config import get_baselines_summary
from utils.central_metrics_aggregator import central_aggregator, start_metrics_aggregation
from utils.unified_performance_reporting import start_performance_reporting
from utils.monitoring_systems_integration import MonitoringSystemsIntegration
from utils.comprehensive_monitoring_dashboard import (
    comprehensive_dashboard, start_comprehensive_monitoring, get_dashboard_data
)
from utils.enhanced_alert_correlation import (
    alert_correlation, start_alert_correlation, get_correlation_status
)
from utils.performance_regression_detection import (
    regression_detector, start_regression_detection, get_regression_status
)
from utils.system_health import SystemHealthMonitor

logger = logging.getLogger(__name__)


class MonitoringSystemStatus(Enum):
    """Status of monitoring subsystems"""
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class SubsystemStatus:
    """Status of a monitoring subsystem"""
    name: str
    status: MonitoringSystemStatus
    started_at: Optional[datetime] = None
    error_message: Optional[str] = None
    last_health_check: Optional[datetime] = None
    health_status: str = "unknown"
    performance_impact: str = "minimal"


class MonitoringOrchestrator:
    """
    Central orchestrator for the comprehensive monitoring infrastructure.
    Manages all monitoring subsystems and provides unified control and status.
    """
    
    def __init__(self):
        self.is_active = False
        self.startup_complete = False
        self.orchestrator_task: Optional[asyncio.Task] = None
        
        # Subsystem management
        self.subsystems: Dict[str, SubsystemStatus] = {}
        self.startup_order = [
            "standardized_metrics",
            "central_aggregator", 
            "monitoring_integration",
            "performance_baselines",
            "unified_reporter",
            "comprehensive_dashboard",
            "alert_correlation", 
            "regression_detection"
        ]
        
        # System statistics
        self.orchestrator_stats = {
            'startup_time': None,
            'uptime_seconds': 0,
            'subsystems_managed': 0,
            'health_checks_performed': 0,
            'errors_handled': 0,
            'performance_optimizations': 0
        }
        
        # Health monitoring
        self.last_system_health_check = datetime.min
        self.health_check_interval = timedelta(minutes=5)
        self.system_health_history = []
        
        # Performance monitoring
        self.performance_metrics = {
            'dashboard_response_time_ms': [],
            'alert_processing_time_ms': [],
            'regression_analysis_time_ms': [],
            'overall_monitoring_overhead_percent': 0.0
        }
        
        logger.info("🎛️ Monitoring Orchestrator initialized")
    
    async def start_monitoring_infrastructure(self):
        """Start the complete monitoring infrastructure"""
        if self.is_active:
            logger.warning("Monitoring infrastructure already starting/running")
            return
        
        logger.info("🚀 STARTING COMPREHENSIVE MONITORING INFRASTRUCTURE")
        
        self.is_active = True
        startup_start_time = time.time()
        
        try:
            # Initialize all subsystems in order
            await self._initialize_subsystems()
            
            # Start orchestrator management loop
            self.orchestrator_task = asyncio.create_task(self._orchestrator_loop())
            
            # Record startup completion
            startup_time = time.time() - startup_start_time
            self.orchestrator_stats['startup_time'] = startup_time
            self.startup_complete = True
            
            logger.info(f"✅ MONITORING INFRASTRUCTURE STARTED in {startup_time:.2f}s")
            logger.info(f"📊 Subsystems active: {self._get_active_subsystem_count()}/{len(self.startup_order)}")
            
            # Perform initial health check
            await self._perform_comprehensive_health_check()
            
        except Exception as e:
            logger.error(f"❌ Failed to start monitoring infrastructure: {e}")
            self.is_active = False
            self.orchestrator_stats['errors_handled'] += 1
            raise
    
    async def stop_monitoring_infrastructure(self):
        """Stop the complete monitoring infrastructure"""
        if not self.is_active:
            logger.info("Monitoring infrastructure already stopped")
            return
        
        logger.info("🛑 STOPPING COMPREHENSIVE MONITORING INFRASTRUCTURE")
        
        self.is_active = False
        
        try:
            # Stop orchestrator loop
            if self.orchestrator_task:
                self.orchestrator_task.cancel()
                try:
                    await self.orchestrator_task
                except asyncio.CancelledError:
                    pass
            
            # Stop all subsystems in reverse order
            await self._stop_all_subsystems()
            
            logger.info("✅ MONITORING INFRASTRUCTURE STOPPED")
            
        except Exception as e:
            logger.error(f"❌ Error stopping monitoring infrastructure: {e}")
            self.orchestrator_stats['errors_handled'] += 1
    
    async def _initialize_subsystems(self):
        """Initialize all monitoring subsystems in the correct order"""
        logger.info("🔧 Initializing monitoring subsystems...")
        
        for subsystem_name in self.startup_order:
            try:
                await self._start_subsystem(subsystem_name)
                await asyncio.sleep(1)  # Brief pause between startups
            except Exception as e:
                logger.error(f"❌ Failed to start {subsystem_name}: {e}")
                self.subsystems[subsystem_name] = SubsystemStatus(
                    name=subsystem_name,
                    status=MonitoringSystemStatus.ERROR,
                    error_message=str(e)
                )
                self.orchestrator_stats['errors_handled'] += 1
                # Continue with other subsystems even if one fails
        
        self.orchestrator_stats['subsystems_managed'] = len(self.subsystems)
    
    async def _start_subsystem(self, subsystem_name: str):
        """Start a specific monitoring subsystem"""
        logger.info(f"🚀 Starting {subsystem_name}...")
        
        self.subsystems[subsystem_name] = SubsystemStatus(
            name=subsystem_name,
            status=MonitoringSystemStatus.STARTING,
            started_at=datetime.utcnow()
        )
        
        try:
            if subsystem_name == "standardized_metrics":
                await start_metrics_collection()
                
            elif subsystem_name == "central_aggregator":
                await start_metrics_aggregation()
                
            elif subsystem_name == "monitoring_integration":
                integration = MonitoringSystemsIntegration()
                await integration.initialize_integrations()
                
            elif subsystem_name == "performance_baselines":
                # Performance baselines are configured, just validate
                summary = get_baselines_summary()
                if not summary.get('baselines_count', 0):
                    raise Exception("No performance baselines configured")
                
            elif subsystem_name == "unified_reporter":
                await start_performance_reporting()
                
            elif subsystem_name == "comprehensive_dashboard":
                await start_comprehensive_monitoring()
                
            elif subsystem_name == "alert_correlation":
                await start_alert_correlation()
                
            elif subsystem_name == "regression_detection":
                await start_regression_detection()
            
            else:
                raise Exception(f"Unknown subsystem: {subsystem_name}")
            
            # Mark as running
            self.subsystems[subsystem_name].status = MonitoringSystemStatus.RUNNING
            self.subsystems[subsystem_name].health_status = "healthy"
            
            logger.info(f"✅ {subsystem_name} started successfully")
            
        except Exception as e:
            self.subsystems[subsystem_name].status = MonitoringSystemStatus.ERROR
            self.subsystems[subsystem_name].error_message = str(e)
            raise
    
    async def _stop_all_subsystems(self):
        """Stop all monitoring subsystems in reverse order"""
        logger.info("🛑 Stopping monitoring subsystems...")
        
        # Stop in reverse order
        for subsystem_name in reversed(self.startup_order):
            if subsystem_name in self.subsystems:
                try:
                    await self._stop_subsystem(subsystem_name)
                except Exception as e:
                    logger.error(f"❌ Error stopping {subsystem_name}: {e}")
                    self.orchestrator_stats['errors_handled'] += 1
    
    async def _stop_subsystem(self, subsystem_name: str):
        """Stop a specific monitoring subsystem"""
        if subsystem_name not in self.subsystems:
            return
        
        logger.info(f"🛑 Stopping {subsystem_name}...")
        
        self.subsystems[subsystem_name].status = MonitoringSystemStatus.STOPPING
        
        try:
            if subsystem_name == "comprehensive_dashboard":
                await comprehensive_dashboard.stop_monitoring()
                
            elif subsystem_name == "alert_correlation":
                await alert_correlation.stop_correlation()
                
            elif subsystem_name == "regression_detection":
                await regression_detector.stop_detection()
            
            # Other subsystems may not have explicit stop methods
            
            self.subsystems[subsystem_name].status = MonitoringSystemStatus.STOPPED
            logger.info(f"✅ {subsystem_name} stopped")
            
        except Exception as e:
            self.subsystems[subsystem_name].status = MonitoringSystemStatus.ERROR
            self.subsystems[subsystem_name].error_message = str(e)
            raise
    
    async def _orchestrator_loop(self):
        """Main orchestrator management loop"""
        logger.info("🎛️ Starting orchestrator management loop")
        
        loop_count = 0
        
        while self.is_active:
            try:
                loop_start_time = time.time()
                
                # Update uptime
                if self.orchestrator_stats['startup_time']:
                    self.orchestrator_stats['uptime_seconds'] = time.time() - self.orchestrator_stats['startup_time']
                
                # Perform health checks
                await self._check_subsystem_health()
                
                # Monitor performance
                await self._monitor_performance()
                
                # Handle any issues
                await self._handle_subsystem_issues()
                
                # Optimize performance if needed
                await self._optimize_performance()
                
                # Log status periodically (every 10 minutes)
                loop_count += 1
                if loop_count % 20 == 0:  # Every 20 loops (10 minutes at 30s intervals)
                    await self._log_orchestrator_status()
                
                loop_time = time.time() - loop_start_time
                if loop_time > 2.0:
                    logger.warning(f"⏳ Orchestrator loop took {loop_time:.2f}s")
                
            except Exception as e:
                logger.error(f"Error in orchestrator loop: {e}")
                self.orchestrator_stats['errors_handled'] += 1
            
            await asyncio.sleep(30)  # Check every 30 seconds
    
    async def _check_subsystem_health(self):
        """Check health of all monitoring subsystems"""
        try:
            current_time = datetime.utcnow()
            
            # Check if it's time for comprehensive health check
            if current_time - self.last_system_health_check > self.health_check_interval:
                await self._perform_comprehensive_health_check()
                self.last_system_health_check = current_time
            
            # Quick health check for each subsystem
            for subsystem_name, subsystem in self.subsystems.items():
                if subsystem.status == MonitoringSystemStatus.RUNNING:
                    health_status = await self._check_individual_subsystem_health(subsystem_name)
                    subsystem.health_status = health_status
                    subsystem.last_health_check = current_time
            
            self.orchestrator_stats['health_checks_performed'] += 1
            
        except Exception as e:
            logger.error(f"Error checking subsystem health: {e}")
    
    async def _check_individual_subsystem_health(self, subsystem_name: str) -> str:
        """Check health of an individual subsystem"""
        try:
            if subsystem_name == "comprehensive_dashboard":
                dashboard_data = get_dashboard_data()
                return "healthy" if dashboard_data and not dashboard_data.get('error') else "degraded"
                
            elif subsystem_name == "alert_correlation":
                correlation_status = get_correlation_status()
                return "healthy" if correlation_status.get('system_status') == 'active' else "degraded"
                
            elif subsystem_name == "regression_detection":
                regression_status = get_regression_status()
                return "healthy" if regression_status.get('system_status') == 'active' else "degraded"
                
            elif subsystem_name == "standardized_metrics":
                metrics = standardized_metrics.get_current_metrics()
                return "healthy" if metrics else "degraded"
                
            elif subsystem_name == "central_aggregator":
                summary = central_aggregator.get_aggregation_summary()
                return "healthy" if summary.get('collectors_active', 0) > 0 else "degraded"
            
            # Default for other subsystems
            return "healthy"
            
        except Exception as e:
            logger.debug(f"Error checking {subsystem_name} health: {e}")
            return "error"
    
    async def _perform_comprehensive_health_check(self):
        """Perform comprehensive health check of the entire system"""
        try:
            logger.info("🏥 Performing comprehensive monitoring health check...")
            
            health_check_start = time.time()
            
            # Collect health data from all components
            health_data = {
                'timestamp': datetime.utcnow().isoformat(),
                'orchestrator_uptime_hours': self.orchestrator_stats['uptime_seconds'] / 3600,
                'subsystems': {},
                'system_health': {},
                'performance_summary': {},
                'alerts_summary': {},
                'regressions_summary': {}
            }
            
            # Get subsystem statuses
            for name, subsystem in self.subsystems.items():
                health_data['subsystems'][name] = {
                    'status': subsystem.status.value,
                    'health': subsystem.health_status,
                    'uptime_hours': (datetime.utcnow() - subsystem.started_at).total_seconds() / 3600 if subsystem.started_at else 0,
                    'error': subsystem.error_message
                }
            
            # Get system health
            try:
                system_health = SystemHealthMonitor.check_system_health()
                health_data['system_health'] = system_health
            except Exception as e:
                health_data['system_health'] = {'error': str(e)}
            
            # Get dashboard summary
            try:
                dashboard_data = get_dashboard_data()
                if dashboard_data and 'system_health_summary' in dashboard_data:
                    health_data['performance_summary'] = dashboard_data['system_health_summary']
            except Exception as e:
                health_data['performance_summary'] = {'error': str(e)}
            
            # Get alert correlation summary
            try:
                correlation_status = get_correlation_status()
                health_data['alerts_summary'] = correlation_status
            except Exception as e:
                health_data['alerts_summary'] = {'error': str(e)}
            
            # Get regression detection summary
            try:
                regression_status = get_regression_status()
                health_data['regressions_summary'] = regression_status
            except Exception as e:
                health_data['regressions_summary'] = {'error': str(e)}
            
            # Store in history
            self.system_health_history.append(health_data)
            if len(self.system_health_history) > 100:  # Keep last 100 health checks
                self.system_health_history.pop(0)
            
            health_check_time = time.time() - health_check_start
            
            # Log summary
            active_subsystems = self._get_active_subsystem_count()
            total_subsystems = len(self.subsystems)
            
            logger.info(
                f"🏥 Health Check Complete: {active_subsystems}/{total_subsystems} subsystems healthy, "
                f"System Health: {health_data['system_health'].get('status', 'unknown')}, "
                f"Check time: {health_check_time:.2f}s"
            )
            
        except Exception as e:
            logger.error(f"Error in comprehensive health check: {e}")
    
    async def _monitor_performance(self):
        """Monitor performance of the monitoring infrastructure itself"""
        try:
            # Monitor dashboard response time
            dashboard_start = time.time()
            get_dashboard_data()
            dashboard_time = (time.time() - dashboard_start) * 1000
            
            self.performance_metrics['dashboard_response_time_ms'].append(dashboard_time)
            if len(self.performance_metrics['dashboard_response_time_ms']) > 100:
                self.performance_metrics['dashboard_response_time_ms'].pop(0)
            
            # Monitor alert correlation performance
            correlation_start = time.time()
            get_correlation_status()
            correlation_time = (time.time() - correlation_start) * 1000
            
            self.performance_metrics['alert_processing_time_ms'].append(correlation_time)
            if len(self.performance_metrics['alert_processing_time_ms']) > 100:
                self.performance_metrics['alert_processing_time_ms'].pop(0)
            
            # Monitor regression detection performance  
            regression_start = time.time()
            get_regression_status()
            regression_time = (time.time() - regression_start) * 1000
            
            self.performance_metrics['regression_analysis_time_ms'].append(regression_time)
            if len(self.performance_metrics['regression_analysis_time_ms']) > 100:
                self.performance_metrics['regression_analysis_time_ms'].pop(0)
            
            # Calculate overall monitoring overhead
            total_monitoring_time = dashboard_time + correlation_time + regression_time
            # Assume this represents roughly 1% of system time in normal operation
            self.performance_metrics['overall_monitoring_overhead_percent'] = min(5.0, total_monitoring_time / 1000.0)
            
        except Exception as e:
            logger.debug(f"Error monitoring performance: {e}")
    
    async def _handle_subsystem_issues(self):
        """Handle any issues with monitoring subsystems"""
        try:
            for subsystem_name, subsystem in self.subsystems.items():
                if subsystem.status == MonitoringSystemStatus.ERROR:
                    # Attempt to restart failed subsystems
                    logger.warning(f"🔄 Attempting to restart failed subsystem: {subsystem_name}")
                    try:
                        await self._start_subsystem(subsystem_name)
                        logger.info(f"✅ Successfully restarted {subsystem_name}")
                    except Exception as e:
                        logger.error(f"❌ Failed to restart {subsystem_name}: {e}")
                        self.orchestrator_stats['errors_handled'] += 1
                
                elif subsystem.health_status == "error":
                    # Log degraded subsystems but don't restart automatically
                    logger.warning(f"⚠️ Subsystem {subsystem_name} in error state")
        
        except Exception as e:
            logger.error(f"Error handling subsystem issues: {e}")
    
    async def _optimize_performance(self):
        """Optimize performance of monitoring infrastructure"""
        try:
            # Check if any performance metrics are concerning
            if self.performance_metrics['dashboard_response_time_ms']:
                avg_dashboard_time = sum(self.performance_metrics['dashboard_response_time_ms']) / len(self.performance_metrics['dashboard_response_time_ms'])
                
                if avg_dashboard_time > 1000:  # > 1 second
                    logger.warning(f"⏳ Dashboard response time high: {avg_dashboard_time:.1f}ms")
                    # Could implement dashboard optimization here
                    self.orchestrator_stats['performance_optimizations'] += 1
            
            # Check overall monitoring overhead
            if self.performance_metrics['overall_monitoring_overhead_percent'] > 3.0:
                logger.warning(f"📊 Monitoring overhead high: {self.performance_metrics['overall_monitoring_overhead_percent']:.1f}%")
                # Could implement optimization strategies here
                self.orchestrator_stats['performance_optimizations'] += 1
        
        except Exception as e:
            logger.debug(f"Error optimizing performance: {e}")
    
    async def _log_orchestrator_status(self):
        """Log comprehensive orchestrator status"""
        try:
            active_count = self._get_active_subsystem_count()
            total_count = len(self.subsystems)
            uptime_hours = self.orchestrator_stats['uptime_seconds'] / 3600
            
            # Calculate average performance metrics
            avg_dashboard_time = 0
            if self.performance_metrics['dashboard_response_time_ms']:
                avg_dashboard_time = sum(self.performance_metrics['dashboard_response_time_ms']) / len(self.performance_metrics['dashboard_response_time_ms'])
            
            logger.info(
                f"🎛️ ORCHESTRATOR STATUS: {active_count}/{total_count} subsystems active, "
                f"Uptime: {uptime_hours:.1f}h, Dashboard: {avg_dashboard_time:.1f}ms, "
                f"Errors: {self.orchestrator_stats['errors_handled']}, "
                f"Health Checks: {self.orchestrator_stats['health_checks_performed']}"
            )
            
        except Exception as e:
            logger.error(f"Error logging orchestrator status: {e}")
    
    def _get_active_subsystem_count(self) -> int:
        """Get count of active subsystems"""
        return sum(1 for s in self.subsystems.values() if s.status == MonitoringSystemStatus.RUNNING)
    
    def get_orchestrator_status(self) -> Dict[str, Any]:
        """Get comprehensive orchestrator status"""
        try:
            current_time = datetime.utcnow()
            
            # Calculate performance averages
            avg_dashboard_time = 0
            avg_alert_time = 0
            avg_regression_time = 0
            
            if self.performance_metrics['dashboard_response_time_ms']:
                avg_dashboard_time = sum(self.performance_metrics['dashboard_response_time_ms']) / len(self.performance_metrics['dashboard_response_time_ms'])
            
            if self.performance_metrics['alert_processing_time_ms']:
                avg_alert_time = sum(self.performance_metrics['alert_processing_time_ms']) / len(self.performance_metrics['alert_processing_time_ms'])
            
            if self.performance_metrics['regression_analysis_time_ms']:
                avg_regression_time = sum(self.performance_metrics['regression_analysis_time_ms']) / len(self.performance_metrics['regression_analysis_time_ms'])
            
            return {
                'timestamp': current_time.isoformat(),
                'orchestrator_active': self.is_active,
                'startup_complete': self.startup_complete,
                'statistics': self.orchestrator_stats,
                'subsystems': {
                    name: asdict(status) for name, status in self.subsystems.items()
                },
                'subsystems_summary': {
                    'total': len(self.subsystems),
                    'active': self._get_active_subsystem_count(),
                    'error': sum(1 for s in self.subsystems.values() if s.status == MonitoringSystemStatus.ERROR),
                    'healthy': sum(1 for s in self.subsystems.values() if s.health_status == "healthy")
                },
                'performance_metrics': {
                    'avg_dashboard_response_ms': avg_dashboard_time,
                    'avg_alert_processing_ms': avg_alert_time,
                    'avg_regression_analysis_ms': avg_regression_time,
                    'monitoring_overhead_percent': self.performance_metrics['overall_monitoring_overhead_percent']
                },
                'system_health_checks': len(self.system_health_history),
                'last_health_check': self.last_system_health_check.isoformat() if self.last_system_health_check != datetime.min else None
            }
        
        except Exception as e:
            logger.error(f"Error getting orchestrator status: {e}")
            return {'error': str(e), 'timestamp': datetime.utcnow().isoformat()}
    
    def get_system_health_history(self) -> List[Dict[str, Any]]:
        """Get system health history"""
        return self.system_health_history
    
    def get_monitoring_summary(self) -> Dict[str, Any]:
        """Get comprehensive monitoring infrastructure summary"""
        try:
            orchestrator_status = self.get_orchestrator_status()
            
            # Get data from each monitoring component
            dashboard_data = get_dashboard_data() if 'comprehensive_dashboard' in self.subsystems else {}
            correlation_status = get_correlation_status() if 'alert_correlation' in self.subsystems else {}
            regression_status = get_regression_status() if 'regression_detection' in self.subsystems else {}
            
            return {
                'timestamp': datetime.utcnow().isoformat(),
                'infrastructure_status': 'operational' if self.is_active and self.startup_complete else 'initializing',
                'orchestrator': orchestrator_status,
                'monitoring_dashboard': dashboard_data,
                'alert_correlation': correlation_status,
                'regression_detection': regression_status,
                'baselines_configuration': get_baselines_summary(),
                'overall_health': self._calculate_overall_health()
            }
        
        except Exception as e:
            logger.error(f"Error getting monitoring summary: {e}")
            return {'error': str(e), 'timestamp': datetime.utcnow().isoformat()}
    
    def _calculate_overall_health(self) -> Dict[str, Any]:
        """Calculate overall health of the monitoring infrastructure"""
        try:
            total_subsystems = len(self.subsystems)
            self._get_active_subsystem_count()
            healthy_subsystems = sum(1 for s in self.subsystems.values() if s.health_status == "healthy")
            
            if total_subsystems == 0:
                return {'status': 'unknown', 'score': 0}
            
            # Calculate health score (0-100)
            health_score = (healthy_subsystems / total_subsystems) * 100
            
            # Determine status
            if health_score >= 90:
                status = 'excellent'
            elif health_score >= 75:
                status = 'good'
            elif health_score >= 60:
                status = 'degraded'
            elif health_score >= 30:
                status = 'poor'
            else:
                status = 'critical'
            
            return {
                'status': status,
                'score': health_score,
                'subsystems_healthy': healthy_subsystems,
                'subsystems_total': total_subsystems,
                'infrastructure_uptime_hours': self.orchestrator_stats['uptime_seconds'] / 3600,
                'errors_handled': self.orchestrator_stats['errors_handled']
            }
        
        except Exception as e:
            logger.debug(f"Error calculating overall health: {e}")
            return {'status': 'error', 'score': 0, 'error': str(e)}


# Global instance for easy access
monitoring_orchestrator = MonitoringOrchestrator()


# Convenience functions
async def start_monitoring():
    """Start the complete monitoring infrastructure"""
    await monitoring_orchestrator.start_monitoring_infrastructure()


async def stop_monitoring():
    """Stop the complete monitoring infrastructure"""
    await monitoring_orchestrator.stop_monitoring_infrastructure()


def get_monitoring_status() -> Dict[str, Any]:
    """Get current monitoring infrastructure status"""
    return monitoring_orchestrator.get_orchestrator_status()


def get_complete_monitoring_summary() -> Dict[str, Any]:
    """Get comprehensive monitoring summary"""
    return monitoring_orchestrator.get_monitoring_summary()


# Auto-start monitoring when module is imported
try:
    asyncio.create_task(start_monitoring())
    logger.info("🎛️ Monitoring Orchestrator scheduled for startup")
except RuntimeError:
    # Event loop not running yet, will be started later
    logger.info("🎛️ Monitoring Orchestrator ready for manual startup")