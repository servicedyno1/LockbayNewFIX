"""
Background Job Trace Integration
Provides comprehensive trace correlation for all background jobs and scheduled tasks
with operation context maintenance across async workflows
"""

import asyncio
import json
from functools import wraps
from typing import Dict, Any, Optional, Callable, List
from datetime import datetime
import traceback

from utils.trace_correlation import (
    trace_manager, OperationType, TraceStatus, TraceContext
)
from utils.trace_logging_integration import (
    get_trace_logger, MonitoringIntegration
)

logger = get_trace_logger(__name__)

class BackgroundJobType:
    """Types of background jobs for detailed categorization"""
    WORKFLOW_PROCESSOR = "workflow_processor"
    RETRY_ENGINE = "retry_engine"
    RECONCILIATION = "reconciliation"
    BALANCE_MONITOR = "balance_monitor"
    AUTO_WITHDRAWAL = "auto_withdrawal"
    CLEANUP_EXPIRY = "cleanup_expiry"
    NOTIFICATION_PROCESSOR = "notification_processor"
    MONITORING_JOB = "monitoring_job"
    HEALTH_CHECK = "health_check"
    MAINTENANCE = "maintenance"
    DATA_SYNC = "data_sync"
    WEBHOOK_PROCESSOR = "webhook_processor"

def background_job_traced(
    job_type: str,
    job_name: Optional[str] = None,
    capture_job_data: bool = True,
    batch_operation: bool = False,
    expected_duration_seconds: Optional[int] = None,
    critical_job: bool = False
):
    """
    Decorator for background jobs to add automatic trace correlation
    
    Args:
        job_type: Type of background job from BackgroundJobType
        job_name: Custom job name (defaults to function name)
        capture_job_data: Whether to capture job execution data
        batch_operation: Whether this is a batch processing job
        expected_duration_seconds: Expected job duration for performance monitoring
        critical_job: Whether this is a critical job that should alert on failure
    """
    
    def decorator(func: Callable) -> Callable:
        actual_job_name = job_name or func.__name__
        
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            # Create background job trace context
            job_context = BackgroundJobTraceExtractor.extract_job_context(
                args, kwargs, job_type, actual_job_name
            )
            
            # Create trace context for background job
            trace_context = trace_manager.create_trace_context(
                operation_type=OperationType.BACKGROUND_JOB,
                operation_name=actual_job_name,
                correlation_data={
                    'job_type': job_type,
                    'batch_operation': batch_operation,
                    'critical_job': critical_job,
                    'expected_duration_seconds': expected_duration_seconds,
                    **job_context
                }
            )
            
            if not trace_context:
                logger.warning(f"Failed to create trace context for background job: {actual_job_name}")
                return await func(*args, **kwargs)
            
            # Set trace context
            trace_manager.set_trace_context(trace_context)
            
            # Start main job span
            span = trace_manager.start_span(f"background_job_{actual_job_name}", "background_job")
            
            try:
                # Add job-specific tags
                if span:
                    span.add_tag('job_type', job_type)
                    span.add_tag('batch_operation', batch_operation)
                    span.add_tag('critical_job', critical_job)
                    span.add_tag('function_name', func.__name__)
                    
                    if expected_duration_seconds:
                        span.add_tag('expected_duration_seconds', expected_duration_seconds)
                    
                    if capture_job_data and job_context:
                        span.add_tag('job_context', json.dumps(job_context, default=str)[:1000])
                        
                # Log job start
                logger.info(
                    f"⚙️ Background Job Started: {actual_job_name}",
                    job_details={
                        'job_type': job_type,
                        'batch_operation': batch_operation,
                        'critical_job': critical_job,
                        'expected_duration': expected_duration_seconds
                    }
                )
                
                # Execute the background job
                start_time = datetime.utcnow()
                result = await func(*args, **kwargs)
                execution_time = (datetime.utcnow() - start_time).total_seconds()
                execution_time_ms = execution_time * 1000
                
                # Process job result
                job_result = BackgroundJobTraceExtractor.extract_job_result(result)
                
                # Check performance against expected duration
                performance_status = "normal"
                if expected_duration_seconds:
                    if execution_time > expected_duration_seconds * 1.5:
                        performance_status = "slow"
                    elif execution_time > expected_duration_seconds * 2:
                        performance_status = "very_slow"
                
                # Log successful completion
                logger.info(
                    f"✅ Background Job Completed: {actual_job_name}",
                    performance_metrics={
                        'execution_time_seconds': execution_time,
                        'execution_time_ms': execution_time_ms,
                        'performance_status': performance_status,
                        'job_result': job_result
                    }
                )
                
                # Complete span and trace
                if span:
                    span.add_tag('execution_time_seconds', execution_time)
                    span.add_tag('execution_time_ms', execution_time_ms)
                    span.add_tag('performance_status', performance_status)
                    
                    if job_result:
                        span.add_tag('result_summary', json.dumps(job_result, default=str)[:500])
                        
                    trace_manager.finish_span(span, TraceStatus.COMPLETED)
                    
                trace_manager.complete_trace(
                    trace_context.trace_id, 
                    TraceStatus.COMPLETED,
                    performance_metrics={
                        'execution_time_seconds': execution_time,
                        'execution_time_ms': execution_time_ms,
                        'performance_status': performance_status,
                        'background_job': True
                    }
                )
                
                # Integrate with monitoring systems
                MonitoringIntegration.correlate_performance_metrics({
                    'job': actual_job_name,
                    'job_type': job_type,
                    'execution_time_seconds': execution_time,
                    'execution_time_ms': execution_time_ms,
                    'performance_status': performance_status,
                    'background_job': True
                })
                
                # Alert if critical job took too long
                if critical_job and expected_duration_seconds and execution_time > expected_duration_seconds * 1.5:
                    logger.warning(
                        f"⚠️ CRITICAL_JOB_SLOW: {actual_job_name} took {execution_time:.1f}s "
                        f"(expected: {expected_duration_seconds}s)"
                    )
                
                return result
                
            except Exception as e:
                # Handle background job errors with full trace correlation
                execution_time = (datetime.utcnow() - trace_context.start_time).total_seconds()
                
                error_info = {
                    'error_type': type(e).__name__,
                    'error_message': str(e),
                    'error_traceback': traceback.format_exc(),
                    'job_type': job_type,
                    'job_name': actual_job_name,
                    'critical_job': critical_job,
                    'function_name': func.__name__
                }
                
                # Log error with job context
                logger.error(
                    f"❌ Background Job Failed: {actual_job_name}",
                    error_details=error_info,
                    performance_metrics={'execution_time_seconds': execution_time}
                )
                
                # Update span and trace with error
                if span:
                    span.set_error(e, error_info)
                    trace_manager.finish_span(span, TraceStatus.FAILED)
                    
                trace_manager.complete_trace(
                    trace_context.trace_id, 
                    TraceStatus.FAILED, 
                    error_info,
                    performance_metrics={'execution_time_seconds': execution_time}
                )
                
                # Critical job failures should be escalated
                if critical_job:
                    logger.critical(
                        f"🚨 CRITICAL_JOB_FAILED: {actual_job_name} failed with {type(e).__name__}: {str(e)}"
                    )
                
                # Re-raise the exception to maintain original behavior
                raise
                
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            # Similar implementation for sync background jobs (rare but possible)
            job_context = BackgroundJobTraceExtractor.extract_job_context(
                args, kwargs, job_type, actual_job_name
            )
            
            trace_context = trace_manager.create_trace_context(
                operation_type=OperationType.BACKGROUND_JOB,
                operation_name=actual_job_name,
                correlation_data={
                    'job_type': job_type,
                    'batch_operation': batch_operation,
                    'critical_job': critical_job,
                    **job_context
                }
            )
            
            if not trace_context:
                return func(*args, **kwargs)
                
            trace_manager.set_trace_context(trace_context)
            span = trace_manager.start_span(f"background_job_{actual_job_name}", "background_job")
            
            try:
                if span:
                    span.add_tag('job_type', job_type)
                    span.add_tag('critical_job', critical_job)
                    
                start_time = datetime.utcnow()
                result = func(*args, **kwargs)
                execution_time = (datetime.utcnow() - start_time).total_seconds()
                
                if span:
                    span.add_tag('execution_time_seconds', execution_time)
                    trace_manager.finish_span(span, TraceStatus.COMPLETED)
                    
                trace_manager.complete_trace(
                    trace_context.trace_id, 
                    TraceStatus.COMPLETED,
                    performance_metrics={'execution_time_seconds': execution_time}
                )
                
                return result
                
            except Exception as e:
                error_info = {
                    'error_type': type(e).__name__,
                    'error_message': str(e),
                    'job_type': job_type,
                    'critical_job': critical_job
                }
                
                if span:
                    span.set_error(e, error_info)
                    trace_manager.finish_span(span, TraceStatus.FAILED)
                    
                trace_manager.complete_trace(trace_context.trace_id, TraceStatus.FAILED, error_info)
                raise
                
        # Return appropriate wrapper
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
            
    return decorator

class BackgroundJobTraceExtractor:
    """Extract background job context for tracing"""
    
    @staticmethod
    def extract_job_context(args: tuple, kwargs: dict, job_type: str, job_name: str) -> Dict[str, Any]:
        """Extract background job context from parameters"""
        context = {
            'job_execution_time': datetime.utcnow().isoformat(),
            'job_type': job_type,
            'job_name': job_name
        }
        
        # Extract common job parameters
        if 'batch_size' in kwargs:
            context['batch_size'] = kwargs['batch_size']
            
        if 'limit' in kwargs:
            context['processing_limit'] = kwargs['limit']
            
        if 'timeout' in kwargs:
            context['timeout_seconds'] = kwargs['timeout']
            
        # Extract from first argument if it's an application or context object
        if len(args) > 0:
            first_arg = args[0]
            if hasattr(first_arg, '__class__'):
                context['executor_class'] = first_arg.__class__.__name__
                
        return context
    
    @staticmethod
    def extract_job_result(result: Any) -> Dict[str, Any]:
        """Extract context from job execution result"""
        if result is None:
            return {'result_type': 'None'}
            
        context = {'result_type': type(result).__name__}
        
        if isinstance(result, dict):
            # Look for common result fields
            for field in ['processed', 'successful', 'failed', 'errors', 'total', 'status']:
                if field in result:
                    context[field] = result[field]
                    
            # Extract performance metrics if available
            if 'execution_time_ms' in result:
                context['reported_execution_time_ms'] = result['execution_time_ms']
                
        elif isinstance(result, (list, tuple)):
            context['result_count'] = len(result)
            
        elif isinstance(result, (int, float)):
            context['result_value'] = result
            
        return context

class ScheduledJobTraceManager:
    """Manage trace correlation for scheduled jobs"""
    
    @staticmethod
    def create_scheduled_job_context(job_name: str, job_type: str, schedule_info: Dict[str, Any]) -> Optional[TraceContext]:
        """Create trace context for scheduled jobs"""
        return trace_manager.create_trace_context(
            operation_type=OperationType.BACKGROUND_JOB,
            operation_name=f"scheduled_{job_name}",
            correlation_data={
                'scheduled_job': True,
                'job_type': job_type,
                'schedule_info': schedule_info,
                'scheduler_trigger': schedule_info.get('trigger', 'unknown')
            }
        )
    
    @staticmethod
    def correlate_job_dependencies(dependent_jobs: List[str], dependency_results: Dict[str, Any]):
        """Correlate dependent job execution"""
        current_context = trace_manager.get_current_trace_context()
        if current_context:
            current_context.correlation_data.update({
                'job_dependencies': dependent_jobs,
                'dependency_results': dependency_results,
                'dependent_execution': True
            })

class BatchJobTraceManager:
    """Manage trace correlation for batch processing jobs"""
    
    @staticmethod
    def create_batch_context(batch_name: str, batch_size: int, total_items: Optional[int] = None) -> Optional[TraceContext]:
        """Create trace context for batch processing"""
        correlation_data = {
            'batch_processing': True,
            'batch_size': batch_size,
            'batch_name': batch_name
        }
        
        if total_items is not None:
            correlation_data['total_items'] = total_items
            correlation_data['estimated_batches'] = (total_items + batch_size - 1) // batch_size
            
        return trace_manager.create_trace_context(
            operation_type=OperationType.BACKGROUND_JOB,
            operation_name=f"batch_{batch_name}",
            correlation_data=correlation_data
        )
    
    @staticmethod
    def track_batch_progress(processed: int, successful: int, failed: int):
        """Track batch processing progress"""
        current_context = trace_manager.get_current_trace_context()
        if current_context:
            current_context.correlation_data.update({
                'batch_progress': {
                    'processed': processed,
                    'successful': successful,
                    'failed': failed,
                    'success_rate': successful / processed if processed > 0 else 0,
                    'last_updated': datetime.utcnow().isoformat()
                }
            })
            
            # Log progress at intervals
            if processed > 0 and processed % 100 == 0:  # Every 100 items
                logger.info(
                    "📊 Batch Progress Update",
                    batch_metrics={
                        'processed': processed,
                        'successful': successful,
                        'failed': failed,
                        'success_rate': f"{(successful / processed * 100):.1f}%"
                    }
                )

class WorkflowTraceManager:
    """Manage trace correlation for workflow operations"""
    
    @staticmethod
    def create_workflow_context(workflow_name: str, workflow_step: str, workflow_data: Optional[Dict[str, Any]] = None) -> Optional[TraceContext]:
        """Create trace context for workflow operations"""
        return trace_manager.create_trace_context(
            operation_type=OperationType.BACKGROUND_JOB,
            operation_name=f"workflow_{workflow_name}_{workflow_step}",
            correlation_data={
                'workflow_execution': True,
                'workflow_name': workflow_name,
                'workflow_step': workflow_step,
                'workflow_data': workflow_data or {}
            }
        )
    
    @staticmethod
    def correlate_workflow_state_transition(old_state: str, new_state: str, transition_data: Optional[Dict[str, Any]] = None):
        """Correlate workflow state transitions"""
        current_context = trace_manager.get_current_trace_context()
        if current_context:
            current_context.correlation_data.update({
                'workflow_state_transition': True,
                'old_state': old_state,
                'new_state': new_state,
                'transition_data': transition_data or {},
                'transition_timestamp': datetime.utcnow().isoformat()
            })

# Utility functions for common background job operations
def correlate_job_scheduler_execution(scheduler_name: str, job_count: int, execution_metrics: Dict[str, Any]):
    """Add job scheduler execution correlation to current trace"""
    current_context = trace_manager.get_current_trace_context()
    if current_context:
        current_context.correlation_data.update({
            'job_scheduler_execution': True,
            'scheduler_name': scheduler_name,
            'job_count': job_count,
            'execution_metrics': execution_metrics
        })

def correlate_resource_utilization(cpu_percent: float, memory_mb: float, active_jobs: int):
    """Add resource utilization correlation to current trace"""
    current_context = trace_manager.get_current_trace_context()
    if current_context:
        current_context.correlation_data.update({
            'resource_utilization': {
                'cpu_percent': cpu_percent,
                'memory_mb': memory_mb,
                'active_jobs': active_jobs,
                'timestamp': datetime.utcnow().isoformat()
            }
        })

def correlate_job_queue_metrics(queue_size: int, processing_rate: float, average_wait_time: float):
    """Add job queue metrics correlation to current trace"""
    current_context = trace_manager.get_current_trace_context()
    if current_context:
        current_context.correlation_data.update({
            'job_queue_metrics': {
                'queue_size': queue_size,
                'processing_rate': processing_rate,
                'average_wait_time': average_wait_time,
                'timestamp': datetime.utcnow().isoformat()
            }
        })

def setup_background_job_trace_integration():
    """Setup background job trace integration with existing systems"""
    logger.info("⚙️ Setting up background job trace integration...")
    
    # This function can be called during application initialization
    # to ensure background job trace correlation is properly configured
    
    logger.info("✅ Background job trace integration configured")

# Integration with APScheduler for automatic trace correlation
class TracedAPSchedulerIntegration:
    """Integration with APScheduler to automatically add trace correlation"""
    
    @staticmethod
    def wrap_scheduled_function(job_func: Callable, job_id: str, job_type: str) -> Callable:
        """Wrap a scheduled function with trace correlation"""
        
        @background_job_traced(
            job_type=job_type,
            job_name=job_id,
            critical_job=job_type in [BackgroundJobType.RECONCILIATION, BackgroundJobType.BALANCE_MONITOR]
        )
        async def traced_job_wrapper(*args, **kwargs):
            # Add scheduler-specific correlation
            correlate_job_scheduler_execution('apscheduler', 1, {'job_id': job_id})
            
            return await job_func(*args, **kwargs)
            
        return traced_job_wrapper

logger.info("⚙️ Background job trace integration module initialized")