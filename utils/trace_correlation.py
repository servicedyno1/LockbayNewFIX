"""
Unified Trace Correlation System
Provides comprehensive request tracking and debugging capabilities across all LockBay systems

Features:
- Unique trace ID generation and propagation
- Cross-system correlation and context management
- Async operation context preservation
- Performance tracking and debugging correlation
- Integration with existing monitoring systems
"""

import logging
import asyncio
import time
import uuid
from contextvars import ContextVar
from typing import Dict, Any, Optional, List, Callable
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from enum import Enum
from functools import wraps
import traceback
import threading

logger = logging.getLogger(__name__)

class OperationType(Enum):
    """Types of operations for trace correlation"""
    TELEGRAM_MESSAGE = "telegram_message"
    TELEGRAM_CALLBACK = "telegram_callback" 
    WEBHOOK_REQUEST = "webhook_request"
    BACKGROUND_JOB = "background_job"
    FINANCIAL_OPERATION = "financial_operation"
    DATABASE_OPERATION = "database_operation"
    EXTERNAL_API_CALL = "external_api_call"
    EMAIL_OPERATION = "email_operation"
    SMS_OPERATION = "sms_operation"
    ADMIN_OPERATION = "admin_operation"
    SYSTEM_OPERATION = "system_operation"

class TraceStatus(Enum):
    """Status of traced operations"""
    STARTED = "started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"

@dataclass
class TraceContext:
    """Trace context containing all correlation information"""
    trace_id: str
    parent_trace_id: Optional[str]
    operation_type: OperationType
    operation_name: str
    user_id: Optional[int]
    admin_user_id: Optional[int]
    session_id: Optional[str]
    request_id: Optional[str]
    correlation_data: Dict[str, Any]
    start_time: datetime
    end_time: Optional[datetime] = None
    status: TraceStatus = TraceStatus.STARTED
    error_info: Optional[Dict[str, Any]] = None
    performance_metrics: Dict[str, Any] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for logging and storage"""
        result = asdict(self)
        result['operation_type'] = self.operation_type.value
        result['status'] = self.status.value
        result['start_time'] = self.start_time.isoformat()
        if self.end_time:
            result['end_time'] = self.end_time.isoformat()
            result['duration_ms'] = int((self.end_time - self.start_time).total_seconds() * 1000)
        return result

class TraceSpan:
    """Represents a span within a trace for detailed operation tracking"""
    
    def __init__(self, trace_context: TraceContext, span_name: str, span_type: str = "operation"):
        self.trace_context = trace_context
        self.span_id = str(uuid.uuid4())[:8]
        self.span_name = span_name
        self.span_type = span_type
        self.start_time = datetime.utcnow()
        self.end_time = None
        self.status = TraceStatus.STARTED
        self.tags = {}
        self.logs = []
        self.error_info = None
        
    def add_tag(self, key: str, value: Any):
        """Add a tag to the span"""
        self.tags[key] = value
        
    def add_log(self, message: str, level: str = "INFO", data: Optional[Dict[str, Any]] = None):
        """Add a log entry to the span"""
        self.logs.append({
            'timestamp': datetime.utcnow().isoformat(),
            'level': level,
            'message': message,
            'data': data or {}
        })
        
    def set_error(self, error: Exception, error_data: Optional[Dict[str, Any]] = None):
        """Set error information for the span"""
        self.status = TraceStatus.FAILED
        self.error_info = {
            'error_type': type(error).__name__,
            'error_message': str(error),
            'error_traceback': traceback.format_exc(),
            'error_data': error_data or {}
        }
        
    def finish(self, status: TraceStatus = TraceStatus.COMPLETED):
        """Finish the span"""
        self.end_time = datetime.utcnow()
        if self.status != TraceStatus.FAILED:
            self.status = status
            
    def to_dict(self) -> Dict[str, Any]:
        """Convert span to dictionary"""
        result = {
            'span_id': self.span_id,
            'trace_id': self.trace_context.trace_id,
            'span_name': self.span_name,
            'span_type': self.span_type,
            'start_time': self.start_time.isoformat(),
            'status': self.status.value,
            'tags': self.tags,
            'logs': self.logs
        }
        
        if self.end_time:
            result['end_time'] = self.end_time.isoformat()
            result['duration_ms'] = int((self.end_time - self.start_time).total_seconds() * 1000)
            
        if self.error_info:
            result['error_info'] = self.error_info
            
        return result

# Context variables for trace propagation
_trace_context: ContextVar[Optional[TraceContext]] = ContextVar('trace_context', default=None)
_current_spans: ContextVar[List[TraceSpan]] = ContextVar('current_spans', default=[])

class TraceCorrelationManager:
    """Central manager for trace correlation across all systems"""
    
    def __init__(self):
        self.active_traces: Dict[str, TraceContext] = {}
        self.completed_traces: List[TraceContext] = []
        self.trace_storage_limit = 10000  # Keep last 10k completed traces
        self.correlation_patterns = {}
        self.performance_thresholds = {
            'telegram_message': 2000,  # 2s for message handling
            'webhook_request': 1000,   # 1s for webhook processing
            'database_operation': 500, # 500ms for DB operations
            'external_api_call': 5000, # 5s for external APIs
            'background_job': 30000,   # 30s for background jobs
        }
        
        # Thread-safe operations
        self._lock = threading.Lock()
        
        logger.info("🔗 Trace correlation manager initialized")

    def generate_trace_id(self, prefix: str = "trace") -> str:
        """Generate a unique trace ID"""
        timestamp = int(time.time() * 1000)
        unique_id = str(uuid.uuid4())[:8]
        return f"{prefix}_{timestamp}_{unique_id}"

    def create_trace_context(
        self,
        operation_type: OperationType,
        operation_name: str,
        user_id: Optional[int] = None,
        admin_user_id: Optional[int] = None,
        session_id: Optional[str] = None,
        request_id: Optional[str] = None,
        parent_trace_id: Optional[str] = None,
        correlation_data: Optional[Dict[str, Any]] = None
    ) -> TraceContext:
        """Create a new trace context"""
        
        trace_id = self.generate_trace_id()
        
        context = TraceContext(
            trace_id=trace_id,
            parent_trace_id=parent_trace_id,
            operation_type=operation_type,
            operation_name=operation_name,
            user_id=user_id,
            admin_user_id=admin_user_id,
            session_id=session_id,
            request_id=request_id,
            correlation_data=correlation_data or {},
            start_time=datetime.utcnow(),
            performance_metrics={}
        )
        
        with self._lock:
            self.active_traces[trace_id] = context
            
        logger.debug(f"🔗 Created trace context: {trace_id} for {operation_type.value}:{operation_name}")
        return context

    def get_current_trace_context(self) -> Optional[TraceContext]:
        """Get the current trace context from context variables"""
        return _trace_context.get()

    def set_trace_context(self, context: TraceContext):
        """Set the current trace context"""
        _trace_context.set(context)

    def create_child_trace(
        self,
        operation_type: OperationType,
        operation_name: str,
        additional_correlation: Optional[Dict[str, Any]] = None
    ) -> Optional[TraceContext]:
        """Create a child trace from current context"""
        
        parent_context = self.get_current_trace_context()
        if not parent_context:
            logger.warning(f"No parent trace context for child trace: {operation_name}")
            return self.create_trace_context(operation_type, operation_name)
            
        # Merge correlation data
        merged_correlation = parent_context.correlation_data.copy()
        if additional_correlation:
            merged_correlation.update(additional_correlation)
            
        child_context = self.create_trace_context(
            operation_type=operation_type,
            operation_name=operation_name,
            user_id=parent_context.user_id,
            admin_user_id=parent_context.admin_user_id,
            session_id=parent_context.session_id,
            parent_trace_id=parent_context.trace_id,
            correlation_data=merged_correlation
        )
        
        return child_context

    def start_span(self, span_name: str, span_type: str = "operation") -> Optional[TraceSpan]:
        """Start a new span within the current trace"""
        
        current_context = self.get_current_trace_context()
        if not current_context:
            logger.warning(f"No trace context available for span: {span_name}")
            return None
            
        span = TraceSpan(current_context, span_name, span_type)
        
        # Add to current spans
        current_spans = _current_spans.get([])
        current_spans.append(span)
        _current_spans.set(current_spans)
        
        logger.debug(f"🔗 Started span: {span.span_id} - {span_name} in trace {current_context.trace_id}")
        return span

    def finish_span(self, span: TraceSpan, status: TraceStatus = TraceStatus.COMPLETED):
        """Finish a span"""
        span.finish(status)
        
        # Remove from current spans
        current_spans = _current_spans.get([])
        if span in current_spans:
            current_spans.remove(span)
            _current_spans.set(current_spans)
            
        # Log span completion
        logger.debug(f"🔗 Finished span: {span.span_id} - {span.span_name} ({span.status.value})")
        
        # Check performance thresholds
        if span.end_time and span.start_time:
            duration_ms = (span.end_time - span.start_time).total_seconds() * 1000
            threshold = self.performance_thresholds.get(span.span_type, 1000)
            
            if duration_ms > threshold:
                logger.warning(
                    f"🐌 SLOW_SPAN: {span.span_name} took {duration_ms:.0f}ms "
                    f"(threshold: {threshold}ms) in trace {span.trace_context.trace_id}"
                )

    def complete_trace(self, trace_id: str, status: TraceStatus = TraceStatus.COMPLETED, 
                      error_info: Optional[Dict[str, Any]] = None,
                      performance_metrics: Optional[Dict[str, Any]] = None):
        """Complete a trace and move it to completed traces"""
        
        with self._lock:
            if trace_id not in self.active_traces:
                logger.warning(f"Attempted to complete non-existent trace: {trace_id}")
                return
                
            context = self.active_traces[trace_id]
            context.end_time = datetime.utcnow()
            context.status = status
            
            if error_info:
                context.error_info = error_info
                
            if performance_metrics:
                context.performance_metrics.update(performance_metrics)
                
            # Move to completed traces
            self.completed_traces.append(context)
            del self.active_traces[trace_id]
            
            # Maintain storage limit
            if len(self.completed_traces) > self.trace_storage_limit:
                self.completed_traces = self.completed_traces[-self.trace_storage_limit:]
                
        duration_ms = int((context.end_time - context.start_time).total_seconds() * 1000)
        
        logger.info(
            f"🔗 Trace completed: {trace_id} - {context.operation_type.value}:{context.operation_name} "
            f"({status.value}) in {duration_ms}ms"
        )
        
        # Check performance thresholds
        threshold = self.performance_thresholds.get(context.operation_type.value, 1000)
        if duration_ms > threshold:
            logger.warning(
                f"🐌 SLOW_TRACE: {context.operation_name} took {duration_ms}ms "
                f"(threshold: {threshold}ms)"
            )

    def get_trace_by_id(self, trace_id: str) -> Optional[TraceContext]:
        """Get a trace by ID from active or completed traces"""
        
        with self._lock:
            # Check active traces first
            if trace_id in self.active_traces:
                return self.active_traces[trace_id]
                
            # Check completed traces
            for trace in self.completed_traces:
                if trace.trace_id == trace_id:
                    return trace
                    
        return None

    def find_related_traces(self, trace_id: str) -> List[TraceContext]:
        """Find all traces related to a given trace ID"""
        
        related_traces = []
        target_trace = self.get_trace_by_id(trace_id)
        
        if not target_trace:
            return related_traces
            
        with self._lock:
            # Find parent and child traces
            all_traces = list(self.active_traces.values()) + self.completed_traces
            
            for trace in all_traces:
                # Include the target trace itself
                if trace.trace_id == trace_id:
                    related_traces.append(trace)
                # Include child traces
                elif trace.parent_trace_id == trace_id:
                    related_traces.append(trace)
                # Include parent trace
                elif target_trace.parent_trace_id and trace.trace_id == target_trace.parent_trace_id:
                    related_traces.append(trace)
                # Include sibling traces (same parent)
                elif (target_trace.parent_trace_id and trace.parent_trace_id == target_trace.parent_trace_id 
                      and trace.trace_id != trace_id):
                    related_traces.append(trace)
                # Include traces with same user_id or correlation data
                elif self._traces_are_correlated(target_trace, trace):
                    related_traces.append(trace)
                    
        return sorted(related_traces, key=lambda t: t.start_time)

    def _traces_are_correlated(self, trace1: TraceContext, trace2: TraceContext) -> bool:
        """Check if two traces are correlated based on common attributes"""
        
        # Same user
        if trace1.user_id and trace1.user_id == trace2.user_id:
            # Within reasonable time window (1 hour)
            time_diff = abs((trace1.start_time - trace2.start_time).total_seconds())
            if time_diff <= 3600:
                return True
                
        # Same session
        if trace1.session_id and trace1.session_id == trace2.session_id:
            return True
            
        # Common correlation data keys
        if trace1.correlation_data and trace2.correlation_data:
            common_keys = set(trace1.correlation_data.keys()) & set(trace2.correlation_data.keys())
            for key in common_keys:
                if trace1.correlation_data[key] == trace2.correlation_data[key]:
                    return True
                    
        return False

    def get_performance_summary(self, hours: int = 1) -> Dict[str, Any]:
        """Get performance summary for the last N hours"""
        
        cutoff_time = datetime.utcnow() - timedelta(hours=hours)
        
        with self._lock:
            recent_traces = [
                trace for trace in self.completed_traces 
                if trace.start_time >= cutoff_time and trace.end_time
            ]
            
        if not recent_traces:
            return {'total_traces': 0, 'summary': 'No recent traces'}
            
        # Calculate statistics
        durations = []
        status_counts = {}
        operation_stats = {}
        
        for trace in recent_traces:
            duration_ms = (trace.end_time - trace.start_time).total_seconds() * 1000
            durations.append(duration_ms)
            
            # Status counts
            status = trace.status.value
            status_counts[status] = status_counts.get(status, 0) + 1
            
            # Operation type stats
            op_type = trace.operation_type.value
            if op_type not in operation_stats:
                operation_stats[op_type] = {'count': 0, 'total_duration': 0, 'errors': 0}
                
            operation_stats[op_type]['count'] += 1
            operation_stats[op_type]['total_duration'] += duration_ms
            
            if trace.status == TraceStatus.FAILED:
                operation_stats[op_type]['errors'] += 1
                
        # Calculate averages
        for op_type, stats in operation_stats.items():
            if stats['count'] > 0:
                stats['avg_duration_ms'] = stats['total_duration'] / stats['count']
                stats['error_rate'] = stats['errors'] / stats['count']
                
        durations.sort()
        total_traces = len(recent_traces)
        
        return {
            'total_traces': total_traces,
            'time_window_hours': hours,
            'status_breakdown': status_counts,
            'operation_stats': operation_stats,
            'duration_percentiles': {
                'p50': durations[int(total_traces * 0.5)] if total_traces > 0 else 0,
                'p90': durations[int(total_traces * 0.9)] if total_traces > 0 else 0,
                'p95': durations[int(total_traces * 0.95)] if total_traces > 0 else 0,
                'p99': durations[int(total_traces * 0.99)] if total_traces > 0 else 0,
            }
        }

# Global trace correlation manager instance
trace_manager = TraceCorrelationManager()

def traced_operation(
    operation_type: OperationType,
    operation_name: Optional[str] = None,
    capture_args: bool = False,
    capture_result: bool = False
):
    """Decorator for automatic trace correlation of operations"""
    
    def decorator(func: Callable) -> Callable:
        actual_operation_name = operation_name or func.__name__
        
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            # Create or get trace context
            current_context = trace_manager.get_current_trace_context()
            
            if current_context:
                # Create child trace for nested operations
                trace_context = trace_manager.create_child_trace(
                    operation_type, 
                    actual_operation_name,
                    {'parent_function': func.__name__}
                )
            else:
                # Create new root trace
                trace_context = trace_manager.create_trace_context(
                    operation_type, 
                    actual_operation_name
                )
                
            if not trace_context:
                # Fallback to original function if tracing fails
                logger.warning(f"Failed to create trace context for {actual_operation_name}")
                return await func(*args, **kwargs)
                
            # Set trace context for this operation
            trace_manager.set_trace_context(trace_context)
            
            # Start span
            span = trace_manager.start_span(actual_operation_name, operation_type.value)
            
            try:
                # Capture arguments if requested
                if capture_args and span:
                    span.add_tag('function_args', str(args)[:500])  # Limit size
                    span.add_tag('function_kwargs', str(kwargs)[:500])
                    
                result = await func(*args, **kwargs)
                
                # Capture result if requested
                if capture_result and span:
                    span.add_tag('function_result', str(result)[:500])  # Limit size
                    
                # Finish span and trace
                if span:
                    trace_manager.finish_span(span, TraceStatus.COMPLETED)
                    
                trace_manager.complete_trace(trace_context.trace_id, TraceStatus.COMPLETED)
                return result
                
            except Exception as e:
                # Handle errors
                error_info = {
                    'error_type': type(e).__name__,
                    'error_message': str(e),
                    'error_traceback': traceback.format_exc()
                }
                
                if span:
                    span.set_error(e)
                    trace_manager.finish_span(span, TraceStatus.FAILED)
                    
                trace_manager.complete_trace(
                    trace_context.trace_id, 
                    TraceStatus.FAILED, 
                    error_info
                )
                
                logger.error(
                    f"🔗 Operation failed in trace {trace_context.trace_id}: "
                    f"{actual_operation_name} - {str(e)}"
                )
                raise
                
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            # Similar implementation for sync functions
            current_context = trace_manager.get_current_trace_context()
            
            if current_context:
                trace_context = trace_manager.create_child_trace(
                    operation_type, 
                    actual_operation_name,
                    {'parent_function': func.__name__}
                )
            else:
                trace_context = trace_manager.create_trace_context(
                    operation_type, 
                    actual_operation_name
                )
                
            if not trace_context:
                logger.warning(f"Failed to create trace context for {actual_operation_name}")
                return func(*args, **kwargs)
                
            trace_manager.set_trace_context(trace_context)
            span = trace_manager.start_span(actual_operation_name, operation_type.value)
            
            try:
                if capture_args and span:
                    span.add_tag('function_args', str(args)[:500])
                    span.add_tag('function_kwargs', str(kwargs)[:500])
                    
                result = func(*args, **kwargs)
                
                if capture_result and span:
                    span.add_tag('function_result', str(result)[:500])
                    
                if span:
                    trace_manager.finish_span(span, TraceStatus.COMPLETED)
                    
                trace_manager.complete_trace(trace_context.trace_id, TraceStatus.COMPLETED)
                return result
                
            except Exception as e:
                error_info = {
                    'error_type': type(e).__name__,
                    'error_message': str(e),
                    'error_traceback': traceback.format_exc()
                }
                
                if span:
                    span.set_error(e)
                    trace_manager.finish_span(span, TraceStatus.FAILED)
                    
                trace_manager.complete_trace(
                    trace_context.trace_id, 
                    TraceStatus.FAILED, 
                    error_info
                )
                
                logger.error(
                    f"🔗 Operation failed in trace {trace_context.trace_id}: "
                    f"{actual_operation_name} - {str(e)}"
                )
                raise
                
        # Return appropriate wrapper based on function type
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
            
    return decorator

def with_trace_context(context: TraceContext):
    """Context manager for running operations with a specific trace context"""
    
    class TraceContextManager:
        def __init__(self, trace_context: TraceContext):
            self.trace_context = trace_context
            self.previous_context = None
            
        def __enter__(self):
            self.previous_context = trace_manager.get_current_trace_context()
            trace_manager.set_trace_context(self.trace_context)
            return self.trace_context
            
        def __exit__(self, exc_type, exc_val, exc_tb):
            trace_manager.set_trace_context(self.previous_context)
            
        async def __aenter__(self):
            self.previous_context = trace_manager.get_current_trace_context()
            trace_manager.set_trace_context(self.trace_context)
            return self.trace_context
            
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            trace_manager.set_trace_context(self.previous_context)
            
    return TraceContextManager(context)

def get_trace_logger(name: str) -> logging.Logger:
    """Get a logger that includes trace information in log records"""
    
    class TraceLoggerAdapter(logging.LoggerAdapter):
        def process(self, msg, kwargs):
            trace_context = trace_manager.get_current_trace_context()
            if trace_context:
                extra = kwargs.setdefault('extra', {})
                extra['trace_id'] = trace_context.trace_id
                extra['operation_type'] = trace_context.operation_type.value
                extra['operation_name'] = trace_context.operation_name
                if trace_context.user_id:
                    extra['user_id'] = trace_context.user_id
                    
                # Include trace ID in the message for easy filtering
                msg = f"[{trace_context.trace_id[:12]}] {msg}"
                
            return msg, kwargs
            
    return TraceLoggerAdapter(logging.getLogger(name), {})

# Utility functions for common trace operations
def start_telegram_trace(update_data: Dict[str, Any], operation_name: str) -> Optional[TraceContext]:
    """Start a trace for Telegram bot operations"""
    user_id = None
    if 'message' in update_data and 'from' in update_data['message']:
        user_id = update_data['message']['from'].get('id')
    elif 'callback_query' in update_data and 'from' in update_data['callback_query']:
        user_id = update_data['callback_query']['from'].get('id')
        
    return trace_manager.create_trace_context(
        operation_type=OperationType.TELEGRAM_MESSAGE,
        operation_name=operation_name,
        user_id=user_id,
        correlation_data={
            'update_type': list(update_data.keys())[0] if update_data else 'unknown',
            'update_id': update_data.get('update_id')
        }
    )

def start_webhook_trace(request_data: Dict[str, Any], webhook_type: str) -> Optional[TraceContext]:
    """Start a trace for webhook operations"""
    return trace_manager.create_trace_context(
        operation_type=OperationType.WEBHOOK_REQUEST,
        operation_name=f"webhook_{webhook_type}",
        correlation_data={
            'webhook_type': webhook_type,
            'request_method': request_data.get('method', 'POST'),
            'content_length': len(str(request_data))
        }
    )

def start_background_job_trace(job_name: str, job_data: Optional[Dict[str, Any]] = None) -> Optional[TraceContext]:
    """Start a trace for background job operations"""
    return trace_manager.create_trace_context(
        operation_type=OperationType.BACKGROUND_JOB,
        operation_name=job_name,
        correlation_data=job_data or {}
    )

def start_financial_operation_trace(operation_name: str, user_id: Optional[int], 
                                  transaction_data: Optional[Dict[str, Any]] = None) -> Optional[TraceContext]:
    """Start a trace for financial operations"""
    return trace_manager.create_trace_context(
        operation_type=OperationType.FINANCIAL_OPERATION,
        operation_name=operation_name,
        user_id=user_id,
        correlation_data=transaction_data or {}
    )

def correlate_with_external_id(external_id: str, external_system: str):
    """Add external system correlation to current trace"""
    current_context = trace_manager.get_current_trace_context()
    if current_context:
        current_context.correlation_data[f'{external_system}_id'] = external_id
        current_context.correlation_data[f'{external_system}_timestamp'] = datetime.utcnow().isoformat()

logger.info("🔗 Trace correlation system initialized")