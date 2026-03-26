"""
Idempotency Key Service
Prevents duplicate financial operations through unique key tracking
Ensures exactly-once semantics for critical financial transactions
"""

import json
import time
import hashlib
import logging
from typing import Any, Optional, Dict, List, Callable
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from enum import Enum
import asyncio

from services.state_manager import state_manager
from config import Config

# SECURITY: Database imports for fallback coordination
from database import SessionLocal
from sqlalchemy.exc import IntegrityError
from models import IdempotencyKey  # Import existing model instead of defining duplicate

# Import advisory locks for database-backed coordination
from utils.db_advisory_locks import db_advisory_locks

logger = logging.getLogger(__name__)


# SECURITY: Exception classes for secure coordination
class IdempotencySecurityError(Exception):
    """Critical security exception for coordination failures"""
    pass


# NOTE: Using existing IdempotencyKey model from models.py instead of duplicate definition
# This prevents SQLAlchemy table redefinition conflicts during imports


class OperationType(Enum):
    """Types of operations that require idempotency"""
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal" 
    CASHOUT = "cashout"
    ESCROW_CREATE = "escrow_create"
    ESCROW_RELEASE = "escrow_release"
    ESCROW_REFUND = "escrow_refund"
    EXCHANGE_CREATE = "exchange_create"
    WALLET_TRANSFER = "wallet_transfer"
    FEE_DEDUCTION = "fee_deduction"
    BALANCE_UPDATE = "balance_update"
    EXTERNAL_API_CALL = "external_api_call"


class IdempotencyStatus(Enum):
    """Status of idempotency key processing"""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"


@dataclass
class IdempotencyRecord:
    """Record of an idempotency key and its operation"""
    key: str
    operation_type: OperationType
    user_id: Optional[int]
    entity_id: Optional[str]  # escrow_id, cashout_id, etc.
    request_hash: str
    status: IdempotencyStatus
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime]
    ttl_seconds: int
    result: Optional[Dict[str, Any]]
    error: Optional[str]
    retry_count: int
    metadata: Dict[str, Any]


class IdempotencyKeyGenerator:
    """Generate idempotency keys for different operation types"""
    
    @staticmethod
    def generate_key(
        operation_type: OperationType,
        user_id: int,
        operation_data: Dict[str, Any],
        entity_id: Optional[str] = None
    ) -> str:
        """
        Generate deterministic idempotency key
        
        Args:
            operation_type: Type of operation
            user_id: User performing the operation
            operation_data: Core operation data for hashing
            entity_id: Optional entity identifier (escrow_id, cashout_id, etc.)
            
        Returns:
            str: Unique idempotency key
        """
        # Create deterministic hash of operation data
        operation_str = json.dumps(operation_data, sort_keys=True)
        operation_hash = hashlib.sha256(operation_str.encode()).hexdigest()[:16]
        
        # Build key components
        components = [
            operation_type.value,
            str(user_id),
            operation_hash
        ]
        
        if entity_id:
            components.append(str(entity_id))
        
        # Create idempotency key
        key = f"idempotency:{'_'.join(components)}"
        return key
    
    @staticmethod
    def generate_cashout_key(
        user_id: int,
        amount: float,
        currency: str,
        destination: str,
        timestamp: Optional[int] = None
    ) -> str:
        """Generate idempotency key for cashout operations"""
        operation_data = {
            'amount': amount,
            'currency': currency,
            'destination': destination,
            'timestamp': timestamp or int(time.time())
        }
        return IdempotencyKeyGenerator.generate_key(
            OperationType.CASHOUT,
            user_id,
            operation_data
        )
    
    @staticmethod
    def generate_deposit_key(
        user_id: int,
        amount: float,
        currency: str,
        source: str,
        external_transaction_id: Optional[str] = None
    ) -> str:
        """Generate idempotency key for deposit operations"""
        operation_data = {
            'amount': amount,
            'currency': currency,
            'source': source
        }
        if external_transaction_id:
            operation_data['external_tx_id'] = external_transaction_id
            
        return IdempotencyKeyGenerator.generate_key(
            OperationType.DEPOSIT,
            user_id,
            operation_data
        )
    
    @staticmethod
    def generate_escrow_key(
        user_id: int,
        amount: float,
        currency: str,
        counterparty_id: int,
        operation_subtype: str = "create"
    ) -> str:
        """Generate idempotency key for escrow operations"""
        operation_data = {
            'amount': amount,
            'currency': currency,
            'counterparty_id': counterparty_id,
            'subtype': operation_subtype
        }
        
        op_type = OperationType.ESCROW_CREATE
        if operation_subtype == "release":
            op_type = OperationType.ESCROW_RELEASE
        elif operation_subtype == "refund":
            op_type = OperationType.ESCROW_REFUND
            
        return IdempotencyKeyGenerator.generate_key(
            op_type,
            user_id,
            operation_data
        )
    
    @staticmethod
    def generate_api_call_key(
        service: str,
        endpoint: str,
        method: str,
        payload: Dict[str, Any],
        user_context: Optional[int] = None
    ) -> str:
        """Generate idempotency key for external API calls"""
        operation_data = {
            'service': service,
            'endpoint': endpoint,
            'method': method,
            'payload_hash': hashlib.sha256(
                json.dumps(payload, sort_keys=True).encode()
            ).hexdigest()[:16]
        }
        
        return IdempotencyKeyGenerator.generate_key(
            OperationType.EXTERNAL_API_CALL,
            user_context or 0,
            operation_data
        )


class IdempotencyService:
    """
    Service for managing idempotency keys and preventing duplicate operations
    """
    
    def __init__(self):
        # Use Config-based TTL values
        self.default_ttl = Config.REDIS_IDEMPOTENCY_TTL
        self.processing_timeout = Config.REDIS_PROCESSING_TIMEOUT
        
        # Metrics tracking
        self.metrics = {
            'keys_created': 0,
            'keys_found_duplicate': 0,
            'operations_completed': 0,
            'operations_failed': 0,
            'keys_expired': 0,
            'atomic_claims': 0,
            'atomic_completes': 0,
            'race_conditions_prevented': 0,
            # SECURITY: Fallback metrics
            'redis_fallback_claims': 0,
            'db_fallback_claims': 0,
            'security_blocks': 0
        }
        
        # Lua scripts for atomic operations
        self._lua_scripts = self._initialize_lua_scripts()
    
    def is_coordination_safe(self) -> bool:
        """
        CRITICAL SECURITY: Check if it's safe to process coordination operations
        
        Returns False during Redis outages unless safe fallback is available.
        This prevents split-brain scenarios in multi-instance deployments.
        """
        return state_manager.is_financial_safe()
    
    async def atomic_claim_operation_safe(
        self,
        key: str,
        operation_type: OperationType,
        request_data: Dict[str, Any],
        user_id: Optional[int] = None,
        entity_id: Optional[str] = None,
        ttl_seconds: Optional[int] = None
    ) -> tuple[bool, Optional[str]]:
        """
        CRITICAL SECURITY: Safe atomic claim with fallback handling
        
        Uses Redis when available, database-backed fallback when safe,
        or blocks operations during unsafe conditions.
        
        Args:
            key: Unique idempotency key
            operation_type: Type of operation
            request_data: Request data for hashing
            user_id: User ID associated with operation
            entity_id: Entity ID (escrow_id, cashout_id, etc.)
            ttl_seconds: Custom TTL (uses default if not provided)
            
        Returns:
            tuple: (claimed, existing_status) - claimed=True if operation claimed
        """
        # SECURITY CHECK: Verify coordination is safe
        if not self.is_coordination_safe():
            logger.critical(f"🚨 COORDINATION_UNSAFE: Blocking operation {key} ({operation_type.value})")
            logger.critical("   Redis unavailable and no safe fallback configured")
            self.metrics['security_blocks'] += 1
            raise IdempotencySecurityError(f"Cannot safely coordinate operation: {key}")
        
        # Check which backend to use
        if state_manager.is_redis_available():
            # Redis available - use normal Redis-based coordination
            logger.debug(f"🔗 Using Redis coordination for {key}")
            result = await self.atomic_claim_operation(
                key, operation_type, request_data, user_id, entity_id, ttl_seconds
            )
            if result[0]:  # Successfully claimed
                self.metrics['redis_fallback_claims'] += 1
            return result
        else:
            # Redis unavailable - use database-backed fallback
            logger.warning(f"🛡️ Using DB fallback coordination for {key}")
            result = await self.atomic_claim_operation_db(
                key, operation_type, request_data, user_id, entity_id, ttl_seconds
            )
            if result[0]:  # Successfully claimed
                self.metrics['db_fallback_claims'] += 1
            return result
    
    async def atomic_claim_operation_db(
        self,
        key: str,
        operation_type: OperationType,
        request_data: Dict[str, Any],
        user_id: Optional[int] = None,
        entity_id: Optional[str] = None,
        ttl_seconds: Optional[int] = None
    ) -> tuple[bool, Optional[str]]:
        """
        SECURITY: Database-backed atomic claim operation for Redis fallback
        
        Uses PostgreSQL UNIQUE constraints and advisory locks for atomic operations
        when Redis is unavailable. Prevents split-brain scenarios.
        
        Args:
            key: Unique idempotency key
            operation_type: Type of operation
            request_data: Request data for hashing
            user_id: User ID associated with operation
            entity_id: Entity ID (escrow_id, cashout_id, etc.)
            ttl_seconds: Custom TTL (uses default if not provided)
            
        Returns:
            tuple: (claimed, existing_status) - claimed=True if operation claimed
        """
        # Create request hash for duplicate detection
        request_hash = hashlib.sha256(
            json.dumps(request_data, sort_keys=True).encode()
        ).hexdigest()
        
        session = SessionLocal()
        
        try:
            # Use advisory lock to prevent race conditions during insertion
            lock_acquired = db_advisory_locks.acquire_lock(
                session, 
                f"idempotency_{key}",
                timeout_seconds=10,  # Short timeout for idempotency ops
                is_financial=True
            )
            
            if not lock_acquired:
                logger.error(f"❌ DB_CLAIM_LOCK_FAILED: Could not acquire advisory lock for {key}")
                return False, 'lock_error'
            
            try:
                # Check if record already exists
                existing = session.query(IdempotencyKey).filter_by(operation_key=key).first()
                
                if existing:
                    # Verify request hash matches (detect conflicting operations)
                    if existing.request_hash != request_hash:
                        logger.warning(f"🚨 DB_CLAIM_HASH_MISMATCH: {key} has different request hash")
                        self.metrics['race_conditions_prevented'] += 1
                        return False, 'hash_mismatch'
                    
                    # Return existing operation status based on completion
                    status = 'completed' if existing.success is True else ('failed' if existing.success is False else 'processing')
                    logger.debug(f"🔑 DB_CLAIM_EXISTS: {key} already exists with status {status}")
                    self.metrics['keys_found_duplicate'] += 1
                    return False, status
                
                # Create new idempotency record
                new_record = IdempotencyKey(
                    operation_key=key,
                    operation_type=operation_type.value,
                    user_id=user_id,
                    entity_id=entity_id,
                    request_hash=request_hash,
                    success=None,  # Will be updated when operation completes
                    created_at=datetime.utcnow(),
                    expires_at=datetime.utcnow() + timedelta(seconds=ttl_seconds or self.default_ttl),
                    result_data={'fallback_mode': 'database'}  # Use JSONB field instead of metadata
                )
                
                # Atomic insert with unique constraint protection
                session.add(new_record)
                session.commit()
                
                logger.info(f"🛡️ DB_CLAIM_SUCCESS: {key} ({operation_type.value}) claimed via database")
                self.metrics['keys_created'] += 1
                self.metrics['atomic_claims'] += 1
                
                return True, None
                
            except IntegrityError:
                # Unique constraint violation - operation already claimed
                session.rollback()
                logger.info(f"🔑 DB_CLAIM_DUPLICATE: {key} already claimed (integrity error)")
                self.metrics['keys_found_duplicate'] += 1
                self.metrics['race_conditions_prevented'] += 1
                
                # Get existing record status
                try:
                    existing = session.query(IdempotencyKey).filter_by(operation_key=key).first()
                    existing_status = 'completed' if existing and existing.success is True else ('failed' if existing and existing.success is False else 'processing')
                    return False, existing_status
                except Exception as e2:
                    logger.error(f"❌ DB_CLAIM_STATUS_ERROR: {key} - {e2}")
                    return False, 'error'
                    
        except Exception as e:
            session.rollback()
            logger.error(f"❌ DB_CLAIM_ERROR: {key} - {e}")
            return False, 'error'
            
        finally:
            # Always release advisory lock
            try:
                db_advisory_locks.release_lock(session, f"idempotency_{key}")
            except Exception as e:
                logger.error(f"❌ DB_CLAIM_LOCK_RELEASE_ERROR: {key} - {e}")
            
            # Close session
            try:
                session.close()
            except Exception as e:
                logger.error(f"❌ DB_CLAIM_SESSION_CLOSE_ERROR: {e}")
    
    async def atomic_claim_operation(
        self,
        key: str,
        operation_type: OperationType,
        request_data: Dict[str, Any],
        user_id: Optional[int] = None,
        entity_id: Optional[str] = None,
        ttl_seconds: Optional[int] = None
    ) -> tuple[bool, Optional[str]]:
        """
        Atomically claim operation for processing using SETNX + Lua script
        
        Args:
            key: Unique idempotency key
            operation_type: Type of operation
            request_data: Request data for hashing
            user_id: User ID associated with operation
            entity_id: Entity ID (escrow_id, cashout_id, etc.)
            ttl_seconds: Custom TTL (uses default if not provided)
            
        Returns:
            tuple: (claimed, existing_status) - claimed=True if operation claimed, existing_status if duplicate
        """
        try:
            # Create request hash
            request_hash = hashlib.sha256(
                json.dumps(request_data, sort_keys=True).encode()
            ).hexdigest()
            
            # Create idempotency record data
            record_data = {
                'key': key,
                'operation_type': operation_type.value,
                'user_id': user_id,
                'entity_id': entity_id,
                'request_hash': request_hash,
                'status': IdempotencyStatus.PROCESSING.value,  # Directly to PROCESSING
                'created_at': datetime.utcnow().isoformat(),
                'updated_at': datetime.utcnow().isoformat(),
                'completed_at': None,
                'ttl_seconds': ttl_seconds or self.default_ttl,
                'result': None,
                'error': None,
                'retry_count': 0,
                'metadata': {}
            }
            
            # Use atomic claim with Key-Value Store
            result = await self._atomic_claim_kv(
                key,
                record_data,
                ttl_seconds or self.default_ttl,
                request_hash
            )
            
            if result == 'CLAIMED':
                self.metrics['keys_created'] += 1
                self.metrics['atomic_claims'] += 1
                logger.info(f"⚛️ Atomically claimed operation: {key} ({operation_type.value})")
                return True, None
            elif result == 'DUPLICATE':
                self.metrics['keys_found_duplicate'] += 1
                self.metrics['race_conditions_prevented'] += 1
                logger.debug(f"🔑 Operation already claimed: {key}")
                # Get existing record status
                existing_record = await self.get_idempotency_record(key)
                existing_status = existing_record.status.value if existing_record else 'unknown'
                return False, existing_status
            elif result == 'HASH_MISMATCH':
                self.metrics['race_conditions_prevented'] += 1
                logger.warning(f"🚨 Hash mismatch for key: {key}")
                return False, 'hash_mismatch'
            else:
                logger.error(f"❌ Failed to claim operation: {key} (result: {result})")
                return False, 'error'
                
        except Exception as e:
            logger.error(f"❌ Error claiming operation {key}: {e}")
            return False, 'error'
    
    async def _atomic_claim_kv(
        self,
        key: str,
        record_data: Dict[str, Any],
        ttl_seconds: int,
        request_hash: str
    ) -> str:
        """Atomic claim operation using Key-Value Store native operations"""
        try:
            # Check if key already exists
            existing_data = await state_manager.get_state(key)
            
            if existing_data:
                existing_record = existing_data.get('value', {})
                existing_hash = existing_record.get('request_hash')
                
                if existing_hash == request_hash:
                    return 'DUPLICATE'
                else:
                    return 'HASH_MISMATCH'
            
            # Try to atomically set the key (SETNX equivalent)
            success = await state_manager.set_state(
                key,
                record_data,
                ttl_seconds=ttl_seconds,
                only_if_not_exists=True
            )
            
            if success:
                return 'CLAIMED'
            else:
                return 'RACE_CONDITION'
                
        except Exception as e:
            logger.error(f"❌ Error in atomic claim KV: {e}")
            return 'ERROR'
    
    async def _atomic_complete_kv(
        self,
        key: str,
        result_data: Dict[str, Any],
        metadata: Dict[str, Any],
        completed_at: str
    ) -> str:
        """Atomic complete operation using Key-Value Store native operations"""
        try:
            # Get current record
            existing_data = await state_manager.get_state(key)
            if not existing_data:
                return 'NOT_FOUND'
            
            record = existing_data.get('value', {})
            
            # Check if operation is in PROCESSING state
            if record.get('status') != 'processing':
                return f'INVALID_STATE:{record.get("status")}'
            
            # Update record with completion data
            record['status'] = 'completed'
            record['result'] = result_data
            record['completed_at'] = completed_at
            record['updated_at'] = completed_at
            
            # Update metadata
            if metadata:
                if 'metadata' not in record:
                    record['metadata'] = {}
                record['metadata'].update(metadata)
            
            # Save updated record (preserving TTL)
            success = await state_manager.set_state(
                key,
                record,
                preserve_ttl=True
            )
            
            return 'COMPLETED' if success else 'SAVE_FAILED'
            
        except Exception as e:
            logger.error(f"❌ Error in atomic complete KV: {e}")
            return 'ERROR'
    
    async def _atomic_fail_kv(
        self,
        key: str,
        error_message: str,
        metadata: Dict[str, Any],
        failed_at: str
    ) -> str:
        """Atomic fail operation using Key-Value Store native operations"""
        try:
            # Get current record
            existing_data = await state_manager.get_state(key)
            if not existing_data:
                return 'NOT_FOUND'
            
            record = existing_data.get('value', {})
            
            # Check if operation is in PROCESSING state
            if record.get('status') != 'processing':
                return f'INVALID_STATE:{record.get("status")}'
            
            # Update record with failure data
            record['status'] = 'failed'
            record['error'] = error_message
            record['updated_at'] = failed_at
            record['retry_count'] = record.get('retry_count', 0) + 1
            
            # Update metadata
            if metadata:
                if 'metadata' not in record:
                    record['metadata'] = {}
                record['metadata'].update(metadata)
            
            # Save updated record (preserving TTL)
            success = await state_manager.set_state(
                key,
                record,
                preserve_ttl=True
            )
            
            return 'FAILED' if success else 'SAVE_FAILED'
            
        except Exception as e:
            logger.error(f"❌ Error in atomic fail KV: {e}")
            return 'ERROR'
    
    def _initialize_lua_scripts(self) -> Dict[str, str]:
        """Initialize scripts - now handled natively by Key-Value Store operations"""
        # Lua scripts replaced with native Key-Value Store operations
        # Scripts are no longer used but kept for backward compatibility
        return {}
    
    async def get_idempotency_record(self, key: str) -> Optional[IdempotencyRecord]:
        """
        Get idempotency record by key with proper datetime deserialization
        
        Returns:
            IdempotencyRecord: Record if found, None otherwise
        """
        try:
            record_data = await state_manager.get_state(key)
            if not record_data:
                return None
            
            # Convert datetime strings back to datetime objects
            datetime_fields = ['created_at', 'updated_at', 'completed_at']
            for field in datetime_fields:
                if field in record_data and record_data[field] and isinstance(record_data[field], str):
                    try:
                        record_data[field] = datetime.fromisoformat(record_data[field])
                    except (ValueError, TypeError):
                        record_data[field] = None
            
            # Convert enums
            record_data['operation_type'] = OperationType(record_data['operation_type'])
            record_data['status'] = IdempotencyStatus(record_data['status'])
            
            # Convert back to dataclass
            record = IdempotencyRecord(**record_data)
            return record
            
        except Exception as e:
            logger.error(f"❌ Error getting idempotency record {key}: {e}")
            return None
    
    async def mark_processing(self, key: str) -> bool:
        """
        Mark idempotency key as currently processing
        
        Returns:
            bool: True if successfully marked as processing
        """
        try:
            record = await self.get_idempotency_record(key)
            if not record:
                logger.error(f"❌ Idempotency key not found: {key}")
                return False
            
            if record.status != IdempotencyStatus.PENDING:
                logger.warning(
                    f"⚠️ Cannot mark as processing - key {key} status: {record.status.value}"
                )
                return False
            
            # Update status to processing
            record.status = IdempotencyStatus.PROCESSING
            record.updated_at = datetime.utcnow()
            
            success = await state_manager.update_state(key, asdict(record))
            
            if success:
                logger.debug(f"🔄 Marked idempotency key as processing: {key}")
                return True
            else:
                return False
                
        except Exception as e:
            logger.error(f"❌ Error marking key as processing {key}: {e}")
            return False
    
    async def atomic_complete_operation(
        self,
        key: str,
        result: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Atomically mark operation as completed with result using Lua script
        
        Args:
            key: Idempotency key
            result: Operation result data
            metadata: Additional metadata
            
        Returns:
            bool: True if successfully marked as completed
        """
        try:
            completed_at = datetime.utcnow().isoformat()
            
            # Use native Key-Value Store operation
            script_result = await self._atomic_complete_kv(
                key,
                result,
                metadata or {},
                completed_at
            )
            
            if script_result == 'COMPLETED':
                self.metrics['operations_completed'] += 1
                self.metrics['atomic_completes'] += 1
                logger.info(f"⚛️ Atomically completed operation: {key}")
                return True
            elif script_result == 'NOT_FOUND':
                logger.error(f"❌ Idempotency key not found: {key}")
                return False
            elif script_result.startswith('INVALID_STATE:'):
                current_state = script_result.split(':', 1)[1]
                logger.warning(f"⚠️ Cannot complete operation {key} - current state: {current_state}")
                return False
            else:
                logger.error(f"❌ Failed to complete operation {key}: {script_result}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Error completing operation {key}: {e}")
            return False
    
    async def atomic_fail_operation(
        self,
        key: str,
        error: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Atomically mark operation as failed with error using Lua script
        
        Args:
            key: Idempotency key
            error: Error message
            metadata: Additional metadata
            
        Returns:
            bool: True if successfully marked as failed
        """
        try:
            failed_at = datetime.utcnow().isoformat()
            
            # Use native Key-Value Store operation
            script_result = await self._atomic_fail_kv(
                key,
                error,
                metadata or {},
                failed_at
            )
            
            if script_result == 'FAILED':
                self.metrics['operations_failed'] += 1
                logger.error(f"⚛️ Atomically failed operation: {key} - {error}")
                return True
            elif script_result == 'NOT_FOUND':
                logger.error(f"❌ Idempotency key not found: {key}")
                return False
            elif script_result.startswith('INVALID_STATE:'):
                current_state = script_result.split(':', 1)[1]
                logger.warning(f"⚠️ Cannot fail operation {key} - current state: {current_state}")
                return False
            else:
                logger.error(f"❌ Failed to fail operation {key}: {script_result}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Error failing operation {key}: {e}")
            return False
    
    async def is_duplicate_operation(
        self,
        key: str,
        request_data: Dict[str, Any]
    ) -> tuple[bool, Optional[IdempotencyRecord]]:
        """
        Check if operation is a duplicate
        
        Args:
            key: Idempotency key to check
            request_data: Current request data for comparison
            
        Returns:
            tuple: (is_duplicate, existing_record)
        """
        try:
            record = await self.get_idempotency_record(key)
            if not record:
                return False, None
            
            # Verify request hash matches
            current_hash = hashlib.sha256(
                json.dumps(request_data, sort_keys=True).encode()
            ).hexdigest()
            
            if record.request_hash != current_hash:
                logger.warning(
                    f"⚠️ Hash mismatch for idempotency key {key} - "
                    f"different request data detected"
                )
                return False, record
            
            # Check if operation is still valid (not expired)
            if record.status == IdempotencyStatus.EXPIRED:
                return False, record
            
            # Check for processing timeout
            if record.status == IdempotencyStatus.PROCESSING:
                processing_time = datetime.utcnow() - record.updated_at
                if processing_time.total_seconds() > self.processing_timeout:
                    logger.warning(
                        f"⏰ Processing timeout for key {key} - "
                        f"been processing for {processing_time}"
                    )
                    # Reset to pending for retry
                    record.status = IdempotencyStatus.PENDING
                    await state_manager.update_state(key, asdict(record))
                    return False, record
            
            return True, record
            
        except Exception as e:
            logger.error(f"❌ Error checking duplicate operation {key}: {e}")
            return False, None
    
    async def get_operation_result(self, key: str) -> Optional[Dict[str, Any]]:
        """
        Get result of completed operation
        
        Returns:
            dict: Operation result or None if not completed
        """
        try:
            record = await self.get_idempotency_record(key)
            if not record:
                return None
            
            if record.status == IdempotencyStatus.COMPLETED:
                return record.result
            else:
                logger.debug(f"🔍 Operation not completed: {key} status={record.status.value}")
                return None
                
        except Exception as e:
            logger.error(f"❌ Error getting operation result {key}: {e}")
            return None
    
    async def cleanup_expired_keys(self) -> int:
        """
        Cleanup expired idempotency keys
        
        Returns:
            int: Number of keys cleaned up
        """
        try:
            # Get all idempotency keys
            keys = await state_manager.get_keys_by_tag('idempotency')
            cleaned_count = 0
            
            for key in keys:
                record = await self.get_idempotency_record(key)
                if not record:
                    continue
                
                # Check if expired
                age = datetime.utcnow() - record.created_at
                if age.total_seconds() > record.ttl_seconds:
                    await state_manager.delete_state(key)
                    cleaned_count += 1
                    self.metrics['keys_expired'] += 1
            
            if cleaned_count > 0:
                logger.info(f"🧹 Cleaned up {cleaned_count} expired idempotency keys")
            
            return cleaned_count
            
        except Exception as e:
            logger.error(f"❌ Error cleaning up expired keys: {e}")
            return 0
    
    async def get_user_operations(
        self,
        user_id: int,
        operation_type: Optional[OperationType] = None,
        status: Optional[IdempotencyStatus] = None
    ) -> List[IdempotencyRecord]:
        """
        Get all operations for a user
        
        Returns:
            List[IdempotencyRecord]: List of user operations
        """
        try:
            all_keys = await state_manager.get_keys_by_tag('idempotency')
            user_operations = []
            
            for key in all_keys:
                record = await self.get_idempotency_record(key)
                if not record or record.user_id != user_id:
                    continue
                
                # Filter by operation type
                if operation_type and record.operation_type != operation_type:
                    continue
                
                # Filter by status
                if status and record.status != status:
                    continue
                
                user_operations.append(record)
            
            # Sort by creation time (newest first)
            user_operations.sort(key=lambda x: x.created_at, reverse=True)
            
            logger.debug(
                f"📋 Retrieved {len(user_operations)} operations for user {user_id}"
            )
            return user_operations
            
        except Exception as e:
            logger.error(f"❌ Error getting user operations for {user_id}: {e}")
            return []
    
    async def get_metrics(self) -> Dict[str, Any]:
        """Get idempotency service metrics"""
        total_keys = await state_manager.get_keys_by_tag('idempotency')
        
        return {
            **self.metrics,
            'total_active_keys': len(total_keys),
            'default_ttl_hours': self.default_ttl / 3600,
            'processing_timeout_minutes': self.processing_timeout / 60
        }


# Decorators for automatic idempotency handling

def with_idempotency(
    operation_type: OperationType,
    key_generator: Optional[Callable] = None,
    ttl_seconds: Optional[int] = None
):
    """
    Decorator to automatically handle idempotency for operations
    
    Args:
        operation_type: Type of operation
        key_generator: Custom key generation function
        ttl_seconds: Custom TTL for idempotency key
    """
    def decorator(func):
        async def wrapper(*args, **kwargs):
            # Extract user_id and request data from function arguments
            user_id = kwargs.get('user_id') or (args[1] if len(args) > 1 else None)
            request_data = kwargs.copy()
            
            # Generate idempotency key
            if key_generator:
                key = key_generator(*args, **kwargs)
            else:
                # Default key generation
                key = IdempotencyKeyGenerator.generate_key(
                    operation_type,
                    user_id,
                    request_data
                )
            
            service = IdempotencyService()
            
            # Try to atomically claim the operation
            claimed, existing_status = await service.atomic_claim_operation(
                key, operation_type, request_data, user_id, ttl_seconds=ttl_seconds
            )
            
            if not claimed:
                # Operation was not claimed, check existing status
                if existing_status == 'completed':
                    # Return cached result
                    existing_record = await service.get_idempotency_record(key)
                    if existing_record and existing_record.result:
                        logger.info(f"🔑 Returning cached result for idempotent operation: {key}")
                        return existing_record.result
                elif existing_status == 'processing':
                    logger.info(f"⏳ Operation already processing: {key}")
                    # Wait briefly and return processing status
                    await asyncio.sleep(0.1)
                    return {'status': 'processing', 'idempotency_key': key}
                elif existing_status == 'failed':
                    logger.info(f"🔄 Retrying failed operation: {key}")
                    # Try to claim for retry
                    claimed, _ = await service.atomic_claim_operation(
                        key, operation_type, request_data, user_id, ttl_seconds=ttl_seconds
                    )
                    if not claimed:
                        logger.error(f"❌ Failed to claim operation for retry: {key}")
                        return {'status': 'error', 'message': 'Unable to retry operation'}
                else:
                    logger.error(f"❌ Failed to claim operation: {key} (status: {existing_status})")
                    return {'status': 'error', 'message': 'Unable to process operation'}
            
            try:
                # Execute the actual operation
                result = await func(*args, **kwargs)
                
                # Atomically mark as completed
                success = await service.atomic_complete_operation(key, {'result': result})
                if not success:
                    logger.error(f"❌ Failed to mark operation as completed: {key}")
                
                return result
                
            except Exception as e:
                # Atomically mark as failed
                await service.atomic_fail_operation(key, str(e))
                raise
                
        return wrapper
    return decorator


# Global idempotency service instance
idempotency_service = IdempotencyService()


# Utility functions

async def ensure_idempotent_operation(
    operation_type: OperationType,
    user_id: int,
    request_data: Dict[str, Any],
    operation_func: Callable,
    entity_id: Optional[str] = None,
    custom_key: Optional[str] = None
) -> Any:
    """
    Ensure operation is executed exactly once using idempotency keys
    
    Args:
        operation_type: Type of operation
        user_id: User ID
        request_data: Request data
        operation_func: Function to execute
        entity_id: Optional entity ID
        custom_key: Custom idempotency key (generated if not provided)
        
    Returns:
        Operation result
    """
    # Generate key if not provided
    if custom_key:
        key = custom_key
    else:
        key = IdempotencyKeyGenerator.generate_key(
            operation_type, user_id, request_data, entity_id
        )
    
    # Check for duplicate
    is_duplicate, existing_record = await idempotency_service.is_duplicate_operation(
        key, request_data
    )
    
    if is_duplicate and existing_record.status == IdempotencyStatus.COMPLETED:
        logger.info(f"🔑 Returning cached result for: {key}")
        return existing_record.result
    
    # Create key if not duplicate
    if not is_duplicate:
        await idempotency_service.create_idempotency_key(
            key, operation_type, request_data, user_id, entity_id
        )
    
    # Mark as processing
    await idempotency_service.mark_processing(key)
    
    try:
        # Execute operation
        result = await operation_func()
        
        # Mark as completed
        await idempotency_service.complete_operation(key, {'result': result})
        
        return result
        
    except Exception as e:
        # Mark as failed
        await idempotency_service.fail_operation(key, str(e))
        raise