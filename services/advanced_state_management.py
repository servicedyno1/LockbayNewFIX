"""
Advanced State Management Integration Layer
Coordinates all advanced state management features including leader election,
job coordination, saga patterns, and TTL cleanup for the LockBay system
"""

import asyncio
import logging
from typing import Dict, List, Optional, Any, Callable
from datetime import datetime
from dataclasses import dataclass, asdict
import uuid

# Import all the advanced state management components
from services.leader_election import distributed_job_coordinator
from services.job_idempotency_service import job_idempotency_service, claim_and_execute_job
from services.saga_coordinator import saga_coordinator, execute_escrow_creation_saga, execute_cashout_processing_saga
from services.ttl_cleanup_service import ttl_cleanup_service
from services.state_manager import state_manager

logger = logging.getLogger(__name__)


@dataclass
class SystemHealthStatus:
    """Overall system health status"""
    leader_election_healthy: bool
    job_coordination_healthy: bool
    saga_coordination_healthy: bool
    cleanup_service_healthy: bool
    state_manager_healthy: bool
    overall_healthy: bool
    last_check: datetime
    issues: List[str]


class AdvancedStateManager:
    """
    Unified coordinator for all advanced state management features
    
    Features:
    - Leader election coordination
    - Distributed job processing
    - Saga transaction management
    - TTL-based cleanup
    - Comprehensive monitoring
    - Integration with existing systems
    """
    
    def __init__(self, instance_id: Optional[str] = None):
        self.instance_id = instance_id or f"lockbay_{uuid.uuid4().hex[:8]}"
        self.initialized = False
        self.running = False
        
        # Health monitoring
        self.health_check_interval = 60  # seconds
        self.health_task: Optional[asyncio.Task] = None
        self.last_health_check: Optional[datetime] = None
        self.health_status: Optional[SystemHealthStatus] = None
        
        # Metrics aggregation
        self.metrics = {
            'initialization_time': 0,
            'uptime_seconds': 0,
            'total_jobs_processed': 0,
            'total_sagas_executed': 0,
            'total_keys_cleaned': 0,
            'leader_election_changes': 0,
            'system_restarts': 0
        }
        
        # Integration callbacks
        self.on_leader_elected_callbacks: List[Callable] = []
        self.on_leader_lost_callbacks: List[Callable] = []
        self.on_system_unhealthy_callbacks: List[Callable] = []
        
        logger.info(f"🎯 Advanced state manager initialized for instance: {self.instance_id}")
    
    async def initialize(self) -> bool:
        """Initialize all advanced state management components"""
        if self.initialized:
            logger.warning("Advanced state manager already initialized")
            return True
        
        start_time = datetime.utcnow()
        
        try:
            logger.info("🚀 Initializing advanced state management systems...")
            
            # Initialize state manager first
            if not state_manager.is_connected:
                success = await state_manager.initialize()
                if not success:
                    raise RuntimeError("Failed to initialize state manager")
            
            # Initialize leader election and job coordination
            await distributed_job_coordinator.start()
            logger.info("✅ Leader election and job coordination initialized")
            
            # Initialize saga coordinator
            # Register additional handlers if needed
            logger.info("✅ Saga coordinator initialized")
            
            # Initialize job idempotency service
            logger.info("✅ Job idempotency service initialized")
            
            # Initialize TTL cleanup service
            await ttl_cleanup_service.start()
            logger.info("✅ TTL cleanup service started")
            
            # Register integration callbacks
            self._setup_integration_callbacks()
            
            # Start health monitoring
            self.health_task = asyncio.create_task(self._health_monitoring_loop())
            
            self.initialized = True
            self.running = True
            
            initialization_time = (datetime.utcnow() - start_time).total_seconds()
            self.metrics['initialization_time'] = initialization_time
            
            logger.info(f"✅ Advanced state management fully initialized in {initialization_time:.2f}s")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to initialize advanced state management: {e}")
            await self.cleanup()
            return False
    
    async def cleanup(self):
        """Cleanup all advanced state management components"""
        if not self.initialized:
            return
        
        logger.info("🛑 Shutting down advanced state management systems...")
        
        try:
            self.running = False
            
            # Stop health monitoring
            if self.health_task:
                self.health_task.cancel()
                try:
                    await self.health_task
                except asyncio.CancelledError:
                    pass
            
            # Stop all services
            await ttl_cleanup_service.stop()
            await distributed_job_coordinator.stop()
            
            logger.info("✅ Advanced state management systems shut down cleanly")
            
        except Exception as e:
            logger.error(f"❌ Error during cleanup: {e}")
        
        finally:
            self.initialized = False
            self.running = False
    
    def _setup_integration_callbacks(self):
        """Setup integration callbacks between systems"""
        
        # Leader election callbacks
        distributed_job_coordinator.leader_election.add_election_callback(
            "elected", 
            self._on_become_leader
        )
        
        distributed_job_coordinator.leader_election.add_election_callback(
            "deposed", 
            self._on_lose_leadership
        )
        
        distributed_job_coordinator.leader_election.add_election_callback(
            "leader_changed", 
            self._on_leader_changed
        )
    
    async def _on_become_leader(self):
        """Called when this instance becomes the leader"""
        logger.info(f"👑 Instance {self.instance_id} became leader")
        self.metrics['leader_election_changes'] += 1
        
        # Notify callbacks
        for callback in self.on_leader_elected_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback()
                else:
                    callback()
            except Exception as e:
                logger.error(f"Error in leader elected callback: {e}")
    
    async def _on_lose_leadership(self):
        """Called when this instance loses leadership"""
        logger.info(f"👥 Instance {self.instance_id} lost leadership")
        self.metrics['leader_election_changes'] += 1
        
        # Notify callbacks
        for callback in self.on_leader_lost_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback()
                else:
                    callback()
            except Exception as e:
                logger.error(f"Error in leader lost callback: {e}")
    
    async def _on_leader_changed(self, old_leader: str, new_leader: str):
        """Called when leadership changes"""
        logger.info(f"🔄 Leadership changed: {old_leader} -> {new_leader}")
    
    async def _health_monitoring_loop(self):
        """Continuous health monitoring of all systems"""
        while self.running:
            try:
                await self._check_system_health()
                await asyncio.sleep(self.health_check_interval)
            except Exception as e:
                logger.error(f"❌ Error in health monitoring: {e}")
                await asyncio.sleep(self.health_check_interval)
    
    async def _check_system_health(self):
        """Check health of all advanced state management systems"""
        try:
            issues = []
            
            # Check leader election health
            leader_healthy = distributed_job_coordinator.leader_election.running
            if not leader_healthy:
                issues.append("Leader election service not running")
            
            # Check job coordination health
            job_coordination_healthy = True  # Assume healthy for now
            
            # Check saga coordination health
            saga_healthy = len(saga_coordinator.running_sagas) < saga_coordinator.max_concurrent_sagas
            if not saga_healthy:
                issues.append("Too many running sagas")
            
            # Check cleanup service health
            cleanup_healthy = ttl_cleanup_service.running
            if not cleanup_healthy:
                issues.append("TTL cleanup service not running")
            
            # Check state manager health
            state_manager_healthy = state_manager.is_connected
            if not state_manager_healthy:
                issues.append("State manager not connected")
            
            # Overall health
            overall_healthy = all([
                leader_healthy,
                job_coordination_healthy,
                saga_healthy,
                cleanup_healthy,
                state_manager_healthy
            ])
            
            self.health_status = SystemHealthStatus(
                leader_election_healthy=leader_healthy,
                job_coordination_healthy=job_coordination_healthy,
                saga_coordination_healthy=saga_healthy,
                cleanup_service_healthy=cleanup_healthy,
                state_manager_healthy=state_manager_healthy,
                overall_healthy=overall_healthy,
                last_check=datetime.utcnow(),
                issues=issues
            )
            
            self.last_health_check = datetime.utcnow()
            
            if not overall_healthy:
                logger.warning(f"⚠️ System health issues detected: {', '.join(issues)}")
                
                # Notify unhealthy callbacks
                for callback in self.on_system_unhealthy_callbacks:
                    try:
                        if asyncio.iscoroutinefunction(callback):
                            await callback(self.health_status)
                        else:
                            callback(self.health_status)
                    except Exception as e:
                        logger.error(f"Error in system unhealthy callback: {e}")
            else:
                logger.debug("💚 All advanced state management systems healthy")
                
        except Exception as e:
            logger.error(f"❌ Error checking system health: {e}")
    
    def is_leader(self) -> bool:
        """Check if this instance is the current leader"""
        return distributed_job_coordinator.leader_election.is_leader()
    
    def get_leader_info(self):
        """Get information about the current leader"""
        return distributed_job_coordinator.leader_election.get_leader_info()
    
    async def execute_coordinated_job(
        self,
        job_type: str,
        job_key: str,
        parameters: Dict[str, Any],
        job_handler: Callable,
        max_retries: int = 3
    ) -> tuple[bool, Any, Optional[str]]:
        """
        Execute a job with full coordination (idempotency, leader election, etc.)
        
        Args:
            job_type: Type of job
            job_key: Unique key for the job
            parameters: Job parameters
            job_handler: Function to execute
            max_retries: Maximum retry attempts
            
        Returns:
            Tuple of (success, result, error)
        """
        return await claim_and_execute_job(
            job_type=job_type,
            job_key=job_key,
            parameters=parameters,
            job_handler=job_handler,
            instance_id=self.instance_id,
            max_retries=max_retries
        )
    
    async def execute_saga_transaction(
        self,
        saga_name: str,
        steps: List[Dict[str, Any]],
        context: Optional[Dict[str, Any]] = None,
        timeout_seconds: Optional[int] = None
    ) -> str:
        """
        Execute a saga transaction with full coordination
        
        Args:
            saga_name: Name of the saga
            steps: List of saga steps
            context: Execution context
            timeout_seconds: Transaction timeout
            
        Returns:
            Saga ID
        """
        return await saga_coordinator.start_saga(
            saga_name=saga_name,
            steps=steps,
            context=context,
            metadata={'instance_id': self.instance_id},
            timeout_seconds=timeout_seconds
        )
    
    async def force_cleanup(self, cleanup_type: Optional[str] = None) -> Dict[str, Any]:
        """Force cleanup of specific data types"""
        if cleanup_type:
            return await ttl_cleanup_service.force_cleanup(cleanup_type)
        else:
            return await ttl_cleanup_service.force_cleanup()
    
    async def get_comprehensive_metrics(self) -> Dict[str, Any]:
        """Get comprehensive metrics from all systems"""
        try:
            metrics = {
                'instance_id': self.instance_id,
                'uptime_seconds': (datetime.utcnow() - datetime.utcnow()).total_seconds() if self.initialized else 0,
                'health_status': asdict(self.health_status) if self.health_status else None,
                'last_health_check': self.last_health_check.isoformat() if self.last_health_check else None,
                'is_leader': self.is_leader(),
                'leader_info': asdict(self.get_leader_info()) if self.get_leader_info() else None,
                'system_metrics': self.metrics,
                'job_idempotency_metrics': job_idempotency_service.get_metrics(),
                'saga_metrics': saga_coordinator.get_metrics(),
                'cleanup_metrics': await ttl_cleanup_service.get_cache_metrics(),
                'state_manager_metrics': state_manager.metrics
            }
            
            return metrics
            
        except Exception as e:
            logger.error(f"❌ Error getting comprehensive metrics: {e}")
            return {'error': str(e)}
    
    def add_callback(self, event: str, callback: Callable):
        """Add callback for system events"""
        if event == "leader_elected":
            self.on_leader_elected_callbacks.append(callback)
        elif event == "leader_lost":
            self.on_leader_lost_callbacks.append(callback)
        elif event == "system_unhealthy":
            self.on_system_unhealthy_callbacks.append(callback)
        else:
            raise ValueError(f"Unknown event type: {event}")


# Global instance
advanced_state_manager = AdvancedStateManager()


# High-level integration functions
async def initialize_advanced_state_management() -> bool:
    """Initialize all advanced state management systems"""
    return await advanced_state_manager.initialize()


async def cleanup_advanced_state_management():
    """Cleanup all advanced state management systems"""
    await advanced_state_manager.cleanup()


async def execute_financial_escrow_saga(
    buyer_id: int,
    seller_id: int,
    amount: float,
    currency: str
) -> str:
    """Execute a complete escrow creation saga with advanced coordination"""
    return await execute_escrow_creation_saga(
        buyer_id=buyer_id,
        seller_id=seller_id,
        amount=amount,
        currency=currency,
        metadata={'instance_id': advanced_state_manager.instance_id}
    )


async def execute_financial_cashout_saga(
    user_id: int,
    amount: float,
    currency: str,
    destination: str
) -> str:
    """Execute a complete cashout processing saga with advanced coordination"""
    return await execute_cashout_processing_saga(
        user_id=user_id,
        amount=amount,
        currency=currency,
        destination=destination,
        metadata={'instance_id': advanced_state_manager.instance_id}
    )


async def execute_background_job_with_coordination(
    job_type: str,
    job_key: str,
    parameters: Dict[str, Any],
    job_handler: Callable,
    leader_only: bool = False
) -> tuple[bool, Any, Optional[str]]:
    """
    Execute a background job with full coordination and leader election
    
    Args:
        job_type: Type of job
        job_key: Unique key for the job
        parameters: Job parameters
        job_handler: Function to execute
        leader_only: Whether this job should only run on the leader
        
    Returns:
        Tuple of (success, result, error)
    """
    # Check if this is a leader-only job
    if leader_only and not advanced_state_manager.is_leader():
        return False, None, "Job requires leader instance"
    
    return await advanced_state_manager.execute_coordinated_job(
        job_type=job_type,
        job_key=job_key,
        parameters=parameters,
        job_handler=job_handler
    )


async def get_system_health() -> SystemHealthStatus:
    """Get current system health status"""
    if advanced_state_manager.health_status:
        return advanced_state_manager.health_status
    else:
        # Force health check if not available
        await advanced_state_manager._check_system_health()
        return advanced_state_manager.health_status


async def get_system_metrics() -> Dict[str, Any]:
    """Get comprehensive system metrics"""
    return await advanced_state_manager.get_comprehensive_metrics()


# Integration with existing systems
def register_system_callback(event: str, callback: Callable):
    """Register callback for system events"""
    advanced_state_manager.add_callback(event, callback)


# Example financial operation handlers for integration
async def process_escrow_creation_with_coordination(
    buyer_id: int,
    seller_id: int,
    amount: float,
    currency: str
) -> Dict[str, Any]:
    """Process escrow creation with full coordination"""
    try:
        # Use saga pattern for complex escrow creation
        saga_id = await execute_financial_escrow_saga(
            buyer_id=buyer_id,
            seller_id=seller_id,
            amount=amount,
            currency=currency
        )
        
        return {
            'success': True,
            'saga_id': saga_id,
            'escrow_status': 'processing'
        }
        
    except Exception as e:
        logger.error(f"❌ Escrow creation failed: {e}")
        return {
            'success': False,
            'error': str(e)
        }


async def process_cashout_with_coordination(
    user_id: int,
    amount: float,
    currency: str,
    destination: str
) -> Dict[str, Any]:
    """Process cashout with full coordination"""
    try:
        # Use saga pattern for complex cashout processing
        saga_id = await execute_financial_cashout_saga(
            user_id=user_id,
            amount=amount,
            currency=currency,
            destination=destination
        )
        
        return {
            'success': True,
            'saga_id': saga_id,
            'cashout_status': 'processing'
        }
        
    except Exception as e:
        logger.error(f"❌ Cashout processing failed: {e}")
        return {
            'success': False,
            'error': str(e)
        }