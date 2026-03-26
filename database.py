"""
Database Configuration and Session Management
============================================

This module provides the main database engine, session factory, and table creation
functionality for the LockBay Telegram Escrow Bot.
"""

import logging
import asyncio
from contextlib import contextmanager, asynccontextmanager
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import QueuePool
from sqlalchemy.exc import OperationalError
from config import Config
from models import Base

logger = logging.getLogger(__name__)

# Register User timestamp validator immediately after importing models
# This ensures timezone-aware datetimes are automatically converted to naive
try:
    from utils.user_timestamp_validator import register_user_timestamp_validator
    register_user_timestamp_validator()
    logger.info("✅ USER_TIMESTAMP_VALIDATOR: Registered at module import - timezone safety enabled globally")
except Exception as validator_error:
    logger.warning(f"⚠️ USER_TIMESTAMP_VALIDATOR: Registration failed: {validator_error}")
    # Non-critical - continue initialization

# Database engine with connection pooling
if not Config.DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is required")

# FIX: Increased connection pools to prevent exhaustion under background job load
# Sync pool: 10 base + 15 overflow = 25 max
# Async pool: 12 base + 20 overflow = 32 max
# Total: 57 connections max (safely under 100 limit)
engine = create_engine(
    Config.DATABASE_URL,
    poolclass=QueuePool,
    pool_size=10,          # Increased from 3 - background jobs need more connections
    max_overflow=15,       # Increased from 5 - burst capacity for concurrent operations
    pool_pre_ping=True,    # Validate connections before use
    pool_recycle=600,      # Recycle every 10 min (was 30 min) - Railway proxy compatibility
    pool_timeout=10,       # Reduced from 30s - fail fast to prevent blocking user handlers
    pool_reset_on_return='rollback',  # Ensure clean state when returned to pool
    echo=False,            # Set to True for SQL logging in development
    connect_args={
        "connect_timeout": 10,
        "application_name": "lockbay_telegram_bot",
        "options": "-c idle_in_transaction_session_timeout=120000 -c statement_timeout=30000",
        "keepalives": 1,
        "keepalives_idle": 15,
        "keepalives_interval": 5,
        "keepalives_count": 5,
    }
)

# Async database engine with connection pooling
# CRITICAL FIX: Convert sslmode parameter for asyncpg compatibility
async_database_url = Config.DATABASE_URL.replace('postgresql://', 'postgresql+asyncpg://')
# asyncpg uses 'ssl' instead of 'sslmode' parameter
async_database_url = async_database_url.replace('sslmode=require', 'ssl=require')
async_database_url = async_database_url.replace('sslmode=prefer', 'ssl=prefer')
async_database_url = async_database_url.replace('sslmode=disable', 'ssl=disable')
# FIX: Increased async pool for background job load + Railway proxy compatibility
# Combined with 4-minute keep-alive job to maintain database warmth
async_engine = create_async_engine(
    async_database_url,
    pool_size=12,          # Increased from 5 - async handlers + background jobs need more
    max_overflow=20,       # Increased from 10 - burst capacity for webhook floods
    pool_pre_ping=True,    # Validate connections before use
    pool_recycle=600,      # Recycle every 10 min (was 30 min) - Railway proxy drops idle conns
    pool_timeout=10,       # Reduced from 30s - fail fast, don't block event loop
    pool_reset_on_return='rollback',  # Ensure clean state when returned to pool
    echo=False,            # Disable SQL logging (set DEBUG=true to enable)
    echo_pool=False,       # Disable connection pool logging
    connect_args={
        "server_settings": {
            "application_name": "lockbay_telegram_bot_async",
            "idle_in_transaction_session_timeout": "120000",   # 2 min (was 5) - kill leaked txns faster
            "statement_timeout": "30000",                       # 30s (was 60) - kill runaway queries faster
        },
        "timeout": 10,  # Connection timeout
        "command_timeout": 15,  # Command timeout reduced from 30s
    }
)

# Session factory
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

# Sync session factory for compatibility
SyncSessionLocal = SessionLocal

# Async session factory (FIXED - now uses proper async sessions)
AsyncSessionLocal = async_sessionmaker(
    async_engine,
    class_=AsyncSession,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False  # CRITICAL: Prevent greenlet errors in background tasks
)


@asynccontextmanager
async def get_async_session():
    """
    Async context manager for database sessions in Telegram handlers.
    
    PERFORMANCE: Use this instead of SyncSessionLocal() in async handlers
    to prevent event loop blocking (200-500ms → <50ms improvement).
    
    Usage:
        async def button_callback(update, context):
            # ✅ Answer callback FIRST (instant feedback)
            await update.callback_query.answer("⏳ Processing...")
            
            # ✅ Use async session (non-blocking)
            async with get_async_session() as session:
                result = await session.execute(select(User).where(...))
                user = result.scalar_one_or_none()
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


def create_tables():
    """Create all database tables if they don't exist"""
    try:
        logger.info("🏗️ Creating database tables (if they don't exist)...")
        
        # CRITICAL FIX: Import all models to register them with Base.metadata
        # This ensures all table definitions are available for creation
        
        # Create a fresh engine specifically for table creation to avoid transaction conflicts
        from sqlalchemy import create_engine
        from sqlalchemy.exc import ProgrammingError
        if not Config.DATABASE_URL:
            raise ValueError("DATABASE_URL environment variable is required")
        fresh_engine = create_engine(Config.DATABASE_URL, pool_pre_ping=True)
        
        # Log how many tables are registered
        logger.info(f"📊 Found {len(Base.metadata.tables)} table models to create")
        
        try:
            # Use checkfirst=True to only create tables that don't exist
            Base.metadata.create_all(bind=fresh_engine, checkfirst=True)
        except ProgrammingError as e:
            # Handle cases where indexes already exist (this is normal and expected)
            if "already exists" in str(e):
                logger.info(f"⚠️ Some database objects already exist (this is normal): {e}")
            else:
                raise  # Re-raise if it's a different error
        
        # Log which tables were created/verified
        from sqlalchemy import inspect
        inspector = inspect(fresh_engine)
        existing_tables = inspector.get_table_names()
        
        logger.info(f"✅ Database schema verified: {len(existing_tables)} tables available")
        logger.info(f"📋 Tables: {', '.join(sorted(existing_tables))}")
        
        # Clean up the fresh engine
        fresh_engine.dispose()
        
        return True
    except Exception as e:
        # Only treat as error if it's not about existing objects
        if "already exists" not in str(e):
            logger.error(f"❌ Failed to create database tables: {e}")
            import traceback
            logger.error(f"❌ Full error: {traceback.format_exc()}")
            return False
        else:
            logger.info("✅ Database initialization completed (some objects already existed)")
            return True


def get_session() -> Session:
    """Get a new database session"""
    return SessionLocal()


# Connection pool recovery for handling database endpoint suspensions
_last_pool_reset = 0.0
_pool_reset_cooldown = 30.0  # Minimum seconds between pool resets


async def _async_dispose():
    """Async helper to dispose async engine"""
    try:
        await async_engine.dispose()
        logger.info("✅ Async connection pool disposed")
    except Exception as e:
        logger.warning(f"⚠️ Async pool disposal warning: {e}")


def reset_connection_pools():
    """
    Reset all database connection pools to recover from endpoint suspension.
    
    Called when OperationalError indicates the database endpoint is disabled.
    This clears stale connections and allows fresh connections to be established.
    """
    global _last_pool_reset
    import time
    
    current_time = time.time()
    if current_time - _last_pool_reset < _pool_reset_cooldown:
        logger.debug("🔄 Pool reset skipped - cooldown active")
        return False
    
    try:
        logger.warning("🔄 DATABASE_RECOVERY: Resetting connection pools due to endpoint error...")
        
        # Dispose sync engine pool
        engine.dispose()
        logger.info("✅ Sync connection pool reset")
        
        # Dispose async engine pool (synchronously - this is safe)
        # Note: async_engine.dispose() is sync-safe in SQLAlchemy 2.0
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Schedule async disposal
                asyncio.create_task(_async_dispose())
            else:
                asyncio.run(_async_dispose())
        except RuntimeError:
            # No event loop - try sync disposal which works for most cases
            pass
        
        logger.info("✅ Async connection pool reset scheduled")
        
        _last_pool_reset = current_time
        logger.warning("✅ DATABASE_RECOVERY: Connection pools reset - next connection will establish fresh")
        return True
        
    except Exception as e:
        logger.error(f"❌ DATABASE_RECOVERY: Pool reset failed: {e}")
        return False


def handle_database_error(error: Exception) -> bool:
    """
    Handle database errors and attempt recovery if appropriate.
    
    Returns True if recovery was attempted (caller should retry),
    False if error is not recoverable through pool reset.
    """
    error_str = str(error).lower()
    
    # Check for endpoint suspension errors (Neon, Railway, etc.)
    recoverable_patterns = [
        "endpoint has been disabled",
        "endpoint is disabled",
        "connection refused",
        "could not connect to server",
        "connection timed out",
        "server closed the connection unexpectedly",
        "terminating connection due to administrator command",
        "the database system is starting up",
        "the database system is shutting down",
    ]
    
    for pattern in recoverable_patterns:
        if pattern in error_str:
            logger.warning(f"🔄 Detected recoverable database error: {pattern}")
            return reset_connection_pools()
    
    return False


@contextmanager
def managed_session():
    """
    Sync context manager for database sessions with automatic recovery.
    
    If the database endpoint is suspended (Neon, Railway), this will reset
    the connection pool and retry, avoiding the need for a full bot restart.
    """
    import time
    max_retries = 2
    last_error = None
    
    for attempt in range(max_retries + 1):
        session = None
        try:
            session = SessionLocal()
            yield session
            session.commit()
            return  # Success
        except OperationalError as e:
            if session:
                try:
                    session.rollback()
                except Exception:
                    pass
            last_error = e
            if attempt < max_retries and handle_database_error(e):
                logger.info(f"🔄 Retrying database operation (attempt {attempt + 2}/{max_retries + 1})...")
                time.sleep(1)
                continue
            logger.error(f"Database connection error: {e}")
            raise
        except Exception as e:
            if session:
                session.rollback()
            logger.error(f"Database session error: {e}")
            raise
        finally:
            if session:
                try:
                    session.close()
                except Exception:
                    pass
    
    if last_error:
        raise last_error


@asynccontextmanager
async def async_managed_session():
    """
    Async context manager for database sessions with automatic recovery.
    
    If the database endpoint is suspended (Neon, Railway), this will reset
    the connection pool and retry, avoiding the need for a full bot restart.
    """
    max_retries = 2
    last_error = None
    
    for attempt in range(max_retries + 1):
        session = None
        try:
            session = AsyncSessionLocal()
            yield session
            await session.commit()
            return  # Success
        except OperationalError as e:
            if session:
                try:
                    await session.rollback()
                except Exception:
                    pass
            last_error = e
            if attempt < max_retries and handle_database_error(e):
                logger.info(f"🔄 Retrying async database operation (attempt {attempt + 2}/{max_retries + 1})...")
                await asyncio.sleep(1)
                continue
            logger.error(f"Database connection error: {e}")
            raise
        except Exception as e:
            if session:
                await session.rollback()
            logger.error(f"Database session error: {e}")
            raise
        finally:
            if session:
                try:
                    await session.close()
                except Exception:
                    pass
    
    if last_error:
        raise last_error


# Alias for compatibility
sync_managed_session = managed_session


def get_db_session():
    """Get a database session (compatibility function)"""
    return SessionLocal()


def get_sync_db_session():
    """Get a synchronous database session (compatibility function)"""
    return SessionLocal()


def test_connection():
    """Test database connection"""
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            logger.info("✅ Database connection test successful")
            return True
    except Exception as e:
        logger.error(f"❌ Database connection test failed: {e}")
        return False


# PostgreSQL connection management
# Note: PostgreSQL handles foreign keys and constraints natively - no additional setup needed


def get_pool_stats():
    """
    Get connection pool statistics for monitoring and debugging
    
    Returns dict with:
    - sync_pool_size: Current sync pool size
    - sync_checked_out: Checked out sync connections
    - sync_overflow: Overflow sync connections
    - async_pool_size: Current async pool size (estimated)
    - async_checked_out: Checked out async connections (estimated)
    - total_connections: Total estimated active connections
    """
    stats = {}
    
    try:
        # Sync pool stats
        sync_pool = engine.pool
        stats['sync_pool_size'] = sync_pool.size()  # type: ignore[attr-defined]
        stats['sync_checked_out'] = sync_pool.checkedout()  # type: ignore[attr-defined]
        stats['sync_overflow'] = sync_pool.overflow()  # type: ignore[attr-defined]
        
        # Async pool stats (async pool doesn't expose same stats directly)
        # We can get approximate stats from the pool object
        async_pool = async_engine.pool
        if hasattr(async_pool, 'size'):
            stats['async_pool_size'] = async_pool.size()  # type: ignore[attr-defined]
        else:
            stats['async_pool_size'] = 'N/A'
            
        if hasattr(async_pool, 'checkedout'):
            stats['async_checked_out'] = async_pool.checkedout()  # type: ignore[attr-defined]
        else:
            stats['async_checked_out'] = 'N/A'
        
        # Calculate total
        total = 0
        if isinstance(stats['sync_checked_out'], int):
            total += stats['sync_checked_out']
        if isinstance(stats['async_checked_out'], int):
            total += stats['async_checked_out']
        stats['total_connections'] = total
        
        logger.debug(
            f"📊 POOL_STATS: Sync({stats['sync_checked_out']}/{stats['sync_pool_size']}+{stats['sync_overflow']}) "
            f"Async({stats['async_checked_out']}/{stats['async_pool_size']}) "
            f"Total: {stats['total_connections']}"
        )
        
    except Exception as e:
        logger.error(f"❌ Error getting pool stats: {e}")
        stats['error'] = str(e)
    
    return stats


# Note: engine_connect ping removed to prevent SAWarning conflicts with pool_pre_ping=True
# The pool_pre_ping=True setting already handles connection testing automatically


def is_pool_healthy(threshold: float = 0.8) -> bool:
    """
    Check if the connection pool has capacity for new operations.
    Returns True if pool utilization is below threshold (default 80%).
    Used by background jobs to skip execution when the pool is exhausted.
    """
    try:
        sync_pool = engine.pool
        checked_out = sync_pool.checkedout()
        pool_size = sync_pool.size()
        max_overflow = engine.pool._max_overflow
        total_capacity = pool_size + max_overflow
        
        if total_capacity == 0:
            return True
        
        utilization = checked_out / total_capacity
        if utilization >= threshold:
            logger.warning(
                f"POOL_GUARD: Pool utilization {utilization:.0%} >= {threshold:.0%} "
                f"({checked_out}/{total_capacity} connections in use) - skipping non-critical operations"
            )
            return False
        return True
    except Exception:
        return True  # Assume healthy if we can't check
