"""
Financial Operations Trace Integration
Provides comprehensive trace correlation for all financial operations including
escrow, cashouts, payments, and external API integrations (Fincra, Kraken, etc.)
"""

import asyncio
from functools import wraps
from typing import Dict, Any, Optional, Callable, Union, List
from datetime import datetime
from decimal import Decimal

from utils.trace_correlation import (
    trace_manager, OperationType, TraceStatus, correlate_with_external_id
)
from utils.trace_logging_integration import (
    get_trace_logger, MonitoringIntegration, trace_external_api_call
)

logger = get_trace_logger(__name__)

class FinancialOperationType:
    """Types of financial operations for detailed categorization"""
    ESCROW_CREATE = "escrow_create"
    ESCROW_ACCEPT = "escrow_accept"
    ESCROW_COMPLETE = "escrow_complete"
    ESCROW_CANCEL = "escrow_cancel"
    CASHOUT_REQUEST = "cashout_request"
    CASHOUT_PROCESS = "cashout_process"
    PAYMENT_PROCESS = "payment_process"
    BALANCE_CHECK = "balance_check"
    EXCHANGE_OPERATION = "exchange_operation"
    FEE_CALCULATION = "fee_calculation"
    RATE_FETCH = "rate_fetch"
    WALLET_OPERATION = "wallet_operation"

def financial_traced(
    financial_operation_type: str,
    operation_name: Optional[str] = None,
    capture_amounts: bool = True,
    capture_user_context: bool = True,
    external_service: Optional[str] = None,
    sensitive_data_fields: Optional[List[str]] = None
):
    """
    Decorator for financial operations to add automatic trace correlation
    
    Args:
        financial_operation_type: Type of financial operation from FinancialOperationType
        operation_name: Custom operation name (defaults to function name)
        capture_amounts: Whether to capture amount data in trace (with privacy considerations)
        capture_user_context: Whether to capture user context
        external_service: Name of external service if applicable (e.g., 'fincra', 'kraken')
        sensitive_data_fields: List of parameter names to exclude from trace logs
    """
    
    def decorator(func: Callable) -> Callable:
        actual_operation_name = operation_name or f"{financial_operation_type}_{func.__name__}"
        sensitive_fields = sensitive_data_fields or ['api_key', 'secret', 'private_key', 'password', 'token']
        
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            # Extract financial context from parameters
            financial_context = FinancialTraceExtractor.extract_financial_context(
                args, kwargs, sensitive_fields
            )
            
            # Create or get parent trace context
            current_context = trace_manager.get_current_trace_context()
            
            if current_context:
                # Create child trace for financial operation
                trace_context = trace_manager.create_child_trace(
                    OperationType.FINANCIAL_OPERATION,
                    actual_operation_name,
                    {
                        'financial_operation_type': financial_operation_type,
                        'external_service': external_service,
                        **financial_context
                    }
                )
            else:
                # Create new root trace for financial operation
                trace_context = trace_manager.create_trace_context(
                    operation_type=OperationType.FINANCIAL_OPERATION,
                    operation_name=actual_operation_name,
                    user_id=financial_context.get('user_id'),
                    correlation_data={
                        'financial_operation_type': financial_operation_type,
                        'external_service': external_service,
                        **financial_context
                    }
                )
                
            if not trace_context:
                logger.warning(f"Failed to create trace context for financial operation: {actual_operation_name}")
                return await func(*args, **kwargs)
            
            # Set trace context
            trace_manager.set_trace_context(trace_context)
            
            # Start financial operation span
            span = trace_manager.start_span(f"financial_{actual_operation_name}", "financial_operation")
            
            try:
                # Add financial operation tags
                if span:
                    span.add_tag('financial_operation_type', financial_operation_type)
                    span.add_tag('external_service', external_service or 'internal')
                    span.add_tag('function_name', func.__name__)
                    
                    if capture_amounts and financial_context.get('amount'):
                        # Capture amounts with privacy considerations (rounded)
                        amount = financial_context.get('amount')
                        if isinstance(amount, (int, float, Decimal)):
                            span.add_tag('amount_range', FinancialTraceExtractor.get_amount_range(amount))
                            
                    if capture_user_context and financial_context.get('user_id'):
                        span.add_tag('user_id', financial_context['user_id'])
                        
                    if external_service:
                        span.add_tag('external_integration', True)
                        
                # Log operation start
                logger.info(
                    f"💰 Financial Operation Started: {actual_operation_name}",
                    operation_details={
                        'financial_type': financial_operation_type,
                        'external_service': external_service,
                        'user_id': financial_context.get('user_id'),
                        'has_amount': bool(financial_context.get('amount'))
                    }
                )
                
                # Execute the financial operation
                start_time = datetime.utcnow()
                result = await func(*args, **kwargs)
                execution_time = (datetime.utcnow() - start_time).total_seconds() * 1000
                
                # Process result for correlation
                result_context = FinancialTraceExtractor.extract_result_context(result)
                
                # Add external ID correlation if available
                if external_service and result_context.get('external_id'):
                    correlate_with_external_id(result_context['external_id'], external_service)
                    
                # Log successful completion
                logger.info(
                    f"✅ Financial Operation Completed: {actual_operation_name}",
                    performance_metrics={
                        'execution_time_ms': execution_time,
                        'external_service': external_service,
                        'result_status': result_context.get('status', 'success')
                    }
                )
                
                # Complete span and trace
                if span:
                    span.add_tag('execution_time_ms', execution_time)
                    span.add_tag('result_status', result_context.get('status', 'success'))
                    
                    if result_context.get('external_id'):
                        span.add_tag('external_id', result_context['external_id'])
                        
                    trace_manager.finish_span(span, TraceStatus.COMPLETED)
                    
                trace_manager.complete_trace(
                    trace_context.trace_id, 
                    TraceStatus.COMPLETED,
                    performance_metrics={
                        'execution_time_ms': execution_time,
                        'financial_operation': True
                    }
                )
                
                # Integrate with monitoring systems
                MonitoringIntegration.correlate_performance_metrics({
                    'operation': actual_operation_name,
                    'execution_time_ms': execution_time,
                    'financial_operation': True,
                    'external_service': external_service
                })
                
                return result
                
            except Exception as e:
                # Handle financial operation errors with full context
                execution_time = (datetime.utcnow() - trace_context.start_time).total_seconds() * 1000
                
                error_info = {
                    'error_type': type(e).__name__,
                    'error_message': str(e),
                    'financial_operation_type': financial_operation_type,
                    'external_service': external_service,
                    'function_name': func.__name__,
                    'financial_context': {
                        'user_id': financial_context.get('user_id'),
                        'has_amount': bool(financial_context.get('amount'))
                    }
                }
                
                # Log error with financial context
                logger.error(
                    f"❌ Financial Operation Failed: {actual_operation_name}",
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
            # Similar implementation for sync financial operations
            financial_context = FinancialTraceExtractor.extract_financial_context(
                args, kwargs, sensitive_fields
            )
            
            current_context = trace_manager.get_current_trace_context()
            
            if current_context:
                trace_context = trace_manager.create_child_trace(
                    OperationType.FINANCIAL_OPERATION,
                    actual_operation_name,
                    {
                        'financial_operation_type': financial_operation_type,
                        'external_service': external_service,
                        **financial_context
                    }
                )
            else:
                trace_context = trace_manager.create_trace_context(
                    operation_type=OperationType.FINANCIAL_OPERATION,
                    operation_name=actual_operation_name,
                    user_id=financial_context.get('user_id'),
                    correlation_data={
                        'financial_operation_type': financial_operation_type,
                        'external_service': external_service,
                        **financial_context
                    }
                )
                
            if not trace_context:
                return func(*args, **kwargs)
                
            trace_manager.set_trace_context(trace_context)
            span = trace_manager.start_span(f"financial_{actual_operation_name}", "financial_operation")
            
            try:
                if span:
                    span.add_tag('financial_operation_type', financial_operation_type)
                    span.add_tag('external_service', external_service or 'internal')
                    
                result = func(*args, **kwargs)
                
                if span:
                    trace_manager.finish_span(span, TraceStatus.COMPLETED)
                    
                trace_manager.complete_trace(trace_context.trace_id, TraceStatus.COMPLETED)
                return result
                
            except Exception as e:
                error_info = {
                    'error_type': type(e).__name__,
                    'error_message': str(e),
                    'financial_operation_type': financial_operation_type
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

class FinancialTraceExtractor:
    """Extract financial operation context for tracing"""
    
    @staticmethod
    def extract_financial_context(args: tuple, kwargs: dict, sensitive_fields: List[str]) -> Dict[str, Any]:
        """Extract financial context from function parameters"""
        context = {}
        
        # Look for common financial parameters
        if 'user_id' in kwargs:
            context['user_id'] = kwargs['user_id']
        elif len(args) > 0 and hasattr(args[0], 'user_id'):
            context['user_id'] = getattr(args[0], 'user_id', None)
            
        if 'amount' in kwargs:
            context['amount'] = kwargs['amount']
            
        if 'currency' in kwargs:
            context['currency'] = kwargs['currency']
            
        if 'transaction_id' in kwargs:
            context['transaction_id'] = kwargs['transaction_id']
            
        if 'escrow_id' in kwargs:
            context['escrow_id'] = kwargs['escrow_id']
            
        # Extract from first argument if it's a model instance
        if len(args) > 0:
            first_arg = args[0]
            if hasattr(first_arg, '__dict__'):
                for attr in ['id', 'user_id', 'amount', 'currency', 'status']:
                    if hasattr(first_arg, attr):
                        context[attr] = getattr(first_arg, attr)
                        
        # Filter out sensitive data
        filtered_context = {}
        for key, value in context.items():
            if key.lower() not in [field.lower() for field in sensitive_fields]:
                # Convert Decimal to float for JSON serialization
                if isinstance(value, Decimal):
                    filtered_context[key] = float(value)
                else:
                    filtered_context[key] = value
                    
        return filtered_context
    
    @staticmethod
    def extract_result_context(result: Any) -> Dict[str, Any]:
        """Extract context from operation result"""
        context = {}
        
        if isinstance(result, dict):
            # Look for common result fields
            for field in ['id', 'status', 'external_id', 'transaction_id', 'reference']:
                if field in result:
                    context[field] = result[field]
        elif hasattr(result, '__dict__'):
            # Extract from result object
            for attr in ['id', 'status', 'external_id', 'transaction_id', 'reference']:
                if hasattr(result, attr):
                    context[attr] = getattr(result, attr)
                    
        return context
    
    @staticmethod
    def get_amount_range(amount: Union[int, float, Decimal]) -> str:
        """Get privacy-safe amount range for tracing"""
        try:
            amount_float = float(amount)
            
            if amount_float < 1:
                return "< 1"
            elif amount_float < 10:
                return "1-10"
            elif amount_float < 100:
                return "10-100"
            elif amount_float < 1000:
                return "100-1000"
            elif amount_float < 10000:
                return "1000-10000"
            else:
                return "> 10000"
        except (ValueError, TypeError):
            return "unknown"

# Service-specific trace integrations
class FincraTraceIntegration:
    """Trace integration specifically for Fincra service operations"""
    
    @staticmethod
    def trace_fincra_operation(operation_name: str, request_data: Dict[str, Any]):
        """Trace Fincra API operations"""
        current_context = trace_manager.get_current_trace_context()
        if current_context:
            fincra_context = trace_external_api_call('fincra', operation_name)
            if fincra_context:
                # Add Fincra-specific correlation
                fincra_context.correlation_data.update({
                    'fincra_operation': operation_name,
                    'request_type': request_data.get('type', 'unknown'),
                    'currency': request_data.get('currency'),
                    'business_id': request_data.get('business_id') is not None
                })
                return fincra_context
        return None
    
    @staticmethod
    def correlate_fincra_webhook(webhook_data: Dict[str, Any]):
        """Correlate Fincra webhook with existing operations"""
        external_id = webhook_data.get('reference') or webhook_data.get('id')
        if external_id:
            correlate_with_external_id(external_id, 'fincra')
            
            # Try to find related trace by external ID
            current_context = trace_manager.get_current_trace_context()
            if current_context:
                current_context.correlation_data.update({
                    'fincra_webhook_received': True,
                    'fincra_webhook_type': webhook_data.get('type', 'unknown'),
                    'fincra_status': webhook_data.get('status'),
                    'fincra_reference': external_id
                })

class KrakenTraceIntegration:
    """Trace integration specifically for Kraken service operations"""
    
    @staticmethod
    def trace_kraken_operation(operation_name: str, request_data: Dict[str, Any]):
        """Trace Kraken API operations"""
        current_context = trace_manager.get_current_trace_context()
        if current_context:
            kraken_context = trace_external_api_call('kraken', operation_name)
            if kraken_context:
                # Add Kraken-specific correlation
                kraken_context.correlation_data.update({
                    'kraken_operation': operation_name,
                    'crypto_currency': request_data.get('asset'),
                    'address': request_data.get('key'),  # Withdrawal key name
                    'amount': FinancialTraceExtractor.get_amount_range(request_data.get('amount', 0))
                })
                return kraken_context
        return None
    
    @staticmethod
    def correlate_kraken_response(response_data: Dict[str, Any]):
        """Correlate Kraken API response with current operation"""
        current_context = trace_manager.get_current_trace_context()
        if current_context:
            if 'error' in response_data and response_data['error']:
                current_context.correlation_data['kraken_errors'] = response_data['error']
                
            if 'result' in response_data:
                result = response_data['result']
                if isinstance(result, dict) and 'refid' in result:
                    correlate_with_external_id(result['refid'], 'kraken')

class EscrowTraceIntegration:
    """Trace integration for escrow operations"""
    
    @staticmethod
    def trace_escrow_lifecycle(escrow_id: int, operation: str, user_id: Optional[int] = None):
        """Trace escrow lifecycle operations"""
        current_context = trace_manager.get_current_trace_context()
        
        if current_context:
            # Create child trace for escrow operation
            escrow_context = trace_manager.create_child_trace(
                OperationType.FINANCIAL_OPERATION,
                f"escrow_{operation}",
                {
                    'escrow_id': escrow_id,
                    'escrow_operation': operation,
                    'escrow_lifecycle': True
                }
            )
        else:
            # Create new trace for escrow operation
            escrow_context = trace_manager.create_trace_context(
                operation_type=OperationType.FINANCIAL_OPERATION,
                operation_name=f"escrow_{operation}",
                user_id=user_id,
                correlation_data={
                    'escrow_id': escrow_id,
                    'escrow_operation': operation,
                    'escrow_lifecycle': True
                }
            )
            
        return escrow_context
    
    @staticmethod
    def correlate_escrow_status_change(escrow_id: int, old_status: str, new_status: str):
        """Correlate escrow status changes"""
        current_context = trace_manager.get_current_trace_context()
        if current_context:
            current_context.correlation_data.update({
                'escrow_status_change': True,
                'escrow_old_status': old_status,
                'escrow_new_status': new_status,
                'escrow_id': escrow_id
            })

class WalletTraceIntegration:
    """Trace integration for wallet operations"""
    
    @staticmethod
    def trace_wallet_operation(user_id: int, operation: str, currency: str, amount: Optional[Union[int, float, Decimal]] = None):
        """Trace wallet operations"""
        correlation_data = {
            'wallet_operation': operation,
            'currency': currency,
            'user_id': user_id
        }
        
        if amount is not None:
            correlation_data['amount_range'] = FinancialTraceExtractor.get_amount_range(amount)
            
        current_context = trace_manager.get_current_trace_context()
        
        if current_context:
            wallet_context = trace_manager.create_child_trace(
                OperationType.FINANCIAL_OPERATION,
                f"wallet_{operation}",
                correlation_data
            )
        else:
            wallet_context = trace_manager.create_trace_context(
                operation_type=OperationType.FINANCIAL_OPERATION,
                operation_name=f"wallet_{operation}",
                user_id=user_id,
                correlation_data=correlation_data
            )
            
        return wallet_context

# Utility functions for common financial operations
def correlate_payment_processing(payment_id: str, payment_method: str, amount: Union[int, float, Decimal], currency: str):
    """Add payment processing correlation to current trace"""
    current_context = trace_manager.get_current_trace_context()
    if current_context:
        current_context.correlation_data.update({
            'payment_processing': True,
            'payment_id': payment_id,
            'payment_method': payment_method,
            'payment_currency': currency,
            'payment_amount_range': FinancialTraceExtractor.get_amount_range(amount)
        })

def correlate_rate_fetching(base_currency: str, target_currency: str, rate_source: str):
    """Add rate fetching correlation to current trace"""
    current_context = trace_manager.get_current_trace_context()
    if current_context:
        current_context.correlation_data.update({
            'rate_fetching': True,
            'base_currency': base_currency,
            'target_currency': target_currency,
            'rate_source': rate_source,
            'exchange_rate_operation': True
        })

def correlate_balance_check(service: str, currency: str, balance_status: str):
    """Add balance check correlation to current trace"""
    current_context = trace_manager.get_current_trace_context()
    if current_context:
        current_context.correlation_data.update({
            'balance_check': True,
            'balance_service': service,
            'balance_currency': currency,
            'balance_status': balance_status
        })

def setup_financial_trace_integration():
    """Setup financial trace integration with existing systems"""
    logger.info("💰 Setting up financial trace integration...")
    
    # This function can be called during application initialization
    # to ensure financial trace correlation is properly configured
    
    logger.info("✅ Financial trace integration configured")

logger.info("💰 Financial trace integration module initialized")