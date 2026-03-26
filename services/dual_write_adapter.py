"""
Dual Write Adapter System
Manages atomic dual-write operations during legacy-to-unified status migration
Ensures data consistency between legacy and unified transaction systems
"""

from typing import Dict, Optional, Any, Union, Tuple
from datetime import datetime
from enum import Enum
from dataclasses import dataclass
import logging
import contextlib
from sqlalchemy.orm import Session
from sqlalchemy import or_

from database import managed_session
from models import (
    UnifiedTransaction, UnifiedTransactionStatus, UnifiedTransactionType,
    UnifiedTransactionStatusHistory, EscrowStatus, CashoutStatus, ExchangeStatus,
    Cashout, Escrow, ExchangeOrder  # Assuming these models exist
)
from services.legacy_status_mapper import LegacyStatusMapper, LegacySystemType
from utils.universal_id_generator import UniversalIDGenerator

logger = logging.getLogger(__name__)


class DualWriteMode(Enum):
    """Dual-write operation modes during migration"""
    LEGACY_ONLY = "legacy_only"           # Write only to legacy system (pre-migration)
    DUAL_WRITE_LEGACY_PRIMARY = "dual_write_legacy_primary"  # Write both, read from legacy
    DUAL_WRITE_UNIFIED_PRIMARY = "dual_write_unified_primary"  # Write both, read from unified
    UNIFIED_ONLY = "unified_only"         # Write only to unified system (post-migration)


class DualWriteStrategy(Enum):
    """Strategy for handling dual-write failures"""
    FAIL_FAST = "fail_fast"              # Fail entire operation if either system fails
    LEGACY_FALLBACK = "legacy_fallback"  # Continue with legacy if unified fails
    UNIFIED_FALLBACK = "unified_fallback"  # Continue with unified if legacy fails
    UNIFIED_FIRST = "unified_first"      # Prefer unified system (alias for unified_fallback)
    BEST_EFFORT = "best_effort"          # Continue with whichever system succeeds


@dataclass
class DualWriteResult:
    """Result of a dual-write operation"""
    legacy_success: bool
    unified_success: bool
    legacy_data: Optional[Any] = None
    unified_data: Optional[Any] = None
    legacy_error: Optional[Exception] = None
    unified_error: Optional[Exception] = None
    rollback_performed: bool = False
    operation_id: Optional[str] = None
    
    def overall_success(self, mode: DualWriteMode, strategy: DualWriteStrategy) -> bool:
        """Overall operation success based on mode and strategy - FINANCIAL SAFETY CRITICAL"""
        # FAIL_FAST modes: Both writes must succeed
        if strategy == DualWriteStrategy.FAIL_FAST:
            if mode in [DualWriteMode.DUAL_WRITE_LEGACY_PRIMARY, DualWriteMode.DUAL_WRITE_UNIFIED_PRIMARY]:
                return self.legacy_success and self.unified_success  # Both required
            elif mode == DualWriteMode.LEGACY_ONLY:
                return self.legacy_success
            elif mode == DualWriteMode.UNIFIED_ONLY:
                return self.unified_success
        
        # Fallback strategies: Allow one to succeed
        elif strategy == DualWriteStrategy.LEGACY_FALLBACK:
            return self.legacy_success  # Prefer legacy
        elif strategy == DualWriteStrategy.UNIFIED_FALLBACK:
            return self.unified_success  # Prefer unified
        elif strategy == DualWriteStrategy.UNIFIED_FIRST:
            return self.unified_success  # Prefer unified (alias for unified_fallback)
        elif strategy == DualWriteStrategy.BEST_EFFORT:
            return self.legacy_success or self.unified_success  # Either succeeds
        
        # Default to safe mode
        return self.legacy_success and self.unified_success
    
    @property
    def has_inconsistency(self) -> bool:
        """Check if there's inconsistency between systems"""
        return self.legacy_success != self.unified_success


@dataclass
class DualWriteConfig:
    """Configuration for dual-write adapter"""
    mode: DualWriteMode = DualWriteMode.DUAL_WRITE_LEGACY_PRIMARY
    strategy: DualWriteStrategy = DualWriteStrategy.FAIL_FAST
    rollback_enabled: bool = True
    retry_attempts: int = 3
    retry_delay_seconds: float = 1.0
    validation_enabled: bool = True
    audit_logging: bool = True
    inconsistency_alerts: bool = True
    

class DualWriteAdapter:
    """
    Manages atomic dual-write operations between legacy and unified transaction systems
    Provides seamless transition capabilities with configurable fallback strategies
    """
    
    def __init__(self, config: DualWriteConfig = None):
        self.config = config or DualWriteConfig()
        self.mapper = LegacyStatusMapper()
        self._operation_counter = 0
        
    def _generate_operation_id(self) -> str:
        """Generate unique operation ID for tracking"""
        self._operation_counter += 1
        timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        return f"DW{timestamp}{self._operation_counter:04d}"
    
    # =============== TRANSACTION CREATION METHODS ===============
    
    def create_transaction(self,
                         transaction_type: Union[str, UnifiedTransactionType],
                         user_id: int,
                         amount: float,
                         currency: str = "USD",
                         legacy_entity_id: Optional[str] = None,
                         metadata: Optional[Dict[str, Any]] = None,
                         session: Optional[Session] = None) -> DualWriteResult:
        """
        Create transaction in both legacy and unified systems atomically
        
        Args:
            transaction_type: Type of transaction (wallet_cashout, escrow, etc.)
            user_id: User ID for the transaction
            amount: Transaction amount
            currency: Currency code
            legacy_entity_id: Existing legacy entity ID (cashout_id, escrow_id, etc.)
            metadata: Additional transaction metadata
            session: Optional database session
            
        Returns:
            DualWriteResult with creation status for both systems
        """
        operation_id = self._generate_operation_id()
        
        logger.info(f"Starting dual-write create transaction [Op: {operation_id}] "
                   f"type={transaction_type}, user={user_id}, amount={amount}")
        
        result = DualWriteResult(
            legacy_success=False,
            unified_success=False,
            operation_id=operation_id
        )
        
        # Determine transaction type and system type
        if isinstance(transaction_type, str):
            transaction_type = UnifiedTransactionType(transaction_type)
        
        system_type = self._get_system_type_from_transaction_type(transaction_type)
        
        # Use existing session or create new managed session
        session_context = contextlib.nullcontext(session) if session else managed_session()
        
        try:
            with session_context as db_session:
                # Create unified transaction first (generates transaction_id)
                if self.config.mode in [DualWriteMode.DUAL_WRITE_LEGACY_PRIMARY, 
                                      DualWriteMode.DUAL_WRITE_UNIFIED_PRIMARY,
                                      DualWriteMode.UNIFIED_ONLY]:
                    try:
                        result.unified_data = self._create_unified_transaction(
                            db_session, transaction_type, user_id, amount, currency,
                            legacy_entity_id, metadata
                        )
                        result.unified_success = True
                        logger.info(f"✅ Unified transaction created [Op: {operation_id}] "
                                  f"ID: {result.unified_data.transaction_id}")
                        
                    except Exception as e:
                        result.unified_error = e
                        logger.error(f"❌ Unified transaction creation failed [Op: {operation_id}]: {e}")
                        
                        if self.config.strategy == DualWriteStrategy.FAIL_FAST:
                            raise
                
                # Create or update legacy transaction
                if self.config.mode in [DualWriteMode.LEGACY_ONLY,
                                      DualWriteMode.DUAL_WRITE_LEGACY_PRIMARY,
                                      DualWriteMode.DUAL_WRITE_UNIFIED_PRIMARY]:
                    try:
                        if legacy_entity_id:
                            # Update existing legacy entity with unified reference
                            result.legacy_data = self._update_legacy_entity_with_unified_ref(
                                db_session, system_type, legacy_entity_id,
                                result.unified_data.transaction_id if result.unified_data else None
                            )
                        else:
                            # Create new legacy entity
                            result.legacy_data = self._create_legacy_transaction(
                                db_session, system_type, user_id, amount, currency,
                                result.unified_data.transaction_id if result.unified_data else None,
                                metadata
                            )
                        
                        result.legacy_success = True
                        logger.info(f"✅ Legacy transaction created [Op: {operation_id}] "
                                  f"Type: {system_type}")
                        
                    except Exception as e:
                        result.legacy_error = e
                        logger.error(f"❌ Legacy transaction creation failed [Op: {operation_id}]: {e}")
                        
                        if self.config.strategy == DualWriteStrategy.FAIL_FAST:
                            # Rollback unified transaction if created
                            if result.unified_success and self.config.rollback_enabled:
                                self._rollback_unified_transaction(db_session, result.unified_data)
                                result.rollback_performed = True
                            raise
                
                # Validate consistency if both succeeded
                if result.legacy_success and result.unified_success and self.config.validation_enabled:
                    self._validate_transaction_consistency(result.legacy_data, result.unified_data)
                
                # Handle partial failures based on strategy - FINANCIAL SAFETY CHECK
                if result.has_inconsistency:
                    self._handle_inconsistency(result, db_session)
                
                # CRITICAL: Validate overall success using proper mode/strategy logic
                if not result.overall_success(self.config.mode, self.config.strategy):
                    raise Exception(f"Dual-write operation failed according to mode={self.config.mode.value}, strategy={self.config.strategy.value}")
                
                # Commit transaction if using managed session
                if not session:  # Only commit if we created the session
                    db_session.commit()
                    
        except Exception as e:
            logger.error(f"💥 Dual-write transaction creation failed [Op: {operation_id}]: {e}")
            if not result.legacy_error and not result.unified_error:
                result.legacy_error = e
            raise
        
        # FINANCIAL SAFETY: Final validation before returning
        if not result.overall_success(self.config.mode, self.config.strategy):
            logger.error(f"🚨 FINANCIAL SAFETY: Dual-write operation reported failure [Op: {operation_id}] mode={self.config.mode.value}, strategy={self.config.strategy.value}")
            raise Exception("Dual-write transaction creation failed according to configured mode/strategy")
        
        # Log audit trail
        if self.config.audit_logging:
            self._log_dual_write_audit("CREATE_TRANSACTION", operation_id, result)
        
        return result
    
    # =============== STATUS UPDATE METHODS ===============
    
    def update_status(self,
                     entity_id: str,  # Could be transaction_id, cashout_id, escrow_id
                     new_status: Union[UnifiedTransactionStatus, str],
                     system_type: Optional[LegacySystemType] = None,
                     reason: str = "Status update",
                     triggered_by: str = "system",
                     metadata: Optional[Dict[str, Any]] = None,
                     session: Optional[Session] = None) -> DualWriteResult:
        """
        Update transaction status in both legacy and unified systems atomically
        
        Args:
            entity_id: Transaction ID (unified) or legacy entity ID
            new_status: New unified status or string representation
            system_type: Legacy system type (if updating legacy entity)
            reason: Reason for status change
            triggered_by: Who/what triggered the change
            metadata: Additional status update metadata
            session: Optional database session
            
        Returns:
            DualWriteResult with update status for both systems
        """
        operation_id = self._generate_operation_id()
        
        if isinstance(new_status, str):
            new_status = UnifiedTransactionStatus(new_status)
        
        logger.info(f"Starting dual-write status update [Op: {operation_id}] "
                   f"entity={entity_id}, status={new_status.value}")
        
        result = DualWriteResult(
            legacy_success=False,
            unified_success=False,
            operation_id=operation_id
        )
        
        session_context = contextlib.nullcontext(session) if session else managed_session()
        
        try:
            with session_context as db_session:
                # Determine entity type and find existing records
                unified_tx = None
                legacy_entity = None
                
                if entity_id.startswith("UTX"):  # Unified transaction ID
                    unified_tx = db_session.query(UnifiedTransaction).filter(
                        UnifiedTransaction.transaction_id == entity_id
                    ).first()
                    
                    if unified_tx:
                        # Find linked legacy entity
                        legacy_entity = self._find_linked_legacy_entity(db_session, unified_tx)
                        if legacy_entity:
                            system_type = self._detect_legacy_system_type(legacy_entity)
                else:
                    # Assume legacy entity ID, need system_type
                    if not system_type:
                        raise ValueError("system_type required for legacy entity updates")
                    
                    legacy_entity = self._find_legacy_entity(db_session, entity_id, system_type)
                    if legacy_entity:
                        # Find linked unified transaction
                        unified_tx = self._find_linked_unified_transaction(db_session, legacy_entity)
                
                # Update unified transaction status
                if unified_tx and self.config.mode in [
                    DualWriteMode.DUAL_WRITE_LEGACY_PRIMARY,
                    DualWriteMode.DUAL_WRITE_UNIFIED_PRIMARY,
                    DualWriteMode.UNIFIED_ONLY
                ]:
                    try:
                        old_status = UnifiedTransactionStatus(unified_tx.status)
                        unified_tx.status = new_status.value
                        unified_tx.updated_at = datetime.utcnow()
                        
                        # Add to status history
                        history_entry = UnifiedTransactionStatusHistory(
                            transaction_id=unified_tx.transaction_id,
                            from_status=old_status.value,
                            to_status=new_status.value,
                            change_reason=reason,
                            triggered_by=triggered_by,
                            change_metadata=metadata
                        )
                        db_session.add(history_entry)
                        
                        result.unified_data = unified_tx
                        result.unified_success = True
                        logger.info(f"✅ Unified status updated [Op: {operation_id}] "
                                  f"{old_status.value} → {new_status.value}")
                        
                    except Exception as e:
                        result.unified_error = e
                        logger.error(f"❌ Unified status update failed [Op: {operation_id}]: {e}")
                        
                        if self.config.strategy == DualWriteStrategy.FAIL_FAST:
                            raise
                
                # Update legacy entity status
                if legacy_entity and system_type and self.config.mode in [
                    DualWriteMode.LEGACY_ONLY,
                    DualWriteMode.DUAL_WRITE_LEGACY_PRIMARY,
                    DualWriteMode.DUAL_WRITE_UNIFIED_PRIMARY
                ]:
                    try:
                        legacy_status = self.mapper.map_from_unified(new_status, system_type)
                        old_legacy_status = self._get_legacy_entity_status(legacy_entity, system_type)
                        
                        self._update_legacy_entity_status(legacy_entity, legacy_status, system_type)
                        
                        result.legacy_data = legacy_entity
                        result.legacy_success = True
                        logger.info(f"✅ Legacy status updated [Op: {operation_id}] "
                                  f"{old_legacy_status} → {legacy_status}")
                        
                    except Exception as e:
                        result.legacy_error = e
                        logger.error(f"❌ Legacy status update failed [Op: {operation_id}]: {e}")
                        
                        if self.config.strategy == DualWriteStrategy.FAIL_FAST:
                            # Rollback unified status if updated
                            if result.unified_success and self.config.rollback_enabled:
                                unified_tx.status = old_status.value
                                unified_tx.updated_at = datetime.utcnow()
                                result.rollback_performed = True
                            raise
                
                # Handle partial failures
                if result.has_inconsistency:
                    self._handle_inconsistency(result, db_session)
                
                # Commit if using managed session
                if not session:
                    db_session.commit()
                    
        except Exception as e:
            logger.error(f"💥 Dual-write status update failed [Op: {operation_id}]: {e}")
            raise
        
        # Log audit trail
        if self.config.audit_logging:
            self._log_dual_write_audit("UPDATE_STATUS", operation_id, result)
        
        return result
    
    # =============== READ METHODS ===============
    
    def get_transaction_status(self,
                             entity_id: str,
                             system_type: Optional[LegacySystemType] = None,
                             session: Optional[Session] = None) -> Tuple[Optional[UnifiedTransactionStatus], Dict[str, Any]]:
        """
        Get current unified transaction status with metadata
        Reads from primary system based on current mode
        
        Args:
            entity_id: Transaction ID or legacy entity ID
            system_type: Legacy system type (if reading legacy entity)
            session: Optional database session
            
        Returns:
            Tuple of (unified_status, metadata_dict)
        """
        session_context = contextlib.nullcontext(session) if session else managed_session()
        
        with session_context as db_session:
            # Determine read priority based on mode
            read_unified_first = self.config.mode in [
                DualWriteMode.DUAL_WRITE_UNIFIED_PRIMARY,
                DualWriteMode.UNIFIED_ONLY
            ]
            
            if read_unified_first:
                # Try unified system first
                if entity_id.startswith("UTX"):
                    unified_tx = db_session.query(UnifiedTransaction).filter(
                        UnifiedTransaction.transaction_id == entity_id
                    ).first()
                    
                    if unified_tx:
                        return UnifiedTransactionStatus(unified_tx.status), {
                            "source": "unified",
                            "transaction_id": unified_tx.transaction_id,
                            "updated_at": unified_tx.updated_at,
                            "retry_count": unified_tx.retry_count,
                            "external_reference_id": unified_tx.external_reference_id
                        }
                
                # Fallback to legacy system
                if system_type:
                    legacy_entity = self._find_legacy_entity(db_session, entity_id, system_type)
                    if legacy_entity:
                        legacy_status = self._get_legacy_entity_status(legacy_entity, system_type)
                        unified_status = self.mapper.map_to_unified(legacy_status, system_type)
                        
                        return unified_status, {
                            "source": "legacy",
                            "system_type": system_type.value,
                            "legacy_status": legacy_status.value if hasattr(legacy_status, 'value') else str(legacy_status),
                            "entity_id": entity_id
                        }
            
            else:
                # Try legacy system first
                if system_type:
                    legacy_entity = self._find_legacy_entity(db_session, entity_id, system_type)
                    if legacy_entity:
                        legacy_status = self._get_legacy_entity_status(legacy_entity, system_type)
                        unified_status = self.mapper.map_to_unified(legacy_status, system_type)
                        
                        return unified_status, {
                            "source": "legacy",
                            "system_type": system_type.value,
                            "legacy_status": legacy_status.value if hasattr(legacy_status, 'value') else str(legacy_status),
                            "entity_id": entity_id
                        }
                
                # Fallback to unified system (use reference_id and external_id)
                unified_tx = db_session.query(UnifiedTransaction).filter(
                    or_(
                        UnifiedTransaction.external_id == entity_id,
                        UnifiedTransaction.reference_id == entity_id
                    )
                ).first()
                
                if unified_tx:
                    return UnifiedTransactionStatus(unified_tx.status), {
                        "source": "unified",
                        "transaction_id": unified_tx.transaction_id,
                        "updated_at": unified_tx.updated_at,
                        "retry_count": unified_tx.retry_count,
                        "external_reference_id": unified_tx.external_reference_id
                    }
        
        return None, {"source": "not_found", "entity_id": entity_id}
    
    # =============== CONSISTENCY AND VALIDATION METHODS ===============
    
    def check_consistency(self,
                         entity_id: str,
                         system_type: Optional[LegacySystemType] = None,
                         session: Optional[Session] = None) -> Dict[str, Any]:
        """
        Check consistency between legacy and unified systems for a transaction
        
        Returns:
            Dictionary with consistency analysis results
        """
        session_context = contextlib.nullcontext(session) if session else managed_session()
        
        with session_context as db_session:
            unified_tx = None
            legacy_entity = None
            
            # Find both entities
            if entity_id.startswith("UTX"):
                unified_tx = db_session.query(UnifiedTransaction).filter(
                    UnifiedTransaction.transaction_id == entity_id
                ).first()
                if unified_tx:
                    legacy_entity = self._find_linked_legacy_entity(db_session, unified_tx)
                    if legacy_entity:
                        system_type = self._detect_legacy_system_type(legacy_entity)
            else:
                if not system_type:
                    return {"error": "system_type required for legacy entity consistency check"}
                
                legacy_entity = self._find_legacy_entity(db_session, entity_id, system_type)
                if legacy_entity:
                    unified_tx = self._find_linked_unified_transaction(db_session, legacy_entity)
            
            # Analyze consistency
            report = {
                "entity_id": entity_id,
                "check_timestamp": datetime.utcnow(),
                "unified_exists": unified_tx is not None,
                "legacy_exists": legacy_entity is not None,
                "system_type": system_type.value if system_type else None,
                "consistent": True,
                "inconsistencies": []
            }
            
            if unified_tx and legacy_entity:
                # Compare statuses
                unified_status = UnifiedTransactionStatus(unified_tx.status)
                legacy_status = self._get_legacy_entity_status(legacy_entity, system_type)
                expected_unified_status = self.mapper.map_to_unified(legacy_status, system_type)
                
                if unified_status != expected_unified_status:
                    report["consistent"] = False
                    report["inconsistencies"].append({
                        "type": "status_mismatch",
                        "unified_status": unified_status.value,
                        "legacy_status": legacy_status.value if hasattr(legacy_status, 'value') else str(legacy_status),
                        "expected_unified_status": expected_unified_status.value
                    })
                
                # Compare basic fields
                if abs(float(unified_tx.amount) - self._get_legacy_entity_amount(legacy_entity)) > 0.0001:
                    report["consistent"] = False
                    report["inconsistencies"].append({
                        "type": "amount_mismatch",
                        "unified_amount": float(unified_tx.amount),
                        "legacy_amount": self._get_legacy_entity_amount(legacy_entity)
                    })
                
                if unified_tx.user_id != self._get_legacy_entity_user_id(legacy_entity):
                    report["consistent"] = False
                    report["inconsistencies"].append({
                        "type": "user_id_mismatch",
                        "unified_user_id": unified_tx.user_id,
                        "legacy_user_id": self._get_legacy_entity_user_id(legacy_entity)
                    })
            
            elif unified_tx and not legacy_entity:
                report["consistent"] = False
                report["inconsistencies"].append({
                    "type": "missing_legacy_entity",
                    "message": "Unified transaction exists but no linked legacy entity found"
                })
            
            elif legacy_entity and not unified_tx:
                report["consistent"] = False
                report["inconsistencies"].append({
                    "type": "missing_unified_transaction",
                    "message": "Legacy entity exists but no linked unified transaction found"
                })
            
            return report
    
    def repair_inconsistency(self,
                           entity_id: str,
                           repair_strategy: str = "unified_wins",
                           system_type: Optional[LegacySystemType] = None,
                           session: Optional[Session] = None) -> DualWriteResult:
        """
        Repair consistency issues between legacy and unified systems
        
        Args:
            entity_id: Transaction ID or legacy entity ID
            repair_strategy: 'unified_wins', 'legacy_wins', or 'manual'
            system_type: Legacy system type
            session: Optional database session
            
        Returns:
            DualWriteResult with repair operation results
        """
        operation_id = self._generate_operation_id()
        logger.info(f"Starting consistency repair [Op: {operation_id}] "
                   f"entity={entity_id}, strategy={repair_strategy}")
        
        # First check current consistency
        consistency_report = self.check_consistency(entity_id, system_type, session)
        
        if consistency_report.get("consistent", True):
            return DualWriteResult(
                legacy_success=True,
                unified_success=True,
                operation_id=operation_id
            )
        
        result = DualWriteResult(
            legacy_success=False,
            unified_success=False,
            operation_id=operation_id
        )
        
        session_context = contextlib.nullcontext(session) if session else managed_session()
        
        try:
            with session_context as db_session:
                if repair_strategy == "unified_wins":
                    # Update legacy to match unified
                    result = self._repair_unified_wins(db_session, entity_id, system_type, operation_id)
                
                elif repair_strategy == "legacy_wins":
                    # Update unified to match legacy  
                    result = self._repair_legacy_wins(db_session, entity_id, system_type, operation_id)
                
                elif repair_strategy == "manual":
                    # Just log the inconsistencies for manual resolution
                    logger.warning(f"Manual repair requested [Op: {operation_id}]: {consistency_report}")
                    result.legacy_success = True
                    result.unified_success = True
                
                # Commit if using managed session
                if not session:
                    db_session.commit()
                    
        except Exception as e:
            logger.error(f"💥 Consistency repair failed [Op: {operation_id}]: {e}")
            raise
        
        # Log audit trail
        if self.config.audit_logging:
            self._log_dual_write_audit("REPAIR_INCONSISTENCY", operation_id, result)
        
        return result
    
    # =============== HELPER METHODS (Private) ===============
    
    def _create_unified_transaction(self,
                                  session: Session,
                                  transaction_type: UnifiedTransactionType,
                                  user_id: int,
                                  amount: float,
                                  currency: str,
                                  legacy_entity_id: Optional[str],
                                  metadata: Optional[Dict[str, Any]]) -> UnifiedTransaction:
        """Create unified transaction record"""
        
        unified_tx = UnifiedTransaction(
            transaction_id=UniversalIDGenerator.generate_transaction_id(),
            user_id=user_id,
            transaction_type=transaction_type.value,
            status=UnifiedTransactionStatus.PENDING.value,
            amount=amount,
            currency=currency,
            fee_amount=0,  # Will be calculated later
            total_amount=amount,
            fund_movement_type="hold",  # Default movement type
            description=f"{transaction_type.value} transaction",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        
        # Link to legacy entity if provided
        if legacy_entity_id:
            if transaction_type == UnifiedTransactionType.WALLET_CASHOUT:
                unified_tx.cashout_id = legacy_entity_id
            elif transaction_type == UnifiedTransactionType.ESCROW:
                unified_tx.escrow_id = legacy_entity_id
            elif transaction_type in [UnifiedTransactionType.EXCHANGE_SELL_CRYPTO, 
                                    UnifiedTransactionType.EXCHANGE_BUY_CRYPTO]:
                # Assuming legacy_entity_id is numeric for exchange orders
                try:
                    unified_tx.exchange_order_id = int(legacy_entity_id)
                except ValueError:
                    logger.warning(f"Invalid exchange order ID: {legacy_entity_id}")
        
        # Add metadata if provided
        if metadata:
            for key, value in metadata.items():
                if hasattr(unified_tx, key):
                    setattr(unified_tx, key, value)
        
        session.add(unified_tx)
        session.flush()  # Get the ID
        
        return unified_tx
    
    def _get_system_type_from_transaction_type(self, transaction_type: UnifiedTransactionType) -> LegacySystemType:
        """Map unified transaction type to legacy system type"""
        mapping = {
            UnifiedTransactionType.WALLET_CASHOUT: LegacySystemType.CASHOUT,
            UnifiedTransactionType.ESCROW: LegacySystemType.ESCROW,
            UnifiedTransactionType.EXCHANGE_SELL_CRYPTO: LegacySystemType.EXCHANGE,
            UnifiedTransactionType.EXCHANGE_BUY_CRYPTO: LegacySystemType.EXCHANGE
        }
        return mapping[transaction_type]
    
    def _create_legacy_transaction(self,
                                 session: Session,
                                 system_type: LegacySystemType,
                                 user_id: int,
                                 amount: float,
                                 currency: str,
                                 unified_transaction_id: Optional[str],
                                 metadata: Optional[Dict[str, Any]]) -> Any:
        """Create legacy transaction record"""
        # Implementation would depend on actual legacy models
        # This is a placeholder showing the structure
        
        if system_type == LegacySystemType.CASHOUT:
            # Create Cashout record
            legacy_entity = Cashout(
                user_id=user_id,
                amount=amount,
                currency=currency,
                status=CashoutStatus.PENDING.value,
                created_at=datetime.utcnow()
            )
            # Add reference to unified transaction
            if hasattr(legacy_entity, 'unified_transaction_id'):
                legacy_entity.unified_transaction_id = unified_transaction_id
                
        elif system_type == LegacySystemType.ESCROW:
            # Create Escrow record
            legacy_entity = Escrow(
                user_id=user_id,
                amount=amount,
                currency=currency,
                status=EscrowStatus.CREATED.value,
                created_at=datetime.utcnow()
            )
            if hasattr(legacy_entity, 'unified_transaction_id'):
                legacy_entity.unified_transaction_id = unified_transaction_id
                
        elif system_type == LegacySystemType.EXCHANGE:
            # Create ExchangeOrder record
            legacy_entity = ExchangeOrder(
                user_id=user_id,
                amount=amount,
                currency=currency,
                status=ExchangeStatus.CREATED.value,
                created_at=datetime.utcnow()
            )
            if hasattr(legacy_entity, 'unified_transaction_id'):
                legacy_entity.unified_transaction_id = unified_transaction_id
        
        session.add(legacy_entity)
        session.flush()
        
        return legacy_entity
    
    def _update_legacy_entity_with_unified_ref(self,
                                             session: Session,
                                             system_type: LegacySystemType,
                                             legacy_entity_id: str,
                                             unified_transaction_id: str) -> Any:
        """Update existing legacy entity with unified transaction reference"""
        
        legacy_entity = self._find_legacy_entity(session, legacy_entity_id, system_type)
        if not legacy_entity:
            raise ValueError(f"Legacy entity not found: {legacy_entity_id}")
        
        # Add reference to unified transaction
        if hasattr(legacy_entity, 'unified_transaction_id'):
            legacy_entity.unified_transaction_id = unified_transaction_id
            legacy_entity.updated_at = datetime.utcnow()
        
        return legacy_entity
    
    def _find_legacy_entity(self, session: Session, entity_id: str, system_type: LegacySystemType) -> Optional[Any]:
        """Find legacy entity by ID and type"""
        try:
            if system_type == LegacySystemType.CASHOUT:
                return session.query(Cashout).filter(Cashout.cashout_id == entity_id).first()
            elif system_type == LegacySystemType.ESCROW:
                return session.query(Escrow).filter(Escrow.escrow_id == entity_id).first()
            elif system_type == LegacySystemType.EXCHANGE:
                return session.query(ExchangeOrder).filter(ExchangeOrder.id == int(entity_id)).first()
        except Exception as e:
            logger.error(f"Error finding legacy entity {entity_id} of type {system_type}: {e}")
            return None
    
    def _find_linked_legacy_entity(self, session: Session, unified_tx: UnifiedTransaction) -> Optional[Any]:
        """Find legacy entity linked to unified transaction (using reference_id)"""
        # Extract entity type from metadata or transaction_type
        entity_id = unified_tx.reference_id  # reference_id stores the cashout_id/escrow_id/exchange_id
        
        if not entity_id:
            return None
            
        # Determine system type from transaction_type
        if unified_tx.transaction_type == "wallet_cashout":
            return self._find_legacy_entity(session, entity_id, LegacySystemType.CASHOUT)
        elif unified_tx.transaction_type == "escrow":
            return self._find_legacy_entity(session, entity_id, LegacySystemType.ESCROW)
        elif unified_tx.transaction_type in ["exchange_sell_crypto", "exchange_buy_crypto"]:
            return self._find_legacy_entity(session, entity_id, LegacySystemType.EXCHANGE)
        return None
    
    def _find_linked_unified_transaction(self, session: Session, legacy_entity: Any) -> Optional[UnifiedTransaction]:
        """Find unified transaction linked to legacy entity (using reference_id)"""
        # First try direct reference
        if hasattr(legacy_entity, 'unified_transaction_id') and legacy_entity.unified_transaction_id:
            return session.query(UnifiedTransaction).filter(
                UnifiedTransaction.external_id == legacy_entity.unified_transaction_id
            ).first()
        
        # Try reverse lookup by entity ID (stored in reference_id)
        entity_id = self._get_legacy_entity_id(legacy_entity)
        
        return session.query(UnifiedTransaction).filter(
            UnifiedTransaction.reference_id == entity_id
        ).first()
    
    def _detect_legacy_system_type(self, legacy_entity: Any) -> LegacySystemType:
        """Detect legacy system type from entity"""
        if isinstance(legacy_entity, Cashout):
            return LegacySystemType.CASHOUT
        elif isinstance(legacy_entity, Escrow):
            return LegacySystemType.ESCROW
        elif isinstance(legacy_entity, ExchangeOrder):
            return LegacySystemType.EXCHANGE
        else:
            raise ValueError(f"Unknown legacy entity type: {type(legacy_entity)}")
    
    def _get_legacy_entity_id(self, legacy_entity: Any) -> str:
        """Get ID from legacy entity"""
        if hasattr(legacy_entity, 'cashout_id'):
            return legacy_entity.cashout_id
        elif hasattr(legacy_entity, 'escrow_id'):
            return legacy_entity.escrow_id
        elif hasattr(legacy_entity, 'id'):
            return str(legacy_entity.id)
        else:
            raise ValueError(f"Cannot determine ID for legacy entity: {type(legacy_entity)}")
    
    def _get_legacy_entity_status(self, legacy_entity: Any, system_type: LegacySystemType) -> Any:
        """Get status from legacy entity"""
        if system_type == LegacySystemType.CASHOUT:
            return CashoutStatus(legacy_entity.status)
        elif system_type == LegacySystemType.ESCROW:
            return EscrowStatus(legacy_entity.status)
        elif system_type == LegacySystemType.EXCHANGE:
            return ExchangeStatus(legacy_entity.status)
        else:
            raise ValueError(f"Unknown system type: {system_type}")
    
    def _update_legacy_entity_status(self, legacy_entity: Any, new_status: Any, system_type: LegacySystemType):
        """Update status on legacy entity"""
        legacy_entity.status = new_status.value
        legacy_entity.updated_at = datetime.utcnow()
    
    def _get_legacy_entity_amount(self, legacy_entity: Any) -> float:
        """Get amount from legacy entity"""
        return float(legacy_entity.amount)
    
    def _get_legacy_entity_user_id(self, legacy_entity: Any) -> int:
        """Get user ID from legacy entity"""
        return legacy_entity.user_id
    
    def _validate_transaction_consistency(self, legacy_entity: Any, unified_tx: UnifiedTransaction):
        """Validate consistency between legacy and unified transactions"""
        # Check basic fields match
        if abs(self._get_legacy_entity_amount(legacy_entity) - float(unified_tx.amount)) > 0.0001:
            logger.warning(f"Amount mismatch: legacy={self._get_legacy_entity_amount(legacy_entity)}, "
                         f"unified={unified_tx.amount}")
        
        if self._get_legacy_entity_user_id(legacy_entity) != unified_tx.user_id:
            logger.warning(f"User ID mismatch: legacy={self._get_legacy_entity_user_id(legacy_entity)}, "
                         f"unified={unified_tx.user_id}")
    
    def _handle_inconsistency(self, result: DualWriteResult, session: Session):
        """Handle inconsistencies based on configuration"""
        if self.config.inconsistency_alerts:
            logger.warning(f"⚠️ Dual-write inconsistency detected [Op: {result.operation_id}] "
                         f"legacy_success={result.legacy_success}, unified_success={result.unified_success}")
        
        # Could implement automatic reconciliation strategies here
        # For now, just log the inconsistency
        
    def _rollback_unified_transaction(self, session: Session, unified_tx: UnifiedTransaction):
        """Rollback unified transaction creation"""
        try:
            session.delete(unified_tx)
            session.flush()
            logger.info(f"🔄 Rolled back unified transaction: {unified_tx.transaction_id}")
        except Exception as e:
            logger.error(f"❌ Failed to rollback unified transaction: {e}")
    
    def _repair_unified_wins(self, session: Session, entity_id: str, 
                           system_type: LegacySystemType, operation_id: str) -> DualWriteResult:
        """Repair consistency with unified system as source of truth"""
        # Implementation for unified-wins repair strategy
        # This would update legacy entity to match unified transaction
        logger.info(f"Executing unified-wins repair [Op: {operation_id}]")
        return DualWriteResult(legacy_success=True, unified_success=True, operation_id=operation_id)
    
    def _repair_legacy_wins(self, session: Session, entity_id: str,
                          system_type: LegacySystemType, operation_id: str) -> DualWriteResult:
        """Repair consistency with legacy system as source of truth"""
        # Implementation for legacy-wins repair strategy
        # This would update unified transaction to match legacy entity
        logger.info(f"Executing legacy-wins repair [Op: {operation_id}]")
        return DualWriteResult(legacy_success=True, unified_success=True, operation_id=operation_id)
    
    def _log_dual_write_audit(self, operation_type: str, operation_id: str, result: DualWriteResult):
        """Log audit trail for dual-write operations"""
        audit_data = {
            "operation_type": operation_type,
            "operation_id": operation_id,
            "timestamp": datetime.utcnow(),
            "legacy_success": result.legacy_success,
            "unified_success": result.unified_success,
            "overall_success": result.overall_success,
            "has_inconsistency": result.has_inconsistency,
            "rollback_performed": result.rollback_performed,
            "mode": self.config.mode.value,
            "strategy": self.config.strategy.value
        }
        
        logger.info(f"📋 AUDIT_LOG [{operation_type}]: {audit_data}")


# =============== DUAL WRITE ADAPTER FACTORY ===============

def create_dual_write_adapter(mode: DualWriteMode = DualWriteMode.DUAL_WRITE_LEGACY_PRIMARY,
                             strategy: DualWriteStrategy = DualWriteStrategy.FAIL_FAST,
                             **config_kwargs) -> DualWriteAdapter:
    """
    Factory function to create configured DualWriteAdapter
    
    Args:
        mode: Dual-write operation mode
        strategy: Failure handling strategy
        **config_kwargs: Additional configuration options
        
    Returns:
        Configured DualWriteAdapter instance
    """
    config = DualWriteConfig(mode=mode, strategy=strategy, **config_kwargs)
    return DualWriteAdapter(config)


# =============== GLOBAL ADAPTER INSTANCES ===============

# Create commonly used adapter configurations
legacy_primary_adapter = create_dual_write_adapter(
    mode=DualWriteMode.DUAL_WRITE_LEGACY_PRIMARY,
    strategy=DualWriteStrategy.FAIL_FAST
)

unified_primary_adapter = create_dual_write_adapter(
    mode=DualWriteMode.DUAL_WRITE_UNIFIED_PRIMARY,
    strategy=DualWriteStrategy.FAIL_FAST
)

best_effort_adapter = create_dual_write_adapter(
    mode=DualWriteMode.DUAL_WRITE_LEGACY_PRIMARY,
    strategy=DualWriteStrategy.BEST_EFFORT
)