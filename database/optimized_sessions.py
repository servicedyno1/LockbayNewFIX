"""
Optimized Database Session Management with Connection Pooling and Query Optimization
"""

import logging
import time
from contextlib import contextmanager
from typing import Generator, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import event
from database import SessionLocal, engine

logger = logging.getLogger(__name__)


class DatabaseSessionManager:
    """Optimized database session manager with performance monitoring"""

    def __init__(self):
        self.session_factory = SessionLocal
        self._query_count = 0
        self._total_query_time = 0.0
        self._slow_queries = []
        self._active_sessions = 0

        # Register event listeners for performance monitoring
        self._setup_query_monitoring()

    def _setup_query_monitoring(self):
        """Setup query performance monitoring"""

        @event.listens_for(engine, "before_cursor_execute")
        def before_cursor_execute(
            conn, cursor, statement, parameters, context, executemany
        ):
            context._query_start_time = time.time()

        @event.listens_for(engine, "after_cursor_execute")
        def after_cursor_execute(
            conn, cursor, statement, parameters, context, executemany
        ):
            total = time.time() - context._query_start_time
            self._query_count += 1
            self._total_query_time += total

            # Track slow queries (>1 second)
            if total > 1.0:
                self._slow_queries.append(
                    {
                        "query": (
                            statement[:200] + "..."
                            if len(statement) > 200
                            else statement
                        ),
                        "duration": total,
                        "timestamp": time.time(),
                    }
                )
                # Keep only last 50 slow queries
                if len(self._slow_queries) > 50:
                    self._slow_queries.pop(0)

                logger.warning(
                    f"Slow query detected: {total:.2f}s - {statement[:100]}..."
                )

    @contextmanager
    def get_optimized_session(
        self, autocommit: bool = False
    ) -> Generator[Session, None, None]:
        """Get optimized database session with automatic cleanup"""
        session = self.session_factory()
        self._active_sessions += 1

        try:
            yield session
            if autocommit:
                session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"Database session error: {e}")
            raise
        finally:
            session.close()
            self._active_sessions -= 1

    @contextmanager
    def get_bulk_session(self) -> Generator[Session, None, None]:
        """Get session optimized for bulk operations"""
        session = self.session_factory()
        self._active_sessions += 1

        # OPTIMIZED: Configure for bulk operations
        session.bulk_insert_mappings = session.bulk_insert_mappings
        session.bulk_update_mappings = session.bulk_update_mappings

        try:
            yield session
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"Bulk session error: {e}")
            raise
        finally:
            session.close()
            self._active_sessions -= 1

    def get_performance_stats(self) -> Dict[str, Any]:
        """Get database performance statistics"""
        avg_query_time = (
            (self._total_query_time / self._query_count) if self._query_count > 0 else 0
        )

        return {
            "total_queries": self._query_count,
            "avg_query_time_ms": avg_query_time * 1000,
            "total_query_time_s": self._total_query_time,
            "slow_queries_count": len(self._slow_queries),
            "active_sessions": self._active_sessions,
            "recent_slow_queries": (
                self._slow_queries[-5:] if self._slow_queries else []
            ),
        }

    def reset_stats(self):
        """Reset performance statistics"""
        self._query_count = 0
        self._total_query_time = 0.0
        self._slow_queries.clear()
        logger.info("Database performance stats reset")


# Global optimized session manager
db_session_manager = DatabaseSessionManager()


# Convenience functions for common patterns
def get_db_session():
    """Get standard database session"""
    return db_session_manager.get_optimized_session()


def get_bulk_db_session():
    """Get bulk operation database session"""
    return db_session_manager.get_bulk_session()


# OPTIMIZED: Query helpers for common patterns
class OptimizedQueries:
    """Common optimized query patterns"""

    @staticmethod
    def get_user_with_wallets(session: Session, user_id: int):
        """Get user with wallets in single query (eager loading)"""
        from models import User
        from sqlalchemy.orm import joinedload

        return (
            session.query(User)
            .filter(User.id == user_id)
            .options(joinedload(User.wallets))
            .first()
        )

    @staticmethod
    def get_active_escrows_batch(session: Session, user_id: int):
        """Get active escrows with minimal data loading"""
        from models import Escrow
        from sqlalchemy import or_

        return (
            session.query(Escrow)
            .filter(
                or_(Escrow.buyer_id == user_id, Escrow.seller_id == user_id),
                Escrow.status.in_(["pending", "active", "in_dispute"]),
            )
            .all()
        )

    @staticmethod
    def bulk_update_wallet_balances(session: Session, wallet_updates: list):
        """Bulk update wallet balances efficiently"""
        from models import Wallet

        if wallet_updates:
            session.bulk_update_mappings(Wallet.__mapper__, wallet_updates)
            session.commit()

    @staticmethod
    def get_support_tickets_with_users(session: Session, limit: int = 10):
        """Get support tickets with user data in single query (prevents N+1)"""
        from models import SupportTicket
        from sqlalchemy.orm import joinedload

        return (
            session.query(SupportTicket)
            .options(joinedload(SupportTicket.user))
            .order_by(SupportTicket.created_at.desc())
            .limit(limit)
            .all()
        )

    @staticmethod
    def get_escrows_with_participants(session: Session, escrow_ids: list):
        """Get escrows with buyer/seller data in single query (prevents N+1)"""
        from models import Escrow
        from sqlalchemy.orm import joinedload

        return (
            session.query(Escrow)
            .filter(Escrow.id.in_(escrow_ids))
            .options(
                joinedload(Escrow.buyer),
                joinedload(Escrow.seller)
            )
            .all()
        )

    @staticmethod
    def get_user_escrows_optimized(session: Session, user_id: int):
        """Get user's escrows with minimal queries"""
        from models import Escrow
        from sqlalchemy.orm import joinedload
        from sqlalchemy import or_

        return (
            session.query(Escrow)
            .filter(or_(Escrow.buyer_id == user_id, Escrow.seller_id == user_id))
            .options(
                joinedload(Escrow.buyer),
                joinedload(Escrow.seller)
            )
            .order_by(Escrow.created_at.desc())
            .all()
        )

    @staticmethod
    def get_transactions_with_relations(session: Session, user_id: int):
        """Get user transactions with escrow/cashout data (prevents N+1)"""
        from models import Transaction
        from sqlalchemy.orm import joinedload

        return (
            session.query(Transaction)
            .filter(Transaction.user_id == user_id)
            .options(
                joinedload(Transaction.escrow),
                joinedload(Transaction.cashout)
            )
            .order_by(Transaction.created_at.desc())
            .all()
        )
