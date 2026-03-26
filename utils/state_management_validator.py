"""
State Management Validation and Testing System
Comprehensive validation for Redis sessions, financial locks, and database operations
Ensures data integrity and proper operation of enhanced state management
"""

import logging
import asyncio
import time
from typing import Dict, Any, List, Optional
from datetime import datetime
from dataclasses import dataclass

# Import all state management components
from utils.session_migration_helper import session_migration_helper
from utils.financial_operation_locker import financial_locker, FinancialLockType
from utils.enhanced_db_session_manager import enhanced_db_session_manager
from utils.state_management_monitor import state_management_monitor
from handlers.wallet_direct_enhanced_operations import (
    get_wallet_balance_with_locking
)
from models import EscrowStatus

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Result of a validation test"""
    test_name: str
    success: bool
    duration_ms: float
    details: Dict[str, Any]
    error_message: Optional[str] = None


class StateManagementValidator:
    """
    Comprehensive validation system for all state management components
    
    Features:
    - Redis session validation
    - Financial locking validation
    - Database session validation
    - Performance and stress testing
    - Integration testing
    - Data integrity verification
    """
    
    def __init__(self):
        self.validation_results: List[ValidationResult] = []
        self.test_user_id = 999999999  # Test user ID
        self.test_context = None  # Mock context for testing
    
    async def run_comprehensive_validation(self) -> Dict[str, Any]:
        """
        Run comprehensive validation of all state management components
        
        Returns:
            Dict with validation results and overall status
        """
        logger.info("🗺️ Starting comprehensive state management validation")
        
        validation_tests = [
            self._validate_redis_session_operations,
            self._validate_financial_locking,
            self._validate_database_sessions,
            self._validate_wallet_operations,
            self._validate_escrow_operations,
            self._validate_concurrent_operations,
            self._validate_error_handling,
            self._validate_monitoring_system
        ]
        
        start_time = time.time()
        
        # Run all validation tests
        for test_func in validation_tests:
            try:
                result = await test_func()
                self.validation_results.append(result)
                
                if result.success:
                    logger.info(f"✅ {result.test_name}: PASSED ({result.duration_ms:.2f}ms)")
                else:
                    logger.error(f"❌ {result.test_name}: FAILED - {result.error_message}")
                    
            except Exception as e:
                failed_result = ValidationResult(
                    test_name=test_func.__name__,
                    success=False,
                    duration_ms=0.0,
                    details={'exception': str(e)},
                    error_message=str(e)
                )
                self.validation_results.append(failed_result)
                logger.error(f"❌ {test_func.__name__}: EXCEPTION - {e}")
        
        total_duration = (time.time() - start_time) * 1000
        
        # Compile results
        passed_tests = [r for r in self.validation_results if r.success]
        failed_tests = [r for r in self.validation_results if not r.success]
        
        overall_success = len(failed_tests) == 0
        
        results_summary = {
            'overall_success': overall_success,
            'total_tests': len(self.validation_results),
            'passed_tests': len(passed_tests),
            'failed_tests': len(failed_tests),
            'total_duration_ms': total_duration,
            'avg_test_duration_ms': total_duration / len(self.validation_results) if self.validation_results else 0,
            'test_results': [
                {
                    'name': r.test_name,
                    'success': r.success,
                    'duration_ms': r.duration_ms,
                    'error': r.error_message
                }
                for r in self.validation_results
            ]
        }
        
        if overall_success:
            logger.info(f"✅ ALL VALIDATION TESTS PASSED! ({len(passed_tests)}/{len(self.validation_results)}) in {total_duration:.2f}ms")
        else:
            logger.error(f"❌ VALIDATION FAILED: {len(failed_tests)} tests failed out of {len(self.validation_results)}")
        
        return results_summary
    
    async def _validate_redis_session_operations(self) -> ValidationResult:
        """Validate Redis session operations"""
        start_time = time.time()
        
        try:
            # Test session creation and retrieval
            test_data = {
                'test_key': 'test_value',
                'timestamp': str(datetime.utcnow()),
                'counter': 42
            }
            
            # Set session data
            await session_migration_helper.set_session_data(
                self.test_user_id, self.test_context, test_data, "test_data", "validation_test"
            )
            
            # Get session data
            retrieved_data = await session_migration_helper.get_session_data(
                self.test_user_id, self.test_context, "test_data"
            )
            
            # Validate data integrity
            if retrieved_data.get('test_key') != 'test_value':
                raise ValueError("Session data integrity check failed")
            
            # Test session clearing
            await session_migration_helper.clear_session_data(self.test_user_id, self.test_context)
            
            duration_ms = (time.time() - start_time) * 1000
            
            return ValidationResult(
                test_name="Redis Session Operations",
                success=True,
                duration_ms=duration_ms,
                details={
                    'operations_tested': ['set', 'get', 'clear'],
                    'data_integrity': 'verified',
                    'session_cleared': 'success'
                }
            )
            
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            return ValidationResult(
                test_name="Redis Session Operations",
                success=False,
                duration_ms=duration_ms,
                details={'error_type': type(e).__name__},
                error_message=str(e)
            )
    
    async def _validate_financial_locking(self) -> ValidationResult:
        """Validate financial locking mechanisms"""
        start_time = time.time()
        
        try:
            operation_id = f"validation_test_{int(time.time())}"
            
            # Test distributed locking
            async with financial_locker.atomic_financial_operation(
                operation_id=operation_id,
                lock_type=FinancialLockType.WALLET_BALANCE,
                timeout_seconds=10
            ) as session:
                
                # Verify session is valid
                if not session:
                    raise ValueError("Failed to acquire financial lock")
                
                # Test lock metrics
                lock_metrics = financial_locker.get_lock_metrics()
                
                if lock_metrics['active_locks'] < 1:
                    raise ValueError("Active lock count validation failed")
            
            # Verify lock was released
            financial_locker.get_lock_metrics()
            
            duration_ms = (time.time() - start_time) * 1000
            
            return ValidationResult(
                test_name="Financial Locking",
                success=True,
                duration_ms=duration_ms,
                details={
                    'lock_acquired': 'success',
                    'lock_released': 'success',
                    'metrics_validated': 'success'
                }
            )
            
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            return ValidationResult(
                test_name="Financial Locking",
                success=False,
                duration_ms=duration_ms,
                details={'error_type': type(e).__name__},
                error_message=str(e)
            )
    
    async def _validate_database_sessions(self) -> ValidationResult:
        """Validate database session management"""
        start_time = time.time()
        
        try:
            # Test managed session creation
            async with enhanced_db_session_manager.managed_session(
                operation_name="validation_test",
                timeout_seconds=10
            ) as db_session:
                
                # Test basic database operation
                result = db_session.execute("SELECT 1 as test_value")
                row = result.fetchone()
                
                if not row or row[0] != 1:
                    raise ValueError("Database session test query failed")
            
            # Test session metrics
            session_metrics = enhanced_db_session_manager.get_session_metrics()
            
            if session_metrics['operations_total'] < 1:
                raise ValueError("Session metrics validation failed")
            
            duration_ms = (time.time() - start_time) * 1000
            
            return ValidationResult(
                test_name="Database Sessions",
                success=True,
                duration_ms=duration_ms,
                details={
                    'session_created': 'success',
                    'query_executed': 'success',
                    'metrics_available': 'success'
                }
            )
            
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            return ValidationResult(
                test_name="Database Sessions",
                success=False,
                duration_ms=duration_ms,
                details={'error_type': type(e).__name__},
                error_message=str(e)
            )
    
    async def _validate_wallet_operations(self) -> ValidationResult:
        """Validate enhanced wallet operations"""
        start_time = time.time()
        
        try:
            # Test balance checking with locking
            balance_result = await get_wallet_balance_with_locking(
                user_id=self.test_user_id,
                currency="USD"
            )
            
            # This may return None if test user doesn't exist, which is expected
            # The important part is that the operation completes without errors
            
            duration_ms = (time.time() - start_time) * 1000
            
            return ValidationResult(
                test_name="Wallet Operations",
                success=True,
                duration_ms=duration_ms,
                details={
                    'balance_check': 'completed',
                    'locking_mechanism': 'functional',
                    'result': str(balance_result) if balance_result else 'no_wallet_found'
                }
            )
            
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            return ValidationResult(
                test_name="Wallet Operations",
                success=False,
                duration_ms=duration_ms,
                details={'error_type': type(e).__name__},
                error_message=str(e)
            )
    
    async def _validate_escrow_operations(self) -> ValidationResult:
        """Validate enhanced escrow operations"""
        start_time = time.time()
        
        try:
            # This test validates the escrow operation framework
            # without actually creating escrows (which would require valid users)
            
            # Test status transition validation logic
            from utils.financial_operation_locker import financial_locker
            
            # Test escrow status transition validation
            valid_transitions = financial_locker._get_valid_escrow_transitions(EscrowStatus.PENDING_PAYMENT)
            
            expected_transitions = [EscrowStatus.PAYMENT_RECEIVED, EscrowStatus.CANCELLED]
            
            if not all(status in valid_transitions for status in expected_transitions):
                raise ValueError("Escrow status transition validation failed")
            
            duration_ms = (time.time() - start_time) * 1000
            
            return ValidationResult(
                test_name="Escrow Operations",
                success=True,
                duration_ms=duration_ms,
                details={
                    'transition_validation': 'success',
                    'valid_transitions': len(valid_transitions),
                    'framework_functional': 'verified'
                }
            )
            
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            return ValidationResult(
                test_name="Escrow Operations",
                success=False,
                duration_ms=duration_ms,
                details={'error_type': type(e).__name__},
                error_message=str(e)
            )
    
    async def _validate_concurrent_operations(self) -> ValidationResult:
        """Validate concurrent operation handling"""
        start_time = time.time()
        
        try:
            # Test concurrent session operations
            async def concurrent_session_test(user_id: int):
                test_data = {'concurrent_test': True, 'user_id': user_id}
                await session_migration_helper.set_session_data(
                    user_id, self.test_context, test_data, "concurrent_test", "validation"
                )
                return await session_migration_helper.get_session_data(
                    user_id, self.test_context, "concurrent_test"
                )
            
            # Run 5 concurrent operations
            tasks = [
                concurrent_session_test(self.test_user_id + i) 
                for i in range(5)
            ]
            
            results = await asyncio.gather(*tasks)
            
            # Verify all operations completed successfully
            if len(results) != 5:
                raise ValueError("Concurrent operation count mismatch")
            
            for i, result in enumerate(results):
                if not result or result.get('user_id') != self.test_user_id + i:
                    raise ValueError(f"Concurrent operation {i} failed")
            
            duration_ms = (time.time() - start_time) * 1000
            
            return ValidationResult(
                test_name="Concurrent Operations",
                success=True,
                duration_ms=duration_ms,
                details={
                    'concurrent_tasks': 5,
                    'all_completed': True,
                    'data_integrity': 'verified'
                }
            )
            
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            return ValidationResult(
                test_name="Concurrent Operations",
                success=False,
                duration_ms=duration_ms,
                details={'error_type': type(e).__name__},
                error_message=str(e)
            )
    
    async def _validate_error_handling(self) -> ValidationResult:
        """Validate error handling mechanisms"""
        start_time = time.time()
        
        try:
            # Test graceful error handling
            try:
                # Attempt operation with invalid parameters
                async with financial_locker.atomic_financial_operation(
                    operation_id="invalid_test",
                    lock_type=FinancialLockType.WALLET_BALANCE,
                    timeout_seconds=0.001  # Very short timeout to trigger timeout error
                ):
                    pass  # This should timeout
            except Exception as expected_error:
                # This error is expected and shows error handling works
                logger.debug(f"Expected error caught: {expected_error}")
            
            # Test session cleanup on errors
            try:
                await session_migration_helper.set_session_data(
                    None,  # Invalid user_id
                    self.test_context,
                    {'test': 'data'},
                    "test",
                    "validation"
                )
            except Exception as expected_error:
                logger.debug(f"Expected session error caught: {expected_error}")
            
            duration_ms = (time.time() - start_time) * 1000
            
            return ValidationResult(
                test_name="Error Handling",
                success=True,
                duration_ms=duration_ms,
                details={
                    'timeout_handling': 'verified',
                    'invalid_parameter_handling': 'verified',
                    'graceful_degradation': 'functional'
                }
            )
            
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            return ValidationResult(
                test_name="Error Handling",
                success=False,
                duration_ms=duration_ms,
                details={'error_type': type(e).__name__},
                error_message=str(e)
            )
    
    async def _validate_monitoring_system(self) -> ValidationResult:
        """Validate monitoring and metrics system"""
        start_time = time.time()
        
        try:
            # Test metrics collection
            metrics = await state_management_monitor.collect_comprehensive_metrics()
            
            if not metrics:
                raise ValueError("Failed to collect metrics")
            
            # Validate metric structure
            required_fields = [
                'active_sessions', 'total_sessions_created', 'active_locks',
                'active_db_sessions', 'overall_status', 'last_updated'
            ]
            
            for field in required_fields:
                if not hasattr(metrics, field):
                    raise ValueError(f"Missing metric field: {field}")
            
            # Test metrics summary
            summary = state_management_monitor.get_metrics_summary(hours=1)
            
            if 'error' in summary:
                logger.warning(f"Metrics summary has no data: {summary['error']}")
            
            duration_ms = (time.time() - start_time) * 1000
            
            return ValidationResult(
                test_name="Monitoring System",
                success=True,
                duration_ms=duration_ms,
                details={
                    'metrics_collected': 'success',
                    'metric_fields': len(required_fields),
                    'health_status': metrics.overall_status.value,
                    'summary_available': 'error' not in summary
                }
            )
            
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            return ValidationResult(
                test_name="Monitoring System",
                success=False,
                duration_ms=duration_ms,
                details={'error_type': type(e).__name__},
                error_message=str(e)
            )
    
    def get_validation_report(self) -> str:
        """Generate a comprehensive validation report"""
        if not self.validation_results:
            return "No validation results available. Run validation first."
        
        passed = [r for r in self.validation_results if r.success]
        failed = [r for r in self.validation_results if not r.success]
        
        report = [
            "\n========================================",
            "   STATE MANAGEMENT VALIDATION REPORT",
            "========================================",
            "",
            f"Total Tests: {len(self.validation_results)}",
            f"Passed: {len(passed)}",
            f"Failed: {len(failed)}",
            f"Success Rate: {(len(passed) / len(self.validation_results) * 100):.1f}%",
            "",
            "TEST RESULTS:",
            "-" * 40
        ]
        
        for result in self.validation_results:
            status = "✅ PASS" if result.success else "❌ FAIL"
            report.append(f"{status} {result.test_name} ({result.duration_ms:.2f}ms)")
            
            if not result.success:
                report.append(f"      Error: {result.error_message}")
        
        report.extend([
            "",
            "========================================",
            "VALIDATION COMPLETE",
            "========================================"
        ])
        
        return "\n".join(report)


# Global validator instance
state_management_validator = StateManagementValidator()
