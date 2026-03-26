"""
Enhanced Escrow Idempotency System
Prevents duplicate escrow creation and ensures consistent operations
"""

import logging
import hashlib
from typing import Dict
from datetime import datetime, timedelta
from contextlib import contextmanager
from sqlalchemy import text

logger = logging.getLogger(__name__)

class EscrowIdempotencyManager:
    """
    Manages idempotency for escrow creation and critical operations
    """
    
    def __init__(self):
        # In-memory cache for recent operations
        self._operation_cache: Dict[str, datetime] = {}
        self._cache_duration = timedelta(minutes=15)  # Keep operations for 15 minutes
    
    def generate_escrow_key(self, user_id: int, amount: float, currency: str, 
                           participant_info: str = "", context: str = "") -> str:
        """
        Generate idempotency key for escrow creation
        
        Args:
            user_id: Creator user ID
            amount: Escrow amount
            currency: Currency code
            participant_info: Participant information
            context: Additional context (e.g., button_click, api_call)
        
        Returns:
            Unique idempotency key
        """
        # Create deterministic hash of escrow data
        key_data = f"{user_id}_{amount}_{currency}_{participant_info}_{context}_{int(datetime.now().timestamp() / 300)}"  # 5-minute window
        escrow_hash = hashlib.sha256(key_data.encode()).hexdigest()[:16]
        
        return f"escrow_{user_id}_{escrow_hash}"
    
    def is_duplicate_operation(self, idempotency_key: str) -> bool:
        """Check if operation is duplicate within the cache window"""
        
        # Clean expired entries
        self._cleanup_cache()
        
        if idempotency_key in self._operation_cache:
            logger.info(f"Duplicate operation detected: {idempotency_key}")
            return True
        
        # Record this operation
        self._operation_cache[idempotency_key] = datetime.now()
        return False
    
    def _cleanup_cache(self):
        """Remove expired entries from cache"""
        cutoff_time = datetime.now() - self._cache_duration
        expired_keys = [
            key for key, timestamp in self._operation_cache.items()
            if timestamp < cutoff_time
        ]
        
        for key in expired_keys:
            del self._operation_cache[key]
    
    @contextmanager
    def escrow_creation_lock(self, session, user_id: int, escrow_params: Dict):
        """
        Context manager to ensure atomic escrow creation with database-level constraints
        """
        try:
            # Create database-level advisory lock based on user and operation
            lock_id = hash(f"escrow_creation_{user_id}_{escrow_params.get('amount')}_{escrow_params.get('currency')}") % 2147483647
            
            # Acquire advisory lock
            result = session.execute(text("SELECT pg_try_advisory_lock(:lock_id)"), {"lock_id": lock_id})
            lock_acquired = result.scalar()
            
            if not lock_acquired:
                raise RuntimeError("Could not acquire escrow creation lock - operation in progress")
            
            logger.debug(f"Acquired escrow creation lock for user {user_id}")
            
            yield
            
        except Exception as e:
            logger.error(f"Error in escrow creation lock: {e}")
            raise
        finally:
            # Always release the lock
            try:
                session.execute(text("SELECT pg_advisory_unlock(:lock_id)"), {"lock_id": lock_id})
                logger.debug(f"Released escrow creation lock for user {user_id}")
            except Exception as e:
                logger.error(f"Error releasing escrow creation lock: {e}")
    
    def ensure_escrow_uniqueness_constraints(self, session):
        """
        Ensure database constraints exist to prevent duplicate escrows
        """
        constraints_sql = [
            # Prevent duplicate active escrows with same parameters within timeframe
            """
            CREATE INDEX IF NOT EXISTS idx_escrow_duplicate_prevention 
            ON escrows(creator_id, amount, currency, created_at) 
            WHERE status IN ('CREATED', 'PAYMENT_PENDING', 'ACTIVE')
            """,
            
            # Unique constraint on escrow_id to prevent duplicates
            """
            ALTER TABLE escrows 
            ADD CONSTRAINT uk_escrows_escrow_id UNIQUE (escrow_id)
            ON CONFLICT DO NOTHING
            """
        ]
        
        for constraint_sql in constraints_sql:
            try:
                session.execute(text(constraint_sql))
                session.commit()
                logger.debug("Applied escrow uniqueness constraint")
            except Exception as e:
                logger.debug(f"Constraint may already exist: {e}")
                session.rollback()

class TransactionIdempotencyManager:
    """
    Manages idempotency for financial transactions
    """
    
    def __init__(self):
        self._transaction_cache: Dict[str, datetime] = {}
        self._cache_duration = timedelta(hours=1)  # Keep transaction records longer
    
    def generate_transaction_key(self, user_id: int, transaction_type: str, 
                                amount: float, currency: str, reference_id: str = "") -> str:
        """Generate idempotency key for financial transactions"""
        
        key_data = f"{user_id}_{transaction_type}_{amount}_{currency}_{reference_id}_{int(datetime.now().timestamp() / 60)}"  # 1-minute window
        tx_hash = hashlib.sha256(key_data.encode()).hexdigest()[:16]
        
        return f"tx_{user_id}_{transaction_type}_{tx_hash}"
    
    def is_duplicate_transaction(self, idempotency_key: str) -> bool:
        """Check if transaction is duplicate"""
        
        # Clean expired entries
        cutoff_time = datetime.now() - self._cache_duration
        expired_keys = [
            key for key, timestamp in self._transaction_cache.items()
            if timestamp < cutoff_time
        ]
        
        for key in expired_keys:
            del self._transaction_cache[key]
        
        if idempotency_key in self._transaction_cache:
            logger.warning(f"Duplicate transaction detected: {idempotency_key}")
            return True
        
        self._transaction_cache[idempotency_key] = datetime.now()
        return False

class WalletOperationIdempotency:
    """
    Manages idempotency for wallet operations (credits, debits)
    """
    
    def __init__(self):
        self._wallet_operations: Dict[str, datetime] = {}
        self._cache_duration = timedelta(minutes=30)
    
    def generate_wallet_operation_key(self, user_id: int, operation: str, 
                                    amount: float, currency: str, source: str = "") -> str:
        """Generate idempotency key for wallet operations"""
        
        key_data = f"{user_id}_{operation}_{amount}_{currency}_{source}_{int(datetime.now().timestamp() / 30)}"  # 30-second window
        wallet_hash = hashlib.sha256(key_data.encode()).hexdigest()[:16]
        
        return f"wallet_{user_id}_{operation}_{wallet_hash}"
    
    def is_duplicate_wallet_operation(self, idempotency_key: str) -> bool:
        """Check if wallet operation is duplicate"""
        
        # Cleanup expired operations
        cutoff_time = datetime.now() - self._cache_duration
        self._wallet_operations = {
            k: v for k, v in self._wallet_operations.items()
            if v > cutoff_time
        }
        
        if idempotency_key in self._wallet_operations:
            logger.warning(f"Duplicate wallet operation detected: {idempotency_key}")
            return True
        
        self._wallet_operations[idempotency_key] = datetime.now()
        return False

# Global instances
escrow_idempotency = EscrowIdempotencyManager()
transaction_idempotency = TransactionIdempotencyManager()
wallet_idempotency = WalletOperationIdempotency()