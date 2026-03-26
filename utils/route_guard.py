"""
Route Guard Utilities - Determine active user session types for message routing
Ensures correct handler receives messages based on user state
"""

import logging
import time
from typing import Optional, Dict, Tuple
from telegram.ext import ContextTypes

from services.onboarding_service import OnboardingService
from utils.universal_session_manager import universal_session_manager, SessionType
from handlers.wallet_direct import has_active_cashout_db_by_telegram

logger = logging.getLogger(__name__)


class RouteGuard:
    """Guards to determine where text messages should be routed"""
    
    # Performance optimization: Cache user conversation states
    # Key: user_id, Value: (conversation_state, user_exists, timestamp)
    _conversation_state_cache: Dict[int, Tuple[str, bool, float]] = {}
    _cache_ttl = 10  # Cache for 10 seconds
    
    @staticmethod
    def invalidate_conversation_cache(user_id: int) -> None:
        """
        Invalidate the conversation state cache for a specific user.
        Call this whenever you update user.conversation_state to ensure fresh data.
        
        Args:
            user_id: Telegram user ID
        """
        if user_id in RouteGuard._conversation_state_cache:
            del RouteGuard._conversation_state_cache[user_id]
            logger.debug(f"🗑️ CACHE INVALIDATED: user {user_id} conversation_state cache cleared")
            try:
                from utils.performance_telemetry import telemetry
                telemetry.record_cache_invalidation('route_guard')
            except Exception:
                pass
    
    @staticmethod
    async def has_active_onboarding(user_id: int) -> bool:
        """Check if user has active onboarding session - highest priority routing"""
        try:
            # Use the explicit session existence check we implemented
            return await OnboardingService.has_active_session(user_id)
        except Exception as e:
            logger.error(f"Error checking onboarding session for user {user_id}: {e}")
            # Default to True for safety - better to route to onboarding than lose the message
            return True
    
    @staticmethod
    async def has_active_cashout(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
        """Check if user has active cashout/wallet session"""
        try:
            # Database-backed check for active cashouts
            db_cashout = await has_active_cashout_db_by_telegram(user_id, context)
            if db_cashout:
                return True
            
            # Check context user_data for active wallet states
            if context.user_data:
                cud = context.user_data
                wallet_states = [
                    'selecting_amount', 'selecting_amount_crypto', 'selecting_amount_ngn',
                    'entering_crypto_address', 'entering_crypto_details', 'verifying_crypto_otp',
                    'verifying_ngn_otp', 'adding_bank_selecting', 'adding_bank_account_number',
                    'adding_bank_confirming', 'adding_bank_label', 'adding_bank_searching',
                    'entering_custom_amount', 'entering_withdraw_address'
                ]
                
                return bool(
                    cud.get('pending_address_save') or
                    cud.get('pending_cashout') or
                    cud.get('wallet_state') in wallet_states or
                    cud.get('current_state') == 'ENTERING_CUSTOM_AMOUNT' or
                    cud.get('wallet_data', {}).get('state') in wallet_states or
                    bool(cud.get('cashout_data', {}).get('amount'))
                )
            
            return False
        except Exception as e:
            logger.error(f"Error checking cashout session for user {user_id}: {e}")
            return False
    
    @staticmethod
    def is_support_chat_active(user_id: int) -> bool:
        """Check if user has active support chat session"""
        try:
            from handlers.support_chat import active_support_sessions
            
            # Check if user has active support session
            has_session = user_id in active_support_sessions
            if has_session:
                logger.info(f"🔥 SUPPORT_CHAT DETECTED: Found active support session for user {user_id}")
            return has_session
        except Exception as e:
            logger.error(f"Error checking support chat session: {e}")
            return False
    
    @staticmethod
    def is_messages_hub_active(context: ContextTypes.DEFAULT_TYPE, user_id: Optional[int] = None) -> bool:
        """Check if user has explicit messages hub session active"""
        try:
            # PRIORITY 1: Check universal_session_manager for active trade_chat sessions
            if user_id:
                trade_chat_sessions = universal_session_manager.get_user_sessions(
                    user_id=user_id,
                    session_type=SessionType.TRADE_CHAT
                )
                if trade_chat_sessions:
                    logger.info(f"🔥 TRADE_CHAT DETECTED: Found {len(trade_chat_sessions)} active session(s) for user {user_id}")
                    return True
            
            # PRIORITY 2: Check context.user_data for legacy session markers
            if not context.user_data:
                return False
            
            # Check for explicit messages hub session markers
            return bool(
                context.user_data.get('active_conversation') == 'messages_hub' or
                context.user_data.get('chat_session_active') or
                context.user_data.get('sending_message_to_trade')
            )
        except Exception as e:
            logger.error(f"Error checking messages hub session: {e}")
            return False
    
    @staticmethod
    async def has_active_escrow_conversation(user_id: int, context: ContextTypes.DEFAULT_TYPE, db_state: Optional[str] = None) -> bool:
        """Check if user has active escrow conversation (highest priority for conversation handlers)
        
        Args:
            user_id: Telegram user ID
            context: Telegram context
            db_state: Optional pre-fetched database state to avoid duplicate queries
        """
        try:
            escrow_states = ['seller_input', 'buyer_input', 'amount_input', 'description_input', 'delivery_time', 'terms_input', 'trade_review']
            
            # STATE EXPIRY FIX: Auto-clear states older than 30 minutes
            STATE_EXPIRY_SECONDS = 1800  # 30 minutes
            
            # First check context.user_data for escrow states
            if context.user_data:
                conversation_state = context.user_data.get('conversation_state')
                
                # Check state timestamp for expiry
                state_timestamp = context.user_data.get('conversation_state_timestamp', 0)
                import time
                current_time = time.time()
                state_age = current_time - state_timestamp
                
                if conversation_state in escrow_states:
                    # STALE STATE FIX: Expire states older than 30 minutes
                    if state_timestamp > 0 and state_age > STATE_EXPIRY_SECONDS:
                        logger.warning(f"⏰ STALE_STATE_CLEARED: user {user_id} escrow state '{conversation_state}' expired (age: {state_age/60:.1f}min)")
                        context.user_data['conversation_state'] = None
                        context.user_data['conversation_state_timestamp'] = 0
                        # Also clear from database
                        try:
                            from database import SessionLocal
                            from models import User
                            from utils.normalizers import normalize_telegram_id
                            normalized_id = normalize_telegram_id(user_id)
                            if normalized_id:
                                with SessionLocal() as session:
                                    user_obj = session.query(User).filter(User.telegram_id == normalized_id).first()
                                    if user_obj:
                                        user_obj.conversation_state = None
                                        session.commit()
                                        # PERFORMANCE: Invalidate cache after clearing stale state
                                        RouteGuard.invalidate_conversation_cache(user_id)
                                        logger.info(f"✅ Cleared stale database state for user {user_id}")
                        except Exception as db_err:
                            logger.error(f"Failed to clear stale database state: {db_err}")
                        return False
                    
                    logger.debug(f"🎯 ESCROW: user {user_id} in context conversation state '{conversation_state}' (age: {state_age/60:.1f}min)")
                    return True
                
                # Also check for early escrow IDs as indication of active escrow creation
                if context.user_data.get('early_escrow_id'):
                    logger.debug(f"🎯 ESCROW: user {user_id} has early escrow ID")
                    return True
            
            # PERFORMANCE: Use pre-fetched db_state if provided to avoid duplicate DB query
            if db_state is not None:
                if db_state in escrow_states:
                    logger.info(f"🎯 ESCROW: user {user_id} in database conversation state '{db_state}' (cached)")
                    return True
                else:
                    logger.info(f"🔍 ESCROW: user {user_id} state '{db_state}' not in escrow states")
                    return False
            
            # Fallback: Check database only if db_state not provided
            from database import SessionLocal
            from models import User
            from utils.normalizers import normalize_telegram_id
            
            normalized_id = normalize_telegram_id(user_id)
            if normalized_id is None:
                logger.warning(f"Invalid user_id provided: {user_id}")
                return False
            
            session = SessionLocal()
            try:
                user_obj = session.query(User).filter(User.telegram_id == normalized_id).first()
                logger.info(f"🔍 DB CHECK: user {normalized_id} found={user_obj is not None}")
                
                if user_obj and hasattr(user_obj, 'conversation_state'):
                    fetched_state = user_obj.conversation_state
                    logger.info(f"🔍 DB STATE: user {normalized_id} conversation_state='{fetched_state}'")
                    
                    if fetched_state in escrow_states:
                        logger.info(f"🎯 ESCROW: user {normalized_id} in database conversation state '{fetched_state}' - RETURNING TRUE")
                        return True
                    else:
                        logger.info(f"🔍 ESCROW: user {normalized_id} state '{fetched_state}' not in escrow states")
                else:
                    logger.info(f"🔍 DB CHECK: user {normalized_id} has no conversation_state attribute")
                        
            except Exception as db_e:
                logger.error(f"Database check failed for escrow state (user {normalized_id}): {db_e}")
            finally:
                session.close()
                
            return False
        except Exception as e:
            logger.error(f"Error checking escrow conversation for user {user_id}: {e}")
            return False

    @staticmethod
    def _is_numeric_amount(text: str) -> bool:
        """Check if text looks like a numeric amount (with decimals, commas, etc.)"""
        if not text:
            return False
        
        # Remove common currency symbols and whitespace
        cleaned = text.strip().replace(',', '').replace('$', '').replace('€', '').replace('£', '')
        
        # Check if it's a valid decimal number
        try:
            float(cleaned)
            return True
        except ValueError:
            return False
    
    @staticmethod
    def _looks_like_crypto_address(text: str) -> bool:
        """Detect if text looks like a crypto address for smart routing to wallet"""
        if not text or len(text) < 26:
            return False
        
        # Ethereum/ERC20 addresses start with 0x and are 42 chars
        if text.startswith('0x') and len(text) == 42:
            # Check if hex chars after 0x
            if all(c in '0123456789abcdefABCDEF' for c in text[2:]):
                return True
        
        # Bitcoin/Litecoin/Dogecoin/other addresses are 26-44 chars, alphanumeric
        if 26 <= len(text) <= 44:
            # Remove common separators
            cleaned = text.replace('_', '').replace('-', '')
            if cleaned.isalnum():
                # Must have mix of letters and numbers (not just numbers)
                has_letters = any(c.isalpha() for c in text)
                has_numbers = any(c.isdigit() for c in text)
                if has_letters and has_numbers:
                    return True
        
        return False

    @staticmethod
    async def get_routing_decision(user_id: int, context: ContextTypes.DEFAULT_TYPE, text: str = "") -> str:
        """
        Determine where to route a text message based on user state
        Returns: 'onboarding', 'wallet', 'messages_hub', 'escrow', or 'fallback'
        """
        # Track latency for routing decisions
        decision_start_time = time.time()
        
        try:
            # PERFORMANCE OPTIMIZATION: Check cache first before database query
            current_time = time.time()
            if user_id in RouteGuard._conversation_state_cache:
                cached_state, cached_exists, cached_time = RouteGuard._conversation_state_cache[user_id]
                if current_time - cached_time < RouteGuard._cache_ttl:
                    # Cache hit - use cached values
                    db_state = cached_state
                    user_exists = cached_exists
                    logger.debug(f"✅ CACHE HIT: user {user_id} state='{db_state}' (age: {current_time - cached_time:.1f}s)")
                    try:
                        from utils.performance_telemetry import telemetry
                        telemetry.record_cache_hit('route_guard')
                    except Exception:
                        pass
                else:
                    # Cache expired - will query database
                    logger.debug(f"⏰ CACHE EXPIRED: user {user_id} (age: {current_time - cached_time:.1f}s)")
                    del RouteGuard._conversation_state_cache[user_id]
            
            # PERFORMANCE FIX: Use async session instead of creating new sync session
            from database import async_managed_session
            from models import User
            from sqlalchemy import select
            from utils.normalizers import normalize_telegram_id
            
            db_state = ""
            user_exists = False
            
            # Only query database if cache miss
            if user_id not in RouteGuard._conversation_state_cache or current_time - RouteGuard._conversation_state_cache[user_id][2] >= RouteGuard._cache_ttl:
                try:
                    from utils.performance_telemetry import telemetry
                    telemetry.record_cache_miss('route_guard')
                except Exception:
                    pass
                try:
                    # CRITICAL FIX: Normalize telegram_id to int for database query
                    normalized_id = normalize_telegram_id(user_id)
                    if normalized_id is None:
                        logger.warning(f"Invalid user_id provided for routing: {user_id}")
                        return 'fallback'
                    
                    async with async_managed_session() as session:
                        # FIX: Query User.id to check if user exists, not just conversation_state
                        stmt = select(User.id, User.conversation_state).where(User.telegram_id == normalized_id)
                        result = await session.execute(stmt)
                        row = result.first()
                        
                        if row:
                            user_exists = True
                            db_state = row[1] or ""  # row[1] is conversation_state
                        
                        # Cache the result
                        RouteGuard._conversation_state_cache[user_id] = (db_state, user_exists, current_time)
                        logger.info(f"🔍 DB CHECK: user {normalized_id} found={user_exists}, conversation_state='{db_state}' (cached)")
                except Exception as e:
                    logger.debug(f"Could not get DB state for smart routing: {e}")
            else:
                # Use cached values
                db_state, user_exists, _ = RouteGuard._conversation_state_cache[user_id]
                logger.debug(f"📦 USING CACHE: user {user_id} state='{db_state}'")
            
            # HIGHEST PRIORITY: Active trade chat sessions (explicit user-initiated sessions)
            # Check this FIRST to prevent stale database states from hijacking messages
            if RouteGuard.is_messages_hub_active(context, user_id):
                logger.info(f"🎯 ROUTE DECISION: user {user_id} → messages_hub (active trade chat session)")
                return 'messages_hub'
            
            # SECOND PRIORITY: Crypto address detection - MUST come before support chat check
            # BUG FIX: Stale support sessions were hijacking crypto address submissions for cashout
            # If user sends a crypto address, it should ALWAYS route to wallet regardless of support session
            if text and RouteGuard._looks_like_crypto_address(text):
                # Auto-clear stale support session if present
                if RouteGuard.is_support_chat_active(user_id):
                    logger.warning(f"⚠️ SUPPORT_OVERRIDE: user {user_id} sent crypto address while support session active - routing to wallet, clearing stale support session")
                    try:
                        from handlers.support_chat import active_support_sessions
                        if user_id in active_support_sessions:
                            del active_support_sessions[user_id]
                    except Exception:
                        pass
                logger.info(f"🎯 SMART ROUTE: user {user_id} sent crypto address → wallet (for cashout)")
                return 'wallet'
            
            # SECOND-B PRIORITY: Active wallet input session (check before support chat)
            # CRITICAL FIX: Wallet input states must not be hijacked by stale support sessions
            if db_state == "wallet_input":
                logger.info(f"🎯 ROUTE DECISION: user {user_id} → wallet (active wallet_input session: {db_state})")
                return 'wallet'
            
            # SECOND-C PRIORITY: Active cashout/wallet operations (MUST CHECK BEFORE SUPPORT)
            # CRITICAL FIX: OTP verification and cashout flows must take priority over support sessions
            # to prevent OTP codes and addresses from being swallowed by stale support chat
            if await RouteGuard.has_active_cashout(user_id, context):
                # Auto-clear stale support session if present
                if RouteGuard.is_support_chat_active(user_id):
                    logger.warning(f"⚠️ SUPPORT_OVERRIDE: user {user_id} has active cashout while support session active - routing to wallet, clearing stale support session")
                    try:
                        from handlers.support_chat import active_support_sessions
                        if user_id in active_support_sessions:
                            del active_support_sessions[user_id]
                    except Exception:
                        pass
                logger.info(f"🎯 ROUTE DECISION: user {user_id} → wallet (active cashout/OTP verification)")
                return 'wallet'
            
            # THIRD PRIORITY: Active support chat sessions
            # Now checked AFTER wallet/cashout operations to prevent stale sessions from hijacking critical flows
            if RouteGuard.is_support_chat_active(user_id):
                logger.info(f"🎯 ROUTE DECISION: user {user_id} → support (active support chat session)")
                return 'support'
            
            # FOURTH PRIORITY: Smart UX - Trade review mode + numeric input
            if db_state == "trade_review" and text and RouteGuard._is_numeric_amount(text):
                logger.info(f"🎯 SMART ROUTE: user {user_id} in trade_review typing amount '{text}' → escrow")
                return 'escrow'
            
            # FOURTH-B PRIORITY: Active rating session (check before escrow/onboarding)
            # CRITICAL FIX: Detect rating states to prevent misrouting to onboarding
            # DEFENSIVE: Only route if there's a meaningful step after "rating_" prefix
            if db_state and db_state.startswith("rating_"):
                rating_step = db_state.replace("rating_", "")
                if rating_step and rating_step != "":
                    logger.info(f"🎯 ROUTE DECISION: user {user_id} → rating (active rating session: {db_state})")
                    return 'rating'
                else:
                    logger.debug(f"⏭️ SKIP RATING ROUTE: user {user_id} has empty rating state '{db_state}' - routing to next priority")
            
            # FIFTH PRIORITY: Active escrow conversation (ConversationHandler states)
            # PERFORMANCE: Pass db_state to avoid duplicate DB query
            escrow_active = await RouteGuard.has_active_escrow_conversation(user_id, context, db_state=db_state)
            
            if escrow_active:
                logger.info(f"🎯 ROUTE DECISION: user {user_id} → escrow (active conversation)")
                return 'escrow'
            
            # FIFTH-B PRIORITY: Active dispute session (check before onboarding)
            # CRITICAL FIX: Dispute states must be checked before onboarding
            # to prevent dispute messages from being routed to onboarding
            dispute_states = ['dispute_chat', 'dispute_messaging', 'multi_dispute_selected']
            if db_state in dispute_states:
                logger.info(f"🎯 ROUTE DECISION: user {user_id} → dispute (active dispute session: {db_state})")
                return 'dispute'
            
            # FIFTH-B PRIORITY: Admin states (MUST CHECK BEFORE ONBOARDING)
            # CRITICAL FIX: Admin broadcast and other admin flows must not be routed to onboarding
            # Admin states start with "admin_" prefix (admin_broadcast_, admin_transaction_, etc.)
            # DO NOT route to 'fallback' - admin broadcast text router will handle it
            if db_state and db_state.startswith("admin_"):
                logger.info(f"🎯 ROUTE DECISION: user {user_id} → admin (admin state: {db_state}) - admin_broadcast_text_router will handle")
                return 'admin'
            
            # SIXTH PRIORITY: Active onboarding or new user registration
            # CRITICAL FIX: Use user_exists to properly route onboarding
            if not user_exists:
                # New user - needs to go through onboarding registration
                logger.info(f"🎯 ROUTE DECISION: user {user_id} → onboarding (new user, user_exists=False)")
                return 'onboarding'
            
            onboarding_active = await RouteGuard.has_active_onboarding(user_id)
            
            if onboarding_active:
                logger.info(f"🎯 ROUTE DECISION: user {user_id} → onboarding (active session, user_exists=True)")
                return 'onboarding'
            
            # Default: No active session - fallback
            logger.info(f"🎯 ROUTE DECISION: user {user_id} → fallback (no active session)")
            return 'fallback'
            
        except Exception as e:
            logger.error(f"Error determining routing for user {user_id}: {e}")
            # Default to onboarding for safety
            return 'onboarding'
        finally:
            # Record routing decision latency
            try:
                decision_latency = (time.time() - decision_start_time) * 1000  # Convert to milliseconds
                from utils.performance_telemetry import telemetry
                telemetry.record_latency('route_decision', decision_latency)
            except Exception:
                pass


class OnboardingProtection:
    """Protection mechanisms to prevent other handlers from interfering with onboarding"""
    
    @staticmethod
    async def should_block_processing(user_id: int, handler_name: str) -> bool:
        """
        Check if handler should be blocked from processing due to active onboarding
        Returns True if handler should exit early
        """
        if handler_name == 'onboarding':
            return False  # Onboarding handler should never be blocked
            
        # Allowlist for edit operations - these should proceed even during onboarding
        edit_allowlist = {
            'escrow_edit', 'wallet_balance_check', 'payment_method_edit',
            'handle_make_payment', 'handle_payment_method_selection',
            'handle_view_trade', 'handle_trade_review_callbacks',
            'handle_fee_split_selection', 'messages_hub_cleanup',
            'view_trades_messages_hub', 'escrow_direct_handlers',
            'payment_processing', 'crypto_payment', 'ngn_payment'
        }
        
        if handler_name in edit_allowlist:
            logger.info(f"✅ EDIT ALLOWLIST: Allowing {handler_name} for user {user_id} (edit operation)")
            return False  # Don't block allowlisted edit operations
        
        try:
            has_onboarding = await RouteGuard.has_active_onboarding(user_id)
            if has_onboarding:
                logger.warning(f"🚫 ONBOARDING PROTECTION: Blocking {handler_name} for user {user_id} (active onboarding session)")
                return True
            
            return False
        except Exception as e:
            logger.error(f"Error in onboarding protection check: {e}")
            return False  # Don't block on errors
    
    @staticmethod
    def get_protection_message() -> str:
        """Get standard message for when handler is blocked due to onboarding"""
        return (
            "⚠️ **Please complete your registration first**\n\n"
            "You have an active registration process that needs to be completed.\n"
            "Use /start to continue your registration."
        )