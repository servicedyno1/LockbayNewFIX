"""
Unified Text Router - Central text message routing based on user state
Eliminates handler conflicts by routing messages to correct handler based on active sessions
"""

import logging
from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters

from utils.route_guard import RouteGuard, OnboardingProtection
from utils.handler_decorators import audit_handler
from utils.comprehensive_audit_logger import AuditEventType

logger = logging.getLogger(__name__)


class UnifiedTextRouter:
    """Central router for all text messages - prevents handler conflicts"""
    
    @staticmethod
    @audit_handler(AuditEventType.USER_INTERACTION, "unified_text_routing")
    async def route_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Route text messages to appropriate handler based on user state"""
        user = update.effective_user
        message = update.message
        
        if not user or not message or not message.text:
            logger.warning("🚫 Unified router: Invalid message or user")
            return
        
        user_id = user.id
        text = message.text.strip()
        
        # CRITICAL: Check if user is blocked before routing any message
        try:
            from utils.fast_user_lookup import async_fast_user_lookup
            from database import get_async_session
            from sqlalchemy import text as sql_text
            
            async with get_async_session() as session:
                # FIRST: Check permanent blocklist (blocked_telegram_ids table)
                blocklist_result = await session.execute(
                    sql_text("SELECT id FROM blocked_telegram_ids WHERE telegram_id = :telegram_id"),
                    {"telegram_id": user_id}
                )
                if blocklist_result.scalar():
                    logger.critical(f"🚫🚫🚫 BLOCKLIST_VIOLATION: User {user_id} on PERMANENT BLOCKLIST attempted message: '{text[:30]}...'")
                    try:
                        await message.reply_text("❌ Your account has been permanently suspended.")
                    except:
                        pass
                    return
                
                # SECOND: Check user's is_blocked flag (for existing users)
                db_user = await async_fast_user_lookup(str(user_id), session=session)
                
                if db_user and db_user.is_blocked:
                    logger.warning(f"🚫 BLOCKED_MESSAGE: User {user_id} attempted to send message: '{text[:30]}...'")
                    try:
                        await message.reply_text("❌ Your account has been suspended and you cannot access this service.")
                    except:
                        pass
                    return
        except Exception as e:
            logger.debug(f"Blocking check error in text router: {e}")
        
        logger.info(f"🎯 UNIFIED ROUTER: Processing text '{text[:30]}...' from user {user_id}")
        
        try:
            # CRITICAL FIX: Check for exclusive conversation states BEFORE admin routing
            # This ensures admins can complete OTP verification and other critical flows
            exclusive_wallet_states = [
                'verifying_crypto_otp', 'verifying_ngn_otp', 
                'entering_crypto_address', 'entering_crypto_details',
                'selecting_amount', 'selecting_amount_crypto', 'selecting_amount_ngn',
                'adding_bank_account_number', 'entering_custom_amount', 'entering_withdraw_address'
            ]
            
            # BUGFIX: Get wallet state from Redis-backed session, not context.user_data
            from handlers.wallet_direct import get_wallet_state
            wallet_state = await get_wallet_state(user_id, context)
            is_in_exclusive_state = wallet_state in exclusive_wallet_states
            
            # CRITICAL FIX: Check for admin broadcast state BEFORE support reply detection
            from utils.admin_security import is_admin_silent
            if is_admin_silent(user_id):
                from handlers.admin_broadcast_direct import get_admin_broadcast_state
                broadcast_state = await get_admin_broadcast_state(user_id)
                if broadcast_state == "composing":
                    logger.info(f"📢 BROADCAST_ACTIVE: Admin {user_id} is composing broadcast - routing to broadcast handler ONLY (skipping ALL other checks)")
                    from handlers.admin_broadcast import handle_broadcast_message
                    await handle_broadcast_message(update, context)
                    return  # CRITICAL: Stop here completely, do NOT proceed to any other checks
            
            # PRIORITY CHECK: Admin support reply detection (only if NOT in exclusive state AND NOT composing broadcast)
            if not is_in_exclusive_state:
                from handlers.admin_support import handle_admin_reply_message
                admin_reply_handled = await handle_admin_reply_message(update, context)
                if admin_reply_handled:
                    logger.info(f"👨‍💼 ADMIN REPLY: Message from admin {user_id} handled by support system")
                    return
            else:
                logger.info(f"🔒 EXCLUSIVE_STATE: User {user_id} in state '{wallet_state}' - skipping admin routing")
            
            # CRITICAL FIX: If user is in exclusive wallet state, route DIRECTLY to wallet
            # This prevents stale support sessions from intercepting cashout address/OTP inputs
            if is_in_exclusive_state:
                logger.info(f"🔒 EXCLUSIVE_WALLET_ROUTE: User {user_id} in wallet state '{wallet_state}' - routing directly to wallet, bypassing RouteGuard")
                await UnifiedTextRouter._route_to_wallet(update, context, text)
                return
            
            # Get routing decision based on user state
            logger.info(f"🔍 TEXT ROUTER: About to call RouteGuard.get_routing_decision for user {user_id}")
            route_decision = await RouteGuard.get_routing_decision(user_id, context, text)
            logger.info(f"🔍 TEXT ROUTER: Got route_decision='{route_decision}' for user {user_id}")
            
            # Route to appropriate handler
            if route_decision == 'escrow':
                logger.info(f"🏪 ROUTE: user {user_id} → escrow direct handler")
                await UnifiedTextRouter._route_to_escrow(update, context, text)
                return
                
            elif route_decision == 'onboarding':
                logger.info(f"📧 ROUTE: user {user_id} → onboarding_router")
                await UnifiedTextRouter._route_to_onboarding(update, context, text)
                
            elif route_decision == 'rating':
                logger.info(f"⭐ ROUTE: user {user_id} → rating_direct")
                await UnifiedTextRouter._route_to_rating(update, context, text)
                
            elif route_decision == 'dispute':
                logger.info(f"⚖️ ROUTE: user {user_id} → dispute_handler")
                await UnifiedTextRouter._route_to_dispute(update, context, text)
                
            elif route_decision == 'wallet':
                logger.info(f"💰 ROUTE: user {user_id} → wallet_direct")
                await UnifiedTextRouter._route_to_wallet(update, context, text)
                
            elif route_decision == 'messages_hub':
                logger.info(f"💬 ROUTE: user {user_id} → messages_hub")
                await UnifiedTextRouter._route_to_messages_hub(update, context, text)
                
            elif route_decision == 'support':
                logger.info(f"🆘 ROUTE: user {user_id} → support_chat")
                await UnifiedTextRouter._route_to_support(update, context, text)
                
            elif route_decision == 'admin':
                logger.info(f"👨‍💼 ROUTE: user {user_id} → admin (skipping - admin_broadcast_text_router will handle)")
                return
                
            else:
                logger.info(f"❓ ROUTE: user {user_id} → fallback (main menu)")
                await UnifiedTextRouter._handle_fallback(update, context, text)
        
        except Exception as e:
            logger.error(f"🚨 CRITICAL ERROR in unified text router for user {user_id}: {e}")
            logger.error(f"🚨 EXCEPTION TYPE: {type(e).__name__}")
            logger.error(f"🚨 EXCEPTION ARGS: {e.args}")
            import traceback
            logger.error(f"🚨 FULL TRACEBACK: {traceback.format_exc()}")
            # Fallback to onboarding for safety
            await UnifiedTextRouter._route_to_onboarding(update, context, text)
    
    @staticmethod
    async def _route_to_onboarding(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
        """Route to onboarding handler"""
        try:
            # Import here to avoid circular imports
            from handlers.onboarding_router import onboarding_text_handler
            logger.debug(f"🔄 Routing to onboarding handler for text: '{text[:20]}...'")
            await onboarding_text_handler(update, context)
        except Exception as e:
            logger.error(f"Error routing to onboarding: {e}")
            if update.message:
                await update.message.reply_text(
                    "⚠️ **Registration Error**\n\n"
                    "There was an issue processing your registration.\n"
                    "Please try /start to restart the process."
                )
    
    @staticmethod
    async def _route_to_escrow(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
        """Route to escrow direct handler"""
        try:
            # Import here to avoid circular imports
            from handlers.escrow_direct import route_text_message_to_escrow_flow
            logger.info(f"🔄 Routing to escrow direct handler for text: '{text[:20]}...'")
            result = await route_text_message_to_escrow_flow(update, context)
            if not result:
                logger.warning(f"Escrow handler did not process message: '{text[:20]}...'")
        except Exception as e:
            logger.error(f"Error routing to escrow handler: {e}")
            if update.message:
                await update.message.reply_text(
                    "⚠️ **Escrow Error**\n\n"
                    "There was an issue processing your escrow input.\n"
                    "Please try again or contact support if the problem persists."
                )

    @staticmethod
    async def _route_to_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
        """Route to wallet handler"""
        try:
            # Import here to avoid circular imports  
            from handlers.wallet_direct import handle_wallet_text_input
            logger.debug(f"🔄 Routing to wallet handler for text: '{text[:20]}...'")
            await handle_wallet_text_input(update, context)
        except Exception as e:
            logger.error(f"Error routing to wallet: {e}")
            if update.message:
                await update.message.reply_text(
                    "⚠️ **Wallet Error**\n\n"
                    "There was an issue processing your wallet operation.\n"
                    "Please try again or contact support if the problem persists."
                )
    
    @staticmethod
    async def _route_to_rating(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
        """Route to rating handler - CRITICAL FIX for rating feedback routing"""
        try:
            # Import here to avoid circular imports
            from handlers.user_rating_direct import direct_handle_rating_comment
            logger.info(f"⭐ Routing to rating handler for text: '{text[:20]}...'")
            await direct_handle_rating_comment(update, context)
        except Exception as e:
            logger.error(f"Error routing to rating handler: {e}")
            if update.message:
                await update.message.reply_text(
                    "⚠️ **Rating Error**\n\n"
                    "There was an issue processing your rating feedback.\n"
                    "Please try again or contact support if the problem persists."
                )
    
    @staticmethod
    async def _route_to_dispute(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
        """Route to dispute handler - handles dispute chat messages"""
        try:
            # Import here to avoid circular imports
            from handlers.dispute_chat_direct import dispute_text_router
            logger.info(f"⚖️ Routing to dispute handler for text: '{text[:20]}...'")
            await dispute_text_router(update, context)
        except Exception as e:
            logger.error(f"Error routing to dispute handler: {e}")
            if update.message:
                await update.message.reply_text(
                    "⚠️ **Dispute Error**\n\n"
                    "There was an issue processing your dispute message.\n"
                    "Please try again or contact support if the problem persists."
                )
    
    @staticmethod
    async def _route_to_messages_hub(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
        """Route to messages hub"""
        try:
            # Import here to avoid circular imports
            from handlers.messages_hub import handle_message_input
            logger.debug(f"🔄 Routing to messages hub for text: '{text[:20]}...'")
            await handle_message_input(update, context)
        except Exception as e:
            logger.error(f"Error routing to messages hub: {e}")
            if update.message:
                await update.message.reply_text(
                    "⚠️ **Message Error**\n\n"
                    "There was an issue processing your message.\n"
                    "Please try again or return to the main menu."
                )
    
    @staticmethod
    async def _route_to_support(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
        """Route to support chat handler - ConversationHandler will process the message"""
        # CRITICAL FIX: DO NOT call handle_support_message_input here!
        # The ConversationHandler's MessageHandler will process support messages automatically
        # Calling it here would result in duplicate message processing (text_router + ConversationHandler)
        logger.info(f"🆘 Support session detected for text: '{text[:20]}...' - letting ConversationHandler handle it")
        # Just return - ConversationHandler will handle the text message in SUPPORT_CHAT_VIEW state
        return
    
    @staticmethod
    async def _handle_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
        """Handle messages when no specific session is active"""
        user = update.effective_user
        if not user:
            return
        
        logger.info(f"❓ FALLBACK: No active session for user {user.id}, message: '{text[:30]}...'")
        
        # Check if user is trying to do something that requires onboarding
        if any(indicator in text.lower() for indicator in ['@', '.com', '.org', '.net']):
            # Looks like email input - check if they need onboarding
            try:
                from services.onboarding_service import OnboardingService
                current_step = await OnboardingService.get_current_step(user.id)
                if current_step != 'done':
                    logger.info(f"📧 FALLBACK→ONBOARDING: Email-like input from unverified user {user.id}")
                    await UnifiedTextRouter._route_to_onboarding(update, context, text)
                    return
            except Exception as e:
                logger.error(f"Error checking onboarding step in fallback: {e}")
        
        # Default fallback - suggest /start or main menu
        if update.message:
            await update.message.reply_text(
                "🤔 **I'm not sure what you're trying to do**\n\n"
                "💡 **Try:**\n"
                "• /start - If you're new or want to restart\n"
                "• /menu - Go to main menu\n"
                "• /help - Get help\n\n"
                "Or use the buttons in your last message for navigation."
            )


# Create the message handler for registration
def create_unified_text_handler() -> MessageHandler:
    """Create the unified text message handler"""
    return MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        UnifiedTextRouter.route_text_message,
        block=True  # Block to prevent concurrent processing
    )