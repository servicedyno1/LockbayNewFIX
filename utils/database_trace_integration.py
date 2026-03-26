"""
Database Operations Trace Integration
Provides comprehensive trace correlation for all database operations and connection management
"""

import asyncio
import json
from functools import wraps
from typing import Dict, Any, Optional, Callable, List
from datetime import datetime
from contextlib import contextmanager, asynccontextmanager
import traceback

from utils.trace_correlation import (
    trace_manager, OperationType, TraceStatus
)
from utils.trace_logging_integration import (
    get_trace_logger, MonitoringIntegration
)

logger = get_trace_logger(__name__)

class DatabaseOperationType:
    """Types of database operations for detailed categorization"""
    QUERY = "query"
    INSERT = "insert"
    UPDATE = "update"
    DELETE = "delete"
    TRANSACTION = "transaction"
    MIGRATION = "migration"
    CONNECTION_MANAGEMENT = "connection_management"
    SCHEMA_OPERATION = "schema_operation"
    BULK_OPERATION = "bulk_operation"

def database_traced(
    operation_type: str = DatabaseOperationType.QUERY,
    operation_name: Optional[str] = None,
    capture_query: bool = False,
    capture_parameters: bool = False,
    sensitive_tables: Optional[List[str]] = None,
    expected_duration_ms: Optional[int] = None
):
    """
    Decorator for database operations to add automatic trace correlation
    
    Args:
        operation_type: Type of database operation from DatabaseOperationType
        operation_name: Custom operation name (defaults to function name)
        capture_query: Whether to capture SQL query in trace (be careful with sensitive data)
        capture_parameters: Whether to capture query parameters
        sensitive_tables: List of table names to exclude from detailed logging
        expected_duration_ms: Expected operation duration for performance monitoring
    """
    
    def decorator(func: Callable) -> Callable:
        actual_operation_name = operation_name or f"db_{func.__name__}"
        sensitive_tables_set = set(sensitive_tables or ['users', 'api_keys', 'secrets'])
        
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            # Extract database context from parameters
            db_context = DatabaseTraceExtractor.extract_database_context(
                args, kwargs, operation_type
            )
            
            # Create or get parent trace context
            current_context = trace_manager.get_current_trace_context()
            
            if current_context:
                # Create child trace for database operation
                trace_context = trace_manager.create_child_trace(
                    OperationType.DATABASE_OPERATION,
                    actual_operation_name,
                    {
                        'database_operation_type': operation_type,
                        **db_context
                    }
                )
            else:
                # Create new root trace for database operation
                trace_context = trace_manager.create_trace_context(
                    operation_type=OperationType.DATABASE_OPERATION,
                    operation_name=actual_operation_name,
                    correlation_data={
                        'database_operation_type': operation_type,
                        **db_context
                    }
                )
                
            if not trace_context:
                logger.warning(f"Failed to create trace context for database operation: {actual_operation_name}")
                return await func(*args, **kwargs)
            
            # Set trace context
            trace_manager.set_trace_context(trace_context)
            
            # Start database operation span
            span = trace_manager.start_span(f"database_{actual_operation_name}", "database_operation")
            
            try:
                # Add database operation tags
                if span:
                    span.add_tag('database_operation_type', operation_type)
                    span.add_tag('function_name', func.__name__)
                    
                    if expected_duration_ms:
                        span.add_tag('expected_duration_ms', expected_duration_ms)
                    
                    # Capture query information if requested and safe
                    if capture_query and db_context.get('query'):
                        query = db_context['query']
                        if not DatabaseTraceExtractor.contains_sensitive_data(query, sensitive_tables_set):
                            span.add_tag('sql_query', query[:500])  # Truncated
                        else:
                            span.add_tag('sql_query', '[SENSITIVE_QUERY_REDACTED]')
                            
                    if capture_parameters and db_context.get('parameters'):
                        # Always sanitize parameters
                        sanitized_params = DatabaseTraceExtractor.sanitize_parameters(db_context['parameters'])
                        span.add_tag('query_parameters', json.dumps(sanitized_params, default=str)[:300])
                        
                # Log operation start
                logger.info(
                    f"💾 Database Operation Started: {actual_operation_name}",
                    operation_details={
                        'operation_type': operation_type,
                        'has_query': bool(db_context.get('query')),
                        'has_parameters': bool(db_context.get('parameters')),
                        'table_names': db_context.get('table_names', [])
                    }
                )
                
                # Execute the database operation
                start_time = datetime.utcnow()
                result = await func(*args, **kwargs)
                execution_time = (datetime.utcnow() - start_time).total_seconds() * 1000  # ms
                
                # Process database result
                result_context = DatabaseTraceExtractor.extract_result_context(result)
                
                # Check performance against expected duration
                performance_status = "normal"
                if expected_duration_ms:
                    if execution_time > expected_duration_ms * 1.5:
                        performance_status = "slow"
                    elif execution_time > expected_duration_ms * 2:
                        performance_status = "very_slow"
                
                # Log successful completion
                logger.info(
                    f"✅ Database Operation Completed: {actual_operation_name}",
                    performance_metrics={
                        'execution_time_ms': execution_time,
                        'performance_status': performance_status,
                        'rows_affected': result_context.get('rows_affected'),
                        'result_type': result_context.get('result_type')
                    }
                )
                
                # Complete span and trace
                if span:
                    span.add_tag('execution_time_ms', execution_time)
                    span.add_tag('performance_status', performance_status)
                    
                    if result_context.get('rows_affected') is not None:
                        span.add_tag('rows_affected', result_context['rows_affected'])
                        
                    trace_manager.finish_span(span, TraceStatus.COMPLETED)
                    
                trace_manager.complete_trace(
                    trace_context.trace_id, 
                    TraceStatus.COMPLETED,
                    performance_metrics={
                        'execution_time_ms': execution_time,
                        'performance_status': performance_status,
                        'database_operation': True
                    }
                )
                
                # Integrate with monitoring systems
                MonitoringIntegration.correlate_performance_metrics({
                    'operation': actual_operation_name,
                    'execution_time_ms': execution_time,
                    'performance_status': performance_status,
                    'database_operation': True,
                    'operation_type': operation_type
                })
                
                # Alert if operation took too long
                if expected_duration_ms and execution_time > expected_duration_ms * 2:
                    logger.warning(
                        f"⚠️ SLOW_DATABASE_OPERATION: {actual_operation_name} took {execution_time:.1f}ms "
                        f"(expected: {expected_duration_ms}ms)"
                    )
                
                return result
                
            except Exception as e:
                # Handle database operation errors with full context
                execution_time = (datetime.utcnow() - trace_context.start_time).total_seconds() * 1000
                
                error_info = {
                    'error_type': type(e).__name__,
                    'error_message': str(e),
                    'error_traceback': traceback.format_exc(),
                    'database_operation_type': operation_type,
                    'function_name': func.__name__,
                    'database_context': {
                        'has_query': bool(db_context.get('query')),
                        'table_names': db_context.get('table_names', [])
                    }
                }
                
                # Log error with database context
                logger.error(
                    f"❌ Database Operation Failed: {actual_operation_name}",
                    error_details=error_info,
                    performance_metrics={'execution_time_ms': execution_time}
                )
                
                # Update span and trace with error
                if span:
                    span.set_error(e, error_info)
                    trace_manager.finish_span(span, TraceStatus.FAILED)
                    
                trace_manager.complete_trace(
                    trace_context.trace_id, 
                    TraceStatus.FAILED, 
                    error_info,
                    performance_metrics={'execution_time_ms': execution_time}
                )
                
                # Re-raise the exception to maintain original behavior
                raise
                
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            # Similar implementation for sync database operations
            db_context = DatabaseTraceExtractor.extract_database_context(
                args, kwargs, operation_type
            )
            
            current_context = trace_manager.get_current_trace_context()
            
            if current_context:
                trace_context = trace_manager.create_child_trace(
                    OperationType.DATABASE_OPERATION,
                    actual_operation_name,
                    {'database_operation_type': operation_type, **db_context}
                )
            else:
                trace_context = trace_manager.create_trace_context(
                    operation_type=OperationType.DATABASE_OPERATION,
                    operation_name=actual_operation_name,
                    correlation_data={'database_operation_type': operation_type, **db_context}
                )
                
            if not trace_context:
                return func(*args, **kwargs)
                
            trace_manager.set_trace_context(trace_context)
            span = trace_manager.start_span(f"database_{actual_operation_name}", "database_operation")
            
            try:
                if span:
                    span.add_tag('database_operation_type', operation_type)
                    
                start_time = datetime.utcnow()
                result = func(*args, **kwargs)
                execution_time = (datetime.utcnow() - start_time).total_seconds() * 1000
                
                if span:
                    span.add_tag('execution_time_ms', execution_time)
                    trace_manager.finish_span(span, TraceStatus.COMPLETED)
                    
                trace_manager.complete_trace(
                    trace_context.trace_id, 
                    TraceStatus.COMPLETED,
                    performance_metrics={'execution_time_ms': execution_time}
                )
                
                return result
                
            except Exception as e:
                error_info = {
                    'error_type': type(e).__name__,
                    'error_message': str(e),
                    'database_operation_type': operation_type
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

class DatabaseTraceExtractor:
    """Extract database operation context for tracing"""
    
    @staticmethod
    def extract_database_context(args: tuple, kwargs: dict, operation_type: str) -> Dict[str, Any]:
        """Extract database context from function parameters"""
        context = {
            'database_operation_type': operation_type,
            'operation_timestamp': datetime.utcnow().isoformat()
        }
        
        # Look for common database parameters
        if 'query' in kwargs:
            context['query'] = kwargs['query']
            context['table_names'] = DatabaseTraceExtractor.extract_table_names(kwargs['query'])
            
        if 'sql' in kwargs:
            context['query'] = kwargs['sql']
            context['table_names'] = DatabaseTraceExtractor.extract_table_names(kwargs['sql'])
            
        if 'params' in kwargs:
            context['parameters'] = kwargs['params']
            
        if 'parameters' in kwargs:
            context['parameters'] = kwargs['parameters']
            
        if 'session' in kwargs:
            context['has_session'] = True
            
        if 'connection' in kwargs:
            context['has_connection'] = True
            
        # Extract from first argument if it's a query string
        if len(args) > 0 and isinstance(args[0], str):
            context['query'] = args[0]
            context['table_names'] = DatabaseTraceExtractor.extract_table_names(args[0])
            
        return context
    
    @staticmethod
    def extract_table_names(query: str) -> List[str]:
        """Extract table names from SQL query"""
        if not query:
            return []
            
        query_lower = query.lower()
        table_names = []
        
        # Simple table name extraction (could be improved with proper SQL parsing)
        import re
        
        # FROM clause
        from_matches = re.findall(r'from\s+(\w+)', query_lower)
        table_names.extend(from_matches)
        
        # JOIN clauses
        join_matches = re.findall(r'join\s+(\w+)', query_lower)
        table_names.extend(join_matches)
        
        # INSERT INTO
        insert_matches = re.findall(r'insert\s+into\s+(\w+)', query_lower)
        table_names.extend(insert_matches)
        
        # UPDATE
        update_matches = re.findall(r'update\s+(\w+)', query_lower)
        table_names.extend(update_matches)
        
        # DELETE FROM
        delete_matches = re.findall(r'delete\s+from\s+(\w+)', query_lower)
        table_names.extend(delete_matches)
        
        return list(set(table_names))  # Remove duplicates
    
    @staticmethod
    def contains_sensitive_data(query: str, sensitive_tables: set) -> bool:
        """Check if query contains sensitive data"""
        if not query:
            return False
            
        query_lower = query.lower()
        
        # Check for sensitive table names
        for table in sensitive_tables:
            if table.lower() in query_lower:
                return True
                
        # Check for sensitive keywords
        sensitive_keywords = ['password', 'secret', 'key', 'token', 'api_key']
        for keyword in sensitive_keywords:
            if keyword in query_lower:
                return True
                
        return False
    
    @staticmethod
    def sanitize_parameters(params: Any) -> Any:
        """Sanitize database parameters for logging"""
        if params is None:
            return None
            
        if isinstance(params, dict):
            sanitized = {}
            for key, value in params.items():
                key_lower = str(key).lower()
                if any(sensitive in key_lower for sensitive in ['password', 'secret', 'key', 'token']):
                    sanitized[key] = '[REDACTED]'
                else:
                    sanitized[key] = str(value)[:100] if isinstance(value, str) else value
            return sanitized
            
        elif isinstance(params, (list, tuple)):
            # For positional parameters, just truncate strings
            return [str(param)[:100] if isinstance(param, str) else param for param in params]
            
        else:
            return str(params)[:100] if isinstance(params, str) else params
    
    @staticmethod
    def extract_result_context(result: Any) -> Dict[str, Any]:
        """Extract context from database operation result"""
        context = {'result_type': type(result).__name__}
        
        if result is None:
            return context
            
        # For SQLAlchemy result objects
        if hasattr(result, 'rowcount'):
            context['rows_affected'] = result.rowcount
            
        # For list results (like fetchall())
        elif isinstance(result, list):
            context['rows_returned'] = len(result)
            
        # For single row results
        elif hasattr(result, '__dict__'):
            context['single_row_result'] = True
            
        return context

# Context managers for transaction tracing
@asynccontextmanager
async def traced_transaction(session, transaction_name: str = "database_transaction"):
    """Async context manager for tracing database transactions"""
    
    # Create child trace for transaction
    current_context = trace_manager.get_current_trace_context()
    
    if current_context:
        transaction_context = trace_manager.create_child_trace(
            OperationType.DATABASE_OPERATION,
            transaction_name,
            {
                'database_operation_type': DatabaseOperationType.TRANSACTION,
                'transaction_scope': True
            }
        )
    else:
        transaction_context = trace_manager.create_trace_context(
            operation_type=OperationType.DATABASE_OPERATION,
            operation_name=transaction_name,
            correlation_data={
                'database_operation_type': DatabaseOperationType.TRANSACTION,
                'transaction_scope': True
            }
        )
    
    if transaction_context:
        trace_manager.set_trace_context(transaction_context)
        
    span = trace_manager.start_span(transaction_name, "database_transaction")
    
    try:
        if span:
            span.add_tag('transaction_started', datetime.utcnow().isoformat())
            
        logger.info(f"💾 Database Transaction Started: {transaction_name}")
        
        yield session
        
        # Transaction successful
        if span:
            span.add_tag('transaction_committed', True)
            trace_manager.finish_span(span, TraceStatus.COMPLETED)
            
        if transaction_context:
            trace_manager.complete_trace(transaction_context.trace_id, TraceStatus.COMPLETED)
            
        logger.info(f"✅ Database Transaction Committed: {transaction_name}")
        
    except Exception as e:
        # Transaction failed
        error_info = {
            'error_type': type(e).__name__,
            'error_message': str(e),
            'transaction_name': transaction_name
        }
        
        if span:
            span.set_error(e, error_info)
            span.add_tag('transaction_rolled_back', True)
            trace_manager.finish_span(span, TraceStatus.FAILED)
            
        if transaction_context:
            trace_manager.complete_trace(transaction_context.trace_id, TraceStatus.FAILED, error_info)
            
        logger.error(f"❌ Database Transaction Failed: {transaction_name} - {str(e)}")
        raise

@contextmanager
def traced_connection(connection, connection_name: str = "database_connection"):
    """Context manager for tracing database connections"""
    
    span = trace_manager.start_span(f"connection_{connection_name}", "database_connection")
    
    try:
        if span:
            span.add_tag('connection_acquired', datetime.utcnow().isoformat())
            
        logger.debug(f"💾 Database Connection Acquired: {connection_name}")
        
        yield connection
        
        if span:
            span.add_tag('connection_released', datetime.utcnow().isoformat())
            trace_manager.finish_span(span, TraceStatus.COMPLETED)
            
        logger.debug(f"✅ Database Connection Released: {connection_name}")
        
    except Exception as e:
        error_info = {
            'error_type': type(e).__name__,
            'error_message': str(e),
            'connection_name': connection_name
        }
        
        if span:
            span.set_error(e, error_info)
            trace_manager.finish_span(span, TraceStatus.FAILED)
            
        logger.error(f"❌ Database Connection Error: {connection_name} - {str(e)}")
        raise

# Utility functions for common database operations
def correlate_session_management(session_id: str, operation: str, connection_info: Dict[str, Any]):
    """Add session management correlation to current trace"""
    current_context = trace_manager.get_current_trace_context()
    if current_context:
        current_context.correlation_data.update({
            'session_management': True,
            'session_id': session_id,
            'session_operation': operation,
            'connection_info': connection_info
        })

def correlate_connection_pool_metrics(pool_size: int, active_connections: int, idle_connections: int):
    """Add connection pool metrics correlation to current trace"""
    current_context = trace_manager.get_current_trace_context()
    if current_context:
        current_context.correlation_data.update({
            'connection_pool_metrics': {
                'pool_size': pool_size,
                'active_connections': active_connections,
                'idle_connections': idle_connections,
                'utilization_percent': (active_connections / pool_size * 100) if pool_size > 0 else 0,
                'timestamp': datetime.utcnow().isoformat()
            }
        })

def correlate_migration_operation(migration_name: str, migration_direction: str, affected_tables: List[str]):
    """Add migration operation correlation to current trace"""
    current_context = trace_manager.get_current_trace_context()
    if current_context:
        current_context.correlation_data.update({
            'database_migration': True,
            'migration_name': migration_name,
            'migration_direction': migration_direction,
            'affected_tables': affected_tables,
            'migration_timestamp': datetime.utcnow().isoformat()
        })

def setup_database_trace_integration():
    """Setup database trace integration with existing systems"""
    logger.info("💾 Setting up database trace integration...")
    
    # This function can be called during application initialization
    # to ensure database trace correlation is properly configured
    
    logger.info("✅ Database trace integration configured")

logger.info("💾 Database trace integration module initialized")