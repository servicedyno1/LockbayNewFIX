"""Start and onboarding handlers with enhanced navigation and reliability"""

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.constants import ChatAction
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)
from telegram.error import TelegramError
from sqlalchemy.exc import SQLAlchemyError
from models import User, Escrow, EscrowStatus, Wallet
from sqlalchemy import and_, or_, select, text
from utils.keyboards import main_menu_keyboard
from utils.helpers import (
    validate_email,
    parse_start_parameter,
    update_user_from_telegram,
    get_user_display_name,
)
from utils.wallet_manager import get_or_create_wallet, get_user_wallet
from utils.callback_utils import safe_answer_callback_query

# Enhanced user interaction logging for anomaly detection
from utils.unified_activity_monitor import track_user_activity

# Email verification integrated into this handler
from utils.constants import CURRENCY_EMOJIS

# UNIQUE STATES: Onboarding conversation handler states
class OnboardingStates:
    COLLECTING_EMAIL = 100
    VERIFYING_EMAIL_OTP = 101
    CONFIRMING_EMAIL = 102
    ACCEPTING_TOS = 103
    ONBOARDING_SHOWCASE = 104
    SELECTING_CASHOUT_TYPE = 300
    SELECTING_AMOUNT = 301
    SELECTING_METHOD = 302
    ENTERING_CUSTOM_AMOUNT = 321
    SELECTING_WITHDRAW_CURRENCY = 303
    SELECTING_CRYPTO_CURRENCY = 308
    ENTERING_WITHDRAW_AMOUNT = 304
    SELECTING_WITHDRAW_NETWORK = 305
    ENTERING_WITHDRAW_ADDRESS = 306
    CONFIRMING_CASHOUT = 307
    CONFIRMING_SAVED_ADDRESS = 319  # Fixed duplicate state ID
    CONFIRMING_SAVE_ADDRESS = 309
    ENTERING_NGN_BANK_DETAILS = 310
    SELECTING_NGN_BANK = 311
    CONFIRMING_NGN_CASHOUT = 312
    CONFIRMING_NGN_SAVE = 313
    SELECTING_NGN_MATCH = 314
    SELECTING_BANK_FROM_MATCHES = 315
    VERIFICATION_OPTIONS = 316
    AWAITING_EMAIL_VERIFICATION = 317
    SELECTING_SAVED_BANK = 318
    ENTERING_EMAIL = 800
    ENTERING_OTP = 801
    VERIFYING_EMAIL_INVITATION = 900
from config import Config
from database import SessionLocal, SyncSessionLocal, get_async_session

# Import enhanced navigation and reliability systems
from utils.conversation_protection import (
    conversation_wrapper,
)

# Import per-update caching system
from utils.update_cache import get_cached_user, invalidate_user_cache

# PERFORMANCE OPTIMIZATION: Onboarding context prefetch (reduces 70 queries to 2)
from utils.onboarding_prefetch import (
    prefetch_onboarding_context,
    get_cached_onboarding_data,
    cache_onboarding_data,
    invalidate_onboarding_cache
)

logger = logging.getLogger(__name__)

async def process_existing_user_async(
    update: Update, 
    context: ContextTypes.DEFAULT_TYPE, 
    user_telegram_id: int,
    start_param: str | None,
    handler_start_time: float
) -> int | None:
    """
    PERFORMANCE OPTIMIZED: Process existing user with ONE shared AsyncSession
    
    This function consolidates all database operations for the existing user flow:
    - User lookup
    - User info update
    - Pending invitations check  
    - Auto-complete onboarding (email verification removed)
    - Main menu display
    
    All operations use a single shared session to eliminate Neon cold start penalties.
    
    Args:
        update: Telegram Update object
        context: Bot context
        user_telegram_id: User's Telegram ID
        start_param: Deep link parameter (if any)
        handler_start_time: Start time for performance monitoring
        
    Returns:
        Conversation state or None
    """
    from utils.helpers import async_update_user_from_telegram
    from utils.fast_user_lookup import async_fast_user_lookup
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    from database import async_managed_session
    import time
    import asyncio
    
    user = update.effective_user
    if not user or not update.message:
        return ConversationHandler.END
    
    logger.info(f"⚡ PROCESS_EXISTING_USER_ASYNC: Starting shared session for user {user_telegram_id}")
    
    try:
        # CRITICAL PERFORMANCE FIX: Open ONE shared session for entire existing user flow
        async with get_async_session() as shared_session:
            session_start = time.time()
            
            # STEP 1: User lookup with shared session
            db_user = await asyncio.wait_for(
                async_fast_user_lookup(str(user_telegram_id), session=shared_session),
                timeout=2.0
            )
            
            if not db_user:
                logger.warning(f"⚠️ User {user_telegram_id} not found in shared session flow")
                return None
            
            # CHECK IF USER IS BLOCKED - BLOCK IMMEDIATELY
            if db_user.is_blocked:
                logger.warning(f"🚫 BLOCKED USER: {user_telegram_id} attempted to access bot")
                try:
                    await update.message.reply_text(
                        "❌ Your account has been suspended and you cannot access this service.",
                        reply_markup=None
                    )
                except Exception as e:
                    logger.error(f"Error sending blocked message: {e}")
                return ConversationHandler.END
            
            lookup_time = time.time() - session_start
            logger.info(f"⚡ SHARED_SESSION: User lookup completed in {lookup_time*1000:.1f}ms")
            
            # Cache the user for future requests (optimized - skip time measurement)
            from utils.user_cache import cache_user
            cache_user(str(user_telegram_id), db_user, ttl=Config.USER_CACHE_TTL_MINUTES * 60)
            
            logger.info(f"👤 EXISTING USER: {db_user.first_name} (ID: {db_user.id})")
            
            # Check if user needs onboarding - EMAIL VERIFICATION REMOVED
            # Auto-complete onboarding instead of routing to onboarding flow
            needs_onboarding = (
                not hasattr(db_user, 'onboarding_completed') or 
                not bool(db_user.onboarding_completed)
            )
            
            if needs_onboarding:
                # Auto-complete onboarding instead of routing to onboarding flow
                logger.info(f"🚀 Auto-completing onboarding for user {user.id} - skipping onboarding flow")
                try:
                    from sqlalchemy import update as sql_update
                    from models import User
                    await shared_session.execute(
                        sql_update(User).where(User.id == db_user.id).values(onboarding_completed=True)
                    )
                    await shared_session.commit()
                    db_user.onboarding_completed = True

                    # Broadcast new user event to registered groups
                    try:
                        from services.group_event_service import group_event_service
                        asyncio.create_task(group_event_service.broadcast_new_user_onboarded({
                            'first_name': db_user.first_name or 'New User',
                            'username': db_user.username
                        }))
                    except Exception as grp_err:
                        logger.error(f"Failed to broadcast new user event: {grp_err}")

                    logger.info(f"✅ Auto-completed onboarding for user {user.id}")
                except Exception as e:
                    logger.error(f"Error auto-completing onboarding for user {user.id}: {e}")

                # Show main menu directly
                await show_main_menu_optimized_async(update, context, db_user, shared_session)
                return ConversationHandler.END
            
            # Extract user data BEFORE operations (needed for parallel execution)
            user_id_db = db_user.id
            user_email = db_user.email if db_user.email is not None else None
            user_email_verified = getattr(db_user, 'email_verified', False) or False
            user_referral_code = getattr(db_user, 'referral_code', None)
            user_referred_by = getattr(db_user, 'referred_by_id', None)
            
            # STEP 2: Quick user update (skip if not critical)
            # Most user info doesn't change frequently, so we can skip this for speed
            ops_start = time.time()
            
            # STEP 3: Fast invitation check with aggressive caching
            pending_invitation = None
            cache_key = f"inv_check_{user_telegram_id}"
            current_time = time.time()
            
            last_check = getattr(context, 'user_data', {}).get(cache_key, 0) if context.user_data else 0
            if current_time - last_check < (Config.USER_CACHE_TTL_MINUTES * 60):
                logger.info("⚡ SKIP: Invitation check cached")
            else:
                try:
                    user_email_for_check = user_email if user_email is not None else ""
                    pending_invitation = await check_pending_invitations_by_user_data(
                        user_id_db, user_email_for_check, shared_session
                    )
                    if context.user_data is not None:
                        context.user_data[cache_key] = current_time
                except Exception as e:
                    logger.warning(f"Invitation check failed: {e}")
            
            ops_time = time.time() - ops_start
            logger.info(f"⚡ FAST_OPS: Invitation check completed in {ops_time*1000:.1f}ms")
            
            # Handle deep link if present
            if start_param:
                logger.info(f"🔗 Handling deep link for existing user: {start_param}")
                
                if start_param.startswith("ref_"):
                    referral_code = start_param[4:]
                    logger.info(f"🔗 Existing user clicked referral link: {referral_code}")
                    
                    if user_referral_code == referral_code:
                        await update.message.reply_text("😅 You can't use your own referral code!")
                    elif user_referred_by:
                        await update.message.reply_text("ℹ️ You're already part of our referral program!")
                    else:
                        await update.message.reply_text("ℹ️ Referral codes can only be used when joining for the first time.")
                    
                    # Show main menu after message (reuse shared session)
                    await show_main_menu_optimized_async(update, context, db_user, shared_session)
                    total_elapsed = time.time() - handler_start_time
                    logger.info(f"⚡ SHARED_SESSION: Completed referral flow in {total_elapsed*1000:.2f}ms")
                    return ConversationHandler.END
                
                # Handle other deep links
                logger.info(f"🔗 Calling handle_deep_link for: {start_param}")
                return await handle_deep_link(update, context, start_param, db_user)
            
            # Store pending invitations for main menu notification
            if pending_invitation and isinstance(pending_invitation, dict):
                if context.user_data is not None:
                    context.user_data["pending_invitations"] = pending_invitation
                    logger.info(f"📬 Stored pending invitations for main menu badge")
            
            # EMAIL VERIFICATION REMOVED: OTP was removed from onboarding flow.
            # Users go directly to main menu regardless of email_verified status.
            logger.info(f"✅ Skipping email verification check (OTP removed from onboarding) for user {user_id_db}")
            
            # STEP 5: Show main menu using shared session
            menu_start = time.time()
            await show_main_menu_optimized_async(update, context, db_user, shared_session)
            menu_time = time.time() - menu_start
            
            total_elapsed = time.time() - handler_start_time
            
            # Calculate overhead
            tracked_time = lookup_time + ops_time + menu_time
            overhead = total_elapsed - tracked_time
            
            logger.info(
                f"⚡ COMPLETE: Total {total_elapsed*1000:.2f}ms "
                f"(lookup: {lookup_time*1000:.1f}ms, ops: {ops_time*1000:.1f}ms, "
                f"menu: {menu_time*1000:.1f}ms, overhead: {overhead*1000:.1f}ms)"
            )
            
            return ConversationHandler.END
            
    except asyncio.TimeoutError:
        logger.error(f"⏰ Database timeout in shared session for user {user_telegram_id}")
        return None
    except Exception as e:
        logger.error(f"❌ Error in shared session flow for user {user_telegram_id}: {e}", exc_info=True)
        return None


async def start_onboarding_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int | None:
    """Handle start onboarding button from main menu redirect"""
    query = update.callback_query
    if not query:
        return ConversationHandler.END
    
    # Track user interaction for anomaly detection
    user = query.from_user
    if user:
        track_user_activity(
            user_id=user.id,
            action="start_onboarding_button",
            username=user.username or f"user_{user.id}",
            details={
                "handler": "start_onboarding_callback",
                "callback_data": query.data,
                "timestamp": query.message.date.isoformat() if query.message else None
            }
        )

    from utils.callback_utils import safe_answer_callback_query

    await safe_answer_callback_query(query, "🚀 Starting...")

    user = update.effective_user
    if not user:
        return ConversationHandler.END

    # Instead of simulating /start, directly check for pending invitations
    # and show the appropriate flow - bypassing rapid command detection
    async with get_async_session() as session:
        # Check for pending invitations like the start handler does
        pending_invitation = (
            await check_pending_invitations_by_telegram_id_with_username(
                user.id, user.username or "", session
            )
        )

        if pending_invitation:
            if pending_invitation.get("multiple_invitations"):
                logger.info(
                    f"📬 Found {pending_invitation['count']} pending invitations for user during onboarding callback"
                )
                return await show_multiple_pending_invitations(
                    update, context, pending_invitation, user
                )
            else:
                logger.info(
                    "📬 Found single pending invitation for user during onboarding callback"
                )
                return await handle_email_invitation_for_new_user_by_telegram(
                    update, context, pending_invitation
                )
        else:
            # No pending invitations, start normal onboarding
            logger.info("🆕 Starting normal onboarding flow from callback")
            return await start_onboarding(update, context)  # Use existing function

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int | None:
    """Enhanced /start command with deep link support and session recovery"""
    # PERFORMANCE MONITORING: Track total handler execution time
    import time
    handler_start_time = time.time()
    
    user = update.effective_user
    user_id = user.id if user else None
    username = user.username if user else "Unknown"
    
    logger.info(
        f"⏱️ PERF: START HANDLER ENTRY - User: {user_id or 'Unknown'} at {handler_start_time}"
    )
    
    # PERFORMANCE: Typing indicator removed to save ~490ms
    # User gets instant response instead of waiting for typing animation
    
    # Track user interaction for anomaly detection
    if user_id:
        track_user_activity(
            user_id=user_id,
            action="/start command",
            username=username or f"user_{user_id}",
            details={
                "handler": "start_handler",
                "timestamp": update.message.date.isoformat() if update.message else None,
                "user_first_name": user.first_name if user else None
            }
        )
    
    # CRITICAL FIX: Selective state clearing to preserve conversation handler functionality  
    if context.user_data:
        # Log active conversation before clearing
        active_conv = context.user_data.get("active_conversation")
        if active_conv:
            logger.info(f"🧹 Clearing active conversation: {active_conv}")
        
        # SELECTIVE CLEARING: Remove conversation data but preserve handler state
        conversation_keys_to_clear = [
            "exchange_data", "exchange_session_id", "active_conversation",
            "escrow_data", "wallet_data", "active_chat", "onboarding_data",
            "expecting_funding_amount", "expecting_custom_amount"
        ]
        
        # CRITICAL FIX: Preserve active cashout sessions during /start
        has_active_cashout = context.user_data.get('pending_address_save') or context.user_data.get('pending_cashout')
        if has_active_cashout and update.effective_user:
            logger.info(f"🔒 Preserving active cashout session during /start for user {update.effective_user.id}")
            # Skip clearing any cashout-related data
            conversation_keys_to_clear = [key for key in conversation_keys_to_clear if key not in ['wallet_data']]
        
        for key in conversation_keys_to_clear:
            context.user_data.pop(key, None)
            
        logger.info(f"✅ Selective conversation state cleared for clean /start (preserved {len(context.user_data)} items)")
    
    # CRITICAL: Reset conversation handler state by ending any active conversations
    # This ensures conversation handlers can accept new entry points
    try:
        # Force end any active conversation by returning END and clearing chat data
        context.chat_data.clear() if hasattr(context, 'chat_data') and context.chat_data else None
        logger.info("✅ Chat data cleared to reset conversation handlers")
    except Exception as e:
        logger.warning(f"Could not clear chat data: {e}")
    
    # PERFORMANCE FIX: Skip database cleanup for /start - not critical for user experience
    # The cleanup can happen later in background jobs if needed
    # This saves ~636ms of blocking database operations
        
    # PERFORMANCE FIX: Skip universal session cleanup for /start - not critical
    # Sessions will expire naturally or get cleaned up by background jobs
    # This saves additional overhead

    if not user or not update.message:
        logger.error("❌ No user or message in start handler")
        return ConversationHandler.END

    logger.info(f"🔍 START HANDLER - User {user.id} proceeding with handler logic")
    
    # PERFORMANCE FIX: Skip rapid start detection - adds overhead
    # Users can just press /start again if needed without complex detection

    # PERFORMANCE FIX: Use centralized session management with async safety
    from utils.session_manager import SessionManager
    import asyncio

    try:
        # Check for start parameter (deep links)
        start_param = None
        if context.args:
            start_param = context.args[0]
            logger.info(f"Start parameter received: {start_param}")
        else:
            logger.info("No start parameter provided")

        logger.info(f"⏱️ PERF: Looking for user with telegram_id: {user.id}")

        # OPTIMIZATION: Add performance monitoring and caching to user lookup
        import time
        db_query_start = time.time()
        from utils.query_performance_monitor import QueryTimer, ConnectionTimer
        from utils.user_cache import get_cached_user, cache_user
        from utils.connection_pool_monitor import pool_monitor
        from database import async_managed_session
        
        # PERFORMANCE OPTIMIZATION: Prefetch onboarding context (reduces 70 queries to 2)
        logger.info(f"⚡ ONBOARDING_PREFETCH: Starting context prefetch for user {user.id}")
        prefetch_start = time.time()
        
        try:
            async with async_managed_session() as session:
                prefetch_data = await prefetch_onboarding_context(user.id, session)
                if prefetch_data:
                    cache_onboarding_data(context.user_data, prefetch_data)
                    prefetch_time = (time.time() - prefetch_start) * 1000
                    logger.info(
                        f"⚡ ONBOARDING_PREFETCH_SUCCESS: Completed in {prefetch_time:.1f}ms "
                        f"(is_new_user: {prefetch_data.is_new_user}, "
                        f"email_verified: {prefetch_data.email_verified}, "
                        f"onboarding_complete: {prefetch_data.onboarding_complete})"
                    )
                else:
                    logger.warning(f"⚠️ ONBOARDING_PREFETCH: No data returned for user {user.id}")
        except Exception as e:
            prefetch_time = (time.time() - prefetch_start) * 1000
            logger.error(f"❌ ONBOARDING_PREFETCH_ERROR: Failed in {prefetch_time:.1f}ms: {e}")
        
        # PRIORITY 1: Check onboarding prefetch cache (most comprehensive)
        cached_onboarding = get_cached_onboarding_data(context.user_data)
        
        if cached_onboarding:
            logger.info(
                f"⚡ ONBOARDING_CACHE_HIT: Using prefetched data "
                f"(is_new_user: {cached_onboarding.get('is_new_user')}, "
                f"email_verified: {cached_onboarding.get('email_verified')}, "
                f"onboarding_complete: {cached_onboarding.get('onboarding_complete')})"
            )
            
            # Handle new user flow
            if cached_onboarding.get('is_new_user'):
                logger.info("🆕 NEW USER DETECTED (from prefetch cache) - Starting onboarding flow")
                
                # Check for referral code
                referral_code = None
                if start_param and start_param.startswith("ref_"):
                    referral_code = start_param[4:]
                    logger.info(f"🔗 Referral code detected: {referral_code}")
                    if context.user_data is not None:
                        context.user_data["pending_referral_code"] = referral_code
                
                # Check for escrow invitation
                if start_param and (start_param.startswith("escrow_") or start_param.startswith("invite_")):
                    logger.info(f"📩 New user accessing escrow invitation: {start_param}")
                    try:
                        return await handle_email_invitation_for_new_user(update, context, start_param)
                    except Exception as e:
                        logger.error(f"❌ Error in email invitation handler: {e}", exc_info=True)
                        await update.message.reply_text("❌ Error processing invitation. Please try again.")
                        return ConversationHandler.END
                
                # Check for pending email invitations
                logger.info("🔍 Checking for pending email invitations for new user")
                async with get_async_session() as session:
                    pending_invitation = await check_pending_invitations_by_telegram_id_with_username(
                        user.id, user.username or "", session
                    )
                
                if pending_invitation:
                    if pending_invitation.get("multiple_invitations"):
                        logger.info(f"📬 Found {pending_invitation['count']} pending invitations for new user")
                        return await show_multiple_pending_invitations(update, context, pending_invitation, user)
                    else:
                        logger.info(f"📬 Found pending invitation for new user: {pending_invitation['escrow_id']}")
                        return await handle_email_invitation_for_new_user_by_telegram(update, context, pending_invitation)
                
                # Route to onboarding
                logger.info("🚀 Routing new user to stateless onboarding router")
                from handlers.onboarding_router import onboarding_router
                await onboarding_router(update, context)
                return ConversationHandler.END
            
            # Handle existing user flow
            else:
                logger.info(f"👤 EXISTING USER (from prefetch cache) - User ID: {cached_onboarding.get('user_id')}")
                
                # Check if user needs onboarding - auto-complete instead of routing to flow
                if not cached_onboarding.get('onboarding_complete'):
                    # Auto-complete onboarding - EMAIL VERIFICATION REMOVED
                    logger.info(f"🚀 Auto-completing onboarding for user {user.id} from cached path - skipping onboarding flow")
                    try:
                        from sqlalchemy import update as sql_update
                        from models import User as UserModel
                        async with async_managed_session() as auto_session:
                            await auto_session.execute(
                                sql_update(UserModel).where(UserModel.id == cached_onboarding.get('user_id')).values(onboarding_completed=True)
                            )
                            await auto_session.commit()

                        # Broadcast new user event to registered groups
                        try:
                            from services.group_event_service import group_event_service
                            import asyncio
                            asyncio.create_task(group_event_service.broadcast_new_user_onboarded({
                                'first_name': user.first_name or 'New User',
                                'username': cached_onboarding.get('username')
                            }))
                        except Exception as grp_err:
                            logger.error(f"Failed to broadcast new user event: {grp_err}")

                        logger.info(f"✅ Auto-completed onboarding for user {user.id} from cached path")
                    except Exception as e:
                        logger.error(f"Error auto-completing onboarding for user {user.id}: {e}")

                    # Handle referral code if present
                    if start_param and start_param.startswith("ref_"):
                        referral_code = start_param[4:]
                        logger.info(f"🔗 Incomplete user {user.id} using referral link: {referral_code}")
                        if context.user_data is None:
                            context.user_data = {}
                        context.user_data["pending_referral_code"] = referral_code
                    
                    # Show main menu directly after auto-complete
                    from types import SimpleNamespace
                    db_user = SimpleNamespace(
                        id=cached_onboarding.get('user_id'),
                        telegram_id=user.id,
                        first_name=user.first_name,
                        username=cached_onboarding.get('username'),
                        email=cached_onboarding.get('email'),
                        email_verified=cached_onboarding.get('email_verified'),
                    )
                    return await show_main_menu(update, context, db_user)
                
                # Handle deep link if present
                if start_param:
                    logger.info(f"🔗 Handling deep link for existing user: {start_param}")
                    
                    if start_param.startswith("ref_"):
                        referral_code = start_param[4:]
                        logger.info(f"🔗 Existing user clicked referral link: {referral_code}")
                        
                        if cached_onboarding.get('referral_code') == referral_code:
                            await update.message.reply_text("😅 You can't use your own referral code!")
                        elif cached_onboarding.get('referred_by_id'):
                            await update.message.reply_text("ℹ️ You're already part of our referral program!")
                        else:
                            await update.message.reply_text("ℹ️ Referral codes can only be used when joining for the first time.")
                        
                        # Show menu (need to construct db_user from cache)
                        from types import SimpleNamespace
                        db_user = SimpleNamespace(
                            id=cached_onboarding.get('user_id'),
                            telegram_id=user.id,
                            first_name=user.first_name,
                            username=cached_onboarding.get('username'),
                            email=cached_onboarding.get('email'),
                            email_verified=cached_onboarding.get('email_verified'),
                        )
                        return await show_main_menu(update, context, db_user)
                
                # EMAIL VERIFICATION REMOVED: OTP was removed from onboarding flow.
                # Users go directly to main menu regardless of email_verified status.
                logger.info(f"✅ Skipping email verification check (OTP removed from onboarding) for user {cached_onboarding.get('user_id')}")
                
                # Show main menu using cached data
                from types import SimpleNamespace
                db_user = SimpleNamespace(
                    id=cached_onboarding.get('user_id'),
                    telegram_id=user.id,
                    first_name=user.first_name,
                    username=cached_onboarding.get('username'),
                    email=cached_onboarding.get('email'),
                    email_verified=cached_onboarding.get('email_verified'),
                    phone_number=cached_onboarding.get('phone_number'),
                )
                
                total_elapsed = time.time() - handler_start_time
                logger.info(
                    f"⚡ ONBOARDING_CACHE_COMPLETE: Total {total_elapsed*1000:.2f}ms "
                    f"(using prefetch cache - minimal DB queries)"
                )
                
                return await show_main_menu(update, context, db_user)
        
        # PRIORITY 2: Use user_cache for complete DB bypass (fallback if no onboarding cache)
        cached_user_data = get_cached_user(str(user.id))
        db_user = None
        
        # Import invalidate_user_cache at module level to avoid unbound reference
        from utils.user_cache import invalidate_user_cache
        
        if cached_user_data:
            cache_start = time.time()
            logger.info(f"⚡⚡⚡ CACHE_HIT: Complete bypass - skipping ALL DB lookups!")
            
            # CRITICAL PERFORMANCE: Create user object from cache to completely skip DB
            from types import SimpleNamespace
            
            # Check if onboarding is completed (default True if field missing for backwards compat)
            needs_onboarding = not cached_user_data.get('onboarding_completed', True)
            
            if needs_onboarding:
                # Auto-complete onboarding - EMAIL VERIFICATION REMOVED
                logger.info(f"🚀 Auto-completing onboarding for cached user - skipping onboarding flow")
                try:
                    from sqlalchemy import update as sql_update
                    from models import User as UserModel
                    async with async_managed_session() as auto_session:
                        await auto_session.execute(
                            sql_update(UserModel).where(UserModel.id == cached_user_data.get('id')).values(onboarding_completed=True)
                        )
                        await auto_session.commit()
                    logger.info(f"✅ Auto-completed onboarding for cached user")
                except Exception as e:
                    logger.error(f"Error auto-completing onboarding: {e}")
                
                # Show main menu directly
                from types import SimpleNamespace
                db_user = SimpleNamespace(
                    id=cached_user_data.get('id'),
                    telegram_id=cached_user_data.get('telegram_id'),
                    first_name=cached_user_data.get('first_name'),
                    username=cached_user_data.get('username'),
                    email=cached_user_data.get('email'),
                    email_verified=cached_user_data.get('email_verified', False),
                )
                async with get_async_session() as session:
                    await show_main_menu_optimized_async(update, context, db_user, session)
                cache_time = time.time() - cache_start
                logger.info(f"⚡ CACHE_COMPLETE: Auto-complete + menu in {cache_time*1000:.1f}ms")
                return ConversationHandler.END
            
            # Reconstruct db_user from cached data
            db_user = SimpleNamespace(
                id=cached_user_data.get('id'),
                telegram_id=cached_user_data.get('telegram_id'),
                first_name=cached_user_data.get('first_name'),
                last_name=cached_user_data.get('last_name'),
                username=cached_user_data.get('username'),
                email=cached_user_data.get('email'),
                email_verified=cached_user_data.get('email_verified', False),
                phone_number=cached_user_data.get('phone_number'),
                is_admin=cached_user_data.get('is_admin', False),
                referral_code=cached_user_data.get('referral_code'),
                referred_by_id=cached_user_data.get('referred_by_id'),
            )
            
            # Handle deep links (some may need DB access)
            if start_param:
                if start_param.startswith("ref_"):
                    referral_code = start_param[4:]
                    if db_user.referral_code == referral_code:
                        await update.message.reply_text("😅 You can't use your own referral code!")
                    elif db_user.referred_by_id:
                        await update.message.reply_text("ℹ️ You're already part of our referral program!")
                    else:
                        await update.message.reply_text("ℹ️ Referral codes can only be used when joining for the first time.")
                    
                    # Show menu from cache
                    async with get_async_session() as session:
                        await show_main_menu_optimized_async(update, context, db_user, session)
                    cache_time = time.time() - cache_start
                    logger.info(f"⚡ CACHE_COMPLETE: Referral flow in {cache_time*1000:.1f}ms")
                    return ConversationHandler.END
                else:
                    # Other deep links need DB access - fall through to DB path
                    logger.info(f"🔗 Deep link requires DB - falling through to full lookup")
                    cached_user_data = None  # Force cache MISS path
            else:
                # FASTEST PATH: Show menu from cache WITHOUT any DB queries
                menu_start = time.time()
                async with get_async_session() as session:
                    await show_main_menu_optimized_async(update, context, db_user, session)
                menu_time = time.time() - menu_start
                
                total_time = time.time() - cache_start
                logger.info(
                    f"⚡⚡⚡ CACHE_BYPASS_COMPLETE: Total {total_time*1000:.1f}ms "
                    f"(cache: ~5ms, menu: {menu_time*1000:.1f}ms) - ZERO DB LOOKUPS!"
                )
                return ConversationHandler.END
        
        elif not cached_user_data:
            logger.info(f"⏱️ PERF: Cache MISS for user {user.id} - calling shared session helper")
            # PERFORMANCE OPTIMIZATION: Call helper function with ONE shared session for all operations
            connection_start = time.time()
            try:
                # Call the optimized helper that uses ONE shared session for the entire flow
                result = await process_existing_user_async(
                    update=update,
                    context=context,
                    user_telegram_id=user.id,
                    start_param=start_param,
                    handler_start_time=handler_start_time
                )
                
                # Record successful connection
                pool_monitor.record_connection_acquisition(time.time() - connection_start, True)
                
                # If helper successfully processed the user, return its result
                if result is not None:
                    logger.info(f"✅ Shared session helper completed successfully")
                    return result
                
                # If helper returned None (user not found), set db_user to None to trigger new user flow
                db_user = None
                logger.info(f"⚠️ Shared session helper returned None - treating as new user")
                    
            except Exception as e:
                pool_monitor.record_connection_acquisition(time.time() - connection_start, False)
                logger.error(f"❌ Shared session helper error: {e}", exc_info=True)
                
                # FALLBACK: Use degraded mode for database errors
                from types import SimpleNamespace
                db_user = SimpleNamespace(
                    id=None,
                    telegram_id=str(user.id),
                    username=user.username,
                    first_name=user.first_name,
                    last_name=user.last_name,
                    is_admin=False,
                    email_verified=False,
                    display_name=user.first_name or user.username or f"User {user.id}"
                )
                logger.warning(f"🔄 Using degraded mode for user {user.id} due to helper error: {e}")

        if not db_user:
            logger.info("🆕 NEW USER DETECTED - Starting onboarding flow")

            # Check if this is a referral code
            referral_code = None
            if start_param and start_param.startswith("ref_"):
                referral_code = start_param[4:]  # Remove 'ref_' prefix
                logger.info(f"🔗 Referral code detected: {referral_code}")
                # Store referral code for after user creation
                if context.user_data is not None:
                    context.user_data["pending_referral_code"] = referral_code

            # Check if this is an escrow invitation for email seller
            if start_param and (
                start_param.startswith("escrow_") or start_param.startswith("invite_")
            ):
                logger.info(f"📩 New user accessing escrow invitation: {start_param}")
                try:
                    return await handle_email_invitation_for_new_user(
                        update, context, start_param
                    )
                except Exception as e:
                    logger.error(
                        f"❌ Error in email invitation handler: {e}", exc_info=True
                    )
                    await update.message.reply_text(
                        "❌ Error processing invitation. Please try again."
                    )
                    return ConversationHandler.END

            # Check for pending email invitations even for new users
            logger.info("🔍 Checking for pending email invitations for new user")
            async with get_async_session() as session:
                pending_invitation = (
                    await check_pending_invitations_by_telegram_id_with_username(
                        user.id, user.username or "", session
                    )
                )
            if pending_invitation:
                if pending_invitation.get("multiple_invitations"):
                    logger.info(
                        f"📬 Found {pending_invitation['count']} pending invitations for new user"
                    )
                    return await show_multiple_pending_invitations(
                        update, context, pending_invitation, user
                    )
                else:
                    logger.info(
                        f"📬 Found pending invitation for new user: {pending_invitation['escrow_id']}"
                    )
                    return await handle_email_invitation_for_new_user_by_telegram(
                        update, context, pending_invitation
                    )

            logger.info("🎯 NEW USER - Calling start_onboarding function")
            # PHASE 2A: Use new stateless onboarding router for new users
            logger.info("🚀 PHASE 2A: Routing new user to stateless onboarding router")
            from handlers.onboarding_router import onboarding_router
            await onboarding_router(update, context)
            return ConversationHandler.END

        logger.info(f"👤 EXISTING USER: {db_user.first_name} (ID: {db_user.id})")

        # PHASE 2A: Check if existing user needs onboarding - auto-complete instead of routing to flow
        # EMAIL VERIFICATION REMOVED: OTP was removed from onboarding flow.
        needs_onboarding = (
            not hasattr(db_user, 'onboarding_completed') or 
            not bool(db_user.onboarding_completed)
        )
        if needs_onboarding:
            # Auto-complete onboarding instead of routing to onboarding flow
            logger.info(f"🚀 Auto-completing onboarding for user {user.id} - skipping onboarding flow")
            try:
                from database import async_managed_session as _async_managed_session
                from sqlalchemy import update as sql_update
                from models import User as UserModel
                async with _async_managed_session() as auto_session:
                    await auto_session.execute(
                        sql_update(UserModel).where(UserModel.id == db_user.id).values(onboarding_completed=True)
                    )
                    await auto_session.commit()

                # Broadcast new user event to registered groups
                try:
                    from services.group_event_service import group_event_service
                    import asyncio
                    asyncio.create_task(group_event_service.broadcast_new_user_onboarded({
                        'first_name': db_user.first_name or 'New User',
                        'username': db_user.username
                    }))
                except Exception as grp_err:
                    logger.error(f"Failed to broadcast new user event: {grp_err}")

                logger.info(f"✅ Auto-completed onboarding for user {user.id}")
            except Exception as e:
                logger.error(f"Error auto-completing onboarding for user {user.id}: {e}")

            # Show main menu directly
            async with async_managed_session() as menu_session:
                await show_main_menu_optimized_async(update, context, db_user, menu_session)
            return ConversationHandler.END

        # RESILIENCE GUARD: Re-apply full commands for onboarded users as safety measure
        # This ensures commands are restored even if startup migration fails
        try:
            from utils.bot_commands import BotCommandsManager
            bot = context.bot
            if bot:
                # Silently re-apply full commands without blocking
                import asyncio
                asyncio.create_task(
                    BotCommandsManager.set_user_commands(
                        user_id=user.id,
                        is_onboarded=True,
                        bot=bot
                    )
                )
                logger.debug(f"🔒 RESILIENCE_GUARD: Re-applying full commands for onboarded user {user.id}")
        except Exception as e:
            # Don't let this fail the /start command - it's just a safety measure
            logger.debug(f"⚠️ RESILIENCE_GUARD: Failed to re-apply commands for user {user.id}: {e}")

        # ASYNC FIX: Use async session instead of blocking sync session
        from database import async_managed_session
        
        # Extract all needed user data using async session
        user_id_db = None
        user_email = None
        user_email_verified = False
        user_referral_code = None
        user_referred_by = None
        user_first_name = None
        
        # Async user info update with retry logic
        max_retries = 3
        for attempt in range(max_retries):
            try:
                async with async_managed_session() as session:
                    # Get fresh user from database with async query
                    result = await session.execute(select(User).where(User.telegram_id == user.id))
                    fresh_user = result.scalar_one_or_none()
                    if fresh_user:
                        db_user = update_user_from_telegram(fresh_user, user)
                        await session.commit()
                        logger.info("✅ User info updated in database")
                        
                        # Extract all user data while session is open
                        user_id_db = db_user.id if db_user.id is not None else None
                        user_email = db_user.email if db_user.email is not None else None
                        user_email_verified = getattr(db_user, 'email_verified', False) or False
                        user_referral_code = getattr(db_user, 'referral_code', None)
                        user_referred_by = getattr(db_user, 'referred_by_id', None) 
                        user_first_name = db_user.first_name if db_user.first_name is not None else None
                        break  # Success, exit retry loop
                    else:
                        logger.error("User not found during update")
                        break
            except Exception as e:
                if "SSL connection has been closed unexpectedly" in str(e) and attempt < max_retries - 1:
                    logger.warning(f"⚠️ SSL connection drop on attempt {attempt + 1}/3, retrying user update...")
                    continue  # Retry
                else:
                    logger.error(f"Error updating user info: {e}")
                    # Set fallback values to prevent downstream errors
                    if 'db_user' in locals() and db_user:
                        user_id_db = getattr(db_user, 'id', None)
                        user_email = getattr(db_user, 'email', None) 
                        user_email_verified = getattr(db_user, 'email_verified', False)
                        user_referral_code = getattr(db_user, 'referral_code', None)
                        user_referred_by = getattr(db_user, 'referred_by_id', None)
                        user_first_name = getattr(db_user, 'first_name', None)
                    break

        # Handle deep link if present
        if start_param:
            logger.info(f"🔗 Handling deep link for existing user: {start_param}")

            # Check if this is a referral code for existing user
            if start_param.startswith("ref_"):
                referral_code = start_param[4:]  # Remove 'ref_' prefix
                logger.info(f"🔗 Existing user clicked referral link: {referral_code}")

                # Show message that they can't use their own referral or already have referrer
                # Use extracted values instead of accessing db_user

                if user_referral_code == referral_code:
                    await update.message.reply_text(
                        "😅 You can't use your own referral code!"
                    )
                elif user_referred_by:
                    await update.message.reply_text(
                        "ℹ️ You're already part of our referral program!"
                    )
                else:
                    await update.message.reply_text(
                        "ℹ️ Referral codes can only be used when joining for the first time."
                    )

                # Show main menu after message
                logger.info("📱 Showing main menu after referral message")
                return await show_main_menu(update, context, db_user)

            logger.info(f"🔗 Calling handle_deep_link for: {start_param}")
            return await handle_deep_link(update, context, start_param, db_user)
        else:
            # PERFORMANCE: Quick invitation check with caching
            logger.info("Checking for pending invitations for existing user")
            try:
                # OPTIMIZATION: Skip invitation check for recently active users
                import time
                # Use telegram user ID instead of db_user.id to avoid session issues
                cache_key = f"inv_check_{user.id}"
                current_time = time.time()
                
                # Skip invitation check if user was active in last 5 minutes
                last_check = getattr(context, 'user_data', {}).get(cache_key, 0) if context.user_data else 0
                if current_time - last_check < (Config.USER_CACHE_TTL_MINUTES * 60):  # Configurable minutes cache
                    logger.info("Skipping invitation check - recent activity")
                    pending_invitation = None
                else:
                    # Fast invitation check with async session
                    async with get_async_session() as session:
                        # Get user data directly from session to avoid lazy loading issues
                        result = await session.execute(select(User).where(User.telegram_id == user.id))
                        fresh_user = result.scalar_one_or_none()
                        if fresh_user:
                            # Call optimized function with user ID and email to avoid object dependency
                            # CRITICAL FIX: Handle None email by providing empty string
                            user_email = fresh_user.email if fresh_user.email is not None else ""
                            result = await check_pending_invitations_by_user_data(fresh_user.id, user_email, session)
                            pending_invitation = result
                        else:
                            pending_invitation = None
                    
                    # Cache the check result (avoid user_data assignment error)
                    try:
                        if context.user_data is not None:
                            context.user_data[cache_key] = current_time
                        else:
                            logger.debug("user_data is None, skipping cache update")
                    except Exception as cache_error:
                        logger.debug(f"Cache update failed: {cache_error}")
            except Exception as e:
                logger.warning(f"Invitation check failed: {e}")
                pending_invitation = None
            
            # STORE pending invitations for notification badge (don't force user into flow)
            if pending_invitation and isinstance(pending_invitation, dict):
                if pending_invitation.get("multiple_invitations"):
                    logger.info(
                        f"Found {pending_invitation.get('count', 0)} pending invitations - showing main menu with notification"
                    )
                    # Store in context for main menu notification
                    if context.user_data is not None:
                        context.user_data["pending_invitations"] = pending_invitation
                else:
                    logger.info(
                        f"Found pending invitation for escrow: {pending_invitation.get('escrow_id', 'unknown')} - showing main menu with notification"
                    )
                    # Store in context for main menu notification
                    if context.user_data is not None:
                        context.user_data["pending_invitations"] = pending_invitation

        # EMAIL VERIFICATION REMOVED: OTP was removed from onboarding flow.
        # Users go directly to main menu regardless of email_verified status.
        logger.info(f"✅ Skipping email verification check (OTP removed from onboarding) for user {user_id_db}")
        
        # Typing indicator already sent at the beginning - no need to send again
        total_elapsed = time.time() - handler_start_time
        logger.info(f"⏱️ PERF: START HANDLER at {total_elapsed*1000:.2f}ms - Loading main menu with async queries")
        
        # PERFORMANCE OPTIMIZATION: Use async session instead of blocking sync session
        from database import async_managed_session
        async with async_managed_session() as session:
            result = await session.execute(select(User).where(User.telegram_id == user.id))
            fresh_db_user = result.scalar_one_or_none()
            if fresh_db_user:
                await show_main_menu_optimized_async(update, context, fresh_db_user, session)
                total_elapsed = time.time() - handler_start_time
                logger.info(f"⚡ PERF: START HANDLER completed in {total_elapsed*1000:.2f}ms - Main menu sent")
            else:
                logger.error(f"Could not find user {user.id} for main menu")

    except TelegramError as e:
        # Handle "Message is not modified" error silently
        if "Message is not modified" in str(e):
            return
        logger.error(f"Telegram error in start handler: {e}")
    except SQLAlchemyError as e:
        logger.error(f"Database error in start handler: {e}")
        
        # Provide user-friendly fallback for database errors
        try:
            if update.message:
                await update.message.reply_text(
                    "🔧 We're experiencing some technical difficulties.\n\n"
                    "✨ Please try again in a moment or contact support if the issue persists.\n\n"
                    "🔄 Use /start to retry",
                    parse_mode="Markdown"
                )
            elif update.callback_query and update.callback_query.message:
                from telegram import Message
                if isinstance(update.callback_query.message, Message):
                    await update.callback_query.message.reply_text(
                        "🔧 Technical issue detected. Please try /start again.",
                        parse_mode="Markdown"
                    )
        except Exception:
            pass  # Prevent infinite error loops
    except Exception as e:
        logger.error(f"Unexpected error in start handler: {e}", exc_info=True)
        
        # Send user-friendly error message
        if update.message:
            try:
                await update.message.reply_text(
                    "😅 Oops! Something went wrong\n\n💡 Try /start to restart",
                    parse_mode="Markdown",
                )
            except Exception:
                pass  # Prevent nested loops

@conversation_wrapper(timeout_minutes=Config.CONVERSATION_TIMEOUT_MINUTES)
async def start_onboarding(
    update: Update, context: ContextTypes.DEFAULT_TYPE, start_param=None
) -> int:
    """Enhanced onboarding process with navigation reliability and analytics"""
    logger.info(
        f"🎯 start_onboarding called for user {update.effective_user.id if update.effective_user else 'Unknown'}"
    )

    user = update.effective_user

    if not user:
        logger.error("❌ No effective user found in start_onboarding")
        return ConversationHandler.END

    # Store start parameter for later use
    if start_param and context.user_data is not None:
        context.user_data["start_param"] = start_param

    # Check if this is a referral signup
    referral_code = None
    referrer_name = None
    referee_bonus_amount = Decimal("5.0")  # Default bonus amount
    
    if context.user_data and context.user_data.get("pending_referral_code"):
        referral_code = context.user_data["pending_referral_code"]
        
        # Look up referrer's name for personalized welcome
        try:
            from utils.referral import ReferralSystem
            from sqlalchemy import func
            
            async with get_async_session() as session:
                result = await session.execute(
                    select(User).filter(func.upper(User.referral_code) == referral_code.upper())
                )
                referrer = result.scalar_one_or_none()
                if referrer:
                    referrer_name = referrer.first_name or referrer.username or "A friend"
                    referee_bonus_amount = ReferralSystem.REFEREE_REWARD_USD
                    logger.info(f"Referral landing page: User referred by {referrer_name}")
        except Exception as e:
            logger.error(f"Error looking up referrer for landing page: {e}")

    # ADAPTIVE LANDING PAGE: Different message based on referral status
    if referrer_name:
        # REFERRAL VERSION: Personalized with referrer name and bonus
        welcome_caption = f"""🎉 Welcome to {Config.PLATFORM_NAME}!

👤 {referrer_name} invited you

Get ${referee_bonus_amount} USD bonus instantly!

🛡️ Escrow • 💱 Exchange • ⚡ Fast"""
        
        button_text = "✨ Claim Bonus & Start"
    else:
        # STANDARD VERSION: Clean and professional
        welcome_caption = f"""🔒 {Config.PLATFORM_NAME}

Secure crypto trades & instant cashouts

🛡️ Escrow • 💱 Exchange • ⚡ Fast"""
        
        button_text = "🚀 Get Started"

    # UX IMPROVEMENT: Single clear call-to-action
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    keyboard = [
        [InlineKeyboardButton(button_text, callback_data="start_email_input")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Use local stock image for landing page
    import os
    welcome_photo_path = os.path.join("attached_assets", "stock_images", "modern_digital_payme_d71509ef.jpg")

    if update.message:
        try:
            # Send photo with caption and interactive buttons
            with open(welcome_photo_path, 'rb') as photo:
                await update.message.reply_photo(
                    photo=photo,
                    caption=welcome_caption,
                    parse_mode="HTML",
                    reply_markup=reply_markup,
                )
        except Exception as e:
            # Fallback to text if photo fails
            logger.warning(f"Failed to send welcome photo: {e}, falling back to text")
            await update.message.reply_text(
                welcome_caption, parse_mode="HTML", reply_markup=reply_markup
            )

    return OnboardingStates.ONBOARDING_SHOWCASE  # New state for explore mode

async def handle_explore_demo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle explore demo mode - show features without signup"""
    query = update.callback_query
    if query:
        # IMMEDIATE FEEDBACK: Specific exploration action
        await safe_answer_callback_query(query, "🔍 Exploring features...")

    demo_text = f"""🔍 <b>{Config.PLATFORM_NAME} Demo</b>

💱 <b>Quick Exchange</b> - Crypto to cash in {Config.AVERAGE_PROCESSING_TIME_MINUTES} min
🛡️ <b>Safe Trading</b> - ${int(Config.MIN_ESCROW_AMOUNT_USD)}+ peer-to-peer trades  
💰 <b>Multi-Wallet</b> - USD, BTC, ETH, USDT

📊 <b>{Config.PLATFORM_VOLUME_CLAIM} traded</b> • <b>{Config.PLATFORM_USER_COUNT_CLAIM} users</b> • <b>{Config.PLATFORM_UPTIME_CLAIM} uptime</b>

Ready to start?"""

    keyboard = [
        [InlineKeyboardButton("📧 Sign Up Now", callback_data="start_email_input")],
        [InlineKeyboardButton("🔄 Quick Exchange Demo", callback_data="demo_exchange")],
        [InlineKeyboardButton("🛡️ Trade Demo", callback_data="demo_escrow")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="back_to_welcome")],
    ]

    if query:
        from utils.message_editor import safe_edit

        await safe_edit(update, demo_text, reply_markup=InlineKeyboardMarkup(keyboard))

    return OnboardingStates.ONBOARDING_SHOWCASE

async def handle_demo_exchange(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show Quick Exchange demo"""
    query = update.callback_query
    if query:
        # IMMEDIATE FEEDBACK: Demo selection
        await safe_answer_callback_query(query, "💱 Quick Exchange demo")

    demo_text = f"""💱 <b>Quick Exchange</b>

<b>How:</b> Pick crypto → Enter amount → Get cash
<b>Example:</b> ${int(Config.SMALL_TRADE_EXAMPLE_USD)} → ₦{int(Config.SMALL_TRADE_EXAMPLE_USD * 1650):,} ({Config.AVERAGE_PROCESSING_TIME_MINUTES} min)
<b>Fee:</b> {float(Config.EXCHANGE_MARKUP_PERCENTAGE)}% • <b>Min:</b> ${int(Config.MIN_EXCHANGE_AMOUNT_USD)} • <b>Max:</b> ${int(Config.MAX_EXCHANGE_AMOUNT_USD)}

Ready to try?"""

    keyboard = [
        [
            InlineKeyboardButton(
                "🚀 Start Real Exchange", callback_data="start_email_input"
            )
        ],
        [InlineKeyboardButton("🛡️ See Trade Demo", callback_data="demo_escrow")],
        [InlineKeyboardButton("⬅️ Back to Features", callback_data="explore_demo")],
    ]

    if query:
        from utils.message_editor import safe_edit

        await safe_edit(update, demo_text, reply_markup=InlineKeyboardMarkup(keyboard))

    return OnboardingStates.ONBOARDING_SHOWCASE

async def handle_demo_escrow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show Trade demo"""
    query = update.callback_query
    if query:
        # IMMEDIATE FEEDBACK: Demo selection
        await safe_answer_callback_query(query, "🛡️ Secure Trade demo")

    demo_text = """🛡️ <b>Safe Trading</b>

<b>How:</b> Create trade → Invite seller → Funds locked → Service delivered → Auto-release
<b>Example:</b> ${int(Config.LARGE_TRADE_EXAMPLE_USD)} Website Design ({Config.MAX_DELIVERY_HOURS_CLAIM}h deadline)
<b>Min:</b> ${int(Config.MIN_ESCROW_AMOUNT_USD)} • <b>Security:</b> Full refund guarantee

Ready to trade?"""

    keyboard = [
        [
            InlineKeyboardButton(
                "🚀 Start Real Trading", callback_data="start_email_input"
            )
        ],
        [InlineKeyboardButton("💱 See Exchange Demo", callback_data="demo_exchange")],
        [InlineKeyboardButton("⬅️ Back to Features", callback_data="explore_demo")],
    ]

    if query:
        from utils.message_editor import safe_edit

        await safe_edit(update, demo_text, reply_markup=InlineKeyboardMarkup(keyboard))

    return OnboardingStates.ONBOARDING_SHOWCASE

async def handle_start_email_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle the 'Enter Email & Start' button click"""
    query = update.callback_query
    if not query:
        logger.error("❌ handle_start_email_input: No callback query found")
        return OnboardingStates.ONBOARDING_SHOWCASE
    
    user_id = update.effective_user.id if update.effective_user else "Unknown"
    logger.info(f"🔄 handle_start_email_input: Processing email input request for user {user_id}")
    
    try:
        # IMMEDIATE FEEDBACK: Email input start
        await safe_answer_callback_query(query, "📧 Starting email input...")

        email_prompt = """<b>📧 Enter Email</b>

<i>We'll send a verification code.</i>

<b>Type email below:</b>"""

        from utils.message_editor import safe_edit

        # Enhanced error handling for message editing
        edit_success = await safe_edit(update, email_prompt, parse_mode="HTML")
        
        if edit_success:
            logger.info(f"✅ handle_start_email_input: Successfully updated message for user {user_id}")
        else:
            logger.warning(f"⚠️ handle_start_email_input: Message edit may have failed for user {user_id}")
        
        logger.info(f"🔄 handle_start_email_input: Transitioning to COLLECTING_EMAIL state for user {user_id}")
        return OnboardingStates.COLLECTING_EMAIL
        
    except Exception as e:
        logger.error(f"❌ handle_start_email_input: Error processing request for user {user_id}: {e}")
        # Fallback: provide feedback even if editing fails
        try:
            await safe_answer_callback_query(query, "❌ Error starting email input. Please try again.")
        except Exception as e:
            pass
        return OnboardingStates.ONBOARDING_SHOWCASE


async def handle_invitation_decide_later(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Handle the new 'Setup Account First' button from invitation flow
    MODERNIZED: Routes to modern onboarding router instead of old conversation handler
    """
    query = update.callback_query
    if not query:
        logger.error("❌ handle_invitation_decide_later: No callback query found")
        return ConversationHandler.END
    
    user_id = update.effective_user.id if update.effective_user else "Unknown"
    logger.info(f"🔄 handle_invitation_decide_later: Processing modern onboarding request for user {user_id}")
    
    try:
        # INSTANT FEEDBACK: Account setup start
        await safe_answer_callback_query(query, "🚀 Setting up your account...")

        # MODERN PATTERN: Route to modern onboarding router instead of old conversation handler
        logger.info(f"🚀 MODERN ROUTING: Directing invitation user {user_id} to modern onboarding router")
        
        # Import and call modern onboarding router
        from handlers.onboarding_router import onboarding_router
        
        # Create a proper context for the onboarding router
        user = update.effective_user
        if user:
            # The onboarding router can handle callback queries properly
            await onboarding_router(update, context)
            
        return ConversationHandler.END
        
    except Exception as e:
        logger.error(f"❌ handle_invitation_decide_later: Error processing request for user {user_id}: {e}")
        
        # ENHANCED ERROR HANDLING: User-friendly error message with fallback
        try:
            await safe_answer_callback_query(query, "❌ Error setting up account. Please try /start")
        except Exception as e:
            pass
            
        # Fallback: Try manual onboarding start
        try:
            from handlers.onboarding_router import onboarding_router
            await onboarding_router(update, context)
        except Exception as fallback_error:
            logger.error(f"❌ Fallback onboarding also failed for user {user_id}: {fallback_error}")
            
        return ConversationHandler.END

# Removed show_help_from_onboarding_callback - now using URL button

async def handle_back_to_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle going back to the welcome screen"""
    query = update.callback_query
    if query:
        # IMMEDIATE FEEDBACK: Back to welcome
        await safe_answer_callback_query(query, "🏠 Back to welcome")

        # Get user info for personalized welcome
        telegram_user = update.effective_user
        if telegram_user:
            pass
        else:
            pass

        # Recreate the original welcome message with fee transparency
        welcome_caption = f"""<b>🎉 Welcome to {Config.PLATFORM_NAME}!</b>

💱 <b>Crypto → Cash</b> (5m) • 🛡️ <b>Safe Escrow</b>
💵 <b>5% Fee</b> (refundable)

<b>🚀 Ready?</b>"""

        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        keyboard = [
            [
                InlineKeyboardButton(
                    "🚀 Get Started", callback_data="start_email_input"
                )
            ],
            [
                InlineKeyboardButton(
                    "ℹ️ Learn More", callback_data="show_help_onboarding"
                )
            ],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        from utils.message_editor import safe_edit

        await safe_edit(
            update, welcome_caption, reply_markup=reply_markup, parse_mode="HTML"
        )

    return OnboardingStates.COLLECTING_EMAIL

async def collect_email(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Collect email during onboarding - now mandatory with OTP verification"""
    logger.info(
        f"📧 collect_email ENTRY: User {update.effective_user.id if update.effective_user else 'Unknown'} in COLLECTING_EMAIL state"
    )
    if update.message and update.message.text:
        logger.info("📧 collect_email: Processing message text")
        # Check if admin is trying to use commands - bypass onboarding
        from utils.admin_security import is_admin_silent

        if update.effective_user and is_admin_silent(update.effective_user.id):
            text = update.message.text.strip()
            if text.startswith("/") or text in [
                "admin",
                "broadcast",
                "hello all",
                "Hi there",
            ]:
                # Admin is trying to use commands, end conversation and let command handlers take over
                logger.info(
                    f"Admin user {update.effective_user.id} bypassing onboarding for command: {text}"
                )
                # CRITICAL FIX: End conversation but DON'T call show_main_menu 
                # Let the actual command handlers process the command
                return ConversationHandler.END

        logger.info(
            "📧 collect_email: Admin check passed, proceeding with email validation"
        )
        
        # PRODUCTION FIX: Check for conversation conflicts
        from utils.conversation_isolation import check_conversation_conflict
        if not check_conversation_conflict(context, "onboarding"):
            return ConversationHandler.END
            
        # Secure input validation and sanitization
        from utils.enhanced_input_validation import SecurityInputValidator
        
        validation_result = SecurityInputValidator.validate_and_sanitize_input(
            update.message.text,
            "email",
            max_length=Config.MAX_EMAIL_LENGTH,  # RFC configurable limit
            required=True
        )
        
        if not validation_result["is_valid"]:
            logger.info(f"📧 collect_email: Input security validation failed")
            await update.message.reply_text("📧 Invalid input. Please enter a valid email:")
            return OnboardingStates.COLLECTING_EMAIL
        
        text = validation_result["sanitized_value"].strip()
        logger.info(f"📧 collect_email: Validating email: {text}")
        try:
            is_valid = validate_email(text)
            logger.info(
                f"📧 collect_email: Email validation result: {is_valid} for {text}"
            )
            if not is_valid:
                logger.info(f"📧 collect_email: Invalid email format: {text}")
                await update.message.reply_text("📧 Invalid email. Try again:")
                return OnboardingStates.COLLECTING_EMAIL
        except Exception as e:
            logger.error(
                f"📧 collect_email: Email validation EXCEPTION: {e} for {text}"
            )
            await update.message.reply_text("📧 Email validation error. Try again:")
            return OnboardingStates.COLLECTING_EMAIL

        logger.info(
            f"📧 collect_email: Email validation PASSED for {text} - proceeding with OTP generation"
        )
        
        # Check if this is trade acceptance email collection
        if context.user_data and context.user_data.get("email_collection_flow"):
            logger.info("📧 collect_email: Detected trade acceptance email collection flow")
            
            # Store email for trade acceptance
            context.user_data["pending_email"] = text
            
            # Generate OTP and send verification email using existing logic
            from services.email import EmailService
            import random
            import string
            
            # Generate cryptographically secure 6-digit OTP
            from utils.secure_crypto import SecureCrypto
            otp = SecureCrypto.generate_secure_otp(6)
            context.user_data["otp"] = otp
            context.user_data["otp_email"] = text
            
            logger.info(f"📧 collect_email: OTP generated for trade acceptance: {otp[:2]}****")
            
            try:
                # Send OTP email using generic send_email method
                email_service = EmailService()
                
                # Check if email service is enabled
                if not email_service.enabled:
                    logger.warning("Email service is disabled - proceeding without OTP verification for trade acceptance")
                    # Skip email verification for trade acceptance when email is disabled
                    await update.message.reply_text(
                        f"📧 Email verification skipped (service disabled)\n\n"
                        f"✅ Proceeding with trade acceptance for {text}..."
                    )
                    
                    # Process trade acceptance directly
                    async with get_async_session() as session:
                        escrow_id = context.user_data.get("pending_trade_acceptance", {}).get("escrow_id", "")
                        return await complete_trade_acceptance_with_email(update, context, escrow_id, text, session)
                
                # PERFORMANCE FIX: Use background queue instead of blocking email send
                import time
                email_start = time.time()
                
                logger.info(f"⏱️ PERF: Starting background email queue for trade acceptance to {text}")
                from services.background_email_queue import background_email_queue
                
                # CRITICAL FIX: Ensure user is not None before accessing properties
                user = update.effective_user
                if not user:
                    await update.message.reply_text("❌ User session expired. Please try again.")
                    return ConversationHandler.END
                
                queue_result = await background_email_queue.queue_otp_email(
                    recipient=text,
                    otp_code=otp,
                    purpose="trade_acceptance",
                    user_id=user.id,
                    user_name=user.first_name or "User"
                )
                
                email_elapsed = time.time() - email_start
                logger.info(f"⏱️ PERF: Email queued in {email_elapsed*1000:.2f}ms (was blocking 2-5s)")
                
                if queue_result.get("success"):
                    logger.info(f"✅ OTP email queued successfully to {text} - Job ID: {queue_result.get('job_id')}")
                    verification_message = f"""📧 Verification email queued!

Check your inbox for: {text}

Enter the 6-digit code to complete trade acceptance:"""

                    await update.message.reply_text(verification_message, parse_mode="Markdown")
                    return OnboardingStates.VERIFYING_EMAIL_OTP
                else:
                    logger.error(f"❌ Failed to queue OTP email for trade acceptance to {text}: {queue_result.get('error')}")
                    await update.message.reply_text(
                        f"❌ Failed to send verification email. Please try another email:"
                    )
                    return OnboardingStates.COLLECTING_EMAIL
                    
            except Exception as e:
                logger.error(f"❌ Error queuing OTP email for trade acceptance: {e}")
                await update.message.reply_text(
                    f"❌ Error sending verification email. Please try another email:"
                )
                return OnboardingStates.COLLECTING_EMAIL
        
        # Store valid email and send OTP for regular onboarding
        if context.user_data is not None:
            context.user_data["email"] = text

        # Use centralized email verification system for onboarding
        # EmailOTPService integrated into this handler
        logger.info(f"📧 collect_email: Starting OTP generation process for {text}")

        session = None
        try:
            from models import User, EmailVerification
            from datetime import datetime, timedelta, timezone

            logger.info("📧 collect_email: Imported required modules successfully")

            session = SyncSessionLocal()
            logger.info("📧 collect_email: Database session created")

            user_id = update.effective_user.id if update.effective_user else 0
            logger.info(f"📧 collect_email: Querying user with telegram_id: {user_id}")
            result = session.execute(select(User).where(User.telegram_id == user_id))
            user = result.scalar_one_or_none()
            logger.info(
                f"📧 collect_email: User query result: {'Found' if user else 'Not found'}"
            )

            # Create user record if it doesn't exist (onboarding flow)
            if not user:
                logger.info(
                    f"📧 collect_email: Creating new user record for telegram_id: {user_id}"
                )
                user = User(
                    telegram_id=str(user_id),
                    username=(
                        update.effective_user.username
                        if update.effective_user
                        else None
                    ),
                    email=text,
                    email_verified=False,
                )
                session.add(user)
                session.commit()  # Commit to get the user.id
                logger.info(f"📧 collect_email: New user created with ID: {user.id}")
            else:
                logger.info(
                    f"📧 collect_email: Updating user email in database for user_id: {user.id}"
                )
                # Update email in user record during onboarding safely
                from utils.secure_database import secure_db
                
                # Use secure database operations to prevent SQL injection
                from utils.orm_typing_helpers import as_int
                email_updated = secure_db.safe_update_user_field(
                    session=session,
                    user_id=as_int(user.id),
                    field_name="email",
                    field_value=text
                )
                
                if email_updated:
                    verified_updated = secure_db.safe_update_user_field(
                        session=session,
                        user_id=as_int(user.id),
                        field_name="email_verified",
                        field_value=False
                    )
                    
                    if not verified_updated:
                        logger.warning(f"Failed to update email_verified for user {user.id}")
                # Commit is handled by secure_db operations
                logger.info("📧 collect_email: User email updated successfully")

            # Continue with OTP generation for both new and existing users
            logger.info(
                f"📧 collect_email: Proceeding with OTP generation for user_id: {user.id}"
            )

            # Generate OTP for onboarding verification
            logger.info("📧 collect_email: Generating OTP code")
            # Generate cryptographically secure OTP
            from utils.secure_crypto import SecureCrypto
            otp = SecureCrypto.generate_secure_otp(6)
            expiry_time = datetime.now(timezone.utc) + timedelta(minutes=Config.VERIFICATION_EXPIRY_MINUTES)
            logger.info(f"📧 collect_email: OTP generated successfully: [REDACTED]")

            # CRITICAL FIX: Check for existing OTP to prevent race conditions
            existing_otp = session.query(EmailVerification).filter(
                EmailVerification.user_id == user.id, 
                EmailVerification.email == text,
                EmailVerification.expires_at > datetime.now(timezone.utc)
            ).first()
            
            if existing_otp:
                logger.info(f"📧 collect_email: Valid OTP already exists for user {user.id}, using existing code")
                from utils.orm_typing_helpers import as_str, as_datetime
                otp = as_str(existing_otp.verification_code) or ""
                expiry_time = as_datetime(existing_otp.expires_at) or datetime.now(timezone.utc) + timedelta(minutes=Config.VERIFICATION_EXPIRY_MINUTES)
            else:
                # UNIFIED SYSTEM: Clean existing records and store in EmailVerification table
                logger.info("📧 collect_email: Cleaning existing verification records")
                # Clean existing verification records for this user and email
                session.query(EmailVerification).filter(
                    EmailVerification.user_id == user.id, EmailVerification.email == text
                ).delete()
                logger.info("📧 collect_email: Creating new verification record")

                # FIXED: Use correct schema with proper column names and atomic UPSERT
                # Actual DB columns: user_id, email, verification_code, is_verified, expires_at, purpose, attempts, max_attempts, created_at
                logger.info(
                    "📧 collect_email: Inserting verification record with correct schema"
                )
                from sqlalchemy import text as sql_text

                # ATOMIC UPSERT to prevent race conditions
                session.execute(
                    sql_text(
                        """
                        INSERT INTO email_verifications (user_id, email, verification_code, verified, expires_at, purpose, attempts, max_attempts, created_at)
                        VALUES (:user_id, :email, :verification_code, :verified, :expires_at, :purpose, :attempts, :max_attempts, :created_at)
                        ON CONFLICT (user_id, email, purpose) DO UPDATE SET
                            verification_code = EXCLUDED.verification_code,
                            verified = EXCLUDED.verified,
                            expires_at = EXCLUDED.expires_at,
                            attempts = 0,
                            created_at = EXCLUDED.created_at
                    """
                    ),
                    {
                        "user_id": user.id,
                        "email": text,
                        "verification_code": otp,
                        "verified": False,
                        "expires_at": expiry_time,
                        "purpose": "registration",  # FIX: Align with OnboardingService
                        "attempts": 0,
                        "max_attempts": 5,
                        "created_at": datetime.now(timezone.utc),
                    },
                )
            logger.info("📧 collect_email: Committing verification record to database")

            session.commit()
            logger.info("📧 collect_email: Verification record committed successfully")

            # PERFORMANCE FIX: Use background queue instead of blocking email send
            import time
            email_start = time.time()
            
            logger.info(f"⏱️ PERF: Starting background email queue for onboarding to {text}")
            user_name = (
                update.effective_user.first_name if update.effective_user else "there"
            )
            from services.background_email_queue import background_email_queue

            queue_result = await background_email_queue.queue_otp_email(
                recipient=text,
                otp_code=otp,
                purpose="registration",  # FIX: Align with OnboardingService
                user_id=user.id,
                user_name=user_name
            )
            
            email_elapsed = time.time() - email_start
            logger.info(f"⏱️ PERF: Email queued in {email_elapsed*1000:.2f}ms (was blocking 2-5s)")
            
            email_sent = queue_result.get("success")
            logger.info(f"📧 collect_email: Email queuing result: {email_sent}")

            if email_sent:
                keyboard = [
                    [
                        InlineKeyboardButton(
                            "🔄 Change Email Address",
                            callback_data="change_email_onboarding",
                        )
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)

                await update.message.reply_text(
                    f"📧 Code sent to {text}\n\nEnter verification code:",
                    reply_markup=reply_markup,
                )

                logger.info(
                    f"✅ Email OTP sent successfully to {text} for user {user_id}"
                )

                # Set context for unified OTP handling
                if context.user_data is not None:
                    context.user_data["verification_purpose"] = "email_onboarding"

                logger.info(
                    f"🔄 Transitioning to VERIFYING_EMAIL_OTP state for user {user_id}"
                )
                return OnboardingStates.VERIFYING_EMAIL_OTP
            else:
                logger.error(f"❌ Email sending failed for {text} - user {user_id}")
                await update.message.reply_text("❌ Email failed\n\nTry again:")
                return OnboardingStates.COLLECTING_EMAIL

        except Exception as e:
            logger.error(
                f"📧 collect_email: CRITICAL ERROR in OTP generation/email sending: {e}",
                exc_info=True,
            )
            await update.message.reply_text(
                "❌ System error occurred. Please try again:"
            )
            return OnboardingStates.COLLECTING_EMAIL
        finally:
            if session:
                logger.info("📧 collect_email: Closing database session")
                session.close()

        return OnboardingStates.COLLECTING_EMAIL

    elif update.callback_query:
        # Handle callback from email request
        query = update.callback_query
        # IMMEDIATE FEEDBACK: Email input request
        if query:
            await safe_answer_callback_query(query, "📧 Email input")

        await query.edit_message_text("📧 Enter your email:", parse_mode="Markdown")
        return OnboardingStates.COLLECTING_EMAIL

    return OnboardingStates.COLLECTING_EMAIL

# REMOVED: Duplicate OTP verification - now using centralized EmailOTPService

# REMOVED: Duplicate OTP verification - now using centralized EmailOTPService
# OTP handling integrated into this handler

async def handle_change_email_during_verification(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle user request to change email during OTP verification"""
    query = update.callback_query
    # IMMEDIATE FEEDBACK: Change email request
    if query:
        await safe_answer_callback_query(query, "🔄 Changing email")

    # Clear previous email and OTP data
    if context.user_data is not None:
        context.user_data.pop("email", None)
        context.user_data.pop("email_otp", None)
        context.user_data.pop("email_otp_attempts", None)

    # Go back to email collection
    if query:
        # Check for referral information to personalize the message
        referral_code = context.user_data.get('pending_referral_code') if context.user_data else None
        referrer_name = None
        
        if referral_code:
            # Query database to get referrer's name
            session = SyncSessionLocal()
            try:
                referrer = session.query(User).filter(User.referral_code == referral_code).first()
                if referrer:
                    referrer_name = referrer.first_name or referrer.username or "a friend"
            except Exception as e:
                logger.error(f"Error fetching referrer info: {e}")
            finally:
                session.close()
        
        # Build the message based on referral status
        if referrer_name:
            # Enhanced message for referred users
            from utils.referral import ReferralSystem
            message = (
                f"🎁 Welcome from {referrer_name}!\n\n"
                f"📧 Email Address\n\n"
                f"Enter your email for {Config.PLATFORM_NAME}:\n\n"
                f"✨ After verification, you'll receive:\n"
                f"• ${ReferralSystem.REFEREE_REWARD_USD:.2f} welcome bonus\n"
                f"• Full wallet access\n"
                f"• Start trading crypto\n\n"
                f"📎 We'll send security codes here"
            )
        else:
            # Standard message for non-referred users
            message = (
                f"📧 Email Address\n\n"
                f"Enter your email for {Config.PLATFORM_NAME}:\n\n"
                f"📎 We'll send security codes here"
            )
        
        await query.edit_message_text(message, parse_mode="Markdown")

    return OnboardingStates.COLLECTING_EMAIL

async def handle_resend_otp_onboarding(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle resending OTP during onboarding email verification"""
    query = update.callback_query
    if not query:
        return ConversationHandler.END
    
    from utils.callback_utils import safe_answer_callback_query
    await safe_answer_callback_query(query, "📮 Sending...")
    
    user = update.effective_user
    if not user:
        return ConversationHandler.END
    
    session = SyncSessionLocal()
    try:
        # Get user from database
        db_user = session.query(User).filter(User.telegram_id == user.id).first()
        if not db_user or not db_user.email:
            await query.edit_message_text(
                "❌ Error: User email not found. Please restart the process with /start"
            )
            return ConversationHandler.END
        
        # Generate new OTP
        from models import EmailVerification
        from datetime import datetime, timezone, timedelta
        import random
        # Generate cryptographically secure OTP
        from utils.secure_crypto import SecureCrypto
        otp = SecureCrypto.generate_secure_otp(6)
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=Config.VERIFICATION_EXPIRY_MINUTES)
        
        # Clean existing verification records for this user
        session.query(EmailVerification).filter(
            EmailVerification.user_id == db_user.id,
            EmailVerification.purpose == "registration"  # FIX: Align with OnboardingService
        ).delete()
        
        # FIXED: Use correct schema with proper column names and atomic UPSERT
        from sqlalchemy import text as sql_text
        
        # ATOMIC UPSERT to prevent race conditions
        session.execute(
            sql_text(
                """
                INSERT INTO email_verifications (user_id, email, verification_code, verified, expires_at, purpose, attempts, max_attempts, created_at)
                VALUES (:user_id, :email, :verification_code, :verified, :expires_at, :purpose, :attempts, :max_attempts, :created_at)
                ON CONFLICT (user_id, email, purpose) DO UPDATE SET
                    verification_code = EXCLUDED.verification_code,
                    verified = EXCLUDED.verified,
                    expires_at = EXCLUDED.expires_at,
                    attempts = 0,
                    created_at = EXCLUDED.created_at
            """
            ),
            {
                "user_id": db_user.id,
                "email": db_user.email,
                "verification_code": otp,
                "verified": False,
                "expires_at": expires_at,
                "purpose": "registration",  # FIX: Align with OnboardingService
                "attempts": 0,
                "max_attempts": 5,
                "created_at": datetime.now(timezone.utc),
            },
        )
        session.commit()
        
        # PERFORMANCE FIX: Use background queue instead of blocking email send
        import time
        email_start = time.time()
        
        logger.info(f"⏱️ PERF: Starting background email queue for OTP resend to {db_user.email}")
        from services.background_email_queue import background_email_queue
        from utils.orm_typing_helpers import as_str
        
        try:
            queue_result = await background_email_queue.queue_otp_email(
                recipient=as_str(db_user.email) or "",
                otp_code=otp,
                purpose="registration",  # FIX: Align with OnboardingService
                user_id=db_user.id,
                user_name=as_str(db_user.first_name) or 'User'
            )
            
            email_elapsed = time.time() - email_start
            logger.info(f"⏱️ PERF: Email queued in {email_elapsed*1000:.2f}ms (was blocking 2-5s)")
            
            email_sent = queue_result.get("success")
        except Exception as e:
            logger.error(f"❌ Failed to queue OTP email: {e}")
            email_sent = False
        
        if email_sent:
            await query.edit_message_text(
                f"✅ New Code Sent!\n\n"
                f"📧 A fresh 6-digit verification code has been sent to:\n"
                f"{db_user.email}\n\n"
                f"💡 Check your inbox and spam folder\n"
                f"⏰ Code expires in {Config.VERIFICATION_EXPIRY_MINUTES} minutes",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Resend Again", callback_data="resend_otp_onboarding")],
                    [InlineKeyboardButton("✏️ Change Email", callback_data="change_email_onboarding")]
                ])
            )
            logger.info(f"✅ OTP resent successfully to {db_user.email} for user {db_user.id}")
            return OnboardingStates.VERIFYING_EMAIL_OTP
        else:
            await query.edit_message_text(
                "❌ Failed to send email\n\n"
                "There was a problem sending the verification code. Please try again or change your email address.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Try Again", callback_data="resend_otp_onboarding")],
                    [InlineKeyboardButton("✏️ Change Email", callback_data="change_email_onboarding")]
                ])
            )
            return OnboardingStates.VERIFYING_EMAIL_OTP
            
    except Exception as e:
        logger.error(f"Error resending OTP for user {user.id}: {e}")
        await query.edit_message_text(
            "❌ An error occurred while resending the code. Please try /start to restart the process."
        )
        return ConversationHandler.END
    finally:
        session.close()

async def complete_trade_acceptance_with_email(
    update: Update, context: ContextTypes.DEFAULT_TYPE, escrow_id: str, user_email: str, session
) -> int:
    """Complete trade acceptance after email verification"""
    try:
        user = update.effective_user
        if not user:
            return ConversationHandler.END
        
        logger.info(f"Creating account for trade acceptance: {escrow_id} with email {user_email}")
        
        # Create user account with verified email
        from models import User, Wallet
        
        db_user = User(
            telegram_id=str(user.id),
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
            email=user_email,
            email_verified=True,  # Email is verified through OTP
        )
        
        session.add(db_user)
        await session.commit()
        await session.refresh(db_user)
        
        # Generate referral code for new user
        try:
            from utils.referral import ReferralSystem
            # Use sync helper with async session - these operations are fast and non-blocking
            ReferralSystem.ensure_user_has_referral_code(db_user, session)
        except Exception as e:
            logger.error(f"Error generating referral code: {e}")
        
        # Create USD wallet (default currency) using utility function
        from utils.helpers import create_user_wallet
        from utils.orm_typing_helpers import as_int
        wallet = create_user_wallet(as_int(db_user.id), session)
        await session.commit()
        
        logger.info(f"Created user account for trade acceptance: {user_email}")
        
        # Send welcome email in background (non-blocking)
        import asyncio
        
        async def send_welcome_email_background() -> None:
            try:
                from services.welcome_email import WelcomeEmailService
                from utils.orm_typing_helpers import as_int
                
                welcome_service = WelcomeEmailService()
                await welcome_service.send_welcome_email(
                    user_email, user.first_name or "Trader", as_int(db_user.id)
                )
                logger.info(f"Welcome email sent to {user_email} after trade acceptance")
            except Exception as e:
                logger.error(f"Failed to send welcome email during trade acceptance: {e}")
        
        # Run email sending in background without blocking trade acceptance
        asyncio.create_task(send_welcome_email_background())
        
        # Clear email collection flow data
        if context.user_data:
            context.user_data.pop("email_collection_flow", None)
            context.user_data.pop("pending_trade_acceptance", None)
            context.user_data.pop("pending_email", None)
        
        # Complete the trade acceptance
        from handlers.escrow import handle_seller_invitation_response
        
        if update.message:
            await update.message.reply_text(
                "✅ Account created successfully!\nNow accepting the trade...",
                parse_mode="Markdown"
            )
        
        # Refresh the session to ensure the user is available
        await session.commit()
        await session.flush()
        
        await handle_seller_invitation_response(
            update, context, escrow_id, "accept", session
        )
        
        return ConversationHandler.END
        
    except Exception as e:
        logger.error(f"Error completing trade acceptance with email: {e}")
        if update.message:
            await update.message.reply_text(
                "❌ Error completing trade acceptance. Please try again."
            )
        return ConversationHandler.END

async def verify_email_otp_onboarding(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Verify OTP during onboarding and transition to Terms of Service"""
    if not update.message or not update.message.text:
        return OnboardingStates.VERIFYING_EMAIL_OTP

    user_id = update.effective_user.id if update.effective_user else None
    if not user_id:
        return ConversationHandler.END

    otp_code = update.message.text.strip()
    logger.info(
        f"🔐 verify_email_otp_onboarding: User {user_id} entered OTP: {otp_code}"
    )

    try:
        from services.email import EmailService

        EmailService()

        # Check if this is trade acceptance email verification
        if context.user_data and context.user_data.get("email_collection_flow"):
            # For trade acceptance, verify OTP from context.user_data (not database)
            stored_email = context.user_data.get("otp_email")
            stored_otp = context.user_data.get("otp")
            
            if not stored_email or not stored_otp:
                logger.error("Missing email or OTP for trade acceptance verification")
                await update.message.reply_text("❌ Error verifying code. Please try again.")
                return ConversationHandler.END
            
            logger.info(f"Trade acceptance OTP verification: entered={otp_code}, email={stored_email}")
            
            # Verify OTP directly from context data (not database)
            if otp_code == stored_otp:
                success = True
                message = "OTP verified successfully"
                logger.info(f"✅ Trade acceptance OTP verified successfully: {otp_code}")
            else:
                success = False
                message = "Invalid verification code"
                logger.info(f"❌ Trade acceptance OTP verification failed: entered={otp_code}, expected={stored_otp}")
            
            if success:
                # OTP verified for trade acceptance
                pending_trade_id = context.user_data.get("pending_trade_acceptance")
                user_email = context.user_data.get("pending_email")
                
                if pending_trade_id and user_email:
                    logger.info(f"✅ Trade acceptance OTP verified, completing trade {pending_trade_id}")
                    session = SyncSessionLocal()
                    try:
                        return await complete_trade_acceptance_with_email(
                            update, context, pending_trade_id, user_email, session
                        )
                    finally:
                        session.close()
                else:
                    logger.error("Missing trade ID or email for trade acceptance completion")
                    await update.message.reply_text("❌ Error completing trade acceptance. Please try again.")
                    return ConversationHandler.END
            else:
                logger.info(f"❌ OTP verification failed for trade acceptance: {message}")
                await update.message.reply_text(f"❌ {message}")
                return OnboardingStates.VERIFYING_EMAIL_OTP
        
        # Regular onboarding email verification - check database
        from models import EmailVerification, User
        from datetime import datetime

        session = SyncSessionLocal()
        try:
            # CRITICAL FIX: Get database user ID, not Telegram ID
            db_user = session.query(User).filter(User.telegram_id == user_id).first()
            if not db_user:
                logger.error(f"User not found for telegram_id: {user_id}")
                await update.message.reply_text("❌ User not found. Please restart with /start")
                return ConversationHandler.END
            
            actual_user_id = db_user.id
            logger.info(f"🔐 Resolved user mapping: Telegram {user_id} -> DB User ID {actual_user_id}")
            
            verification = (
                session.query(EmailVerification)
                .filter(
                    EmailVerification.user_id == actual_user_id,
                    EmailVerification.verification_code == otp_code,
                    EmailVerification.purpose == "registration",  # FIX: Align with OnboardingService which uses 'registration'
                )
                .first()
            )

            is_verified = False
            verification_error = None
            
            if verification is not None:
                # CRITICAL FIX: Handle timezone comparison properly
                from datetime import datetime, timezone
                current_time = datetime.now(timezone.utc)
                expires_time = verification.expires_at
                
                # Handle timezone-aware vs naive datetime comparison
                if expires_time.tzinfo is None:
                    # Database stored naive datetime, compare with naive
                    current_time = datetime.utcnow()
                
                # Enhanced logging for debugging
                logger.info(f"🔐 Verification Check:")
                logger.info(f"  - User DB ID: {actual_user_id}, Telegram ID: {user_id}")
                logger.info(f"  - OTP Entered: {otp_code}")
                logger.info(f"  - OTP Stored: {verification.verification_code}")
                logger.info(f"  - Expires At: {expires_time}")
                logger.info(f"  - Current Time: {current_time}")
                logger.info(f"  - Time Remaining: {(expires_time - current_time).total_seconds()/60:.1f} minutes")
                
                # Check various failure conditions
                if verification.verification_code != otp_code:
                    verification_error = "Code does not match"
                    logger.warning(f"❌ OTP mismatch: entered '{otp_code}' vs stored '{verification.verification_code}'")
                elif expires_time <= current_time:
                    verification_error = "Code has expired"
                    logger.warning(f"❌ OTP expired: {expires_time} <= {current_time}")
                elif verification.attempts >= 5:
                    verification_error = "Too many attempts"
                    logger.warning(f"❌ Too many attempts: {verification.attempts}")
                else:
                    is_verified = True
                    logger.info(f"✅ OTP verification successful")
                
                # Update attempt count if not verified
                if not is_verified and verification:
                    verification.attempts = (verification.attempts or 0) + 1
                    session.commit()
                    logger.info(f"📝 Updated attempts to {verification.attempts}")
            else:
                verification_error = "No verification record found"
                logger.error(f"❌ No verification record for user {actual_user_id} with OTP {otp_code}")
            if is_verified:
                # SECURITY: Mark user as email verified
                # db_user is already loaded above, just update it
                db_user.email_verified = True
                logger.info(f"🔒 SECURITY: Marked user DB ID {actual_user_id} (Telegram: {user_id}) as email verified")
                
                # CRITICAL FIX: Preserve context data for Terms of Service
                if context.user_data is None:
                    context.user_data = {}
                context.user_data["email"] = db_user.email
                context.user_data["email_verified"] = True
                logger.info(f"📋 Context preserved: email={db_user.email}, verified=True")
                
                session.delete(verification)
                session.commit()
                
                # CACHE INVALIDATION: Email verified, invalidate onboarding cache
                invalidate_onboarding_cache(context.user_data)
                logger.info(f"🗑️ CACHE_INVALIDATE: Onboarding cache cleared after email verification")
        finally:
            session.close()
        logger.info(
            f"🔐 verify_email_otp_onboarding: OTP verification result: {is_verified}"
        )

        if is_verified:
            logger.info(
                "✅ verify_email_otp_onboarding: OTP verified successfully, transitioning to Terms of Service"
            )
            # Transition to Terms of Service acceptance
            return await show_terms_of_service(update, context)
        else:
            # Provide specific error message based on failure reason
            error_message = "❌ Verification Failed\n\n"
            
            if verification_error == "Code has expired":
                error_message += "⏰ Your verification code has expired.\nPlease request a new code."
            elif verification_error == "Too many attempts":
                error_message += "🚫 Too many incorrect attempts.\nPlease request a new code."
            elif verification_error == "Code does not match":
                error_message += "❌ Invalid code. Please check and try again:\n\n💡 Enter the 6-digit code from your email"
            else:
                error_message += "❌ Verification error. Please try again or request a new code."
            
            logger.warning(f"❌ Verification failed for user {user_id}: {verification_error}")
            await update.message.reply_text(
                error_message,
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "🔄 Change Email Address",
                                callback_data="change_email_onboarding",
                            )
                        ]
                    ]
                ),
            )
            return OnboardingStates.VERIFYING_EMAIL_OTP

    except Exception as e:
        logger.error(
            f"🔐 verify_email_otp_onboarding: EXCEPTION during OTP verification: {e}",
            exc_info=True,
        )
        await update.message.reply_text(
            "❌ Verification error. Try again:",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🔄 Change Email Address",
                            callback_data="change_email_onboarding",
                        )
                    ]
                ]
            ),
        )
        return OnboardingStates.VERIFYING_EMAIL_OTP

async def request_email_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle email request - simplified since email is now mandatory"""
    query = update.callback_query
    # PERFORMANCE: Instant acknowledgment
    if query:
        # IMMEDIATE FEEDBACK: Terms and conditions
        await safe_answer_callback_query(query, "📋 Terms and conditions")

    if query and query.message:
        await query.edit_message_text("📧 Enter your email:", parse_mode="Markdown")
    return OnboardingStates.COLLECTING_EMAIL

async def show_terms_of_service(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show terms of service for new users"""
    text = f"""📋 Trading Terms

I agree to:
• Use the platform legally
• Provide real information
• Accept {Config.PLATFORM_NAME}'s protection

🚀 Start trading now?"""

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ I Agree - Start Trading", callback_data="confirm_tos"
                )
            ],
            [InlineKeyboardButton("❌ Maybe Later", callback_data="cancel_tos")],
        ]
    )

    if update.message:
        await update.message.reply_text(
            text, parse_mode="Markdown", reply_markup=keyboard
        )
    elif update.callback_query:
        await update.callback_query.edit_message_text(
            text, parse_mode="Markdown", reply_markup=keyboard
        )

    return OnboardingStates.ACCEPTING_TOS

async def accept_terms(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle terms acceptance"""
    query = update.callback_query
    if not query:
        return ConversationHandler.END

    # PERFORMANCE: Instant acknowledgment
    if query:
        # IMMEDIATE FEEDBACK: Terms and conditions
        await safe_answer_callback_query(query, "📋 Terms and conditions")

    if query.data == "confirm_tos":
        # Check if this is from trade acceptance flow
        if context.user_data and context.user_data.get("trade_acceptance_flow"):
            return await complete_trade_acceptance_onboarding(update, context)
        else:
            return await complete_onboarding(update, context)
    else:
        await query.edit_message_text(
            "👋 No problem!\n\nCome back anytime - just hit /start!"
        )
        return ConversationHandler.END

async def complete_onboarding(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Complete user onboarding with mandatory email"""
    query = update.callback_query
    user = update.effective_user
    if not query or not user:
        return ConversationHandler.END

    from database import AsyncSessionLocal
    from sqlalchemy import select
    
    session = AsyncSessionLocal()

    try:
        # FIXED: Check database first, then context as fallback (now async)
        result = await session.execute(select(User).filter(User.telegram_id == user.id))
        existing_user = result.scalar_one_or_none()
        
        if existing_user:
            # User already exists - redirect to main menu
            logger.info(f"User {user.id} already exists, redirecting to main menu")
            await query.edit_message_text(
                f"👋 Welcome back, {user.first_name}!\n\nRedirecting to main menu...",
                parse_mode="Markdown",
            )
            # Call main menu function from same module
            await show_main_menu(update, context, existing_user)
            return ConversationHandler.END
        
        # Get email from context (now required and verified)
        email = context.user_data.get("email") if context.user_data else None
        email_verified = (
            context.user_data.get("email_verified", False)
            if context.user_data
            else False
        )

        if not email or not email_verified:
            # Enhanced error with debugging info
            logger.error(f"Email verification failed - email: {email}, verified: {email_verified}")
            logger.error(f"Context data: {context.user_data}")
            await query.edit_message_text(
                "❌ Email verification lost.\n\nPlease restart with /start to verify your email again.",
                parse_mode="Markdown",
            )
            return ConversationHandler.END

        logger.info(f"Creating new user with verified email: {email}")

        # Create new user account with mandatory email
        db_user = User(
            telegram_id=str(user.id),
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
            email=email,
            email_verified=True,  # Email is verified via OTP during onboarding
        )

        session.add(db_user)
        await session.commit()

        # Process referral code if present
        referral_welcome_message = None
        if context.user_data and context.user_data.get("pending_referral_code"):
            referral_code = context.user_data["pending_referral_code"]
            logger.info(
                f"Processing referral code {referral_code} for new user {db_user.id}"
            )

            try:
                from utils.referral import ReferralSystem

                # Refresh user object from database to get the ID
                await session.refresh(db_user)

                result = await ReferralSystem.process_referral_signup(
                    db_user, referral_code, session
                )
                if result["success"]:
                    referrer = result["referrer"]
                    referrer_name = (
                        referrer.first_name or referrer.username or "someone"
                    )
                    referral_welcome_message = f"🎉 Welcome bonus: ${result['welcome_bonus']} USD from {referrer_name}!"
                    logger.info(
                        f"Successfully processed referral for user {db_user.id}"
                    )
                else:
                    logger.warning(f"Failed to process referral: {result.get('error')}")

                # Clear the pending referral code
                del context.user_data["pending_referral_code"]

            except Exception as e:
                logger.error(f"Error processing referral: {e}")

        # Generate referral code for new user
        try:
            from utils.referral import ReferralSystem

            ReferralSystem.ensure_user_has_referral_code(db_user, session)
        except Exception as e:
            logger.error(f"Error generating referral code: {e}")

        # Create wallet for new user using utility function
        from utils.helpers import create_user_wallet
        from utils.orm_typing_helpers import as_int
        wallet = create_user_wallet(as_int(db_user.id), session)
        await session.commit()

        logger.info(f"User created successfully: {db_user.telegram_id}")

        # IMPROVED: Concise account creation confirmation
        display_name = user.first_name if user.first_name else "there"

        # Step 1: Simple welcome confirmation
        welcome_text = f"""✅ Welcome {display_name}!

📧 {email} ✅
⏰ Setting up..."""

        # Add referral welcome message if present
        if referral_welcome_message:
            welcome_text += f"\n\n{referral_welcome_message}"

        await query.edit_message_text(welcome_text, parse_mode="Markdown")

        # Step 2: Brief pause for reading (3 seconds instead of 6)
        import asyncio

        await asyncio.sleep(3)

        # Step 3: Simple service selection
        feature_text = f"""🏠 {Config.PLATFORM_NAME}

🚀 Quick Exchange (5m) • 🛡️ Secure Trade

🎯 Ready to trade?"""

        continue_keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🚀 Let's Start Trading!", callback_data="continue_to_dashboard"
                    )
                ],
                [InlineKeyboardButton("💡 Learn More", callback_data="show_help")],
            ]
        )

        await query.edit_message_text(
            feature_text, parse_mode="Markdown", reply_markup=continue_keyboard
        )

        # OPTIMIZED: Send welcome email in background (non-blocking)
        import asyncio

        async def send_welcome_email_background() -> None:
            try:
                from services.welcome_email import WelcomeEmailService

                welcome_service = WelcomeEmailService()
                await welcome_service.send_welcome_email(
                    email, user.first_name or "Trader", user.id
                )
                logger.info(f"Welcome email sent to {email}")
            except Exception as e:
                logger.error(f"Failed to send welcome email: {e}")

        # Run email sending in background without blocking UI
        asyncio.create_task(send_welcome_email_background())

        # Store user data for the next step
        if context.user_data is not None:
            context.user_data["new_user"] = db_user

        # Continue with the retention-focused onboarding
        return OnboardingStates.ONBOARDING_SHOWCASE

    except Exception as e:
        logger.error(f"Error completing onboarding: {e}")
        await query.edit_message_text(
            "😅 Setup failed!\n\n" "💡 Tap /start to try again", parse_mode="Markdown"
        )
        return ConversationHandler.END
    finally:
        await session.close()

async def complete_trade_acceptance_onboarding(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Complete user onboarding for trade acceptance flow"""
    query = update.callback_query
    user = update.effective_user
    if not query or not user:
        return ConversationHandler.END

    session = SyncSessionLocal()

    try:
        # Check if user already exists
        existing_user = session.query(User).filter(User.telegram_id == user.id).first()
        
        if existing_user:
            logger.info(f"User {user.id} already exists, processing trade acceptance")
            # Process trade acceptance for existing user
            return await finalize_trade_acceptance(update, context, existing_user, session)
        
        # Get email from context (required for trade acceptance)
        email = context.user_data.get("email") if context.user_data else None
        email_verified = context.user_data.get("email_verified", False) if context.user_data else False

        if not email or not email_verified:
            logger.error(f"Trade acceptance: Email verification failed - email: {email}, verified: {email_verified}")
            await query.edit_message_text(
                "❌ Email verification required. Please restart the process.",
                parse_mode="Markdown"
            )
            return ConversationHandler.END

        # Create new user for trade acceptance
        new_user = User(
            telegram_id=str(user.id),
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
            email=email,
            email_verified=True,
            terms_accepted=True,
            created_at=datetime.utcnow(),
        )

        session.add(new_user)
        session.flush()  # Get ID without committing

        # Create wallet for new user using utility function
        from utils.helpers import create_user_wallet
        from utils.orm_typing_helpers import as_int
        wallet = create_user_wallet(as_int(new_user.id), session)
        session.commit()

        logger.info(f"✅ Trade acceptance: Created user {user.id} with email {email}")

        # Send welcome email in background (non-blocking)
        import asyncio
        
        async def send_welcome_email_background() -> None:
            try:
                from services.welcome_email import WelcomeEmailService
                from utils.orm_typing_helpers import as_int
                
                welcome_service = WelcomeEmailService()
                await welcome_service.send_welcome_email(
                    email, user.first_name or "Trader", as_int(new_user.id)
                )
                logger.info(f"Welcome email sent to {email} after trade acceptance")
            except Exception as e:
                logger.error(f"Failed to send welcome email during trade acceptance: {e}")
        
        # Run email sending in background without blocking trade acceptance
        asyncio.create_task(send_welcome_email_background())

        # Process trade acceptance
        return await finalize_trade_acceptance(update, context, new_user, session)

    except Exception as e:
        logger.error(f"Error in trade acceptance onboarding: {e}")
        session.rollback()
        await query.edit_message_text(
            "❌ Setup error. Please try again with /start",
            parse_mode="Markdown"
        )
        return ConversationHandler.END
    finally:
        session.close()

async def finalize_trade_acceptance(update: Update, context: ContextTypes.DEFAULT_TYPE, user_obj: User, session) -> int:
    """Finalize trade acceptance after user creation/validation"""
    query = update.callback_query
    
    try:
        # CRITICAL FIX: Check for None context.user_data
        if not context.user_data:
            if query:
                await query.edit_message_text("❌ Session error. Please try again with /start")
            return ConversationHandler.END
            
        escrow_id = context.user_data.get("pending_trade_acceptance")
        if not escrow_id:
            if query:
                await query.edit_message_text("❌ Trade information not found. Please try again.")
            return ConversationHandler.END

        # Get the escrow
        escrow = session.query(Escrow).filter(Escrow.escrow_id == escrow_id).first()
        if not escrow:
            if query:
                await query.edit_message_text("❌ Trade not found.")
            return ConversationHandler.END

        # SECURITY FIX: Validate state transition before acceptance to prevent DISPUTED→ACTIVE
        from utils.escrow_state_validator import EscrowStateValidator
        
        validator = EscrowStateValidator()
        current_status = escrow.status
        if not validator.is_valid_transition(current_status, EscrowStatus.ACTIVE.value):
            logger.error(
                f"🚫 DEEP_LINK_ACCEPT_BLOCKED: Invalid transition {current_status}→ACTIVE for trade {escrow_id}"
            )
            if query:
                await query.edit_message_text(
                    f"❌ Trade cannot be accepted at this time.\n\n"
                    f"Current status: {current_status}\n\n"
                    f"Please contact support if you believe this is an error."
                )
            return ConversationHandler.END

        # Set seller to the new user - CRITICAL FIX: Ensure proper database updates
        escrow.seller_id = user_obj.id  # Direct assignment
        escrow.status = EscrowStatus.ACTIVE.value  # Use .value for database compatibility
        escrow.seller_accepted_at = datetime.utcnow()
        
        # CRITICAL FIX: Ensure database commit is successful with error handling
        try:
            session.commit()
            logger.info(f"✅ Trade {escrow.escrow_id} accepted by new seller {user_obj.id} - database updated successfully")
        except Exception as commit_error:
            logger.error(f"❌ Failed to commit seller acceptance for trade {escrow.escrow_id}: {commit_error}")
            session.rollback()
            raise commit_error

        # CRITICAL FIX: Show Terms of Service before trade acceptance completion
        tos_message = f"""🎉 Trade Accepted Successfully!

🆔 Trade ID: #{escrow_id}
💰 Amount: ${float(escrow.amount):.2f} USD

📋 Terms of Service Agreement

By accepting this trade, you agree to:
• Deliver the promised service/product within the specified timeframe
• Maintain professional communication with the buyer
• Accept responsibility for trade completion
• Allow platform mediation for any disputes
• Pay applicable platform fees (10% of trade value)

⚖️ Your Rights:
• Full dispute resolution support
• Secure payment guarantee upon delivery
• Platform protection against fraudulent claims

The buyer has been notified. Welcome to {Config.PLATFORM_NAME}! 🚀"""

        if query:
                await query.edit_message_text(
                tos_message,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("💬 Open Trade Chat", callback_data=f"trade_chat_open:{escrow.id}")],
                    [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
                ])
            )

        # Send notifications to buyer
        buyer = session.query(User).filter(User.id == escrow.buyer_id).first()
        if buyer:
            # Send Telegram notification
            try:
                await context.bot.send_message(
                    chat_id=buyer.telegram_id,
                    text=(
                        f"🎉 Trade Accepted!\n\n"
                        f"The seller has accepted your trade:\n"
                        f"#{escrow.escrow_id} • ${float(escrow.amount):.2f}\n\n"
                        f"✅ Trade is now active\n"
                        f"💬 You can now chat with the seller\n"
                        f"📦 Waiting for delivery"
                    ),
                    parse_mode="Markdown"
                )
                logger.info(f"✅ Telegram notification sent to buyer {buyer.telegram_id}")
            except Exception as e:
                logger.error(f"Failed to send Telegram notification to buyer: {e}")
            
            # Send email notification to buyer
            if buyer.email and buyer.is_verified:
                try:
                    from services.email import EmailService
                    email_service = EmailService()
                    await email_service.send_trade_notification(
                        str(buyer.email),
                        str(buyer.first_name or buyer.username or "Buyer"),
                        str(escrow.escrow_id),
                        "seller_accepted",
                        {
                            "amount": Decimal(str(escrow.amount)),
                            "currency": str(escrow.currency or "USD"),
                            "seller": str(escrow.seller_contact_display or user_obj.first_name or user_obj.username or "Seller"),
                            "payment_method": str(escrow.payment_method or "N/A"),
                            "description": str(escrow.description or "N/A"),
                        }
                    )
                    logger.info(f"✅ Email notification sent to buyer {buyer.email}")
                except Exception as e:
                    logger.error(f"Failed to send email notification to buyer: {e}")
        
        # Send email notification to seller (if email verified)
        if user_obj.email and user_obj.is_verified:
            try:
                from services.email import EmailService
                email_service = EmailService()
                await email_service.send_trade_notification(
                    str(user_obj.email),
                    str(user_obj.first_name or user_obj.username or "Seller"),
                    str(escrow.escrow_id),
                    "seller_trade_accepted",
                    {
                        "amount": Decimal(str(escrow.amount)),
                        "currency": str(escrow.currency or "USD"),
                        "buyer": str(buyer.first_name or buyer.username or "Buyer") if buyer else "Buyer",
                        "payment_method": str(escrow.payment_method or "N/A"),
                        "description": str(escrow.description or "N/A"),
                    }
                )
                logger.info(f"✅ Email notification sent to seller {user_obj.email}")
            except Exception as e:
                logger.error(f"Failed to send email notification to seller: {e}")

        # Clean up context
        if context.user_data:
            context.user_data.pop("pending_trade_acceptance", None)
            context.user_data.pop("trade_acceptance_flow", None)
            context.user_data.pop("email", None)
            context.user_data.pop("email_verified", None)

        return ConversationHandler.END

    except Exception as e:
        logger.error(f"Error finalizing trade acceptance: {e}")
        if query:
            await query.edit_message_text("❌ Error processing trade acceptance. Please try again.")
        return ConversationHandler.END

async def navigate_to_dashboard(
    update: Update, context: ContextTypes.DEFAULT_TYPE, source: str = "unknown"
) -> int:
    """
    Shared helper to navigate user to main dashboard.
    Works for both onboarding flow and global navigation (e.g., old Quick Guide messages).
    
    Args:
        update: Telegram update
        context: Bot context
        source: Source of navigation for logging (e.g., "onboarding", "quick_guide")
    
    Returns:
        ConversationHandler.END to exit any active conversation
    """
    query = update.callback_query
    if not query:
        return ConversationHandler.END

    # PERFORMANCE: Instant acknowledgment
    await safe_answer_callback_query(query, "🚀")

    # Get Telegram user
    user = update.effective_user
    if not user:
        await query.edit_message_text("❌ Error: Unable to identify user. Please /start again.")
        return ConversationHandler.END

    session = SyncSessionLocal()
    try:
        # Look up user from database by Telegram ID (conversation-agnostic)
        from models import User
        db_user = session.query(User).filter(User.telegram_id == user.id).first()
        
        if not db_user:
            await query.edit_message_text("❌ User not found. Please /start to register.")
            return ConversationHandler.END

        # Get fresh user data and wallet balance
        display_name = user.first_name if user.first_name else "there"
        wallet = session.query(Wallet).filter(Wallet.user_id == db_user.id, Wallet.currency == "USD").first()
        
        # Safe SQLAlchemy Column to float conversion with explicit typing
        balance: float = 0.0
        if wallet:
            try:
                available = float(getattr(wallet, "available_balance", 0) or 0)
                trading = float(getattr(wallet, "trading_credit", 0) or 0)
                balance = available + trading
            except (ValueError, TypeError):
                balance = 0.0

        # Get trust badge and trader level
        from utils.trusted_trader import TrustedTraderSystem
        try:
            level_info = TrustedTraderSystem.get_trader_level(db_user, session)
            trust_badge = level_info["badge"] if level_info else "⭐"
            trader_status = level_info["name"] if level_info else "New User"
        except Exception as e:
            trust_badge = "⭐"
            trader_status = "New User"
            logger.warning(f"Failed to get trust badge for user {user.id}: {e}")

        # Get trade statistics
        from models import Escrow
        total_trades = session.query(Escrow).filter(
            (Escrow.buyer_id == db_user.id) | (Escrow.seller_id == db_user.id)
        ).count()

        # Professional dashboard with clear next steps
        dashboard_text = f"""🏠 {Config.PLATFORM_NAME} Dashboard

👋 Welcome {display_name}!

📊 Your Account:
💰 Balance: ${balance:.2f} USD
🤝 Trades: {total_trades} | 💎 Volume: $0.00 USD
⭐ Status: {trader_status} {trust_badge}
📧 Email: ✅ Verified

🎯 Ready to make your {"first" if total_trades == 0 else "next"} transaction?
💡 *Tip: Try a small Quick Exchange to see our 5-minute speed!*"""

        keyboard = main_menu_keyboard(
            balance=balance, 
            total_trades=total_trades, 
            active_escrows=0, 
            user_telegram_id=str(user.id), 
            active_disputes=0
        )
        await query.edit_message_text(dashboard_text, reply_markup=keyboard)

        # Clear user data
        if context.user_data:
            context.user_data.clear()

        logger.info(f"✅ Dashboard navigation successful for user {user.id} (source: {source})")
        return ConversationHandler.END

    except Exception as e:
        logger.error(f"Error navigating to dashboard (source: {source}): {e}")
        await query.edit_message_text("❌ Error loading dashboard. Please try /start again.")
        return ConversationHandler.END
    finally:
        session.close()


async def handle_onboarding_continue(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle 'Let's Start Trading!' button from onboarding - delegates to shared helper"""
    return await navigate_to_dashboard(update, context, source="onboarding")

async def show_help_from_onboarding_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle 'Learn More' button from onboarding"""
    query = update.callback_query
    if not query:
        return ConversationHandler.END

    await safe_answer_callback_query(query, "💡")

    # Simple help overview with fee info - COMPACT VERSION
    from utils.fee_policy_messages import FeePolicyMessages
    help_text = f"""💡 Quick Guide

🚀 Quick Exchange (under ${int(Config.SECURE_TRADE_THRESHOLD_USD)} USD)
🛡️ Secure Trade (up to ${int(Config.SECURE_TRADE_THRESHOLD_USD)} USD)

5% fee • Refund on early cancel

✨ BTC, ETH, USDT, LTC & more
🔒 Escrow protected • Rate locked
⚡ ~5 min processing • Global

💬 /support for help"""

    # Create new trade button
    back_keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🚀 Create New Trade", callback_data="create_secure_trade"
                )
            ]
        ]
    )

    if query:
        await query.edit_message_text(
            help_text, parse_mode="Markdown", reply_markup=back_keyboard
        )

    # Stay in the same state so they can go back to dashboard
    return OnboardingStates.ONBOARDING_SHOWCASE

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, user=None) -> int | None:
    """Show main menu to user - ENHANCED WITH COMPLETE STATE CLEANUP"""
    
    # CRITICAL: Complete conversation state cleanup (same pattern as /start)
    if context.user_data:
        # Clear funding context
        context.user_data.pop("expecting_funding_amount", None)
        context.user_data.pop("expecting_custom_amount", None)
        
        # ENHANCED: Clear ALL conversation states to prevent frozen buttons
        context.user_data.pop("active_conversation", None)
        context.user_data.pop("exchange_data", None)
        context.user_data.pop("exchange_session_id", None)
        context.user_data.pop("escrow_data", None)
        context.user_data.pop("contact_data", None)
        context.user_data.pop("wallet_data", None)
        logger.debug("🧹 Main menu: Cleared all conversation states")
    
    # ENHANCED: Clear universal session manager sessions 
    if update.effective_user:
        try:
            from utils.universal_session_manager import universal_session_manager
            user_session_ids = universal_session_manager.get_user_session_ids(update.effective_user.id)
            if user_session_ids:
                logger.info(f"🧹 Main menu: Clearing {len(user_session_ids)} universal sessions")
                for session_id in user_session_ids:
                    universal_session_manager.terminate_session(session_id, "main_menu_navigation")
                logger.info("✅ Main menu: Universal sessions cleaned")
        except Exception as e:
            logger.warning(f"Could not clear universal sessions in main menu: {e}")
    if not user and update.effective_user:
        session = SyncSessionLocal()
        try:
            user = (
                session.query(User)
                .filter(User.telegram_id == update.effective_user.id)
                .first()
            )
            if user:
                # Use optimized version with existing session
                await show_main_menu_optimized(update, context, user, session)
                return
        finally:
            session.close()

    # For new users or callback queries without database user
    if not user:
        # If it's a callback query (like from buttons), redirect to /start
        if update.callback_query:
            from utils.callback_utils import safe_answer_callback_query

            await safe_answer_callback_query(update.callback_query, "🏠 Redirecting...")

            text = f"""🏠 Welcome to {Config.PLATFORM_NAME}!

Ready to start secure trading?"""

            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🚀 Get Started", callback_data="start_onboarding"
                        )
                    ]
                ]
            )

            await update.callback_query.edit_message_text(text, reply_markup=keyboard)
            return
        elif update.effective_message:
            await update.effective_message.reply_text(
                "❌ User not found. Please /start again."
            )
        return

    # For calls with existing user object, create session and use optimized version
    session = SyncSessionLocal()
    try:
        await show_main_menu_optimized(update, context, user, session)
    finally:
        session.close()

async def show_main_menu_optimized(
    update: Update, context: ContextTypes.DEFAULT_TYPE, user, session
) -> int | None:
    """PERFORMANCE: Optimized main menu with shared session"""
    if not user:
        if update.effective_message:
            await update.effective_message.reply_text(
                "❌ User not found. Please /start again."
            )
        return

    try:
        # PERFORMANCE: Combined query for wallet balance and escrow count
        from sqlalchemy import text

        # PERFORMANCE: Fast essential data query including active escrows AND total trades for proper UI
        # CRITICAL FIX: Show USD balance ONLY (multi-currency wallets are internal payment rails)
        result = session.execute(
            text(
                """
                SELECT 
                    COALESCE(w.available_balance + w.trading_credit, 0) as balance,
                    COALESCE(COUNT(CASE WHEN e.status IN ('created', 'payment_pending', 'payment_confirmed', 'partial_payment', 'active', 'disputed') THEN 1 END), 0) as active_escrows,
                    COALESCE(COUNT(CASE WHEN e.id IS NOT NULL THEN 1 END), 0) as total_trades,
                    COALESCE(COUNT(CASE WHEN e.status = 'payment_confirmed' AND e.seller_id = u.id THEN 1 END), 0) as pending_invitations,
                    COALESCE((SELECT COUNT(*) FROM users WHERE referred_by_id = u.id), 0) as referral_count,
                    COALESCE(SUM(CASE WHEN e.status IN ('completed', 'released') THEN COALESCE(e.amount, 0) ELSE 0 END), 0) as total_volume,
                    COALESCE((SELECT COUNT(*) FROM disputes d JOIN escrows e2 ON d.escrow_id = e2.id WHERE (e2.buyer_id = u.id OR e2.seller_id = u.id) AND d.status IN ('open', 'pending')), 0) as active_disputes
                FROM users u
                LEFT JOIN wallets w ON w.user_id = u.id AND w.currency = 'USD'
                LEFT JOIN escrows e ON (e.buyer_id = u.id OR e.seller_id = u.id)
                WHERE u.id = :uid
                GROUP BY u.id, w.available_balance, w.trading_credit
                """
            ),
            {"uid": user.id},
        ).first()

        balance = float(result[0]) if result and result[0] else 0.0
        active_escrows = int(result[1]) if result and len(result) > 1 and result[1] else 0
        total_trades = int(result[2]) if result and len(result) > 2 and result[2] else 0
        pending_invitations = int(result[3]) if result and len(result) > 3 and result[3] else 0
        referral_count = int(result[4]) if result and len(result) > 4 and result[4] else 0
        total_volume = float(result[5]) if result and len(result) > 5 and result[5] else 0.0
        active_disputes = int(result[6]) if result and len(result) > 6 and result[6] else 0

        # Get trader level info in same session
        reputation_display = "⭐ New Trader"
        try:
            from utils.trusted_trader import TrustedTraderSystem

            level_info = TrustedTraderSystem.get_trader_level(user, session)
            if level_info and isinstance(level_info, dict):
                reputation_display = f"{str(level_info.get('badge', '⭐'))} {str(level_info.get('name', 'New Trader'))}"
                str(level_info.get("badge", "⭐"))

                # Add rating info with safe type conversion
                total_ratings_val = 0
                reputation_score = 0.0
                try:
                    # Note: total_ratings field doesn't exist in User model
                    # Using reputation_score from actual User model
                    if (
                        hasattr(user, "reputation_score")
                        and user.reputation_score is not None
                    ):
                        try:
                            reputation_score = float(user.reputation_score)
                        except (ValueError, TypeError):
                            reputation_score = 0.0
                    # total_ratings not available in current model
                    total_ratings_val = 0
                except (ValueError, TypeError):
                    total_ratings_val = 0
                    reputation_score = 0.0

                if total_ratings_val > 0 and reputation_score > 0:
                    reputation_display += (
                        f" ({reputation_score:.1f}/5 from {total_ratings_val} ratings)"
                    )
        except Exception as e:
            logger.error(f"Error getting trader level: {e}")

    except Exception as e:
        logger.error(f"Error in show_main_menu_optimized: {e}")
        try:
            session.rollback()
        except Exception as rollback_error:
            logger.error(f"Failed to rollback session in menu: {rollback_error}")
        
        # Send error message to user instead of crashing
        try:
            if update.callback_query:
                # CRITICAL FIX: Edit the message instead of just answering callback
                await safe_answer_callback_query(update.callback_query, "❌ Loading error - retrying...")
                await update.callback_query.edit_message_text(
                    "🔧 Technical Issue\n\n"
                    "We're experiencing some difficulties loading your menu.\n\n"
                    "✨ Please try again or use /start to refresh.",
                    parse_mode='Markdown',
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔄 Try Again", callback_data="back_to_main")],
                        [InlineKeyboardButton("🏠 Start Over", callback_data="start_onboarding")]
                    ])
                )
            elif update.effective_message:
                await update.effective_message.reply_text(
                    "🔧 We're experiencing some technical difficulties.\n\n"
                    "✨ Please try again in a moment or contact support if the issue persists.\n\n"
                    "🔄 Use /start to retry"
                )
        except Exception as msg_error:
            logger.error(f"Failed to send error message: {msg_error}")
        return

    # Safe user data extraction with comprehensive type safety
    display_name = f"User {user.id}"

    try:
        if hasattr(user, "first_name") and user.first_name:
            display_name = str(user.first_name)
        # total_trades and total_volume are now calculated from the query above
    except (ValueError, TypeError, AttributeError):
        pass  # Keep default values

    # Construct balanced, appealing text - STANDARDIZED FORMAT FOR ALL USERS
    # Config already imported globally

    # UNIFIED MAIN MENU FORMAT (same for all users)
    # Add dispute indicator when disputes exist
    dispute_line = ""
    if active_disputes > 0:
        dispute_line = f"\n⚠️ Disputes: {active_disputes} (needs attention!)"
    
    text = f"""🏠 Welcome to {Config.PLATFORM_NAME}!

Hey {display_name}! 👋

💰 Balance: ${balance:.2f} USD
📊 Total Trades: {total_trades}
⚡ Active: {active_escrows}{dispute_line}

What would you like to do today?"""

    # Use the updated main menu keyboard function with consolidated interface
    keyboard = main_menu_keyboard(
        balance=balance, total_trades=total_trades, active_escrows=active_escrows, 
        pending_invitations=pending_invitations, referral_count=referral_count,
        user_telegram_id=str(update.effective_user.id) if update.effective_user else "",
        active_disputes=active_disputes
    )

    if update.callback_query:
        from utils.callback_utils import safe_edit_message_text

        await safe_edit_message_text(
            update.callback_query, text, parse_mode="Markdown", reply_markup=keyboard
        )
    elif update.effective_message:
        await update.effective_message.reply_text(
            text, parse_mode="Markdown", reply_markup=keyboard
        )

async def show_main_menu_optimized_async(
    update: Update, context: ContextTypes.DEFAULT_TYPE, user, session
) -> int | None:
    """
    PERFORMANCE OPTIMIZED: Async main menu with context caching (11 queries → 1 query)
    
    CACHING STRATEGY:
    - First menu access: 1 database query (fetch all menu data)
    - Subsequent navigations: 0 queries (use cached data)
    - Cache invalidation: On balance changes, new trades, or timeout
    """
    if not user:
        if update.effective_message:
            await update.effective_message.reply_text(
                "❌ User not found. Please /start again."
            )
        return

    try:
        # PERFORMANCE: Check context cache first (11 queries → 0 queries on cache hit)
        import time
        current_time = time.time()
        cache_key = 'menu_user_data'
        cache_ttl = 30  # 30 seconds cache TTL for menu data
        
        cached_data = context.user_data.get(cache_key) if context.user_data else None
        cache_timestamp = context.user_data.get(f'{cache_key}_timestamp') if context.user_data else None
        
        if cached_data and cache_timestamp and (current_time - cache_timestamp < cache_ttl):
            # CACHE HIT: Use cached menu data (0 queries)
            logger.info(f"✅ MENU_CACHE_HIT: Using cached data (0 queries)")
            balance = cached_data.get('balance', 0.0)
            active_escrows = cached_data.get('active_escrows', 0)
            total_trades = cached_data.get('total_trades', 0)
            pending_invitations = cached_data.get('pending_invitations', 0)
            referral_count = cached_data.get('referral_count', 0)
            total_volume = cached_data.get('total_volume', 0.0)
            active_disputes = cached_data.get('active_disputes', 0)
        else:
            # CACHE MISS: Query database (1 query)
            logger.info(f"ℹ️ MENU_CACHE_MISS: Fetching fresh data (1 query)")
            from sqlalchemy import text
            
            query_start = time.time()
            
            # PERFORMANCE: Optimized menu query with proper JOINs (no correlated subqueries)
            result = await session.execute(
                text(
                    """
                    SELECT 
                        COALESCE(w.available_balance + w.trading_credit, 0) as balance,
                        COALESCE(COUNT(DISTINCT CASE WHEN e.status IN ('created', 'payment_pending', 'payment_confirmed', 'partial_payment', 'active', 'disputed') THEN e.id END), 0) as active_escrows,
                        COALESCE(COUNT(DISTINCT e.id), 0) as total_trades,
                        COALESCE(COUNT(DISTINCT CASE WHEN e.status = 'payment_confirmed' AND e.seller_id = u.id THEN e.id END), 0) as pending_invitations,
                        COALESCE(COUNT(DISTINCT r.id), 0) as referral_count,
                        COALESCE(SUM(DISTINCT CASE WHEN e.status IN ('completed', 'released') THEN COALESCE(e.amount, 0) ELSE 0 END), 0) as total_volume,
                        COALESCE(COUNT(DISTINCT CASE WHEN d.status IN ('open', 'pending') THEN d.id END), 0) as active_disputes
                    FROM users u
                    LEFT JOIN wallets w ON w.user_id = u.id AND w.currency = 'USD'
                    LEFT JOIN escrows e ON (e.buyer_id = u.id OR e.seller_id = u.id)
                    LEFT JOIN users r ON r.referred_by_id = u.id
                    LEFT JOIN disputes d ON d.escrow_id = e.id
                    WHERE u.id = :uid
                    GROUP BY u.id, w.available_balance, w.trading_credit
                    """
                ),
                {"uid": user.id},
            )
            row = result.first()
            
            query_time = (time.time() - query_start) * 1000
            logger.info(f"⚡ MENU_QUERY: Completed in {query_time:.1f}ms")

            balance = float(row[0]) if row and row[0] else 0.0
            active_escrows = int(row[1]) if row and len(row) > 1 and row[1] else 0
            total_trades = int(row[2]) if row and len(row) > 2 and row[2] else 0
            pending_invitations = int(row[3]) if row and len(row) > 3 and row[3] else 0
            referral_count = int(row[4]) if row and len(row) > 4 and row[4] else 0
            total_volume = float(row[5]) if row and len(row) > 5 and row[5] else 0.0
            active_disputes = int(row[6]) if row and len(row) > 6 and row[6] else 0
            
            # CACHE THE DATA: Store in context for subsequent menu navigations
            if context.user_data is not None:
                context.user_data[cache_key] = {
                    'user_id': user.id,
                    'telegram_id': user.telegram_id,
                    'username': user.username if hasattr(user, 'username') else None,
                    'email': user.email if hasattr(user, 'email') else None,
                    'balance': balance,
                    'active_escrows': active_escrows,
                    'total_trades': total_trades,
                    'pending_invitations': pending_invitations,
                    'referral_count': referral_count,
                    'total_volume': total_volume,
                    'active_disputes': active_disputes
                }
                context.user_data[f'{cache_key}_timestamp'] = current_time
                logger.info(f"✅ MENU_CACHE: Stored menu data (TTL: {cache_ttl}s)")

    except Exception as e:
        logger.error(f"⚠️ Error in show_main_menu_optimized_async: {e}")
        
        # Send error message to user instead of crashing
        try:
            if update.callback_query:
                await safe_answer_callback_query(update.callback_query, "❌ Loading error - retrying...")
                await update.callback_query.edit_message_text(
                    "🔧 Technical Issue\n\n"
                    "We're experiencing some difficulties loading your menu.\n\n"
                    "✨ Please try again or use /start to refresh.",
                    parse_mode='Markdown',
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔄 Try Again", callback_data="back_to_main")],
                        [InlineKeyboardButton("🏠 Start Over", callback_data="start_onboarding")]
                    ])
                )
            elif update.effective_message:
                await update.effective_message.reply_text(
                    "🔧 We're experiencing some technical difficulties.\n\n"
                    "✨ Please try again in a moment or contact support if the issue persists.\n\n"
                    "🔄 Use /start to retry"
                )
        except Exception as msg_error:
            logger.error(f"Failed to send error message: {msg_error}")
        return

    # Safe user data extraction
    display_name = str(user.first_name) if hasattr(user, "first_name") and user.first_name else f"User {user.id}"

    # Construct menu text
    dispute_line = ""
    if active_disputes > 0:
        dispute_line = f"\n⚠️ Disputes: {active_disputes} (needs attention!)"
    
    # Check verification status for badge
    verification_badge = ""
    if hasattr(user, 'email_verified') and user.email_verified:
        verification_badge = "✅ <b>Email Verified</b>\n"
    elif hasattr(user, 'email') and user.email:
        # Has email but not verified
        verification_badge = "⚠️ <b>Unverified</b> (No OTP protection)\n"
    
    text = f"""🏠 Welcome to {Config.PLATFORM_NAME}!

Hey {display_name}! 👋
{verification_badge}
💰 Balance: ${balance:.2f} USD
📊 Total Trades: {total_trades}
⚡ Active: {active_escrows}{dispute_line}

What would you like to do today?"""

    # Use the updated main menu keyboard function
    keyboard = main_menu_keyboard(
        balance=balance, total_trades=total_trades, active_escrows=active_escrows, 
        pending_invitations=pending_invitations, referral_count=referral_count,
        user_telegram_id=str(update.effective_user.id) if update.effective_user else "",
        active_disputes=active_disputes
    )

    if update.callback_query:
        from utils.callback_utils import safe_edit_message_text
        await safe_edit_message_text(
            update.callback_query, text, parse_mode="Markdown", reply_markup=keyboard
        )
    elif update.effective_message:
        await update.effective_message.reply_text(
            text, parse_mode="Markdown", reply_markup=keyboard
        )

async def check_pending_invitations_by_user_data(user_id: int, user_email: str, session) -> dict | None:
    """PERFORMANCE: Check for pending escrow invitations using user data instead of object"""
    try:
        if not user_id:
            return None

        # PERFORMANCE: Single optimized query that excludes cancelled/completed escrows
        valid_pending_statuses = [
            EscrowStatus.PAYMENT_CONFIRMED.value,
        ]

        # OPTIMIZED: Single query with OR to check both seller_id AND seller_email in one go
        # This uses the composite indexes: ix_escrows_seller_status and ix_escrows_seller_email_status
        conditions = [Escrow.seller_id == user_id]
        if user_email:
            conditions.append(Escrow.seller_email == user_email)
        
        result = await session.execute(
            select(Escrow)
            .where(
                or_(*conditions),
                Escrow.status.in_(valid_pending_statuses),
            )
        )
        escrows = result.scalars().all()

        if escrows:
            # Return all escrows for proper multi-invitation handling
            if len(escrows) == 1:
                return {"escrow_id": escrows[0].escrow_id, "escrow": escrows[0]}
            else:
                # Multiple invitations - return all
                return {
                    "multiple_invitations": True,
                    "escrows": escrows,
                    "count": len(escrows),
                }
    except Exception as e:
        logger.error(f"Error checking pending invitations: {e}")

    return None

async def check_pending_invitations_optimized(user_obj, session) -> dict | None:
    """DEPRECATED: Use check_pending_invitations_by_user_data instead"""
    if not user_obj:
        return None
    return await check_pending_invitations_by_user_data(user_obj.id, user_obj.email, session)

async def check_pending_invitations_by_telegram_id_with_username(
    telegram_id: int, username: str, session
) -> dict | None:
    """Check for pending escrow invitations by telegram ID - ASYNC VERSION"""
    logger.info(f"🔍 INVITATION CHECK START: telegram_id={telegram_id}, username={username}")
    try:
        # Look for escrows where user is seller by email or seller_id
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        user_obj = result.scalar_one_or_none()
        escrows = []

        # FIXED: Unified comprehensive status check for both existing and new users
        valid_pending_statuses = [
            EscrowStatus.CREATED.value,           # "created" - just created, awaiting payment
            EscrowStatus.PAYMENT_PENDING.value,   # "payment_pending" - payment in progress
            EscrowStatus.PAYMENT_CONFIRMED.value, # "payment_confirmed" - payment confirmed, awaiting seller
            EscrowStatus.AWAITING_SELLER.value,   # "awaiting_seller" - waiting for seller acceptance
            EscrowStatus.PENDING_SELLER.value,    # "pending_seller" - pending seller response
            EscrowStatus.ACTIVE.value,            # "active" - trade is active
        ]

        if user_obj:
            # Check by seller_id first - exclude cancelled/completed escrows
            result = await session.execute(
                select(Escrow)
                .where(
                    Escrow.seller_id == user_obj.id,
                    Escrow.status.in_(valid_pending_statuses),
                )
            )
            escrows = result.scalars().all()

            # Also check by email if no direct matches and user has email
            if not escrows and user_obj.email:
                result = await session.execute(
                    select(Escrow)
                    .where(
                        Escrow.seller_email == user_obj.email,
                        Escrow.status.in_(valid_pending_statuses),
                    )
                )
                escrows = result.scalars().all()
        else:
            # For new users, check if there are escrows waiting for their username
            if username:
                try:
                    # Look for escrows where seller_email OR seller_contact matches the username
                    # Use the same comprehensive status list as existing users
                    result = await session.execute(
                        select(Escrow)
                        .where(
                            or_(
                                Escrow.seller_email == username,
                                and_(
                                    Escrow.seller_contact_type == 'username',
                                    Escrow.seller_contact_value == username
                                ),
                            ),
                            Escrow.status.in_(valid_pending_statuses),
                        )
                    )
                    escrows = result.scalars().all()
                    logger.info(
                        f"✅ PENDING INVITATION CHECK: username={username}, found={len(escrows)} escrows, statuses_checked={valid_pending_statuses}"
                    )
                    
                    # Additional debugging for each escrow found
                    for escrow in escrows:
                        logger.info(
                            f"📋 Found escrow: ID={escrow.escrow_id}, status={escrow.status}, seller_email={getattr(escrow, 'seller_email', None)}, seller_contact_type={getattr(escrow, 'seller_contact_type', None)}, seller_contact_value={getattr(escrow, 'seller_contact_value', None)}"
                        )
                except Exception as e:
                    logger.error(
                        f"Error checking username-based escrows for {username}: {e}"
                    )

        if escrows:
            # Return all escrows for proper multi-invitation handling (same as check_pending_invitations_optimized)
            if len(escrows) == 1:
                return {"escrow_id": escrows[0].escrow_id, "escrow": escrows[0]}
            else:
                # Multiple invitations - return all
                return {
                    "multiple_invitations": True,
                    "escrows": escrows,
                    "count": len(escrows),
                }
    except Exception as e:
        logger.error(f"Error checking pending invitations by telegram ID: {e}")

    return None

async def show_pending_invitation(
    update: Update, context: ContextTypes.DEFAULT_TYPE, invitation, user
) -> int:
    """Show pending trade invitation to user"""
    try:
        escrow = invitation["escrow"]

        # Format trade details
        # Note: CryptoService import removed as it's not being used

        # Get currency info
        currency_emoji = CURRENCY_EMOJIS.get(str(escrow.currency), "💰")
        network_info = f" ({escrow.network})" if escrow.network else ""

        text = f"""💰 Trade Invitation • #{escrow.escrow_id}

👤 {get_user_display_name(escrow.buyer)}
💵 You earn: ${float(getattr(escrow, 'total_amount', 0) or 0):.2f} USD
📋 {getattr(escrow, 'description', 'Service')[:30]}{'...' if len(getattr(escrow, 'description', '')) > 30 else ''}

📝 Choose an option:
• Accept = Start immediately
• Reject = Cancel permanently  
• Decide Later = Setup account first"""

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ Accept",
                        callback_data=f"accept_trade:{invitation['escrow_id']}",
                    ),
                    InlineKeyboardButton(
                        "❌ Reject Forever",
                        callback_data=f"decline_trade:{invitation['escrow_id']}",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "⏸️ Decide Later", callback_data="continue_onboarding"
                    )
                ],
            ]
        )

        if update.message:
            await update.message.reply_text(
                text, parse_mode="HTML", reply_markup=keyboard
            )
        elif update.callback_query:
            await update.callback_query.edit_message_text(
                text, parse_mode="HTML", reply_markup=keyboard
            )

        return ConversationHandler.END

    except Exception as e:
        logger.error(f"Error showing pending invitation: {e}")
        await show_main_menu(update, context, user)
        return ConversationHandler.END

async def show_multiple_pending_invitations(
    update: Update, context: ContextTypes.DEFAULT_TYPE, invitations_data, user
) -> int | None:
    """Show all pending trade invitations with navigation"""
    try:
        escrows = invitations_data["escrows"]
        count = invitations_data["count"]

        text = f"""💰 {count} Pending Invitation{'s' if count != 1 else ''}

"""

        # UNIFIED DISPLAY FORMAT (same as messages_hub.py)
        keyboard = []
        
        # Use same clean format as unified trade display
        for escrow in escrows:
            # Get buyer display name
            buyer = escrow.buyer if hasattr(escrow, 'buyer') and escrow.buyer else None
            buyer_name = get_user_display_name(buyer) if buyer else "Buyer"
            
            # Use same amount calculation as unified display
            amount = float(getattr(escrow, 'total_amount', 0) or getattr(escrow, 'amount', 0) or 0)
            
            # Use unified status icon (payment_confirmed = seller pending)
            status_icon = '✅'
            
            # UNIFIED button format (same design as messages_hub.py)
            escrow_display = escrow.escrow_id[-6:] if escrow.escrow_id else str(escrow.id)
            
            keyboard.append([
                InlineKeyboardButton(
                    f"{status_icon} #{escrow_display} • ${amount:.0f} USD with {buyer_name}",
                    callback_data=f"view_invitation:{escrow.escrow_id}"
                )
            ])

        text += "Select a trade to view details and respond:"

        # Add main menu button
        keyboard.append(
            [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
        )

        reply_markup = InlineKeyboardMarkup(keyboard)

        if update.callback_query:
            await update.callback_query.edit_message_text(
                text, reply_markup=reply_markup
            )
        elif update.message:
            await update.message.reply_text(text, reply_markup=reply_markup)

        return ConversationHandler.END

    except Exception as e:
        logger.error(f"Error showing multiple pending invitations: {e}")
        if update.message:
            await update.message.reply_text(
                "❌ Error loading invitations. Please try /start again."
            )

async def handle_view_pending_invitations(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle viewing pending invitations - redirects to My Trades (streamlined UX)"""
    logger.info(f"🎯 handle_view_pending_invitations: Redirecting to My Trades")
    
    # Redirect to My Trades instead of showing redundant invitations page
    from handlers.messages_hub import show_trades_messages_hub
    return await show_trades_messages_hub(update, context)

async def handle_view_individual_invitation(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle viewing individual invitation from multiple invitations list"""
    logger.info(f"🎯 handle_view_individual_invitation called!")
    
    query = update.callback_query
    if not query or not query.data:
        logger.warning("🎯 No query or query data in handle_view_individual_invitation")
        return ConversationHandler.END

    from utils.callback_utils import safe_answer_callback_query

    # IMMEDIATE FEEDBACK: Demo selection
    await safe_answer_callback_query(query, "🎯 Viewing invitation")
    logger.info(f"🎯 Callback data: {query.data}")

    try:
        # Parse escrow ID from callback data
        escrow_id = query.data.split(":")[1]
        logger.info(f"🎯 Parsed escrow_id: {escrow_id}")

        # Get user
        user = update.effective_user
        if not user:
            logger.error("🎯 No effective user found")
            return ConversationHandler.END
        
        logger.info(f"🎯 Processing invitation view for user {user.id} (@{user.username})")

        # Get escrow from database
        session = SyncSessionLocal()
        try:
            from models import User, Escrow

            # For new users viewing invitations, we don't need them in User table yet
            # Just get the escrow and verify authorization by username/email matching

            # Get the specific escrow
            escrow = session.query(Escrow).filter_by(escrow_id=escrow_id).first()
            if not escrow:
                await query.edit_message_text("❌ Trade invitation not found.")
                return ConversationHandler.END

            # Check if user is authorized to view this invitation
            # For new users, check by telegram username match
            seller_username = getattr(escrow, "seller_username", None)
            telegram_username = getattr(user, "username", None)
            logger.info(f"🎯 Authorization check - seller_username: {seller_username}, telegram_username: {telegram_username}")

            # Also check if they have a User record and match by ID/email
            db_user = session.query(User).filter_by(telegram_id=str(user.id)).first()
            logger.info(f"🎯 Found db_user: {db_user.id if db_user else 'None'}")
            
            seller_id_matches = db_user and getattr(
                escrow, "seller_id", None
            ) == getattr(db_user, "id", None)
            seller_email_matches = db_user and getattr(
                escrow, "seller_email", None
            ) == getattr(db_user, "email", None)
            username_matches = (
                seller_username
                and telegram_username
                and seller_username.lower() == telegram_username.lower()
            )

            logger.info(f"🎯 Authorization results - seller_id_matches: {seller_id_matches}, seller_email_matches: {seller_email_matches}, username_matches: {username_matches}")
            logger.info(f"🎯 Escrow seller_id: {getattr(escrow, 'seller_id', None)}, db_user.id: {getattr(db_user, 'id', None) if db_user else 'None'}")

            if (
                not seller_id_matches
                and not seller_email_matches
                and not username_matches
            ):
                logger.warning(f"🎯 Authorization failed for user {user.id} viewing escrow {escrow_id}")
                await query.edit_message_text(
                    "❌ You're not authorized to view this invitation."
                )
                return ConversationHandler.END
            
            logger.info(f"🎯 Authorization passed! Showing trade details for escrow {escrow_id}")

            # Format invitation data like the original function

            # Get currency info
            currency = getattr(escrow, "currency", None) or "USD"
            currency_emoji = CURRENCY_EMOJIS.get(str(currency), "💰")
            network = getattr(escrow, "network", None)
            network_info = f" ({network})" if network else ""

            text = f"""🔔 Trade Invitation Details

🆔 Trade #{escrow.escrow_id}
👤 Buyer: {get_user_display_name(escrow.buyer)}
💰 Amount: ${float(getattr(escrow, 'total_amount', 0) or 0):.2f} USD
{currency_emoji} Payment: {currency}{network_info}

📝 Description: {getattr(escrow, 'description', None) or 'No description provided'}

⏰ Delivery Time: Standard delivery timeframe from acceptance

What would you like to do?"""

            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "✅ Accept", callback_data=f"accept_trade:{escrow_id}"
                        ),
                        InlineKeyboardButton(
                            "❌ Reject", callback_data=f"decline_trade:{escrow_id}"
                        ),
                    ],
                    [
                        InlineKeyboardButton(
                            "⏸️ Decide Later", callback_data="main_menu"
                        ),
                    ],
                    [
                        InlineKeyboardButton(
                            "⬅️ Back to All Invitations",
                            callback_data="view_all_invitations",
                        ),
                        InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu"),
                    ],
                ]
            )

            await query.edit_message_text(text, reply_markup=keyboard)
            return ConversationHandler.END

        finally:
            session.close()

    except Exception as e:
        logger.error(f"Error viewing individual invitation: {e}")
        if query:
            try:
                await query.edit_message_text(
                    "❌ Error loading invitation. Please try /start again."
                )
            except Exception as e:
                pass
        return ConversationHandler.END

async def handle_view_all_invitations(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle returning to all invitations view"""
    query = update.callback_query
    if query:
        from utils.callback_utils import safe_answer_callback_query

        # IMMEDIATE FEEDBACK: Start action
        await safe_answer_callback_query(query, "🚀 Start action")

    try:
        # Get user
        user = update.effective_user
        if not user:
            return ConversationHandler.END

        # Get all pending invitations again
        session = SyncSessionLocal()
        try:
            from models import User

            # For new users, check by username like in the main flow
            pending_invitations = (
                await check_pending_invitations_by_telegram_id_with_username(
                    user.id, user.username or "", session
                )
            )

            # If no results by username, try checking if they're an existing user
            if not pending_invitations:
                db_user = (
                    session.query(User).filter_by(telegram_id=str(user.id)).first()
                )
                if db_user:
                    pending_invitations = await check_pending_invitations_optimized(
                        db_user, session
                    )
            if pending_invitations and pending_invitations.get("multiple_invitations"):
                await show_multiple_pending_invitations(
                    update, context, pending_invitations, user
                )
            else:
                if query and query.message:
                    await query.edit_message_text("No pending invitations found.")

            return ConversationHandler.END

        finally:
            session.close()

    except Exception as e:
        logger.error(f"Error returning to all invitations: {e}")
        if query:
            try:
                await query.edit_message_text(
                    "❌ Error loading invitations. Please try /start again."
                )
            except Exception as e:
                pass
        return ConversationHandler.END

async def show_escrow_status(
    update: Update, context: ContextTypes.DEFAULT_TYPE, escrow, user
) -> int | None:
    """Show escrow status based on current state"""
    try:
        from utils.constants import STATUS_EMOJIS
        from datetime import datetime

        status_emoji = STATUS_EMOJIS.get(escrow.status, "⚪")

        if escrow.status == EscrowStatus.PAYMENT_CONFIRMED.value:
            # Payment-first system: Buyer has paid, waiting for seller decision
            buyer_name = (
                get_user_display_name(escrow.buyer) if escrow.buyer else "Buyer"
            )
            text = f"""💰 <b>Trade Offer Ready</b> {status_emoji}

🆔 <code>{escrow.escrow_id}</code> • 👤 {buyer_name}
💵 <b>You'll receive:</b> ${escrow.amount:.2f} USD
📝 <b>Service:</b> {escrow.description}

✅ <b>Buyer paid all fees</b> - funds secured in escrow
⚡ <b>Accept to start delivery, decline for auto-refund</b>"""

            # Add Accept/Decline buttons
            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "✅ Accept",
                            callback_data=f"accept_trade:{escrow.escrow_id}",
                        ),
                        InlineKeyboardButton(
                            "❌ Decline",
                            callback_data=f"decline_trade:{escrow.escrow_id}",
                        ),
                    ],
                    [
                        InlineKeyboardButton(
                            "🏠 Main Menu", callback_data="continue_onboarding"
                        )
                    ],
                ]
            )

            if update.message:
                await update.message.reply_text(
                    text, parse_mode="HTML", reply_markup=keyboard
                )
            elif update.callback_query:
                await update.callback_query.edit_message_text(
                    text, parse_mode="HTML", reply_markup=keyboard
                )
            return

        elif escrow.status == EscrowStatus.ACTIVE.value:
            # Active trade - show delivery timeline
            buyer_name = (
                get_user_display_name(escrow.buyer) if escrow.buyer else "Buyer"
            )
            deadline_text = ""
            if escrow.delivery_deadline:
                deadline = escrow.delivery_deadline
                time_left = deadline - datetime.utcnow()
                if time_left.total_seconds() > 0:
                    hours_left = int(time_left.total_seconds() / 3600)
                    deadline_text = f"⏰ <b>Deliver by:</b> {deadline.strftime('%b %d, %H:%M UTC')} ({hours_left}h left)"
                else:
                    deadline_text = "⏰ <b>Delivery:</b> ⚠️ Overdue"

            text = f"""🔄 <b>Trade Active</b> {status_emoji}

💰 ${escrow.amount:.2f} • {escrow.description}
👤 <b>Buyer:</b> {buyer_name}
{deadline_text}
⚡ <b>Action:</b> Deliver promptly to complete"""

        elif escrow.status == EscrowStatus.COMPLETED.value:
            # Completed trade
            buyer_name = (
                get_user_display_name(escrow.buyer) if escrow.buyer else "Buyer"
            )
            text = f"""✅ <b>Trade Completed</b> {status_emoji}

💰 ${escrow.amount:.2f} • {escrow.description}
👤 <b>Buyer:</b> {buyer_name}
🎉 Funds released • Trade complete"""

        elif escrow.status in [
            EscrowStatus.CANCELLED.value,
            EscrowStatus.EXPIRED.value,
        ]:
            # Cancelled or expired
            status_text = (
                "Cancelled"
                if escrow.status == EscrowStatus.CANCELLED.value
                else "Expired"
            )
            text = f"""❌ <b>Trade {status_text}</b> {status_emoji}

💰 ${escrow.amount:.2f} • {escrow.description}
💡 This trade is no longer active"""

        else:
            # Default status display
            text = f"""ℹ️ <b>Trade Status</b> {status_emoji}

🆔 <b>Trade ID:</b> <code>{escrow.escrow_id}</code>
💰 <b>Amount:</b> ${escrow.amount:.2f} USD
📝 <b>Description:</b> {escrow.description}
⚪ <b>Status:</b> {escrow.status.replace('_', ' ').title()}"""

        # Add main menu button
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🏠 Main Menu", callback_data="continue_onboarding"
                    )
                ]
            ]
        )

        if update.message:
            await update.message.reply_text(
                text, parse_mode="HTML", reply_markup=keyboard
            )
        elif update.callback_query:
            await update.callback_query.edit_message_text(
                text, parse_mode="HTML", reply_markup=keyboard
            )

    except Exception as e:
        logger.error(f"Error showing escrow status: {e}")
        await show_main_menu(update, context, user)

async def handle_deep_link(
    update: Update, context: ContextTypes.DEFAULT_TYPE, start_param: str, user
) -> int | None:
    """Handle deep link after user registration"""
    try:
        # Handle rating deep links (rate_ESCROWID) with SECURITY VALIDATION
        if start_param.startswith("rate_"):
            escrow_id = start_param[5:]  # Remove "rate_" prefix
            
            # IMPORTANT: `user` parameter is already the DB user object (passed from process_existing_user_async)
            db_user = user
            telegram_user = update.effective_user
            
            logger.info(f"🌟 Rating deep link triggered for escrow: {escrow_id} by user {db_user.id} (telegram: {telegram_user.id if telegram_user else 'unknown'})")
            
            if update.message:
                try:
                    session = SyncSessionLocal()
                    try:
                        # SECURITY: Fetch escrow and validate user is a participant
                        escrow = (
                            session.query(Escrow)
                            .filter(Escrow.escrow_id == escrow_id)
                            .first()
                        )
                        
                        if not escrow:
                            logger.warning(f"🔒 SECURITY: User {db_user.id} attempted to rate non-existent escrow {escrow_id}")
                            await update.message.reply_text(
                                f"❌ <b>Trade Not Found</b>\n\n"
                                f"This trade does not exist or has been removed.",
                                parse_mode='HTML',
                                reply_markup=InlineKeyboardMarkup([
                                    [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
                                ])
                            )
                            return ConversationHandler.END
                        
                        # CRITICAL SECURITY CHECK: Verify user is buyer or seller
                        is_participant = db_user.id in [escrow.buyer_id, escrow.seller_id]
                        
                        if not is_participant:
                            logger.warning(
                                f"🔒 SECURITY: User {db_user.id} (telegram: {db_user.telegram_id}) attempted to rate "
                                f"escrow {escrow_id} but is NOT a participant (buyer: {escrow.buyer_id}, seller: {escrow.seller_id})"
                            )
                            await update.message.reply_text(
                                f"❌ <b>Access Denied</b>\n\n"
                                f"You can only rate trades you participated in.",
                                parse_mode='HTML',
                                reply_markup=InlineKeyboardMarkup([
                                    [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
                                ])
                            )
                            return ConversationHandler.END
                        
                        # Check if trade is completed
                        if escrow.status != EscrowStatus.COMPLETED:
                            logger.info(f"ℹ️ User {db_user.id} tried to rate incomplete escrow {escrow_id} (status: {escrow.status})")
                            await update.message.reply_text(
                                f"❌ <b>Trade Not Completed</b>\n\n"
                                f"You can only rate completed trades.",
                                parse_mode='HTML',
                                reply_markup=InlineKeyboardMarkup([
                                    [InlineKeyboardButton("📋 View Trade", callback_data=f"view_trade_{escrow_id}")],
                                    [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
                                ])
                            )
                            return ConversationHandler.END
                        
                        # Check if user has already rated
                        from models import Rating
                        user_role = 'buyer' if db_user.id == escrow.buyer_id else 'seller'
                        rating_category = 'seller' if user_role == 'buyer' else 'buyer'
                        
                        existing_rating = (
                            session.query(Rating)
                            .filter(
                                Rating.escrow_id == escrow.id,
                                Rating.rater_id == db_user.id,
                                Rating.category == rating_category
                            )
                            .first()
                        )
                        
                        if existing_rating:
                            logger.info(f"ℹ️ User {db_user.id} already rated escrow {escrow_id}")
                            await update.message.reply_text(
                                f"ℹ️ <b>Already Rated</b>\n\n"
                                f"You've already rated this trade. Thank you for your feedback!",
                                parse_mode='HTML',
                                reply_markup=InlineKeyboardMarkup([
                                    [InlineKeyboardButton("📋 View Trade", callback_data=f"view_trade_{escrow_id}")],
                                    [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
                                ])
                            )
                            return ConversationHandler.END
                        
                        # AUTHORIZED: Send rating prompt
                        counterpart_name = "seller" if user_role == 'buyer' else "buyer"
                        logger.info(f"✅ AUTHORIZED: User {db_user.id} can rate {counterpart_name} for escrow {escrow_id}")
                        
                        await update.message.reply_text(
                            f"⭐ <b>Rate Trade #{escrow_id}</b>\n\n"
                            f"How was your experience with the {counterpart_name}?\n"
                            f"Your rating helps build trust in our community.",
                            parse_mode='HTML',
                            reply_markup=InlineKeyboardMarkup([
                                [InlineKeyboardButton("⭐ Rate Now", callback_data=f"rate_escrow_{escrow.id}")],
                                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
                            ])
                        )
                        logger.info(f"✅ Rating prompt sent for escrow {escrow_id} to authorized user {db_user.id}")
                        
                    finally:
                        session.close()
                except Exception as e:
                    logger.error(f"Error handling rating deep link for {escrow_id}: {e}")
                    await update.message.reply_text(
                        "❌ Unable to load rating. Please try again from your trade history.",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
                        ])
                    )
            
            return ConversationHandler.END
        
        # Handle other deep links
        parsed = parse_start_parameter(start_param)

        if not parsed:
            await show_main_menu(update, context, user)
            return ConversationHandler.END

        if parsed["type"] == "trade_invitation":
            escrow_id = parsed.get("escrow_id")  # FIX: Use correct key
            if escrow_id:
                # Handle escrow invitation
                session = SyncSessionLocal()
                try:
                    escrow = (
                        session.query(Escrow)
                        .filter(Escrow.escrow_id == escrow_id)
                        .first()
                    )

                    if escrow:
                        # Show invitation status regardless of current status
                        await show_escrow_status(update, context, escrow, user)
                        return ConversationHandler.END
                    else:
                        # Escrow not found
                        error_text = "❌ Invitation Not Found\n\nThis invitation link is invalid or expired."
                        if update.message:
                            await update.message.reply_text(
                                error_text, parse_mode="Markdown"
                            )
                        return ConversationHandler.END
                finally:
                    session.close()

        # Default to main menu
        await show_main_menu(update, context, user)

    except Exception as e:
        logger.error(f"Error handling deep link: {e}")
        await show_main_menu(update, context, user)

async def handle_continue_onboarding(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int | None:
    """Handle continue onboarding callback"""
    query = update.callback_query
    if query:
        # IMMEDIATE FEEDBACK: Terms and conditions
        await safe_answer_callback_query(query, "📋 Terms and conditions")
        user = update.effective_user
        if user:
            session = SyncSessionLocal()
            try:
                db_user = (
                    session.query(User).filter(User.telegram_id == user.id).first()
                )
                if db_user:
                    await show_main_menu(update, context, db_user)
                else:
                    await start_onboarding(update, context)
            finally:
                session.close()

async def welcome_existing_user(
    update: Update, context: ContextTypes.DEFAULT_TYPE, user
) -> int | None:
    """Welcome back existing user"""
    try:
        # Get user stats
        session = SyncSessionLocal()
        try:
            wallet = session.query(Wallet).filter(Wallet.user_id == user.id, Wallet.currency == "USD").first()
            # Ensure proper type conversion from SQLAlchemy Column to float
            # Safe SQLAlchemy Column to float conversion with explicit typing
            balance: float = 0.0
            if wallet and hasattr(wallet, "balance"):
                balance_value = getattr(wallet, "balance", None)
                if balance_value is not None:
                    try:
                        balance = float(balance_value)
                    except (ValueError, TypeError):
                        balance = 0.0

            total_escrows = (
                session.query(Escrow)
                .filter(or_(Escrow.buyer_id == user.id, Escrow.seller_id == user.id))
                .count()
            )
            
            # Calculate active escrows
            active_escrows = (
                session.query(Escrow)
                .filter(
                    or_(Escrow.buyer_id == user.id, Escrow.seller_id == user.id),
                    Escrow.status.in_(['created', 'payment_pending', 'payment_confirmed', 'partial_payment', 'active', 'disputed'])
                )
                .count()
            )
            
            # Calculate active disputes
            from models import Dispute
            active_disputes = (
                session.query(Dispute)
                .join(Escrow, Dispute.escrow_id == Escrow.id)
                .filter(
                    or_(Escrow.buyer_id == user.id, Escrow.seller_id == user.id),
                    Dispute.status.in_(['open', 'pending'])
                )
                .count()
            )
        finally:
            session.close()

        display_name = get_user_display_name(user)

        # Show minimal welcome
        fallback_text = f"""👋 {display_name} • 💰 ${balance:.2f} USD • 📊 {total_escrows} trades

✨ What would you like to do?"""

        keyboard = main_menu_keyboard(
            balance=balance, total_trades=total_escrows, active_escrows=active_escrows,
            user_telegram_id=str(update.effective_user.id) if update.effective_user else "",
            active_disputes=active_disputes
        )

        if update.callback_query:
            await update.callback_query.edit_message_text(
                fallback_text, parse_mode="Markdown", reply_markup=keyboard
            )
        elif update.effective_message:
            await update.effective_message.reply_text(
                fallback_text, parse_mode="Markdown", reply_markup=keyboard
            )

    except Exception as e:
        logger.error(f"Error in welcome_existing_user: {e}")
        # Fallback to basic message
        if update.callback_query:
            await update.callback_query.edit_message_text(
                "👋 Welcome back!", parse_mode="Markdown"
            )
        elif update.effective_message:
            await update.effective_message.reply_text(
                "👋 Welcome back!", parse_mode="Markdown"
            )

async def cancel_onboarding(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel onboarding process"""
    user = update.effective_user
    
    # Use unified cleanup function to clear all state
    if user:
        from utils.conversation_cleanup import clear_user_conversation_state
        await clear_user_conversation_state(
            user_id=user.id,
            context=context,
            trigger="cancel_onboarding"
        )
    
    if update.message:
        await update.message.reply_text(
            "❌ Registration cancelled. You can restart anytime with /start"
        )
    return ConversationHandler.END

async def handle_onboarding_main_menu_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle main menu navigation from onboarding conversation"""
    logger.info("Onboarding main menu fallback: Redirecting to main menu")
    await show_main_menu(update, context)
    return ConversationHandler.END

async def handle_accept_trade_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle accept_trade callback specifically for conversation handler"""
    query = update.callback_query
    if not query:
        return ConversationHandler.END

    # PERFORMANCE: Instant acknowledgment
    await safe_answer_callback_query(query, "✅ Processing trade acceptance...")

    # Extract escrow ID
    if not query.data or not query.data.startswith("accept_trade:"):
        return ConversationHandler.END
        
    escrow_id = query.data.split(":")[1]
    user = update.effective_user

    if not user:
        return ConversationHandler.END

    session = SyncSessionLocal()
    try:
        # Get the escrow
        escrow = session.query(Escrow).filter(Escrow.escrow_id == escrow_id).first()

        if not escrow:
            await query.edit_message_text("❌ Error: Trade not found.")
            return ConversationHandler.END

        # Check both seller identification methods
        escrow_seller_email = getattr(escrow, "seller_email", None)
        escrow_seller_username = getattr(escrow, "seller_username", None)
        
        # Validate that this user matches the intended seller
        seller_matches = False
        
        if escrow_seller_email:
            # Email-based trade - email already verified by invitation delivery
            seller_matches = True
            logger.info(f"Email-based trade acceptance for {escrow_seller_email}")
            
            # Store trade info and redirect to terms of service (email pre-verified)
            if context.user_data is None:
                context.user_data = {}
            context.user_data["pending_trade_acceptance"] = escrow_id
            context.user_data["trade_acceptance_flow"] = True
            context.user_data["email"] = escrow_seller_email
            context.user_data["email_verified"] = True  # Pre-verified via email invitation
            
            # Show terms of service before trade acceptance
            return await show_terms_of_service(update, context)
            
        elif escrow_seller_username and user.username and user.username.lower() == escrow_seller_username.lower():
            # Username-based trade - check if user already exists
            seller_matches = True
            
            # Check if user already has an account (existing user)
            from models import User
            db_user = session.query(User).filter_by(telegram_id=str(user.id)).first()
            
            if db_user and getattr(db_user, 'email_verified', False):
                # EXISTING USER - directly process the trade acceptance
                logger.info(f"Existing verified user @{escrow_seller_username} accepting trade - bypassing email verification")
                
                # Import and use the escrow handler directly
                from handlers.escrow import handle_seller_invitation_response
                return await handle_seller_invitation_response(
                    update, context, escrow_id, "accept", session
                )
            else:
                # NEW USER - require email verification flow
                logger.info(f"New username-based trade requires email verification for @{escrow_seller_username}")
                
                # Redirect to email collection flow with clear messaging
                await query.edit_message_text(
                    f"✅ Trade Accepted!\n\n"
                    f"📧 Enter your email to continue:\n"
                    f"example@gmail.com",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_email_setup")]
                    ])
                )
                
                # Store the escrow_id and flow flags in context
                if context.user_data is None:
                    context.user_data = {}
                context.user_data["pending_trade_acceptance"] = escrow_id
                context.user_data["email_collection_flow"] = True
                context.user_data["trade_acceptance_flow"] = True
                
                return OnboardingStates.COLLECTING_EMAIL
        
        if not seller_matches:
            seller_info = escrow_seller_email or f"@{escrow_seller_username}" if escrow_seller_username else "unknown"
            await query.edit_message_text(
                f"❌ Error: This trade is intended for {seller_info}. Please contact the buyer directly if you believe this is an error."
            )
            return ConversationHandler.END

    except Exception as e:
        logger.error(f"Error in accept_trade callback: {e}")
        await query.edit_message_text("❌ Error processing trade acceptance. Please try again.")
        return ConversationHandler.END
    finally:
        session.close()
    
    return ConversationHandler.END

# UNIFIED ONBOARDING CONVERSATION HANDLER - Fixed routing for OTP emails
onboarding_conversation = ConversationHandler(
    entry_points=[
        # CRITICAL FIX: Add regular onboarding entry points
        CallbackQueryHandler(handle_start_email_input, pattern="^start_email_input$"),
        # MODERN INTEGRATION: New invitation decide later handler
        CallbackQueryHandler(handle_invitation_decide_later, pattern="^invitation_decide_later$"),
        # Trade acceptance entry points
        CallbackQueryHandler(handle_accept_trade_callback, pattern=r"^accept_trade:.*$"),
        CallbackQueryHandler(handle_view_individual_invitation, pattern=r"^decline_trade:.*$"),
        CallbackQueryHandler(handle_view_individual_invitation, pattern=r"^confirm_decline_trade:.*$"),
    ],
    states={
        OnboardingStates.COLLECTING_EMAIL: [
            CallbackQueryHandler(request_email_handler, pattern=r"^request_email$"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, collect_email),
            CallbackQueryHandler(
                start_handler, pattern="^cancel_email_setup$"
            ),  # Handle cancel email setup
        ],
        OnboardingStates.VERIFYING_EMAIL_OTP: [
            MessageHandler(
                filters.TEXT & ~filters.COMMAND, verify_email_otp_onboarding
            ),  # ✅ RESTORED: OTP verification handler
            CallbackQueryHandler(
                handle_change_email_during_verification,
                pattern="^change_email_onboarding$",
            ),
            CallbackQueryHandler(
                handle_resend_otp_onboarding,
                pattern="^resend_otp_onboarding$",
            ),
        ],
        OnboardingStates.ACCEPTING_TOS: [
            CallbackQueryHandler(accept_terms, pattern=r"^(confirm_tos|cancel_tos)$"),
        ],
        OnboardingStates.ONBOARDING_SHOWCASE: [
            CallbackQueryHandler(
                handle_start_email_input, pattern="^start_email_input$"
            ),
            CallbackQueryHandler(handle_explore_demo, pattern="^explore_demo$"),
            CallbackQueryHandler(handle_demo_exchange, pattern="^demo_exchange$"),
            CallbackQueryHandler(handle_demo_escrow, pattern="^demo_escrow$"),
            CallbackQueryHandler(handle_back_to_welcome, pattern="^back_to_welcome$"),
            CallbackQueryHandler(
                handle_onboarding_continue, pattern=r"^continue_to_dashboard$"
            ),
            CallbackQueryHandler(
                show_help_from_onboarding_callback, pattern=r"^show_help$"
            ),
        ],
    },
    fallbacks=[
        CommandHandler("cancel", cancel_onboarding),
        # CRITICAL: Add main menu fallback handler for responsive navigation
        CallbackQueryHandler(handle_onboarding_main_menu_fallback, pattern="^main_menu$"),
    ],
    name="onboarding",
    persistent=False,
    per_message=False,  # Allow warnings - this is the intended behavior
    per_chat=True,      # ISOLATION: Separate conversations per chat
    per_user=True,      # ISOLATION: Separate conversations per user
    # TIMEOUT: Auto-cleanup abandoned conversations after 10 minutes
    conversation_timeout=600,  # 10 minutes of inactivity
)

# Keep the original name for compatibility
# Onboarding conversation defined above

async def handle_email_invitation_for_new_user(
    update: Update, context: ContextTypes.DEFAULT_TYPE, start_param: str
) -> int:
    """Handle email invitation for new users who haven't joined Telegram yet"""
    user = update.effective_user
    if not user:
        logger.error("No effective user in handle_email_invitation_for_new_user")
        return ConversationHandler.END

    session = SyncSessionLocal()
    try:
        # Parse the invitation parameters
        parsed = parse_start_parameter(start_param)
        if not parsed or parsed["type"] not in [
            "trade_invitation",
            "email_invitation",
        ]:
            logger.info("Not an escrow invitation, proceeding with normal onboarding")
            return await start_onboarding(update, context)

        # Handle email invitation token format
        if parsed["type"] == "email_invitation":
            # Look up escrow by invitation token (FIXED: use invitation_token field)
            invitation_token = parsed.get("token", "")
            escrow = (
                session.query(Escrow)
                .filter(Escrow.invitation_token == invitation_token)  # type: ignore[attr-defined]
                .first()
            )
        else:
            # Handle direct escrow ID format
            escrow_id = parsed.get(
                "escrow_id"
            )  # CRITICAL BUG FIX: was 'id', should be 'escrow_id'
            if escrow_id:
                escrow = (
                    session.query(Escrow).filter(Escrow.escrow_id == escrow_id).first()
                )
            else:
                escrow = None

        if not escrow:
            logger.error(
                f"Escrow not found for invitation token: {parsed.get('token', start_param)}"
            )
            if update.message:
                await update.message.reply_text(
                    "❌ Trade Invitation Not Found\n\n"
                    "This trade invitation has expired, been cancelled, or the link is invalid.\n\n"
                    "If you believe this is an error, please contact the person who sent you this invitation.\n\n"
                    "💡 Starting normal registration instead...",
                    parse_mode="Markdown",
                )
            return await start_onboarding(update, context)

        # FIXED: Handle both email and phone invitations (not just email)
        escrow_seller_email = getattr(escrow, "seller_email", None)
        escrow_seller_phone = getattr(escrow, "seller_phone", None)

        if not escrow_seller_email and not escrow_seller_phone:
            logger.error(
                f"Escrow {getattr(escrow, 'escrow_id', 'unknown')} has no seller_email or seller_phone"
            )
            return await start_onboarding(update, context)

        seller_info = (
            getattr(escrow, "seller_email", None)
            or getattr(escrow, "seller_phone", None)
            or "unknown"
        )
        logger.info(f"Found escrow {escrow.escrow_id} for seller {seller_info}")

        # Show trade details immediately with decision buttons
        # Note: CryptoService import removed as it's not being used

        # Get currency info
        escrow_currency = getattr(escrow, "currency", None)
        escrow_network = getattr(escrow, "network", None)
        currency_emoji = CURRENCY_EMOJIS.get(
            str(escrow_currency) if escrow_currency else "", "💰"
        )
        network_info = f" ({escrow_network})" if escrow_network else ""

        trade_details = f"""🎉 Welcome to {Config.PLATFORM_NAME}!

📨 You've been invited to a secure trade:

🆔 Trade #<code>{escrow.escrow_id}</code>
👤 Buyer: {get_user_display_name(escrow.buyer)}
💰 Amount: ${float(getattr(escrow, 'total_amount', 0) or 0):.2f} USD
{currency_emoji} Payment: {escrow.currency}{network_info}

📝 Description:
{getattr(escrow, 'description', None) or 'No description provided'}

⏰ Delivery Time: Standard delivery timeframe from acceptance

🔒 How it works:
• Accept the trade to get started
• Buyer pays into secure escrow
• Complete your delivery
• Get paid automatically when done

Ready to proceed?"""

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ Accept",
                        callback_data=f"accept_trade:{escrow.escrow_id}",
                    ),
                    InlineKeyboardButton(
                        "❌ Decline", callback_data=f"decline_trade:{escrow.escrow_id}"
                    ),
                ]
                # CRITICAL FIX: Removed contact button - only show during active trades, not invitations
            ]
        )

        if update.message:
            await update.message.reply_text(
                trade_details, parse_mode="HTML", reply_markup=keyboard
            )
        return (
            ConversationHandler.END
        )  # End conversation, user can now interact with buttons

    except Exception as e:
        logger.error(f"Error handling email invitation for new user: {e}")
        return await start_onboarding(update, context)
    finally:
        session.close()

async def handle_trade_acceptance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int | None:
    """Handle trade acceptance from email invitation"""
    query = update.callback_query
    if not query:
        return

    # PERFORMANCE: Instant acknowledgment
    if query:
        # IMMEDIATE FEEDBACK: Terms and conditions
        await safe_answer_callback_query(query, "📋 Terms and conditions")

    # Handle both accept_trade and decline_trade callbacks
    
    if query and query.data and query.data.startswith("accept_trade:"):
        # Handle trade acceptance
        escrow_id = query.data.split(":")[1]
        
        session = SyncSessionLocal()
        try:
            # Get the escrow
            escrow = session.query(Escrow).filter(Escrow.escrow_id == escrow_id).first()
            
            if not escrow:
                await query.edit_message_text(f"❌ Error: Trade #{escrow_id} not found.")
                return
            
            # Verify escrow is in payment_confirmed status
            if escrow.status != EscrowStatus.PAYMENT_CONFIRMED.value:  # type: ignore[comparison-overlap]
                await query.edit_message_text(
                    f"❌ Error: Trade #{escrow_id} is not ready for acceptance (status: {escrow.status})"
                )
                return
            
            # SECURITY FIX: Validate state transition before acceptance to prevent DISPUTED→ACTIVE
            from utils.escrow_state_validator import EscrowStateValidator
            
            validator = EscrowStateValidator()
            current_status = str(escrow.status)  # Explicit cast to str for type safety
            if not validator.is_valid_transition(current_status, EscrowStatus.ACTIVE.value):
                logger.error(
                    f"🚫 EMAIL_ACCEPT_BLOCKED: Invalid transition {current_status}→ACTIVE for trade {escrow_id}"
                )
                await query.edit_message_text(
                    f"❌ Trade cannot be accepted at this time.\n\n"
                    f"Current status: {current_status}\n\n"
                    f"Please contact support if you believe this is an error."
                )
                return
            
            # Update escrow status to ACTIVE
            escrow.status = EscrowStatus.ACTIVE.value  # type: ignore[assignment]
            escrow.accepted_at = datetime.utcnow()  # type: ignore[attr-defined]
            
            session.commit()
            
            logger.info(f"✅ Trade {escrow_id} accepted by seller - status changed to ACTIVE")
            
            # Send acceptance confirmation
            accept_message = f"""✅ Trade Accepted
            
🆔 Trade: #{escrow_id}
💰 Amount: ${float(getattr(escrow, 'total_amount', 0) or 0):.2f} USD

You have successfully accepted this trade! 
The buyer will be notified and the escrow is now active.

You can now communicate with the buyer and proceed with delivery."""
            
            await query.edit_message_text(accept_message, parse_mode="Markdown")
            
            # Notify buyer about acceptance
            from services.consolidated_notification_service import (
                consolidated_notification_service as NotificationService,
            )
            
            try:
                await NotificationService.send_buyer_seller_accepted_notification(escrow)  # type: ignore[attr-defined]
                logger.info(f"Buyer notification sent for accepted trade {escrow_id}")
            except Exception as notify_error:
                logger.error(f"Failed to notify buyer about accepted trade {escrow_id}: {notify_error}")
            
            return
            
        except Exception as e:
            logger.error(f"Error accepting trade {escrow_id}: {e}")
            await query.edit_message_text("❌ Error processing trade acceptance. Please try again.")
            return
        finally:
            session.close()
    
    elif query and query.data and query.data.startswith("decline_trade:"):
        # Handle trade decline - Show confirmation dialog
        escrow_id = query.data.split(":")[1]

        session = SyncSessionLocal()
        try:
            # Get the escrow
            escrow = session.query(Escrow).filter(Escrow.escrow_id == escrow_id).first()

            if not escrow:
                await query.edit_message_text(
                    f"❌ Error: Trade #{escrow_id} not found."
                )
                return

            # Show confirmation dialog instead of immediately processing
            base_amount = float(getattr(escrow, "amount", 0) or 0)
            
            confirmation_text = f"""⚠️ Confirm Trade Rejection

🆔 Trade: #{escrow.escrow_id}
💰 Amount: ${base_amount:.2f} USD

❗ This action is permanent - you won't be able to accept this trade later.

Are you sure you want to reject this trade forever?"""

            confirmation_keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "❌ Yes, Reject Forever", 
                        callback_data=f"confirm_decline_trade:{escrow_id}"
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "↩️ Go Back", 
                        callback_data=f"view_invitation:{escrow_id}"
                    ),
                ]
            ])

            await query.edit_message_text(
                confirmation_text,
                parse_mode="Markdown",
                reply_markup=confirmation_keyboard
            )
            return
            
        except Exception as e:
            logger.error(f"Error showing decline confirmation for {escrow_id}: {e}")
            await query.edit_message_text("❌ Error showing confirmation. Please try again.")
            return
        finally:
            session.close()
    
    elif query and query.data and query.data.startswith("confirm_decline_trade:"):
        # Handle confirmed trade decline 
        escrow_id = query.data.split(":")[1]

        session = SyncSessionLocal()
        try:
            # Get the escrow
            escrow = session.query(Escrow).filter(Escrow.escrow_id == escrow_id).first()

            if not escrow:
                await query.edit_message_text(
                    f"❌ Error: Trade #{escrow_id} not found."
                )
                return

            # Now process the actual decline
            setattr(escrow, "status", EscrowStatus.CANCELLED.value)
            setattr(escrow, "cancelled_reason", "Declined by seller")
            
            # Send admin notification about escrow cancellation
            try:
                from services.admin_trade_notifications import admin_trade_notifications
                from models import User
                
                # Get buyer and seller information
                buyer = session.query(User).filter(User.id == escrow.buyer_id).first()
                seller = session.query(User).filter(User.id == escrow.seller_id).first() if escrow.seller_id else None  # type: ignore[comparison-overlap]
                
                buyer_info = (
                    buyer.username or buyer.first_name or f"User_{buyer.telegram_id}"
                    if buyer else "Unknown Buyer"
                )
                seller_info = (
                    seller.username or seller.first_name or f"User_{seller.telegram_id}"
                    if seller else "Unknown Seller"
                )
                
                escrow_cancellation_data = {
                    'escrow_id': escrow.escrow_id,
                    'amount': float(escrow.amount) if escrow.amount else 0.0,  # type: ignore[arg-type,comparison-overlap]
                    'currency': 'USD',
                    'buyer_info': buyer_info,
                    'seller_info': seller_info,
                    'cancellation_reason': 'Seller declined invitation',
                    'cancelled_at': datetime.utcnow()
                }
                
                # Send admin notification asynchronously
                import asyncio
                asyncio.create_task(
                    admin_trade_notifications.notify_escrow_cancelled(escrow_cancellation_data)
                )
                logger.info(f"Admin notification queued for escrow cancellation: {escrow_id}")
                
            except Exception as e:
                logger.error(f"Failed to queue admin notification for escrow cancellation: {e}")

            # CRITICAL: Process automatic refund using centralized service
            from services.refund_service import RefundService

            refund_result = RefundService.process_escrow_refund(
                escrow, "seller_declined", session
            )
            refund_processed = refund_result["success"]

            if refund_processed:
                logger.info(
                    f"Refund processed via RefundService: {refund_result['message']} for trade {escrow_id}"
                )
            else:
                logger.info(
                    f"No refund needed: {refund_result['message']} for trade {escrow_id}"
                )

            session.commit()

            logger.info(
                f"Trade {escrow_id} declined by seller, refund_processed: {refund_processed}"
            )

            decline_message = f"""❌ Declined: #{escrow_id}
{refund_result['message'] if refund_processed else 'No refund needed'} • Buyer notified"""

            await query.edit_message_text(decline_message, parse_mode="Markdown")

            # Notify buyer and admin about cancellation
            from services.consolidated_notification_service import (
                consolidated_notification_service as NotificationService,
            )

            # FIXED: Use correct trade notification method for cancelled trades
            try:
                await NotificationService.send_trade_notification(  # type: ignore[attr-defined]
                    escrow=escrow, event_type="cancelled", context=None
                )
                logger.info(
                    f"✅ Buyer notification sent for declined trade #{escrow_id}"
                )
            except Exception as e:
                logger.error(f"❌ Failed to send decline notification to buyer: {e}")

            # Send admin notification
            try:
                from services.email import email_service as admin_email_service
            except ImportError:
                logger.warning(
                    "admin_email service not available - skipping admin notification"
                )
                admin_email_service = None
            if admin_email_service:
                try:
                    # FIXED: Use correct email service method
                    amount_value = getattr(escrow, "amount", None)
                    amount_float = float(amount_value) if amount_value else 0.0

                    # CRITICAL FIX: Check if admin_email_service.send_email returns an awaitable
                    import inspect
                    result = admin_email_service.send_email(
                        to_email=Config.SUPPORT_EMAIL,
                        subject="Trade Declined Alert",
                        text_content=f"Trade #{escrow.escrow_id} declined by seller. Amount: ${amount_float:.2f} USD",
                        html_content=f"""
                        <h3>Trade Declined Alert</h3>
                        <p><strong>Trade ID:</strong> {escrow.escrow_id}</p>
                        <p><strong>Amount:</strong> ${amount_float:.2f} USD</p>
                        <p><strong>Action:</strong> Declined by seller</p>
                        <p><strong>Buyer:</strong> {getattr(escrow.buyer, 'first_name', 'Unknown') if escrow.buyer else 'Unknown'}</p>
                        """,
                    )
                    # Only await if the result is actually awaitable
                    if inspect.iscoroutine(result):
                        await result
                except Exception as e:
                    logger.error(
                        f"Failed to send admin notification for trade decline {escrow_id}: {e}"
                    )

        except Exception as e:
            logger.error(f"Error processing trade decline for {escrow_id}: {e}")
            await query.edit_message_text(
                "❌ Error processing decline. Please try again."
            )
        finally:
            session.close()

async def handle_email_invitation_for_new_user_by_telegram(
    update: Update, context: ContextTypes.DEFAULT_TYPE, invitation
) -> int:
    """
    Handle email invitation when new user joins by Telegram ID
    MODERNIZED: Now with instant feedback, progress indicators, and modern UX patterns
    """
    user = update.effective_user
    if not user:
        return ConversationHandler.END

    session = SyncSessionLocal()
    try:
        escrow = invitation["escrow"]

        # INSTANT FEEDBACK: Show loading state while processing invitation
        loading_msg = None
        if update.message:
            loading_msg = await update.message.reply_text(
                "🔄 <b>Processing Trade Invitation...</b>\n\n📊 Loading trade details...",
                parse_mode="HTML"
            )

        # Get trade details with currency emoji
        currency_emoji = CURRENCY_EMOJIS.get(str(escrow.currency), "💰")

        # Calculate seller's net amount and fee details
        base_amount = float(getattr(escrow, "amount", 0) or 0)
        seller_fee = float(getattr(escrow, "seller_fee_amount", 0) or 0)
        buyer_fee = float(getattr(escrow, "buyer_fee_amount", 0) or 0)
        fee_split = getattr(escrow, "fee_split_option", "split")

        # Determine fee text and seller payout
        if fee_split == "buyer_pays":
            fee_text = "🟢 No fees for you"
            seller_receives = base_amount
        elif fee_split == "seller_pays":
            fee_text = f"🔴 You pay ${seller_fee:.2f} fee"
            seller_receives = base_amount - seller_fee
        else:  # split
            fee_text = f"🟡 Split fees (you pay ${seller_fee:.2f})"
            seller_receives = base_amount - seller_fee

        # MODERN UX: Enhanced trade details with professional formatting and progress indicators
        trade_details = f"""🎉 <b>Welcome to {Config.PLATFORM_NAME}!</b>

🎯 <b>Trade Invitation Received</b>
🟦⬜⬜ <b>Quick 3-step process:</b>
📊 Review trade → 🔐 Setup account → 💰 Get paid

💰 <b>Trade Details:</b>
🆔 Trade #{escrow.escrow_id}
👤 <b>From:</b> {get_user_display_name(escrow.buyer)}
💵 <b>Amount:</b> ${base_amount:.2f} USD {currency_emoji} ✅ <i>Paid & Secured</i>
💸 <b>Fees:</b> {fee_text}
💳 <b>You receive:</b> <u>${seller_receives:.2f} USD</u>

📝 <b>Service:</b>
{getattr(escrow, 'description', 'No description provided')[:80]}{'...' if len(str(getattr(escrow, 'description', ''))) > 80 else ''}

🔒 <b>How it works:</b>
• Accept → Quick account setup → Start delivery
• Buyer's payment is already secured in escrow
• Get paid automatically when task is complete
• Full platform protection & dispute resolution

💡 <b>Choose your next step:</b>"""

        # MODERN UX: Enhanced keyboard with clear action hierarchy
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ Accept & Setup Account",
                        callback_data=f"accept_trade:{escrow.escrow_id}",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "❌ Decline Trade", callback_data=f"decline_trade:{escrow.escrow_id}"
                    ),
                    InlineKeyboardButton(
                        "⏸️ Setup Account First", callback_data="invitation_decide_later"
                    ),
                ]
            ]
        )

        # INSTANT FEEDBACK: Replace loading message with final content
        if loading_msg:
            from utils.callback_utils import safe_edit_message_text
            await safe_edit_message_text(
                update,
                trade_details,
                parse_mode="HTML",
                reply_markup=keyboard,
                message_id=loading_msg.message_id
            )
        elif update.message:
            await update.message.reply_text(
                trade_details, parse_mode="HTML", reply_markup=keyboard
            )

        # MODERN PATTERN: Store invitation context for seamless flow continuation
        if context.user_data is not None:
            context.user_data["pending_invitation"] = {
                "escrow_id": escrow.escrow_id,
                "amount": seller_receives,
                "type": "email_invitation"
            }

        return ConversationHandler.END

    except Exception as e:
        logger.error(f"Error handling email invitation by Telegram: {e}")
        
        # ENHANCED ERROR HANDLING: User-friendly error message
        error_msg = "⚠️ <b>Error Loading Invitation</b>\n\n" \
                   "We're having trouble loading your trade invitation. " \
                   "Please try again in a moment.\n\n" \
                   "💡 <i>Starting account setup instead...</i>"
        
        if update.message:
            await update.message.reply_text(error_msg, parse_mode="HTML")
        
        # Fallback to modern onboarding router instead of old start_onboarding
        from handlers.onboarding_router import onboarding_router
        await onboarding_router(update, context)
        return ConversationHandler.END
    finally:
        session.close()

async def show_multiple_pending_invitations_for_new_user(
    update: Update, context: ContextTypes.DEFAULT_TYPE, invitations_data, user
) -> int | None:
    """Show all pending trade invitations for new users"""
    try:
        escrows = invitations_data["escrows"]
        count = invitations_data["count"]

        text = f"""🎉 Welcome to {Config.PLATFORM_NAME}!

💰 {count} Pending Invitation{'s' if count != 1 else ''}

"""

        # UNIFIED DISPLAY FORMAT (same as messages_hub.py)
        keyboard = []
        
        # Use same clean format as unified trade display
        for escrow in escrows:
            # Get buyer display name
            buyer = escrow.buyer if hasattr(escrow, 'buyer') and escrow.buyer else None
            buyer_name = get_user_display_name(buyer) if buyer else "Buyer"
            
            # Use same amount calculation as unified display
            amount = float(getattr(escrow, 'total_amount', 0) or getattr(escrow, 'amount', 0) or 0)
            
            # Use unified status icon (payment_confirmed = seller pending)
            status_icon = '✅'
            
            # UNIFIED button format (same design as messages_hub.py)
            escrow_display = escrow.escrow_id[-6:] if escrow.escrow_id else str(escrow.id)
            
            keyboard.append([
                InlineKeyboardButton(
                    f"{status_icon} #{escrow_display} • ${amount:.0f} USD with {buyer_name}",
                    callback_data=f"view_invitation:{escrow.escrow_id}"
                )
            ])

        text += """Select a trade to view details and respond:

💡 Complete your account setup after managing these trades."""

        # Add setup button for after handling trades
        keyboard.append(
            [
                InlineKeyboardButton(
                    "🚀 Complete Account Setup", callback_data="continue_onboarding"
                )
            ]
        )

        reply_markup = InlineKeyboardMarkup(keyboard)

        if update.callback_query:
            await update.callback_query.edit_message_text(
                text, reply_markup=reply_markup
            )
        elif update.message:
            await update.message.reply_text(text, reply_markup=reply_markup)

        return ConversationHandler.END

    except Exception as e:
        logger.error(f"Error showing multiple pending invitations for new user: {e}")
        if update.message:
            await update.message.reply_text(
                "❌ Error loading invitations. Please try /start again."
            )
