"""
Async database utilities to prevent blocking the event loop
Provides thread-safe database operations for async handlers
"""

import asyncio
import logging
from functools import wraps
from typing import Any, Callable, TypeVar
from contextlib import asynccontextmanager
from database import SessionLocal

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


def run_in_thread(func: F) -> F:
    """Decorator to run synchronous database operations in a thread pool"""

    @wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            # Run the synchronous operation in a thread pool
            return await asyncio.to_thread(func, *args, **kwargs)
        except Exception as e:
            logger.error(f"Error in threaded database operation {func.__name__}: {e}")
            raise

    return wrapper


@asynccontextmanager
async def async_session():
    """Async context manager for database sessions using thread pool"""

    def _get_session():
        return SessionLocal()

    def _close_session(session):
        try:
            session.close()
        except Exception as e:
            logger.error(f"Error closing database session: {e}")

    # Get session in thread pool
    session = await asyncio.to_thread(_get_session)
    try:
        yield session
    finally:
        # Close session in thread pool
        await asyncio.to_thread(_close_session, session)




async def async_query(session, query_func):
    """Execute a query function in a thread pool"""
    return await asyncio.to_thread(query_func)


async def async_commit(session):
    """Commit session in thread pool"""
    return await asyncio.to_thread(session.commit)


async def async_add(session, obj):
    """Add object to session in thread pool"""
    return await asyncio.to_thread(session.add, obj)


# Specific database operation helpers
async def get_user_by_telegram_id(telegram_id: str):
    """Get user by telegram ID asynchronously with production caching"""
    from models import User
    from utils.production_cache import UserCacheOptimized
    from utils.normalizers import normalize_telegram_id
    
    # CRITICAL FIX: Convert telegram_id string to int to match database column type (bigint)
    normalized_id = normalize_telegram_id(telegram_id)
    if normalized_id is None:
        logger.warning(f"Invalid telegram_id provided: {telegram_id}")
        return None
    
    # Try production cache first for maximum performance
    cached_user_data = UserCacheOptimized.get_cached_user(str(normalized_id))
    if cached_user_data:
        # Convert dict to object-like structure for attribute access
        class CachedUser:
            def __init__(self, data):
                self.__dict__.update(data)
        
        logger.debug(f"Cache HIT for user {normalized_id}")
        return CachedUser(cached_user_data)

    def _query_user(session):
        # Optimized query with eager loading - use normalized int ID
        return (
            session.query(User)
            .filter(User.telegram_id == normalized_id)
            .first()
        )

    async with async_session() as session:
        user = await async_query(session, lambda: _query_user(session))
        if user:
            # Cache the user with production caching system
            UserCacheOptimized.cache_user_data(user, ttl=600)  # 10 minutes
            logger.debug(f"Cache MISS for user {normalized_id} - cached for future requests")
        return user


async def get_user_saved_addresses(user_id: int):
    """Get user's saved addresses asynchronously"""
    from models import SavedAddress

    def _query_addresses(session):
        return session.query(SavedAddress).filter_by(user_id=user_id).all()

    async with async_session() as session:
        return await async_query(session, lambda: _query_addresses(session))


async def get_user_saved_banks(user_id: int):
    """Get user's saved bank accounts asynchronously"""
    from models import SavedBankAccount

    def _query_banks(session):
        return session.query(SavedBankAccount).filter_by(user_id=user_id).all()

    async with async_session() as session:
        return await async_query(session, lambda: _query_banks(session))
