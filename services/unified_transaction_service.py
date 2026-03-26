"""
Unified Transaction Service - Complete implementation for all transaction types

Handles all transaction flows according to document specification:
1. WALLET_CASHOUT: pending → [OTP] → processing → awaiting_response → success/failed  
2. EXCHANGE_SELL_CRYPTO: pending → awaiting_payment → payment_confirmed → processing → awaiting_response → success
3. EXCHANGE_BUY_CRYPTO: pending → awaiting_payment → payment_confirmed → processing → awaiting_response → success  
4. ESCROW: pending → payment_confirmed → awaiting_seller_acceptance → funds_held → release_pending → success

Key distinctions:
- External API calls: ONLY for wallet cashouts (Fincra NGN, Kraken crypto)
- Internal transfers: Escrow releases and exchange completions (direct wallet credit)
- OTP required: ONLY for wallet cashouts per ConditionalOTPService
- Retry logic: ONLY for external API failures, not internal transfers
"""

import logging
from typing import Dict, Any, Optional, List, Union
from decimal import Decimal
from datetime import datetime
from dataclasses import dataclass

from sqlalchemy.orm import Session

from database import managed_session, async_managed_session
from models import (
    UnifiedTransaction, UnifiedTransactionStatus, UnifiedTransactionType,
    UnifiedTransactionPriority,
    CashoutStatus, EscrowStatus, ExchangeStatus,
    Cashout, Escrow, ExchangeOrder
)

# Import existing services
from services.conditional_otp_service import ConditionalOTPService
from services.dual_write_adapter import DualWriteAdapter, DualWriteConfig, DualWriteMode, DualWriteStrategy

# Import utilities

# Import centralized status flows and validation
from utils.status_flows import (
    unified_transition_validator,
    unified_status_flows
)

# Import StatusUpdateFacade for centralized status management
from utils.status_update_facade import (
    StatusUpdateFacade,
    StatusUpdateRequest,
    StatusUpdateContext
)

logger = logging.getLogger(__name__)


class TransactionError(Exception):
    """Custom exception for transaction processing errors"""
    def __init__(self, message: str, error_code: str = None, is_retryable: bool = False):
        super().__init__(message)
        self.error_code = error_code
        self.is_retryable = is_retryable


class ExternalAPIError(TransactionError):
    """Exception for external API failures (retryable)"""
    def __init__(self, message: str, error_code: str = None):
        super().__init__(message, error_code, is_retryable=True)


class InternalTransferError(TransactionError):
    """Exception for internal transfer failures (non-retryable)"""
    def __init__(self, message: str, error_code: str = None):
        super().__init__(message, error_code, is_retryable=False)


@dataclass
class TransactionRequest:
    """Request for creating a new transaction"""
    transaction_type: Union[str, UnifiedTransactionType]
    user_id: int
    amount: Union[float, Decimal]
    currency: str = "USD"
    priority: Union[str, UnifiedTransactionPriority] = UnifiedTransactionPriority.NORMAL
    metadata: Optional[Dict[str, Any]] = None
    
    # External entity associations (for dual-write)
    legacy_entity_id: Optional[str] = None  # cashout_id, escrow_id, exchange_id
    
    # Transaction-specific parameters
    destination_address: Optional[str] = None      # For crypto cashouts
    destination_bank_account: Optional[str] = None # For NGN cashouts  
    exchange_rate: Optional[Decimal] = None        # For exchange operations
    escrow_details: Optional[Dict[str, Any]] = None # For escrow transactions


@dataclass
class TransactionResult:
    """Result of transaction processing"""
    success: bool
    transaction_id: Optional[str] = None
    status: Optional[str] = None
    message: Optional[str] = None
    error: Optional[str] = None
    requires_otp: bool = False
    next_action: Optional[str] = None
    processing_data: Optional[Dict[str, Any]] = None
    

@dataclass
class StatusTransitionResult:
    """Result of status transition"""
    success: bool
    old_status: str
    new_status: str
    message: Optional[str] = None
    error: Optional[str] = None
    next_action: Optional[str] = None


class UnifiedTransactionService:
    """
    Unified service for all transaction types with proper status transitions
    
    Features:
    - All 4 transaction types: WALLET_CASHOUT, EXCHANGE_SELL_CRYPTO, EXCHANGE_BUY_CRYPTO, ESCROW
    - Proper status transitions per document specification 
    - External API integration for wallet cashouts only
    - Internal transfers for escrow releases and exchange completions
    - OTP integration via ConditionalOTPService
    - Atomic fund management with existing wallet system
    - Dual-write support for legacy system migration
    - Comprehensive retry logic for external API failures only
    """
    
    def __init__(self, dual_write_config: Optional[DualWriteConfig] = None):
        self.dual_write_adapter = DualWriteAdapter(dual_write_config)
        self.otp_service = ConditionalOTPService()
        
        # Centralized status update coordination
        self.status_facade = StatusUpdateFacade(dual_write_config)
        
        # Retry configuration for external API calls only
        self.max_external_api_retries = 3
        self.retry_delay_seconds = [60, 300, 900]  # 1min, 5min, 15min
        
        # Use centralized status flow validation (replaces _initialize_status_flows)
        self.transition_validator = unified_transition_validator
        self.status_flows = unified_status_flows
    
    
    async def continue_external_processing(self, transaction_id: str) -> TransactionResult:
        """
        Continue external processing for transactions that failed and are being retried
        
        This method is called by the unified retry service to resume external API calls
        for transactions that previously failed with retryable errors.
        
        Args:
            transaction_id: ID of the transaction to continue processing
            
        Returns:
            TransactionResult indicating success or failure of retry attempt
        """
        start_time = datetime.utcnow()
        
        logger.info(f"🔄 CONTINUE_EXTERNAL_PROCESSING: Retrying transaction {transaction_id}")
        
        async with async_managed_session() as db:
            try:
                # Get the transaction
                tx = db.query(UnifiedTransaction).filter(
                    UnifiedTransaction.transaction_id == transaction_id
                ).first()
                
                if not tx:
                    return TransactionResult(
                        success=False,
                        error=f"Transaction {transaction_id} not found"
                    )
                
                # Verify transaction is in appropriate state for retry
                if tx.status not in [UnifiedTransactionStatus.AWAITING_RESPONSE.value, UnifiedTransactionStatus.PROCESSING.value]:
                    return TransactionResult(
                        success=False,
                        error=f"Transaction {transaction_id} is in status {tx.status}, cannot retry"
                    )
                
                # Only retry external API transactions (wallet cashouts)
                if tx.transaction_type != UnifiedTransactionType.WALLET_CASHOUT.value:
                    return TransactionResult(
                        success=False,
                        error=f"Transaction type {tx.transaction_type} does not support external API retry"
                    )
                
                # Determine provider for this transaction
                provider_result = self._determine_payout_provider(tx)
                if not provider_result["success"]:
                    return TransactionResult(
                        success=False,
                        error=f"Cannot determine payout provider: {provider_result['error']}"
                    )
                
                provider = provider_result["provider"]
                
                # Reset to processing status for retry attempt
                await self.transition_status(
                    transaction_id,
                    UnifiedTransactionStatus.PROCESSING,
                    f"Retry attempt for {provider} API",
                    session=db
                )
                
                # Execute external API call
                api_result = await self._execute_external_api_call(tx, provider, session=db)
                
                if api_result['success']:
                    # Success - transition to success and consume held funds
                    await self.transition_status(
                        transaction_id,
                        UnifiedTransactionStatus.SUCCESS,
                        f"External payout successful via {provider}",
                        metadata={
                            "provider": provider,
                            "external_reference": api_result.get('external_reference'),
                            "processing_time": api_result.get('processing_time')
                        },
                        session=db
                    )
                    
                    # Consume held funds permanently
                    consume_result = await self._consume_held_funds(transaction_id, db)
                    if not consume_result["success"]:
                        logger.error(f"Failed to consume held funds for successful retry {transaction_id}: {consume_result['error']}")
                    
                    processing_time = (datetime.utcnow() - start_time).total_seconds()
                    
                    logger.info(f"✅ RETRY_SUCCESS: {transaction_id} completed successfully via {provider} in {processing_time:.3f}s")
                    
                    return TransactionResult(
                        success=True,
                        transaction_id=transaction_id,
                        status=UnifiedTransactionStatus.SUCCESS.value,
                        message=f"External payout successful via {provider}",
                        processing_data={
                            "provider": provider,
                            "external_reference": api_result.get('external_reference'),
                            "processing_time": processing_time
                        }
                    )
                    
                else:
                    # Failed again - will be handled by retry service for potential additional retries
                    processing_time = (datetime.utcnow() - start_time).total_seconds()
                    
                    logger.warning(f"❌ RETRY_FAILED: {transaction_id} failed again via {provider} in {processing_time:.3f}s: {api_result['error']}")
                    
                    return TransactionResult(
                        success=False,
                        transaction_id=transaction_id,
                        status=tx.status,
                        error=api_result['error'],
                        processing_data={
                            "provider": provider,
                            "is_retryable": api_result.get('is_retryable', False),
                            "processing_time": processing_time
                        }
                    )
                    
            except Exception as e:
                logger.error(f"❌ CONTINUE_EXTERNAL_PROCESSING_ERROR: Failed to retry {transaction_id}: {e}")
                return TransactionResult(
                    success=False,
                    transaction_id=transaction_id,
                    error=f"Retry processing failed: {str(e)}"
                )
        
    # =============== STATUS TRANSITION METHODS (via StatusUpdateFacade) ===============
    
    async def transition_status(self,
                              transaction_id: str,
                              new_status: Union[str, UnifiedTransactionStatus],
                              reason: Optional[str] = None,
                              metadata: Optional[Dict[str, Any]] = None,
                              user_id: Optional[int] = None,
                              admin_id: Optional[int] = None,
                              context: StatusUpdateContext = StatusUpdateContext.AUTOMATED_SYSTEM,
                              session: Optional[Session] = None) -> StatusTransitionResult:
        """
        Transition transaction status using the centralized StatusUpdateFacade
        
        This is the primary method for all status transitions in the unified system.
        All status updates now go through the validate → dual-write → history pattern.
        
        Args:
            transaction_id: Unified transaction ID
            new_status: Target status to transition to
            reason: Human-readable reason for the change
            metadata: Additional metadata for the transition
            user_id: User who initiated the change (if applicable)
            admin_id: Admin who performed the change (if applicable)
            context: Context for the status update
            session: Optional database session for transactional consistency
            
        Returns:
            StatusTransitionResult with transition details
        """
        async def _execute_status_transition(db_session: Session) -> StatusTransitionResult:
            """Execute status transition with proper session handling"""
            # Load current transaction to determine current status
            transaction = db_session.query(UnifiedTransaction).filter(
                UnifiedTransaction.transaction_id == transaction_id
            ).first()
            
            if not transaction:
                return StatusTransitionResult(
                    success=False,
                    old_status="unknown",
                    new_status=str(new_status),
                    error=f"Transaction {transaction_id} not found"
                )
            
            current_status = transaction.status
            
            # Create status update request
            update_request = StatusUpdateRequest(
                transaction_id=transaction_id,
                transaction_type=transaction.transaction_type,
                current_status=current_status,
                new_status=new_status,
                context=context,
                reason=reason,
                metadata=metadata,
                user_id=user_id,
                admin_id=admin_id,
                legacy_entity_id=transaction.legacy_entity_id
            )
            
            # Use StatusUpdateFacade for unified transaction updates
            facade_result = await self.status_facade.update_unified_transaction_status(
                update_request, session=db_session
            )
            
            # Convert StatusUpdateResult to StatusTransitionResult
            return StatusTransitionResult(
                success=facade_result.success,
                old_status=facade_result.old_status or str(current_status),
                new_status=facade_result.new_status or str(new_status),
                message=facade_result.message,
                error=facade_result.error,
                next_action="check_terminal_status" if facade_result.is_terminal else None
            )
        
        try:
            if session is None:
                async with managed_session() as db_session:
                    return await _execute_status_transition(db_session)
            else:
                return await _execute_status_transition(session)
                
        except Exception as e:
            logger.error(f"🚨 TRANSITION_STATUS_ERROR: {transaction_id} | Error: {str(e)}")
            return StatusTransitionResult(
                success=False,
                old_status="unknown",
                new_status=str(new_status),
                error=f"Status transition failed: {str(e)}"
            )
    
    async def transition_cashout_status(self,
                                     cashout_id: str,
                                     new_status: Union[str, CashoutStatus],
                                     reason: Optional[str] = None,
                                     metadata: Optional[Dict[str, Any]] = None,
                                     user_id: Optional[int] = None,
                                     admin_id: Optional[int] = None,
                                     context: StatusUpdateContext = StatusUpdateContext.AUTOMATED_SYSTEM,
                                     session: Optional[Session] = None) -> StatusTransitionResult:
        """
        Transition cashout status using the centralized StatusUpdateFacade
        
        Args:
            cashout_id: Legacy cashout ID
            new_status: Target cashout status to transition to
            reason: Human-readable reason for the change
            metadata: Additional metadata for the transition
            user_id: User who initiated the change (if applicable)
            admin_id: Admin who performed the change (if applicable) 
            context: Context for the status update
            session: Optional database session for transactional consistency
            
        Returns:
            StatusTransitionResult with transition details
        """
        async def _execute_cashout_status_transition(db_session: Session) -> StatusTransitionResult:
            """Execute cashout status transition with proper session handling"""
            # Load current cashout to determine current status
            cashout = db_session.query(Cashout).filter(
                Cashout.cashout_id == cashout_id
            ).first()
            
            if not cashout:
                return StatusTransitionResult(
                    success=False,
                    old_status="unknown",
                    new_status=str(new_status),
                    error=f"Cashout {cashout_id} not found"
                )
            
            current_status = cashout.status
            
            # Create status update request
            update_request = StatusUpdateRequest(
                legacy_entity_id=cashout_id,
                transaction_type=UnifiedTransactionType.WALLET_CASHOUT,
                current_status=current_status,
                new_status=new_status,
                context=context,
                reason=reason,
                metadata=metadata,
                user_id=user_id,
                admin_id=admin_id
            )
            
            # Use StatusUpdateFacade for cashout updates
            facade_result = await self.status_facade.update_cashout_status(
                update_request, session=db_session
            )
                
            # Convert StatusUpdateResult to StatusTransitionResult
            return StatusTransitionResult(
                success=facade_result.success,
                old_status=facade_result.old_status or str(current_status),
                new_status=facade_result.new_status or str(new_status),
                message=facade_result.message,
                error=facade_result.error,
                next_action="check_terminal_status" if facade_result.is_terminal else None
            )
        
        try:
            if session is None:
                async with managed_session() as db_session:
                    return await _execute_cashout_status_transition(db_session)
            else:
                return await _execute_cashout_status_transition(session)
                
        except Exception as e:
            logger.error(f"🚨 TRANSITION_CASHOUT_STATUS_ERROR: {cashout_id} | Error: {str(e)}")
            return StatusTransitionResult(
                success=False,
                old_status="unknown",
                new_status=str(new_status),
                error=f"Cashout status transition failed: {str(e)}"
            )
    
    async def transition_escrow_status(self,
                                     escrow_id: str,
                                     new_status: Union[str, EscrowStatus],
                                     reason: Optional[str] = None,
                                     metadata: Optional[Dict[str, Any]] = None,
                                     user_id: Optional[int] = None,
                                     admin_id: Optional[int] = None,
                                     context: StatusUpdateContext = StatusUpdateContext.AUTOMATED_SYSTEM,
                                     session: Optional[Session] = None) -> StatusTransitionResult:
        """
        Transition escrow status using the centralized StatusUpdateFacade
        
        Args:
            escrow_id: Legacy escrow ID
            new_status: Target escrow status to transition to
            reason: Human-readable reason for the change
            metadata: Additional metadata for the transition
            user_id: User who initiated the change (if applicable)
            admin_id: Admin who performed the change (if applicable)
            context: Context for the status update
            session: Optional database session for transactional consistency
            
        Returns:
            StatusTransitionResult with transition details
        """
        async def _execute_escrow_status_transition(db_session: Session) -> StatusTransitionResult:
            """Execute escrow status transition with proper session handling"""
            # Load current escrow to determine current status
            escrow = db_session.query(Escrow).filter(
                Escrow.escrow_id == escrow_id
                ).first()
            
            if not escrow:
                return StatusTransitionResult(
                    success=False,
                    old_status="unknown",
                    new_status=str(new_status),
                    error=f"Escrow {escrow_id} not found"
                )
            
            current_status = escrow.status
            
            # Create status update request
            update_request = StatusUpdateRequest(
                legacy_entity_id=escrow_id,
                transaction_type=UnifiedTransactionType.ESCROW,
                current_status=current_status,
                new_status=new_status,
                context=context,
                reason=reason,
                metadata=metadata,
                user_id=user_id,
                admin_id=admin_id
            )
            
            # Use StatusUpdateFacade for escrow updates
            facade_result = await self.status_facade.update_escrow_status(
                update_request, session=db_session
            )
            
            # Convert StatusUpdateResult to StatusTransitionResult
            return StatusTransitionResult(
                success=facade_result.success,
                old_status=facade_result.old_status or str(current_status),
                new_status=facade_result.new_status or str(new_status),
                message=facade_result.message,
                error=facade_result.error,
                next_action="check_terminal_status" if facade_result.is_terminal else None
            )
        
        try:
            if session is None:
                async with managed_session() as db_session:
                    return await _execute_escrow_status_transition(db_session)
            else:
                return await _execute_escrow_status_transition(session)
                
        except Exception as e:
            logger.error(f"🚨 TRANSITION_ESCROW_STATUS_ERROR: {escrow_id} | Error: {str(e)}")
            return StatusTransitionResult(
                success=False,
                old_status="unknown",
                new_status=str(new_status),
                error=f"Escrow status transition failed: {str(e)}"
            )
    
    async def transition_exchange_status(self,
                                       exchange_id: str,
                                       new_status: Union[str, ExchangeStatus],
                                       reason: Optional[str] = None,
                                       metadata: Optional[Dict[str, Any]] = None,
                                       user_id: Optional[int] = None,
                                       admin_id: Optional[int] = None,
                                       context: StatusUpdateContext = StatusUpdateContext.AUTOMATED_SYSTEM,
                                       session: Optional[Session] = None) -> StatusTransitionResult:
        """
        Transition exchange status using the centralized StatusUpdateFacade
        
        Args:
            exchange_id: Legacy exchange ID
            new_status: Target exchange status to transition to
            reason: Human-readable reason for the change
            metadata: Additional metadata for the transition
            user_id: User who initiated the change (if applicable)
            admin_id: Admin who performed the change (if applicable)
            context: Context for the status update
            session: Optional database session for transactional consistency
            
        Returns:
            StatusTransitionResult with transition details
        """
        async def _execute_exchange_status_transition(db_session: Session) -> StatusTransitionResult:
            """Execute exchange status transition with proper session handling"""
            # Load current exchange to determine current status
            exchange = db_session.query(ExchangeOrder).filter(
                    ExchangeOrder.exchange_id == exchange_id
            ).first()
            
            if not exchange:
                return StatusTransitionResult(
                    success=False,
                    old_status="unknown",
                    new_status=str(new_status),
                    error=f"Exchange {exchange_id} not found"
                )
            
            current_status = exchange.status
            
            # Determine exchange transaction type
            transaction_type = (UnifiedTransactionType.EXCHANGE_BUY_CRYPTO 
                              if exchange.order_type == 'buy' 
                              else UnifiedTransactionType.EXCHANGE_SELL_CRYPTO)
            
            # Create status update request
            update_request = StatusUpdateRequest(
                legacy_entity_id=exchange_id,
                transaction_type=transaction_type,
                current_status=current_status,
                new_status=new_status,
                context=context,
                reason=reason,
                metadata=metadata,
                user_id=user_id,
                admin_id=admin_id
            )
            
            # Use StatusUpdateFacade for exchange updates
            facade_result = await self.status_facade.update_exchange_status(
                update_request, session=db_session
            )
            
            # Convert StatusUpdateResult to StatusTransitionResult
            return StatusTransitionResult(
                success=facade_result.success,
                old_status=facade_result.old_status or str(current_status),
                new_status=facade_result.new_status or str(new_status),
                message=facade_result.message,
                error=facade_result.error,
                next_action="check_terminal_status" if facade_result.is_terminal else None
            )
        
        try:
            if session is None:
                async with managed_session() as db_session:
                    return await _execute_exchange_status_transition(db_session)
            else:
                return await _execute_exchange_status_transition(session)
                
        except Exception as e:
            logger.error(f"🚨 TRANSITION_EXCHANGE_STATUS_ERROR: {exchange_id} | Error: {str(e)}")
            return StatusTransitionResult(
                success=False,
                old_status="unknown",
                new_status=str(new_status),
                error=f"Exchange status transition failed: {str(e)}"
            )
    
    # =============== HELPER METHODS ===============
    
    async def get_transaction_status(self, transaction_id: str) -> Optional[UnifiedTransactionStatus]:
        """Get current status of a unified transaction"""
        try:
            async with async_managed_session() as db:
                transaction = db.query(UnifiedTransaction).filter(
                    UnifiedTransaction.transaction_id == transaction_id
                ).first()
                return UnifiedTransactionStatus(transaction.status) if transaction else None
        except Exception as e:
            logger.error(f"Error getting transaction status: {e}")
            return None
    
    async def get_allowed_next_statuses_for_transaction(self, 
                                      transaction_id: str) -> List[str]:
        """Get allowed next statuses for a transaction"""
        try:
            async with async_managed_session() as db:
                transaction = db.query(UnifiedTransaction).filter(
                    UnifiedTransaction.transaction_id == transaction_id
                ).first()
                
                if not transaction:
                    return []
                
                return await self.status_facade.get_allowed_next_statuses(
                    transaction.status,
                    transaction.transaction_type
                )
        except Exception as e:
            logger.error(f"Error getting allowed next statuses: {e}")
            return []


# Factory function to create UnifiedTransactionService instances
def create_unified_transaction_service(dual_write_mode: Optional[DualWriteMode] = None) -> UnifiedTransactionService:
    """
    Factory function to create UnifiedTransactionService instances
    
    Args:
        dual_write_mode: Optional dual-write mode for legacy system migration
        
    Returns:
        Configured UnifiedTransactionService instance
    """
    dual_write_config = None
    if dual_write_mode:
        dual_write_config = DualWriteConfig(
            mode=dual_write_mode,
            strategy=DualWriteStrategy.UNIFIED_FIRST  # Default strategy
        )
    
    return UnifiedTransactionService(dual_write_config=dual_write_config)