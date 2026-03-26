"""
Centralized Session Management for Database Operations
Provides safe, async-ready session handling patterns
"""

import logging
from contextlib import asynccontextmanager, contextmanager
from typing import Generator, Optional, Any, Dict
from sqlalchemy.orm import Session
from database import SessionLocal
from utils.atomic_transactions import atomic_transaction

logger = logging.getLogger(__name__)


class SessionManager:
    """Centralized session management with enhanced safety patterns"""
    
    @staticmethod
    @contextmanager
    def get_session() -> Generator[Session, None, None]:
        """
        Safe session context manager with automatic cleanup
        Use this for all database operations to prevent detached objects
        """
        session = SessionLocal()
        try:
            yield session
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"Session error, rolled back: {e}")
            raise
        finally:
            session.close()
    
    @staticmethod
    @contextmanager
    def get_locked_session(timeout_seconds: int = 30) -> Generator[Session, None, None]:
        """
        Session with row-level locking support for critical operations
        """
        session = SessionLocal()
        try:
            # Configure session for locking operations with dialect safety
            from sqlalchemy import text
            try:
                # Only set timeout for PostgreSQL, other dialects may not support this
                if session.bind.dialect.name == 'postgresql':
                    session.execute(text("SET statement_timeout = :timeout"), {"timeout": timeout_seconds * 1000})
                elif session.bind.dialect.name == 'mysql':
                    # MySQL equivalent (if needed)
                    session.execute(text("SET SESSION innodb_lock_wait_timeout = :timeout"), {"timeout": timeout_seconds})
                # SQLite doesn't support query timeouts at session level
            except Exception as e:
                logger.warning(f"Could not set session timeout for dialect {session.bind.dialect.name}: {e}")
            yield session
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"Locked session error, rolled back: {e}")
            raise
        finally:
            session.close()
    
    @staticmethod
    def refresh_object_in_session(session: Session, obj: Any) -> Any:
        """
        Safely refresh or re-query an object to avoid detached instance errors
        Returns fresh object attached to the session
        """
        try:
            if hasattr(obj, 'id') and obj.id:
                # Re-query the object in the current session
                fresh_obj = session.query(obj.__class__).filter(
                    obj.__class__.id == obj.id
                ).first()
                return fresh_obj
            return obj
        except Exception as e:
            logger.warning(f"Could not refresh object {obj}: {e}")
            return obj
    
    @staticmethod
    def make_transient(obj: Any) -> Dict[str, Any]:
        """
        Convert SQLAlchemy object to dict to pass between sessions safely
        Prevents detached instance errors
        """
        if not obj:
            return {}
        
        try:
            # Extract all non-private attributes
            obj_dict = {}
            for key, value in obj.__dict__.items():
                if not key.startswith('_'):
                    obj_dict[key] = value
            return obj_dict
        except Exception as e:
            logger.warning(f"Could not make object transient {obj}: {e}")
            return {}
    
    @staticmethod
    @contextmanager
    def atomic_operation(session: Optional[Session] = None) -> Generator[Session, None, None]:
        """
        Enhanced atomic operation context manager
        Ensures financial operations are fully atomic and consistent
        """
        with atomic_transaction(session) as atomic_session:
            yield atomic_session


class AsyncSessionManager:
    """
    Async-ready session patterns for future migration
    Currently provides sync session handling with async compatibility
    """
    
    @staticmethod
    @asynccontextmanager
    async def async_session():
        """
        Future-ready async session manager
        Currently wraps sync session for compatibility
        """
        with SessionManager.get_session() as session:
            yield session
    
    @staticmethod
    async def execute_async_safe(operation, *args, **kwargs):
        """
        Execute database operation with async safety
        Prevents blocking in async context
        """
        try:
            with SessionManager.get_session() as session:
                return operation(session, *args, **kwargs)
        except Exception as e:
            logger.error(f"Async-safe operation failed: {e}")
            raise


# Convenience functions for common patterns
def with_session(func):
    """
    Decorator to automatically provide session to functions
    Usage: @with_session
           def my_function(session, ...):
    """
    def wrapper(*args, **kwargs):
        with SessionManager.get_session() as session:
            return func(session, *args, **kwargs)
    return wrapper


def with_locked_session(timeout_seconds: int = 30):
    """
    Decorator for operations requiring row-level locking
    Usage: @with_locked_session(timeout_seconds=60)
           def my_function(session, ...):
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            with SessionManager.get_locked_session(timeout_seconds) as session:
                return func(session, *args, **kwargs)
        return wrapper
    return decorator


# Migration utilities
class SessionMigrationHelper:
    """Helper for migrating existing code to use centralized session management"""
    
    @staticmethod
    def fix_detached_object(obj: Any, session: Session) -> Any:
        """
        Fix detached object by re-querying in current session
        """
        return SessionManager.refresh_object_in_session(session, obj)
    
    @staticmethod
    def safe_query_by_id(model_class, object_id: int, session: Session):
        """
        Safe way to query object by ID with session management
        """
        try:
            return session.query(model_class).filter(
                model_class.id == object_id
            ).first()
        except Exception as e:
            logger.error(f"Error querying {model_class} with ID {object_id}: {e}")
            return None