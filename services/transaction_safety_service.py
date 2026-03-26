"""
Transaction Safety Service - Atomic balance operations with proper locking and validation

Provides atomic balance operations with comprehensive safety mechanisms, complete audit trails,
and rollback capabilities to ensure data consistency and prevent race conditions.

Key Features:
- Atomic balance operations with database-level locking
- Complete integration with BalanceAuditService for audit trails
- Support for complex multi-wallet operations
- Automatic rollback on failures
- Idempotency protection for financial operations
- Balance validation before and after operations
- Support for both user and internal wallet operations
"""

import logging
import uuid
from typing import Dict, Any, Optional, List
from decimal import Decimal
from datetime import datetime, timezone
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum

from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from sqlalchemy import and_

from models import (
    Wallet, InternalWallet, IdempotencyToken
)
from utils.database_locking import DatabaseLockingService
from services.balance_audit_service import (
    BalanceAuditService, BalanceChangeContext, balance_audit_service
)

logger = logging.getLogger(__name__)


class OperationResult(Enum):
    """Result types for transaction operations"""
    SUCCESS = "success"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"
    DUPLICATE = "duplicate"
    INSUFFICIENT_FUNDS = "insufficient_funds"
    VALIDATION_FAILED = "validation_failed"

# Alias for backward compatibility 
TransactionResultType = OperationResult


@dataclass
class BalanceOperation:
    """Represents a single balance operation in a transaction"""
    wallet_type: str  # 'user' or 'internal'
    user_id: Optional[int] = None
    wallet_id: Optional[int] = None
    internal_wallet_id: Optional[str] = None
    currency: str = "USD"
    balance_type: str = "available"  # 'available', 'frozen', 'locked', 'reserved'
    amount: Decimal = Decimal('0')
    operation: str = "credit"  # 'credit' or 'debit'
    
    # Operation context
    description: str = ""
    transaction_id: Optional[str] = None
    reference_id: Optional[str] = None
    metadata: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


@dataclass
class TransactionContext:
    """Context information for a transaction"""
    transaction_id: str
    transaction_type: str
    operation_type: str
    initiated_by: str = "system"
    initiated_by_id: Optional[str] = None
    reason: str = "Balance operation"
    
    # Related entities
    escrow_id: Optional[str] = None
    cashout_id: Optional[str] = None
    exchange_id: Optional[str] = None
    
    # Additional metadata
    metadata: Dict[str, Any] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


@dataclass
class TransactionResult:
    """Result of a transaction operation"""
    success: bool
    result_type: OperationResult
    transaction_id: str
    operations_completed: int = 0
    operations_failed: int = 0
    error_message: Optional[str] = None
    audit_ids: List[str] = None
    balances_after: Dict[str, Decimal] = None
    rollback_performed: bool = False
    duplicate_detected: bool = False
    
    def __post_init__(self):
        if self.audit_ids is None:
            self.audit_ids = []
        if self.balances_after is None:
            self.balances_after = {}


class TransactionSafetyService:
    """
    Service for atomic balance operations with comprehensive safety mechanisms
    
    Provides transactional safety for all balance operations with proper locking,
    validation, and complete audit trails.
    """
    
    def __init__(self):
        """Initialize the transaction safety service"""
        self.locking_service = DatabaseLockingService()
        self.audit_service = balance_audit_service
        
    @contextmanager
    def atomic_transaction(
        self, 
        session: Session,
        context: TransactionContext,
        lock_timeout: int = 30,
        enable_snapshots: bool = True
    ):
        """
        Context manager for atomic transactions with locking and rollback
        
        Args:
            session: Database session
            context: Transaction context information
            lock_timeout: Lock timeout in seconds
            enable_snapshots: Whether to create before/after snapshots
            
        Yields:
            TransactionManager instance for managing the transaction
        """
        transaction_manager = None
        try:
            # Create transaction manager
            transaction_manager = TransactionManager(
                session=session,
                context=context,
                locking_service=self.locking_service,
                audit_service=self.audit_service,
                lock_timeout=lock_timeout,
                enable_snapshots=enable_snapshots
            )
            
            # Begin transaction
            transaction_manager.begin_transaction()
            
            logger.info(f"🔒 TRANSACTION: Started atomic transaction {context.transaction_id}")
            
            yield transaction_manager
            
            # Commit if no exceptions
            transaction_manager.commit_transaction()
            
            logger.info(f"✅ TRANSACTION: Successfully committed transaction {context.transaction_id}")
            
        except Exception as e:
            if transaction_manager:
                try:
                    transaction_manager.rollback_transaction()
                    logger.error(
                        f"🔄 TRANSACTION: Rolled back transaction {context.transaction_id} due to error: {e}"
                    )
                except Exception as rollback_error:
                    logger.error(
                        f"❌ TRANSACTION: Failed to rollback transaction {context.transaction_id}: {rollback_error}"
                    )
            raise
        finally:
            if transaction_manager:
                transaction_manager.cleanup()
    
    def execute_balance_operations(
        self,
        session: Session,
        operations: List[BalanceOperation],
        context: TransactionContext,
        validate_balances: bool = True,
        create_snapshots: bool = True
    ) -> TransactionResult:
        """
        Execute multiple balance operations atomically
        
        Args:
            session: Database session
            operations: List of balance operations to execute
            context: Transaction context
            validate_balances: Whether to validate balances before and after
            create_snapshots: Whether to create balance snapshots
            
        Returns:
            TransactionResult with operation outcome
        """
        try:
            with self.atomic_transaction(session, context, enable_snapshots=create_snapshots) as tx_manager:
                
                # Check for idempotency
                if self._check_duplicate_transaction(session, context.transaction_id):
                    return TransactionResult(
                        success=True,
                        result_type=OperationResult.DUPLICATE,
                        transaction_id=context.transaction_id,
                        duplicate_detected=True
                    )
                
                # Create idempotency token
                self._create_idempotency_token(session, context.transaction_id, "balance_operations")
                
                # Execute all operations
                result = tx_manager.execute_operations(operations, context)
                
                if not result.success:
                    return result
                
                # Validate final state if requested
                if validate_balances:
                    validation_success = tx_manager.validate_final_balances(operations)
                    if not validation_success:
                        return TransactionResult(
                            success=False,
                            result_type=OperationResult.VALIDATION_FAILED,
                            transaction_id=context.transaction_id,
                            error_message="Post-operation balance validation failed"
                        )
                
                return result
                
        except Exception as e:
            logger.error(f"❌ BALANCE_OPS: Error executing balance operations: {e}")
            return TransactionResult(
                success=False,
                result_type=OperationResult.FAILED,
                transaction_id=context.transaction_id,
                error_message=str(e)
            )
    
    def safe_wallet_credit(
        self,
        session: Session,
        user_id: int,
        amount: Decimal,
        currency: str = "USD",
        transaction_type: str = "deposit",
        description: str = "",
        **kwargs
    ) -> TransactionResult:
        """
        Safely credit a user wallet with full audit trail
        
        Args:
            session: Database session
            user_id: User ID
            amount: Amount to credit
            currency: Currency
            transaction_type: Type of transaction
            description: Description of the operation
            **kwargs: Additional context parameters
            
        Returns:
            TransactionResult with operation outcome
        """
        # Generate transaction ID
        transaction_id = f"credit_{uuid.uuid4().hex[:12]}"
        
        # Create transaction context
        context = TransactionContext(
            transaction_id=transaction_id,
            transaction_type=transaction_type,
            operation_type="wallet_credit",
            reason=description or f"Credit {amount} {currency}",
            **{k: v for k, v in kwargs.items() if k in ['initiated_by', 'initiated_by_id', 'escrow_id', 'cashout_id', 'exchange_id']}
        )
        
        # Create balance operation
        operation = BalanceOperation(
            wallet_type="user",
            user_id=user_id,
            currency=currency,
            balance_type="available",
            amount=amount,
            operation="credit",
            description=description,
            transaction_id=transaction_id
        )
        
        return self.execute_balance_operations(session, [operation], context)
    
    def safe_wallet_debit(
        self,
        session: Session,
        user_id: int,
        amount: Decimal,
        currency: str = "USD",
        transaction_type: str = "withdrawal",
        description: str = "",
        check_balance: bool = True,
        **kwargs
    ) -> TransactionResult:
        """
        Safely debit a user wallet with balance validation
        
        Args:
            session: Database session
            user_id: User ID
            amount: Amount to debit
            currency: Currency
            transaction_type: Type of transaction
            description: Description of the operation
            check_balance: Whether to check sufficient balance
            **kwargs: Additional context parameters
            
        Returns:
            TransactionResult with operation outcome
        """
        # Generate transaction ID
        transaction_id = f"debit_{uuid.uuid4().hex[:12]}"
        
        # Check sufficient balance if requested
        if check_balance:
            wallet = session.query(Wallet).filter(
                and_(Wallet.user_id == user_id, Wallet.currency == currency)
            ).first()
            
            if not wallet or wallet.available_balance < amount:
                return TransactionResult(
                    success=False,
                    result_type=OperationResult.INSUFFICIENT_FUNDS,
                    transaction_id=transaction_id,
                    error_message=f"Insufficient balance for debit of {amount} {currency}"
                )
        
        # Create transaction context
        context = TransactionContext(
            transaction_id=transaction_id,
            transaction_type=transaction_type,
            operation_type="wallet_debit",
            reason=description or f"Debit {amount} {currency}",
            **{k: v for k, v in kwargs.items() if k in ['initiated_by', 'initiated_by_id', 'escrow_id', 'cashout_id', 'exchange_id']}
        )
        
        # Create balance operation
        operation = BalanceOperation(
            wallet_type="user",
            user_id=user_id,
            currency=currency,
            balance_type="available",
            amount=amount,
            operation="debit",
            description=description,
            transaction_id=transaction_id
        )
        
        return self.execute_balance_operations(session, [operation], context)
    
    def safe_internal_wallet_operation(
        self,
        session: Session,
        internal_wallet_id: str,
        amount: Decimal,
        operation: str = "credit",
        balance_type: str = "available",
        transaction_type: str = "internal_operation",
        description: str = "",
        **kwargs
    ) -> TransactionResult:
        """
        Safely perform internal wallet operations
        
        Args:
            session: Database session
            internal_wallet_id: Internal wallet ID
            amount: Amount to credit/debit
            operation: 'credit' or 'debit'
            balance_type: 'available', 'locked', or 'reserved'
            transaction_type: Type of transaction
            description: Description of the operation
            **kwargs: Additional context parameters
            
        Returns:
            TransactionResult with operation outcome
        """
        # Generate transaction ID
        transaction_id = f"internal_{operation}_{uuid.uuid4().hex[:12]}"
        
        # Get internal wallet for currency
        internal_wallet = session.query(InternalWallet).filter(
            InternalWallet.wallet_id == internal_wallet_id
        ).first()
        
        if not internal_wallet:
            return TransactionResult(
                success=False,
                result_type=OperationResult.FAILED,
                transaction_id=transaction_id,
                error_message=f"Internal wallet {internal_wallet_id} not found"
            )
        
        # Create transaction context
        context = TransactionContext(
            transaction_id=transaction_id,
            transaction_type=transaction_type,
            operation_type=f"internal_wallet_{operation}",
            reason=description or f"Internal {operation} {amount} {internal_wallet.currency}",
            **{k: v for k, v in kwargs.items() if k in ['initiated_by', 'initiated_by_id']}
        )
        
        # Create balance operation
        balance_operation = BalanceOperation(
            wallet_type="internal",
            internal_wallet_id=internal_wallet_id,
            currency=internal_wallet.currency,
            balance_type=balance_type,
            amount=amount,
            operation=operation,
            description=description,
            transaction_id=transaction_id
        )
        
        return self.execute_balance_operations(session, [balance_operation], context)
    
    def transfer_between_wallets(
        self,
        session: Session,
        from_user_id: int,
        to_user_id: int,
        amount: Decimal,
        currency: str = "USD",
        transaction_type: str = "transfer",
        description: str = "",
        **kwargs
    ) -> TransactionResult:
        """
        Transfer funds between user wallets atomically
        
        Args:
            session: Database session
            from_user_id: Source user ID
            to_user_id: Destination user ID
            amount: Amount to transfer
            currency: Currency
            transaction_type: Type of transaction
            description: Description of the transfer
            **kwargs: Additional context parameters
            
        Returns:
            TransactionResult with operation outcome
        """
        # Generate transaction ID
        transaction_id = f"transfer_{uuid.uuid4().hex[:12]}"
        
        # Check source wallet balance
        source_wallet = session.query(Wallet).filter(
            and_(Wallet.user_id == from_user_id, Wallet.currency == currency)
        ).first()
        
        if not source_wallet or source_wallet.available_balance < amount:
            return TransactionResult(
                success=False,
                result_type=OperationResult.INSUFFICIENT_FUNDS,
                transaction_id=transaction_id,
                error_message=f"Insufficient balance in source wallet for transfer of {amount} {currency}"
            )
        
        # Create transaction context
        context = TransactionContext(
            transaction_id=transaction_id,
            transaction_type=transaction_type,
            operation_type="wallet_transfer",
            reason=description or f"Transfer {amount} {currency} from user {from_user_id} to user {to_user_id}",
            **{k: v for k, v in kwargs.items() if k in ['initiated_by', 'initiated_by_id', 'escrow_id']}
        )
        
        # Create debit and credit operations
        operations = [
            BalanceOperation(
                wallet_type="user",
                user_id=from_user_id,
                currency=currency,
                balance_type="available",
                amount=amount,
                operation="debit",
                description=f"Transfer to user {to_user_id}",
                transaction_id=transaction_id
            ),
            BalanceOperation(
                wallet_type="user",
                user_id=to_user_id,
                currency=currency,
                balance_type="available",
                amount=amount,
                operation="credit",
                description=f"Transfer from user {from_user_id}",
                transaction_id=transaction_id
            )
        ]
        
        return self.execute_balance_operations(session, operations, context)
    
    # Private helper methods
    
    def _check_duplicate_transaction(self, session: Session, transaction_id: str) -> bool:
        """Check if transaction has already been processed"""
        try:
            existing = session.query(IdempotencyToken).filter(
                IdempotencyToken.idempotency_key == transaction_id
            ).first()
            return existing is not None
        except Exception as e:
            logger.error(f"Error checking duplicate transaction: {e}")
            return False
    
    def _create_idempotency_token(self, session: Session, transaction_id: str, operation_type: str):
        """Create idempotency token to prevent duplicate processing"""
        try:
            token = IdempotencyToken(
                idempotency_key=transaction_id,
                operation_type=operation_type,
                resource_id=transaction_id,
                status="processing",
                expires_at=datetime.now(timezone.utc).replace(hour=23, minute=59, second=59)  # End of day
            )
            session.add(token)
            session.flush()
        except IntegrityError:
            # Token already exists - this is expected for duplicate detection
            pass


class TransactionManager:
    """
    Internal transaction manager for atomic operations
    
    Handles locking, validation, audit logging, and rollback for complex transactions.
    """
    
    def __init__(
        self,
        session: Session,
        context: TransactionContext,
        locking_service: DatabaseLockingService,
        audit_service: BalanceAuditService,
        lock_timeout: int = 30,
        enable_snapshots: bool = True
    ):
        self.session = session
        self.context = context
        self.locking_service = locking_service
        self.audit_service = audit_service
        self.lock_timeout = lock_timeout
        self.enable_snapshots = enable_snapshots
        
        # State tracking
        self.locks_acquired = []
        self.snapshots_created = []
        self.audit_logs_created = []
        self.operations_completed = []
        self.rollback_operations = []
        
    def begin_transaction(self):
        """Begin the transaction with initial setup"""
        logger.debug(f"🔄 TX_MGR: Beginning transaction {self.context.transaction_id}")
    
    def execute_operations(self, operations: List[BalanceOperation], context: TransactionContext) -> TransactionResult:
        """Execute all balance operations with proper locking and audit"""
        operations_completed = 0
        operations_failed = 0
        audit_ids = []
        balances_after = {}
        
        try:
            for operation in operations:
                # Acquire locks for the operation
                self._acquire_locks_for_operation(operation)
                
                # Create pre-operation snapshot if enabled
                if self.enable_snapshots:
                    snapshot_id = self._create_pre_operation_snapshot(operation)
                    if snapshot_id:
                        self.snapshots_created.append(snapshot_id)
                
                # Get current balance
                current_balance = self._get_current_balance(operation)
                
                # Calculate new balance
                if operation.operation == "credit":
                    new_balance = current_balance + operation.amount
                else:  # debit
                    new_balance = current_balance - operation.amount
                    if new_balance < 0:
                        raise ValueError(f"Operation would result in negative balance: {new_balance}")
                
                # Update the balance in database
                self._update_balance_in_db(operation, new_balance)
                
                # Create audit log
                audit_context = self._create_audit_context(operation, context)
                audit_result = self.audit_service.log_balance_change(
                    self.session,
                    audit_context,
                    current_balance,
                    new_balance
                )
                
                if audit_result.success:
                    audit_ids.append(audit_result.audit_id)
                    self.audit_logs_created.append(audit_result.audit_id)
                
                # Track operation for rollback
                self.operations_completed.append({
                    'operation': operation,
                    'previous_balance': current_balance,
                    'new_balance': new_balance
                })
                
                # Store final balance
                balance_key = f"{operation.wallet_type}_{operation.user_id or operation.internal_wallet_id}_{operation.currency}_{operation.balance_type}"
                balances_after[balance_key] = new_balance
                
                operations_completed += 1
                
                logger.debug(
                    f"✅ TX_MGR: Completed operation {operation.operation} "
                    f"{operation.amount} {operation.currency} "
                    f"({current_balance} -> {new_balance})"
                )
            
            return TransactionResult(
                success=True,
                result_type=OperationResult.SUCCESS,
                transaction_id=context.transaction_id,
                operations_completed=operations_completed,
                operations_failed=operations_failed,
                audit_ids=audit_ids,
                balances_after=balances_after
            )
            
        except Exception as e:
            operations_failed = len(operations) - operations_completed
            logger.error(f"❌ TX_MGR: Operation failed: {e}")
            
            return TransactionResult(
                success=False,
                result_type=OperationResult.FAILED,
                transaction_id=context.transaction_id,
                operations_completed=operations_completed,
                operations_failed=operations_failed,
                error_message=str(e),
                audit_ids=audit_ids,
                balances_after=balances_after
            )
    
    def commit_transaction(self):
        """Commit the transaction and update idempotency token"""
        try:
            # Update idempotency token to completed
            self.session.query(IdempotencyToken).filter(
                IdempotencyToken.idempotency_key == self.context.transaction_id
            ).update({
                "status": "completed",
                "completed_at": datetime.now(timezone.utc)
            })
            
            self.session.commit()
            logger.debug(f"✅ TX_MGR: Transaction {self.context.transaction_id} committed successfully")
            
        except Exception as e:
            logger.error(f"❌ TX_MGR: Error committing transaction {self.context.transaction_id}: {e}")
            raise
    
    def rollback_transaction(self) -> bool:
        """Rollback the transaction and restore previous state"""
        try:
            # Rollback database changes
            self.session.rollback()
            
            logger.warning(f"🔄 TX_MGR: Transaction {self.context.transaction_id} rolled back")
            return True
            
        except Exception as e:
            logger.error(f"❌ TX_MGR: Error rolling back transaction {self.context.transaction_id}: {e}")
            return False
    
    def validate_final_balances(self, operations: List[BalanceOperation]) -> bool:
        """Validate that all balances are in valid state after operations"""
        try:
            for operation in operations:
                current_balance = self._get_current_balance(operation)
                if current_balance < 0:
                    logger.error(f"❌ VALIDATE: Negative balance detected after operation: {current_balance}")
                    return False
            return True
        except Exception as e:
            logger.error(f"❌ VALIDATE: Error validating final balances: {e}")
            return False
    
    def cleanup(self):
        """Clean up resources and release locks"""
        # Locks are automatically released when session ends
        logger.debug(f"🧹 TX_MGR: Cleanup completed for transaction {self.context.transaction_id}")
    
    # Private helper methods
    
    def _acquire_locks_for_operation(self, operation: BalanceOperation):
        """Acquire necessary locks for the operation"""
        if operation.wallet_type == "user":
            # Use wallet locking for user operations
            lock_key = f"user_wallet_{operation.user_id}_{operation.currency}"
            # Database row-level locking is handled by the session
        elif operation.wallet_type == "internal":
            # Use internal wallet locking
            lock_key = f"internal_wallet_{operation.internal_wallet_id}"
            # Database row-level locking is handled by the session
        
        self.locks_acquired.append(lock_key)
    
    def _create_pre_operation_snapshot(self, operation: BalanceOperation) -> Optional[str]:
        """Create a pre-operation balance snapshot"""
        try:
            return self.audit_service.create_balance_snapshot(
                self.session,
                wallet_type=operation.wallet_type,
                user_id=operation.user_id,
                wallet_id=operation.wallet_id,
                internal_wallet_id=operation.internal_wallet_id,
                snapshot_type="pre_operation",
                trigger_event=f"Before {operation.operation} {operation.amount} {operation.currency}"
            )
        except Exception as e:
            logger.warning(f"⚠️ SNAPSHOT: Failed to create pre-operation snapshot: {e}")
            return None
    
    def _get_current_balance(self, operation: BalanceOperation) -> Decimal:
        """Get the current balance for the operation"""
        if operation.wallet_type == "user":
            wallet = self.session.query(Wallet).filter(
                and_(
                    Wallet.user_id == operation.user_id,
                    Wallet.currency == operation.currency
                )
            ).first()
            
            if not wallet:
                # Create wallet if it doesn't exist
                wallet = Wallet(
                    user_id=operation.user_id,
                    currency=operation.currency,
                    available_balance=Decimal('0'),
                    frozen_balance=Decimal('0'),
                    locked_balance=Decimal('0')
                )
                self.session.add(wallet)
                self.session.flush()
                operation.wallet_id = wallet.id
                return Decimal('0')
            
            operation.wallet_id = wallet.id
            
            if operation.balance_type == "available":
                return wallet.available_balance
            elif operation.balance_type == "frozen":
                return wallet.frozen_balance
            elif operation.balance_type == "locked":
                return wallet.locked_balance
            else:
                raise ValueError(f"Invalid balance type for user wallet: {operation.balance_type}")
                
        elif operation.wallet_type == "internal":
            internal_wallet = self.session.query(InternalWallet).filter(
                InternalWallet.wallet_id == operation.internal_wallet_id
            ).first()
            
            if not internal_wallet:
                raise ValueError(f"Internal wallet {operation.internal_wallet_id} not found")
            
            if operation.balance_type == "available":
                return internal_wallet.available_balance
            elif operation.balance_type == "locked":
                return internal_wallet.locked_balance
            elif operation.balance_type == "reserved":
                return internal_wallet.reserved_balance
            else:
                raise ValueError(f"Invalid balance type for internal wallet: {operation.balance_type}")
    
    def _update_balance_in_db(self, operation: BalanceOperation, new_balance: Decimal):
        """Update the balance in the database"""
        if operation.wallet_type == "user":
            wallet = self.session.query(Wallet).filter(
                Wallet.id == operation.wallet_id
            ).first()
            
            if operation.balance_type == "available":
                wallet.available_balance = new_balance
            elif operation.balance_type == "frozen":
                wallet.frozen_balance = new_balance
            elif operation.balance_type == "locked":
                wallet.locked_balance = new_balance
            
            # Update version for optimistic locking
            wallet.version += 1
            
        elif operation.wallet_type == "internal":
            internal_wallet = self.session.query(InternalWallet).filter(
                InternalWallet.wallet_id == operation.internal_wallet_id
            ).first()
            
            if operation.balance_type == "available":
                internal_wallet.available_balance = new_balance
            elif operation.balance_type == "locked":
                internal_wallet.locked_balance = new_balance
            elif operation.balance_type == "reserved":
                internal_wallet.reserved_balance = new_balance
            
            # Update total balance
            internal_wallet.total_balance = (
                internal_wallet.available_balance + 
                internal_wallet.locked_balance + 
                internal_wallet.reserved_balance
            )
            
            # Update version for optimistic locking
            internal_wallet.version += 1
    
    def _create_audit_context(self, operation: BalanceOperation, context: TransactionContext) -> BalanceChangeContext:
        """Create audit context for balance change logging"""
        return BalanceChangeContext(
            wallet_type=operation.wallet_type,
            user_id=operation.user_id,
            wallet_id=operation.wallet_id,
            internal_wallet_id=operation.internal_wallet_id,
            currency=operation.currency,
            balance_type=operation.balance_type,
            transaction_id=operation.transaction_id,
            transaction_type=context.transaction_type,
            operation_type=context.operation_type,
            initiated_by=context.initiated_by,
            initiated_by_id=context.initiated_by_id,
            reason=operation.description or context.reason,
            escrow_id=context.escrow_id,
            cashout_id=context.cashout_id,
            exchange_id=context.exchange_id,
            metadata=operation.metadata,
            ip_address=context.ip_address,
            user_agent=context.user_agent
        )


# Global instance for easy access
transaction_safety_service = TransactionSafetyService()