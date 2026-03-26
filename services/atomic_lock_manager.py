"""
Atomic Lock Manager Service
Provides TRUE atomic distributed locking using database unique constraints
Replaces vulnerable key-value store based coordination system
"""

import logging
import uuid
import json
import asyncio
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from enum import Enum

from sqlalchemy import select, delete, update, and_
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from database import SessionLocal
from models import DistributedLock, IdempotencyToken

logger = logging.getLogger(__name__)


class LockOperationType(Enum):
    """Types of operations that require atomic locking"""
    FINANCIAL_OPERATION = "financial_operation"
    CRYPTO_ADDRESS_GENERATION = "crypto_address_generation" 
    WALLET_BALANCE_UPDATE = "wallet_balance_update"
    ESCROW_STATUS_CHANGE = "escrow_status_change"
    TRANSACTION_PROCESSING = "transaction_processing"
    CASHOUT_PROCESSING = "cashout_processing"
    CIRCUIT_BREAKER_STATE = "circuit_breaker_state"


class AtomicLockManager:
    """
    Database-backed atomic lock manager with true atomicity guarantees
    
    Uses database unique constraints to prevent race conditions that are
    inherent in key-value store get→check→set patterns.
    """
    
    def __init__(self):
        self.session_factory = SessionLocal
        self.cleanup_interval = 300  # 5 minutes
        self.default_lock_timeout = 60  # 1 minute
        self.max_lock_duration = 3600  # 1 hour safety limit
        
        # Performance metrics
        self.metrics = {
            'locks_acquired': 0,
            'locks_failed': 0,
            'locks_released': 0,
            'lock_contentions': 0,
            'cleanup_operations': 0,
            'idempotency_checks': 0,
            'idempotency_duplicates': 0
        }
    
    async def acquire_lock(
        self,
        lock_name: str,
        operation_type: LockOperationType,
        resource_id: str = None,
        timeout_seconds: int = None,
        metadata: Dict[str, Any] = None
    ) -> Optional[str]:
        """
        Acquire an atomic distributed lock using database constraints
        
        Args:
            lock_name: Unique name for the lock
            operation_type: Type of operation requiring the lock
            resource_id: ID of resource being locked (e.g., user_id, transaction_id)
            timeout_seconds: Lock timeout (default: 60 seconds)
            metadata: Additional metadata for debugging
            
        Returns:
            Lock token if successful, None if lock could not be acquired
        """
        timeout = timeout_seconds or self.default_lock_timeout
        if timeout > self.max_lock_duration:
            timeout = self.max_lock_duration
            logger.warning(f"Lock timeout capped at {self.max_lock_duration}s for {lock_name}")
        
        owner_token = str(uuid.uuid4())
        expires_at = datetime.utcnow() + timedelta(seconds=timeout)
        process_id = f"worker_{uuid.uuid4().hex[:8]}"
        
        async with self.session_factory() as session:
            try:
                # Clean up expired locks first (opportunistic cleanup)
                await self._cleanup_expired_locks(session)
                
                # Create lock record - this will fail atomically if lock already exists
                lock_record = DistributedLock(
                    lock_name=lock_name,
                    owner_token=owner_token,
                    expires_at=expires_at,
                    operation_type=operation_type.value,
                    resource_id=resource_id,
                    process_id=process_id,
                    is_active=True,
                    metadata_json=json.dumps(metadata) if metadata else None
                )
                
                session.add(lock_record)
                await session.commit()
                
                self.metrics['locks_acquired'] += 1
                logger.info(
                    f"🔒 ATOMIC_LOCK_ACQUIRED: {lock_name} "
                    f"[{operation_type.value}] token={owner_token[:8]}... "
                    f"expires_in={timeout}s resource={resource_id}"
                )
                
                return owner_token
                
            except IntegrityError:
                # Lock already exists - this is the atomic guarantee in action
                await session.rollback()
                self.metrics['lock_contentions'] += 1
                
                # Check if lock is expired (race condition handling)
                try:
                    result = await session.execute(
                        select(DistributedLock).filter(
                            DistributedLock.lock_name == lock_name
                        )
                    )
                    existing_lock = result.scalar_one_or_none()
                    
                    if existing_lock and existing_lock.expires_at < datetime.utcnow():
                        logger.info(f"🔄 ATOMIC_LOCK_EXPIRED: Attempting cleanup for {lock_name}")
                        # Try to clean up and retry once
                        await self._cleanup_expired_locks(session)
                        return await self.acquire_lock(lock_name, operation_type, resource_id, timeout_seconds, metadata)
                    else:
                        logger.debug(f"⏳ ATOMIC_LOCK_CONTENTION: {lock_name} already held")
                        
                except SQLAlchemyError as cleanup_error:
                    logger.error(f"❌ Error checking expired lock {lock_name}: {cleanup_error}")
                
                self.metrics['locks_failed'] += 1
                return None
                
            except SQLAlchemyError as e:
                await session.rollback()
                self.metrics['locks_failed'] += 1
                logger.error(f"❌ ATOMIC_LOCK_ERROR: Failed to acquire {lock_name}: {e}")
                return None
            

    
    async def release_lock(self, lock_name: str, owner_token: str) -> bool:
        """
        Release an atomic distributed lock
        
        Args:
            lock_name: Name of the lock to release
            owner_token: Token proving ownership of the lock
            
        Returns:
            True if successfully released, False otherwise
        """
        async with self.session_factory() as session:
            try:
                # Update lock to released status (atomic operation)
                result = await session.execute(
                    update(DistributedLock).filter(
                        and_(
                            DistributedLock.lock_name == lock_name,
                            DistributedLock.owner_token == owner_token,
                            DistributedLock.is_active == True
                        )
                    ).values({
                        'is_active': False,
                        'released_at': datetime.utcnow(),
                        'updated_at': datetime.utcnow()
                    })
                )
                
                await session.commit()
                
                if result > 0:
                    self.metrics['locks_released'] += 1
                    logger.info(f"🔓 ATOMIC_LOCK_RELEASED: {lock_name} token={owner_token[:8]}...")
                    return True
                else:
                    logger.warning(f"⚠️ ATOMIC_LOCK_NOT_FOUND: Cannot release {lock_name} token={owner_token[:8]}...")
                    return False
                    
            except SQLAlchemyError as e:
                await session.rollback()
                logger.error(f"❌ ATOMIC_LOCK_RELEASE_ERROR: {lock_name}: {e}")
                return False

    
    async def extend_lock(
        self, 
        lock_name: str, 
        owner_token: str, 
        additional_seconds: int = 30
    ) -> bool:
        """
        Extend the timeout of an existing lock
        
        Args:
            lock_name: Name of the lock to extend
            owner_token: Token proving ownership
            additional_seconds: Additional time to add
            
        Returns:
            True if extended, False otherwise
        """
        async with self.session_factory() as session:
            try:
                new_expires_at = datetime.utcnow() + timedelta(seconds=additional_seconds)
                
                result = await session.execute(
                    update(DistributedLock).filter(
                        and_(
                            DistributedLock.lock_name == lock_name,
                            DistributedLock.owner_token == owner_token,
                            DistributedLock.is_active == True
                        )
                    ).values({
                        'expires_at': new_expires_at,
                        'updated_at': datetime.utcnow()
                    })
                )
                
                await session.commit()
                
                if result > 0:
                    logger.info(f"⏰ ATOMIC_LOCK_EXTENDED: {lock_name} +{additional_seconds}s")
                    return True
                else:
                    logger.warning(f"⚠️ ATOMIC_LOCK_EXTEND_FAILED: {lock_name} not found or not owned")
                    return False
                    
            except SQLAlchemyError as e:
                await session.rollback()
                logger.error(f"❌ ATOMIC_LOCK_EXTEND_ERROR: {lock_name}: {e}")
                return False

    
    @asynccontextmanager
    async def atomic_lock_context(
        self,
        lock_name: str,
        operation_type: LockOperationType,
        resource_id: str = None,
        timeout_seconds: int = None,
        metadata: Dict[str, Any] = None
    ):
        """
        Context manager for atomic lock operations
        
        Usage:
            async with atomic_lock_manager.atomic_lock_context(
                "financial_op_user_123",
                LockOperationType.FINANCIAL_OPERATION,
                resource_id="123"
            ) as lock_token:
                if lock_token:
                    # Perform atomic operation
                    pass
                else:
                    # Lock could not be acquired
                    raise RuntimeError("Could not acquire lock")
        """
        lock_token = await self.acquire_lock(
            lock_name, operation_type, resource_id, timeout_seconds, metadata
        )
        
        try:
            yield lock_token
        finally:
            if lock_token:
                await self.release_lock(lock_name, lock_token)
    
    async def ensure_idempotency(
        self,
        idempotency_key: str,
        operation_type: str,
        resource_id: str,
        ttl_seconds: int = 3600
    ) -> tuple[bool, Optional[Dict[str, Any]]]:
        """
        Ensure operation idempotency using database constraints
        
        Args:
            idempotency_key: Unique key for the operation
            operation_type: Type of operation
            resource_id: Resource being operated on
            ttl_seconds: How long to keep the idempotency record
            
        Returns:
            (is_duplicate, previous_result): 
            - (False, None) if this is a new operation
            - (True, result_data) if this is a duplicate operation
        """
        async with self.session_factory() as session:
            expires_at = datetime.utcnow() + timedelta(seconds=ttl_seconds)
            
            try:
                # Try to create idempotency record
                idempotency_record = IdempotencyToken(
                    idempotency_key=idempotency_key,
                    operation_type=operation_type,
                    resource_id=resource_id,
                    status='processing',
                    expires_at=expires_at
                )
                
                session.add(idempotency_record)
                await session.commit()
                
                self.metrics['idempotency_checks'] += 1
                logger.debug(f"🔑 IDEMPOTENCY_NEW: {idempotency_key} [{operation_type}]")
                return False, None
                
            except IntegrityError:
                # Duplicate detected - check existing record
                await session.rollback()
                result = await session.execute(
                    select(IdempotencyToken).filter(
                        IdempotencyToken.idempotency_key == idempotency_key
                    )
                )
                existing = result.scalar_one_or_none()
                
                if existing:
                    if existing.expires_at < datetime.utcnow():
                        # Expired, allow retry
                        logger.info(f"🔄 IDEMPOTENCY_EXPIRED: {idempotency_key}")
                        return False, None
                    
                    self.metrics['idempotency_duplicates'] += 1
                    result_data = None
                    if existing.result_data:
                        try:
                            result_data = json.loads(existing.result_data)
                        except json.JSONDecodeError:
                            pass
                    
                    logger.info(
                        f"🚫 IDEMPOTENCY_DUPLICATE: {idempotency_key} "
                        f"status={existing.status} [{operation_type}]"
                    )
                    
                    return True, result_data
                
                logger.warning(f"⚠️ IDEMPOTENCY_RACE: {idempotency_key}")
                return True, None
                
            except SQLAlchemyError as e:
                await session.rollback()
                logger.error(f"❌ IDEMPOTENCY_ERROR: {idempotency_key}: {e}")
                # Fail open - allow operation to proceed
                return False, None

    
    async def complete_idempotent_operation(
        self,
        idempotency_key: str,
        success: bool,
        result_data: Dict[str, Any] = None,
        error_message: str = None
    ) -> bool:
        """
        Mark an idempotent operation as completed
        
        Args:
            idempotency_key: The idempotency key
            success: Whether operation succeeded
            result_data: Result data to store
            error_message: Error message if failed
            
        Returns:
            True if successfully updated
        """
        async with self.session_factory() as session:
            try:
                status = 'completed' if success else 'failed'
                update_data = {
                    'status': status,
                    'completed_at': datetime.utcnow(),
                    'updated_at': datetime.utcnow()
                }
                
                if result_data:
                    update_data['result_data'] = json.dumps(result_data)
                if error_message:
                    update_data['error_message'] = error_message
                
                result = await session.execute(
                    update(IdempotencyToken).filter(
                        IdempotencyToken.idempotency_key == idempotency_key
                    ).values(update_data)
                )
                
                await session.commit()
                
                if result > 0:
                    logger.debug(f"✅ IDEMPOTENCY_COMPLETED: {idempotency_key} [{status}]")
                    return True
                else:
                    logger.warning(f"⚠️ IDEMPOTENCY_NOT_FOUND: {idempotency_key}")
                    return False
                    
            except SQLAlchemyError as e:
                await session.rollback()
                logger.error(f"❌ IDEMPOTENCY_COMPLETION_ERROR: {idempotency_key}: {e}")
                return False

    
    async def _cleanup_expired_locks(self, session: AsyncSession = None) -> int:
        """Clean up expired locks and idempotency tokens"""
        if session is None:
            async with self.session_factory() as session:
                return await self._cleanup_expired_locks(session)
        
        try:
            current_time = datetime.utcnow()
            
            # Clean up expired locks
            lock_result = await session.execute(
                update(DistributedLock).filter(
                    and_(
                        DistributedLock.expires_at < current_time,
                        DistributedLock.is_active == True
                    )
                ).values({
                    'is_active': False,
                    'released_at': current_time,
                    'updated_at': current_time
                })
            )
            
            # Clean up expired idempotency tokens
            idempotency_result = await session.execute(
                delete(IdempotencyToken).filter(
                    IdempotencyToken.expires_at < current_time
                )
            )
            
            await session.commit()
            
            total_cleaned = lock_result + idempotency_result
            if total_cleaned > 0:
                self.metrics['cleanup_operations'] += 1
                logger.info(
                    f"🧹 ATOMIC_LOCK_CLEANUP: "
                    f"locks={lock_result} idempotency={idempotency_result}"
                )
            
            return total_cleaned
            
        except SQLAlchemyError as e:
            await session.rollback()
            logger.error(f"❌ CLEANUP_ERROR: {e}")
            return 0
    
    async def get_lock_status(self, lock_name: str) -> Optional[Dict[str, Any]]:
        """Get current status of a lock"""
        async with self.session_factory() as session:
            try:
                result = await session.execute(
                    select(DistributedLock).filter(
                        DistributedLock.lock_name == lock_name
                    )
                )
                lock_record = result.scalar_one_or_none()
                
                if not lock_record:
                    return None
                
                return {
                    'lock_name': lock_record.lock_name,
                    'is_active': lock_record.is_active,
                    'operation_type': lock_record.operation_type,
                    'resource_id': lock_record.resource_id,
                    'acquired_at': lock_record.acquired_at.isoformat(),
                    'expires_at': lock_record.expires_at.isoformat(),
                    'is_expired': lock_record.expires_at < datetime.utcnow(),
                    'owner_token': lock_record.owner_token[:8] + "...",  # Partial for security
                    'metadata': json.loads(lock_record.metadata_json) if lock_record.metadata_json else None
                }
                
            except SQLAlchemyError as e:
                logger.error(f"❌ GET_LOCK_STATUS_ERROR: {lock_name}: {e}")
                return None

    
    def get_metrics(self) -> Dict[str, Any]:
        """Get performance metrics"""
        return self.metrics.copy()
    
    async def initialize(self) -> bool:
        """Initialize the atomic lock manager"""
        try:
            # Ensure database tables exist
            
            # This will create tables if they don't exist - using async create_tables from database
            from database import create_tables
            await create_tables()
            
            # Start cleanup task
            asyncio.create_task(self._periodic_cleanup())
            
            logger.info("🔒 AtomicLockManager initialized successfully")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to initialize AtomicLockManager: {e}")
            return False
    
    async def _periodic_cleanup(self):
        """Periodic cleanup of expired locks and tokens"""
        while True:
            try:
                await asyncio.sleep(self.cleanup_interval)
                await self._cleanup_expired_locks()
            except Exception as e:
                logger.error(f"❌ Periodic cleanup error: {e}")
                await asyncio.sleep(60)  # Wait before retrying


# Global atomic lock manager instance
atomic_lock_manager = AtomicLockManager()