"""
Distributed Lock Service for Payment Confirmations
Prevents race conditions in webhook processing by implementing database-backed locking
"""

import logging
import time
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from contextlib import contextmanager

from sqlalchemy.exc import IntegrityError

from models import DistributedLock
from database import SessionLocal

logger = logging.getLogger(__name__)


class DistributedLockService:
    """Service for managing distributed locks to prevent race conditions"""
    
    def __init__(self, default_timeout: int = 300):  # 5 minutes default timeout
        self.default_timeout = default_timeout
        self.service_id = f"lockservice_{int(time.time())}"
    
    def generate_lock_key(self, lock_type: str, identifier: str, additional_key: str = "") -> str:
        """Generate unique lock key for a specific operation"""
        key_data = f"{lock_type}:{identifier}:{additional_key}"
        return hashlib.sha256(key_data.encode()).hexdigest()[:32]
    
    @contextmanager
    def acquire_payment_lock(self, 
                           order_id: str, 
                           txid: str,
                           user_id: Optional[int] = None,
                           timeout: Optional[int] = None,
                           additional_data: Optional[Dict[str, Any]] = None):
        """
        CRITICAL: Acquire distributed lock for payment processing
        
        Args:
            order_id: Order identifier (escrow_id or exchange_order_id)
            txid: Transaction hash for unique identification
            user_id: User ID for debugging
            timeout: Lock timeout in seconds
            additional_data: Extra debugging data
        
        Usage:
            with lock_service.acquire_payment_lock(order_id, txid) as lock:
                if lock.acquired:
                    # Process payment safely
                    pass
        """
        lock_timeout = timeout or self.default_timeout
        lock_key = self.generate_lock_key("payment_confirmation", order_id, txid)
        lock_result = LockResult(acquired=False, lock_key=lock_key)
        
        try:
            # Attempt to acquire lock
            session = SessionLocal()
            try:
                expires_at = datetime.now(timezone.utc) + timedelta(seconds=lock_timeout)
                
                # Create lock record (using actual database schema)
                lock_record = DistributedLock(
                    lock_name=lock_key,
                    locked_at=datetime.now(timezone.utc),  # FIXED: Use locked_at instead of acquired_at
                    expires_at=expires_at,
                    locked_by=self.service_id,  # FIXED: Use locked_by instead of owner_token
                    lock_metadata=additional_data if additional_data else None  # FIXED: Use lock_metadata field
                )
                
                try:
                    session.add(lock_record)
                    session.commit()
                    
                    lock_result.acquired = True
                    lock_result.lock_key = lock_key
                    
                    logger.critical(
                        f"DISTRIBUTED_LOCK_ACQUIRED: Key={lock_key}, Order={order_id}, "
                        f"TXID={txid}, Service={self.service_id}, Timeout={lock_timeout}s"
                    )
                    
                except IntegrityError:
                    # Lock already exists - check if it's expired
                    session.rollback()
                    existing_lock = session.query(DistributedLock).filter(
                        DistributedLock.lock_name == lock_key
                    ).first()
                    
                    if existing_lock:
                        if datetime.now(timezone.utc) > existing_lock.expires_at:
                            # Lock expired - try to clean it up and retry
                            logger.warning(
                                f"EXPIRED_LOCK_CLEANUP: Key={lock_key}, "
                                f"Expired={existing_lock.expires_at}, Service={existing_lock.locked_by}"
                            )
                            
                            try:
                                session.delete(existing_lock)  # FIXED: Simplified - just delete expired lock
                                session.commit()
                                
                                # Retry lock acquisition
                                session.add(lock_record)
                                session.commit()
                                
                                lock_result.acquired = True
                                lock_result.lock_key = lock_key
                                
                                logger.critical(
                                    f"DISTRIBUTED_LOCK_ACQUIRED_AFTER_CLEANUP: Key={lock_key}, "
                                    f"Order={order_id}, Service={self.service_id}"
                                )
                                
                            except Exception as cleanup_error:
                                logger.error(f"Failed to cleanup expired lock: {cleanup_error}")
                                lock_result.acquired = False
                                lock_result.error = f"Lock cleanup failed: {cleanup_error}"
                        else:
                            # Active lock exists
                            lock_result.acquired = False
                            lock_result.error = f"Payment already being processed by {existing_lock.locked_by}"
                            
                            logger.warning(
                                f"DISTRIBUTED_LOCK_COLLISION: Key={lock_key}, Order={order_id}, "
                                f"ExistingService={existing_lock.locked_by}, "
                                f"LockAge={(datetime.now(timezone.utc) - existing_lock.locked_at).total_seconds():.1f}s"
                            )
            finally:
                session.close()
            
            yield lock_result
            
        finally:
            # Always release lock when done
            if lock_result.acquired:
                self._release_lock(lock_result.lock_key)
    
    def _release_lock(self, lock_key: str):
        """Release acquired lock using lock key to avoid detached instance errors"""
        try:
            session = SessionLocal()
            try:
                # Query the lock record fresh to avoid detached instance issues
                lock_record = session.query(DistributedLock).filter(
                    DistributedLock.lock_name == lock_key
                ).first()
                
                if lock_record:
                    # Delete the lock record (simplified - no need for inactive marking)
                    session.delete(lock_record)
                    session.commit()
                    
                    logger.info(
                        f"DISTRIBUTED_LOCK_RELEASED: Key={lock_key}, "
                        f"Service={self.service_id}"
                    )
                else:
                    logger.warning(f"Lock record not found for key {lock_key} during release")
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"Failed to release lock {lock_key}: {e}")
    
    def cleanup_expired_locks(self, max_age_hours: int = 24) -> int:
        """Clean up expired locks older than specified hours"""
        try:
            cutoff_time = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
            
            session = SessionLocal()
            try:
                expired_locks = session.query(DistributedLock).filter(
                    DistributedLock.expires_at < cutoff_time
                ).all()
                
                count = len(expired_locks)
                for lock in expired_locks:
                    session.delete(lock)  # FIXED: Simplified cleanup
                
                session.commit()
                
                if count > 0:
                    logger.info(f"Cleaned up {count} expired distributed locks")
                
                return count
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"Failed to cleanup expired locks: {e}")
            return 0
    
    def get_active_locks(self) -> list:
        """Get all currently active locks for monitoring"""
        try:
            session = SessionLocal()
            try:
                active_locks = session.query(DistributedLock).filter(
                    DistributedLock.expires_at > datetime.now(timezone.utc)
                ).all()
                
                return [{
                    "lock_name": lock.lock_name,
                    "locked_by": lock.locked_by,
                    "locked_at": lock.locked_at.isoformat() if lock.locked_at else None,
                    "expires_at": lock.expires_at.isoformat(),
                    "age_seconds": (datetime.now(timezone.utc) - lock.locked_at).total_seconds() if lock.locked_at else 0
                } for lock in active_locks]
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"Failed to get active locks: {e}")
            return []


class LockResult:
    """Result object for lock acquisition attempts"""
    
    def __init__(self, acquired: bool, lock_key: str):
        self.acquired = acquired
        self.lock_key = lock_key
        self.error: Optional[str] = None
        self.lock_record: Optional[DistributedLock] = None


# Global instance for service-wide use
distributed_lock_service = DistributedLockService()