"""
Database Row-Level Locking Utilities
Provides safe row-level locking with SELECT FOR UPDATE and SKIP LOCKED for concurrent operations
"""

import logging
from typing import Optional, Any, Dict
from contextlib import contextmanager
from sqlalchemy.orm import Session
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from models import Cashout, Wallet, WalletHolds

logger = logging.getLogger(__name__)


class DatabaseLockingService:
    """Service for managing database row-level locks with proper timeout handling"""

    # Lock timeout in seconds (PostgreSQL default: 0 = wait indefinitely)
    DEFAULT_LOCK_TIMEOUT = 30  # 30 seconds max wait for locks
    
    @classmethod
    @contextmanager
    def locked_cashout(
        cls, 
        session: Session, 
        cashout_id: str, 
        skip_locked: bool = False,
        timeout_seconds: Optional[int] = None
    ):
        """
        Context manager for locking a cashout row with SELECT FOR UPDATE
        
        Args:
            session: Database session
            cashout_id: ID of cashout to lock
            skip_locked: If True, uses SKIP LOCKED to avoid blocking
            timeout_seconds: Lock timeout (default: 30 seconds)
            
        Returns:
            Locked cashout object or None if skip_locked=True and row is locked
            
        Raises:
            CashoutLockError: If cashout not found or lock fails
            CashoutLockTimeoutError: If lock timeout exceeded
        """
        timeout = timeout_seconds or cls.DEFAULT_LOCK_TIMEOUT
        
        try:
            # Set lock timeout for this transaction
            session.execute(text(f"SET LOCAL lock_timeout = '{timeout}s'"))
            
            # Build lock query
            lock_clause = "FOR UPDATE"
            if skip_locked:
                lock_clause += " SKIP LOCKED"
            
            # Execute locking query
            query = text(f"""
                SELECT * FROM cashouts 
                WHERE cashout_id = :cashout_id 
                {lock_clause}
            """)
            
            result = session.execute(query, {"cashout_id": cashout_id}).fetchone()
            
            if not result and not skip_locked:
                raise CashoutLockError(f"Cashout {cashout_id} not found")
            elif not result and skip_locked:
                logger.info(f"🔒 SKIP_LOCKED: Cashout {cashout_id} is locked by another process")
                yield None
                return
            
            # Get the full cashout object
            cashout = session.query(Cashout).filter(Cashout.cashout_id == cashout_id).first()
            if not cashout:
                raise CashoutLockError(f"Cashout {cashout_id} not found after lock")
            
            logger.info(f"🔒 LOCKED: Cashout {cashout_id} locked for update (timeout: {timeout}s)")
            
            yield cashout
            
        except OperationalError as e:
            if "lock_timeout" in str(e).lower() or "timeout" in str(e).lower():
                logger.error(f"🕐 LOCK_TIMEOUT: Failed to lock cashout {cashout_id} within {timeout}s")
                raise CashoutLockTimeoutError(f"Lock timeout for cashout {cashout_id}")
            else:
                logger.error(f"❌ LOCK_ERROR: Database error locking cashout {cashout_id}: {e}")
                raise CashoutLockError(f"Database error: {e}")
        except Exception as e:
            logger.error(f"❌ LOCK_UNEXPECTED: Unexpected error locking cashout {cashout_id}: {e}")
            raise CashoutLockError(f"Unexpected error: {e}")
        finally:
            # Reset lock timeout
            try:
                session.execute(text("SET LOCAL lock_timeout = DEFAULT"))
            except Exception as e:
                logger.debug(f"Could not reset lock timeout: {e}")
                pass  # Ignore cleanup errors
    
    @classmethod
    @contextmanager
    def locked_user_wallet(
        cls,
        session: Session,
        user_id: int,
        currency: str,
        skip_locked: bool = False,
        timeout_seconds: Optional[int] = None
    ):
        """
        Context manager for locking a user's wallet for atomic balance operations
        
        Args:
            session: Database session
            user_id: User ID
            currency: Currency code (USD, BTC, etc.)
            skip_locked: If True, uses SKIP LOCKED to avoid blocking
            timeout_seconds: Lock timeout (default: 30 seconds)
            
        Returns:
            Locked wallet object or None if skip_locked=True and row is locked
        """
        timeout = timeout_seconds or cls.DEFAULT_LOCK_TIMEOUT
        
        try:
            # Set lock timeout
            session.execute(text(f"SET LOCAL lock_timeout = '{timeout}s'"))
            
            # Build lock query
            lock_clause = "FOR UPDATE"
            if skip_locked:
                lock_clause += " SKIP LOCKED"
            
            # Lock wallet row
            query = text(f"""
                SELECT * FROM wallets 
                WHERE user_id = :user_id AND currency = :currency 
                {lock_clause}
            """)
            
            result = session.execute(query, {
                "user_id": user_id, 
                "currency": currency
            }).fetchone()
            
            if not result and not skip_locked:
                raise WalletLockError(f"Wallet not found: user {user_id}, currency {currency}")
            elif not result and skip_locked:
                logger.info(f"🔒 SKIP_LOCKED: Wallet locked for user {user_id}, currency {currency}")
                yield None
                return
            
            # Get the full wallet object
            wallet = session.query(Wallet).filter(
                Wallet.user_id == user_id,
                Wallet.currency == currency
            ).first()
            
            if not wallet:
                raise WalletLockError(f"Wallet not found after lock: user {user_id}, currency {currency}")
            
            logger.info(f"🔒 WALLET_LOCKED: User {user_id} {currency} wallet locked (timeout: {timeout}s)")
            
            yield wallet
            
        except OperationalError as e:
            if "lock_timeout" in str(e).lower():
                logger.error(f"🕐 WALLET_LOCK_TIMEOUT: Failed to lock wallet for user {user_id}, currency {currency}")
                raise WalletLockTimeoutError(f"Wallet lock timeout: user {user_id}, currency {currency}")
            else:
                logger.error(f"❌ WALLET_LOCK_ERROR: Database error locking wallet: {e}")
                raise WalletLockError(f"Database error: {e}")
        except Exception as e:
            logger.error(f"❌ WALLET_LOCK_UNEXPECTED: Unexpected error locking wallet: {e}")
            raise WalletLockError(f"Unexpected error: {e}")
        finally:
            # Reset lock timeout
            try:
                session.execute(text("SET LOCAL lock_timeout = DEFAULT"))
            except Exception as e:
                logger.debug(f"Could not reset lock timeout: {e}")
                pass
    
    @classmethod
    @contextmanager
    def locked_wallet_holds(
        cls,
        session: Session,
        hold_id: Optional[str] = None,
        user_id: Optional[int] = None,
        transaction_type: Optional[str] = None,
        skip_locked: bool = False,
        timeout_seconds: Optional[int] = None
    ):
        """
        Context manager for locking wallet holds for atomic frozen balance operations
        
        Args:
            session: Database session
            hold_id: Specific hold ID to lock (if provided)
            user_id: User ID to lock all holds for (if hold_id not provided)
            transaction_type: Type of transaction to lock holds for
            skip_locked: If True, uses SKIP LOCKED to avoid blocking
            timeout_seconds: Lock timeout (default: 30 seconds)
            
        Returns:
            List of locked WalletHolds objects or empty list if skip_locked=True and locked
        """
        timeout = timeout_seconds or cls.DEFAULT_LOCK_TIMEOUT
        
        try:
            # Set lock timeout
            session.execute(text(f"SET LOCAL lock_timeout = '{timeout}s'"))
            
            # Build lock query based on parameters
            lock_clause = "FOR UPDATE"
            if skip_locked:
                lock_clause += " SKIP LOCKED"
            
            if hold_id:
                # Lock specific hold
                query = text(f"""
                    SELECT * FROM wallet_holds 
                    WHERE hold_id = :hold_id 
                    {lock_clause}
                """)
                params = {"hold_id": hold_id}
            elif user_id:
                # Lock all holds for user
                base_query = "SELECT * FROM wallet_holds WHERE user_id = :user_id"
                params = {"user_id": user_id}
                
                if transaction_type:
                    base_query += " AND transaction_type = :transaction_type"
                    params["transaction_type"] = transaction_type
                
                query = text(f"{base_query} {lock_clause}")
            else:
                raise ValueError("Either hold_id or user_id must be provided")
            
            results = session.execute(query, params).fetchall()
            
            if not results and not skip_locked:
                if hold_id:
                    raise WalletHoldLockError(f"Wallet hold {hold_id} not found")
                else:
                    logger.info(f"No wallet holds found for user {user_id}, transaction_type {transaction_type}")
                    yield []
                    return
            elif not results and skip_locked:
                logger.info("🔒 SKIP_LOCKED: Wallet holds locked by another process")
                yield []
                return
            
            # Get the full hold objects
            if hold_id:
                holds = session.query(WalletHolds).filter(WalletHolds.hold_id == hold_id).all()
            else:
                query_filter = WalletHolds.user_id == user_id
                if transaction_type:
                    query_filter = query_filter & (WalletHolds.transaction_type == transaction_type)
                holds = session.query(WalletHolds).filter(query_filter).all()
            
            logger.info(f"🔒 HOLDS_LOCKED: {len(holds)} wallet holds locked (timeout: {timeout}s)")
            
            yield holds
            
        except OperationalError as e:
            if "lock_timeout" in str(e).lower():
                logger.error("🕐 HOLDS_LOCK_TIMEOUT: Failed to lock wallet holds")
                raise WalletHoldLockTimeoutError("Wallet holds lock timeout")
            else:
                logger.error(f"❌ HOLDS_LOCK_ERROR: Database error locking wallet holds: {e}")
                raise WalletHoldLockError(f"Database error: {e}")
        except Exception as e:
            logger.error(f"❌ HOLDS_LOCK_UNEXPECTED: Unexpected error locking wallet holds: {e}")
            raise WalletHoldLockError(f"Unexpected error: {e}")
        finally:
            # Reset lock timeout
            try:
                session.execute(text("SET LOCAL lock_timeout = DEFAULT"))
            except Exception as e:
                logger.debug(f"Could not reset lock timeout: {e}")
                pass
    
    @classmethod
    def check_lock_conflicts(cls, session: Session, cashout_id: str) -> Dict[str, Any]:
        """
        Check for potential lock conflicts on a cashout and related resources
        
        Returns information about current locks without acquiring them
        """
        try:
            # Query current lock state (pg_locks view)
            query = text("""
                SELECT 
                    l.mode,
                    l.granted,
                    l.pid,
                    c.relname as table_name
                FROM pg_locks l
                LEFT JOIN pg_class c ON l.relation = c.oid
                WHERE l.locktype = 'relation'
                AND c.relname IN ('cashouts', 'wallets', 'wallet_holds')
                AND l.granted = true
            """)
            
            locks = session.execute(query).fetchall()
            
            return {
                "has_conflicts": len(locks) > 0,
                "active_locks": [
                    {
                        "table": row.table_name,
                        "mode": row.mode,
                        "pid": row.pid,
                        "granted": row.granted
                    }
                    for row in locks
                ],
                "lock_count": len(locks)
            }
            
        except Exception as e:
            logger.error(f"Error checking lock conflicts: {e}")
            return {
                "has_conflicts": False,
                "error": str(e),
                "lock_count": 0
            }


# Custom exceptions for lock operations
class DatabaseLockError(Exception):
    """Base exception for database locking errors"""
    pass

class CashoutLockError(DatabaseLockError):
    """Exception raised when cashout locking fails"""
    pass

class CashoutLockTimeoutError(CashoutLockError):
    """Exception raised when cashout lock times out"""
    pass

class WalletLockError(DatabaseLockError):
    """Exception raised when wallet locking fails"""
    pass

class WalletLockTimeoutError(WalletLockError):
    """Exception raised when wallet lock times out"""
    pass

class WalletHoldLockError(DatabaseLockError):
    """Exception raised when wallet hold locking fails"""
    pass

class WalletHoldLockTimeoutError(WalletHoldLockError):
    """Exception raised when wallet hold lock times out"""
    pass