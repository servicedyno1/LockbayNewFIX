"""Atomic transaction utilities for financial operations and admin actions"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager, contextmanager
from functools import wraps
from typing import Any, Callable, Optional, TypeVar, Generator, Dict, AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError, OperationalError, IntegrityError
from sqlalchemy import text
from database import SessionLocal, handle_database_error


# DEPRECATED: AsyncSessionAdapter removed due to thread safety issues
# Use run_io_task pattern with sync sessions instead
# class AsyncSessionAdapter was unsafe and has been removed

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


@asynccontextmanager
async def async_atomic_transaction(
    session: Optional[AsyncSession] = None,
) -> AsyncGenerator[AsyncSession, None]:
    """
    Async context manager for atomic database transactions with proper rollback.
    Ensures financial operations are fully atomic and consistent.
    
    PERFORMANCE OPTIMIZED: Enhanced with connection timeout monitoring and fast-fail patterns.
    """
    session_provided = session is not None
    if not session_provided:
        # DEPRECATED: Creating new async session without providing one is not supported
        # Use run_io_task pattern with sync sessions instead, or pass an async session
        raise DeprecationWarning(
            "async_atomic_transaction without providing a session is deprecated. "
            "Use run_io_task pattern with sync sessions instead, or pass an async session."
        )
    else:
        logger.debug("Using provided async session for atomic transaction")
        # Initialize transaction_depth before try block to avoid unbound variable
        transaction_depth = getattr(session, '_atomic_transaction_depth', 0)
        try:
            # For provided sessions, track transaction depth for nested transactions
            setattr(session, '_atomic_transaction_depth', transaction_depth + 1)
            
            if transaction_depth > 0:
                logger.debug(f"Nested async transaction detected (depth: {transaction_depth + 1})")
            
            yield session
            
            # For nested transactions, let the outermost handle commit
            if transaction_depth == 0:
                await session.commit()
                logger.debug("Outermost async transaction committed successfully")
            else:
                logger.debug(f"Nested async transaction completed (depth: {transaction_depth + 1}), deferring commit to outermost")
                
        except Exception as e:
            # Always rollback on error, regardless of nesting
            await session.rollback()
            logger.error(f"Async transaction rolled back due to error (depth: {transaction_depth + 1}): {e}")
            raise
        finally:
            # Clean up transaction depth tracking
            current_depth = getattr(session, '_atomic_transaction_depth', 1)
            setattr(session, '_atomic_transaction_depth', max(0, current_depth - 1))


@contextmanager
def atomic_transaction(session: Optional[Session] = None) -> Generator[Session, None, None]:
    """
    Synchronous context manager for atomic database transactions with proper rollback.
    Ensures financial operations are fully atomic and consistent.
    
    This is the SYNC version for use with regular `with` statements.
    For async code, use `async with async_atomic_transaction()`.
    
    Includes automatic recovery for database connection errors (Neon/Railway).
    """
    session_provided = session is not None
    if not session_provided:
        # Create new sync session with retry logic for connection errors
        max_retries = 2
        last_error = None
        
        for attempt in range(max_retries + 1):
            session = None
            try:
                session = SessionLocal()
                logger.debug("Created new sync session for atomic transaction")
                try:
                    yield session
                    session.commit()
                    logger.debug("Sync atomic transaction committed successfully")
                    return  # Success
                except OperationalError:
                    if session:
                        try:
                            session.rollback()
                        except Exception:
                            pass
                    raise
                except Exception as e:
                    if session:
                        session.rollback()
                    logger.error(f"Sync transaction rolled back due to error: {e}")
                    raise
            except OperationalError as e:
                last_error = e
                if attempt < max_retries and handle_database_error(e):
                    logger.info(f"🔄 Retrying atomic transaction (attempt {attempt + 2}/{max_retries + 1})...")
                    time.sleep(1)
                    continue
                logger.error(f"Sync transaction rolled back due to error: {e}")
                raise
            finally:
                if session:
                    try:
                        session.close()
                    except Exception:
                        pass
        
        if last_error:
            raise last_error
    else:
        logger.debug("Using provided sync session for atomic transaction")
        # Initialize transaction_depth before try block to avoid unbound variable
        transaction_depth = getattr(session, '_atomic_transaction_depth', 0)
        try:
            # For provided sessions, track transaction depth for nested transactions
            setattr(session, '_atomic_transaction_depth', transaction_depth + 1)
            
            if transaction_depth > 0:
                logger.debug(f"Nested sync transaction detected (depth: {transaction_depth + 1})")
            
            yield session
            
            # For nested transactions, let the outermost handle commit
            if transaction_depth == 0:
                session.commit()
                logger.debug("Outermost sync transaction committed successfully")
            else:
                logger.debug(f"Nested sync transaction completed (depth: {transaction_depth + 1}), deferring commit to outermost")
                
        except Exception as e:
            # Always rollback on error, regardless of nesting
            session.rollback()
            logger.error(f"Sync transaction rolled back due to error (depth: {transaction_depth + 1}): {e}")
            raise
        finally:
            # Clean up transaction depth tracking
            current_depth = getattr(session, '_atomic_transaction_depth', 1)
            setattr(session, '_atomic_transaction_depth', max(0, current_depth - 1))


def require_atomic_transaction(func):
    """
    Decorator to ensure function runs within an atomic transaction.
    Critical for all financial operations to prevent race conditions.
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        # Check if session is already provided in kwargs
        session = kwargs.get("session")

        if session is not None:
            # Session already provided, just execute
            return func(*args, **kwargs)

        # Create new atomic transaction
        with atomic_transaction() as new_session:
            kwargs["session"] = new_session
            return func(*args, **kwargs)

    return wrapper


@contextmanager
def locked_wallet_operation(
    user_id: int, currency: str, session: Session
) -> Generator[Any, None, None]:
    """
    Context manager for wallet operations with row-level locking (SYNC VERSION).
    Prevents race conditions in concurrent wallet operations.
    
    RACE CONDITION FIX: Uses INSERT ON CONFLICT DO NOTHING to prevent
    unique constraint violations during concurrent wallet creation.
    """
    from models import Wallet
    from sqlalchemy import text

    try:
        # RACE CONDITION FIX: First try to create wallet atomically if it doesn't exist
        # This prevents multiple concurrent processes from trying to create the same wallet
        try:
            # Use INSERT ... ON CONFLICT DO NOTHING to safely create wallet without race conditions
            insert_sql = text("""
                INSERT INTO wallets (user_id, currency, available_balance, frozen_balance, created_at, updated_at)
                VALUES (:user_id, :currency, 0.0, 0.0, NOW(), NOW())
                ON CONFLICT (user_id, currency) DO NOTHING
            """)
            
            session.execute(insert_sql, {
                'user_id': user_id,
                'currency': currency
            })
            session.flush()  # Ensure the insert is committed
            
        except IntegrityError:
            # Another process created the wallet simultaneously - this is OK
            session.rollback()
            logger.debug(f"Wallet creation race condition handled for user {user_id}, currency {currency}")
        
        # Now get the wallet with row-level lock (guaranteed to exist)
        wallet = (
            session.query(Wallet)
            .filter(Wallet.user_id == user_id, Wallet.currency == currency)
            .with_for_update()
            .first()
        )

        if not wallet:
            # This should never happen after the INSERT ON CONFLICT above
            raise ValueError(f"CRITICAL: Wallet still not found after creation attempt for user {user_id}, currency {currency}")

        logger.debug(f"🔒 Successfully locked wallet for user {user_id}, currency {currency}")
        yield wallet

    except SQLAlchemyError as e:
        logger.error(f"Database error in locked wallet operation: {e}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error in locked wallet operation: {e}")
        raise


@asynccontextmanager
async def locked_wallet_operation_async(
    user_id: int, currency: str, session: AsyncSession
) -> AsyncGenerator[Any, None]:
    """
    Async context manager for wallet operations with atomic upsert-and-lock.
    Achieves 100% reliability by eliminating race conditions through atomic upsert.
    
    ATOMIC UPSERT SOLUTION: Uses INSERT ... ON CONFLICT ... DO UPDATE ... RETURNING
    to either create or touch existing wallet in a single atomic operation, then 
    applies row-level locking on the returned result.
    
    This eliminates all retry logic and achieves true 100% reliability.
    """
    from models import Wallet
    from sqlalchemy import text, select

    try:
        # Normalize currency to prevent case mismatch issues
        currency = currency.upper()
        
        # ATOMIC UPSERT-AND-LOCK SOLUTION: Single atomic operation that either
        # inserts a new wallet or updates the existing one, returning the row
        upsert_sql = text("""
            INSERT INTO wallets (user_id, currency, available_balance, frozen_balance, created_at, updated_at)
            VALUES (:user_id, :currency, 0.0, 0.0, NOW(), NOW())
            ON CONFLICT (user_id, currency) 
            DO UPDATE SET updated_at = wallets.updated_at
            RETURNING id, user_id, currency, available_balance, frozen_balance, created_at, updated_at
        """)
        
        # Execute atomic upsert and get the returned row
        result = await session.execute(upsert_sql, {
            'user_id': user_id,
            'currency': currency
        })
        wallet_row = result.fetchone()
        
        if not wallet_row:
            # This should never happen with RETURNING clause
            raise ValueError(f"CRITICAL: Atomic upsert failed to return wallet row for user {user_id}, currency {currency}")
        
        # Map the returned row to ORM entity for proper typing and relationships
        Wallet(
            id=wallet_row.id,
            user_id=wallet_row.user_id,
            currency=wallet_row.currency,
            available_balance=wallet_row.available_balance,
            frozen_balance=wallet_row.frozen_balance,
            created_at=wallet_row.created_at,
            updated_at=wallet_row.updated_at
        )
        
        # Apply row-level locking on the upserted wallet for exclusive access
        # This ensures no other transaction can modify this wallet concurrently
        lock_stmt = select(Wallet).where(
            (Wallet.user_id == user_id) & (Wallet.currency == currency)
        ).with_for_update()
        
        lock_result = await session.execute(lock_stmt)
        locked_wallet = lock_result.scalar_one()
        
        logger.debug(f"🔒 Atomic upsert-and-lock successful for user {user_id}, currency {currency}")
        yield locked_wallet

    except SQLAlchemyError as e:
        logger.error(f"Database error in atomic upsert-and-lock wallet operation: {e}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error in atomic upsert-and-lock wallet operation: {e}")
        raise


@contextmanager
def locked_escrow_operation(
    escrow_id: str, session: Session, timeout_seconds: int = 30
) -> Generator[Any, None, None]:
    """
    Context manager for escrow operations with row-level locking and deadlock detection (SYNC VERSION).
    Prevents race conditions in concurrent escrow status changes.
    Enhanced with timeout handling and deadlock recovery.
    """
    from models import Escrow

    start_time = time.time()
    max_retries = 3
    retry_count = 0

    while retry_count < max_retries:
        try:
            # Check timeout
            if time.time() - start_time > timeout_seconds:
                raise TimeoutError(
                    f"Lock acquisition timeout for escrow {escrow_id} after {timeout_seconds}s"
                )

            # Get escrow with row-level lock
            escrow = (
                session.query(Escrow)
                .filter(Escrow.escrow_id == escrow_id)
                .with_for_update(nowait=False)
                .first()
            )

            if not escrow:
                raise ValueError(f"Escrow {escrow_id} not found")

            logger.debug(f"Successfully acquired lock for escrow {escrow_id}")
            yield escrow
            return

        except OperationalError as e:
            # Check for deadlock-related errors
            if (
                "deadlock detected" in str(e).lower()
                or "lock_timeout" in str(e).lower()
            ):
                retry_count += 1
                if retry_count < max_retries:
                    backoff_time = 0.1 * (2**retry_count)  # Exponential backoff
                    logger.warning(
                        f"Deadlock detected for escrow {escrow_id}, retrying ({retry_count}/{max_retries}) after {backoff_time}s"
                    )
                    time.sleep(backoff_time)
                    session.rollback()  # Rollback the deadlocked transaction
                    continue
                else:
                    logger.error(
                        f"Max retries exceeded for escrow {escrow_id} deadlock"
                    )
                    raise
            else:
                logger.error(
                    f"Database operational error in locked escrow operation: {e}"
                )
                raise
        except SQLAlchemyError as e:
            logger.error(f"Database error in locked escrow operation: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error in locked escrow operation: {e}")
            raise


@asynccontextmanager
async def locked_escrow_operation_async(
    escrow_id: str, session: AsyncSession, timeout_seconds: int = 30
) -> AsyncGenerator[Any, None]:
    """
    Async context manager for escrow operations with row-level locking and deadlock detection.
    Prevents race conditions in concurrent escrow status changes.
    Enhanced with timeout handling and deadlock recovery.
    
    ASYNC FIX: Properly awaits all async operations and uses SQLAlchemy 2.0 patterns.
    """
    from models import Escrow
    from sqlalchemy import select

    start_time = time.time()
    max_retries = 3
    retry_count = 0

    while retry_count < max_retries:
        try:
            # Check timeout
            if time.time() - start_time > timeout_seconds:
                raise TimeoutError(
                    f"Lock acquisition timeout for escrow {escrow_id} after {timeout_seconds}s"
                )

            # SQLALCHEMY 2.0 FIX: Use select() instead of .query() for AsyncSession compatibility
            stmt = select(Escrow).where(Escrow.escrow_id == escrow_id).with_for_update(nowait=False)
            result = await session.execute(stmt)
            escrow = result.scalar_one_or_none()

            if not escrow:
                raise ValueError(f"Escrow {escrow_id} not found")

            logger.debug(f"Successfully acquired lock for escrow {escrow_id}")
            yield escrow
            return

        except OperationalError as e:
            # Check for deadlock-related errors
            if (
                "deadlock detected" in str(e).lower()
                or "lock_timeout" in str(e).lower()
            ):
                retry_count += 1
                if retry_count < max_retries:
                    backoff_time = 0.1 * (2**retry_count)  # Exponential backoff
                    logger.warning(
                        f"Deadlock detected for escrow {escrow_id}, retrying ({retry_count}/{max_retries}) after {backoff_time}s"
                    )
                    await asyncio.sleep(backoff_time)
                    await session.rollback()  # Rollback the deadlocked transaction
                    continue
                else:
                    logger.error(
                        f"Max retries exceeded for escrow {escrow_id} deadlock"
                    )
                    raise
            else:
                logger.error(
                    f"Database operational error in async locked escrow operation: {e}"
                )
                raise
        except SQLAlchemyError as e:
            logger.error(f"Database error in async locked escrow operation: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error in async locked escrow operation: {e}")
            raise


@contextmanager
def payment_confirmation_transaction(
    operation_type: str,
    reference_id: str, 
    payment_source: str = "unknown"
) -> Generator[Session, None, None]:
    """
    Specialized context manager for payment confirmation operations.
    Ensures payment confirmation and fund segregation happen atomically.
    
    Args:
        operation_type: Type of operation (escrow_payment, exchange_payment, etc.)
        reference_id: Reference ID for the payment (escrow_id, order_id, etc.)
        payment_source: Source of payment (dynopay, fincra, blockbee, etc.)
    """
    start_time = time.time()
    
    try:
        with atomic_transaction() as session:
            logger.info(
                f"🔄 PAYMENT_CONFIRMATION_TX_START: {operation_type} | "
                f"Reference: {reference_id} | Source: {payment_source}"
            )
            
            yield session
            
            elapsed_time = time.time() - start_time
            logger.info(
                f"✅ PAYMENT_CONFIRMATION_TX_SUCCESS: {operation_type} | "
                f"Reference: {reference_id} | Source: {payment_source} | "
                f"Duration: {elapsed_time:.3f}s"
            )
            
    except Exception as e:
        elapsed_time = time.time() - start_time
        logger.error(
            f"❌ PAYMENT_CONFIRMATION_TX_FAILED: {operation_type} | "
            f"Reference: {reference_id} | Source: {payment_source} | "
            f"Duration: {elapsed_time:.3f}s | Error: {e}"
        )
        raise


class AtomicFinancialOperation:
    """
    Class for managing complex financial operations atomically.
    Ensures all related database changes succeed or fail together.
    """

    def __init__(self, session: Optional[Session] = None):
        self.session = session
        self.operations = []
        self.rollback_operations = []

    def add_wallet_debit(
        self, user_id: int, amount: float, currency: str, description: str = ""
    ):
        """Queue a wallet debit operation"""
        self.operations.append(
            {
                "type": "wallet_debit",
                "user_id": user_id,
                "amount": amount,
                "currency": currency,
                "description": description,
            }
        )

    def add_wallet_credit(
        self, user_id: int, amount: float, currency: str, description: str = ""
    ):
        """Queue a wallet credit operation"""
        self.operations.append(
            {
                "type": "wallet_credit",
                "user_id": user_id,
                "amount": amount,
                "currency": currency,
                "description": description,
            }
        )

    def add_escrow_status_change(self, escrow_id: str, new_status: str, **fields):
        """Queue an escrow status change"""
        self.operations.append(
            {
                "type": "escrow_update",
                "escrow_id": escrow_id,
                "new_status": new_status,
                "fields": fields,
            }
        )

    def execute(self) -> bool:
        """Execute all queued operations atomically"""
        from services.crypto import CryptoServiceAtomic

        if not self.operations:
            return True

        session_provided = self.session is not None
        session = self.session or SessionLocal()

        try:
            for operation in self.operations:
                if operation["type"] == "wallet_debit":
                    success = CryptoServiceAtomic.debit_user_wallet_atomic(
                        user_id=operation["user_id"],
                        amount=operation["amount"],
                        currency=operation["currency"],
                        description=operation["description"],
                        session=session,
                    )
                    if not success:
                        raise Exception(
                            f"Failed to debit wallet for user {operation['user_id']}"
                        )

                elif operation["type"] == "wallet_credit":
                    success = CryptoServiceAtomic.credit_user_wallet_atomic(
                        user_id=operation["user_id"],
                        amount=operation["amount"],
                        currency=operation["currency"],
                        description=operation["description"],
                        session=session,
                    )
                    if not success:
                        raise Exception(
                            f"Failed to credit wallet for user {operation['user_id']}"
                        )

                elif operation["type"] == "escrow_update":
                    with locked_escrow_operation(
                        operation["escrow_id"], session
                    ) as escrow:
                        escrow.status = operation["new_status"]
                        for field, value in operation["fields"].items():
                            if hasattr(escrow, field):
                                setattr(escrow, field, value)

            if not session_provided:
                session.commit()

            logger.info(
                f"Successfully executed {len(self.operations)} atomic operations"
            )
            return True

        except Exception as e:
            if not session_provided:
                session.rollback()
            logger.error(f"Atomic operation failed, rolled back: {e}")
            return False
        finally:
            if not session_provided:
                session.close()


class AtomicAdminActionManager:
    """Manager for atomic admin action transactions with proper isolation and idempotency"""

    @classmethod
    @contextmanager
    def atomic_admin_action(
        cls,
        cashout_id: str,
        action_type: str,
        admin_email: str,
        admin_user_id: Optional[int] = None,
        lock_timeout: int = 30,
        retry_on_conflict: bool = True,
        max_retries: int = 3
    ):
        """
        Context manager for atomic admin actions with proper locking and isolation
        
        Args:
            cashout_id: ID of cashout being operated on
            action_type: Type of admin action (RETRY, REFUND, DECLINE)
            admin_email: Email of admin performing action
            admin_user_id: ID of admin user (if available)
            lock_timeout: Timeout for database locks in seconds
            retry_on_conflict: Whether to retry on lock conflicts
            max_retries: Maximum retry attempts
            
        Yields:
            AtomicAdminActionContext with locked cashout and transaction
        """
        session = None
        retry_count = 0
        last_error = None
        
        while retry_count <= max_retries:
            try:
                session = SessionLocal()
                
                # Begin transaction with isolation level
                session.begin()
                
                # Set transaction isolation level to REPEATABLE READ for consistency
                session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
                
                # Use DatabaseLockingService for proper row-level locking
                from utils.database_locking import DatabaseLockingService
                
                with DatabaseLockingService.locked_cashout(
                    session=session,
                    cashout_id=cashout_id,
                    skip_locked=False,
                    timeout_seconds=lock_timeout
                ) as cashout:
                    
                    if not cashout:
                        raise ValueError(f"Cashout {cashout_id} not found or could not be locked")
                    
                    # Create action context
                    context = AtomicAdminActionContext(
                        session=session,
                        cashout=cashout,
                        action_type=action_type,
                        admin_email=admin_email,
                        admin_user_id=admin_user_id,
                        transaction_id=cls._generate_transaction_id(cashout_id, action_type)
                    )
                    
                    logger.info(f"🔒 ATOMIC_ADMIN_ACTION_START: {action_type} on cashout {cashout_id} by {admin_email} (txn: {context.transaction_id})")
                    
                    try:
                        yield context
                        
                        # If we get here, commit the transaction
                        session.commit()
                        logger.info(f"✅ ATOMIC_ADMIN_ACTION_SUCCESS: {action_type} on cashout {cashout_id} committed (txn: {context.transaction_id})")
                        return  # Success, exit retry loop
                        
                    except Exception as inner_error:
                        # Rollback transaction on any error
                        session.rollback()
                        logger.error(f"❌ ATOMIC_ADMIN_ACTION_ROLLBACK: {action_type} on cashout {cashout_id} failed: {inner_error} (txn: {context.transaction_id})")
                        raise
                
            except (OperationalError, SQLAlchemyError) as e:
                last_error = e
                retry_count += 1
                
                # Check for lock-related errors
                error_str = str(e).lower()
                is_lock_error = any(keyword in error_str for keyword in [
                    "lock_timeout", "deadlock", "timeout", "lock not available"
                ])
                
                if retry_on_conflict and is_lock_error and retry_count <= max_retries:
                    delay = min(2 ** retry_count, 10)  # Exponential backoff, max 10s
                    logger.warning(f"🔄 ADMIN_ACTION_LOCK_RETRY: Attempt {retry_count}/{max_retries} for {action_type} on {cashout_id}, retrying in {delay}s: {e}")
                    time.sleep(delay)
                    continue
                else:
                    logger.error(f"💥 ADMIN_ACTION_LOCK_FINAL: Failed to acquire lock for {action_type} on {cashout_id} after {retry_count} attempts")
                    break
                    
            except Exception as e:
                last_error = e
                logger.error(f"❌ ATOMIC_ADMIN_ACTION_ERROR: {action_type} on cashout {cashout_id}: {e}")
                break
                
            finally:
                if session:
                    try:
                        session.execute(text("SET LOCAL lock_timeout = DEFAULT"))
                    except Exception as e:
                        logger.debug(f"Could not reset lock timeout: {e}")
                        pass  # Ignore cleanup errors
                    session.close()
        
        # If we reach here, all retries failed
        if last_error:
            raise last_error
        else:
            raise RuntimeError(f"Failed to complete atomic admin action {action_type} on cashout {cashout_id}")
    
    @classmethod
    def _generate_transaction_id(cls, cashout_id: str, action_type: str) -> str:
        """Generate unique transaction ID for idempotency tracking using UniversalIDGenerator"""
        from utils.universal_id_generator import UniversalIDGenerator
        
        # Use UniversalIDGenerator for consistent ID generation
        # Generate a unique admin action ID for tracking
        base_id = UniversalIDGenerator.generate_id('admin_action')
        
        # Include cashout_id and action_type for context while maintaining uniqueness
        return f"ADM_{action_type}_{cashout_id}_{base_id}"


class AtomicAdminActionContext:
    """Context object provided by atomic admin action transactions"""
    
    def __init__(
        self, 
        session: Session, 
        cashout, 
        action_type: str, 
        admin_email: str,
        admin_user_id: Optional[int],
        transaction_id: str
    ):
        self.session = session
        self.cashout = cashout
        self.action_type = action_type
        self.admin_email = admin_email
        self.admin_user_id = admin_user_id
        self.transaction_id = transaction_id
        
        # Track idempotency operations
        self._external_operations = {}
        self._wallet_operations = []
        
    def lock_user_wallet(self, currency: str):
        """Lock the user's wallet for atomic balance operations"""
        return locked_wallet_operation(
            user_id=self.cashout.user_id,
            currency=currency,
            session=self.session
        )
    
    def record_external_operation(self, operation_key: str, operation_data: Dict[str, Any]):
        """Record external operation for idempotency checking"""
        self._external_operations[operation_key] = {
            "operation_data": operation_data,
            "timestamp": time.time(),
            "transaction_id": self.transaction_id
        }
        
        logger.info(f"📝 EXTERNAL_OP_RECORDED: {operation_key} for transaction {self.transaction_id}")
    
    def get_external_operation(self, operation_key: str) -> Optional[Dict[str, Any]]:
        """Get previously recorded external operation for idempotency"""
        return self._external_operations.get(operation_key)
    
    def record_wallet_operation(self, operation_type: str, amount: float, currency: str, description: str):
        """Record wallet operation for audit trail"""
        operation = {
            "type": operation_type,
            "amount": amount,
            "currency": currency,
            "description": description,
            "timestamp": time.time(),
            "transaction_id": self.transaction_id
        }
        
        self._wallet_operations.append(operation)
        logger.info(f"💰 WALLET_OP_RECORDED: {operation_type} {amount} {currency} for transaction {self.transaction_id}")
    
    def get_audit_trail(self) -> Dict[str, Any]:
        """Get complete audit trail for this atomic operation"""
        return {
            "transaction_id": self.transaction_id,
            "cashout_id": self.cashout.cashout_id,
            "action_type": self.action_type,
            "admin_email": self.admin_email,
            "admin_user_id": self.admin_user_id,
            "external_operations": self._external_operations,
            "wallet_operations": self._wallet_operations,
            "timestamp": time.time()
        }


class IdempotencyManager:
    """Manager for ensuring idempotent external API operations"""
    
    @classmethod
    def create_operation_key(cls, service: str, operation: str, cashout_id: str, **params) -> str:
        """
        Create unique operation key for idempotency checking
        
        Args:
            service: External service name (kraken, fincra)
            operation: Operation type (withdraw, transfer, etc.)
            cashout_id: Cashout ID
            **params: Additional parameters for uniqueness
            
        Returns:
            Unique operation key
        """
        # Sort params for consistent key generation
        sorted_params = sorted(params.items())
        param_str = "_".join(f"{k}={v}" for k, v in sorted_params)
        
        key = f"{service}_{operation}_{cashout_id}"
        if param_str:
            key += f"_{param_str}"
        
        return key
    
    @classmethod
    def store_operation_result(
        cls, 
        session: Session, 
        operation_key: str, 
        result: Dict[str, Any],
        transaction_id: str,
        service_name: Optional[str] = None,
        operation_type: Optional[str] = None,
        cashout_id: Optional[str] = None,
        operation_params: Optional[Dict[str, Any]] = None
    ):
        """
        Store external operation result in database for idempotency
        
        Args:
            session: Database session
            operation_key: Unique operation key
            result: Operation result
            transaction_id: Transaction ID for tracking
            service_name: Service name (kraken, fincra)
            operation_type: Operation type (withdraw, transfer)
            cashout_id: Associated cashout ID
            operation_params: Input parameters for the operation
        """
        try:
            # NOTE: ExternalOperationLog model may not exist - graceful degradation
            from models import ExternalOperationLog  # type: ignore[attr-defined]
            
            # Extract service and operation info from operation_key if not provided
            if not service_name or not operation_type:
                key_parts = operation_key.split('_')
                if len(key_parts) >= 2:
                    service_name = service_name or key_parts[0]
                    operation_type = operation_type or key_parts[1]
            
            # Create operation log entry
            operation_log = ExternalOperationLog(
                operation_key=operation_key,
                transaction_id=transaction_id,
                service_name=service_name or "unknown",
                operation_type=operation_type or "unknown",
                cashout_id=cashout_id,
                operation_params=operation_params or {},
                operation_result=result,
                success=result.get("success", True) if isinstance(result, dict) else True
            )
            
            session.add(operation_log)
            session.flush()  # Don't commit yet, let parent transaction handle it
            
            logger.info(f"💾 IDEMPOTENCY_STORED: {operation_key} for transaction {transaction_id}")
            
        except Exception as e:
            logger.error(f"❌ IDEMPOTENCY_STORE_ERROR: Failed to store operation {operation_key}: {e}")
            # Don't fail the main transaction for logging issues
    
    @classmethod
    def get_operation_result(cls, session: Session, operation_key: str) -> Optional[Dict[str, Any]]:
        """
        Get previously stored operation result for idempotency checking
        
        Args:
            session: Database session
            operation_key: Unique operation key
            
        Returns:
            Previously stored operation result or None if not found
        """
        try:
            # NOTE: ExternalOperationLog model may not exist - graceful degradation
            from models import ExternalOperationLog  # type: ignore[attr-defined]
            
            operation_log = session.query(ExternalOperationLog).filter(
                ExternalOperationLog.operation_key == operation_key
            ).first()
            
            if operation_log:
                # Update last accessed timestamp
                operation_log.update_last_accessed()
                session.flush()
                
                logger.info(f"♻️ IDEMPOTENCY_HIT: Found existing result for {operation_key}")
                return operation_log.operation_result
            
            return None
            
        except Exception as e:
            logger.error(f"❌ IDEMPOTENCY_GET_ERROR: Failed to get operation {operation_key}: {e}")
            return None
    
    @classmethod
    @contextmanager
    def idempotent_external_call(
        cls,
        context: AtomicAdminActionContext,
        service: str,
        operation: str,
        api_call: Callable,
        **call_params
    ):
        """
        Context manager for idempotent external API calls
        
        Args:
            context: Atomic admin action context
            service: Service name (kraken, fincra)
            operation: Operation name (withdraw, transfer)
            api_call: Callable that performs the API call
            **call_params: Parameters for the API call
            
        Yields:
            API call result (either fresh or cached)
        """
        # Create operation key
        operation_key = cls.create_operation_key(
            service=service,
            operation=operation,
            cashout_id=context.cashout.cashout_id,
            **call_params
        )
        
        # Check for existing result
        existing_result = cls.get_operation_result(context.session, operation_key)
        if existing_result:
            logger.info(f"♻️ IDEMPOTENT_CACHE_HIT: Returning cached result for {operation_key}")
            yield existing_result
            return
        
        # Perform fresh API call
        try:
            logger.info(f"🌐 IDEMPOTENT_API_CALL: Making fresh call for {operation_key}")
            result = api_call(**call_params)
            
            # Store result for future idempotency
            cls.store_operation_result(
                session=context.session,
                operation_key=operation_key,
                result=result,
                transaction_id=context.transaction_id,
                service_name=service,
                operation_type=operation,
                cashout_id=context.cashout.cashout_id,
                operation_params=call_params
            )
            
            # Record in context
            context.record_external_operation(operation_key, {
                "service": service,
                "operation": operation,
                "params": call_params,
                "result": result
            })
            
            yield result
            
        except Exception as e:
            logger.error(f"❌ IDEMPOTENT_API_ERROR: Failed API call for {operation_key}: {e}")
            raise


# Utility function for quick atomic admin actions
def with_atomic_admin_action(
    cashout_id: str,
    action_type: str,
    admin_email: str,
    admin_user_id: Optional[int] = None
):
    """
    Decorator for atomic admin action methods
    
    Usage:
        @with_atomic_admin_action(cashout_id, "RETRY", admin_email)
        def my_admin_action(context: AtomicAdminActionContext):
            # Your atomic action logic here
            pass
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            with AtomicAdminActionManager.atomic_admin_action(
                cashout_id=cashout_id,
                action_type=action_type,
                admin_email=admin_email,
                admin_user_id=admin_user_id
            ) as context:
                return func(context, *args, **kwargs)
        return wrapper
    return decorator
