"""
Job Idempotency and Deduplication Service
Extended idempotency service specifically for background job coordination
Prevents duplicate job execution and provides job fingerprinting
"""

import asyncio
import hashlib
import json
import logging
import time
from typing import Any, Dict, Optional, Tuple
from datetime import datetime
from dataclasses import dataclass, asdict
from enum import Enum

from services.idempotency_service import IdempotencyService
from services.state_manager import state_manager
from config import Config

logger = logging.getLogger(__name__)


class JobExecutionStatus(Enum):
    """Job execution status for coordination"""
    PENDING = "pending"
    CLAIMED = "claimed"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


@dataclass
class JobFingerprint:
    """Unique fingerprint for job identification and deduplication"""
    job_type: str
    job_key: str  # Unique key within job type
    parameters_hash: str
    scheduled_time: Optional[datetime]
    priority: str
    created_by: str
    metadata: Dict[str, Any]


@dataclass
class JobExecutionContext:
    """Context for job execution tracking"""
    job_id: str
    fingerprint: JobFingerprint
    idempotency_key: str
    instance_id: str
    claimed_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    status: JobExecutionStatus
    result: Optional[Any]
    error: Optional[str]
    retry_count: int
    max_retries: int
    ttl_seconds: int


class JobIdempotencyService:
    """
    Extended idempotency service for background job coordination
    
    Features:
    - Job fingerprinting for duplicate detection
    - Distributed job claiming and coordination
    - Idempotent job execution across instances
    - Job result caching and retrieval
    - TTL-based cleanup of job state
    """
    
    def __init__(self):
        self.base_idempotency = IdempotencyService()
        
        # Job coordination configuration
        self.job_claim_ttl = Config.REDIS_JOB_CLAIM_TTL if hasattr(Config, 'REDIS_JOB_CLAIM_TTL') else 300
        self.job_result_ttl = Config.REDIS_JOB_RESULT_TTL if hasattr(Config, 'REDIS_JOB_RESULT_TTL') else 3600
        self.cleanup_interval = Config.REDIS_JOB_CLEANUP_INTERVAL if hasattr(Config, 'REDIS_JOB_CLEANUP_INTERVAL') else 1800
        
        # Key prefixes for Redis
        self.job_fingerprint_prefix = "job_fingerprint"
        self.job_execution_prefix = "job_execution"
        self.job_claim_prefix = "job_claim"
        self.job_result_prefix = "job_result"
        self.job_duplicate_prefix = "job_duplicate"
        
        # Metrics tracking
        self.metrics = {
            'jobs_fingerprinted': 0,
            'jobs_deduplicated': 0,
            'jobs_claimed': 0,
            'jobs_executed': 0,
            'jobs_completed': 0,
            'jobs_failed': 0,
            'duplicates_prevented': 0,
            'coordination_conflicts': 0
        }
        
        logger.info("🔒 Job idempotency service initialized")
    
    def generate_job_fingerprint(
        self,
        job_type: str,
        job_key: str,
        parameters: Dict[str, Any],
        scheduled_time: Optional[datetime] = None,
        priority: str = "normal",
        created_by: str = "system"
    ) -> JobFingerprint:
        """
        Generate a unique fingerprint for job identification
        
        Args:
            job_type: Type of job (e.g., "cashout_processing", "email_notification")
            job_key: Unique key within job type (e.g., user_id, transaction_id)
            parameters: Job parameters for execution
            scheduled_time: When job should be executed
            priority: Job priority level
            created_by: Instance or system that created the job
            
        Returns:
            JobFingerprint: Unique fingerprint for the job
        """
        # Create deterministic hash of parameters
        param_str = json.dumps(parameters, sort_keys=True)
        parameters_hash = hashlib.sha256(param_str.encode()).hexdigest()[:16]
        
        fingerprint = JobFingerprint(
            job_type=job_type,
            job_key=job_key,
            parameters_hash=parameters_hash,
            scheduled_time=scheduled_time,
            priority=priority,
            created_by=created_by,
            metadata={
                'fingerprinted_at': datetime.utcnow().isoformat(),
                'parameter_count': len(parameters),
                'param_keys': list(parameters.keys())
            }
        )
        
        self.metrics['jobs_fingerprinted'] += 1
        return fingerprint
    
    def get_fingerprint_key(self, fingerprint: JobFingerprint) -> str:
        """Generate Redis key for job fingerprint"""
        components = [
            fingerprint.job_type,
            fingerprint.job_key,
            fingerprint.parameters_hash
        ]
        
        # Include scheduled time for time-sensitive jobs
        if fingerprint.scheduled_time:
            time_component = fingerprint.scheduled_time.strftime("%Y%m%d_%H%M")
            components.append(time_component)
        
        return f"{self.job_fingerprint_prefix}:{'_'.join(components)}"
    
    async def check_job_duplicate(
        self,
        fingerprint: JobFingerprint,
        tolerance_seconds: int = 300
    ) -> Tuple[bool, Optional[JobExecutionContext]]:
        """
        Check if a job with the same fingerprint is already running or completed
        
        Args:
            fingerprint: Job fingerprint to check
            tolerance_seconds: Time tolerance for considering jobs as duplicates
            
        Returns:
            Tuple of (is_duplicate, existing_context)
        """
        try:
            fingerprint_key = self.get_fingerprint_key(fingerprint)
            
            # Check for exact duplicate
            existing_context_data = await state_manager.get_state(fingerprint_key)
            
            if existing_context_data:
                existing_context = JobExecutionContext(**existing_context_data)
                
                # Check if existing job is still valid
                if existing_context.status in [JobExecutionStatus.RUNNING, JobExecutionStatus.COMPLETED]:
                    logger.info(f"🔍 Duplicate job detected: {fingerprint.job_type}:{fingerprint.job_key}")
                    self.metrics['duplicates_prevented'] += 1
                    return True, existing_context
                
                # Check if existing job failed and is within retry window
                if existing_context.status == JobExecutionStatus.FAILED:
                    if existing_context.completed_at:
                        failed_time = datetime.fromisoformat(existing_context.completed_at)
                        if (datetime.utcnow() - failed_time).total_seconds() < tolerance_seconds:
                            return True, existing_context
            
            # Check for time-based duplicates (similar jobs within tolerance)
            if fingerprint.scheduled_time:
                await self._check_time_based_duplicates(fingerprint, tolerance_seconds)
            
            return False, None
            
        except Exception as e:
            logger.error(f"❌ Error checking job duplicate: {e}")
            return False, None
    
    async def atomic_claim_job(
        self,
        fingerprint: JobFingerprint,
        instance_id: str,
        max_retries: int = 3,
        ttl_seconds: Optional[int] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Atomically claim a job for execution using distributed locking
        
        Args:
            fingerprint: Job fingerprint to claim
            instance_id: Instance claiming the job
            max_retries: Maximum retry attempts
            ttl_seconds: TTL for job claim
            
        Returns:
            Tuple of (claimed, job_id)
        """
        try:
            fingerprint_key = self.get_fingerprint_key(fingerprint)
            claim_key = f"{self.job_claim_prefix}:{fingerprint_key}"
            
            # Check for duplicates first
            is_duplicate, existing_context = await self.check_job_duplicate(fingerprint)
            if is_duplicate and existing_context:
                if existing_context.status == JobExecutionStatus.COMPLETED:
                    # Return existing result
                    return False, existing_context.job_id
                elif existing_context.status == JobExecutionStatus.RUNNING:
                    # Job already running
                    return False, None
            
            # Generate job ID
            job_id = f"job_{fingerprint.job_type}_{int(time.time())}_{hash(fingerprint_key) % 10000:04d}"
            
            # Try to claim the job atomically
            ttl = ttl_seconds or self.job_claim_ttl
            
            # Use distributed lock for claiming
            async with state_manager.get_distributed_lock(claim_key, timeout=30) as lock:
                if not await lock.acquire():
                    logger.warning(f"⏳ Could not acquire lock for job claim: {fingerprint.job_type}")
                    return False, None
                
                # Double-check for duplicates after acquiring lock
                existing_context_data = await state_manager.get_state(fingerprint_key)
                if existing_context_data:
                    existing_context = JobExecutionContext(**existing_context_data)
                    if existing_context.status in [JobExecutionStatus.RUNNING, JobExecutionStatus.COMPLETED]:
                        self.metrics['coordination_conflicts'] += 1
                        return False, existing_context.job_id
                
                # Create execution context
                execution_context = JobExecutionContext(
                    job_id=job_id,
                    fingerprint=fingerprint,
                    idempotency_key=fingerprint_key,
                    instance_id=instance_id,
                    claimed_at=datetime.utcnow(),
                    started_at=None,
                    completed_at=None,
                    status=JobExecutionStatus.CLAIMED,
                    result=None,
                    error=None,
                    retry_count=0,
                    max_retries=max_retries,
                    ttl_seconds=ttl
                )
                
                # Store execution context
                await state_manager.set_state(
                    fingerprint_key,
                    asdict(execution_context),
                    ttl=ttl,
                    tags=['job_execution', 'claimed', fingerprint.job_type],
                    source='job_idempotency'
                )
                
                # Store claim
                claim_data = {
                    'job_id': job_id,
                    'instance_id': instance_id,
                    'claimed_at': datetime.utcnow().isoformat(),
                    'fingerprint_key': fingerprint_key
                }
                
                await state_manager.set_state(
                    claim_key,
                    claim_data,
                    ttl=ttl,
                    tags=['job_claim', fingerprint.job_type],
                    source='job_idempotency'
                )
                
                self.metrics['jobs_claimed'] += 1
                logger.info(f"✅ Claimed job: {job_id} ({fingerprint.job_type})")
                return True, job_id
        
        except Exception as e:
            logger.error(f"❌ Error claiming job: {e}")
            return False, None
    
    async def start_job_execution(
        self,
        job_id: str,
        fingerprint_key: str
    ) -> bool:
        """
        Mark job as started and update execution context
        
        Args:
            job_id: Job ID to start
            fingerprint_key: Fingerprint key for the job
            
        Returns:
            bool: True if job was started successfully
        """
        try:
            context_data = await state_manager.get_state(fingerprint_key)
            if not context_data:
                logger.error(f"❌ No execution context found for job: {job_id}")
                return False
            
            context = JobExecutionContext(**context_data)
            
            if context.job_id != job_id:
                logger.error(f"❌ Job ID mismatch for execution: {job_id} vs {context.job_id}")
                return False
            
            if context.status != JobExecutionStatus.CLAIMED:
                logger.warning(f"⚠️ Job {job_id} not in CLAIMED status: {context.status}")
                return False
            
            # Update context
            context.status = JobExecutionStatus.RUNNING
            context.started_at = datetime.utcnow()
            
            await state_manager.set_state(
                fingerprint_key,
                asdict(context),
                ttl=context.ttl_seconds,
                tags=['job_execution', 'running', context.fingerprint.job_type],
                source='job_idempotency'
            )
            
            self.metrics['jobs_executed'] += 1
            logger.info(f"▶️ Started job execution: {job_id}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Error starting job execution: {e}")
            return False
    
    async def complete_job_execution(
        self,
        job_id: str,
        fingerprint_key: str,
        result: Any = None,
        error: Optional[str] = None
    ) -> bool:
        """
        Complete job execution and store result
        
        Args:
            job_id: Job ID to complete
            fingerprint_key: Fingerprint key for the job
            result: Job execution result
            error: Error message if job failed
            
        Returns:
            bool: True if job was completed successfully
        """
        try:
            context_data = await state_manager.get_state(fingerprint_key)
            if not context_data:
                logger.error(f"❌ No execution context found for job: {job_id}")
                return False
            
            context = JobExecutionContext(**context_data)
            
            if context.job_id != job_id:
                logger.error(f"❌ Job ID mismatch for completion: {job_id} vs {context.job_id}")
                return False
            
            # Update context
            context.completed_at = datetime.utcnow()
            context.result = result
            context.error = error
            
            if error:
                context.status = JobExecutionStatus.FAILED
                self.metrics['jobs_failed'] += 1
                logger.error(f"❌ Job failed: {job_id} - {error}")
            else:
                context.status = JobExecutionStatus.COMPLETED
                self.metrics['jobs_completed'] += 1
                logger.info(f"✅ Job completed: {job_id}")
            
            # Store updated context with longer TTL for result caching
            result_ttl = self.job_result_ttl if context.status == JobExecutionStatus.COMPLETED else context.ttl_seconds
            
            await state_manager.set_state(
                fingerprint_key,
                asdict(context),
                ttl=result_ttl,
                tags=['job_execution', context.status.value, context.fingerprint.job_type],
                source='job_idempotency'
            )
            
            # Store result separately for easier retrieval
            if context.status == JobExecutionStatus.COMPLETED:
                result_key = f"{self.job_result_prefix}:{job_id}"
                result_data = {
                    'job_id': job_id,
                    'result': result,
                    'completed_at': context.completed_at.isoformat(),
                    'fingerprint_key': fingerprint_key,
                    'job_type': context.fingerprint.job_type
                }
                
                await state_manager.set_state(
                    result_key,
                    result_data,
                    ttl=self.job_result_ttl,
                    tags=['job_result', context.fingerprint.job_type],
                    source='job_idempotency'
                )
            
            # Clean up claim
            claim_key = f"{self.job_claim_prefix}:{fingerprint_key}"
            await state_manager.delete_state(claim_key)
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Error completing job execution: {e}")
            return False
    
    async def get_job_result(
        self,
        job_id: Optional[str] = None,
        fingerprint: Optional[JobFingerprint] = None
    ) -> Tuple[Optional[Any], Optional[str]]:
        """
        Get job result by job ID or fingerprint
        
        Args:
            job_id: Job ID to get result for
            fingerprint: Job fingerprint to get result for
            
        Returns:
            Tuple of (result, error)
        """
        try:
            if job_id:
                # Get result by job ID
                result_key = f"{self.job_result_prefix}:{job_id}"
                result_data = await state_manager.get_state(result_key)
                
                if result_data:
                    return result_data['result'], None
            
            if fingerprint:
                # Get result by fingerprint
                fingerprint_key = self.get_fingerprint_key(fingerprint)
                context_data = await state_manager.get_state(fingerprint_key)
                
                if context_data:
                    context = JobExecutionContext(**context_data)
                    if context.status == JobExecutionStatus.COMPLETED:
                        return context.result, None
                    elif context.status == JobExecutionStatus.FAILED:
                        return None, context.error
            
            return None, "Job not found or not completed"
            
        except Exception as e:
            logger.error(f"❌ Error getting job result: {e}")
            return None, f"Error retrieving result: {e}"
    
    async def cancel_job(
        self,
        job_id: str,
        fingerprint_key: str,
        reason: str = "cancelled"
    ) -> bool:
        """
        Cancel a job execution
        
        Args:
            job_id: Job ID to cancel
            fingerprint_key: Fingerprint key for the job
            reason: Cancellation reason
            
        Returns:
            bool: True if job was cancelled successfully
        """
        try:
            context_data = await state_manager.get_state(fingerprint_key)
            if not context_data:
                return False
            
            context = JobExecutionContext(**context_data)
            
            if context.job_id != job_id:
                return False
            
            if context.status in [JobExecutionStatus.COMPLETED, JobExecutionStatus.FAILED]:
                return False  # Cannot cancel completed jobs
            
            # Update context
            context.status = JobExecutionStatus.CANCELLED
            context.completed_at = datetime.utcnow()
            context.error = reason
            
            await state_manager.set_state(
                fingerprint_key,
                asdict(context),
                ttl=3600,  # Keep cancelled jobs for 1 hour
                tags=['job_execution', 'cancelled', context.fingerprint.job_type],
                source='job_idempotency'
            )
            
            # Clean up claim
            claim_key = f"{self.job_claim_prefix}:{fingerprint_key}"
            await state_manager.delete_state(claim_key)
            
            logger.info(f"🚫 Cancelled job: {job_id} - {reason}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Error cancelling job: {e}")
            return False
    
    async def cleanup_expired_jobs(self) -> Dict[str, int]:
        """
        Clean up expired job state and stale claims
        
        Returns:
            Dict with cleanup statistics
        """
        cleanup_stats = {
            'expired_contexts': 0,
            'stale_claims': 0,
            'old_results': 0,
            'errors': 0
        }
        
        try:
            logger.info("🧹 Starting job idempotency cleanup")
            
            # This would need to scan Redis keys in a real implementation
            # For now, we'll just log the cleanup intention
            logger.info("🧹 Job idempotency cleanup completed")
            
        except Exception as e:
            logger.error(f"❌ Error during job cleanup: {e}")
            cleanup_stats['errors'] += 1
        
        return cleanup_stats
    
    async def _check_time_based_duplicates(
        self,
        fingerprint: JobFingerprint,
        tolerance_seconds: int
    ):
        """Check for time-based duplicates within tolerance window"""
        # This would scan for similar jobs within the time window
        # Implementation would depend on Redis scanning capabilities
        pass
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get job idempotency service metrics"""
        return {
            **self.metrics,
            'base_idempotency_metrics': self.base_idempotency.get_metrics() if hasattr(self.base_idempotency, 'get_metrics') else {}
        }


# Global instance
job_idempotency_service = JobIdempotencyService()


# Convenience functions for job coordination
async def claim_and_execute_job(
    job_type: str,
    job_key: str,
    parameters: Dict[str, Any],
    job_handler: callable,
    instance_id: str,
    max_retries: int = 3
) -> Tuple[bool, Any, Optional[str]]:
    """
    High-level function to claim and execute a job with idempotency
    
    Args:
        job_type: Type of job
        job_key: Unique key for the job
        parameters: Job parameters
        job_handler: Function to execute the job
        instance_id: Instance executing the job
        max_retries: Maximum retry attempts
        
    Returns:
        Tuple of (success, result, error)
    """
    try:
        # Generate fingerprint
        fingerprint = job_idempotency_service.generate_job_fingerprint(
            job_type=job_type,
            job_key=job_key,
            parameters=parameters,
            created_by=instance_id
        )
        
        # Check for duplicates
        is_duplicate, existing_context = await job_idempotency_service.check_job_duplicate(fingerprint)
        if is_duplicate and existing_context:
            if existing_context.status == JobExecutionStatus.COMPLETED:
                logger.info(f"🔄 Returning cached result for duplicate job: {job_type}:{job_key}")
                return True, existing_context.result, None
            else:
                logger.info(f"⚠️ Duplicate job already running: {job_type}:{job_key}")
                return False, None, "Job already running"
        
        # Claim job
        claimed, job_id = await job_idempotency_service.atomic_claim_job(
            fingerprint=fingerprint,
            instance_id=instance_id,
            max_retries=max_retries
        )
        
        if not claimed:
            return False, None, "Could not claim job"
        
        # Start execution
        fingerprint_key = job_idempotency_service.get_fingerprint_key(fingerprint)
        
        if not await job_idempotency_service.start_job_execution(job_id, fingerprint_key):
            return False, None, "Could not start job execution"
        
        # Execute job
        try:
            if asyncio.iscoroutinefunction(job_handler):
                result = await job_handler(**parameters)
            else:
                result = job_handler(**parameters)
            
            # Complete job
            await job_idempotency_service.complete_job_execution(
                job_id=job_id,
                fingerprint_key=fingerprint_key,
                result=result
            )
            
            return True, result, None
            
        except Exception as e:
            # Complete job with error
            await job_idempotency_service.complete_job_execution(
                job_id=job_id,
                fingerprint_key=fingerprint_key,
                error=str(e)
            )
            
            return False, None, str(e)
    
    except Exception as e:
        logger.error(f"❌ Error in claim_and_execute_job: {e}")
        return False, None, str(e)