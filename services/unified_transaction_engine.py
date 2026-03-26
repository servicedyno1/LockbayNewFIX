"""
Unified Transaction Engine (UTE) - Core Engine

The heart of LockBay's transaction processing system. Orchestrates all financial operations
through a unified state machine with saga support, outbox/inbox patterns, and provider abstraction.

Key Features:
- Transaction State Machine (INITIATED → PROCESSING → COMPLETED/FAILED)
- Saga Orchestration for complex multi-step workflows  
- Outbox Pattern for reliable event publishing
- Inbox Pattern for idempotent webhook processing
- Provider Abstraction for external services
- Comprehensive audit logging and observability
"""

import logging
import asyncio
import time
from typing import Dict, Any, Optional, List
from decimal import Decimal
from datetime import datetime
from dataclasses import dataclass

from sqlalchemy.orm import Session
from sqlalchemy import and_, or_

from database import managed_session
from models import (
    UnifiedTransaction, UnifiedTransactionStatus, UnifiedTransactionType,
    UnifiedTransactionPriority, UnifiedTransactionStatusHistory,
    OutboxEvent, OutboxEventStatus, InboxWebhook, InboxWebhookStatus,
    SagaStep, SagaStepStatus, TransactionEngineEvent
)
from utils.universal_id_generator import UniversalIDGenerator
from utils.unified_transaction_state_validator import (
    UnifiedTransactionStateValidator,
    StateTransitionError
)
from config import Config

# Import provider interfaces
from services.providers import BaseProvider

logger = logging.getLogger(__name__)

# Global UTE instance for easy access


class UTEError(Exception):
    """Base UTE error with context"""
    def __init__(self, message: str, error_code: str = None, is_retryable: bool = False):
        super().__init__(message)
        self.error_code = error_code
        self.is_retryable = is_retryable


class TransactionError(UTEError):
    """Transaction processing error"""
    pass


class SagaError(UTEError):
    """Saga orchestration error"""
    pass


@dataclass
class TransactionRequest:
    """Request to create a new transaction"""
    transaction_type: UnifiedTransactionType
    user_id: int
    amount: Decimal
    currency: str = "USD"
    priority: UnifiedTransactionPriority = UnifiedTransactionPriority.NORMAL
    metadata: Optional[Dict[str, Any]] = None
    
    # Optional associations
    legacy_entity_id: Optional[str] = None  # Link to existing cashout/escrow
    parent_transaction_id: Optional[str] = None  # For related transactions
    
    # Provider routing hints
    preferred_provider: Optional[str] = None
    provider_metadata: Optional[Dict[str, Any]] = None


@dataclass
class TransactionResult:
    """Result of transaction processing"""
    success: bool
    transaction_id: Optional[str] = None
    status: Optional[UnifiedTransactionStatus] = None
    message: Optional[str] = None
    error: Optional[str] = None
    error_code: Optional[str] = None
    is_retryable: bool = False
    saga_id: Optional[str] = None
    processing_data: Optional[Dict[str, Any]] = None


@dataclass  
class SagaDefinition:
    """Definition of a saga workflow"""
    saga_id: str
    saga_name: str
    transaction_id: str
    steps: List[Dict[str, Any]]
    compensation_enabled: bool = True
    timeout_seconds: int = 3600  # 1 hour default


class UnifiedTransactionEngine:
    """
    Core Unified Transaction Engine
    
    Orchestrates all financial transactions through a unified state machine
    with saga support, event sourcing, and provider abstraction.
    """
    
    def __init__(self):
        self.provider_registry: Dict[str, BaseProvider] = {}
        self.event_handlers: Dict[str, callable] = {}
        self.saga_handlers: Dict[str, callable] = {}
        
        # Configuration
        self.max_retry_attempts = getattr(Config, 'UTE_MAX_RETRY_ATTEMPTS', 3)
        self.saga_timeout_seconds = getattr(Config, 'UTE_SAGA_TIMEOUT', 3600)
        self.outbox_processing_batch_size = getattr(Config, 'UTE_OUTBOX_BATCH_SIZE', 50)
        
        logger.info("🚀 UTE Engine initialized")
    
    # =========================================================================
    # CORE TRANSACTION LIFECYCLE
    # =========================================================================
    
    async def create_transaction(self, request: TransactionRequest) -> TransactionResult:
        """
        Create a new transaction and initiate processing
        
        Args:
            request: Transaction creation request
            
        Returns:
            TransactionResult with creation outcome
        """
        start_time = time.time()
        transaction_id = UniversalIDGenerator.generate_transaction_id()
        
        log_context = {
            "transaction_id": transaction_id,
            "transaction_type": request.transaction_type.value,
            "user_id": request.user_id,
            "amount": float(request.amount),
            "currency": request.currency
        }
        
        logger.info("🚀 UTE_CREATE_TRANSACTION: Starting", extra=log_context)
        
        try:
            with managed_session() as db:
                # Create unified transaction record
                transaction = UnifiedTransaction(
                    transaction_id=transaction_id,
                    transaction_type=request.transaction_type.value,
                    status=UnifiedTransactionStatus.INITIATED.value,
                    priority=request.priority.value,
                    user_id=request.user_id,
                    amount=request.amount,
                    currency=request.currency,
                    metadata=request.metadata or {},
                    legacy_entity_id=request.legacy_entity_id,
                    parent_transaction_id=request.parent_transaction_id,
                    idempotency_key=f"{transaction_id}_{int(time.time())}",
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow()
                )
                
                db.add(transaction)
                
                # Create status history entry
                await self._record_status_change(
                    db, transaction_id, None, UnifiedTransactionStatus.INITIATED,
                    "Transaction created", request.metadata
                )
                
                # Create engine event
                await self._create_engine_event(
                    db, transaction_id, "transaction_created", "business",
                    {"transaction_type": request.transaction_type.value},
                    correlation_id=transaction_id
                )
                
                # Create outbox event for transaction creation
                await self._create_outbox_event(
                    db, "transaction_created", "transaction", transaction_id,
                    {
                        "transaction_id": transaction_id,
                        "transaction_type": request.transaction_type.value,
                        "user_id": request.user_id,
                        "amount": str(request.amount),
                        "currency": request.currency
                    },
                    transaction_id=transaction_id,
                    user_id=request.user_id
                )
                
                db.commit()
                
                processing_time = (time.time() - start_time) * 1000
                logger.info(f"✅ UTE_CREATE_SUCCESS: Transaction {transaction_id} created in {processing_time:.2f}ms", extra=log_context)
                
                # Initiate processing asynchronously
                asyncio.create_task(self._initiate_processing(transaction_id))
                
                return TransactionResult(
                    success=True,
                    transaction_id=transaction_id,
                    status=UnifiedTransactionStatus.INITIATED,
                    message="Transaction created successfully",
                    processing_data={"processing_time_ms": processing_time}
                )
                
        except Exception as e:
            processing_time = (time.time() - start_time) * 1000
            logger.error(f"❌ UTE_CREATE_ERROR: Failed to create transaction in {processing_time:.2f}ms: {e}", extra=log_context)
            
            return TransactionResult(
                success=False,
                error=f"Failed to create transaction: {str(e)}",
                error_code="UTE_CREATE_FAILED",
                is_retryable=True
            )
    
    async def _initiate_processing(self, transaction_id: str):
        """
        Initiate transaction processing after creation
        
        Args:
            transaction_id: ID of transaction to process
        """
        try:
            with managed_session() as db:
                transaction = db.query(UnifiedTransaction).filter(
                    UnifiedTransaction.transaction_id == transaction_id
                ).first()
                
                if not transaction:
                    logger.error(f"❌ UTE_PROCESS_ERROR: Transaction {transaction_id} not found")
                    return
                
                # Transition to processing status
                await self._transition_status(
                    db, transaction_id, UnifiedTransactionStatus.PROCESSING,
                    "Starting transaction processing"
                )
                
                # Determine if this transaction requires a saga
                if self._requires_saga(transaction.transaction_type):
                    saga_id = f"saga_{transaction_id}_{int(time.time())}"
                    await self._initiate_saga(db, transaction, saga_id)
                else:
                    await self._execute_simple_transaction(db, transaction)
                    
        except Exception as e:
            logger.error(f"❌ UTE_INITIATE_ERROR: Failed to initiate processing for {transaction_id}: {e}")
            
            with managed_session() as db:
                await self._transition_status(
                    db, transaction_id, UnifiedTransactionStatus.FAILED,
                    f"Failed to initiate processing: {str(e)}"
                )
    
    async def get_transaction_status(self, transaction_id: str) -> TransactionResult:
        """
        Get current status of a transaction
        
        Args:
            transaction_id: ID of transaction to check
            
        Returns:
            TransactionResult with current status
        """
        try:
            with managed_session() as db:
                transaction = db.query(UnifiedTransaction).filter(
                    UnifiedTransaction.transaction_id == transaction_id
                ).first()
                
                if not transaction:
                    return TransactionResult(
                        success=False,
                        error="Transaction not found",
                        error_code="UTE_TRANSACTION_NOT_FOUND"
                    )
                
                return TransactionResult(
                    success=True,
                    transaction_id=transaction_id,
                    status=UnifiedTransactionStatus(transaction.status),
                    message=f"Transaction status: {transaction.status}",
                    processing_data={
                        "created_at": transaction.created_at.isoformat(),
                        "updated_at": transaction.updated_at.isoformat(),
                        "amount": str(transaction.amount),
                        "currency": transaction.currency,
                        "transaction_type": transaction.transaction_type
                    }
                )
                
        except Exception as e:
            logger.error(f"❌ UTE_STATUS_ERROR: Failed to get status for {transaction_id}: {e}")
            return TransactionResult(
                success=False,
                error=f"Failed to get transaction status: {str(e)}",
                error_code="UTE_STATUS_FAILED"
            )
    
    # =========================================================================
    # SAGA ORCHESTRATION
    # =========================================================================
    
    def _requires_saga(self, transaction_type: str) -> bool:
        """
        Determine if a transaction type requires saga orchestration
        
        Args:
            transaction_type: Type of transaction
            
        Returns:
            True if saga is required, False for simple transactions
        """
        # Complex multi-step transactions that require sagas
        saga_required_types = {
            UnifiedTransactionType.ESCROW.value,
            UnifiedTransactionType.EXCHANGE_BUY_CRYPTO.value,
            UnifiedTransactionType.EXCHANGE_SELL_CRYPTO.value
        }
        
        return transaction_type in saga_required_types
    
    async def _initiate_saga(self, db: Session, transaction: UnifiedTransaction, saga_id: str):
        """
        Initiate saga orchestration for a complex transaction
        
        Args:
            db: Database session
            transaction: Transaction requiring saga
            saga_id: Unique saga identifier
        """
        logger.info(f"🎭 UTE_SAGA_START: Initiating saga {saga_id} for transaction {transaction.transaction_id}")
        
        try:
            # Create saga definition based on transaction type
            saga_def = await self._create_saga_definition(transaction, saga_id)
            
            # Create saga steps
            for step_config in saga_def.steps:
                step = SagaStep(
                    saga_id=saga_id,
                    step_id=step_config["step_id"],
                    step_name=step_config["step_name"],
                    step_type=step_config["step_type"],
                    step_order=step_config["step_order"],
                    transaction_id=transaction.transaction_id,
                    action_payload=step_config["action_payload"],
                    compensation_payload=step_config.get("compensation_payload"),
                    target_service=step_config["target_service"],
                    target_method=step_config["target_method"],
                    depends_on_steps=step_config.get("depends_on_steps"),
                    max_attempts=step_config.get("max_attempts", 3),
                    timeout_seconds=step_config.get("timeout_seconds", 300)
                )
                db.add(step)
            
            # Create saga tracking event
            await self._create_engine_event(
                db, transaction.transaction_id, "saga_initiated", "system",
                {"saga_id": saga_id, "step_count": len(saga_def.steps)},
                saga_id=saga_id
            )
            
            db.commit()
            
            # Start saga execution
            asyncio.create_task(self._execute_saga(saga_id))
            
        except Exception as e:
            logger.error(f"❌ UTE_SAGA_ERROR: Failed to initiate saga {saga_id}: {e}")
            await self._transition_status(
                db, transaction.transaction_id, UnifiedTransactionStatus.FAILED,
                f"Failed to initiate saga: {str(e)}"
            )
    
    async def _create_saga_definition(self, transaction: UnifiedTransaction, saga_id: str) -> SagaDefinition:
        """
        Create saga definition based on transaction type
        
        Args:
            transaction: Transaction requiring saga
            saga_id: Unique saga identifier
            
        Returns:
            SagaDefinition with steps configured for the transaction type
        """
        transaction_type = transaction.transaction_type
        
        if transaction_type == UnifiedTransactionType.WALLET_CASHOUT.value:
            return self._create_cashout_saga_definition(transaction, saga_id)
        elif transaction_type == UnifiedTransactionType.ESCROW.value:
            return self._create_escrow_saga_definition(transaction, saga_id)
        elif transaction_type in [UnifiedTransactionType.EXCHANGE_BUY_CRYPTO.value, UnifiedTransactionType.EXCHANGE_SELL_CRYPTO.value]:
            return self._create_exchange_saga_definition(transaction, saga_id)
        else:
            raise SagaError(f"Unknown transaction type for saga: {transaction_type}")
    
    def _create_cashout_saga_definition(self, transaction: UnifiedTransaction, saga_id: str) -> SagaDefinition:
        """Create saga definition for wallet cashout"""
        steps = [
            {
                "step_id": "validate_balance",
                "step_name": "Validate User Balance",
                "step_type": "validation",
                "step_order": 1,
                "action_payload": {
                    "user_id": transaction.user_id,
                    "amount": str(transaction.amount),
                    "currency": transaction.currency
                },
                "target_service": "wallet_service",
                "target_method": "validate_balance"
            },
            {
                "step_id": "hold_funds",
                "step_name": "Hold Funds in Wallet",
                "step_type": "payment",
                "step_order": 2,
                "action_payload": {
                    "user_id": transaction.user_id,
                    "amount": str(transaction.amount),
                    "currency": transaction.currency,
                    "purpose": "cashout"
                },
                "compensation_payload": {
                    "release_hold": True
                },
                "target_service": "wallet_service",
                "target_method": "hold_funds"
            },
            {
                "step_id": "external_payout",
                "step_name": "Execute External Payout",
                "step_type": "payment",
                "step_order": 3,
                "action_payload": {
                    "amount": str(transaction.amount),
                    "currency": transaction.currency,
                    "destination": transaction.metadata.get("destination"),
                    "provider": transaction.metadata.get("provider")
                },
                "target_service": "payment_provider",
                "target_method": "execute_payout"
            },
            {
                "step_id": "consume_funds",
                "step_name": "Consume Held Funds",
                "step_type": "payment",
                "step_order": 4,
                "action_payload": {
                    "user_id": transaction.user_id,
                    "amount": str(transaction.amount),
                    "currency": transaction.currency
                },
                "target_service": "wallet_service",
                "target_method": "consume_held_funds"
            },
            {
                "step_id": "send_notification",
                "step_name": "Send Completion Notification",
                "step_type": "notification",
                "step_order": 5,
                "action_payload": {
                    "user_id": transaction.user_id,
                    "notification_type": "cashout_completed",
                    "amount": str(transaction.amount),
                    "currency": transaction.currency
                },
                "target_service": "notification_provider",
                "target_method": "send_notification"
            }
        ]
        
        return SagaDefinition(
            saga_id=saga_id,
            saga_name="Wallet Cashout Saga",
            transaction_id=transaction.transaction_id,
            steps=steps
        )
    
    def _create_escrow_saga_definition(self, transaction: UnifiedTransaction, saga_id: str) -> SagaDefinition:
        """Create saga definition for escrow transaction"""
        # Simplified escrow saga - full implementation would be more complex
        steps = [
            {
                "step_id": "validate_escrow",
                "step_name": "Validate Escrow Parameters",
                "step_type": "validation",
                "step_order": 1,
                "action_payload": transaction.metadata,
                "target_service": "escrow_service",
                "target_method": "validate_escrow"
            },
            {
                "step_id": "create_holding",
                "step_name": "Create Escrow Holding",
                "step_type": "payment",
                "step_order": 2,
                "action_payload": {
                    "amount": str(transaction.amount),
                    "currency": transaction.currency,
                    "buyer_id": transaction.user_id
                },
                "compensation_payload": {
                    "release_holding": True
                },
                "target_service": "escrow_service",
                "target_method": "create_holding"
            }
        ]
        
        return SagaDefinition(
            saga_id=saga_id,
            saga_name="Escrow Saga",
            transaction_id=transaction.transaction_id,
            steps=steps
        )
    
    def _create_exchange_saga_definition(self, transaction: UnifiedTransaction, saga_id: str) -> SagaDefinition:
        """Create saga definition for exchange transaction"""
        # Simplified exchange saga
        steps = [
            {
                "step_id": "get_rates",
                "step_name": "Get Current Exchange Rates",
                "step_type": "validation",
                "step_order": 1,
                "action_payload": {
                    "from_currency": transaction.metadata.get("from_currency"),
                    "to_currency": transaction.metadata.get("to_currency"),
                    "amount": str(transaction.amount)
                },
                "target_service": "rates_provider",
                "target_method": "get_exchange_rate"
            },
            {
                "step_id": "execute_exchange",
                "step_name": "Execute Currency Exchange",
                "step_type": "payment", 
                "step_order": 2,
                "action_payload": {
                    "user_id": transaction.user_id,
                    "amount": str(transaction.amount),
                    "from_currency": transaction.metadata.get("from_currency"),
                    "to_currency": transaction.metadata.get("to_currency")
                },
                "target_service": "exchange_service",
                "target_method": "execute_exchange"
            }
        ]
        
        return SagaDefinition(
            saga_id=saga_id,
            saga_name="Exchange Saga",
            transaction_id=transaction.transaction_id,
            steps=steps
        )
    
    async def _execute_saga(self, saga_id: str):
        """
        Execute saga steps in order
        
        Args:
            saga_id: Saga to execute
        """
        logger.info(f"🎭 UTE_SAGA_EXECUTE: Starting saga execution {saga_id}")
        
        try:
            with managed_session() as db:
                # Get all saga steps ordered by step_order
                steps = db.query(SagaStep).filter(
                    SagaStep.saga_id == saga_id
                ).order_by(SagaStep.step_order).all()
                
                if not steps:
                    logger.error(f"❌ UTE_SAGA_ERROR: No steps found for saga {saga_id}")
                    return
                
                transaction_id = steps[0].transaction_id
                
                # Execute steps sequentially
                for step in steps:
                    success = await self._execute_saga_step(db, step)
                    if not success:
                        logger.error(f"❌ UTE_SAGA_STEP_FAILED: Step {step.step_id} failed, starting compensation")
                        await self._compensate_saga(db, saga_id, step.step_order)
                        await self._transition_status(
                            db, transaction_id, UnifiedTransactionStatus.FAILED,
                            f"Saga step {step.step_id} failed"
                        )
                        return
                
                # All steps completed successfully
                logger.info(f"✅ UTE_SAGA_SUCCESS: Saga {saga_id} completed successfully")
                await self._transition_status(
                    db, transaction_id, UnifiedTransactionStatus.COMPLETED,
                    "Saga completed successfully"
                )
                
        except Exception as e:
            logger.error(f"❌ UTE_SAGA_EXECUTE_ERROR: Failed to execute saga {saga_id}: {e}")
    
    async def _execute_saga_step(self, db: Session, step: SagaStep) -> bool:
        """
        Execute a single saga step
        
        Args:
            db: Database session
            step: Saga step to execute
            
        Returns:
            True if step completed successfully, False otherwise
        """
        step_id = step.step_id
        logger.info(f"🔄 UTE_SAGA_STEP: Executing {step_id}")
        
        try:
            # Update step status to running
            step.status = SagaStepStatus.RUNNING.value
            step.started_at = datetime.utcnow()
            step.attempt_count += 1
            
            # For now, simulate step execution based on target service
            # In full implementation, this would call actual service methods
            result = await self._simulate_step_execution(step)
            
            if result:
                step.status = SagaStepStatus.COMPLETED.value
                step.completed_at = datetime.utcnow()
                step.execution_result = {"success": True, "message": "Step completed"}
                
                logger.info(f"✅ UTE_SAGA_STEP_SUCCESS: {step_id} completed")
                db.commit()
                return True
            else:
                step.status = SagaStepStatus.FAILED.value
                step.error_message = "Step execution failed"
                step.execution_result = {"success": False, "message": "Step failed"}
                
                logger.error(f"❌ UTE_SAGA_STEP_ERROR: {step_id} failed")
                db.commit()
                return False
                
        except Exception as e:
            step.status = SagaStepStatus.FAILED.value
            step.error_message = str(e)
            
            logger.error(f"❌ UTE_SAGA_STEP_EXCEPTION: {step_id} exception: {e}")
            db.commit()
            return False
    
    async def _simulate_step_execution(self, step: SagaStep) -> bool:
        """
        Simulate step execution for demonstration
        
        Args:
            step: Saga step to simulate
            
        Returns:
            True if simulation succeeds, False otherwise
        """
        # Simulate processing time
        await asyncio.sleep(0.1)
        
        # For demo purposes, all validation steps succeed, others have 90% success rate
        if step.step_type == "validation":
            return True
        else:
            # Simulate 90% success rate for other steps
            import random
            return random.random() > 0.1
    
    async def _compensate_saga(self, db: Session, saga_id: str, failed_step_order: int):
        """
        Execute compensation for failed saga
        
        Args:
            db: Database session
            saga_id: Saga that failed
            failed_step_order: Order of the step that failed
        """
        logger.info(f"🔄 UTE_SAGA_COMPENSATE: Starting compensation for saga {saga_id}")
        
        # Get all completed steps that need compensation (reverse order)
        steps_to_compensate = db.query(SagaStep).filter(
            and_(
                SagaStep.saga_id == saga_id,
                SagaStep.step_order < failed_step_order,
                SagaStep.status == SagaStepStatus.COMPLETED.value,
                SagaStep.compensation_payload.isnot(None)
            )
        ).order_by(SagaStep.step_order.desc()).all()
        
        for step in steps_to_compensate:
            try:
                step.status = SagaStepStatus.COMPENSATING.value
                # Simulate compensation execution
                await asyncio.sleep(0.1)
                step.status = SagaStepStatus.COMPENSATED.value
                step.compensation_result = {"success": True, "message": "Compensated"}
                logger.info(f"✅ UTE_SAGA_COMPENSATED: Step {step.step_id}")
            except Exception as e:
                step.compensation_result = {"success": False, "message": str(e)}
                logger.error(f"❌ UTE_SAGA_COMPENSATION_ERROR: Failed to compensate {step.step_id}: {e}")
        
        db.commit()
    
    async def _execute_simple_transaction(self, db: Session, transaction: UnifiedTransaction):
        """
        Execute simple transaction without saga
        
        Args:
            db: Database session
            transaction: Simple transaction to execute
        """
        logger.info(f"⚡ UTE_SIMPLE_EXEC: Processing simple transaction {transaction.transaction_id}")
        
        try:
            # Simulate simple transaction processing
            await asyncio.sleep(0.2)
            
            # For demo, simple transactions succeed 95% of the time
            import random
            if random.random() > 0.05:
                await self._transition_status(
                    db, transaction.transaction_id, UnifiedTransactionStatus.COMPLETED,
                    "Simple transaction completed successfully"
                )
                logger.info(f"✅ UTE_SIMPLE_SUCCESS: Transaction {transaction.transaction_id} completed")
            else:
                await self._transition_status(
                    db, transaction.transaction_id, UnifiedTransactionStatus.FAILED,
                    "Simple transaction failed"
                )
                logger.error(f"❌ UTE_SIMPLE_FAILED: Transaction {transaction.transaction_id} failed")
                
        except Exception as e:
            logger.error(f"❌ UTE_SIMPLE_ERROR: Failed to execute simple transaction {transaction.transaction_id}: {e}")
            await self._transition_status(
                db, transaction.transaction_id, UnifiedTransactionStatus.FAILED,
                f"Execution failed: {str(e)}"
            )
    
    # =========================================================================
    # OUTBOX/INBOX PATTERNS
    # =========================================================================
    
    async def _create_outbox_event(
        self,
        db: Session,
        event_type: str,
        entity_type: str,
        entity_id: str,
        event_payload: Dict[str, Any],
        transaction_id: str = None,
        user_id: int = None,
        scheduled_at: datetime = None
    ):
        """Create outbox event for reliable event publishing"""
        event_id = f"{event_type}_{entity_id}_{int(time.time())}"
        
        event = OutboxEvent(
            event_id=event_id,
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            transaction_id=transaction_id,
            user_id=user_id,
            event_payload=event_payload,
            scheduled_at=scheduled_at or datetime.utcnow()
        )
        
        db.add(event)
        logger.debug(f"📤 UTE_OUTBOX: Created event {event_id}")
    
    async def process_outbox_events(self, batch_size: int = None) -> Dict[str, int]:
        """
        Process pending outbox events
        
        Args:
            batch_size: Maximum number of events to process
            
        Returns:
            Processing statistics
        """
        batch_size = batch_size or self.outbox_processing_batch_size
        processed = failed = 0
        
        try:
            with managed_session() as db:
                events = db.query(OutboxEvent).filter(
                    and_(
                        OutboxEvent.status == OutboxEventStatus.PENDING.value,
                        or_(
                            OutboxEvent.scheduled_at.is_(None),
                            OutboxEvent.scheduled_at <= datetime.utcnow()
                        )
                    )
                ).limit(batch_size).all()
                
                for event in events:
                    try:
                        # Simulate event publishing
                        await self._publish_outbox_event(event)
                        event.status = OutboxEventStatus.PUBLISHED.value
                        event.published_at = datetime.utcnow()
                        processed += 1
                    except Exception as e:
                        event.status = OutboxEventStatus.FAILED.value
                        event.last_error = str(e)
                        event.attempt_count += 1
                        failed += 1
                        logger.error(f"❌ UTE_OUTBOX_ERROR: Failed to publish event {event.event_id}: {e}")
                
                db.commit()
                
                if events:
                    logger.info(f"📤 UTE_OUTBOX_PROCESSED: {processed} events published, {failed} failed")
                
                return {"processed": processed, "failed": failed}
                
        except Exception as e:
            logger.error(f"❌ UTE_OUTBOX_BATCH_ERROR: Failed to process outbox events: {e}")
            return {"processed": 0, "failed": 0}
    
    async def _publish_outbox_event(self, event: OutboxEvent):
        """
        Publish outbox event to appropriate handlers
        
        Args:
            event: Outbox event to publish
        """
        # In full implementation, this would route events to appropriate handlers
        # For now, just simulate publishing
        await asyncio.sleep(0.01)
        logger.debug(f"📡 UTE_OUTBOX_PUBLISHED: Event {event.event_id} of type {event.event_type}")
    
    async def process_inbox_webhook(
        self,
        provider: str,
        webhook_id: str,
        event_type: str,
        payload: Dict[str, Any],
        signature: str = None,
        headers: Dict[str, str] = None
    ) -> Dict[str, Any]:
        """
        Process incoming webhook with idempotency
        
        Args:
            provider: Provider name (fincra, kraken, etc.)
            webhook_id: Unique webhook identifier from provider
            event_type: Type of webhook event
            payload: Webhook payload
            signature: Webhook signature for verification
            headers: HTTP headers
            
        Returns:
            Processing result
        """
        logger.info(f"📥 UTE_INBOX: Processing webhook {webhook_id} from {provider}")
        
        try:
            with managed_session() as db:
                # Check for duplicate webhook
                existing = db.query(InboxWebhook).filter(
                    InboxWebhook.webhook_id == webhook_id
                ).first()
                
                if existing:
                    logger.info(f"🔄 UTE_INBOX_DUPLICATE: Webhook {webhook_id} already processed")
                    return {
                        "success": True,
                        "status": "duplicate",
                        "message": "Webhook already processed"
                    }
                
                # Create inbox record
                inbox_webhook = InboxWebhook(
                    webhook_id=webhook_id,
                    provider=provider,
                    event_type=event_type,
                    request_payload=payload,
                    request_headers=headers or {},
                    request_signature=signature,
                    status=InboxWebhookStatus.RECEIVED.value
                )
                
                db.add(inbox_webhook)
                db.commit()
                
                # Process webhook
                result = await self._process_webhook_payload(provider, event_type, payload)
                
                # Update inbox record with result
                inbox_webhook.status = InboxWebhookStatus.PROCESSED.value if result["success"] else InboxWebhookStatus.FAILED.value
                inbox_webhook.processed_at = datetime.utcnow()
                inbox_webhook.processing_result = result
                if not result["success"]:
                    inbox_webhook.error_message = result.get("error", "Processing failed")
                
                db.commit()
                
                logger.info(f"✅ UTE_INBOX_PROCESSED: Webhook {webhook_id} processed successfully")
                return result
                
        except Exception as e:
            logger.error(f"❌ UTE_INBOX_ERROR: Failed to process webhook {webhook_id}: {e}")
            return {
                "success": False,
                "error": str(e),
                "message": "Webhook processing failed"
            }
    
    async def _process_webhook_payload(self, provider: str, event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process webhook payload based on provider and event type
        
        Args:
            provider: Provider name
            event_type: Event type
            payload: Webhook payload
            
        Returns:
            Processing result
        """
        # Simulate webhook processing
        await asyncio.sleep(0.1)
        
        # In full implementation, this would route to appropriate handlers
        # based on provider and event type
        return {
            "success": True,
            "message": f"Processed {event_type} webhook from {provider}",
            "processed_at": datetime.utcnow().isoformat()
        }
    
    # =========================================================================
    # UTILITY METHODS
    # =========================================================================
    
    async def _transition_status(
        self,
        db: Session,
        transaction_id: str,
        new_status: UnifiedTransactionStatus,
        message: str,
        metadata: Dict[str, Any] = None
    ):
        """
        Transition transaction to new status with validation
        
        CRITICAL: This is the central status update method for UnifiedTransactionEngine.
        All status transitions MUST go through state validation to prevent invalid transitions.
        
        Args:
            db: Database session
            transaction_id: Transaction to update
            new_status: New status to transition to
            message: Status change message
            metadata: Additional metadata
            
        Raises:
            StateTransitionError: If the transition is invalid
        """
        transaction = db.query(UnifiedTransaction).filter(
            UnifiedTransaction.transaction_id == transaction_id
        ).first()
        
        if transaction:
            old_status = transaction.status
            
            # CRITICAL: Validate state transition before applying
            try:
                current_status = UnifiedTransactionStatus(old_status) if isinstance(old_status, str) else old_status
                
                # Validate the transition
                is_valid, reason = UnifiedTransactionStateValidator.validate_transition(
                    current_status,
                    new_status,
                    transaction_id=transaction_id
                )
                
                if not is_valid:
                    error_msg = (
                        f"🚫 ENGINE_TX_TRANSITION_BLOCKED: Invalid transition "
                        f"{current_status.value if hasattr(current_status, 'value') else current_status} → "
                        f"{new_status.value if hasattr(new_status, 'value') else new_status} "
                        f"for transaction {transaction_id}: {reason}"
                    )
                    logger.error(error_msg)
                    raise StateTransitionError(error_msg)
                
                # Apply the validated transition
                transaction.status = new_status.value
                transaction.updated_at = datetime.utcnow()
                
                logger.info(
                    f"✅ UTE_STATUS_TRANSITION: {transaction_id} "
                    f"{current_status.value if hasattr(current_status, 'value') else current_status} → "
                    f"{new_status.value}: {message}"
                )
                
                await self._record_status_change(
                    db, transaction_id, old_status, new_status, message, metadata
                )
                
                await self._create_engine_event(
                    db, transaction_id, "status_changed", "system",
                    {
                        "old_status": old_status,
                        "new_status": new_status.value,
                        "message": message,
                        "validation_passed": True
                    }
                )
                
            except ValueError:
                # Handle unknown status values (legacy compatibility)
                logger.warning(
                    f"⚠️ ENGINE_TX_UNKNOWN_STATUS: Transaction {transaction_id} has unknown status "
                    f"'{old_status}'. Applying transition without validation for legacy compatibility."
                )
                transaction.status = new_status.value
                transaction.updated_at = datetime.utcnow()
                
                await self._record_status_change(
                    db, transaction_id, old_status, new_status, message, metadata
                )
                
                await self._create_engine_event(
                    db, transaction_id, "status_changed", "system",
                    {
                        "old_status": old_status,
                        "new_status": new_status.value,
                        "message": message,
                        "validation_skipped": True,
                        "reason": "unknown_status"
                    }
                )
            except StateTransitionError:
                # Re-raise validation errors
                raise
            except Exception as e:
                # Log unexpected errors but re-raise
                logger.error(
                    f"❌ ENGINE_TX_VALIDATION_ERROR: Unexpected error validating transition for "
                    f"{transaction_id}: {e}"
                )
                raise
    
    async def _record_status_change(
        self,
        db: Session,
        transaction_id: str,
        old_status: str,
        new_status: UnifiedTransactionStatus,
        message: str,
        metadata: Dict[str, Any] = None
    ):
        """Record status change in history"""
        history = UnifiedTransactionStatusHistory(
            transaction_id=transaction_id,
            previous_status=old_status,
            new_status=new_status.value,
            change_reason=message,
            metadata=metadata or {},
            changed_at=datetime.utcnow()
        )
        db.add(history)
    
    async def _create_engine_event(
        self,
        db: Session,
        transaction_id: str,
        event_type: str,
        event_category: str,
        event_data: Dict[str, Any],
        saga_id: str = None,
        correlation_id: str = None,
        parent_event_id: str = None
    ):
        """Create engine event for audit trail"""
        event_id = f"{event_type}_{transaction_id}_{int(time.time() * 1000)}"
        
        event = TransactionEngineEvent(
            event_id=event_id,
            transaction_id=transaction_id,
            saga_id=saga_id,
            event_type=event_type,
            event_category=event_category,
            event_data=event_data,
            correlation_id=correlation_id or transaction_id,
            parent_event_id=parent_event_id
        )
        db.add(event)
    
    def register_provider(self, provider: BaseProvider):
        """Register a provider with the engine"""
        self.provider_registry[provider.provider_name] = provider
        logger.info(f"🔌 UTE_PROVIDER_REGISTERED: {provider.provider_name}")
    
    def get_provider(self, provider_name: str) -> Optional[BaseProvider]:
        """Get registered provider by name"""
        return self.provider_registry.get(provider_name)


# Factory function
def create_ute_engine() -> UnifiedTransactionEngine:
    """
    Create and configure UTE engine instance
    
    Returns:
        Configured UnifiedTransactionEngine
    """
    engine = UnifiedTransactionEngine()
    
    # Register providers would happen here in full implementation
    # engine.register_provider(FincraPaymentAdapter())
    # engine.register_provider(KrakenPaymentAdapter())
    # etc.
    
    return engine


# Global engine instance
ute_engine = create_ute_engine()

# Compatibility alias for imports
unified_transaction_engine = ute_engine