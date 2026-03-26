"""
Redis-based Session Management Foundation
Replaces in-memory session storage with distributed Redis-based storage
"""

import time
import logging
from typing import Any, Optional, Dict, List
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict

from services.state_manager import state_manager
from config import Config

logger = logging.getLogger(__name__)


@dataclass
class SessionState:
    """Represents a user session state"""
    user_id: int
    session_id: str
    conversation_flow: Optional[str]
    current_step: Optional[str]
    data: Dict[str, Any]
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    is_active: bool
    metadata: Dict[str, Any]


class RedisSessionManager:
    """
    Redis-based session management with proper datetime serialization and TTL handling
    """
    
    def __init__(self):
        # Use Config-based TTL values
        self.default_ttl = Config.REDIS_SESSION_TTL
        self.conversation_ttl = Config.REDIS_CONVERSATION_TTL
        self.temp_state_ttl = Config.REDIS_TEMP_STATE_TTL
        
        # Session key prefixes
        self.session_prefix = "session"
        self.conversation_prefix = "conversation"
        self.user_state_prefix = "user_state"
        self.temporary_state_prefix = "temp_state"
    
    async def create_session(
        self, 
        user_id: int, 
        conversation_flow: Optional[str] = None,
        initial_data: Optional[Dict[str, Any]] = None,
        ttl_seconds: Optional[int] = None
    ) -> str:
        """
        Create a new user session
        
        Args:
            user_id: Telegram user ID
            conversation_flow: Current conversation flow (escrow, wallet, etc.)
            initial_data: Initial session data
            ttl_seconds: Custom TTL (uses default if not provided)
            
        Returns:
            str: Session ID
        """
        session_id = f"sess_{user_id}_{int(time.time())}"
        ttl = ttl_seconds or self.default_ttl
        
        session_state = SessionState(
            user_id=user_id,
            session_id=session_id,
            conversation_flow=conversation_flow,
            current_step=None,
            data=initial_data or {},
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(seconds=ttl),
            is_active=True,
            metadata={}
        )
        
        # Store session with proper serialization
        success = await state_manager.set_state(
            f"{self.session_prefix}:{session_id}",
            self._serialize_session_state(session_state),
            ttl=ttl,
            tags=['session', f'user_{user_id}'],
            source='session_manager'
        )
        
        if success:
            # Track active session for user
            await self._add_user_session(user_id, session_id)
            logger.info(f"👤 Created session {session_id} for user {user_id}")
            return session_id
        else:
            raise RuntimeError(f"Failed to create session for user {user_id}")
    
    async def get_session(self, session_id: str) -> Optional[SessionState]:
        """Get session by ID with proper datetime deserialization"""
        session_data = await state_manager.get_state(f"{self.session_prefix}:{session_id}")
        
        if not session_data:
            return None
        
        try:
            # Deserialize session state properly
            session = self._deserialize_session_state(session_data)
            
            # Redis TTL handles expiry, but double-check for safety
            if isinstance(session.expires_at, datetime) and datetime.utcnow() > session.expires_at:
                logger.warning(f"⚠️ Session {session_id} expired but not cleaned by Redis TTL")
                await self.destroy_session(session_id)
                return None
            
            return session
            
        except Exception as e:
            logger.error(f"❌ Failed to deserialize session {session_id}: {e}")
            # Clean up corrupted session
            await self.destroy_session(session_id)
            return None
    
    async def get_user_session(self, user_id: int) -> Optional[SessionState]:
        """Get active session for user (most recent)"""
        user_sessions = await self._get_user_sessions(user_id)
        
        if not user_sessions:
            return None
        
        # Get most recent active session
        for session_id in user_sessions:
            session = await self.get_session(session_id)
            if session and session.is_active:
                return session
        
        return None
    
    async def update_session(
        self,
        session_id: str,
        data_updates: Optional[Dict[str, Any]] = None,
        conversation_flow: Optional[str] = None,
        current_step: Optional[str] = None,
        extend_ttl: bool = True
    ) -> bool:
        """
        Update session data
        
        Args:
            session_id: Session ID to update
            data_updates: Data updates to merge
            conversation_flow: New conversation flow
            current_step: New current step
            extend_ttl: Whether to extend session TTL
            
        Returns:
            bool: True if successful
        """
        session = await self.get_session(session_id)
        if not session:
            logger.warning(f"❌ Session not found for update: {session_id}")
            return False
        
        # Apply updates
        if data_updates:
            session.data.update(data_updates)
        
        if conversation_flow is not None:
            session.conversation_flow = conversation_flow
        
        if current_step is not None:
            session.current_step = current_step
        
        session.updated_at = datetime.utcnow()
        
        # Extend TTL if requested
        if extend_ttl:
            session.expires_at = datetime.utcnow() + timedelta(seconds=self.default_ttl)
        
        # Update in Redis with proper serialization and TTL extension
        success = await state_manager.set_state(
            f"{self.session_prefix}:{session_id}",
            self._serialize_session_state(session),
            ttl=self.default_ttl if extend_ttl else None,
            tags=['session', f'user_{session.user_id}'],
            source='session_manager'
        )
        
        if success:
            logger.debug(f"📝 Updated session {session_id}")
        
        return success
    
    async def destroy_session(self, session_id: str) -> bool:
        """Destroy a session"""
        session = await self.get_session(session_id)
        if session:
            await self._remove_user_session(session.user_id, session_id)
        
        success = await state_manager.delete_state(f"{self.session_prefix}:{session_id}")
        
        if success:
            logger.info(f"🗑️ Destroyed session {session_id}")
        
        return success
    
    async def destroy_user_sessions(self, user_id: int) -> int:
        """Destroy all sessions for a user"""
        user_sessions = await self._get_user_sessions(user_id)
        destroyed_count = 0
        
        for session_id in user_sessions:
            if await self.destroy_session(session_id):
                destroyed_count += 1
        
        # Clear user session tracking
        await state_manager.delete_state(f"user_sessions:{user_id}")
        
        logger.info(f"🧹 Destroyed {destroyed_count} sessions for user {user_id}")
        return destroyed_count
    
    # Conversation State Management
    
    async def set_conversation_state(
        self, 
        user_id: int, 
        flow_name: str, 
        state_data: Dict[str, Any],
        ttl_seconds: Optional[int] = None
    ) -> bool:
        """Set conversation state for user in specific flow"""
        key = f"{self.conversation_prefix}:{user_id}:{flow_name}"
        
        conversation_data = {
            'user_id': user_id,
            'flow_name': flow_name,
            'state': state_data,
            'created_at': datetime.utcnow(),
            'updated_at': datetime.utcnow()
        }
        
        return await state_manager.set_state(
            key,
            conversation_data,
            ttl=ttl_seconds or self.conversation_ttl,
            tags=['conversation', f'user_{user_id}', f'flow_{flow_name}'],
            source='conversation_manager'
        )
    
    async def get_conversation_state(
        self, 
        user_id: int, 
        flow_name: str
    ) -> Optional[Dict[str, Any]]:
        """Get conversation state for user in specific flow"""
        key = f"{self.conversation_prefix}:{user_id}:{flow_name}"
        
        conversation_data = await state_manager.get_state(key)
        if conversation_data:
            return conversation_data.get('state')
        
        return None
    
    async def clear_conversation_state(
        self, 
        user_id: int, 
        flow_name: Optional[str] = None
    ) -> bool:
        """Clear conversation state (specific flow or all)"""
        if flow_name:
            # Clear specific flow
            key = f"{self.conversation_prefix}:{user_id}:{flow_name}"
            return await state_manager.delete_state(key)
        else:
            # Clear all conversation states for user
            pattern = f"{self.conversation_prefix}:{user_id}:*"
            keys = await state_manager.get_keys_by_pattern(pattern)
            
            cleared_count = 0
            for key in keys:
                if await state_manager.delete_state(key):
                    cleared_count += 1
            
            logger.debug(f"🧹 Cleared {cleared_count} conversation states for user {user_id}")
            return cleared_count > 0
    
    # Temporary State Management
    
    async def set_temporary_state(
        self, 
        user_id: int, 
        state_key: str, 
        state_value: Any,
        ttl_seconds: int = 300  # 5 minutes default for temporary state
    ) -> bool:
        """Set temporary state with short TTL"""
        key = f"{self.temporary_state_prefix}:{user_id}:{state_key}"
        
        temp_data = {
            'user_id': user_id,
            'state_key': state_key,
            'value': state_value,
            'created_at': datetime.utcnow()
        }
        
        return await state_manager.set_state(
            key,
            temp_data,
            ttl=ttl_seconds,
            tags=['temporary', f'user_{user_id}'],
            source='temp_state_manager'
        )
    
    async def get_temporary_state(
        self, 
        user_id: int, 
        state_key: str
    ) -> Any:
        """Get temporary state value"""
        key = f"{self.temporary_state_prefix}:{user_id}:{state_key}"
        
        temp_data = await state_manager.get_state(key)
        if temp_data:
            return temp_data.get('value')
        
        return None
    
    async def clear_temporary_state(
        self, 
        user_id: int, 
        state_key: Optional[str] = None
    ) -> bool:
        """Clear temporary state (specific key or all)"""
        if state_key:
            key = f"{self.temporary_state_prefix}:{user_id}:{state_key}"
            return await state_manager.delete_state(key)
        else:
            # Clear all temporary states for user
            pattern = f"{self.temporary_state_prefix}:{user_id}:*"
            keys = await state_manager.get_keys_by_pattern(pattern)
            
            cleared_count = 0
            for key in keys:
                if await state_manager.delete_state(key):
                    cleared_count += 1
            
            return cleared_count > 0
    
    # User State Management
    
    async def set_user_state(
        self, 
        user_id: int, 
        state_data: Dict[str, Any],
        ttl_seconds: Optional[int] = None
    ) -> bool:
        """Set persistent user state"""
        key = f"{self.user_state_prefix}:{user_id}"
        
        user_data = {
            'user_id': user_id,
            'state': state_data,
            'updated_at': datetime.utcnow()
        }
        
        return await state_manager.set_state(
            key,
            user_data,
            ttl=ttl_seconds,  # No TTL means persistent
            tags=['user_state', f'user_{user_id}'],
            source='user_state_manager'
        )
    
    async def get_user_state(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Get persistent user state"""
        key = f"{self.user_state_prefix}:{user_id}"
        
        user_data = await state_manager.get_state(key)
        if user_data:
            return user_data.get('state')
        
        return None
    
    async def clear_user_state(self, user_id: int) -> bool:
        """Clear persistent user state"""
        key = f"{self.user_state_prefix}:{user_id}"
        return await state_manager.delete_state(key)
    
    # Private helper methods
    
    async def _add_user_session(self, user_id: int, session_id: str):
        """Add session to user's session list"""
        user_sessions_key = f"user_sessions:{user_id}"
        current_sessions = await state_manager.get_state(user_sessions_key, [])
        
        # CRITICAL FIX: Ensure current_sessions is always a list to prevent 'bool' object has no attribute 'append' error
        if not isinstance(current_sessions, list):
            logger.warning(f"⚠️ Expected list for user sessions, got {type(current_sessions)}: {current_sessions}")
            current_sessions = []
        
        if session_id not in current_sessions:
            current_sessions.append(session_id)
            await state_manager.set_state(
                user_sessions_key,
                current_sessions,
                ttl=self.default_ttl * 2,  # Longer TTL for session tracking
                tags=['user_sessions', f'user_{user_id}'],
                source='session_tracker'
            )
    
    async def _remove_user_session(self, user_id: int, session_id: str):
        """Remove session from user's session list"""
        user_sessions_key = f"user_sessions:{user_id}"
        current_sessions = await state_manager.get_state(user_sessions_key, [])
        
        # CRITICAL FIX: Ensure current_sessions is always a list to prevent 'bool' object errors
        if not isinstance(current_sessions, list):
            logger.warning(f"⚠️ Expected list for user sessions, got {type(current_sessions)}: {current_sessions}")
            current_sessions = []
        
        if session_id in current_sessions:
            current_sessions.remove(session_id)
            await state_manager.update_state(user_sessions_key, current_sessions)
    
    async def _get_user_sessions(self, user_id: int) -> List[str]:
        """Get all session IDs for user"""
        user_sessions_key = f"user_sessions:{user_id}"
        result = await state_manager.get_state(user_sessions_key, [])
        
        # CRITICAL FIX: Ensure we always return a list to prevent type errors
        if not isinstance(result, list):
            logger.warning(f"⚠️ Expected list for user sessions, got {type(result)}: {result}")
            return []
        
        return result
    
    # Cleanup and maintenance
    
    async def cleanup_expired_sessions(self) -> int:
        """Clean up expired sessions"""
        # Get all session keys
        session_keys = await state_manager.get_keys_by_tag('session')
        expired_count = 0
        
        for key in session_keys:
            session_data = await state_manager.get_state(key)
            if session_data:
                try:
                    # Use proper deserialization
                    session = self._deserialize_session_state(session_data)
                    if datetime.utcnow() > session.expires_at:
                        session_id = key.replace(f'{self.session_prefix}:', '')
                        await self.destroy_session(session_id)
                        expired_count += 1
                except Exception as e:
                    logger.warning(f"⚠️ Failed to check session expiry for {key}, removing: {e}")
                    session_id = key.replace(f'{self.session_prefix}:', '')
                    await self.destroy_session(session_id)
                    expired_count += 1
        
        if expired_count > 0:
            logger.info(f"🧹 Cleaned up {expired_count} expired sessions")
        
        return expired_count
    
    def _serialize_session_state(self, session_state: SessionState) -> Dict[str, Any]:
        """Serialize SessionState with proper datetime handling"""
        data = asdict(session_state)
        
        # Convert datetime objects to ISO strings for JSON serialization
        for key, value in data.items():
            if isinstance(value, datetime):
                data[key] = value.isoformat()
        
        return data
    
    def _deserialize_session_state(self, session_data: Dict[str, Any]) -> SessionState:
        """Deserialize session data back to SessionState with proper datetime parsing"""
        # Convert ISO strings back to datetime objects
        datetime_fields = ['created_at', 'updated_at', 'expires_at']
        
        for field in datetime_fields:
            if field in session_data and isinstance(session_data[field], str):
                try:
                    session_data[field] = datetime.fromisoformat(session_data[field])
                except (ValueError, TypeError) as e:
                    logger.warning(f"⚠️ Failed to parse datetime field {field}: {e}")
                    # Set to current time as fallback
                    session_data[field] = datetime.utcnow()
        
        return SessionState(**session_data)
    
    async def get_metrics(self) -> Dict[str, Any]:
        """Get session management metrics"""
        session_keys = await state_manager.get_keys_by_tag('session')
        conversation_keys = await state_manager.get_keys_by_tag('conversation')
        temp_keys = await state_manager.get_keys_by_tag('temporary')
        
        return {
            'active_sessions': len(session_keys),
            'active_conversations': len(conversation_keys),
            'temporary_states': len(temp_keys),
            'default_session_ttl_minutes': self.default_ttl / 60,
            'conversation_ttl_minutes': self.conversation_ttl / 60
        }


# Global session manager instance
redis_session_manager = RedisSessionManager()


# Migration utilities for existing session handlers

async def migrate_context_data_to_redis(user_id: int, context_data: Dict[str, Any]) -> str:
    """
    Migrate existing telegram context.user_data to Redis session
    
    Args:
        user_id: Telegram user ID
        context_data: Current context.user_data dictionary
        
    Returns:
        str: New session ID
    """
    # Extract conversation flow information
    conversation_flow = None
    current_step = None
    
    # Detect conversation flow from context data
    flow_indicators = {
        'escrow': ['escrow_data', 'pending_escrow_id', 'escrow_creation_step'],
        'wallet': ['wallet_data', 'wallet_operation', 'active_cashout'],
        'exchange': ['exchange_data', 'exchange_session_id', 'rate_locked_until'],
        'contact': ['contact_data', 'active_contact', 'contact_verification_step']
    }
    
    for flow, indicators in flow_indicators.items():
        if any(indicator in context_data for indicator in indicators):
            conversation_flow = flow
            break
    
    # Extract current step information
    step_keys = [
        'conversation_step', 'current_flow', 'escrow_creation_step',
        'wallet_operation', 'contact_verification_step'
    ]
    
    for key in step_keys:
        if key in context_data:
            current_step = str(context_data[key])
            break
    
    # Create Redis session
    session_id = await redis_session_manager.create_session(
        user_id=user_id,
        conversation_flow=conversation_flow,
        initial_data=context_data
    )
    
    # Set current step if detected
    if current_step:
        await redis_session_manager.update_session(
            session_id,
            current_step=current_step
        )
    
    logger.info(
        f"📦 Migrated context data to Redis session {session_id} "
        f"for user {user_id} (flow: {conversation_flow}, step: {current_step})"
    )
    
    return session_id


async def clear_legacy_session_contamination(user_id: int):
    """
    Clear all session contamination for a user across all Redis session types
    This replaces the existing session_state_manager functionality
    """
    logger.info(f"🧹 Clearing all Redis session data for user {user_id}")
    
    # Clear all session types
    destroyed_sessions = await redis_session_manager.destroy_user_sessions(user_id)
    conversation_cleared = await redis_session_manager.clear_conversation_state(user_id)
    temp_cleared = await redis_session_manager.clear_temporary_state(user_id)
    user_state_cleared = await redis_session_manager.clear_user_state(user_id)
    
    logger.info(
        f"🧹 Session cleanup for user {user_id}: "
        f"{destroyed_sessions} sessions, "
        f"conversations: {conversation_cleared}, "
        f"temporary: {temp_cleared}, "
        f"user_state: {user_state_cleared}"
    )