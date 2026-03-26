"""Conversation Timeout Protection and State Management"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Optional, Callable
from functools import wraps
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes, ConversationHandler
from utils.navigation import safe_navigation_fallback

logger = logging.getLogger(__name__)


# BLOCKING ENFORCEMENT: Centralized user blocking check
async def block_handler_wrapper(handler_func: Callable) -> Callable:
    """
    Wrapper that checks if user is blocked before executing any handler.
    Applied to all handlers to prevent blocked users from accessing any screen.
    """
    @wraps(handler_func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user:
            return None
        
        try:
            from utils.fast_user_lookup import async_fast_user_lookup
            from database import get_async_session
            from sqlalchemy import text as sql_text
            
            # Quick blocking check
            async with get_async_session() as session:
                # FIRST: Check permanent blocklist table
                blocklist_result = await session.execute(
                    sql_text("SELECT id FROM blocked_telegram_ids WHERE telegram_id = :telegram_id"),
                    {"telegram_id": user.id}
                )
                if blocklist_result.scalar():
                    logger.critical(f"🚫🚫🚫 BLOCKLIST_VIOLATION: User {user.id} ({user.username}) on PERMANENT BLOCKLIST tried to access handler")
                    try:
                        if update.callback_query:
                            await update.callback_query.answer("❌ Account permanently suspended.", show_alert=True)
                        elif update.message:
                            await update.message.reply_text("❌ Your account has been permanently suspended.")
                    except Exception as e:
                        logger.error(f"Error notifying blocklisted user: {e}")
                    return None
                
                # SECOND: Check user's is_blocked flag
                db_user = await async_fast_user_lookup(str(user.id), session=session)
                
                if db_user and db_user.is_blocked:
                    logger.warning(f"🚫 BLOCKED USER ATTEMPT: User {user.id} ({user.username}) tried to access handler")
                    
                    # Send blocking message to user
                    try:
                        if update.callback_query:
                            await update.callback_query.answer(
                                "❌ Your account has been suspended.",
                                show_alert=True
                            )
                        elif update.message:
                            await update.message.reply_text(
                                "❌ Your account has been suspended and you cannot access this service."
                            )
                    except Exception as e:
                        logger.error(f"Error notifying blocked user: {e}")
                    
                    return None
        except Exception as e:
            logger.error(f"Error in blocking check: {e}")
        
        # User not blocked, proceed with handler
        return await handler_func(update, context)
    
    return wrapper


def create_blocking_aware_handler(handler_func: Callable) -> Callable:
    """
    Creates a handler that checks blocking status before executing.
    This is a non-async wrapper for direct handler registration (callbacks).
    Raises BlockedUserException to prevent handler execution.
    """
    async def blocking_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user:
            return None
        
        user_id = user.id
        
        try:
            from utils.fast_user_lookup import async_fast_user_lookup
            from database import get_async_session
            from sqlalchemy import text as sql_text
            import asyncio
            
            # Check blocking status FIRST before ANY handler execution
            async with get_async_session() as session:
                # FIRST: Check permanent blocklist table (for deleted users)
                blocklist_result = await session.execute(
                    sql_text("SELECT id FROM blocked_telegram_ids WHERE telegram_id = :telegram_id"),
                    {"telegram_id": user_id}
                )
                if blocklist_result.scalar():
                    logger.critical(f"🚫🚫🚫 BLOCKLIST_CALLBACK_REJECTED: User {user_id} ({user.username}) on PERMANENT BLOCKLIST attempted callback")
                    try:
                        if update.callback_query:
                            await update.callback_query.answer("❌ Account permanently suspended.", show_alert=True)
                        elif update.message:
                            await update.message.reply_text("❌ Your account has been permanently suspended.")
                    except Exception as e:
                        logger.error(f"Failed to send blocklist message: {e}")
                    raise BlockedUserException(f"User {user_id} is on permanent blocklist")
                
                # SECOND: Check user's is_blocked flag (for existing users)
                db_user = await async_fast_user_lookup(str(user_id), session=session)
                
                if db_user and db_user.is_blocked:
                    logger.critical(f"🚫🚫🚫 BLOCKED_CALLBACK_REJECTED: User {user_id} ({user.username}) attempted callback")
                    logger.critical("🚫🚫🚫 BLOCKING_ENFORCEMENT: Sending suspension message and blocking handler execution")
                    try:
                        if update.callback_query:
                            await update.callback_query.answer("❌ Account suspended.", show_alert=True)
                        elif update.message:
                            await update.message.reply_text("❌ Your account has been suspended.")
                    except Exception as e:
                        logger.error(f"Failed to send suspension message: {e}")
                    
                    # CRITICAL: Raise exception to STOP execution completely
                    logger.critical(f"🚫🚫🚫 BLOCKING_ENFORCEMENT: Raising BlockedUserException to prevent {handler_func.__name__} execution")
                    raise BlockedUserException(f"User {user_id} is blocked")
                else:
                    logger.debug(f"✅ User {user_id} is NOT blocked, allowing {handler_func.__name__} to execute")
        except BlockedUserException:
            # CRITICAL: Re-raise to prevent handler execution - this MUST not be caught again
            logger.critical(f"🚫🚫🚫 BLOCKING_ENFORCEMENT: Re-raising BlockedUserException, {handler_func.__name__} will NOT execute")
            raise
        except Exception as e:
            logger.error(f"Blocking check error (will allow handler to proceed): {e}")
        
        # CRITICAL: Only execute if we reach this point (user is NOT blocked)
        logger.info(f"✅ HANDLER_EXECUTION_ALLOWED: Starting {handler_func.__name__} for user {user_id}")
        try:
            if asyncio.iscoroutinefunction(handler_func):
                return await handler_func(update, context)
            else:
                return handler_func(update, context)
        except BlockedUserException:
            # If somehow the exception escapes from handler, log it and re-raise
            logger.critical(f"🚫 UNEXPECTED: BlockedUserException escaped from handler {handler_func.__name__}")
            raise
    
    return blocking_check


class BlockedUserException(Exception):
    """Exception raised when a blocked user tries to use the bot"""
    pass


def create_blocking_aware_command_handler(handler_func: Callable) -> Callable:
    """
    Creates a command handler that checks blocking status before executing.
    Specifically designed for /command handlers.
    Raises BlockedUserException to prevent handler execution.
    """
    async def blocking_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user:
            return None
        
        user_id = user.id
        command_text = update.message.text if update.message else 'unknown'
        
        try:
            from utils.fast_user_lookup import async_fast_user_lookup
            from database import get_async_session
            from sqlalchemy import text as sql_text
            import asyncio
            
            # Check blocking status FIRST before ANY handler execution
            async with get_async_session() as session:
                # FIRST: Check permanent blocklist table (for deleted users)
                blocklist_result = await session.execute(
                    sql_text("SELECT id FROM blocked_telegram_ids WHERE telegram_id = :telegram_id"),
                    {"telegram_id": user_id}
                )
                if blocklist_result.scalar():
                    logger.critical(f"🚫🚫🚫 BLOCKLIST_COMMAND_REJECTED: User {user_id} ({user.username}) on PERMANENT BLOCKLIST attempted: {command_text}")
                    try:
                        await update.message.reply_text("❌ Your account has been permanently suspended.")
                    except Exception as e:
                        logger.error(f"Failed to send blocklist message: {e}")
                    raise BlockedUserException(f"User {user_id} is on permanent blocklist")
                
                # SECOND: Check user's is_blocked flag (for existing users)
                db_user = await async_fast_user_lookup(str(user_id), session=session)
                
                if db_user and db_user.is_blocked:
                    logger.critical(f"🚫🚫🚫 BLOCKED_COMMAND_REJECTED: User {user_id} ({user.username}) attempted: {command_text}")
                    logger.critical("🚫🚫🚫 BLOCKING_ENFORCEMENT: Sending suspension message and blocking handler execution")
                    try:
                        await update.message.reply_text("❌ Your account has been suspended and you cannot access this service.")
                    except Exception as e:
                        logger.error(f"Failed to send suspension message: {e}")
                    
                    # CRITICAL: Raise exception to STOP execution completely
                    logger.critical(f"🚫🚫🚫 BLOCKING_ENFORCEMENT: Raising BlockedUserException to prevent {handler_func.__name__} execution")
                    raise BlockedUserException(f"User {user_id} is blocked from using {command_text}")
                else:
                    logger.debug(f"✅ User {user_id} is NOT blocked, allowing {handler_func.__name__} to execute")
        except BlockedUserException:
            # CRITICAL: Re-raise to prevent handler execution - this MUST not be caught again
            logger.critical(f"🚫🚫🚫 BLOCKING_ENFORCEMENT: Re-raising BlockedUserException, {handler_func.__name__} will NOT execute")
            raise
        except Exception as e:
            logger.error(f"Blocking check error (will allow handler to proceed): {e}")
        
        # CRITICAL: Only execute if we reach this point (user is NOT blocked)
        logger.info(f"✅ HANDLER_EXECUTION_ALLOWED: Starting {handler_func.__name__} for user {user_id}")
        try:
            if asyncio.iscoroutinefunction(handler_func):
                return await handler_func(update, context)
            else:
                return handler_func(update, context)
        except BlockedUserException:
            # If somehow the exception escapes from handler, log it and re-raise
            logger.critical(f"🚫 UNEXPECTED: BlockedUserException escaped from handler {handler_func.__name__}")
            raise
    
    return blocking_check


# Global conversation timeout tracking
CONVERSATION_TIMEOUTS: Dict[int, datetime] = {}
TIMEOUT_DURATION = timedelta(minutes=20)  # OPTIMIZED: Reduced from 30 to 20 minutes to free memory faster

# Enhanced navigation protection
USER_START_TIMES: Dict[int, list] = {}  # Track rapid /start commands
USER_NAVIGATION_PATHS: Dict[int, list] = {}  # Track navigation patterns
RAPID_START_THRESHOLD = 4  # OPTIMIZED: Increased tolerance to reduce false positives for legitimate users
NAVIGATION_LOOP_THRESHOLD = 4  # OPTIMIZED: Reduced from 5 to 4 for faster loop detection


# CRITICAL FIX: Enhanced session state preservation for timeout recovery
SESSION_STATE_BACKUP: Dict[int, Dict] = {}  # Store session state before timeout

class ConversationTimeout:
    """Manages conversation timeouts and state protection"""

    @staticmethod
    def start_timeout(user_id: int) -> None:
        """Start timeout tracking for a user conversation"""
        CONVERSATION_TIMEOUTS[user_id] = datetime.now() + TIMEOUT_DURATION
        logger.info(f"Started conversation timeout for user {user_id}")

    @staticmethod
    def extend_timeout(user_id: int) -> None:
        """Extend timeout when user shows activity"""
        CONVERSATION_TIMEOUTS[user_id] = datetime.now() + TIMEOUT_DURATION
        logger.debug(f"Extended conversation timeout for user {user_id}")

    @staticmethod
    def clear_timeout(user_id: int) -> None:
        """Clear timeout when conversation ends normally"""
        CONVERSATION_TIMEOUTS.pop(user_id, None)
        # Also clear backup state when conversation completes successfully
        SESSION_STATE_BACKUP.pop(user_id, None)
        logger.info(f"Cleared conversation timeout for user {user_id}")

    @staticmethod
    def is_expired(user_id: int) -> bool:
        """Check if conversation has expired"""
        if user_id not in CONVERSATION_TIMEOUTS:
            return False
        return datetime.now() > CONVERSATION_TIMEOUTS[user_id]

    @staticmethod
    def get_remaining_time(user_id: int) -> Optional[timedelta]:
        """Get remaining time before timeout"""
        if user_id not in CONVERSATION_TIMEOUTS:
            return None
        remaining = CONVERSATION_TIMEOUTS[user_id] - datetime.now()
        return remaining if remaining.total_seconds() > 0 else timedelta(0)
    
    @staticmethod
    def backup_session_state(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        CRITICAL FIX: Backup important session state before timeout
        Allows recovery of escrow/exchange progress if user returns
        """
        try:
            state_backup = {}
            
            if hasattr(context, 'user_data') and context.user_data:
                # Backup critical data that should survive timeout
                critical_keys = [
                    'escrow_data', 'exchange_data', 'wallet_address', 
                    'payment_method', 'current_action', 'recovery_context'
                ]
                
                for key in critical_keys:
                    if key in context.user_data:
                        state_backup[key] = context.user_data[key]
                
                # Store backup with timestamp
                if state_backup:
                    SESSION_STATE_BACKUP[user_id] = {
                        'data': state_backup,
                        'backed_up_at': datetime.now(),
                        'timeout_reason': 'session_timeout'
                    }
                    logger.info(f"Backed up session state for user {user_id}: {list(state_backup.keys())}")
                    
        except Exception as e:
            logger.error(f"Failed to backup session state for user {user_id}: {e}")
    
    @staticmethod
    def restore_session_state(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
        """
        CRITICAL FIX: Restore session state after timeout recovery
        Returns True if state was restored, False if no backup exists
        """
        try:
            if user_id not in SESSION_STATE_BACKUP:
                return False
            
            backup = SESSION_STATE_BACKUP[user_id]
            backup_age = datetime.now() - backup['backed_up_at']
            
            # Only restore if backup is recent (within 2 hours)
            if backup_age.total_seconds() > 7200:  # 2 hours
                SESSION_STATE_BACKUP.pop(user_id, None)
                logger.info(f"Session backup expired for user {user_id}")
                return False
            
            # Restore backed up data
            if hasattr(context, 'user_data'):
                context.user_data.update(backup['data'])
                logger.info(f"Restored session state for user {user_id}: {list(backup['data'].keys())}")
                return True
                
        except Exception as e:
            logger.error(f"Failed to restore session state for user {user_id}: {e}")
        
        return False


async def timeout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    ENHANCED: Universal timeout handler with state preservation

    Args:
        update: Telegram update object
        context: Bot context

    Returns:
        ConversationHandler.END
    """
    user_id = update.effective_user.id if update.effective_user else 0
    logger.warning(f"Conversation timeout triggered for user {user_id}")

    # CRITICAL FIX: Backup session state before timeout
    ConversationTimeout.backup_session_state(user_id, context)

    # Clear timeout tracking (but preserve backup)
    CONVERSATION_TIMEOUTS.pop(user_id, None)

    # Enhanced timeout message with recovery options
    has_backup = user_id in SESSION_STATE_BACKUP
    backup_info = ""
    if has_backup:
        backup_info = "\n\n🔄 Your progress has been saved and can be recovered."

    enhanced_message = (
        f"⏰ **Session Expired**\n\n"
        f"Your session has timed out after 20 minutes of inactivity.{backup_info}\n\n"
        f"Choose an option below to continue:"
    )

    # Create enhanced recovery keyboard
    recovery_keyboard = []
    
    if has_backup:
        recovery_keyboard.append([
            InlineKeyboardButton("🔄 Resume Previous Action", callback_data="recover_session")
        ])
    
    recovery_keyboard.extend([
        [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")],
        [InlineKeyboardButton("💰 Quick Exchange", callback_data="exchange")],
        [InlineKeyboardButton("🛡️ Secure Trade", callback_data="create_escrow")]
    ])

    # Use safe navigation fallback with enhanced recovery
    from telegram import InlineKeyboardMarkup
    from utils.callback_utils import safe_edit_message_text
    
    try:
        if update.callback_query:
            await safe_edit_message_text(
                update.callback_query, 
                enhanced_message,
                reply_markup=InlineKeyboardMarkup(recovery_keyboard)
            )
        elif update.message:
            await update.message.reply_text(
                enhanced_message,
                reply_markup=InlineKeyboardMarkup(recovery_keyboard),
                parse_mode="Markdown"
            )
    except Exception as e:
        logger.error(f"Error sending enhanced timeout message: {e}")
        # Fallback to original behavior
        return await safe_navigation_fallback(
            update,
            context,
            message="⏰ Session expired. Returning to main menu...",
            cleanup_conversation=True,
        )

    return ConversationHandler.END


async def timeout_warning_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Send warning when conversation is about to timeout

    Args:
        update: Telegram update object
        context: Bot context
    """
    user_id = update.effective_user.id if update.effective_user else 0
    remaining = ConversationTimeout.get_remaining_time(user_id)

    if remaining and remaining.total_seconds() <= 300:  # 5 minutes warning
        try:
            warning_keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🔄 Continue Session", callback_data="extend_session"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "🏠 Main Menu", callback_data="back_to_main"
                        )
                    ],
                ]
            )

            if update.effective_chat:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="⚠️ **Session Warning**\n\nYour session will expire in 5 minutes due to inactivity.\n\nTap 'Continue Session' to extend your time.",
                    reply_markup=warning_keyboard,
                    parse_mode="Markdown",
                )
            logger.info(f"Sent timeout warning to user {user_id}")
        except Exception as e:
            logger.error(f"Failed to send timeout warning to user {user_id}: {e}")


# Remove duplicate conversation_wrapper - keeping the one with @wraps below


async def check_conversation_health(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    """
    Check conversation health and state integrity

    Args:
        update: Telegram update object
        context: Bot context

    Returns:
        True if conversation is healthy, False if needs recovery
    """
    user_id = update.effective_user.id if update.effective_user else 0

    # Check timeout status
    if ConversationTimeout.is_expired(user_id):
        logger.warning(
            f"Conversation health check failed: timeout expired for user {user_id}"
        )
        return False

    # Check essential context integrity
    if not context.user_data:
        logger.warning(
            f"Conversation health check failed: no user data for user {user_id}"
        )
        return False

    # Check for corrupted state indicators
    required_fields = ["user_id"] if "user_id" in context.user_data else []
    for field in required_fields:
        if field not in context.user_data:
            logger.warning(
                f"Conversation health check failed: missing {field} for user {user_id}"
            )
            return False

    return True


async def detect_rapid_start_commands(user_id: int) -> bool:
    """
    Detect if user is rapidly sending /start commands (stuck state indicator)

    Args:
        user_id: User's Telegram ID

    Returns:
        True if rapid start detected, False otherwise
    """
    current_time = datetime.now()

    # Initialize tracking if needed
    if user_id not in USER_START_TIMES:
        USER_START_TIMES[user_id] = []

    # Add current start time
    USER_START_TIMES[user_id].append(current_time)

    # Clean old entries (older than 10 seconds)
    USER_START_TIMES[user_id] = [
        t for t in USER_START_TIMES[user_id] if (current_time - t).total_seconds() <= 10
    ]

    # Check if threshold exceeded
    rapid_start_detected = len(USER_START_TIMES[user_id]) >= RAPID_START_THRESHOLD

    if rapid_start_detected:
        logger.warning(
            f"Rapid /start command detected for user {user_id}: {len(USER_START_TIMES[user_id])} commands in 10 seconds"
        )
        # Clear tracking after detection
        USER_START_TIMES[user_id] = []

    return rapid_start_detected


async def detect_navigation_loops(user_id: int, action: str) -> bool:
    """
    Detect if user is stuck in navigation loops

    Args:
        user_id: User's Telegram ID
        action: Current navigation action

    Returns:
        True if navigation loop detected, False otherwise
    """
    current_time = datetime.now()

    # Initialize tracking if needed
    if user_id not in USER_NAVIGATION_PATHS:
        USER_NAVIGATION_PATHS[user_id] = []

    # Add current action with timestamp
    USER_NAVIGATION_PATHS[user_id].append((action, current_time))

    # Clean old entries (older than 30 seconds)
    USER_NAVIGATION_PATHS[user_id] = [
        (act, time)
        for act, time in USER_NAVIGATION_PATHS[user_id]
        if (current_time - time).total_seconds() <= 30
    ]

    # Count same actions in recent history
    recent_same_actions = [
        act for act, time in USER_NAVIGATION_PATHS[user_id] if act == action
    ]

    # Check if threshold exceeded
    loop_detected = len(recent_same_actions) >= NAVIGATION_LOOP_THRESHOLD

    if loop_detected:
        logger.warning(
            f"Navigation loop detected for user {user_id}: {len(recent_same_actions)} '{action}' actions in 30 seconds"
        )
        # Clear tracking after detection
        USER_NAVIGATION_PATHS[user_id] = []

    return loop_detected


async def smart_navigation_recovery(
    update: Update, context: ContextTypes.DEFAULT_TYPE, issue_type: str = "general"
) -> int:
    """
    Smart navigation recovery based on detected issue type

    Args:
        update: Telegram update object
        context: Bot context
        issue_type: Type of issue detected ('rapid_start', 'navigation_loop', 'general')

    Returns:
        ConversationHandler.END
    """
    user_id = update.effective_user.id if update.effective_user else 0

    # Clear all tracking for this user
    USER_START_TIMES.pop(user_id, None)
    USER_NAVIGATION_PATHS.pop(user_id, None)
    ConversationTimeout.clear_timeout(user_id)

    # Customized recovery messages based on issue type
    if issue_type == "rapid_start":
        message = "🏠 **Returning to Main Menu**"
        cleanup_msg = "Navigation reset due to rapid /start commands"
    elif issue_type == "navigation_loop":
        message = "🔄 **Smart Recovery**\n\nWe detected you were stuck in a loop. Breaking you free!\n\nReturning to main menu..."
        cleanup_msg = "Navigation reset due to loop detection"
    else:
        message = "🔄 **Session Recovery**\n\nWe're helping you get back on track.\n\nReturning to main menu..."
        cleanup_msg = "General navigation recovery"

    logger.info(f"{cleanup_msg} for user {user_id}")

    # Use safe navigation fallback
    return await safe_navigation_fallback(
        update, context, message=message, cleanup_conversation=True
    )


async def emergency_conversation_recovery(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """
    Emergency recovery for corrupted conversation states

    Args:
        update: Telegram update object
        context: Bot context

    Returns:
        ConversationHandler.END
    """
    user_id = update.effective_user.id if update.effective_user else 0
    logger.error(f"Emergency conversation recovery triggered for user {user_id}")

    # Clear all conversation tracking
    ConversationTimeout.clear_timeout(user_id)

    # Attempt to recover user to main menu
    return await safe_navigation_fallback(
        update,
        context,
        message="🚨 **Session Recovery**\n\nWe detected an issue with your session and have safely restored your access.\n\nReturning to main menu...",
        cleanup_conversation=True,
    )


def conversation_wrapper(timeout_minutes: int = 30):
    """
    Decorator to add timeout protection to conversation handlers

    Args:
        timeout_minutes: Timeout duration in minutes

    Returns:
        Decorated function with timeout protection
    """

    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
            user_id = update.effective_user.id if update.effective_user else 0

            # Check if conversation has expired
            if ConversationTimeout.is_expired(user_id):
                logger.warning(
                    f"Expired conversation access attempted by user {user_id}"
                )
                return await timeout_handler(update, context)

            # Extend timeout on activity
            ConversationTimeout.extend_timeout(user_id)

            try:
                # Execute original function
                return await func(update, context)
            except Exception as e:
                logger.error(f"Error in conversation handler {func.__name__}: {e}")
                # Clear timeout on error
                ConversationTimeout.clear_timeout(user_id)
                return await safe_navigation_fallback(update, context)

        return wrapper

    return decorator


# OPTIMIZED: Background cleanup task with memory efficiency
async def cleanup_expired_conversations():
    """Background task to clean up expired conversation timeouts with memory optimization"""
    import gc
    
    current_time = datetime.now()
    
    # OPTIMIZATION: Use list comprehension with early exit
    expired_users = []
    cleanup_count = 0
    
    for user_id, expire_time in list(CONVERSATION_TIMEOUTS.items()):
        if current_time > expire_time:
            expired_users.append(user_id)
            cleanup_count += 1
            
            # OPTIMIZATION: Cleanup immediately to reduce memory footprint
            ConversationTimeout.clear_timeout(user_id)
            
            # Clear navigation tracking as well
            USER_START_TIMES.pop(user_id, None)
            USER_NAVIGATION_PATHS.pop(user_id, None)

    # OPTIMIZATION: Force garbage collection after cleanup
    if cleanup_count > 0:
        gc.collect()
        logger.info(f"🧹 Cleaned up {cleanup_count} expired conversations and triggered garbage collection")
