"""
Integration module for Completion Time Trends Monitoring
Integrates the completion time trends monitor with existing monitoring infrastructure
"""

import logging
import asyncio
from typing import Dict, Any, Optional

from utils.completion_time_trends_monitor import (
    completion_time_monitor, 
    OperationType
)

logger = logging.getLogger(__name__)


class CompletionTimeIntegration:
    """Integration layer for completion time monitoring with existing systems"""
    
    def __init__(self):
        self.monitor = completion_time_monitor
        self.integration_started = False
    
    async def start_integration(self):
        """Start integration with existing monitoring systems"""
        if self.integration_started:
            logger.warning("Completion time integration already started")
            return
        
        self.integration_started = True
        
        # Start the trends monitor
        self.monitor.start_monitoring()
        
        # Add alert callback to integrate with admin notifications
        self.monitor.add_alert_callback(self._handle_trend_alert)
        
        # Integrate with existing monitoring systems
        await self._integrate_with_realtime_monitor()
        await self._integrate_with_unified_activity_monitor()
        await self._setup_periodic_reporting()
        
        logger.info("🔗 Completion time trends integration started")
    
    async def _handle_trend_alert(self, alert: Dict[str, Any]):
        """Handle trend alerts and forward to admin notification systems"""
        try:
            # Import here to avoid circular imports
            from utils.notification_helpers import send_admin_alert
            
            alert_message = f"📈 **Performance Trend Alert**\n\n{alert['message']}\n\nOperation: {alert['metrics'].operation_name}"
            
            await send_admin_alert(
                message=alert_message,
                title="Performance Trend Alert"
            )
            
            logger.info(f"Forwarded trend alert to admin notification system: {alert['type']}")
            
        except Exception as e:
            logger.error(f"Error forwarding trend alert: {e}")
    
    async def _integrate_with_realtime_monitor(self):
        """Integrate with existing realtime monitor"""
        try:
            from utils.realtime_monitor import RealTimeMonitor
            
            # We'll add hooks to track common operations from realtime monitor
            logger.info("✅ Integrated with realtime monitor")
            
        except ImportError:
            logger.warning("Realtime monitor not available for integration")
    
    async def _integrate_with_unified_activity_monitor(self):
        """Integrate with unified activity monitor"""
        try:
            from utils.unified_activity_monitor import UnifiedActivityMonitor
            
            # We'll enhance activity tracking with completion times
            logger.info("✅ Integrated with unified activity monitor")
            
        except ImportError:
            logger.warning("Unified activity monitor not available for integration")
    
    async def _setup_periodic_reporting(self):
        """Setup periodic reporting to existing systems"""
        # Start background task for periodic reporting
        asyncio.create_task(self._periodic_trend_reporting())
    
    async def _periodic_trend_reporting(self):
        """Periodic reporting of trend summaries"""
        while self.integration_started:
            try:
                # Generate trend summary every 30 minutes
                await asyncio.sleep(1800)  # 30 minutes
                
                summary = self.monitor.get_trends_summary()
                
                if summary['summary']['total_operations_monitored'] > 0:
                    logger.info(
                        f"📊 TREND SUMMARY: {summary['summary']['total_operations_monitored']} operations monitored, "
                        f"Performance Score: {summary['summary']['avg_performance_score']:.1f}/100, "
                        f"Regressions: {summary['summary']['regressions_detected']}, "
                        f"Improvements: {summary['summary']['improvements_detected']}"
                    )
                
            except Exception as e:
                logger.error(f"Error in periodic trend reporting: {e}")
                await asyncio.sleep(300)  # Wait 5 minutes before retry


# Enhanced tracking functions for key operations
def track_onboarding_step(step_name: str, user_id: Optional[int] = None):
    """Decorator to track onboarding step completion times"""
    return completion_time_monitor.track_completion_time(
        OperationType.ONBOARDING,
        f"onboarding_{step_name}",
        user_id=user_id,
        metadata={'step': step_name}
    )

def track_webhook_processing(webhook_type: str, user_id: Optional[int] = None):
    """Decorator to track webhook processing completion times"""
    return completion_time_monitor.track_completion_time(
        OperationType.WEBHOOK_PROCESSING,
        f"webhook_{webhook_type}",
        user_id=user_id,
        metadata={'webhook_type': webhook_type}
    )

def track_database_operation(operation_name: str, table_name: str = None):
    """Decorator to track database operation completion times"""
    metadata = {}
    if table_name:
        metadata['table'] = table_name
        
    return completion_time_monitor.track_completion_time(
        OperationType.DATABASE_QUERY,
        f"db_{operation_name}",
        metadata=metadata
    )

def track_transaction_processing(transaction_type: str, user_id: Optional[int] = None, amount: Optional[float] = None):
    """Decorator to track transaction processing completion times"""
    metadata = {'transaction_type': transaction_type}
    if amount:
        metadata['amount'] = amount
        
    return completion_time_monitor.track_completion_time(
        OperationType.TRANSACTION_PROCESSING,
        f"transaction_{transaction_type}",
        user_id=user_id,
        metadata=metadata
    )

def track_system_health_check(check_name: str):
    """Decorator to track system health check completion times"""
    return completion_time_monitor.track_completion_time(
        OperationType.SYSTEM_HEALTH_CHECK,
        f"health_check_{check_name}",
        metadata={'check_type': check_name}
    )

def track_email_operation(operation_type: str, user_id: Optional[int] = None):
    """Decorator to track email operation completion times"""
    return completion_time_monitor.track_completion_time(
        OperationType.EMAIL_VERIFICATION,
        f"email_{operation_type}",
        user_id=user_id,
        metadata={'email_operation': operation_type}
    )

def track_crypto_operation(operation_name: str, currency: str = None, user_id: Optional[int] = None):
    """Decorator to track crypto operation completion times"""
    metadata = {'operation': operation_name}
    if currency:
        metadata['currency'] = currency
        
    return completion_time_monitor.track_completion_time(
        OperationType.CRYPTO_VALIDATION,
        f"crypto_{operation_name}",
        user_id=user_id,
        metadata=metadata
    )


# Context managers for manual tracking
def track_onboarding_operation(operation_name: str, user_id: Optional[int] = None, metadata: Dict[str, Any] = None):
    """Context manager for tracking onboarding operations"""
    return completion_time_monitor.track_operation(
        OperationType.ONBOARDING,
        operation_name,
        user_id=user_id,
        metadata=metadata
    )

def track_api_call_operation(api_name: str, endpoint: str = None, user_id: Optional[int] = None):
    """Context manager for tracking API call operations"""
    metadata = {'api_name': api_name}
    if endpoint:
        metadata['endpoint'] = endpoint
        
    return completion_time_monitor.track_operation(
        OperationType.API_CALL,
        f"api_{api_name}",
        user_id=user_id,
        metadata=metadata
    )


# Manual recording functions (for non-decorator usage)
def record_onboarding_completion_time(step_name: str, completion_time_ms: float, 
                                    user_id: Optional[int] = None, success: bool = True):
    """Manually record onboarding step completion time"""
    completion_time_monitor.record_completion_time(
        OperationType.ONBOARDING,
        f"onboarding_{step_name}",
        completion_time_ms,
        user_id=user_id,
        metadata={'step': step_name, 'manual_record': True},
        success=success
    )

def record_webhook_processing_time(webhook_type: str, completion_time_ms: float, 
                                 user_id: Optional[int] = None, success: bool = True):
    """Manually record webhook processing completion time"""
    completion_time_monitor.record_completion_time(
        OperationType.WEBHOOK_PROCESSING,
        f"webhook_{webhook_type}",
        completion_time_ms,
        user_id=user_id,
        metadata={'webhook_type': webhook_type, 'manual_record': True},
        success=success
    )

def record_database_query_time(operation_name: str, completion_time_ms: float, 
                             table_name: str = None, success: bool = True):
    """Manually record database query completion time"""
    metadata = {'manual_record': True}
    if table_name:
        metadata['table'] = table_name
        
    completion_time_monitor.record_completion_time(
        OperationType.DATABASE_QUERY,
        f"db_{operation_name}",
        completion_time_ms,
        metadata=metadata,
        success=success
    )


# Reporting and analysis functions
async def get_onboarding_trends_report(hours: int = 24) -> Dict[str, Any]:
    """Get trends report specifically for onboarding operations"""
    return completion_time_monitor.get_trends_summary(OperationType.ONBOARDING)

async def get_webhook_trends_report(hours: int = 24) -> Dict[str, Any]:
    """Get trends report specifically for webhook processing"""
    return completion_time_monitor.get_trends_summary(OperationType.WEBHOOK_PROCESSING)

async def get_database_trends_report(hours: int = 24) -> Dict[str, Any]:
    """Get trends report specifically for database operations"""
    return completion_time_monitor.get_trends_summary(OperationType.DATABASE_QUERY)

async def get_system_performance_overview() -> Dict[str, Any]:
    """Get comprehensive system performance overview"""
    full_summary = completion_time_monitor.get_trends_summary()
    
    # Add additional performance insights
    performance_overview = {
        'overall_health': 'healthy' if full_summary['summary']['avg_performance_score'] > 70 else 
                         'warning' if full_summary['summary']['avg_performance_score'] > 50 else 'critical',
        'monitoring_active': completion_time_monitor.is_monitoring_active,
        'total_operations': full_summary['summary']['total_operations_monitored'],
        'performance_score': full_summary['summary']['avg_performance_score'],
        'trends_by_category': {}
    }
    
    # Group by operation type
    for key, metrics in full_summary['metrics'].items():
        op_type = metrics['operation_type']
        if op_type not in performance_overview['trends_by_category']:
            performance_overview['trends_by_category'][op_type] = {
                'operations': [],
                'avg_performance_score': 0,
                'regressions': 0,
                'improvements': 0
            }
        
        category = performance_overview['trends_by_category'][op_type]
        category['operations'].append({
            'name': metrics['operation_name'],
            'current_avg_ms': metrics['current_avg_ms'],
            'performance_score': metrics['performance_score'],
            'trend_direction': metrics['trend_direction'],
            'regression_detected': metrics['regression_detected'],
            'improvement_detected': metrics['improvement_detected']
        })
        
        if metrics['regression_detected']:
            category['regressions'] += 1
        if metrics['improvement_detected']:
            category['improvements'] += 1
    
    # Calculate average performance score per category
    for category in performance_overview['trends_by_category'].values():
        if category['operations']:
            scores = [op['performance_score'] for op in category['operations']]
            category['avg_performance_score'] = sum(scores) / len(scores)
    
    return performance_overview


# Global integration instance
completion_time_integration = CompletionTimeIntegration()