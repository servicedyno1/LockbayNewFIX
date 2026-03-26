"""
Enhanced Database Session Manager
Robust database session lifecycle management with health checks and cleanup
Provides connection pooling, session tracking, and automatic recovery
"""

import logging
import time
import asyncio
from typing import Optional, Dict, Any, AsyncContextManager
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum

from sqlalchemy import text
from sqlalchemy.orm import Session
# ASYNC FIX: Remove direct sync engine import
# from database import engine

logger = logging.getLogger(__name__)


class SessionStatus(Enum):
    """Session status tracking"""
    ACTIVE = "active"
    IDLE = "idle"
    CLOSED = "closed"
    ERROR = "error"
    ABANDONED = "abandoned"


@dataclass
class SessionInfo:
    """Session tracking information"""
    session_id: str
    created_at: datetime
    last_used: datetime
    status: SessionStatus
    operation_count: int
    error_count: int
    current_operation: Optional[str]
    connection_info: Dict[str, Any]


class EnhancedDBSessionManager:
    """
    Enhanced database session manager with comprehensive lifecycle management
    
    Features:
    - Session health monitoring and cleanup
    - Connection pool management with health checks
    - Automatic recovery from connection failures
    - Session leak detection and prevention
    - Performance metrics and monitoring
    """
    
    def __init__(self):
        # ASYNC FIX: Remove sync session factory and direct engine usage
        # Don't use direct engine access - delegate to async patterns
        self.engine = None  # Not using direct engine anymore
        
        # Session tracking
        self.active_sessions: Dict[str, SessionInfo] = {}
        self.session_counter = 0
        
        # Configuration
        self.max_session_age = timedelta(minutes=30)
        self.max_idle_time = timedelta(minutes=10)
        self.health_check_interval = 60  # seconds
        self.cleanup_interval = 300  # 5 minutes
        
        # Metrics
        self.metrics = {
            'sessions_created': 0,
            'sessions_closed': 0,
            'sessions_abandoned': 0,
            'sessions_recovered': 0,
            'connection_errors': 0,
            'connection_recoveries': 0,
            'operations_total': 0,
            'operations_failed': 0,
            'pool_overflows': 0
        }
        
        # Background tasks
        self._cleanup_task = None
        self._health_check_task = None
        self._initialize_background_tasks()
    
    def _initialize_background_tasks(self):
        """Initialize background maintenance tasks"""
        try:
            # Start cleanup task
            loop = asyncio.get_event_loop()
            if loop.is_running():
                self._cleanup_task = loop.create_task(self._periodic_cleanup())
                self._health_check_task = loop.create_task(self._periodic_health_check())
                logger.info("⚙️ Started database session background tasks")
        except Exception as e:
            logger.warning(f"Could not start background tasks: {e}")
    
    @asynccontextmanager
    async def managed_session(
        self, 
        operation_name: str = "unknown",
        timeout_seconds: int = 30
    ) -> AsyncContextManager[Session]:
        """
        Enhanced database session with tracking and monitoring
        
        ASYNC FIX: Now delegates to the main async managed_session
        while providing enhanced monitoring and tracking capabilities
        
        Args:
            operation_name: Name of the operation for tracking
            timeout_seconds: Maximum time to wait for session
            
        Yields:
            Session: Async database session object
        """
        session_id = f"sess_{int(time.time())}_{self.session_counter}"
        self.session_counter += 1
        start_time = time.time()
        
        # Track session start
        session_info = SessionInfo(
            session_id=session_id,
            created_at=datetime.utcnow(),
            last_used=datetime.utcnow(),
            status=SessionStatus.ACTIVE,
            operation_count=0,
            error_count=0,
            current_operation=operation_name,
            connection_info={"type": "async_managed"}
        )
        self.active_sessions[session_id] = session_info
        self.metrics['sessions_created'] += 1
        
        logger.debug(f"📞 Enhanced session {session_id} for operation: {operation_name}")
        
        try:
            # ASYNC FIX: Delegate to main managed_session for compatibility
            from database import managed_session as db_managed_session
            with db_managed_session() as session:
                # Update connection info if possible
                if session_id in self.active_sessions:
                    self.active_sessions[session_id].connection_info = self._get_connection_info(session)
                
                yield session
                
                # Update session info on successful completion
                if session_id in self.active_sessions:
                    self.active_sessions[session_id].status = SessionStatus.IDLE
                    self.active_sessions[session_id].operation_count += 1
            
        except Exception as e:
            # Handle session errors
            if session_id in self.active_sessions:
                self.active_sessions[session_id].status = SessionStatus.ERROR
                self.active_sessions[session_id].error_count += 1
            
            self.metrics['operations_failed'] += 1
            logger.error(f"❌ Enhanced session {session_id} error in '{operation_name}': {e}")
            
            raise
            
        finally:
            # Cleanup session tracking
            duration = time.time() - start_time
            self.metrics['operations_total'] += 1
            
            # Remove from active tracking
            if session_id in self.active_sessions:
                self.active_sessions[session_id].status = SessionStatus.CLOSED
                self.metrics['sessions_closed'] += 1
                # Remove after brief delay
                await asyncio.sleep(0.1)
                if session_id in self.active_sessions:
                    del self.active_sessions[session_id]
            
            if duration > 10:  # Log slow operations
                logger.warning(
                    f"⏱️ Slow enhanced session: {operation_name} took {duration:.2f}s "
                    f"(session: {session_id})"
                )
    
    async def _create_session_with_timeout(self, timeout_seconds: int) -> Session:
        """Create database session with timeout"""
        try:
            # Check connection pool health first
            await self._check_connection_pool_health()
            
            # ASYNC FIX: Use async session creation
            from database import managed_session as sync_managed_session
            with sync_managed_session() as test_session:
                # Test the connection with async query
                test_session.execute(text("SELECT 1"))
                test_session.commit()
            
            # ASYNC FIX: We don't return sync sessions anymore
            # The managed_session context manager handles session creation
            return None
            
        except Exception as e:
            self.metrics['connection_errors'] += 1
            logger.error(f"❌ Failed to create database session: {e}")
            raise
    
    async def _check_connection_pool_health(self) -> bool:
        """Check connection pool health and metrics"""
        try:
            # SYNC FIX: Don't access engine.pool directly since engine is None
            # Instead test actual database connectivity with sync patterns
            from database import managed_session as sync_managed_session
            with sync_managed_session() as test_session:
                test_session.execute(text("SELECT 1"))
                test_session.commit()
            
            logger.debug("🏢 Connection pool health check passed")
            return True
            
        except Exception as e:
            logger.error(f"❌ Connection pool health check failed: {e}")
            return False
    
    def _get_connection_info(self, session: Session) -> Dict[str, Any]:
        """Get connection information for tracking"""
        try:
            if session.bind:
                connection = session.bind
                return {
                    'url': str(connection.url) if hasattr(connection, 'url') else 'unknown',
                    'dialect': str(connection.dialect.name) if hasattr(connection, 'dialect') else 'unknown',
                    'pool_id': id(connection.pool) if hasattr(connection, 'pool') else None
                }
        except Exception as e:
            logger.debug(f"Could not get connection info: {e}")
        
        return {'error': 'connection_info_unavailable'}
    
    async def _cleanup_session(self, session: Optional[Session], session_id: str):
        """Clean up session and update tracking"""
        try:
            if session:
                # Close session (synchronous close for compatibility)
                session.close()
                logger.debug(f"🗺️ Closed session {session_id}")
            
            # Update session tracking
            if session_id in self.active_sessions:
                self.active_sessions[session_id].status = SessionStatus.CLOSED
                self.metrics['sessions_closed'] += 1
                
                # Remove from active tracking after brief delay
                await asyncio.sleep(1)
                if session_id in self.active_sessions:
                    del self.active_sessions[session_id]
            
        except Exception as e:
            logger.error(f"❌ Error cleaning up session {session_id}: {e}")
    
    async def _attempt_session_recovery(self, session: Optional[Session], session_id: str):
        """Attempt to recover from session errors"""
        try:
            logger.info(f"🔄 Attempting session recovery for {session_id}")
            
            # ASYNC FIX: Don't dispose engine - not using direct engine anymore
            # Engine disposal handled by async patterns automatically
            
            # Wait briefly before attempting recovery
            await asyncio.sleep(0.5)
            
            # ASYNC FIX: Test new connection with async patterns
            from database import managed_session as sync_managed_session
            with sync_managed_session() as test_session:
                test_session.execute(text("SELECT 1"))
                test_session.commit()
            
            self.metrics['connection_recoveries'] += 1
            logger.info(f"✅ Session recovery successful for {session_id}")
            
        except Exception as e:
            logger.error(f"❌ Session recovery failed for {session_id}: {e}")
    
    async def _periodic_cleanup(self):
        """Periodic cleanup of abandoned sessions"""
        while True:
            try:
                await asyncio.sleep(self.cleanup_interval)
                await self._cleanup_abandoned_sessions()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ Error in periodic cleanup: {e}")
    
    async def _periodic_health_check(self):
        """Periodic health check of database connections"""
        while True:
            try:
                await asyncio.sleep(self.health_check_interval)
                await self._health_check()  # Call async method with await
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ Error in periodic health check: {e}")
    
    async def _cleanup_abandoned_sessions(self):
        """Clean up sessions that have been abandoned or are too old"""
        now = datetime.utcnow()
        abandoned_sessions = []
        
        for session_id, session_info in list(self.active_sessions.items()):
            # Check for abandoned sessions
            if (
                (now - session_info.last_used) > self.max_idle_time or
                (now - session_info.created_at) > self.max_session_age or
                session_info.status == SessionStatus.ERROR
            ):
                abandoned_sessions.append(session_id)
        
        if abandoned_sessions:
            logger.warning(f"🧹 Cleaning up {len(abandoned_sessions)} abandoned sessions")
            
            for session_id in abandoned_sessions:
                if session_id in self.active_sessions:
                    self.active_sessions[session_id].status = SessionStatus.ABANDONED
                    del self.active_sessions[session_id]
                    self.metrics['sessions_abandoned'] += 1
    
    async def _health_check(self):
        """Perform health check on database connections using sync patterns"""
        try:
            # SYNC FIX: Use sync database patterns for health check
            from database import managed_session as sync_managed_session
            with sync_managed_session() as session:
                # Simple sync test query
                session.execute(text("SELECT 1 as test"))
                session.commit()
                logger.debug("🟢 Database health check passed")
                return True
            
        except Exception as e:
            logger.error(f"❌ Database health check failed: {e}")
            return False
    
    def get_session_metrics(self) -> Dict[str, Any]:
        """Get comprehensive session metrics"""
        now = datetime.utcnow()
        
        # Calculate active session statistics
        active_count = len([s for s in self.active_sessions.values() if s.status == SessionStatus.ACTIVE])
        idle_count = len([s for s in self.active_sessions.values() if s.status == SessionStatus.IDLE])
        error_count = len([s for s in self.active_sessions.values() if s.status == SessionStatus.ERROR])
        
        # Calculate average session age
        if self.active_sessions:
            avg_age = sum(
                (now - s.created_at).total_seconds() 
                for s in self.active_sessions.values()
            ) / len(self.active_sessions)
        else:
            avg_age = 0
        
        return {
            **self.metrics,
            'active_sessions': {
                'total': len(self.active_sessions),
                'active': active_count,
                'idle': idle_count,
                'error': error_count,
                'average_age_seconds': avg_age
            },
            'connection_pool': {
                'status': 'unavailable_direct_access',
                'note': 'Engine pooling managed by async database layer'
            }
        }
    
    async def shutdown(self):
        """Graceful shutdown of session manager"""
        logger.info("📋 Shutting down database session manager")
        
        # Cancel background tasks
        if self._cleanup_task:
            self._cleanup_task.cancel()
        if self._health_check_task:
            self._health_check_task.cancel()
        
        # Close all active sessions
        for session_id in list(self.active_sessions.keys()):
            try:
                await self._cleanup_session(None, session_id)
            except Exception as e:
                logger.error(f"Error closing session {session_id} during shutdown: {e}")
        
        # SYNC FIX: Don't dispose engine since engine is None
        # Engine disposal is handled by the main database layer
        logger.info("✅ Database session manager shutdown complete")


# Global enhanced session manager instance
enhanced_db_session_manager = EnhancedDBSessionManager()
