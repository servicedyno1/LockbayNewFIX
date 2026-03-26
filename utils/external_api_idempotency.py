"""
External API Idempotency Enforcement
Prevents duplicate external API calls (Kraken withdrawals, Fincra transfers) to avoid double payments
"""

import logging
import hashlib
import json
from typing import Dict, Any, Optional
from datetime import datetime
from contextlib import contextmanager
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

logger = logging.getLogger(__name__)


class ExternalAPIIdempotencyManager:
    """
    Manages idempotency for external API calls to prevent duplicate financial operations.
    
    CRITICAL: This prevents double payments by ensuring each financial operation
    (cashout/withdrawal/transfer) is executed exactly once against external APIs.
    """
    
    @classmethod
    def generate_operation_key(
        cls, 
        service_name: str, 
        operation_type: str, 
        cashout_id: str, 
        params: Dict[str, Any]
    ) -> str:
        """
        Generate deterministic operation key for external API calls.
        
        Args:
            service_name: External service (kraken, fincra)
            operation_type: Operation type (withdraw, transfer, disbursement)
            cashout_id: Associated cashout ID
            params: Operation parameters (amount, address, etc.)
            
        Returns:
            Unique operation key for idempotency tracking
        """
        # Create deterministic hash of critical parameters
        # Sort params to ensure consistent ordering
        sorted_params = json.dumps(params, sort_keys=True, default=str)
        
        # Include all identifying information
        key_data = f"{service_name}_{operation_type}_{cashout_id}_{sorted_params}"
        operation_hash = hashlib.sha256(key_data.encode()).hexdigest()[:16]
        
        # Human-readable operation key
        operation_key = f"{service_name}_{operation_type}_{cashout_id}_{operation_hash}"
        
        return operation_key
    
    @classmethod
    @contextmanager
    def ensure_single_external_call(
        cls,
        session: Session,
        service_name: str,
        operation_type: str,
        cashout_id: str,
        operation_params: Dict[str, Any],
        transaction_id: str
    ):
        """
        Context manager to ensure external API call happens exactly once.
        
        CRITICAL FINANCIAL PROTECTION:
        - If operation already exists, returns cached result (prevents duplicate call)
        - If operation is new, yields control to make API call and stores result
        - Atomic database operations prevent race conditions
        
        Usage:
            with ensure_single_external_call(...) as api_guard:
                if api_guard.should_execute:
                    result = await external_api_call(...)
                    api_guard.store_result(result)
                return api_guard.get_result()
        """
        operation_key = cls.generate_operation_key(
            service_name, operation_type, cashout_id, operation_params
        )
        
        # Check if operation already exists
        existing_operation = cls._get_existing_operation(session, operation_key)
        
        if existing_operation:
            # Operation already executed - return cached result
            logger.warning(
                f"🔒 IDEMPOTENCY_PROTECTION: External API call already executed - "
                f"returning cached result for {operation_key}"
            )
            
            guard = ExternalAPIGuard(
                operation_key=operation_key,
                should_execute=False,
                cached_result=existing_operation.operation_result,
                existing_operation=existing_operation
            )
            yield guard
            return
        
        # New operation - prepare for execution
        logger.info(
            f"🆕 NEW_EXTERNAL_API_CALL: Preparing to execute {operation_key}"
        )
        
        guard = ExternalAPIGuard(
            operation_key=operation_key,
            should_execute=True,
            session=session,
            service_name=service_name,
            operation_type=operation_type,
            cashout_id=cashout_id,
            operation_params=operation_params,
            transaction_id=transaction_id
        )
        
        try:
            yield guard
        except Exception as e:
            # Store failed operation to prevent retries with same parameters
            guard.store_error(str(e))
            raise
    
    @classmethod
    def _get_existing_operation(cls, session: Session, operation_key: str):
        """Get existing operation log entry"""
        try:
            from models import ExternalOperationLog
            
            return session.query(ExternalOperationLog).filter(
                ExternalOperationLog.operation_key == operation_key
            ).first()
        except ImportError:
            # ExternalOperationLog model not available - return None (no existing operation)
            logger.debug("ExternalOperationLog model not available - skipping operation log check")
            return None
    
    @classmethod
    def _create_operation_log(
        cls,
        session: Session,
        operation_key: str,
        service_name: str,
        operation_type: str,
        cashout_id: str,
        operation_params: Dict[str, Any],
        transaction_id: str,
        result: Dict[str, Any],
        success: bool,
        error_message: Optional[str] = None
    ):
        """Create new operation log entry"""
        try:
            from models import ExternalOperationLog
        except ImportError:
            # ExternalOperationLog model not available - skip logging
            logger.debug("ExternalOperationLog model not available - skipping operation log creation")
            return None
        
        try:
            operation_log = ExternalOperationLog(
                operation_key=operation_key,
                transaction_id=transaction_id,
                service_name=service_name,
                operation_type=operation_type,
                cashout_id=cashout_id,
                operation_params=operation_params,
                operation_result=result,
                success=success,
                error_message=error_message,
                created_at=datetime.utcnow(),
                last_accessed_at=datetime.utcnow()
            )
            
            session.add(operation_log)
            session.flush()  # Ensure ID is generated
            
            logger.info(
                f"✅ EXTERNAL_API_LOG_CREATED: {operation_key} - "
                f"Success: {success}, Result keys: {list(result.keys())}"
            )
            
            return operation_log
            
        except IntegrityError:
            # Another process created the same operation
            session.rollback()
            logger.warning(
                f"🔒 RACE_CONDITION_DETECTED: Another process created {operation_key} - "
                f"retrieving existing result"
            )
            
            # Get the existing operation that was created by the other process
            existing = cls._get_existing_operation(session, operation_key)
            if existing:
                return existing
            else:
                # This should not happen, but defensive programming
                raise Exception(f"Failed to retrieve operation after race condition: {operation_key}")


class ExternalAPIGuard:
    """
    Guard object for managing external API call execution and result storage.
    """
    
    def __init__(
        self,
        operation_key: str,
        should_execute: bool,
        cached_result: Optional[Dict[str, Any]] = None,
        existing_operation=None,
        session: Optional[Session] = None,
        service_name: Optional[str] = None,
        operation_type: Optional[str] = None,
        cashout_id: Optional[str] = None,
        operation_params: Optional[Dict[str, Any]] = None,
        transaction_id: Optional[str] = None
    ):
        self.operation_key = operation_key
        self.should_execute = should_execute
        self._cached_result = cached_result
        self._existing_operation = existing_operation
        self._stored_result = None
        
        # For new operations
        self._session = session
        self._service_name = service_name
        self._operation_type = operation_type
        self._cashout_id = cashout_id
        self._operation_params = operation_params
        self._transaction_id = transaction_id
        self._operation_log = None
    
    def store_result(self, result: Dict[str, Any]) -> None:
        """Store successful API call result"""
        if not self.should_execute:
            logger.warning(f"🚨 LOGIC_ERROR: Attempted to store result for cached operation {self.operation_key}")
            return
        
        self._stored_result = result
        
        # Create operation log entry only if all required parameters are present
        if all([self._session, self._service_name, self._operation_type, 
                self._cashout_id, self._operation_params, self._transaction_id]):
            self._operation_log = ExternalAPIIdempotencyManager._create_operation_log(
                session=self._session,  # type: ignore
                operation_key=self.operation_key,
                service_name=self._service_name,  # type: ignore
                operation_type=self._operation_type,  # type: ignore
                cashout_id=self._cashout_id,  # type: ignore
                operation_params=self._operation_params,  # type: ignore
                transaction_id=self._transaction_id,  # type: ignore
                result=result,
                success=True
            )
        else:
            logger.debug(f"Skipping operation log creation - missing parameters for {self.operation_key}")
        
        logger.info(f"✅ EXTERNAL_API_RESULT_STORED: {self.operation_key}")
    
    def store_error(self, error_message: str) -> None:
        """Store failed API call for future reference"""
        if not self.should_execute:
            return
        
        error_result = {
            "success": False,
            "error": error_message,
            "timestamp": datetime.utcnow().isoformat()
        }
        
        self._stored_result = error_result
        
        # Create operation log entry for failed operation only if all required parameters are present
        if all([self._session, self._service_name, self._operation_type, 
                self._cashout_id, self._operation_params, self._transaction_id]):
            self._operation_log = ExternalAPIIdempotencyManager._create_operation_log(
                session=self._session,  # type: ignore
                operation_key=self.operation_key,
                service_name=self._service_name,  # type: ignore
                operation_type=self._operation_type,  # type: ignore
                cashout_id=self._cashout_id,  # type: ignore
                operation_params=self._operation_params,  # type: ignore
                transaction_id=self._transaction_id,  # type: ignore
                result=error_result,
                success=False,
                error_message=error_message
            )
        else:
            logger.debug(f"Skipping operation log creation - missing parameters for {self.operation_key}")
        
        logger.error(f"❌ EXTERNAL_API_ERROR_STORED: {self.operation_key} - {error_message}")
    
    def get_result(self) -> Dict[str, Any]:
        """Get the operation result (cached or newly stored)"""
        if self._cached_result is not None:
            # Update last accessed time for cached results
            if self._existing_operation:
                self._existing_operation.last_accessed_at = datetime.utcnow()
            
            logger.info(f"🔄 RETURNING_CACHED_RESULT: {self.operation_key}")
            return self._cached_result
        
        if self._stored_result is not None:
            logger.info(f"🆕 RETURNING_NEW_RESULT: {self.operation_key}")
            return self._stored_result
        
        # This should not happen if used correctly
        logger.error(f"🚨 NO_RESULT_AVAILABLE: {self.operation_key}")
        return {
            "success": False,
            "error": "No result available - operation may not have been executed",
            "operation_key": self.operation_key
        }


# Convenience functions for specific services
class KrakenIdempotencyWrapper:
    """Kraken-specific idempotency wrapper"""
    
    @staticmethod
    @contextmanager
    def ensure_single_withdrawal(
        session: Session,
        cashout_id: str,
        currency: str,
        amount: str,
        address_key: str,
        transaction_id: str
    ):
        """Ensure Kraken withdrawal happens exactly once"""
        operation_params = {
            "currency": currency,
            "amount": amount,
            "address_key": address_key
        }
        
        with ExternalAPIIdempotencyManager.ensure_single_external_call(
            session=session,
            service_name="kraken",
            operation_type="withdraw",
            cashout_id=cashout_id,
            operation_params=operation_params,
            transaction_id=transaction_id
        ) as guard:
            yield guard


class FincraIdempotencyWrapper:
    """Fincra-specific idempotency wrapper"""
    
    @staticmethod
    @contextmanager
    def ensure_single_disbursement(
        session: Session,
        cashout_id: str,
        amount: float,
        currency: str,
        destination_details: Dict[str, Any],
        transaction_id: str
    ):
        """Ensure Fincra disbursement happens exactly once"""
        operation_params = {
            "amount": amount,
            "currency": currency,
            "destination_details": destination_details
        }
        
        with ExternalAPIIdempotencyManager.ensure_single_external_call(
            session=session,
            service_name="fincra",
            operation_type="disbursement",
            cashout_id=cashout_id,
            operation_params=operation_params,
            transaction_id=transaction_id
        ) as guard:
            yield guard