"""
Universal Session Manager for High-Concurrency Operations
Handles 50,000+ concurrent users with multiple simultaneous operations
"""

import logging
import json
from typing import Dict, Optional, Any, List, Tuple
from datetime import datetime, timedelta
from enum import Enum
from dataclasses import dataclass
from collections import defaultdict
import asyncio
from sqlalchemy import text

from database import SessionLocal

logger = logging.getLogger(__name__)


class SessionType(Enum):
    """Types of sessions that can be managed"""
    TRADE_CHAT = "trade_chat"
    DISPUTE_CHAT = "dispute_chat"
    DIRECT_EXCHANGE = "direct_exchange"
    WALLET_OPERATION = "wallet_operation"
    CASHOUT = "cashout"
    DEPOSIT = "deposit"
    ESCROW_CREATE = "escrow_create"
    ESCROW_MESSAGING = "escrow_messaging"


class OperationStatus(Enum):
    """Status of an operation"""
    PENDING = "pending"
    ACTIVE = "active"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"


@dataclass
class SessionData:
    """Data structure for a session"""
    session_id: str
    user_id: int
    session_type: SessionType
    status: OperationStatus
    created_at: datetime
    updated_at: datetime
    metadata: Dict[str, Any]
    priority: int = 0
    expires_at: Optional[datetime] = None


class UniversalSessionManager:
    """
    Manages multiple concurrent sessions for all users
    Designed to handle 50,000+ concurrent users with multiple operations each
    """
    
    def __init__(self):
        # Multi-level session tracking
        # user_id -> session_type -> session_id -> SessionData
        self.user_sessions: Dict[int, Dict[SessionType, Dict[str, SessionData]]] = defaultdict(
            lambda: defaultdict(dict)
        )
        
        # Quick lookup: session_id -> (user_id, session_type)
        self.session_index: Dict[str, Tuple[int, SessionType]] = {}
        
        # Active operation counts per user (for rate limiting)
        self.user_operation_counts: Dict[int, Dict[SessionType, int]] = defaultdict(
            lambda: defaultdict(int)
        )
        
        # Session expiry tracking
        self.expiry_queue: List[Tuple[datetime, str]] = []
        
        # Configuration
        self.MAX_SESSIONS_PER_TYPE = {
            SessionType.TRADE_CHAT: 20,
            SessionType.DISPUTE_CHAT: 10,
            SessionType.DIRECT_EXCHANGE: 5,
            SessionType.WALLET_OPERATION: 3,
            SessionType.CASHOUT: 3,
            SessionType.DEPOSIT: 3,
            SessionType.ESCROW_CREATE: 5,
            SessionType.ESCROW_MESSAGING: 15
        }
        
        self.SESSION_TIMEOUT = {
            SessionType.TRADE_CHAT: timedelta(hours=24),
            SessionType.DISPUTE_CHAT: timedelta(hours=48),
            SessionType.DIRECT_EXCHANGE: timedelta(minutes=15),
            SessionType.WALLET_OPERATION: timedelta(minutes=30),
            SessionType.CASHOUT: timedelta(minutes=30),
            SessionType.DEPOSIT: timedelta(hours=1),
            SessionType.ESCROW_CREATE: timedelta(minutes=30),
            SessionType.ESCROW_MESSAGING: timedelta(hours=24)
        }
        
        # Performance metrics
        self.total_sessions = 0
        self.peak_concurrent = 0
        self.last_cleanup = datetime.utcnow()
    
    def create_session(
        self,
        user_id: int,
        session_type: SessionType,
        session_id: str,
        metadata: Optional[Dict[str, Any]] = None,
        priority: int = 0
    ) -> Optional[SessionData]:
        """Create a new session for a user"""
        try:
            # Check rate limits
            if not self._check_rate_limit(user_id, session_type):
                logger.warning(f"Rate limit exceeded for user {user_id} on {session_type.value}")
                return None
            
            # Create session data
            now = datetime.utcnow()
            expires_at = now + self.SESSION_TIMEOUT[session_type]
            
            session = SessionData(
                session_id=session_id,
                user_id=user_id,
                session_type=session_type,
                status=OperationStatus.ACTIVE,
                created_at=now,
                updated_at=now,
                metadata=metadata or {},
                priority=priority,
                expires_at=expires_at
            )
            
            # Store session
            self.user_sessions[user_id][session_type][session_id] = session
            self.session_index[session_id] = (user_id, session_type)
            self.user_operation_counts[user_id][session_type] += 1
            
            # Track metrics
            self.total_sessions += 1
            current_count = sum(
                len(sessions)
                for user_sessions in self.user_sessions.values()
                for sessions in user_sessions.values()
            )
            self.peak_concurrent = max(self.peak_concurrent, current_count)
            
            # Add to expiry queue
            self.expiry_queue.append((expires_at, session_id))
            
            logger.info(f"Created session {session_id} for user {user_id} ({session_type.value})")
            return session
            
        except Exception as e:
            logger.error(f"Error creating session: {e}")
            return None
    
    def get_session(self, session_id: str) -> Optional[SessionData]:
        """Get a session by ID"""
        if session_id not in self.session_index:
            return None
        
        user_id, session_type = self.session_index[session_id]
        return self.user_sessions[user_id][session_type].get(session_id)
    
    def get_user_sessions(
        self,
        user_id: int,
        session_type: Optional[SessionType] = None,
        status: Optional[OperationStatus] = None
    ) -> List[SessionData]:
        """Get all sessions for a user, optionally filtered"""
        sessions = []
        
        if session_type:
            type_sessions = self.user_sessions[user_id].get(session_type, {})
            sessions.extend(type_sessions.values())
        else:
            for type_sessions in self.user_sessions[user_id].values():
                sessions.extend(type_sessions.values())
        
        if status:
            sessions = [s for s in sessions if s.status == status]
        
        return sorted(sessions, key=lambda s: s.priority, reverse=True)
    
    def update_session(
        self,
        session_id: str,
        status: Optional[OperationStatus] = None,
        metadata_update: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Update a session's status or metadata"""
        session = self.get_session(session_id)
        if not session:
            return False
        
        session.updated_at = datetime.utcnow()
        
        if status:
            session.status = status
        
        if metadata_update:
            session.metadata.update(metadata_update)
        
        logger.debug(f"Updated session {session_id}: status={status}, metadata={metadata_update}")
        return True
    
    def close_session(self, session_id: str) -> bool:
        """Close and remove a session"""
        if session_id not in self.session_index:
            return False
        
        user_id, session_type = self.session_index[session_id]
        
        # Remove from storage
        if session_id in self.user_sessions[user_id][session_type]:
            del self.user_sessions[user_id][session_type][session_id]
            del self.session_index[session_id]
            self.user_operation_counts[user_id][session_type] -= 1
            
            # Clean up empty structures
            if not self.user_sessions[user_id][session_type]:
                del self.user_sessions[user_id][session_type]
            if not self.user_sessions[user_id]:
                del self.user_sessions[user_id]
            
            logger.info(f"Closed session {session_id}")
            return True
        
        return False
    
    def terminate_session(self, session_id: str, reason: str = "manual_termination") -> bool:
        """Terminate a session with reason logging"""
        session = self.get_session(session_id)
        if session:
            logger.info(f"🛑 Terminating session {session_id} (type: {session.session_type.value}, reason: {reason})")
            return self.close_session(session_id)
        return False
    
    def get_user_session_ids(self, user_id: int, session_type: Optional[SessionType] = None) -> List[str]:
        """Get all session IDs for a user, optionally filtered by type"""
        session_ids = []
        
        if user_id not in self.user_sessions:
            return session_ids
        
        if session_type:
            type_sessions = self.user_sessions[user_id].get(session_type, {})
            session_ids.extend(type_sessions.keys())
        else:
            for type_sessions in self.user_sessions[user_id].values():
                session_ids.extend(type_sessions.keys())
        
        return session_ids
    
    def switch_active_session(
        self,
        user_id: int,
        session_type: SessionType,
        session_id: str
    ) -> bool:
        """Switch the active session for a user within a type"""
        sessions = self.user_sessions[user_id].get(session_type, {})
        
        if session_id not in sessions:
            return False
        
        # Mark all others as pending
        for sid, session in sessions.items():
            if sid != session_id and session.status == OperationStatus.ACTIVE:
                session.status = OperationStatus.PENDING
        
        # Mark selected as active
        sessions[session_id].status = OperationStatus.ACTIVE
        sessions[session_id].updated_at = datetime.utcnow()
        
        logger.info(f"Switched active {session_type.value} to {session_id} for user {user_id}")
        return True
    
    def cleanup_expired_sessions(self) -> int:
        """Remove expired sessions"""
        now = datetime.utcnow()
        removed_count = 0
        
        # Process expiry queue
        while self.expiry_queue and self.expiry_queue[0][0] <= now:
            _, session_id = self.expiry_queue.pop(0)
            
            session = self.get_session(session_id)
            if session and session.expires_at and session.expires_at <= now:
                if self.close_session(session_id):
                    removed_count += 1
        
        # Periodic full cleanup (every hour)
        if (now - self.last_cleanup).total_seconds() > 3600:
            self.last_cleanup = now
            
            # Full scan for expired sessions
            expired_sessions = []
            for user_sessions in self.user_sessions.values():
                for type_sessions in user_sessions.values():
                    for sid, session in type_sessions.items():
                        if session.expires_at and session.expires_at <= now:
                            expired_sessions.append(sid)
            
            for sid in expired_sessions:
                if self.close_session(sid):
                    removed_count += 1
        
        if removed_count > 0:
            logger.info(f"Cleaned up {removed_count} expired sessions")
        
        return removed_count
    
    def _check_rate_limit(self, user_id: int, session_type: SessionType) -> bool:
        """Check if user can create a new session of this type"""
        current_count = self.user_operation_counts[user_id][session_type]
        max_allowed = self.MAX_SESSIONS_PER_TYPE[session_type]
        return current_count < max_allowed
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get current statistics"""
        current_sessions = sum(
            len(sessions)
            for user_sessions in self.user_sessions.values()
            for sessions in user_sessions.values()
        )
        
        active_users = len(self.user_sessions)
        
        session_breakdown = defaultdict(int)
        for user_sessions in self.user_sessions.values():
            for session_type, sessions in user_sessions.items():
                session_breakdown[session_type.value] = len(sessions)
        
        return {
            "current_sessions": current_sessions,
            "active_users": active_users,
            "peak_concurrent": self.peak_concurrent,
            "total_sessions_created": self.total_sessions,
            "session_breakdown": dict(session_breakdown),
            "average_sessions_per_user": current_sessions / max(active_users, 1)
        }
    
    def persist_to_database(self) -> bool:
        """Persist current sessions to database for recovery"""
        session = None
        try:
            session = SessionLocal()
            
            # Clear old sessions
            session.execute(text("DELETE FROM user_sessions"))
            
            # Insert current sessions
            for user_sessions in self.user_sessions.values():
                for type_sessions in user_sessions.values():
                    for session_data in type_sessions.values():
                        session.execute(text("""
                            INSERT INTO user_sessions 
                            (session_id, user_id, session_type, status, metadata_json, 
                             created_at, updated_at, expires_at, priority)
                            VALUES 
                            (:sid, :uid, :stype, :status, :meta, 
                             :created, :updated, :expires, :priority)
                        """), {
                            "sid": session_data.session_id,
                            "uid": session_data.user_id,
                            "stype": session_data.session_type.value,
                            "status": session_data.status.value,
                            "meta": json.dumps(session_data.metadata),
                            "created": session_data.created_at,
                            "updated": session_data.updated_at,
                            "expires": session_data.expires_at,
                            "priority": session_data.priority
                        })
            
            session.commit()
            logger.info("Successfully persisted sessions to database")
            return True
            
        except Exception as e:
            logger.error(f"Failed to persist sessions: {e}")
            return False
        finally:
            if session:
                session.close()
    
    async def restore_from_database(self) -> int:
        """Restore sessions from database after restart"""
        session = None
        try:
            session = SessionLocal()
            
            result = session.execute(text("""
                SELECT * FROM user_sessions 
                WHERE expires_at > CURRENT_TIMESTAMP
            """))
            
            restored = 0
            for row in result:
                try:
                    session_data = SessionData(
                        session_id=row.session_id,
                        user_id=row.user_id,
                        session_type=SessionType(row.session_type),
                        status=OperationStatus(row.status),
                        created_at=row.created_at,
                        updated_at=row.updated_at,
                        metadata=json.loads(row.metadata) if row.metadata else {},
                        priority=row.priority,
                        expires_at=row.expires_at
                    )
                    
                    self.user_sessions[row.user_id][SessionType(row.session_type)][row.session_id] = session_data
                    self.session_index[row.session_id] = (row.user_id, SessionType(row.session_type))
                    restored += 1
                    
                except Exception as e:
                    logger.error(f"Failed to restore session {row.session_id}: {e}")
            
            logger.info(f"Restored {restored} sessions from database")
            return restored
            
        except Exception as e:
            logger.error(f"Failed to restore sessions: {e}")
            return 0
        finally:
            if session:
                session.close()


# Global instance
universal_session_manager = UniversalSessionManager()


# Cleanup task
async def periodic_cleanup():
    """Periodic cleanup task"""
    while True:
        try:
            universal_session_manager.cleanup_expired_sessions()
            stats = universal_session_manager.get_statistics()
            
            if stats["current_sessions"] > 0:
                logger.info(f"Session stats: {stats['current_sessions']} active, "
                          f"{stats['active_users']} users, "
                          f"avg {stats['average_sessions_per_user']:.1f} per user")
            
            # Persist to database every 5 minutes if there are active sessions
            if stats["current_sessions"] > 0:
                universal_session_manager.persist_to_database()
            
        except Exception as e:
            logger.error(f"Error in periodic cleanup: {e}")
        
        await asyncio.sleep(300)  # Run every 5 minutes