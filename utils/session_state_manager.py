"""
Session State Manager - Phase 1 Critical Fix
Centralized session state cleanup to prevent cross-conversation contamination
"""

import logging
from typing import Dict, Optional, List, Set
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

class SessionStateManager:
    """
    Centralized session state management
    Prevents state contamination between different conversation flows
    """
    
    def __init__(self):
        # Track active sessions per user
        self._active_sessions: Dict[int, Set[str]] = {}
        
    async def clear_user_state(self, user_id: int, preserve: Optional[List[str]] = None) -> None:
        """
        Clear all conversation state for a user
        
        Args:
            user_id: Telegram user ID
            preserve: List of keys to preserve (e.g., ['user_preferences', 'admin_status'])
        """
        preserve = preserve or ['user_preferences', 'admin_status', 'verified_email', 'verified_phone']
        
        # This will be called with context when available
        logger.info(f"🧹 Clearing session state for user {user_id} (preserving: {preserve})")
        
        # Mark sessions as cleared
        if user_id in self._active_sessions:
            self._active_sessions[user_id].clear()
            
    async def clear_context_state(self, context: ContextTypes.DEFAULT_TYPE, user_id: int, preserve: Optional[List[str]] = None) -> None:
        """
        Clear conversation state from context.user_data
        
        Args:
            context: Telegram context object
            user_id: Telegram user ID  
            preserve: List of keys to preserve
        """
        if not context.user_data:
            return
            
        preserve = preserve or ['user_preferences', 'admin_status', 'verified_email', 'verified_phone']
        
        # State keys that should be cleared to prevent contamination
        state_keys_to_clear = [
            # Conversation flow states
            'active_conversation',
            'conversation_step',
            'current_flow',
            
            # Exchange flow states  
            'exchange_data',
            'exchange_session_id',
            'exchange_amount',
            'exchange_currency',
            'rate_locked_until',
            
            # Escrow flow states
            'escrow_data', 
            'escrow_creation_step',
            'pending_escrow_id',
            'escrow_currency',
            'escrow_amount',
            
            # Wallet flow states
            'wallet_data',
            'wallet_operation',
            'pending_transaction',
            'cashout_data',
            'active_cashout',
            
            # Input expectation states
            'expecting_amount',
            'expecting_crypto_address',
            'expecting_bank_reference', 
            'expecting_hash_input',
            'expecting_funding_amount',
            'expecting_custom_amount',
            'expecting_seller_details',
            'expecting_phone_verification',
            
            # Chat and messaging states
            'active_chat_session',
            'in_dispute_chat',
            'chat_partner_id',
            'dispute_session_id',
            
            # Contact management states
            'contact_data',
            'active_contact',
            'contact_verification_step',
            
            # Temporary UI states
            'last_menu_message_id',
            'pending_callback_data',
            'temporary_message_ids',
            'edit_message_context',
            
            # Rate limiting states (clear periodically)
            'last_command_time',
            'command_count',
        ]
        
        # Clear specified state keys
        cleared_keys = []
        for key in state_keys_to_clear:
            if key in context.user_data and key not in preserve:
                context.user_data.pop(key, None)
                cleared_keys.append(key)
                
        if cleared_keys:
            logger.debug(f"🧹 Cleared {len(cleared_keys)} state keys for user {user_id}: {cleared_keys}")
            
        # Clear universal session manager sessions
        await self._clear_universal_sessions(user_id)
        
        # Update tracking
        self._active_sessions[user_id] = set()
        
    async def _clear_universal_sessions(self, user_id: int) -> None:
        """Clear universal session manager sessions for user"""
        try:
            from utils.universal_session_manager import universal_session_manager
            user_session_ids = universal_session_manager.get_user_session_ids(user_id)
            if user_session_ids:
                logger.debug(f"🧹 Clearing {len(user_session_ids)} universal sessions for user {user_id}")
                for session_id in user_session_ids:
                    universal_session_manager.terminate_session(session_id, "state_manager_cleanup")
        except Exception as e:
            logger.warning(f"Could not clear universal sessions for user {user_id}: {e}")
            
    async def start_conversation(self, user_id: int, conversation_type: str, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        Start a new conversation - clears previous state and sets up new conversation
        
        Args:
            user_id: Telegram user ID
            conversation_type: Type of conversation starting (e.g., 'escrow_creation', 'exchange_flow')
            context: Telegram context object
        """
        # Clear existing state first
        await self.clear_context_state(context, user_id)
        
        # Set up new conversation state
        if not context.user_data:
            context.user_data = {}
            
        context.user_data['active_conversation'] = conversation_type
        context.user_data['conversation_start_time'] = int(time.time())
        
        # Track active session
        if user_id not in self._active_sessions:
            self._active_sessions[user_id] = set()
        self._active_sessions[user_id].add(conversation_type)
        
        logger.info(f"🚀 Started conversation '{conversation_type}' for user {user_id}")
        
    def get_active_conversations(self, user_id: int) -> Set[str]:
        """Get set of active conversation types for user"""
        return self._active_sessions.get(user_id, set())
        
    async def end_conversation(self, user_id: int, conversation_type: str, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        End a specific conversation type
        
        Args:
            user_id: Telegram user ID  
            conversation_type: Type of conversation to end
            context: Telegram context object
        """
        # Remove from active sessions
        if user_id in self._active_sessions:
            self._active_sessions[user_id].discard(conversation_type)
            
        # Clear conversation-specific state
        if context.user_data:
            if context.user_data.get('active_conversation') == conversation_type:
                context.user_data.pop('active_conversation', None)
                
        logger.info(f"🏁 Ended conversation '{conversation_type}' for user {user_id}")

# Import time for timestamps
import time

# Global session state manager instance  
session_state_manager = SessionStateManager()