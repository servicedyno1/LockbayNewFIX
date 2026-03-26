"""
Compensating Saga Pattern for Error Recovery
Coordinates complex multi-step financial operations with rollback capabilities
Ensures data consistency and proper error recovery in distributed transactions
"""

import asyncio
import logging
import uuid
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from enum import Enum

from services.state_manager import state_manager
from services.idempotency_service import IdempotencyService, OperationType
from config import Config

logger = logging.getLogger(__name__)


class SagaStatus(Enum):
    """Status of saga execution"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPENSATING = "compensating"
    COMPENSATED = "compensated"
    FAILED = "failed"
    TIMEOUT = "timeout"


class StepStatus(Enum):
    """Status of individual saga steps"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    COMPENSATING = "compensating"
    COMPENSATED = "compensated"
    SKIPPED = "skipped"


@dataclass
class SagaStep:
    """Individual step in a saga transaction"""
    step_id: str
    step_name: str
    handler: str  # Handler function name
    compensation_handler: str  # Compensation function name
    parameters: Dict[str, Any]
    retry_count: int = 0
    max_retries: int = 3
    timeout_seconds: int = 300
    status: StepStatus = StepStatus.PENDING
    result: Optional[Any] = None
    error: Optional[str] = None
    executed_at: Optional[datetime] = None
    compensated_at: Optional[datetime] = None
    idempotency_key: Optional[str] = None


@dataclass
class SagaTransaction:
    """Complete saga transaction with all steps"""
    saga_id: str
    saga_name: str
    steps: List[SagaStep]
    status: SagaStatus = SagaStatus.PENDING
    created_at: datetime = field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    timeout_at: Optional[datetime] = None
    context: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    compensation_started_at: Optional[datetime] = None
    compensation_completed_at: Optional[datetime] = None
    total_timeout_seconds: int = 1800  # 30 minutes default


class SagaStepHandler:
    """Base class for saga step handlers"""
    
    async def execute(self, parameters: Dict[str, Any], context: Dict[str, Any]) -> Any:
        """Execute the step"""
        raise NotImplementedError
    
    async def compensate(self, parameters: Dict[str, Any], context: Dict[str, Any], original_result: Any) -> Any:
        """Compensate (rollback) the step"""
        raise NotImplementedError


class SagaCoordinator:
    """
    Coordinates compensating saga transactions for complex financial operations
    
    Features:
    - Multi-step transaction coordination
    - Automatic compensation on failure
    - State persistence with Redis
    - Timeout handling and recovery
    - Idempotent step execution
    - Comprehensive audit logging
    """
    
    def __init__(self):
        self.handlers: Dict[str, SagaStepHandler] = {}
        self.running_sagas: Dict[str, asyncio.Task] = {}
        self.idempotency_service = IdempotencyService()
        
        # Configuration
        self.default_timeout = Config.SAGA_DEFAULT_TIMEOUT if hasattr(Config, 'SAGA_DEFAULT_TIMEOUT') else 1800
        self.step_timeout = Config.SAGA_STEP_TIMEOUT if hasattr(Config, 'SAGA_STEP_TIMEOUT') else 300
        self.cleanup_interval = Config.SAGA_CLEANUP_INTERVAL if hasattr(Config, 'SAGA_CLEANUP_INTERVAL') else 3600
        self.max_concurrent_sagas = Config.SAGA_MAX_CONCURRENT if hasattr(Config, 'SAGA_MAX_CONCURRENT') else 10
        
        # Redis key prefixes
        self.saga_prefix = "saga"
        self.saga_lock_prefix = "saga_lock"
        self.saga_result_prefix = "saga_result"
        
        # Metrics
        self.metrics = {
            'sagas_started': 0,
            'sagas_completed': 0,
            'sagas_failed': 0,
            'sagas_compensated': 0,
            'steps_executed': 0,
            'steps_compensated': 0,
            'compensation_time_ms': 0,
            'execution_time_ms': 0
        }
        
        logger.info("🔄 Saga coordinator initialized")
    
    def register_handler(self, handler_name: str, handler: SagaStepHandler):
        """Register a saga step handler"""
        self.handlers[handler_name] = handler
        logger.info(f"📝 Registered saga handler: {handler_name}")
    
    async def start_saga(
        self,
        saga_name: str,
        steps: List[Dict[str, Any]],
        context: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        timeout_seconds: Optional[int] = None
    ) -> str:
        """
        Start a new saga transaction
        
        Args:
            saga_name: Name of the saga
            steps: List of step definitions
            context: Saga execution context
            metadata: Additional metadata
            timeout_seconds: Total saga timeout
            
        Returns:
            str: Saga ID
        """
        saga_id = f"saga_{saga_name}_{uuid.uuid4().hex[:8]}"
        
        try:
            # Create saga steps
            saga_steps = []
            for i, step_def in enumerate(steps):
                step = SagaStep(
                    step_id=f"{saga_id}_step_{i:03d}",
                    step_name=step_def['name'],
                    handler=step_def['handler'],
                    compensation_handler=step_def['compensation_handler'],
                    parameters=step_def.get('parameters', {}),
                    max_retries=step_def.get('max_retries', 3),
                    timeout_seconds=step_def.get('timeout_seconds', self.step_timeout)
                )
                saga_steps.append(step)
            
            # Create saga transaction
            saga = SagaTransaction(
                saga_id=saga_id,
                saga_name=saga_name,
                steps=saga_steps,
                context=context or {},
                metadata=metadata or {},
                total_timeout_seconds=timeout_seconds or self.default_timeout,
                timeout_at=datetime.utcnow() + timedelta(seconds=timeout_seconds or self.default_timeout)
            )
            
            # Store saga state
            await self._persist_saga(saga)
            
            # Start execution
            task = asyncio.create_task(self._execute_saga(saga))
            self.running_sagas[saga_id] = task
            
            self.metrics['sagas_started'] += 1
            logger.info(f"🚀 Started saga: {saga_id} ({saga_name}) with {len(steps)} steps")
            
            return saga_id
            
        except Exception as e:
            logger.error(f"❌ Failed to start saga: {e}")
            raise
    
    async def get_saga_status(self, saga_id: str) -> Optional[SagaTransaction]:
        """Get current saga status"""
        try:
            saga_key = f"{self.saga_prefix}:{saga_id}"
            saga_data = await state_manager.get_state(saga_key)
            
            if not saga_data:
                return None
            
            return self._deserialize_saga(saga_data)
            
        except Exception as e:
            logger.error(f"❌ Error getting saga status: {e}")
            return None
    
    async def cancel_saga(self, saga_id: str, reason: str = "cancelled") -> bool:
        """Cancel a running saga and compensate completed steps"""
        try:
            saga = await self.get_saga_status(saga_id)
            if not saga:
                return False
            
            if saga.status in [SagaStatus.COMPLETED, SagaStatus.COMPENSATED, SagaStatus.FAILED]:
                return False  # Cannot cancel completed sagas
            
            logger.info(f"🚫 Cancelling saga: {saga_id} - {reason}")
            
            # Cancel running task
            if saga_id in self.running_sagas:
                self.running_sagas[saga_id].cancel()
                del self.running_sagas[saga_id]
            
            # Start compensation
            await self._compensate_saga(saga, reason)
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Error cancelling saga: {e}")
            return False
    
    async def _execute_saga(self, saga: SagaTransaction):
        """Execute all steps in a saga"""
        try:
            saga.status = SagaStatus.RUNNING
            saga.started_at = datetime.utcnow()
            await self._persist_saga(saga)
            
            start_time = datetime.utcnow()
            
            # Execute steps sequentially
            for step in saga.steps:
                if datetime.utcnow() > saga.timeout_at:
                    logger.warning(f"⏰ Saga timeout: {saga.saga_id}")
                    saga.status = SagaStatus.TIMEOUT
                    await self._compensate_saga(saga, "timeout")
                    return
                
                # Execute step
                step_success = await self._execute_step(step, saga)
                
                if not step_success:
                    logger.error(f"❌ Step failed: {step.step_id} in saga {saga.saga_id}")
                    saga.status = SagaStatus.FAILED
                    await self._compensate_saga(saga, f"step_failure: {step.step_name}")
                    return
                
                # Update saga context with step result
                if step.result:
                    saga.context[f"{step.step_name}_result"] = step.result
                
                await self._persist_saga(saga)
            
            # All steps completed successfully
            saga.status = SagaStatus.COMPLETED
            saga.completed_at = datetime.utcnow()
            await self._persist_saga(saga)
            
            execution_time = (datetime.utcnow() - start_time).total_seconds() * 1000
            self.metrics['execution_time_ms'] += execution_time
            self.metrics['sagas_completed'] += 1
            
            logger.info(f"✅ Saga completed: {saga.saga_id} in {execution_time:.2f}ms")
            
            # Store final result
            await self._store_saga_result(saga)
            
        except Exception as e:
            logger.error(f"❌ Saga execution failed: {saga.saga_id} - {e}")
            saga.status = SagaStatus.FAILED
            await self._compensate_saga(saga, f"execution_error: {str(e)}")
        
        finally:
            # Clean up running saga tracking
            if saga.saga_id in self.running_sagas:
                del self.running_sagas[saga.saga_id]
    
    async def _execute_step(self, step: SagaStep, saga: SagaTransaction) -> bool:
        """Execute an individual saga step"""
        try:
            # Generate idempotency key for step
            step.idempotency_key = f"saga_step:{saga.saga_id}:{step.step_id}"
            
            # Check if step already executed (for saga recovery)
            existing_result = await self.idempotency_service.get_operation_result(step.idempotency_key)
            if existing_result:
                step.status = StepStatus.COMPLETED
                step.result = existing_result
                step.executed_at = datetime.utcnow()
                logger.info(f"🔄 Step already completed (idempotent): {step.step_id}")
                return True
            
            # Get handler
            handler = self.handlers.get(step.handler)
            if not handler:
                step.error = f"Handler not found: {step.handler}"
                step.status = StepStatus.FAILED
                return False
            
            # Execute with retries
            for attempt in range(step.max_retries + 1):
                try:
                    step.status = StepStatus.RUNNING
                    step.retry_count = attempt
                    await self._persist_saga(saga)
                    
                    logger.info(f"▶️ Executing step: {step.step_id} (attempt {attempt + 1})")
                    
                    # Execute with timeout
                    step.result = await asyncio.wait_for(
                        handler.execute(step.parameters, saga.context),
                        timeout=step.timeout_seconds
                    )
                    
                    # Store result with idempotency
                    await self.idempotency_service.store_operation_result(
                        step.idempotency_key,
                        OperationType.EXTERNAL_API_CALL,
                        step.result,
                        ttl_seconds=86400  # 24 hours
                    )
                    
                    step.status = StepStatus.COMPLETED
                    step.executed_at = datetime.utcnow()
                    self.metrics['steps_executed'] += 1
                    
                    logger.info(f"✅ Step completed: {step.step_id}")
                    return True
                    
                except asyncio.TimeoutError:
                    step.error = f"Step timeout after {step.timeout_seconds}s"
                    logger.warning(f"⏰ Step timeout: {step.step_id}")
                    
                except Exception as e:
                    step.error = str(e)
                    logger.warning(f"⚠️ Step attempt {attempt + 1} failed: {step.step_id} - {e}")
                    
                    if attempt < step.max_retries:
                        await asyncio.sleep(2 ** attempt)  # Exponential backoff
            
            # All retries exhausted
            step.status = StepStatus.FAILED
            logger.error(f"❌ Step failed after {step.max_retries + 1} attempts: {step.step_id}")
            return False
            
        except Exception as e:
            step.status = StepStatus.FAILED
            step.error = str(e)
            logger.error(f"❌ Step execution error: {step.step_id} - {e}")
            return False
    
    async def _compensate_saga(self, saga: SagaTransaction, reason: str):
        """Compensate (rollback) all completed steps in reverse order"""
        try:
            saga.status = SagaStatus.COMPENSATING
            saga.compensation_started_at = datetime.utcnow()
            await self._persist_saga(saga)
            
            logger.info(f"🔄 Starting compensation for saga: {saga.saga_id} - {reason}")
            start_time = datetime.utcnow()
            
            # Compensate completed steps in reverse order
            completed_steps = [s for s in saga.steps if s.status == StepStatus.COMPLETED]
            
            for step in reversed(completed_steps):
                await self._compensate_step(step, saga)
            
            saga.status = SagaStatus.COMPENSATED
            saga.compensation_completed_at = datetime.utcnow()
            await self._persist_saga(saga)
            
            compensation_time = (datetime.utcnow() - start_time).total_seconds() * 1000
            self.metrics['compensation_time_ms'] += compensation_time
            self.metrics['sagas_compensated'] += 1
            
            logger.info(f"✅ Saga compensated: {saga.saga_id} in {compensation_time:.2f}ms")
            
        except Exception as e:
            logger.error(f"❌ Compensation failed: {saga.saga_id} - {e}")
            saga.status = SagaStatus.FAILED
            await self._persist_saga(saga)
    
    async def _compensate_step(self, step: SagaStep, saga: SagaTransaction):
        """Compensate (rollback) an individual step"""
        try:
            # Generate compensation idempotency key
            compensation_key = f"saga_compensation:{saga.saga_id}:{step.step_id}"
            
            # Check if compensation already executed
            existing_result = await self.idempotency_service.get_operation_result(compensation_key)
            if existing_result:
                step.status = StepStatus.COMPENSATED
                step.compensated_at = datetime.utcnow()
                logger.info(f"🔄 Step already compensated (idempotent): {step.step_id}")
                return
            
            # Get compensation handler
            handler = self.handlers.get(step.compensation_handler)
            if not handler:
                logger.error(f"❌ Compensation handler not found: {step.compensation_handler}")
                return
            
            step.status = StepStatus.COMPENSATING
            await self._persist_saga(saga)
            
            logger.info(f"↩️ Compensating step: {step.step_id}")
            
            # Execute compensation
            compensation_result = await asyncio.wait_for(
                handler.compensate(step.parameters, saga.context, step.result),
                timeout=step.timeout_seconds
            )
            
            # Store compensation result
            await self.idempotency_service.store_operation_result(
                compensation_key,
                OperationType.EXTERNAL_API_CALL,
                compensation_result,
                ttl_seconds=86400  # 24 hours
            )
            
            step.status = StepStatus.COMPENSATED
            step.compensated_at = datetime.utcnow()
            self.metrics['steps_compensated'] += 1
            
            logger.info(f"✅ Step compensated: {step.step_id}")
            
        except Exception as e:
            logger.error(f"❌ Step compensation failed: {step.step_id} - {e}")
            # Continue with other compensations even if one fails
    
    async def _persist_saga(self, saga: SagaTransaction):
        """Persist saga state to Redis"""
        try:
            saga_key = f"{self.saga_prefix}:{saga.saga_id}"
            saga_data = self._serialize_saga(saga)
            
            # Calculate TTL based on saga timeout
            ttl = max(saga.total_timeout_seconds * 2, 3600)  # At least 1 hour
            
            await state_manager.set_state(
                saga_key,
                saga_data,
                ttl=ttl,
                tags=['saga', saga.saga_name, saga.status.value],
                source='saga_coordinator'
            )
            
        except Exception as e:
            logger.error(f"❌ Failed to persist saga: {e}")
    
    async def _store_saga_result(self, saga: SagaTransaction):
        """Store final saga result for retrieval"""
        try:
            result_key = f"{self.saga_result_prefix}:{saga.saga_id}"
            result_data = {
                'saga_id': saga.saga_id,
                'saga_name': saga.saga_name,
                'status': saga.status.value,
                'started_at': saga.started_at.isoformat() if saga.started_at else None,
                'completed_at': saga.completed_at.isoformat() if saga.completed_at else None,
                'steps_completed': len([s for s in saga.steps if s.status == StepStatus.COMPLETED]),
                'total_steps': len(saga.steps),
                'context': saga.context,
                'metadata': saga.metadata
            }
            
            await state_manager.set_state(
                result_key,
                result_data,
                ttl=86400,  # Keep results for 24 hours
                tags=['saga_result', saga.saga_name],
                source='saga_coordinator'
            )
            
        except Exception as e:
            logger.error(f"❌ Failed to store saga result: {e}")
    
    def _serialize_saga(self, saga: SagaTransaction) -> Dict:
        """Serialize saga for Redis storage"""
        return {
            'saga_id': saga.saga_id,
            'saga_name': saga.saga_name,
            'status': saga.status.value,
            'created_at': saga.created_at.isoformat(),
            'started_at': saga.started_at.isoformat() if saga.started_at else None,
            'completed_at': saga.completed_at.isoformat() if saga.completed_at else None,
            'timeout_at': saga.timeout_at.isoformat() if saga.timeout_at else None,
            'compensation_started_at': saga.compensation_started_at.isoformat() if saga.compensation_started_at else None,
            'compensation_completed_at': saga.compensation_completed_at.isoformat() if saga.compensation_completed_at else None,
            'context': saga.context,
            'metadata': saga.metadata,
            'total_timeout_seconds': saga.total_timeout_seconds,
            'steps': [
                {
                    'step_id': s.step_id,
                    'step_name': s.step_name,
                    'handler': s.handler,
                    'compensation_handler': s.compensation_handler,
                    'parameters': s.parameters,
                    'retry_count': s.retry_count,
                    'max_retries': s.max_retries,
                    'timeout_seconds': s.timeout_seconds,
                    'status': s.status.value,
                    'result': s.result,
                    'error': s.error,
                    'executed_at': s.executed_at.isoformat() if s.executed_at else None,
                    'compensated_at': s.compensated_at.isoformat() if s.compensated_at else None,
                    'idempotency_key': s.idempotency_key
                }
                for s in saga.steps
            ]
        }
    
    def _deserialize_saga(self, data: Dict) -> SagaTransaction:
        """Deserialize saga from Redis data"""
        steps = []
        for step_data in data['steps']:
            step = SagaStep(
                step_id=step_data['step_id'],
                step_name=step_data['step_name'],
                handler=step_data['handler'],
                compensation_handler=step_data['compensation_handler'],
                parameters=step_data['parameters'],
                retry_count=step_data['retry_count'],
                max_retries=step_data['max_retries'],
                timeout_seconds=step_data['timeout_seconds'],
                status=StepStatus(step_data['status']),
                result=step_data['result'],
                error=step_data['error'],
                executed_at=datetime.fromisoformat(step_data['executed_at']) if step_data['executed_at'] else None,
                compensated_at=datetime.fromisoformat(step_data['compensated_at']) if step_data['compensated_at'] else None,
                idempotency_key=step_data['idempotency_key']
            )
            steps.append(step)
        
        return SagaTransaction(
            saga_id=data['saga_id'],
            saga_name=data['saga_name'],
            status=SagaStatus(data['status']),
            steps=steps,
            created_at=datetime.fromisoformat(data['created_at']),
            started_at=datetime.fromisoformat(data['started_at']) if data['started_at'] else None,
            completed_at=datetime.fromisoformat(data['completed_at']) if data['completed_at'] else None,
            timeout_at=datetime.fromisoformat(data['timeout_at']) if data['timeout_at'] else None,
            compensation_started_at=datetime.fromisoformat(data['compensation_started_at']) if data['compensation_started_at'] else None,
            compensation_completed_at=datetime.fromisoformat(data['compensation_completed_at']) if data['compensation_completed_at'] else None,
            context=data['context'],
            metadata=data['metadata'],
            total_timeout_seconds=data['total_timeout_seconds']
        )
    
    async def cleanup_completed_sagas(self, max_age_hours: int = 24) -> int:
        """Clean up old completed sagas"""
        # This would scan for old saga keys in a real implementation
        # For now, just return 0
        return 0
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get saga coordinator metrics"""
        return {
            **self.metrics,
            'running_sagas': len(self.running_sagas),
            'registered_handlers': len(self.handlers)
        }


# Financial operation saga handlers
class EscrowCreationSagaHandler(SagaStepHandler):
    """Handler for escrow creation saga steps"""
    
    async def execute(self, parameters: Dict[str, Any], context: Dict[str, Any]) -> Any:
        """Execute escrow creation step"""
        # Implementation would integrate with existing escrow system
        logger.info(f"📝 Creating escrow: {parameters}")
        return {"escrow_id": f"escrow_{int(datetime.utcnow().timestamp())}", "status": "created"}
    
    async def compensate(self, parameters: Dict[str, Any], context: Dict[str, Any], original_result: Any) -> Any:
        """Compensate escrow creation (cancel/refund)"""
        logger.info(f"↩️ Compensating escrow creation: {original_result}")
        return {"status": "compensated", "action": "cancelled"}


class PaymentProcessingSagaHandler(SagaStepHandler):
    """Handler for payment processing saga steps"""
    
    async def execute(self, parameters: Dict[str, Any], context: Dict[str, Any]) -> Any:
        """Execute payment processing step"""
        logger.info(f"💳 Processing payment: {parameters}")
        return {"transaction_id": f"txn_{int(datetime.utcnow().timestamp())}", "status": "processed"}
    
    async def compensate(self, parameters: Dict[str, Any], context: Dict[str, Any], original_result: Any) -> Any:
        """Compensate payment processing (refund)"""
        logger.info(f"↩️ Refunding payment: {original_result}")
        return {"status": "refunded", "refund_id": f"refund_{int(datetime.utcnow().timestamp())}"}


# Global instance
saga_coordinator = SagaCoordinator()

# Register common handlers
saga_coordinator.register_handler("escrow_creation", EscrowCreationSagaHandler())
saga_coordinator.register_handler("payment_processing", PaymentProcessingSagaHandler())


# Convenience functions for common financial sagas
async def execute_escrow_creation_saga(
    buyer_id: int,
    seller_id: int,
    amount: float,
    currency: str,
    metadata: Optional[Dict] = None
) -> str:
    """Execute complete escrow creation saga"""
    
    steps = [
        {
            'name': 'validate_participants',
            'handler': 'escrow_creation',
            'compensation_handler': 'escrow_creation',
            'parameters': {
                'buyer_id': buyer_id,
                'seller_id': seller_id,
                'action': 'validate'
            }
        },
        {
            'name': 'lock_funds',
            'handler': 'payment_processing',
            'compensation_handler': 'payment_processing',
            'parameters': {
                'user_id': buyer_id,
                'amount': amount,
                'currency': currency,
                'action': 'lock'
            }
        },
        {
            'name': 'create_escrow',
            'handler': 'escrow_creation',
            'compensation_handler': 'escrow_creation',
            'parameters': {
                'buyer_id': buyer_id,
                'seller_id': seller_id,
                'amount': amount,
                'currency': currency,
                'action': 'create'
            }
        }
    ]
    
    return await saga_coordinator.start_saga(
        saga_name="escrow_creation",
        steps=steps,
        context={'buyer_id': buyer_id, 'seller_id': seller_id},
        metadata=metadata or {},
        timeout_seconds=1800  # 30 minutes
    )


async def execute_cashout_processing_saga(
    user_id: int,
    amount: float,
    currency: str,
    destination: str,
    metadata: Optional[Dict] = None
) -> str:
    """Execute complete cashout processing saga"""
    
    steps = [
        {
            'name': 'validate_balance',
            'handler': 'payment_processing',
            'compensation_handler': 'payment_processing',
            'parameters': {
                'user_id': user_id,
                'amount': amount,
                'currency': currency,
                'action': 'validate_balance'
            }
        },
        {
            'name': 'reserve_funds',
            'handler': 'payment_processing',
            'compensation_handler': 'payment_processing',
            'parameters': {
                'user_id': user_id,
                'amount': amount,
                'currency': currency,
                'action': 'reserve'
            }
        },
        {
            'name': 'process_withdrawal',
            'handler': 'payment_processing',
            'compensation_handler': 'payment_processing',
            'parameters': {
                'user_id': user_id,
                'amount': amount,
                'currency': currency,
                'destination': destination,
                'action': 'withdraw'
            }
        }
    ]
    
    return await saga_coordinator.start_saga(
        saga_name="cashout_processing",
        steps=steps,
        context={'user_id': user_id, 'destination': destination},
        metadata=metadata or {},
        timeout_seconds=1200  # 20 minutes
    )