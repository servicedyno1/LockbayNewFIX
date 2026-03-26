"""
Utility functions for conversation handler state isolation
"""
import logging

logger = logging.getLogger(__name__)

def check_conversation_conflict(context, current_conversation: str):
    """
    Check if there's an active conversation conflict and handle it gracefully
    
    Args:
        context: Telegram context object
        current_conversation: Name of the current conversation ("exchange", "escrow", "wallet", etc.)
        
    Returns:
        bool: True if should continue with current conversation, False if should end
    """
    if not context.user_data:
        return True
        
    active_conversation = context.user_data.get("active_conversation")
    
    # If no active conversation is set, allow this one to proceed
    if not active_conversation:
        context.user_data["active_conversation"] = current_conversation
        return True
        
    # If it's the same conversation, continue
    if active_conversation == current_conversation:
        return True
        
    # Different conversation is active - end this one gracefully
    logger.info(f"🔄 User input routed to {active_conversation} conversation - ending {current_conversation} processing")
    return False

def clear_conflicting_conversations(context, current_conversation: str):
    """
    Clear any conflicting conversation data when starting a new conversation
    
    Args:
        context: Telegram context object  
        current_conversation: Name of the conversation being started
    """
    if not context.user_data:
        # Fix: Cannot assign new value to user_data
        if hasattr(context, 'user_data') and context.user_data is not None:
            context.user_data.clear()
        # If user_data is None, work with existing state
        
    # Clear specific conversation data that might conflict
    conflicts_cleared = []
    
    if current_conversation != "exchange" and "exchange_data" in context.user_data:
        context.user_data.pop("exchange_data", None)
        conflicts_cleared.append("exchange")
        
    if current_conversation != "escrow" and "escrow_data" in context.user_data:
        context.user_data.pop("escrow_data", None)
        conflicts_cleared.append("escrow")
        
    if current_conversation != "wallet" and "wallet_data" in context.user_data:
        context.user_data.pop("wallet_data", None)
        conflicts_cleared.append("wallet")
        
    if current_conversation != "onboarding" and "registering" in context.user_data:
        context.user_data.pop("registering", None)
        conflicts_cleared.append("onboarding")
        
    if conflicts_cleared:
        logger.info(f"🔄 Cleared conflicting conversations {conflicts_cleared} for {current_conversation} start")
        
    # Mark this conversation as active
    context.user_data["active_conversation"] = current_conversation