"""
Simplified Financial Operation Locker
Clean, simple locking using only standard database SELECT FOR UPDATE
Replaces the over-engineered financial_operation_locker.py
"""

import logging
from contextlib import contextmanager

from database import SessionLocal
from models import Wallet, Escrow, Cashout

logger = logging.getLogger(__name__)


class SimpleFinancialLocker:
    """
    Simplified financial locker using only database SELECT FOR UPDATE
    No complex distributed locking or optimistic locking - just standard database locks
    """
    
    @contextmanager
    def lock_wallet_operation(self, user_id: int, currency: str = "USD"):
        """
        Lock a wallet for financial operations using SELECT FOR UPDATE
        FIXED: Start transaction before locking for proper FOR UPDATE behavior
        """
        session = SessionLocal()
        try:
            # FIXED: Start transaction first, then lock
            session.begin()
            
            # Lock the wallet row for this operation
            wallet = (
                session.query(Wallet)
                .filter(Wallet.user_id == user_id, Wallet.currency == currency)
                .with_for_update()  # This requires an active transaction
                .first()
            )
            
            if not wallet:
                raise ValueError(f"No {currency} wallet found for user {user_id}")
            
            logger.debug(f"WALLET_LOCKED: User {user_id} {currency} wallet")
            
            yield session, wallet
            
            # Commit if no exceptions
            session.commit()
            logger.debug(f"WALLET_OPERATION_COMMITTED: User {user_id}")
            
        except Exception as e:
            # Rollback on any error
            session.rollback()
            logger.error(f"WALLET_OPERATION_FAILED: User {user_id} - {e}")
            raise
        finally:
            session.close()

    @contextmanager
    def lock_escrow_operation(self, escrow_id: str):
        """
        Lock an escrow for status transitions using SELECT FOR UPDATE
        FIXED: Start transaction before locking for proper FOR UPDATE behavior
        """
        session = SessionLocal()
        try:
            # FIXED: Start transaction first, then lock
            session.begin()
            
            # Lock the escrow row for this operation
            escrow = (
                session.query(Escrow)
                .filter(Escrow.escrow_id == escrow_id)
                .with_for_update()  # This requires an active transaction
                .first()
            )
            
            if not escrow:
                raise ValueError(f"Escrow {escrow_id} not found")
            
            logger.debug(f"ESCROW_LOCKED: {escrow_id}")
            
            yield session, escrow
            
            # Commit if no exceptions
            session.commit()
            logger.debug(f"ESCROW_OPERATION_COMMITTED: {escrow_id}")
            
        except Exception as e:
            # Rollback on any error
            session.rollback()
            logger.error(f"ESCROW_OPERATION_FAILED: {escrow_id} - {e}")
            raise
        finally:
            session.close()

    @contextmanager
    def lock_cashout_operation(self, cashout_id: str):
        """
        Lock a cashout for processing using SELECT FOR UPDATE
        FIXED: Start transaction before locking for proper FOR UPDATE behavior
        """
        session = SessionLocal()
        try:
            # FIXED: Start transaction first, then lock
            session.begin()
            
            # Lock the cashout row for this operation
            cashout = (
                session.query(Cashout)
                .filter(Cashout.cashout_id == cashout_id)
                .with_for_update()  # This requires an active transaction
                .first()
            )
            
            if not cashout:
                raise ValueError(f"Cashout {cashout_id} not found")
            
            logger.debug(f"CASHOUT_LOCKED: {cashout_id}")
            
            yield session, cashout
            
            # Commit if no exceptions
            session.commit()
            logger.debug(f"CASHOUT_OPERATION_COMMITTED: {cashout_id}")
            
        except Exception as e:
            # Rollback on any error
            session.rollback()
            logger.error(f"CASHOUT_OPERATION_FAILED: {cashout_id} - {e}")
            raise
        finally:
            session.close()


# Global instance for easy import
simple_locker = SimpleFinancialLocker()

# Backward compatibility alias for existing imports
financial_locker = simple_locker

# For enum compatibility (if needed)
class FinancialLockType:
    """Backward compatibility - this enum is no longer needed with simplified locking"""
    WALLET = "wallet"
    ESCROW = "escrow" 
    CASHOUT = "cashout"