"""
Session Reuse Manager - Prevents multiple database connections per request
Ensures a single database session is reused throughout the entire handler lifecycle
"""

import logging
from contextlib import contextmanager
from typing import Optional
from sqlalchemy.orm import Session
from database import SyncSessionLocal
from sqlalchemy.exc import OperationalError
import threading
import time

logger = logging.getLogger(__name__)


class SessionReuseManager:
    """Manages database session reuse within a single request context"""
    
    def __init__(self):
        # Thread-local storage for session reuse
        self._thread_local = threading.local()
        
    def get_or_create_session(self, context_id: str = "default") -> Session:
        """
        Get existing session or create new one for the current context.
        Sessions are cached per thread and context to ensure reuse.
        """
        # Initialize thread-local storage if needed
        if not hasattr(self._thread_local, 'sessions'):
            self._thread_local.sessions = {}
            
        # Check if session exists for this context
        if context_id in self._thread_local.sessions:
            session = self._thread_local.sessions[context_id]
            # PERFORMANCE OPTIMIZATION: Skip session validation for performance-critical contexts
            # Only validate sessions that are likely to be stale (older than 30 seconds)
            session_age = getattr(session, '_created_at', 0)
            current_time = time.time()
            
            if current_time - session_age < 30:
                # Session is fresh, skip validation to improve performance
                return session
                
            # Verify session is still valid with timeout protection and SSL error handling
            try:
                # Simple ping to check connection using proper SQLAlchemy text()
                from sqlalchemy import text
                
                # OPTIMIZATION: Lightweight session validation without heavy logging
                start_time = time.time()
                session.execute(text("SELECT 1"))
                # Only log if session validation takes unusually long
                validation_time = time.time() - start_time
                if validation_time > 1.0:  # Reduced threshold from 2.0s to 1.0s
                    logger.warning(f"⚠️ Slow session validation ({validation_time:.2f}s) for {context_id}")
                return session  # Session is valid, reuse it
            except OperationalError as e:
                if "SSL connection has been closed unexpectedly" in str(e):
                    logger.warning(f"🔌 SSL connection error during validation for {context_id}: {e}")
                    # Enhanced SSL error recovery
                    from database import engine, test_connection
                    engine.dispose()
                    test_connection()  # Proactive health check
                else:
                    logger.debug(f"Session invalid for {context_id}, will create new one: {e}")
                try:
                    session.close()
                except Exception as err:
                    logger.debug(f"Could not close session during SSL error cleanup: {err}")
                    pass
                del self._thread_local.sessions[context_id]
            except Exception as e:
                # Session is invalid, clean it up and create new one
                logger.debug(f"Session invalid for {context_id}, will create new one: {e}")
                try:
                    session.close()
                except Exception as err:
                    logger.debug(f"Could not close session during cleanup: {err}")
                    pass
                del self._thread_local.sessions[context_id]
        
        # Create new session with minimal retry delay to avoid blocking async event loop
        # DESIGN DECISION: Use 1ms sleep instead of asyncio.sleep() because:
        # - session_reuse_manager is synchronous by design (used with sync sessions)
        # - 1ms blocking is negligible (300x faster than original 300ms)
        # - Preserves critical retry logic for SSL/connection recovery
        # - Typical DB connection: 10-100ms, so 1ms retry delay is <1% overhead
        max_retries = 2  # Minimal retries for transient SSL/connection errors
        retry_delay = 0.001  # 1ms delay - negligible blocking for critical SSL/connection recovery
        
        for attempt in range(max_retries):
            try:
                start_time = time.time()
                # OPTIMIZATION: Only log session creation on retry attempts or failures
                if attempt > 0:
                    logger.info(f"📊 Creating new database session for context: {context_id} (retry {attempt + 1})")
                else:
                    logger.debug(f"📊 Creating new database session for context: {context_id}")
                
                # THREAD-SAFE FIX: Use threading.Timer instead of signals for timeout
                timeout_expired = threading.Event()
                
                def timeout_handler():
                    timeout_expired.set()
                
                # Set 3-second timeout using threading.Timer (works in any thread)
                timer = threading.Timer(3.0, timeout_handler)
                timer.start()
                
                try:
                    # OPTIMIZED: Skip connection test for faster startup
                    session = SyncSessionLocal()
                    
                    # PERFORMANCE OPTIMIZATION: Add session creation timestamp for age tracking
                    # Using setattr to avoid LSP warning about dynamic attribute
                    setattr(session, '_created_at', time.time())
                    
                    # Check if timeout expired during connection
                    if timeout_expired.is_set():
                        session.close()
                        raise TimeoutError("Database connection timed out")
                        
                except TimeoutError:
                    logger.error(f"❌ Database connection timeout for {context_id}")
                    if attempt < max_retries - 1:
                        # ASYNC FIX: Minimal 1ms sleep for retry - negligible blocking
                        time.sleep(retry_delay)
                        continue
                    raise
                except OperationalError as e:
                    if "SSL connection has been closed unexpectedly" in str(e):
                        logger.warning(f"🔌 SSL connection error during session creation for {context_id}: {e}")
                        if attempt < max_retries - 1:
                            # Enhanced SSL error recovery
                            from database import engine, test_connection
                            engine.dispose()
                            health_ok = test_connection()
                            logger.info(f"🔄 SSL recovery for {context_id}: health {'OK' if health_ok else 'DEGRADED'}")
                            # ASYNC FIX: Minimal 1ms sleep for SSL recovery - negligible blocking
                            time.sleep(retry_delay)
                            continue
                        else:
                            logger.error(f"❌ SSL connection failed after {max_retries} attempts for {context_id}")
                            raise
                    else:
                        # Other database errors
                        if timeout_expired.is_set():
                            logger.error(f"❌ Database connection timeout for {context_id}")
                            if attempt < max_retries - 1:
                                # ASYNC FIX: Minimal 1ms sleep for retry - negligible blocking
                                time.sleep(retry_delay)
                                continue
                            raise TimeoutError("Database connection timed out")
                        raise
                except Exception:
                    # Check if timeout expired during exception handling
                    if timeout_expired.is_set():
                        logger.error(f"❌ Database connection timeout for {context_id}")
                        if attempt < max_retries - 1:
                            # ASYNC FIX: Minimal 1ms sleep for retry - negligible blocking
                            time.sleep(retry_delay)
                            continue
                        raise TimeoutError("Database connection timed out")
                    raise
                finally:
                    timer.cancel()  # Always cancel timeout timer
                
                connection_time = time.time() - start_time
                if connection_time > 1.0:
                    logger.warning(f"⚠️ Slow connection creation ({connection_time:.2f}s) for {context_id}")
                
                self._thread_local.sessions[context_id] = session
                return session
                
            except OperationalError as e:
                if "SSL connection has been closed unexpectedly" in str(e):
                    logger.error(f"🔌 SSL connection error for {context_id} (attempt {attempt + 1}): {e}")
                    if attempt < max_retries - 1:
                        # Enhanced connection recovery with health validation
                        from database import engine, test_connection
                        engine.dispose()
                        health_ok = test_connection()
                        logger.info(f"🔄 SSL retry for {context_id}: health {'OK' if health_ok else 'DEGRADED'}")
                        # ASYNC FIX: Minimal 1ms sleep for SSL retry - negligible blocking
                        time.sleep(retry_delay)
                        retry_delay *= 1.2  # Gentle backoff for subsequent retries
                    else:
                        logger.error(f"❌ All SSL connection attempts failed for {context_id}")
                        raise
                else:
                    logger.error(f"Database error for {context_id} (attempt {attempt + 1}): {e}")
                    if attempt < max_retries - 1:
                        # ASYNC FIX: Minimal 1ms sleep for database retry - negligible blocking
                        time.sleep(retry_delay)
                        retry_delay *= 2  # Exponential backoff for database errors
                        continue
                    else:
                        logger.error(f"❌ All database connection attempts failed for {context_id}")
                        raise
            except Exception as e:
                logger.error(f"Session creation failed for {context_id} (attempt {attempt + 1}): {e}")
                if attempt < max_retries - 1:
                    # ASYNC FIX: Minimal 1ms sleep for general retry - negligible blocking
                    time.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                    continue
                else:
                    logger.error(f"❌ All session creation attempts failed for {context_id}")
                    raise
        
        # Fallback: This should never be reached, but ensures function always returns or raises
        raise RuntimeError(f"Failed to create session for {context_id} after {max_retries} attempts")
    
    def close_session(self, context_id: str = "default"):
        """Close and remove session for a specific context"""
        if hasattr(self._thread_local, 'sessions'):
            if context_id in self._thread_local.sessions:
                try:
                    session = self._thread_local.sessions[context_id]
                    session.close()
                    del self._thread_local.sessions[context_id]
                    logger.debug(f"✅ Closed session for context: {context_id}")
                except Exception as e:
                    logger.error(f"Error closing session for {context_id}: {e}")
    
    def close_all_sessions(self):
        """Close all sessions in current thread"""
        if hasattr(self._thread_local, 'sessions'):
            for context_id, session in list(self._thread_local.sessions.items()):
                try:
                    session.close()
                    logger.debug(f"Closed session: {context_id}")
                except Exception as e:
                    logger.error(f"Error closing session {context_id}: {e}")
            self._thread_local.sessions.clear()


# Global instance
session_reuse_manager = SessionReuseManager()


@contextmanager
def get_reusable_session(context_id: Optional[str] = None, user_id: Optional[int] = None):
    """
    Context manager that provides a reusable database session.
    The session will be reused for the same context_id within the same thread.
    
    Args:
        context_id: Unique identifier for the context (e.g., "start_handler_123")
        user_id: Optional user ID to include in context
    
    Example:
        with get_reusable_session("start_handler", user_id=123) as session:
            user = session.query(User).filter_by(id=123).first()
    """
    # Generate context ID if not provided
    if context_id is None:
        context_id = "default"
    if user_id is not None:
        context_id = f"{context_id}_{user_id}"
    
    session = session_reuse_manager.get_or_create_session(context_id)
    
    try:
        yield session
        # Don't close here - let it be reused
    except OperationalError as e:
        if "SSL connection has been closed unexpectedly" in str(e):
            logger.warning(f"🔌 SSL connection error in context {context_id}: {e}")
            # Enhanced SSL error recovery
            from database import engine, test_connection
            engine.dispose()
            test_connection()  # Proactive health check
            # Clean up the invalid session
            session_reuse_manager.close_session(context_id)
        session.rollback()
        raise
    except Exception as e:
        session.rollback()
        logger.error(f"Session error in context {context_id}: {e}")
        raise
    finally:
        # Don't close immediately - session can be reused
        pass


def cleanup_handler_sessions(handler_name: str, user_id: Optional[int] = None):
    """
    Clean up sessions after handler completes.
    Should be called at the end of each handler.
    """
    context_id = handler_name
    if user_id:
        context_id = f"{handler_name}_{user_id}"
    
    session_reuse_manager.close_session(context_id)
    logger.debug(f"🧹 Cleaned up session for handler: {handler_name}")