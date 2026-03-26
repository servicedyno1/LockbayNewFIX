"""
Session Migration Helper
Utility to migrate handlers from context.user_data to Redis-backed session management
Provides compatibility layer during transition period
"""

import logging
from typing import Any, Dict, List
from telegram.ext import ContextTypes

from utils.redis_session_foundation import RedisSessionManager

logger = logging.getLogger(__name__)


class SessionMigrationHelper:
    """
    Helper class to gradually migrate from context.user_data to Redis-backed sessions
    Provides compatibility layer and migration utilities
    """
    
    def __init__(self):
        self.redis_session_manager = RedisSessionManager()
        # Track migration status per handler
        self.migrated_handlers = set()
        
    async def get_session_data(self, user_id: int, context: ContextTypes.DEFAULT_TYPE, key: str = None) -> Dict[str, Any]:
        """
        Get session data with fallback to context.user_data during migration
        
        Args:
            user_id: Telegram user ID
            context: Telegram context object
            key: Specific key to retrieve (if None, returns all session data)
            
        Returns:
            Dict containing session data
        """
        try:
            # Try Redis session first
            session = await self.redis_session_manager.get_user_session(user_id)
            if session and session.data:
                logger.debug(f"📡 Retrieved Redis session data for user {user_id}")
                if key:
                    return session.data.get(key, {})
                return session.data
            
            # Fallback to context.user_data if Redis session not found
            if context and context.user_data:
                logger.debug(f"💾 Fallback to context.user_data for user {user_id}")
                if key:
                    return context.user_data.get(key, {})
                return context.user_data
            
            return {}
            
        except Exception as e:
            logger.error(f"❌ Error retrieving session data for user {user_id}: {e}")
            # Final fallback to context.user_data
            if context and context.user_data:
                if key:
                    return context.user_data.get(key, {})
                return context.user_data
            return {}
    
    async def set_session_data(
        self, 
        user_id: int, 
        context: ContextTypes.DEFAULT_TYPE, 
        data: Dict[str, Any], 
        key: str = None,
        conversation_flow: str = None
    ) -> bool:
        """
        Set session data in both Redis and context.user_data during migration
        
        Args:
            user_id: Telegram user ID
            context: Telegram context object
            data: Data to store
            key: Specific key to store under (if None, updates entire session)
            conversation_flow: Current conversation flow
            
        Returns:
            bool: True if successful
        """
        try:
            # Update Redis session
            session = await self.redis_session_manager.get_user_session(user_id)
            if not session:
                # Create new session
                session_id = await self.redis_session_manager.create_session(
                    user_id=user_id,
                    conversation_flow=conversation_flow,
                    initial_data=data if not key else {key: data}
                )
                logger.info(f"🆕 Created new Redis session {session_id} for user {user_id}")
            else:
                # Update existing session
                if key:
                    session.data[key] = data
                    update_data = session.data
                else:
                    update_data = data
                
                success = await self.redis_session_manager.update_session(
                    session.session_id,
                    data_updates=update_data,
                    conversation_flow=conversation_flow
                )
                if not success:
                    logger.warning(f"⚠️ Failed to update Redis session for user {user_id}")
            
            # Also update context.user_data for backward compatibility
            if context:
                if not context.user_data:
                    context.user_data = {}
                
                if key:
                    context.user_data[key] = data
                else:
                    context.user_data.update(data)
                
                logger.debug(f"💾 Updated context.user_data for user {user_id}")
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Error setting session data for user {user_id}: {e}")
            # Fallback to context.user_data only
            if context:
                if not context.user_data:
                    context.user_data = {}
                
                if key:
                    context.user_data[key] = data
                else:
                    context.user_data.update(data)
            return False
    
    async def clear_session_data(self, user_id: int, context: ContextTypes.DEFAULT_TYPE, preserve: List[str] = None) -> None:
        """
        Clear session data from both Redis and context.user_data
        
        Args:
            user_id: Telegram user ID
            context: Telegram context object
            preserve: List of keys to preserve
        """
        preserve = preserve or ['user_preferences', 'admin_status', 'verified_email', 'verified_phone']
        
        try:
            # Clear Redis session
            session = await self.redis_session_manager.get_user_session(user_id)
            if session:
                await self.redis_session_manager.destroy_session(session.session_id)
                logger.info(f"🧹 Cleared Redis session for user {user_id}")
            
            # Clear context.user_data
            if context and context.user_data:
                # Preserve specified keys
                preserved_data = {k: v for k, v in context.user_data.items() if k in preserve}
                context.user_data.clear()
                context.user_data.update(preserved_data)
                logger.debug(f"🧹 Cleared context.user_data for user {user_id} (preserved: {list(preserved_data.keys())})")
            
        except Exception as e:
            logger.error(f"❌ Error clearing session data for user {user_id}: {e}")
    
    async def migrate_context_to_redis(self, user_id: int, context: ContextTypes.DEFAULT_TYPE, conversation_flow: str = None) -> str:
        """
        Migrate existing context.user_data to Redis session
        
        Args:
            user_id: Telegram user ID
            context: Telegram context object
            conversation_flow: Current conversation flow
            
        Returns:
            str: Session ID of created Redis session
        """
        try:
            if not context or not context.user_data:
                logger.debug(f"No context.user_data to migrate for user {user_id}")
                return await self.redis_session_manager.create_session(user_id, conversation_flow)
            
            # Create Redis session with context data
            session_id = await self.redis_session_manager.create_session(
                user_id=user_id,
                conversation_flow=conversation_flow,
                initial_data=dict(context.user_data)  # Copy context data
            )
            
            logger.info(f"📤 Migrated context.user_data to Redis session {session_id} for user {user_id}")
            return session_id
            
        except Exception as e:
            logger.error(f"❌ Error migrating context to Redis for user {user_id}: {e}")
            # Create empty session as fallback
            return await self.redis_session_manager.create_session(user_id, conversation_flow)
    
    def mark_handler_migrated(self, handler_name: str) -> None:
        """
        Mark a handler as migrated to Redis-backed sessions
        
        Args:
            handler_name: Name of the handler that has been migrated
        """
        self.migrated_handlers.add(handler_name)
        logger.info(f"✅ Handler '{handler_name}' marked as migrated to Redis sessions")
    
    def is_handler_migrated(self, handler_name: str) -> bool:
        """
        Check if a handler has been migrated to Redis-backed sessions
        
        Args:
            handler_name: Name of the handler to check
            
        Returns:
            bool: True if handler has been migrated
        """
        return handler_name in self.migrated_handlers


# Global migration helper instance
session_migration_helper = SessionMigrationHelper()
