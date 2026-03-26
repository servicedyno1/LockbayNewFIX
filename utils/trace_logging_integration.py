"""
Enhanced Logging Integration for Trace Correlation
Integrates trace correlation with existing logging infrastructure and monitoring systems
"""

import logging
import json
from typing import Dict, Any, Optional
from datetime import datetime

from utils.trace_correlation import trace_manager, OperationType

class TraceEnhancedFormatter(logging.Formatter):
    """Enhanced formatter that includes trace information in log records"""
    
    def __init__(self, fmt=None, datefmt=None, include_trace=True):
        super().__init__(fmt, datefmt)
        self.include_trace = include_trace
        
    def format(self, record: logging.LogRecord) -> str:
        """Format log record with trace information"""
        
        # Get current trace context
        trace_context = trace_manager.get_current_trace_context()
        
        if self.include_trace and trace_context:
            # Add trace information to record
            record.trace_id = trace_context.trace_id[:12]  # Shortened for logs
            record.operation_type = trace_context.operation_type.value
            record.operation_name = trace_context.operation_name
            
            if trace_context.user_id:
                record.user_id = trace_context.user_id
            
            if trace_context.admin_user_id:
                record.admin_user_id = trace_context.admin_user_id
                
            # Enhance message with trace context
            if hasattr(record, 'trace_id'):
                original_msg = record.getMessage()
                record.msg = f"[{record.trace_id}] {original_msg}"
                record.args = ()  # Clear args since we've formatted the message
                
        return super().format(record)

class TraceStructuredLogger:
    """Structured logger with trace correlation for JSON logging"""
    
    def __init__(self, name: str):
        self.logger = logging.getLogger(name)
        self.name = name
        
    def _get_base_context(self) -> Dict[str, Any]:
        """Get base context with trace information"""
        context = {
            'timestamp': datetime.utcnow().isoformat(),
            'logger_name': self.name,
            'service': 'lockbay-telegram-bot'
        }
        
        trace_context = trace_manager.get_current_trace_context()
        if trace_context:
            context.update({
                'trace_id': trace_context.trace_id,
                'parent_trace_id': trace_context.parent_trace_id,
                'operation_type': trace_context.operation_type.value,
                'operation_name': trace_context.operation_name,
                'user_id': trace_context.user_id,
                'admin_user_id': trace_context.admin_user_id,
                'correlation_data': trace_context.correlation_data
            })
            
        return context
        
    def log_structured(self, level: int, message: str, **kwargs):
        """Log structured message with trace correlation"""
        context = self._get_base_context()
        context.update(kwargs)
        context['message'] = message
        
        # Log as JSON for structured logging systems
        structured_msg = json.dumps(context, default=str)
        self.logger.log(level, structured_msg)
        
    def info(self, message: str, **kwargs):
        """Log info message with trace correlation"""
        self.log_structured(logging.INFO, message, **kwargs)
        
    def warning(self, message: str, **kwargs):
        """Log warning message with trace correlation"""
        self.log_structured(logging.WARNING, message, **kwargs)
        
    def error(self, message: str, **kwargs):
        """Log error message with trace correlation"""
        self.log_structured(logging.ERROR, message, **kwargs)
        
    def critical(self, message: str, **kwargs):
        """Log critical message with trace correlation"""
        self.log_structured(logging.CRITICAL, message, **kwargs)
        
    def debug(self, message: str, **kwargs):
        """Log debug message with trace correlation"""
        self.log_structured(logging.DEBUG, message, **kwargs)

def setup_trace_logging():
    """Setup trace-enhanced logging for the entire application"""
    
    # Get root logger
    root_logger = logging.getLogger()
    
    # Create trace-enhanced formatter
    trace_formatter = TraceEnhancedFormatter(
        fmt='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        include_trace=True
    )
    
    # Update existing handlers with trace formatter
    for handler in root_logger.handlers:
        if not isinstance(handler.formatter, TraceEnhancedFormatter):
            handler.setFormatter(trace_formatter)
            
    logging.info("🔗 Trace-enhanced logging configured")

def get_trace_logger(name: str) -> TraceStructuredLogger:
    """Get a trace-aware structured logger"""
    return TraceStructuredLogger(name)

class MonitoringIntegration:
    """Integration with existing monitoring systems for trace correlation"""
    
    @staticmethod
    def correlate_with_realtime_monitor(event_data: Dict[str, Any]):
        """Add trace correlation to real-time monitoring events"""
        trace_context = trace_manager.get_current_trace_context()
        if trace_context:
            event_data['trace_correlation'] = {
                'trace_id': trace_context.trace_id,
                'operation_type': trace_context.operation_type.value,
                'operation_name': trace_context.operation_name,
                'user_id': trace_context.user_id
            }
        return event_data
        
    @staticmethod
    def correlate_with_activity_monitor(activity_data: Dict[str, Any]):
        """Add trace correlation to unified activity monitor"""
        trace_context = trace_manager.get_current_trace_context()
        if trace_context:
            activity_data['correlation_id'] = trace_context.trace_id
            activity_data['trace_context'] = {
                'operation_type': trace_context.operation_type.value,
                'operation_name': trace_context.operation_name,
                'start_time': trace_context.start_time.isoformat()
            }
        return activity_data
        
    @staticmethod
    def correlate_performance_metrics(metrics: Dict[str, Any]):
        """Add trace correlation to performance metrics"""
        trace_context = trace_manager.get_current_trace_context()
        if trace_context:
            metrics['trace_metadata'] = {
                'trace_id': trace_context.trace_id,
                'operation_context': f"{trace_context.operation_type.value}:{trace_context.operation_name}",
                'user_context': trace_context.user_id
            }
        return metrics

# Create default structured logger instance
trace_logger = get_trace_logger(__name__)

# Integration utilities for common use cases
def trace_database_operation(operation_name: str, query_info: Optional[Dict[str, Any]] = None):
    """Utility to add trace correlation to database operations"""
    current_context = trace_manager.get_current_trace_context()
    if current_context:
        # Create child trace for database operation
        db_context = trace_manager.create_child_trace(
            OperationType.DATABASE_OPERATION,
            operation_name,
            {'query_info': query_info or {}}
        )
        return db_context
    return None

def trace_external_api_call(service_name: str, endpoint: str, method: str = "POST"):
    """Utility to add trace correlation to external API calls"""
    current_context = trace_manager.get_current_trace_context()
    if current_context:
        api_context = trace_manager.create_child_trace(
            OperationType.EXTERNAL_API_CALL,
            f"{service_name}_{endpoint}",
            {
                'service': service_name,
                'endpoint': endpoint,
                'method': method,
                'external_call': True
            }
        )
        return api_context
    return None

def trace_admin_operation(admin_user_id: int, operation_name: str, operation_data: Optional[Dict[str, Any]] = None):
    """Utility to add trace correlation to admin operations"""
    admin_context = trace_manager.create_trace_context(
        OperationType.ADMIN_OPERATION,
        operation_name,
        admin_user_id=admin_user_id,
        correlation_data=operation_data or {}
    )
    return admin_context

def correlate_with_webhook_processing(webhook_type: str, request_data: Dict[str, Any]):
    """Add trace correlation to webhook processing"""
    webhook_context = trace_manager.create_trace_context(
        OperationType.WEBHOOK_REQUEST,
        f"webhook_{webhook_type}",
        correlation_data={
            'webhook_type': webhook_type,
            'request_size': len(str(request_data)),
            'webhook_processing': True
        }
    )
    return webhook_context

def correlate_background_job(job_name: str, job_context: Optional[Dict[str, Any]] = None):
    """Add trace correlation to background jobs"""
    current_context = trace_manager.get_current_trace_context()
    
    if current_context:
        # Create child trace if we're in an existing trace context
        job_trace = trace_manager.create_child_trace(
            OperationType.BACKGROUND_JOB,
            job_name,
            job_context or {}
        )
    else:
        # Create new trace for background jobs
        job_trace = trace_manager.create_trace_context(
            OperationType.BACKGROUND_JOB,
            job_name,
            correlation_data=job_context or {}
        )
    
    return job_trace

logging.info("🔗 Trace logging integration initialized")