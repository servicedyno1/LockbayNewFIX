"""
Button Handler Async Utilities
===============================

Reusable async patterns for button handlers to achieve <500ms response times.

This module provides utilities that eliminate boilerplate and follow performance
best practices extracted from handlers/start.py's process_existing_user_async().

KEY PERFORMANCE PRINCIPLES:
1. Answer callback queries FIRST (instant user feedback)
2. Use ONE shared async session for all DB operations (avoid cold starts)
3. Use caching to bypass DB when possible
4. Use async_fast_user_lookup() for non-blocking user queries

USAGE EXAMPLE (Without Caching):
---------------------------------
```python
from telegram import Update
from telegram.ext import ContextTypes
from sqlalchemy import select
from models import Wallet
from utils.button_handler_async import async_button_user_lookup, button_callback_wrapper

async def direct_wallet_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    '''Handle wallet menu button with optimized async pattern'''
    
    # Auto-handles callback acknowledgment and async session management
    async with button_callback_wrapper(update, "💰 Loading wallet...") as session:
        # Get user with async DB lookup (30-50ms)
        user = await async_button_user_lookup(update.effective_user.id, session)
        
        if not user:
            await update.callback_query.edit_message_text("❌ User not found")
            return
        
        # Your handler logic here with async queries using the shared session
        result = await session.execute(
            select(Wallet).where(Wallet.user_id == user.id)
        )
        wallet = result.scalar_one_or_none()
        
        # Display wallet info
        await update.callback_query.edit_message_text(
            f"💰 Your balance: ${wallet.balance_usd:.2f}"
        )
```

USAGE EXAMPLE (With Per-Update Caching):
-----------------------------------------
For handlers that need multiple user lookups in the same update, use utils.update_cache:
```python
from utils.update_cache import get_cached_user
from utils.button_handler_async import button_callback_wrapper

async def direct_wallet_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Option 1: Use update cache for per-update caching (recommended for multiple lookups)
    user = await get_cached_user(update, context)  # First call: DB, subsequent: cache
    
    # Option 2: Use button wrapper for DB operations
    async with button_callback_wrapper(update, "💰 Loading wallet...") as session:
        # Use cached user to avoid redundant lookups
        result = await session.execute(
            select(Wallet).where(Wallet.user_id == user.id)
        )
```

PERFORMANCE IMPACT:
------------------
- Before: 500-1200ms (multiple sessions, sync operations, event loop blocking)
- After: <500ms (one session, async operations, no event loop blocking)
- With caching: <200ms (utils.update_cache for per-update reuse)
- User feedback: <50ms (callback answered immediately)
"""

import logging
import time
import asyncio
from typing import Optional, AsyncGenerator
from contextlib import asynccontextmanager

from telegram import Update
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_async_session
from utils.fast_user_lookup import async_fast_user_lookup
from models import User

logger = logging.getLogger(__name__)


async def async_button_user_lookup(
    user_telegram_id: int, 
    session: AsyncSession
) -> Optional[User]:
    """
    PERFORMANCE OPTIMIZED: Get user with async DB query for button handlers
    
    This function implements the proven async pattern from handlers/start.py:
    1. Use async_fast_user_lookup() with shared session (no cold starts)
    2. Include performance monitoring and timeout protection
    3. Returns SQLAlchemy User instance ready for use
    
    NOTE: For per-update caching, button handlers should use utils.update_cache.get_cached_user()
    directly if they need caching. This utility focuses on the async DB lookup pattern.
    
    Args:
        user_telegram_id: User's Telegram ID (integer)
        session: Shared AsyncSession to reuse (avoids cold starts)
    
    Returns:
        User object or None if not found
    
    Performance:
        - Async DB query: 30-50ms with shared session (no cold starts)
        - Timeout protection: 2 seconds max
    
    Example:
        ```python
        async with button_callback_wrapper(update, "⏳ Loading...") as session:
            user = await async_button_user_lookup(update.effective_user.id, session)
            if not user:
                return  # Handle error
            
            # Use user object for queries
            result = await session.execute(select(Wallet).where(Wallet.user_id == user.id))
        ```
    """
    start_time = time.time()
    telegram_id_str = str(user_telegram_id)
    
    try:
        # PERFORMANCE: Use async lookup with shared session (avoids cold start penalty)
        logger.info(f"⚡ ASYNC_LOOKUP: Fetching user {user_telegram_id} from DB with shared session")
        
        db_user = await asyncio.wait_for(
            async_fast_user_lookup(telegram_id_str, session=session),
            timeout=2.0  # Fail fast if DB is slow
        )
        
        if not db_user:
            lookup_time = time.time() - start_time
            logger.warning(f"⚠️ USER_NOT_FOUND: User {user_telegram_id} not in DB (checked in {lookup_time*1000:.1f}ms)")
            return None
        
        lookup_time = time.time() - start_time
        logger.info(f"⚡ DB_LOOKUP: User {user_telegram_id} fetched in {lookup_time*1000:.1f}ms")
        
        return db_user
        
    except asyncio.TimeoutError:
        lookup_time = time.time() - start_time
        logger.error(f"❌ TIMEOUT: User lookup exceeded 2s for {user_telegram_id} (took {lookup_time*1000:.1f}ms)")
        return None
    except Exception as e:
        lookup_time = time.time() - start_time
        logger.error(f"❌ ERROR: User lookup failed for {user_telegram_id} after {lookup_time*1000:.1f}ms: {e}")
        return None


@asynccontextmanager
async def button_callback_wrapper(
    update: Update,
    ack_text: Optional[str] = None,
    show_alert: bool = False
) -> AsyncGenerator[AsyncSession, None]:
    """
    PERFORMANCE OPTIMIZED: Standard async pattern for button callback handlers
    
    This context manager implements the proven pattern from handlers/start.py:
    1. Answer callback query FIRST (instant user feedback <50ms)
    2. Open ONE shared async session (avoid multiple cold starts)
    3. Automatically handle commits and rollbacks
    4. Include performance monitoring
    
    Args:
        update: Telegram Update object
        ack_text: Optional acknowledgment text (e.g., "⏳ Loading...")
                 If None, just acknowledges without text
        show_alert: Show as alert popup vs silent ack (default: False)
    
    Yields:
        AsyncSession: Shared async session for all DB operations
    
    Performance:
        - Callback acknowledgment: <50ms (before any DB operations)
        - Session operations: Non-blocking async
        - Total handler time: Typically <200ms with caching
    
    Example - Basic Usage:
        ```python
        async def wallet_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
            async with button_callback_wrapper(update, "💰 Loading wallet...") as session:
                user = await async_button_user_lookup(update.effective_user.id, session)
                
                # All your DB queries here using 'session'
                result = await session.execute(select(Wallet).where(...))
                wallet = result.scalar_one_or_none()
                
                await update.callback_query.edit_message_text(f"Balance: ${wallet.balance_usd:.2f}")
        ```
    
    Example - With Error Handling:
        ```python
        async def complex_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
            try:
                async with button_callback_wrapper(update, "⏳ Processing...") as session:
                    user = await async_button_user_lookup(update.effective_user.id, session)
                    
                    if not user:
                        await update.callback_query.edit_message_text("❌ User not found")
                        return
                    
                    # Complex queries all share same session
                    wallet = await session.execute(select(Wallet).where(Wallet.user_id == user.id))
                    escrows = await session.execute(select(Escrow).where(Escrow.buyer_id == user.id))
                    
                    # Session auto-commits on success
                    
            except Exception as e:
                logger.error(f"Handler error: {e}")
                # Session auto-rollbacks on exception
                if update.callback_query:
                    await update.callback_query.edit_message_text("❌ An error occurred")
        ```
    
    Example - Without Callback (Command Handler):
        ```python
        async def command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
            # Works for commands too (no callback to acknowledge)
            async with button_callback_wrapper(update) as session:
                user = await async_button_user_lookup(update.effective_user.id, session)
                
                # Your logic here
                await update.message.reply_text("Done!")
        ```
    """
    session = None
    start_time = time.time()  # Initialize for error handling path
    
    try:
        # CRITICAL PERFORMANCE: Answer callback FIRST (instant feedback to user)
        # This happens BEFORE any database operations or time measurements
        # MUST await directly - background tasks don't run until event loop gets control
        if update.callback_query:
            ack_start = time.time()  # Measure callback acknowledgment time
            query = update.callback_query  # Capture for type safety
            
            try:
                await query.answer(
                    text=ack_text,
                    show_alert=show_alert
                )
                ack_time = time.time() - ack_start
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(f"⚡ INSTANT_FEEDBACK: Callback answered in {ack_time*1000:.1f}ms")
            except Exception as e:
                # Don't fail if callback already answered
                logger.debug(f"Callback answer skipped (already answered?): {e}")
        
        # PERFORMANCE: Open ONE shared async session for all operations
        session_start = time.time()
        async with get_async_session() as session:
            session_open_time = time.time() - session_start
            logger.info(f"⚡ SHARED_SESSION: Opened in {session_open_time*1000:.1f}ms")
            
            # Yield session to handler code
            yield session
            
            # Session auto-commits if no exception
            total_time = time.time() - start_time
            logger.info(f"⚡ HANDLER_COMPLETE: Total time {total_time*1000:.1f}ms")
            
    except Exception as e:
        # Session auto-rollbacks on exception
        total_time = time.time() - start_time
        logger.error(f"❌ HANDLER_ERROR: Failed after {total_time*1000:.1f}ms: {e}")
        raise


# Convenience function for handlers that don't need the session
async def acknowledge_callback(
    update: Update,
    text: Optional[str] = None,
    show_alert: bool = False
) -> bool:
    """
    Quick acknowledgment of callback query without session management
    
    Use this when you just need to acknowledge a callback without DB operations.
    For handlers with DB operations, use button_callback_wrapper() instead.
    
    Args:
        update: Telegram Update object
        text: Optional acknowledgment text
        show_alert: Show as alert popup vs silent ack
    
    Returns:
        True if acknowledged successfully, False otherwise
    
    Example:
        ```python
        async def simple_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
            # Just acknowledge, no DB needed
            await acknowledge_callback(update, "✅ Done!")
            
            # Your logic here (no DB operations)
            await update.callback_query.edit_message_text("Updated!")
        ```
    """
    if not update.callback_query:
        return False
    
    try:
        await update.callback_query.answer(text=text, show_alert=show_alert)
        return True
    except Exception as e:
        logger.debug(f"Callback acknowledgment failed: {e}")
        return False


# Performance monitoring helper
def log_handler_performance(handler_name: str, start_time: float, operations: dict):
    """
    Log detailed performance metrics for button handlers
    
    Args:
        handler_name: Name of the handler (for logging)
        start_time: Handler start time from time.time()
        operations: Dict of operation_name -> operation_time
    
    Example:
        ```python
        async def my_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
            start = time.time()
            ops = {}
            
            async with button_callback_wrapper(update, "Loading...") as session:
                lookup_start = time.time()
                user = await async_button_user_lookup(update.effective_user.id, session)
                ops['user_lookup'] = time.time() - lookup_start
                
                query_start = time.time()
                result = await session.execute(select(Wallet).where(...))
                ops['wallet_query'] = time.time() - query_start
            
            log_handler_performance("my_handler", start, ops)
        ```
    """
    total_time = time.time() - start_time
    
    logger.info(f"📊 PERFORMANCE [{handler_name}]:")
    logger.info(f"   Total: {total_time*1000:.1f}ms")
    
    for op_name, op_time in operations.items():
        percentage = (op_time / total_time * 100) if total_time > 0 else 0
        logger.info(f"   - {op_name}: {op_time*1000:.1f}ms ({percentage:.1f}%)")
    
    if total_time > 0.5:  # 500ms threshold
        logger.warning(f"⚠️ SLOW_HANDLER: {handler_name} took {total_time*1000:.1f}ms (>500ms)")
