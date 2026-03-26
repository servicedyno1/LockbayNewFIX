"""
Database Advisory Locks for Redis Fallback
PostgreSQL advisory lock implementation for safe distributed coordination when Redis is unavailable
Prevents split-brain scenarios in multi-instance deployments
"""

import logging
import hashlib
import time
from typing import Optional, Dict, Any
from contextlib import asynccontextmanager
from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError
from database import engine

logger = logging.getLogger(__name__)


class DBAdvisoryLockError(Exception):
    """Base exception for database advisory lock operations"""
    pass


class DBAdvisoryLockTimeoutError(DBAdvisoryLockError):
    """Exception raised when advisory lock acquisition times out"""
    pass


class DBAdvisoryLockService:
    """
    PostgreSQL Advisory Lock Service
    
    Provides safe distributed locking using PostgreSQL advisory locks as fallback
    when Redis is unavailable. Ensures exactly-once semantics for financial operations.
    """
    
    # Lock namespace to avoid conflicts (32-bit integer for PostgreSQL advisory locks)
    LOCKBAY_NAMESPACE = 0x4C4F434B  # 'LOCK' in hex
    
    # Lock timeout configurations
    DEFAULT_LOCK_TIMEOUT = 30  # seconds
    FINANCIAL_LOCK_TIMEOUT = 60  # seconds for financial operations
    
    def __init__(self):
        # Track active locks for monitoring and cleanup
        self.active_locks: Dict[int, Dict[str, Any]] = {}
        self.metrics = {
            'locks_acquired': 0,
            'locks_failed': 0,
            'locks_timed_out': 0,
            'locks_released': 0,
            'advisory_lock_errors': 0
        }
    
    @staticmethod
    def _generate_lock_id(key: str) -> int:
        """
        Generate deterministic 32-bit lock ID from string key
        
        Args:
            key: String key to generate lock ID for
            
        Returns:
            32-bit integer lock ID for PostgreSQL advisory locks
        """
        # Create hash of the key
        key_hash = hashlib.sha256(key.encode('utf-8')).hexdigest()
        
        # Take first 8 hex characters and combine with namespace
        key_portion = int(key_hash[:8], 16)
        
        # Combine namespace and key to create unique 64-bit lock ID
        # For PostgreSQL pg_advisory_lock, we use the namespace as first param
        return key_portion & 0x7FFFFFFF  # Ensure positive 32-bit integer
    
    def acquire_lock(
        self,
        session: Session,
        lock_key: str,
        timeout_seconds: Optional[int] = None,
        is_financial: bool = False
    ) -> bool:
        """
        Acquire PostgreSQL advisory lock (synchronous)
        
        Args:
            session: Database session
            lock_key: Unique key for the lock
            timeout_seconds: Maximum time to wait for lock (default: 30s)
            is_financial: Whether this is a financial operation (longer timeout)
            
        Returns:
            True if lock acquired, False if failed
        """
        timeout = timeout_seconds or (self.FINANCIAL_LOCK_TIMEOUT if is_financial else self.DEFAULT_LOCK_TIMEOUT)
        lock_id = self._generate_lock_id(lock_key)
        
        try:
            start_time = time.time()
            
            # Set statement timeout for this lock operation
            session.execute(text(f"SET LOCAL statement_timeout = '{timeout}s'"))
            
            # Try to acquire advisory lock with timeout
            # pg_try_advisory_lock returns immediately (non-blocking)
            # pg_advisory_lock blocks until acquired or timeout
            result = session.execute(
                text("SELECT pg_advisory_lock(:namespace, :lock_id)"),
                {"namespace": self.LOCKBAY_NAMESPACE, "lock_id": lock_id}
            ).scalar()
            
            elapsed = time.time() - start_time
            
            if result is not None:  # Lock acquired
                self.active_locks[lock_id] = {
                    'key': lock_key,
                    'acquired_at': time.time(),
                    'timeout': timeout,
                    'is_financial': is_financial,
                    'session_id': id(session)
                }
                
                logger.info(f"🔒 DB_LOCK_ACQUIRED: {lock_key} (ID: {lock_id}) in {elapsed:.3f}s")
                self.metrics['locks_acquired'] += 1
                return True
            else:
                logger.warning(f"🕐 DB_LOCK_FAILED: {lock_key} (timeout: {timeout}s)")
                self.metrics['locks_failed'] += 1
                return False
                
        except OperationalError as e:
            if "timeout" in str(e).lower() or "statement_timeout" in str(e).lower():
                logger.error(f"🕐 DB_LOCK_TIMEOUT: {lock_key} after {timeout}s")
                self.metrics['locks_timed_out'] += 1
                return False
            else:
                logger.error(f"❌ DB_LOCK_ERROR: {lock_key} - {e}")
                self.metrics['advisory_lock_errors'] += 1
                return False
        except Exception as e:
            logger.error(f"❌ DB_LOCK_UNEXPECTED: {lock_key} - {e}")
            self.metrics['advisory_lock_errors'] += 1
            return False
        finally:
            # Reset statement timeout
            try:
                session.execute(text("SET LOCAL statement_timeout = DEFAULT"))
            except Exception as e:
                logger.debug(f"Could not reset statement timeout: {e}")
                pass
    
    def release_lock(self, session: Session, lock_key: str) -> bool:
        """
        Release PostgreSQL advisory lock
        
        Args:
            session: Database session
            lock_key: Key of the lock to release
            
        Returns:
            True if lock released successfully, False otherwise
        """
        lock_id = self._generate_lock_id(lock_key)
        
        try:
            # Release the advisory lock
            result = session.execute(
                text("SELECT pg_advisory_unlock(:namespace, :lock_id)"),
                {"namespace": self.LOCKBAY_NAMESPACE, "lock_id": lock_id}
            ).scalar()
            
            if result:
                # Remove from active locks tracking
                if lock_id in self.active_locks:
                    lock_info = self.active_locks.pop(lock_id)
                    held_duration = time.time() - lock_info['acquired_at']
                    logger.info(f"🔓 DB_LOCK_RELEASED: {lock_key} (held: {held_duration:.3f}s)")
                else:
                    logger.info(f"🔓 DB_LOCK_RELEASED: {lock_key}")
                
                self.metrics['locks_released'] += 1
                return True
            else:
                logger.warning(f"⚠️ DB_LOCK_NOT_HELD: {lock_key} was not held by this session")
                return False
                
        except Exception as e:
            logger.error(f"❌ DB_LOCK_RELEASE_ERROR: {lock_key} - {e}")
            self.metrics['advisory_lock_errors'] += 1
            return False
    
    def try_acquire_lock(self, session: Session, lock_key: str, is_financial: bool = False) -> bool:
        """
        Try to acquire advisory lock without blocking (non-blocking)
        
        Args:
            session: Database session
            lock_key: Unique key for the lock
            is_financial: Whether this is a financial operation
            
        Returns:
            True if lock acquired immediately, False if already held
        """
        lock_id = self._generate_lock_id(lock_key)
        
        try:
            # Try to acquire lock without blocking
            result = session.execute(
                text("SELECT pg_try_advisory_lock(:namespace, :lock_id)"),
                {"namespace": self.LOCKBAY_NAMESPACE, "lock_id": lock_id}
            ).scalar()
            
            if result:  # Lock acquired
                self.active_locks[lock_id] = {
                    'key': lock_key,
                    'acquired_at': time.time(),
                    'timeout': 0,  # No timeout for non-blocking
                    'is_financial': is_financial,
                    'session_id': id(session)
                }
                
                logger.debug(f"🔒 DB_LOCK_TRY_SUCCESS: {lock_key}")
                self.metrics['locks_acquired'] += 1
                return True
            else:
                logger.debug(f"🔒 DB_LOCK_TRY_BUSY: {lock_key} (already held)")
                return False
                
        except Exception as e:
            logger.error(f"❌ DB_LOCK_TRY_ERROR: {lock_key} - {e}")
            self.metrics['advisory_lock_errors'] += 1
            return False
    
    @asynccontextmanager
    async def advisory_lock(
        self,
        lock_key: str,
        timeout_seconds: Optional[int] = None,
        is_financial: bool = False
    ):
        """
        Async context manager for advisory locks
        
        Args:
            lock_key: Unique key for the lock
            timeout_seconds: Maximum time to wait for lock
            is_financial: Whether this is a financial operation
            
        Yields:
            Database session with the acquired lock
            
        Raises:
            DBAdvisoryLockTimeoutError: If lock cannot be acquired within timeout
            DBAdvisoryLockError: If lock acquisition fails
        """
        from database import SessionLocal
        
        session = SessionLocal()
        lock_acquired = False
        
        try:
            # Acquire the lock
            lock_acquired = self.acquire_lock(session, lock_key, timeout_seconds, is_financial)
            
            if not lock_acquired:
                raise DBAdvisoryLockTimeoutError(f"Failed to acquire advisory lock: {lock_key}")
            
            # Yield the session with the acquired lock
            yield session
            
        except Exception as e:
            logger.error(f"❌ DB_ADVISORY_LOCK_CONTEXT_ERROR: {lock_key} - {e}")
            session.rollback()
            raise
        finally:
            # Always attempt to release the lock if acquired
            if lock_acquired:
                try:
                    self.release_lock(session, lock_key)
                except Exception as e:
                    logger.error(f"❌ DB_LOCK_CLEANUP_ERROR: {lock_key} - {e}")
            
            # Close the session
            try:
                session.close()
            except Exception as e:
                logger.error(f"❌ DB_SESSION_CLEANUP_ERROR: {e}")
    
    def cleanup_orphaned_locks(self) -> Dict[str, Any]:
        """
        Clean up any orphaned locks from dead sessions
        
        Returns:
            Dictionary with cleanup statistics
        """
        try:
            with engine.begin() as conn:
                # Query for all advisory locks held by this application
                result = conn.execute(text("""
                    SELECT 
                        locktype, 
                        classid as namespace,
                        objid as lock_id,
                        pid,
                        granted,
                        mode
                    FROM pg_locks 
                    WHERE locktype = 'advisory' 
                    AND classid = :namespace
                    AND granted = true
                """), {"namespace": self.LOCKBAY_NAMESPACE}).fetchall()
                
                orphaned_count = 0
                active_count = len(result)
                
                # Check if our tracked locks still match database state
                db_lock_ids = {row.lock_id for row in result}
                tracked_lock_ids = set(self.active_locks.keys())
                
                # Remove tracking for locks no longer in database
                for lock_id in list(tracked_lock_ids):
                    if lock_id not in db_lock_ids:
                        removed_lock = self.active_locks.pop(lock_id, None)
                        if removed_lock:
                            logger.info(f"🧹 DB_LOCK_ORPHAN_CLEANUP: Removed tracking for lock {removed_lock['key']}")
                            orphaned_count += 1
                
                return {
                    "active_locks": active_count,
                    "orphaned_cleaned": orphaned_count,
                    "tracked_locks": len(self.active_locks),
                    "cleanup_time": time.time()
                }
                
        except Exception as e:
            logger.error(f"❌ DB_LOCK_CLEANUP_ERROR: {e}")
            return {
                "error": str(e),
                "cleanup_time": time.time()
            }
    
    def get_lock_status(self) -> Dict[str, Any]:
        """Get detailed status of all advisory locks for monitoring"""
        try:
            with engine.begin() as conn:
                # Query current advisory locks from database
                result = conn.execute(text("""
                    SELECT 
                        l.classid as namespace,
                        l.objid as lock_id,
                        l.pid,
                        l.granted,
                        l.mode,
                        a.state,
                        a.query_start,
                        a.state_change
                    FROM pg_locks l
                    LEFT JOIN pg_stat_activity a ON l.pid = a.pid
                    WHERE l.locktype = 'advisory' 
                    AND l.classid = :namespace
                    ORDER BY l.granted DESC, a.query_start ASC
                """), {"namespace": self.LOCKBAY_NAMESPACE}).fetchall()
                
                db_locks = []
                for row in result:
                    # Find corresponding tracked lock
                    tracked = self.active_locks.get(row.lock_id)
                    
                    db_locks.append({
                        "lock_id": row.lock_id,
                        "key": tracked['key'] if tracked else f"untracked_{row.lock_id}",
                        "pid": row.pid,
                        "granted": row.granted,
                        "mode": row.mode,
                        "state": row.state,
                        "query_start": row.query_start,
                        "state_change": row.state_change,
                        "tracked": bool(tracked),
                        "is_financial": tracked.get('is_financial', False) if tracked else None,
                        "acquired_at": tracked.get('acquired_at') if tracked else None
                    })
                
                return {
                    "total_locks": len(db_locks),
                    "granted_locks": sum(1 for lock in db_locks if lock['granted']),
                    "tracked_locks": len(self.active_locks),
                    "db_locks": db_locks,
                    "metrics": self.metrics.copy(),
                    "status_time": time.time()
                }
                
        except Exception as e:
            logger.error(f"❌ DB_LOCK_STATUS_ERROR: {e}")
            return {
                "error": str(e),
                "tracked_locks": len(self.active_locks),
                "metrics": self.metrics.copy(),
                "status_time": time.time()
            }


# Global instance for the application
db_advisory_locks = DBAdvisoryLockService()


# Convenience functions for common use cases

def acquire_financial_lock(session: Session, operation_key: str, timeout: int = 60) -> bool:
    """
    Acquire advisory lock for financial operations with longer timeout
    
    Args:
        session: Database session
        operation_key: Unique key for the financial operation
        timeout: Timeout in seconds (default: 60s for financial ops)
        
    Returns:
        True if lock acquired, False otherwise
    """
    return db_advisory_locks.acquire_lock(session, f"financial_{operation_key}", timeout, is_financial=True)


def release_financial_lock(session: Session, operation_key: str) -> bool:
    """Release advisory lock for financial operations"""
    return db_advisory_locks.release_lock(session, f"financial_{operation_key}")


@asynccontextmanager
async def financial_operation_lock(operation_key: str, timeout: int = 60):
    """
    Async context manager for financial operation locks
    
    Usage:
        async with financial_operation_lock("cashout_user_123") as session:
            # Perform financial operation with session
            # Lock is automatically released when context exits
    """
    async with db_advisory_locks.advisory_lock(
        f"financial_{operation_key}", 
        timeout_seconds=timeout, 
        is_financial=True
    ) as session:
        yield session


def get_advisory_lock_metrics() -> Dict[str, Any]:
    """Get advisory lock metrics for monitoring"""
    return db_advisory_locks.get_lock_status()