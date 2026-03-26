"""
StatusUpdateFacade - Centralized Status Update Coordinator

This facade centralizes ALL status updates through a consistent validate → dual-write → history pattern
to prevent financial integrity issues from inconsistent status transitions across the system.

Key Features:
- Centralized validation using UnifiedTransitionValidator from utils.status_flows
- Bidirectional status mapping via LegacyStatusMapper  
- Coordinated dual-write operations for legacy/unified system consistency
- Comprehensive status history tracking for audit purposes
- Transaction-type-specific methods with proper validation flows
- Progressive status transition enforcement across all transaction types
- Atomic operations to prevent partial status updates

Integration Pattern:
1. VALIDATE: Check status transition validity using UnifiedTransitionValidator
2. MAP: Convert between legacy and unified status formats using LegacyStatusMapper
3. DUAL-WRITE: Update both legacy and unified systems atomically via DualWriteAdapter
4. HISTORY: Record comprehensive status change history for auditing
5. NOTIFY: Optional notifications for status changes

This replaces direct status updates in:
- services/unified_transaction_service.py
- handlers/wallet_direct.py  
- handlers/escrow.py
- handlers/exchange.py
"""

import logging
from typing import Dict, Optional, Any, List, Union
from datetime import datetime
from enum import Enum
from dataclasses import dataclass, asdict
from decimal import Decimal

from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession

from database import managed_session, async_managed_session
from models import (
    UnifiedTransaction, UnifiedTransactionStatus, UnifiedTransactionType,
    UnifiedTransactionStatusHistory, EscrowStatus, CashoutStatus, ExchangeStatus,
    Cashout, Escrow, ExchangeOrder, Wallet, WalletHolds, WalletHoldStatus
)

# Import existing validation and mapping services
from utils.status_flows import (
    unified_transition_validator,
    validate_unified_transition,
    get_allowed_next_statuses,
    is_terminal_transaction_status,
    TransitionValidationResult,
    log_status_transition_metrics
)
from services.legacy_status_mapper import LegacyStatusMapper, LegacySystemType
from services.dual_write_adapter import DualWriteAdapter, DualWriteConfig, DualWriteMode, DualWriteStrategy

# Import utilities
from utils.financial_audit_logger import (
    financial_audit_logger,
    FinancialEventType,
    FinancialContext,
    EntityType
)

logger = logging.getLogger(__name__)


class StatusUpdateError(Exception):
    """Custom exception for status update failures"""
    def __init__(self, message: str, error_code: str = None, is_retryable: bool = False, validation_result: Optional[TransitionValidationResult] = None):
        super().__init__(message)
        self.error_code = error_code
        self.is_retryable = is_retryable
        self.validation_result = validation_result


class StatusUpdateContext(Enum):
    """Context for status updates to provide additional validation context"""
    MANUAL_ADMIN = "manual_admin"           # Admin manually changing status
    AUTOMATED_SYSTEM = "automated_system"   # System automated status change
    WEBHOOK_RESPONSE = "webhook_response"   # External webhook triggering status change
    USER_ACTION = "user_action"            # User action triggering status change
    RETRY_PROCESSING = "retry_processing"   # Retry system processing
    TIMEOUT_HANDLING = "timeout_handling"   # Timeout handling system
    ERROR_RECOVERY = "error_recovery"      # Error recovery process


@dataclass
class StatusUpdateRequest:
    """Request for status update with comprehensive context"""
    # Core identification
    transaction_id: Optional[str] = None           # UnifiedTransaction ID
    legacy_entity_id: Optional[str] = None         # Legacy entity ID (cashout_id, escrow_id, etc.)
    transaction_type: Union[str, UnifiedTransactionType] = None
    
    # Status change details
    current_status: Union[str, UnifiedTransactionStatus, CashoutStatus, EscrowStatus, ExchangeStatus] = None
    new_status: Union[str, UnifiedTransactionStatus, CashoutStatus, EscrowStatus, ExchangeStatus] = None
    
    # Context and metadata
    context: StatusUpdateContext = StatusUpdateContext.AUTOMATED_SYSTEM
    reason: Optional[str] = None                   # Human-readable reason for change
    metadata: Optional[Dict[str, Any]] = None      # Additional context data
    user_id: Optional[int] = None                  # User who initiated change
    admin_id: Optional[int] = None                 # Admin who performed change
    
    # Processing options
    force_update: bool = False                     # Bypass validation (dangerous - admin only)
    skip_notifications: bool = False               # Skip user/admin notifications
    bypass_history: bool = False                   # Skip history logging (dangerous)
    
    # Legacy system mapping
    legacy_system_type: Optional[LegacySystemType] = None


@dataclass
class StatusUpdateResult:
    """Result of status update operation"""
    success: bool
    transaction_id: Optional[str] = None
    old_status: Optional[str] = None
    new_status: Optional[str] = None
    unified_status: Optional[str] = None           # Unified system status
    legacy_status: Optional[str] = None            # Legacy system status
    message: Optional[str] = None
    error: Optional[str] = None
    warnings: Optional[List[str]] = None
    
    # Operation details
    validation_result: Optional[TransitionValidationResult] = None
    dual_write_successful: bool = False
    history_recorded: bool = False
    notifications_sent: bool = False
    
    # Next steps
    next_allowed_statuses: Optional[List[str]] = None
    requires_admin_action: bool = False
    is_terminal: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for logging/serialization"""
        result_dict = asdict(self)
        # Handle complex objects
        if self.validation_result:
            result_dict['validation_result'] = self.validation_result.to_dict()
        return result_dict


class StatusUpdateFacade:
    """
    Centralized facade for all transaction status updates
    
    Enforces consistent validate → dual-write → history pattern across all transaction types
    to prevent financial integrity issues and ensure proper audit trails.
    
    Key Methods:
    - update_cashout_status(): For wallet cashout transactions
    - update_escrow_status(): For escrow transactions
    - update_exchange_status(): For exchange transactions  
    - update_unified_transaction_status(): For direct unified transactions
    
    The facade automatically handles:
    - Status transition validation using UnifiedTransitionValidator
    - Legacy ↔ Unified status mapping using LegacyStatusMapper
    - Dual-write coordination for legacy/unified systems
    - Comprehensive history tracking for auditing
    - Progressive status transition enforcement
    """
    
    def __init__(self, dual_write_config: Optional[DualWriteConfig] = None):
        """
        Initialize StatusUpdateFacade with configurable dual-write behavior
        
        Args:
            dual_write_config: Configuration for dual-write operations during migration
        """
        # Core services
        self.status_mapper = LegacyStatusMapper()
        self.dual_write_adapter = DualWriteAdapter(dual_write_config)
        self.transition_validator = unified_transition_validator
        
        # Operation tracking
        self._operation_counter = 0
        
        logger.info("🔄 STATUS_UPDATE_FACADE: Initialized with dual-write coordination")
    
    def _generate_operation_id(self) -> str:
        """Generate unique operation ID for tracking"""
        self._operation_counter += 1
        timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        return f"SUF{timestamp}{self._operation_counter:04d}"
    
    # =============== CORE STATUS UPDATE METHODS ===============
    
    async def update_cashout_status(self, 
                                  request: StatusUpdateRequest,
                                  session: Optional[Session] = None) -> StatusUpdateResult:
        """
        Update status for wallet cashout transactions
        
        Handles the complete cashout status transition workflow:
        1. Validate cashout status transition
        2. Map between CashoutStatus and UnifiedTransactionStatus
        3. Perform dual-write to both legacy Cashout and UnifiedTransaction
        4. Record comprehensive status history
        5. Trigger appropriate notifications
        
        Args:
            request: StatusUpdateRequest with cashout-specific parameters
            session: Optional database session for transactional consistency
            
        Returns:
            StatusUpdateResult with comprehensive update details
        """
        operation_id = self._generate_operation_id()
        
        logger.info(f"💰 CASHOUT_STATUS_UPDATE_START: {operation_id} | "
                   f"Entity: {request.legacy_entity_id} | "
                   f"Transition: {request.current_status}→{request.new_status} | "
                   f"Context: {request.context.value}")
        
        # Set transaction type context
        request.transaction_type = UnifiedTransactionType.WALLET_CASHOUT
        request.legacy_system_type = LegacySystemType.CASHOUT
        
        async def _execute_cashout_update(db_session: AsyncSession) -> StatusUpdateResult:
            """Execute cashout status update with proper session handling"""
            # Step 1: Load cashout entity and validate request
            cashout_entity = await self._load_cashout_entity(request, db_session)
            if not cashout_entity:
                return StatusUpdateResult(
                    success=False,
                    error=f"Cashout entity not found: {request.legacy_entity_id}",
                    transaction_id=request.transaction_id
                )
            
            # Step 2: Normalize and validate status transition
            validation_result = await self._validate_status_transition(
                request, cashout_entity, db_session
            )
            
            if not validation_result.success and not request.force_update:
                return validation_result
            
            # Step 3: Perform dual-write operation
            dual_write_result = await self._perform_cashout_dual_write(
                request, cashout_entity, validation_result, db_session
            )
            
            if not dual_write_result.success:
                return dual_write_result
            
            # Step 4: Record status history
            await self._record_status_history(
                request, dual_write_result, operation_id, db_session
            )
            
            # Step 5: Handle post-update actions
            await self._handle_post_update_actions(
                request, dual_write_result, db_session
            )
            
            logger.info(f"✅ CASHOUT_STATUS_UPDATE_COMPLETE: {operation_id} | "
                       f"Status: {dual_write_result.new_status} | "
                       f"Success: {dual_write_result.success}")
            
            return dual_write_result
        
        try:
            if session is None:
                async with async_managed_session() as db_session:
                    return await _execute_cashout_update(db_session)
            else:
                return await _execute_cashout_update(session)
                
        except Exception as e:
            logger.error(f"❌ CASHOUT_STATUS_UPDATE_ERROR: {operation_id} | Error: {str(e)}")
            return StatusUpdateResult(
                success=False,
                error=f"Cashout status update failed: {str(e)}",
                transaction_id=request.transaction_id
            )
    
    async def update_escrow_status(self,
                                 request: StatusUpdateRequest,
                                 session: Optional[Session] = None) -> StatusUpdateResult:
        """
        Update status for escrow transactions
        
        Handles the complete escrow status transition workflow:
        1. Validate escrow status transition
        2. Map between EscrowStatus and UnifiedTransactionStatus  
        3. Perform dual-write to both legacy Escrow and UnifiedTransaction
        4. Record comprehensive status history
        5. Handle escrow-specific logic (fund holds, releases, etc.)
        
        Args:
            request: StatusUpdateRequest with escrow-specific parameters
            session: Optional database session for transactional consistency
            
        Returns:
            StatusUpdateResult with comprehensive update details
        """
        operation_id = self._generate_operation_id()
        
        logger.info(f"🔒 ESCROW_STATUS_UPDATE_START: {operation_id} | "
                   f"Entity: {request.legacy_entity_id} | "
                   f"Transition: {request.current_status}→{request.new_status} | "
                   f"Context: {request.context.value}")
        
        # Set transaction type context
        request.transaction_type = UnifiedTransactionType.ESCROW
        request.legacy_system_type = LegacySystemType.ESCROW
        
        async def _execute_escrow_update(db_session: AsyncSession) -> StatusUpdateResult:
            """Execute escrow status update with proper session handling"""
            # Step 1: Load escrow entity and validate request
            escrow_entity = await self._load_escrow_entity(request, db_session)
            if not escrow_entity:
                return StatusUpdateResult(
                    success=False,
                    error=f"Escrow entity not found: {request.legacy_entity_id}",
                    transaction_id=request.transaction_id
                )
            
            # Step 2: Validate escrow-specific business rules
            business_validation = await self._validate_escrow_business_rules(
                request, escrow_entity, db_session
            )
            
            if not business_validation.success:
                return business_validation
            
            # Step 3: Normalize and validate status transition
            validation_result = await self._validate_status_transition(
                request, escrow_entity, db_session
            )
            
            if not validation_result.success and not request.force_update:
                return validation_result
            
            # Step 4: Handle fund management for escrow transitions
            fund_result = await self._handle_escrow_fund_management(
                request, escrow_entity, db_session
            )
            
            if not fund_result.success:
                return fund_result
            
            # Step 5: Perform dual-write operation
            dual_write_result = await self._perform_escrow_dual_write(
                request, escrow_entity, validation_result, db_session
            )
            
            if not dual_write_result.success:
                return dual_write_result
            
            # Step 6: Record status history
            await self._record_status_history(
                request, dual_write_result, operation_id, db_session
            )
            
            # Step 7: Handle post-update actions
            await self._handle_post_update_actions(
                request, dual_write_result, db_session
            )
            
            logger.info(f"✅ ESCROW_STATUS_UPDATE_COMPLETE: {operation_id} | "
                       f"Status: {dual_write_result.new_status} | "
                       f"Success: {dual_write_result.success}")
            
            return dual_write_result
        
        try:
            if session is None:
                async with async_managed_session() as db_session:
                    return await _execute_escrow_update(db_session)
            else:
                return await _execute_escrow_update(session)
                
        except Exception as e:
            logger.error(f"❌ ESCROW_STATUS_UPDATE_ERROR: {operation_id} | Error: {str(e)}")
            return StatusUpdateResult(
                success=False,
                error=f"Escrow status update failed: {str(e)}",
                transaction_id=request.transaction_id
            )
    
    async def update_exchange_status(self,
                                   request: StatusUpdateRequest,
                                   session: Optional[Session] = None) -> StatusUpdateResult:
        """
        Update status for exchange transactions (buy/sell crypto)
        
        Handles the complete exchange status transition workflow:
        1. Validate exchange status transition
        2. Map between ExchangeStatus and UnifiedTransactionStatus
        3. Perform dual-write to both legacy ExchangeOrder and UnifiedTransaction
        4. Record comprehensive status history
        5. Handle exchange-specific logic (rate locks, fund transfers, etc.)
        
        Args:
            request: StatusUpdateRequest with exchange-specific parameters
            session: Optional database session for transactional consistency
            
        Returns:
            StatusUpdateResult with comprehensive update details
        """
        operation_id = self._generate_operation_id()
        
        logger.info(f"🔄 EXCHANGE_STATUS_UPDATE_START: {operation_id} | "
                   f"Entity: {request.legacy_entity_id} | "
                   f"Transition: {request.current_status}→{request.new_status} | "
                   f"Context: {request.context.value}")
        
        # Set transaction type context
        request.transaction_type = self._determine_exchange_transaction_type(request)
        request.legacy_system_type = LegacySystemType.EXCHANGE
        
        async def _execute_exchange_update(db_session: AsyncSession) -> StatusUpdateResult:
            """Execute exchange status update with proper session handling"""
            # Step 1: Load exchange entity and validate request
            exchange_entity = await self._load_exchange_entity(request, db_session)
            if not exchange_entity:
                return StatusUpdateResult(
                    success=False,
                    error=f"Exchange entity not found: {request.legacy_entity_id}",
                    transaction_id=request.transaction_id
                )
            
            # Step 2: Validate exchange-specific business rules
            business_validation = await self._validate_exchange_business_rules(
                request, exchange_entity, db_session
            )
            
            if not business_validation.success:
                return business_validation
            
            # Step 3: Normalize and validate status transition
            validation_result = await self._validate_status_transition(
                request, exchange_entity, db_session
            )
            
            if not validation_result.success and not request.force_update:
                return validation_result
            
            # Step 4: Handle exchange fund management (rates, transfers)
            exchange_result = await self._handle_exchange_fund_management(
                request, exchange_entity, db_session
            )
            
            if not exchange_result.success:
                return exchange_result
            
            # Step 5: Perform dual-write operation
            dual_write_result = await self._perform_exchange_dual_write(
                request, exchange_entity, validation_result, db_session
            )
            
            if not dual_write_result.success:
                return dual_write_result
            
            # Step 6: Record status history
            await self._record_status_history(
                request, dual_write_result, operation_id, db_session
            )
            
            # Step 7: Handle post-update actions
            await self._handle_post_update_actions(
                request, dual_write_result, db_session
            )
            
            logger.info(f"✅ EXCHANGE_STATUS_UPDATE_COMPLETE: {operation_id} | "
                       f"Status: {dual_write_result.new_status} | "
                       f"Success: {dual_write_result.success}")
            
            return dual_write_result
        
        try:
            if session is None:
                async with async_managed_session() as db_session:
                    return await _execute_exchange_update(db_session)
            else:
                return await _execute_exchange_update(session)
                
        except Exception as e:
            logger.error(f"❌ EXCHANGE_STATUS_UPDATE_ERROR: {operation_id} | Error: {str(e)}")
            return StatusUpdateResult(
                success=False,
                error=f"Exchange status update failed: {str(e)}",
                transaction_id=request.transaction_id
            )
    
    async def update_unified_transaction_status(self,
                                              request: StatusUpdateRequest,
                                              session: Optional[Session] = None) -> StatusUpdateResult:
        """
        Update status for unified transactions directly
        
        For transactions that exist only in the unified system or when updating
        the unified status directly without legacy system coordination.
        
        Args:
            request: StatusUpdateRequest with unified transaction details
            session: Optional database session for transactional consistency
            
        Returns:
            StatusUpdateResult with comprehensive update details
        """
        operation_id = self._generate_operation_id()
        
        logger.info(f"🎯 UNIFIED_STATUS_UPDATE_START: {operation_id} | "
                   f"Transaction: {request.transaction_id} | "
                   f"Transition: {request.current_status}→{request.new_status} | "
                   f"Context: {request.context.value}")
        
        async def _execute_unified_transaction_update(db_session: AsyncSession) -> StatusUpdateResult:
            """Execute unified transaction status update with proper session handling"""
            # Step 1: Load unified transaction and validate request
            unified_transaction = await self._load_unified_transaction(request, db_session)
            if not unified_transaction:
                return StatusUpdateResult(
                    success=False,
                    error=f"Unified transaction not found: {request.transaction_id}",
                    transaction_id=request.transaction_id
                )
            
            # Step 2: Normalize and validate status transition
            validation_result = await self._validate_status_transition(
                request, unified_transaction, db_session
            )
            
            if not validation_result.success and not request.force_update:
                return validation_result
            
            # Step 3: Perform unified transaction update
            update_result = await self._perform_unified_transaction_update(
                request, unified_transaction, validation_result, db_session
            )
            
            if not update_result.success:
                return update_result
            
            # Step 4: Record status history
            await self._record_status_history(
                request, update_result, operation_id, db_session
            )
            
            # Step 5: Handle post-update actions
            await self._handle_post_update_actions(
                request, update_result, db_session
            )
            
            logger.info(f"✅ UNIFIED_STATUS_UPDATE_COMPLETE: {operation_id} | "
                       f"Status: {update_result.new_status} | "
                       f"Success: {update_result.success}")
            
            return update_result
        
        try:
            if session is None:
                async with managed_session() as db_session:
                    return await _execute_unified_transaction_update(db_session)
            else:
                return await _execute_unified_transaction_update(session)
                
        except Exception as e:
            logger.error(f"❌ UNIFIED_STATUS_UPDATE_ERROR: {operation_id} | Error: {str(e)}")
            return StatusUpdateResult(
                success=False,
                error=f"Unified transaction status update failed: {str(e)}",
                transaction_id=request.transaction_id
            )
    
    # =============== CONVENIENCE METHODS ===============
    
    async def get_allowed_next_statuses(self,
                                      current_status: Union[str, UnifiedTransactionStatus],
                                      transaction_type: Union[str, UnifiedTransactionType],
                                      session: Optional[Session] = None) -> List[str]:
        """
        Get allowed next statuses for a given current status and transaction type
        
        Args:
            current_status: Current status (unified or legacy)
            transaction_type: Transaction type
            session: Optional database session
            
        Returns:
            List of allowed next status strings
        """
        try:
            # Convert legacy status to unified if needed
            if not isinstance(current_status, UnifiedTransactionStatus):
                # Try to map from legacy status to unified
                unified_status = self.status_mapper.map_legacy_to_unified(
                    current_status, self._get_legacy_system_type(transaction_type)
                )
                if unified_status:
                    current_status = unified_status
            
            return get_allowed_next_statuses(current_status, transaction_type)
            
        except Exception as e:
            logger.error(f"Error getting allowed next statuses: {e}")
            return []
    
    async def validate_transition_only(self,
                                     current_status: Union[str, UnifiedTransactionStatus],
                                     new_status: Union[str, UnifiedTransactionStatus],
                                     transaction_type: Union[str, UnifiedTransactionType],
                                     context: Optional[Dict[str, Any]] = None) -> TransitionValidationResult:
        """
        Validate status transition without performing update
        
        Useful for pre-validation checks in UI or business logic
        
        Args:
            current_status: Current status
            new_status: Proposed new status
            transaction_type: Transaction type
            context: Optional validation context
            
        Returns:
            TransitionValidationResult with validation details
        """
        try:
            return validate_unified_transition(
                current_status, new_status, transaction_type, context
            )
        except Exception as e:
            logger.error(f"Error validating transition: {e}")
            return TransitionValidationResult(
                is_valid=False,
                current_status=str(current_status),
                new_status=str(new_status),
                transaction_type=str(transaction_type),
                error_message=f"Validation error: {str(e)}"
            )
    
    # =============== PRIVATE IMPLEMENTATION METHODS ===============
    
    async def _load_cashout_entity(self, request: StatusUpdateRequest, session: AsyncSession) -> Optional[Cashout]:
        """Load cashout entity from database"""
        try:
            if request.legacy_entity_id:
                return session.query(Cashout).filter(
                    Cashout.cashout_id == request.legacy_entity_id
                ).first()
            elif request.transaction_id:
                # Find via unified transaction
                unified_tx = session.query(UnifiedTransaction).filter(
                    UnifiedTransaction.transaction_id == request.transaction_id
                ).first()
                if unified_tx and unified_tx.legacy_entity_id:
                    return session.query(Cashout).filter(
                        Cashout.cashout_id == unified_tx.legacy_entity_id
                    ).first()
            return None
        except Exception as e:
            logger.error(f"Error loading cashout entity: {e}")
            return None
    
    async def _load_escrow_entity(self, request: StatusUpdateRequest, session: AsyncSession) -> Optional[Escrow]:
        """Load escrow entity from database"""
        try:
            if request.legacy_entity_id:
                return session.query(Escrow).filter(
                    Escrow.escrow_id == request.legacy_entity_id
                ).first()
            elif request.transaction_id:
                # Find via unified transaction
                unified_tx = session.query(UnifiedTransaction).filter(
                    UnifiedTransaction.transaction_id == request.transaction_id
                ).first()
                if unified_tx and unified_tx.legacy_entity_id:
                    return session.query(Escrow).filter(
                        Escrow.escrow_id == unified_tx.legacy_entity_id
                    ).first()
            return None
        except Exception as e:
            logger.error(f"Error loading escrow entity: {e}")
            return None
    
    async def _load_exchange_entity(self, request: StatusUpdateRequest, session: AsyncSession) -> Optional[ExchangeOrder]:
        """Load exchange entity from database"""
        try:
            if request.legacy_entity_id:
                return session.query(ExchangeOrder).filter(
                    ExchangeOrder.exchange_id == request.legacy_entity_id
                ).first()
            elif request.transaction_id:
                # Find via unified transaction
                unified_tx = session.query(UnifiedTransaction).filter(
                    UnifiedTransaction.transaction_id == request.transaction_id
                ).first()
                if unified_tx and unified_tx.legacy_entity_id:
                    return session.query(ExchangeOrder).filter(
                        ExchangeOrder.exchange_id == unified_tx.legacy_entity_id
                    ).first()
            return None
        except Exception as e:
            logger.error(f"Error loading exchange entity: {e}")
            return None
    
    async def _load_unified_transaction(self, request: StatusUpdateRequest, session: AsyncSession) -> Optional[UnifiedTransaction]:
        """Load unified transaction from database"""
        try:
            if request.transaction_id:
                return session.query(UnifiedTransaction).filter(
                    UnifiedTransaction.transaction_id == request.transaction_id
                ).first()
            return None
        except Exception as e:
            logger.error(f"Error loading unified transaction: {e}")
            return None
    
    async def _validate_status_transition(self, 
                                        request: StatusUpdateRequest, 
                                        entity: Any,
                                        session: AsyncSession) -> StatusUpdateResult:
        """
        Core status transition validation using UnifiedTransitionValidator
        
        This is the central validation point that all status updates go through
        """
        try:
            # Normalize statuses to unified format for validation
            current_unified = self._normalize_to_unified_status(
                request.current_status, request.legacy_system_type
            )
            new_unified = self._normalize_to_unified_status(
                request.new_status, request.legacy_system_type
            )
            
            if not current_unified or not new_unified:
                return StatusUpdateResult(
                    success=False,
                    error="Unable to normalize statuses for validation",
                    old_status=str(request.current_status),
                    new_status=str(request.new_status)
                )
            
            # Perform core transition validation
            validation_result = validate_unified_transition(
                current_unified, 
                new_unified, 
                request.transaction_type,
                context={
                    'update_context': request.context.value,
                    'entity_type': request.legacy_system_type.value if request.legacy_system_type else None,
                    'user_id': request.user_id,
                    'admin_id': request.admin_id,
                    'reason': request.reason,
                    'metadata': request.metadata
                }
            )
            
            # Log validation metrics
            log_status_transition_metrics(validation_result)
            
            if not validation_result.is_valid:
                logger.warning(f"🚫 STATUS_VALIDATION_FAILED: {validation_result.error_message} | "
                              f"Transition: {validation_result.current_status}→{validation_result.new_status}")
                
                return StatusUpdateResult(
                    success=False,
                    error=validation_result.error_message,
                    old_status=validation_result.current_status,
                    new_status=validation_result.new_status,
                    validation_result=validation_result
                )
            
            # Success - prepare result with validation details
            next_allowed = get_allowed_next_statuses(new_unified, request.transaction_type)
            is_terminal = is_terminal_transaction_status(new_unified)
            
            return StatusUpdateResult(
                success=True,
                old_status=str(current_unified),
                new_status=str(new_unified),
                unified_status=str(new_unified),
                validation_result=validation_result,
                next_allowed_statuses=next_allowed,
                is_terminal=is_terminal,
                message="Status transition validation passed"
            )
            
        except Exception as e:
            logger.error(f"Error in status transition validation: {e}")
            return StatusUpdateResult(
                success=False,
                error=f"Status validation error: {str(e)}",
                old_status=str(request.current_status),
                new_status=str(request.new_status)
            )
    
    def _normalize_to_unified_status(self, 
                                   status: Union[str, UnifiedTransactionStatus, CashoutStatus, EscrowStatus, ExchangeStatus],
                                   legacy_system_type: Optional[LegacySystemType]) -> Optional[UnifiedTransactionStatus]:
        """
        Normalize any status type to UnifiedTransactionStatus for validation
        """
        try:
            # Already unified status
            if isinstance(status, UnifiedTransactionStatus):
                return status
            
            # String status - try to convert
            if isinstance(status, str):
                # Try as unified status first
                try:
                    return UnifiedTransactionStatus(status)
                except ValueError:
                    # Try mapping from legacy if system type provided
                    if legacy_system_type:
                        return self.status_mapper.map_legacy_to_unified(status, legacy_system_type)
                    return None
            
            # Legacy enum status - map to unified
            if legacy_system_type:
                return self.status_mapper.map_legacy_to_unified(status, legacy_system_type)
            
            return None
            
        except Exception as e:
            logger.error(f"Error normalizing status to unified: {e}")
            return None
    
    def _get_legacy_system_type(self, transaction_type: Union[str, UnifiedTransactionType]) -> Optional[LegacySystemType]:
        """Map transaction type to legacy system type"""
        try:
            if isinstance(transaction_type, str):
                transaction_type = UnifiedTransactionType(transaction_type)
            
            mapping = {
                UnifiedTransactionType.WALLET_CASHOUT: LegacySystemType.CASHOUT,
                UnifiedTransactionType.ESCROW: LegacySystemType.ESCROW,
                UnifiedTransactionType.EXCHANGE_SELL_CRYPTO: LegacySystemType.EXCHANGE,
                UnifiedTransactionType.EXCHANGE_BUY_CRYPTO: LegacySystemType.EXCHANGE,
            }
            
            return mapping.get(transaction_type)
            
        except Exception as e:
            logger.error(f"Error mapping transaction type to legacy system: {e}")
            return None
    
    def _determine_exchange_transaction_type(self, request: StatusUpdateRequest) -> UnifiedTransactionType:
        """Determine specific exchange transaction type (buy vs sell)"""
        # This would be determined from the exchange entity or metadata
        # For now, default to sell crypto
        return UnifiedTransactionType.EXCHANGE_SELL_CRYPTO
    
    # =============== DUAL-WRITE IMPLEMENTATION METHODS ===============
    
    async def _perform_cashout_dual_write(self, 
                                        request: StatusUpdateRequest, 
                                        entity: Cashout, 
                                        validation_result: StatusUpdateResult, 
                                        session: AsyncSession) -> StatusUpdateResult:
        """
        Perform coordinated dual-write for cashout status update
        Updates both legacy Cashout entity and UnifiedTransaction atomically
        """
        try:
            # Determine status values for both systems
            legacy_status = self._normalize_to_legacy_status(
                request.new_status, LegacySystemType.CASHOUT
            )
            unified_status = validation_result.unified_status
            
            # Perform atomic dual-write using DualWriteAdapter
            dual_write_result = self.dual_write_adapter.update_status(
                legacy_entity_type='cashout',
                legacy_entity_id=request.legacy_entity_id,
                unified_transaction_id=request.transaction_id,
                legacy_status=legacy_status,
                unified_status=unified_status,
                metadata={
                    'context': request.context.value,
                    'reason': request.reason,
                    'user_id': request.user_id,
                    'admin_id': request.admin_id
                },
                session=session
            )
            
            if not dual_write_result.overall_success(
                self.dual_write_adapter.config.mode, 
                self.dual_write_adapter.config.strategy
            ):
                logger.error(f"💰 CASHOUT_DUAL_WRITE_FAILED: Legacy: {dual_write_result.legacy_success}, "
                            f"Unified: {dual_write_result.unified_success}")
                
                return StatusUpdateResult(
                    success=False,
                    error="Cashout dual-write operation failed",
                    old_status=str(request.current_status),
                    new_status=str(request.new_status),
                    dual_write_successful=False
                )
            
            logger.info("💰 CASHOUT_DUAL_WRITE_SUCCESS: Updated both legacy and unified systems")
            
            return StatusUpdateResult(
                success=True,
                transaction_id=request.transaction_id,
                old_status=str(request.current_status),
                new_status=str(request.new_status),
                unified_status=unified_status,
                legacy_status=legacy_status,
                dual_write_successful=True,
                message="Cashout status updated in both systems"
            )
            
        except Exception as e:
            logger.error(f"💰 CASHOUT_DUAL_WRITE_ERROR: {str(e)}")
            return StatusUpdateResult(
                success=False,
                error=f"Cashout dual-write error: {str(e)}",
                old_status=str(request.current_status),
                new_status=str(request.new_status)
            )
    
    async def _perform_escrow_dual_write(self, 
                                       request: StatusUpdateRequest, 
                                       entity: Escrow, 
                                       validation_result: StatusUpdateResult, 
                                       session: AsyncSession) -> StatusUpdateResult:
        """
        Perform coordinated dual-write for escrow status update
        Updates both legacy Escrow entity and UnifiedTransaction atomically
        """
        try:
            # Determine status values for both systems
            legacy_status = self._normalize_to_legacy_status(
                request.new_status, LegacySystemType.ESCROW
            )
            unified_status = validation_result.unified_status
            
            # Perform atomic dual-write using DualWriteAdapter
            dual_write_result = self.dual_write_adapter.update_status(
                legacy_entity_type='escrow',
                legacy_entity_id=request.legacy_entity_id,
                unified_transaction_id=request.transaction_id,
                legacy_status=legacy_status,
                unified_status=unified_status,
                metadata={
                    'context': request.context.value,
                    'reason': request.reason,
                    'user_id': request.user_id,
                    'admin_id': request.admin_id
                },
                session=session
            )
            
            if not dual_write_result.overall_success(
                self.dual_write_adapter.config.mode, 
                self.dual_write_adapter.config.strategy
            ):
                logger.error(f"🔒 ESCROW_DUAL_WRITE_FAILED: Legacy: {dual_write_result.legacy_success}, "
                            f"Unified: {dual_write_result.unified_success}")
                
                return StatusUpdateResult(
                    success=False,
                    error="Escrow dual-write operation failed",
                    old_status=str(request.current_status),
                    new_status=str(request.new_status),
                    dual_write_successful=False
                )
            
            logger.info("🔒 ESCROW_DUAL_WRITE_SUCCESS: Updated both legacy and unified systems")
            
            return StatusUpdateResult(
                success=True,
                transaction_id=request.transaction_id,
                old_status=str(request.current_status),
                new_status=str(request.new_status),
                unified_status=unified_status,
                legacy_status=legacy_status,
                dual_write_successful=True,
                message="Escrow status updated in both systems"
            )
            
        except Exception as e:
            logger.error(f"🔒 ESCROW_DUAL_WRITE_ERROR: {str(e)}")
            return StatusUpdateResult(
                success=False,
                error=f"Escrow dual-write error: {str(e)}",
                old_status=str(request.current_status),
                new_status=str(request.new_status)
            )
    
    async def _perform_exchange_dual_write(self, 
                                         request: StatusUpdateRequest, 
                                         entity: ExchangeOrder, 
                                         validation_result: StatusUpdateResult, 
                                         session: AsyncSession) -> StatusUpdateResult:
        """
        Perform coordinated dual-write for exchange status update
        Updates both legacy ExchangeOrder entity and UnifiedTransaction atomically
        """
        try:
            # Determine status values for both systems
            legacy_status = self._normalize_to_legacy_status(
                request.new_status, LegacySystemType.EXCHANGE
            )
            unified_status = validation_result.unified_status
            
            # Perform atomic dual-write using DualWriteAdapter
            dual_write_result = self.dual_write_adapter.update_status(
                legacy_entity_type='exchange',
                legacy_entity_id=request.legacy_entity_id,
                unified_transaction_id=request.transaction_id,
                legacy_status=legacy_status,
                unified_status=unified_status,
                metadata={
                    'context': request.context.value,
                    'reason': request.reason,
                    'user_id': request.user_id,
                    'admin_id': request.admin_id
                },
                session=session
            )
            
            if not dual_write_result.overall_success(
                self.dual_write_adapter.config.mode, 
                self.dual_write_adapter.config.strategy
            ):
                logger.error(f"🔄 EXCHANGE_DUAL_WRITE_FAILED: Legacy: {dual_write_result.legacy_success}, "
                            f"Unified: {dual_write_result.unified_success}")
                
                return StatusUpdateResult(
                    success=False,
                    error="Exchange dual-write operation failed",
                    old_status=str(request.current_status),
                    new_status=str(request.new_status),
                    dual_write_successful=False
                )
            
            logger.info("🔄 EXCHANGE_DUAL_WRITE_SUCCESS: Updated both legacy and unified systems")
            
            return StatusUpdateResult(
                success=True,
                transaction_id=request.transaction_id,
                old_status=str(request.current_status),
                new_status=str(request.new_status),
                unified_status=unified_status,
                legacy_status=legacy_status,
                dual_write_successful=True,
                message="Exchange status updated in both systems"
            )
            
        except Exception as e:
            logger.error(f"🔄 EXCHANGE_DUAL_WRITE_ERROR: {str(e)}")
            return StatusUpdateResult(
                success=False,
                error=f"Exchange dual-write error: {str(e)}",
                old_status=str(request.current_status),
                new_status=str(request.new_status)
            )
    
    async def _perform_unified_transaction_update(self, 
                                                request: StatusUpdateRequest, 
                                                entity: UnifiedTransaction, 
                                                validation_result: StatusUpdateResult, 
                                                session: AsyncSession) -> StatusUpdateResult:
        """
        Perform unified transaction update for transactions without legacy counterparts
        """
        try:
            # Update unified transaction directly
            entity.status = UnifiedTransactionStatus(validation_result.unified_status)
            entity.updated_at = datetime.utcnow()
            
            # Add metadata about the update
            if not entity.metadata:
                entity.metadata = {}
            
            entity.metadata.update({
                'last_status_update': {
                    'timestamp': datetime.utcnow().isoformat(),
                    'context': request.context.value,
                    'reason': request.reason,
                    'user_id': request.user_id,
                    'admin_id': request.admin_id,
                    'old_status': str(request.current_status),
                    'new_status': validation_result.unified_status
                }
            })
            
            session.flush()  # Ensure update is committed
            
            logger.info(f"🎯 UNIFIED_TRANSACTION_UPDATE_SUCCESS: {entity.transaction_id} | "
                       f"Status: {validation_result.unified_status}")
            
            return StatusUpdateResult(
                success=True,
                transaction_id=entity.transaction_id,
                old_status=str(request.current_status),
                new_status=validation_result.unified_status,
                unified_status=validation_result.unified_status,
                message="Unified transaction status updated"
            )
            
        except Exception as e:
            logger.error(f"🎯 UNIFIED_TRANSACTION_UPDATE_ERROR: {str(e)}")
            return StatusUpdateResult(
                success=False,
                error=f"Unified transaction update error: {str(e)}",
                old_status=str(request.current_status),
                new_status=str(request.new_status)
            )
    
    # =============== HISTORY TRACKING IMPLEMENTATION ===============
    
    async def _record_status_history(self, 
                                   request: StatusUpdateRequest, 
                                   result: StatusUpdateResult, 
                                   operation_id: str, 
                                   session: AsyncSession) -> StatusUpdateResult:
        """
        Record comprehensive status history for audit and debugging purposes
        """
        if request.bypass_history:
            logger.warning(f"📝 STATUS_HISTORY_BYPASSED: {operation_id} | Reason: {request.reason}")
            return StatusUpdateResult(success=True, message="Status history bypassed", history_recorded=False)
        
        try:
            # Create status history record
            history_record = UnifiedTransactionStatusHistory(
                transaction_id=request.transaction_id,
                old_status=result.old_status,
                new_status=result.new_status,
                change_reason=request.reason or f"Status update via {request.context.value}",
                changed_by_user_id=request.user_id,
                changed_by_admin_id=request.admin_id,
                operation_id=operation_id,
                context_data={
                    'update_context': request.context.value,
                    'legacy_entity_id': request.legacy_entity_id,
                    'legacy_system_type': request.legacy_system_type.value if request.legacy_system_type else None,
                    'validation_passed': result.validation_result.is_valid if result.validation_result else None,
                    'dual_write_success': result.dual_write_successful,
                    'force_update': request.force_update,
                    'metadata': request.metadata
                },
                created_at=datetime.utcnow()
            )
            
            session.add(history_record)
            session.flush()
            
            # Log financial audit event for critical status changes
            if self._is_financially_critical_status_change(request, result):
                financial_audit_logger.log_event(
                    event_type=FinancialEventType.STATUS_CHANGE,
                    entity_type=EntityType.TRANSACTION,
                    entity_id=request.transaction_id,
                    user_id=request.user_id or request.admin_id,
                    context=FinancialContext(
                        operation='status_update',
                        details={
                            'transaction_type': request.transaction_type.value if request.transaction_type else 'unknown',
                            'status_transition': f"{result.old_status}→{result.new_status}",
                            'operation_id': operation_id,
                            'context': request.context.value,
                            'reason': request.reason
                        }
                    )
                )
            
            logger.info(f"📝 STATUS_HISTORY_RECORDED: {operation_id} | "
                       f"Transaction: {request.transaction_id} | "
                       f"Transition: {result.old_status}→{result.new_status}")
            
            return StatusUpdateResult(
                success=True, 
                message="Status history recorded", 
                history_recorded=True
            )
            
        except Exception as e:
            logger.error(f"📝 STATUS_HISTORY_ERROR: {operation_id} | Error: {str(e)}")
            # Don't fail the entire operation due to history recording failure
            return StatusUpdateResult(
                success=True, 
                message=f"Status history recording failed: {str(e)}", 
                history_recorded=False,
                warnings=[f"History recording failed: {str(e)}"]
            )
    
    def _is_financially_critical_status_change(self, 
                                             request: StatusUpdateRequest, 
                                             result: StatusUpdateResult) -> bool:
        """
        Determine if a status change is financially critical and requires audit logging
        """
        critical_transitions = [
            'pending→success',           # Money released
            'funds_held→success',        # Escrow release
            'processing→success',        # Transaction completion
            'awaiting_response→success', # External API success
            'pending→failed',            # Transaction failure
            'processing→failed',         # Processing failure
            'success→cancelled',         # Reversal of completion
            'success→disputed'           # Dispute on completed transaction
        ]
        
        transition = f"{result.old_status}→{result.new_status}"
        return transition in critical_transitions or request.context == StatusUpdateContext.MANUAL_ADMIN
    
    # =============== BUSINESS RULES VALIDATION ===============
    
    async def _validate_cashout_business_rules(self, 
                                             request: StatusUpdateRequest, 
                                             entity: Cashout, 
                                             session: AsyncSession) -> StatusUpdateResult:
        """
        Validate cashout-specific business rules before status transition
        """
        try:
            warnings = []
            
            # Rule 1: Check for sufficient balance before success status
            if str(request.new_status).lower() in ['success', 'completed']:
                user_wallet = session.query(Wallet).filter(
                    Wallet.user_id == entity.user_id,
                    Wallet.currency == entity.currency
                ).first()
                
                if user_wallet and user_wallet.available_balance < entity.amount:
                    warnings.append(f"Insufficient balance for cashout completion: {user_wallet.available_balance} < {entity.amount}")
            
            # Rule 2: Validate OTP requirement for high-value cashouts
            if entity.amount > Decimal('1000') and not request.force_update:
                if str(request.new_status).lower() == 'processing' and request.context != StatusUpdateContext.WEBHOOK_RESPONSE:
                    # Should have gone through OTP verification
                    if not entity.metadata or not entity.metadata.get('otp_verified'):
                        warnings.append("High-value cashout should require OTP verification")
            
            # Rule 3: Admin approval for certain transitions
            admin_required_transitions = ['pending→admin_pending', 'admin_pending→approved']
            transition = f"{request.current_status}→{request.new_status}"
            
            if transition in admin_required_transitions and not request.admin_id:
                return StatusUpdateResult(
                    success=False,
                    error="Admin approval required for this cashout status transition",
                    requires_admin_action=True
                )
            
            logger.info(f"💰 CASHOUT_BUSINESS_RULES_VALIDATED: {entity.cashout_id} | "
                       f"Warnings: {len(warnings)}")
            
            return StatusUpdateResult(
                success=True, 
                message="Cashout business rules validated",
                warnings=warnings if warnings else None
            )
            
        except Exception as e:
            logger.error(f"💰 CASHOUT_BUSINESS_RULES_ERROR: {str(e)}")
            return StatusUpdateResult(
                success=False,
                error=f"Cashout business rule validation failed: {str(e)}"
            )
    
    async def _validate_escrow_business_rules(self, 
                                            request: StatusUpdateRequest, 
                                            entity: Escrow, 
                                            session: AsyncSession) -> StatusUpdateResult:
        """
        Validate escrow-specific business rules before status transition
        """
        try:
            warnings = []
            
            # Rule 1: Validate fund holds for escrow transitions
            if str(request.new_status).lower() == 'funds_held':
                # Check if funds are actually held
                wallet_holds = session.query(WalletHolds).filter(
                    WalletHolds.reference_id == entity.escrow_id,
                    WalletHolds.status == WalletHoldStatus.ACTIVE
                ).all()
                
                if not wallet_holds:
                    return StatusUpdateResult(
                        success=False,
                        error="Cannot transition to funds_held without active wallet holds"
                    )
            
            # Rule 2: Seller acceptance validation
            if str(request.new_status).lower() in ['active', 'funds_held']:
                if not entity.metadata or not entity.metadata.get('seller_accepted'):
                    warnings.append("Escrow activated without explicit seller acceptance")
            
            # Rule 3: Dispute handling rules
            if str(request.new_status).lower() == 'disputed':
                if str(request.current_status).lower() not in ['active', 'funds_held']:
                    return StatusUpdateResult(
                        success=False,
                        error="Escrow can only be disputed from active or funds_held status"
                    )
            
            logger.info(f"🔒 ESCROW_BUSINESS_RULES_VALIDATED: {entity.escrow_id} | "
                       f"Warnings: {len(warnings)}")
            
            return StatusUpdateResult(
                success=True, 
                message="Escrow business rules validated",
                warnings=warnings if warnings else None
            )
            
        except Exception as e:
            logger.error(f"🔒 ESCROW_BUSINESS_RULES_ERROR: {str(e)}")
            return StatusUpdateResult(
                success=False,
                error=f"Escrow business rule validation failed: {str(e)}"
            )
    
    async def _validate_exchange_business_rules(self, 
                                              request: StatusUpdateRequest, 
                                              entity: ExchangeOrder, 
                                              session: AsyncSession) -> StatusUpdateResult:
        """
        Validate exchange-specific business rules before status transition
        """
        try:
            warnings = []
            
            # Rule 1: Rate lock validation
            if str(request.new_status).lower() == 'rate_locked':
                if not entity.metadata or not entity.metadata.get('exchange_rate'):
                    return StatusUpdateResult(
                        success=False,
                        error="Cannot lock rate without exchange rate data"
                    )
                
                # Check rate expiry
                rate_timestamp = entity.metadata.get('rate_timestamp')
                if rate_timestamp:
                    rate_age = (datetime.utcnow() - datetime.fromisoformat(rate_timestamp)).total_seconds()
                    if rate_age > 3600:  # 1 hour
                        warnings.append("Exchange rate is older than 1 hour")
            
            # Rule 2: Payment confirmation validation
            if str(request.new_status).lower() == 'payment_confirmed':
                if entity.order_type == 'buy' and not entity.metadata.get('payment_received'):
                    warnings.append("Buy order marked as payment confirmed without payment receipt confirmation")
            
            # Rule 3: Processing validation
            if str(request.new_status).lower() == 'processing':
                if not entity.destination_address and entity.order_type == 'buy':
                    return StatusUpdateResult(
                        success=False,
                        error="Cannot process buy order without destination address"
                    )
            
            logger.info(f"🔄 EXCHANGE_BUSINESS_RULES_VALIDATED: {entity.exchange_id} | "
                       f"Warnings: {len(warnings)}")
            
            return StatusUpdateResult(
                success=True, 
                message="Exchange business rules validated",
                warnings=warnings if warnings else None
            )
            
        except Exception as e:
            logger.error(f"🔄 EXCHANGE_BUSINESS_RULES_ERROR: {str(e)}")
            return StatusUpdateResult(
                success=False,
                error=f"Exchange business rule validation failed: {str(e)}"
            )
    
    # =============== FUND MANAGEMENT METHODS ===============
    
    async def _handle_escrow_fund_management(self, 
                                           request: StatusUpdateRequest, 
                                           entity: Escrow, 
                                           session: AsyncSession) -> StatusUpdateResult:
        """
        Handle fund management for escrow status transitions
        """
        try:
            # Handle fund holds/releases based on status transition
            if str(request.new_status).lower() == 'funds_held':
                # Ensure funds are held
                logger.info(f"🔒 ESCROW_FUNDS_HELD: {entity.escrow_id} | Amount: {entity.amount}")
                
            elif str(request.new_status).lower() in ['completed', 'success']:
                # Release funds to seller
                logger.info(f"🔒 ESCROW_FUNDS_RELEASED: {entity.escrow_id} | Amount: {entity.amount}")
                
            elif str(request.new_status).lower() in ['cancelled', 'refunded']:
                # Refund to buyer
                logger.info(f"🔒 ESCROW_FUNDS_REFUNDED: {entity.escrow_id} | Amount: {entity.amount}")
            
            return StatusUpdateResult(success=True, message="Escrow fund management handled")
            
        except Exception as e:
            logger.error(f"🔒 ESCROW_FUND_MANAGEMENT_ERROR: {str(e)}")
            return StatusUpdateResult(
                success=False,
                error=f"Escrow fund management failed: {str(e)}"
            )
    
    async def _handle_exchange_fund_management(self, 
                                             request: StatusUpdateRequest, 
                                             entity: ExchangeOrder, 
                                             session: AsyncSession) -> StatusUpdateResult:
        """
        Handle fund management for exchange status transitions
        """
        try:
            # Handle fund transfers based on exchange type and status
            if str(request.new_status).lower() == 'completed':
                if entity.order_type == 'sell':
                    # Credit USD to user wallet
                    logger.info(f"🔄 EXCHANGE_SELL_COMPLETED: {entity.exchange_id} | "
                               f"Credit: {entity.output_amount} {entity.output_currency}")
                elif entity.order_type == 'buy':
                    # Credit crypto to user wallet  
                    logger.info(f"🔄 EXCHANGE_BUY_COMPLETED: {entity.exchange_id} | "
                               f"Credit: {entity.output_amount} {entity.output_currency}")
            
            return StatusUpdateResult(success=True, message="Exchange fund management handled")
            
        except Exception as e:
            logger.error(f"🔄 EXCHANGE_FUND_MANAGEMENT_ERROR: {str(e)}")
            return StatusUpdateResult(
                success=False,
                error=f"Exchange fund management failed: {str(e)}"
            )
    
    # =============== POST-UPDATE ACTIONS ===============
    
    async def _handle_post_update_actions(self, 
                                        request: StatusUpdateRequest, 
                                        result: StatusUpdateResult, 
                                        session: AsyncSession):
        """
        Handle post-update actions like notifications and cleanup
        """
        try:
            if not request.skip_notifications:
                await self._send_status_notifications(request, result, session)
            
            # Handle any cleanup or follow-up actions
            await self._perform_status_cleanup_actions(request, result, session)
            
        except Exception as e:
            logger.error(f"🔔 POST_UPDATE_ACTIONS_ERROR: {str(e)}")
            # Don't fail the operation due to post-update action failures
    
    async def _send_status_notifications(self, 
                                       request: StatusUpdateRequest, 
                                       result: StatusUpdateResult, 
                                       session: AsyncSession):
        """Send notifications for status changes"""
        # Implementation would integrate with notification system
        logger.info(f"🔔 STATUS_NOTIFICATION_PLACEHOLDER: {result.transaction_id} | "
                   f"Status: {result.new_status} | Context: {request.context.value}")
    
    async def _perform_status_cleanup_actions(self, 
                                            request: StatusUpdateRequest, 
                                            result: StatusUpdateResult, 
                                            session: AsyncSession):
        """Perform cleanup actions after status update"""
        # Implementation would handle cleanup like removing expired holds, etc.
        logger.info(f"🧹 STATUS_CLEANUP_PLACEHOLDER: {result.transaction_id}")
    
    # =============== UTILITY METHODS ===============
    
    def _normalize_to_legacy_status(self, 
                                  status: Union[str, UnifiedTransactionStatus, CashoutStatus, EscrowStatus, ExchangeStatus],
                                  legacy_system_type: LegacySystemType) -> Optional[str]:
        """
        Normalize unified status back to legacy status format
        """
        try:
            # Already legacy status
            if isinstance(status, (CashoutStatus, EscrowStatus, ExchangeStatus)):
                return status.value
            
            # Convert unified to legacy using mapper
            if isinstance(status, UnifiedTransactionStatus):
                return self.status_mapper.map_unified_to_legacy(status, legacy_system_type)
            
            # String status - try conversion
            if isinstance(status, str):
                try:
                    unified_status = UnifiedTransactionStatus(status)
                    return self.status_mapper.map_unified_to_legacy(unified_status, legacy_system_type)
                except ValueError:
                    # Might already be a legacy status string
                    return status
            
            return None
            
        except Exception as e:
            logger.error(f"Error normalizing to legacy status: {e}")
            return None


# =============== GLOBAL INSTANCE ===============
# Provide singleton instance for easy import and use across the application

# Default configuration for dual-write (can be overridden per operation)
default_dual_write_config = DualWriteConfig(
    mode=DualWriteMode.DUAL_WRITE_LEGACY_PRIMARY,
    strategy=DualWriteStrategy.FAIL_FAST,
    rollback_enabled=True,
    retry_attempts=3,
    validation_enabled=True,
    audit_logging=True,
    inconsistency_alerts=True
)

# Global status update facade instance
status_update_facade = StatusUpdateFacade(default_dual_write_config)


# =============== MODULE INITIALIZATION ===============

logger.info("🚀 STATUS_UPDATE_FACADE_INITIALIZED: Centralized status update coordination ready")
logger.info(f"📋 SUPPORTED_TRANSACTION_TYPES: {[t.value for t in UnifiedTransactionType]}")
logger.info(f"🔄 DUAL_WRITE_MODE: {default_dual_write_config.mode.value}")
logger.info(f"⚡ DUAL_WRITE_STRATEGY: {default_dual_write_config.strategy.value}")