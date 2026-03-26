"""
Scheduler and Background Job Lifecycle Audit Logging
Provides comprehensive audit logging for all scheduled jobs with trace correlation
"""

import logging
import time
import uuid
import asyncio
from typing import Dict, Any, Optional, Callable
from functools import wraps
from datetime import datetime

from utils.comprehensive_audit_logger import (
    ComprehensiveAuditLogger,
    AuditLevel,
    TraceContext,
    RelatedIDs,
    audit_user_interaction
)

logger = logging.getLogger(__name__)


class SchedulerAuditLogger:
    """
    Comprehensive audit logger for scheduled jobs and background tasks
    """
    
    def __init__(self):
        self.audit_logger = ComprehensiveAuditLogger()
        self.active_jobs = {}  # Track active job executions
    
    async def log_job_start(
        self,
        job_name: str,
        job_id: str,
        job_type: str = "scheduled",
        related_ids: Optional[RelatedIDs] = None,
        job_metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Log job execution start
        
        Args:
            job_name: Human-readable job name
            job_id: Unique job identifier
            job_type: Type of job (scheduled, background, manual)
            related_ids: Related entity IDs
            job_metadata: Additional job metadata
            
        Returns:
            Trace ID for correlation
        """
        
        # Generate trace ID for this job execution
        trace_id = str(uuid.uuid4())
        TraceContext.set_trace_id(trace_id)
        
        # Store job execution context
        job_context = {
            'job_name': job_name,
            'job_id': job_id,
            'job_type': job_type,
            'start_time': time.time(),
            'trace_id': trace_id,
            'related_ids': related_ids,
            'metadata': job_metadata or {}
        }
        self.active_jobs[trace_id] = job_context
        
        # Log job start
        audit_user_interaction(
            action=f"job_{job_type}_start",
            update=None,
            result="job_started",
            level=AuditLevel.INFO,
            user_id=None,  # Jobs are system-level
            is_admin=False,
            chat_id=None,
            message_id=None,
            latency_ms=0,
            job_name=job_name,
            job_id=job_id,
            job_type=job_type,
            trace_id=trace_id,
            timestamp=datetime.utcnow().isoformat(),
            related_ids=related_ids.to_dict() if related_ids else {},
            **(job_metadata or {})
        )
        
        logger.info(f"⚙️ JOB START: {job_name} ({job_type}) (trace: {trace_id[:8]})")
        
        return trace_id
    
    async def log_job_end(
        self,
        trace_id: str,
        success: bool,
        processing_time_ms: float,
        result_data: Optional[Dict[str, Any]] = None,
        error_details: Optional[str] = None,
        items_processed: Optional[int] = None
    ) -> None:
        """
        Log job execution completion
        
        Args:
            trace_id: Trace ID from start
            success: Whether job succeeded
            processing_time_ms: Processing time in milliseconds
            result_data: Job result data (PII-safe)
            error_details: Error details if failed
            items_processed: Number of items processed
        """
        
        # Set trace context
        TraceContext.set_trace_id(trace_id)
        
        # Get job context
        job_context = self.active_jobs.get(trace_id, {})
        
        # Determine result
        result = "job_completed" if success else "job_failed"
        level = AuditLevel.INFO if success else AuditLevel.ERROR
        
        # Log job completion
        audit_user_interaction(
            action=f"job_{job_context.get('job_type', 'unknown')}_end",
            update=None,
            result=result,
            level=level,
            user_id=None,
            is_admin=False,
            chat_id=None,
            message_id=None,
            latency_ms=processing_time_ms,
            job_name=job_context.get('job_name', 'unknown'),
            job_id=job_context.get('job_id', 'unknown'),
            job_type=job_context.get('job_type', 'unknown'),
            success=success,
            items_processed=items_processed,
            error_details=error_details,
            trace_id=trace_id,
            timestamp=datetime.utcnow().isoformat(),
            related_ids=job_context.get('related_ids', {}).to_dict() if job_context.get('related_ids') else {},
            **(result_data or {})
        )
        
        # Clean up active job context
        if trace_id in self.active_jobs:
            del self.active_jobs[trace_id]
        
        status_emoji = "✅" if success else "❌"
        job_name = job_context.get('job_name', 'unknown')
        logger.info(f"{status_emoji} JOB END: {job_name} "
                   f"in {processing_time_ms:.1f}ms (trace: {trace_id[:8]})")
    
    async def log_job_progress(
        self,
        trace_id: str,
        progress_data: Dict[str, Any],
        items_processed: Optional[int] = None,
        items_total: Optional[int] = None
    ) -> None:
        """
        Log job progress update
        
        Args:
            trace_id: Trace ID from start
            progress_data: Progress information
            items_processed: Number of items processed so far
            items_total: Total number of items to process
        """
        
        # Set trace context
        TraceContext.set_trace_id(trace_id)
        
        # Get job context
        job_context = self.active_jobs.get(trace_id, {})
        
        # Calculate progress percentage if possible
        progress_percent = None
        if items_processed is not None and items_total is not None and items_total > 0:
            progress_percent = (items_processed / items_total) * 100
        
        # Log progress
        audit_user_interaction(
            action=f"job_{job_context.get('job_type', 'unknown')}_progress",
            update=None,
            result="job_progress",
            level=AuditLevel.DEBUG,
            user_id=None,
            is_admin=False,
            chat_id=None,
            message_id=None,
            latency_ms=0,
            job_name=job_context.get('job_name', 'unknown'),
            job_id=job_context.get('job_id', 'unknown'),
            items_processed=items_processed,
            items_total=items_total,
            progress_percent=progress_percent,
            trace_id=trace_id,
            timestamp=datetime.utcnow().isoformat(),
            **progress_data
        )
        
        logger.debug(f"📊 JOB PROGRESS: {job_context.get('job_name', 'unknown')} "
                    f"({items_processed}/{items_total}) (trace: {trace_id[:8]})")


# Global instance
scheduler_audit_logger = SchedulerAuditLogger()


def audit_scheduled_job(
    job_name: str,
    job_type: str = "scheduled",
    track_progress: bool = False
):
    """
    Decorator for comprehensive scheduled job audit logging
    
    Args:
        job_name: Human-readable job name
        job_type: Type of job (scheduled, background, manual)
        track_progress: Whether to track job progress
    """
    def decorator(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            start_time = time.time()
            trace_id = None
            success = False
            error_details = None
            result_data = None
            items_processed = None
            
            try:
                # Generate job ID from function and args
                job_id = f"{func.__name__}_{int(time.time())}"
                
                # Log job start
                trace_id = await scheduler_audit_logger.log_job_start(
                    job_name=job_name,
                    job_id=job_id,
                    job_type=job_type
                )
                
                # Execute job function
                if asyncio.iscoroutinefunction(func):
                    result = await func(*args, **kwargs)
                else:
                    result = func(*args, **kwargs)
                
                success = True
                
                # Extract result data if available
                if isinstance(result, dict):
                    result_data = result
                    items_processed = result.get('items_processed')
                elif isinstance(result, (int, float)):
                    items_processed = int(result)
                    result_data = {'items_processed': items_processed}
                
                return result
                
            except Exception as e:
                success = False
                error_details = str(e)
                logger.error(f"❌ Job {job_name} failed: {e}", exc_info=True)
                raise
                
            finally:
                if trace_id:
                    # Log job completion
                    processing_time = (time.time() - start_time) * 1000
                    await scheduler_audit_logger.log_job_end(
                        trace_id=trace_id,
                        success=success,
                        processing_time_ms=processing_time,
                        result_data=result_data,
                        error_details=error_details,
                        items_processed=items_processed
                    )
        
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            # For synchronous functions, run the async wrapper in an event loop
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # If loop is already running, create a task
                    task = asyncio.create_task(async_wrapper(*args, **kwargs))
                    return task
                else:
                    return loop.run_until_complete(async_wrapper(*args, **kwargs))
            except RuntimeError:
                # No event loop available, create a new one
                return asyncio.run(async_wrapper(*args, **kwargs))
        
        # Return appropriate wrapper based on function type
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
    
    return decorator


# Convenience decorators for different job types
def audit_background_job(job_name: str):
    """Decorator for background job audit logging"""
    return audit_scheduled_job(job_name, job_type="background")


def audit_monitoring_job(job_name: str):
    """Decorator for monitoring job audit logging"""
    return audit_scheduled_job(job_name, job_type="monitoring")


def audit_cleanup_job(job_name: str):
    """Decorator for cleanup job audit logging"""
    return audit_scheduled_job(job_name, job_type="cleanup")


def audit_financial_job(job_name: str):
    """Decorator for financial job audit logging"""
    return audit_scheduled_job(job_name, job_type="financial")


async def log_job_execution(
    job_name: str,
    job_function: Callable,
    job_type: str = "manual",
    job_args: tuple = (),
    job_kwargs: dict = None
) -> Any:
    """
    Convenience function for manual job execution with logging
    
    Args:
        job_name: Human-readable job name
        job_function: Function to execute
        job_type: Type of job
        job_args: Positional arguments for job function
        job_kwargs: Keyword arguments for job function
        
    Returns:
        Job function result
    """
    
    start_time = time.time()
    job_kwargs = job_kwargs or {}
    
    # Generate job ID
    job_id = f"{job_function.__name__}_{int(time.time())}"
    
    # Log job start
    trace_id = await scheduler_audit_logger.log_job_start(
        job_name=job_name,
        job_id=job_id,
        job_type=job_type
    )
    
    try:
        # Execute job function
        if asyncio.iscoroutinefunction(job_function):
            result = await job_function(*job_args, **job_kwargs)
        else:
            result = job_function(*job_args, **job_kwargs)
        
        # Log success
        processing_time = (time.time() - start_time) * 1000
        await scheduler_audit_logger.log_job_end(
            trace_id=trace_id,
            success=True,
            processing_time_ms=processing_time,
            result_data={'result': str(result) if result else None}
        )
        
        return result
        
    except Exception as e:
        # Log failure
        processing_time = (time.time() - start_time) * 1000
        await scheduler_audit_logger.log_job_end(
            trace_id=trace_id,
            success=False,
            processing_time_ms=processing_time,
            error_details=str(e)
        )
        raise


async def log_job_batch_progress(
    trace_id: str,
    batch_name: str,
    items_processed: int,
    items_total: int,
    batch_results: Dict[str, Any] = None
) -> None:
    """
    Log progress for batch processing jobs
    
    Args:
        trace_id: Job trace ID
        batch_name: Name of the batch being processed
        items_processed: Number of items processed
        items_total: Total number of items
        batch_results: Results of the batch processing
    """
    
    progress_data = {
        'batch_name': batch_name,
        'completion_rate': (items_processed / items_total) * 100 if items_total > 0 else 0
    }
    
    if batch_results:
        progress_data.update(batch_results)
    
    await scheduler_audit_logger.log_job_progress(
        trace_id=trace_id,
        progress_data=progress_data,
        items_processed=items_processed,
        items_total=items_total
    )