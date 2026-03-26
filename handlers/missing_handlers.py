"""
Missing Handlers - Critical callback handlers for unresponsive buttons
This module provides handlers for callback patterns that exist in the UI but lack corresponding handlers
"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from datetime import datetime, timezone
from sqlalchemy import or_
from database import SessionLocal
from models import User, Escrow, Rating
from config import Config

# Branding imports
from utils.branding_utils import BrandingUtils, make_header, make_trust_footer
from utils.callback_utils import safe_edit_message_text, safe_answer_callback_query

import telegram

logger = logging.getLogger(__name__)

# ==================== SECURITY UTILITIES ====================

def is_admin(user_id: int) -> bool:
    """Check if user is admin with enhanced security"""
    admin_ids = getattr(Config, 'ADMIN_USER_IDS', [])
    if isinstance(admin_ids, str):
        admin_ids = [int(id.strip()) for id in admin_ids.split(',') if id.strip()]
    return user_id in admin_ids

# ==================== EMAIL VERIFICATION SETTINGS HANDLERS ====================

async def handle_settings_verify_email(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start email verification flow for unverified users from settings"""
    query = update.callback_query
    user_id = update.effective_user.id if update.effective_user else None
    
    if not user_id:
        logger.error("No effective user in handle_settings_verify_email")
        return
    
    # Answer callback first
    if query:
        await safe_answer_callback_query(query, "🔒 Starting email verification...")
    
    try:
        benefits_text = """🔒 <b>Verify Your Email</b>

<b>Get these benefits:</b>
✅ OTP-protected cashouts
✅ Email notifications
✅ Account recovery
✅ Priority support

<b>Quick Setup:</b> Just 2 minutes

Ready to verify your email?"""
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📧 Start Verification", callback_data="start_email_verification")],
            [InlineKeyboardButton("← Back", callback_data="main_menu")]
        ])
        
        from utils.callback_utils import safe_edit_message_text
        if not query:
            return
        await safe_edit_message_text(query, benefits_text, parse_mode="HTML", reply_markup=keyboard)
        
        logger.info(f"✅ Showed email verification benefits to user {user_id}")
        
    except Exception as e:
        logger.error(f"❌ Error in handle_settings_verify_email: {e}")
        if query:
            await safe_edit_message_text(
                query,
                "❌ Error loading verification options.\n\nPlease try again.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
                ])
            )

async def handle_start_email_verification(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle start_email_verification callback - reset to email capture step"""
    query = update.callback_query
    user_id = update.effective_user.id if update.effective_user else None
    
    if not user_id:
        logger.error("No effective user in handle_start_email_verification")
        return
    
    # Answer callback first
    if query:
        await safe_answer_callback_query(query, "📧 Starting email setup...")
    
    try:
        # Import onboarding service and router
        from services.onboarding_service import OnboardingService
        from handlers.onboarding_router import render_step
        from models import OnboardingStep
        from database import get_async_session
        
        # Reset user to email capture step
        async with get_async_session() as session:
            result = await OnboardingService.reset_to_step(
                user_id=user_id,
                step=OnboardingStep.CAPTURE_EMAIL.value,
                session=session
            )
            
            if result.get('success'):
                logger.info(f"✅ Reset user {user_id} to CAPTURE_EMAIL step for verification")
                
                # Render the email capture step
                await render_step(update, OnboardingStep.CAPTURE_EMAIL.value)
                
                # Invalidate user cache to reflect verification status changes
                from utils.update_cache import invalidate_user_cache
                invalidate_user_cache(user_id)
            else:
                error_msg = result.get('error', 'Failed to start verification')
                logger.error(f"❌ Failed to reset user {user_id} to email capture: {error_msg}")
                
                if query:
                    await safe_edit_message_text(
                        query,
                        f"❌ <b>Verification Error</b>\n\n{error_msg}\n\nPlease try again.",
                        parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🔄 Try Again", callback_data="settings_verify_email")],
                            [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
                        ])
                    )
                    
    except Exception as e:
        logger.error(f"❌ Error in handle_start_email_verification: {e}")
        if query:
            await safe_edit_message_text(
                query,
                "❌ Error starting email verification.\n\nPlease try again later.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
                ])
            )

# ==================== NAVIGATION HANDLERS ====================

async def handle_menu_support(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle menu_support callback - redirect to support system"""
    query = update.callback_query
    
    # Answer callback first to prevent timeout
    if query:
        await safe_answer_callback_query(query, "🎧 Opening support...")
    
    # Import and call the support handler
    try:
        from handlers.ux_improvements import handle_contact_support
        await handle_contact_support(update, context)
    except Exception as e:
        logger.error(f"Error opening support from main menu: {e}")
        # Branded fallback message
        header = make_header("Support")
        if not query:
            return
        await query.edit_message_text(
            f"{header}\n\n"
            "🎧 **Support**\n\n"
            "Welcome to our support system!\n"
            "Choose an option below:\n\n"
            f"{make_trust_footer()}",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💬 New Support Ticket", callback_data="start_support_chat")],
                [InlineKeyboardButton("📋 View Support Tickets", callback_data="view_support_tickets")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
            ])
        )

async def handle_main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle main_menu callback - return to main menu with onboarding protection"""
    from telegram.ext import ConversationHandler
    
    query = update.callback_query
    await safe_answer_callback_query(query, "🏠 Returning to main menu...")
    
    # Track user interaction for anomaly detection
    user = query.from_user if query else None
    if user:
        from utils.unified_activity_monitor import track_user_activity
        track_user_activity(
            user_id=user.id,
            action="main_menu_navigation",
            username=user.username or f"user_{user.id}",
            details={
                "handler": "handle_main_menu_callback",
                "callback_data": query.data if query else None,
                "timestamp": query.message.date.isoformat() if query and query.message else None
            }
        )
    
    # CRITICAL: Check onboarding completion before showing main menu
    from models import User
    from utils.session_reuse_manager import get_reusable_session
    
    if user:
        try:
            with get_reusable_session("main_menu_callback_check", user_id=user.id) as session:
                db_user = session.query(User).filter(User.telegram_id == str(user.id)).first()
                if db_user:
                    # Check if user needs onboarding
                    needs_onboarding = (
                        not hasattr(db_user, 'onboarding_completed') or 
                        not db_user.onboarding_completed
                    )
                    if needs_onboarding:
                        logger.info(f"🚨 SECURITY: User {user.id} attempted main menu bypass - redirecting to onboarding")
                        # Redirect to onboarding
                        from handlers.onboarding_router import onboarding_router
                        await onboarding_router(update, context)
                        return ConversationHandler.END
        except Exception as e:
            logger.error(f"Error checking onboarding status: {e}")
    
    # CRITICAL: Clear any persistent conversation states when returning to main menu
    # Use unified cleanup function to ensure consistent state clearing
    if user:
        from utils.conversation_cleanup import clear_user_conversation_state
        cleanup_success = await clear_user_conversation_state(
            user_id=user.id,
            context=context,
            trigger="back_to_menu_button"
        )
        if not cleanup_success:
            logger.warning(f"⚠️ Partial cleanup for user {user.id} - some state may remain")
        
        # CRITICAL: Cancel all background auto-refresh jobs
        # 1. Cancel transaction history auto-refresh
        try:
            from handlers.transaction_history import cancel_tx_auto_refresh
            await cancel_tx_auto_refresh(user.id, context)
        except Exception as tx_error:
            logger.debug(f"Transaction refresh cancellation skipped: {tx_error}")
        
        # 2. Cancel trade status tracker auto-refresh jobs
        try:
            from handlers.ux_improvements import cancel_all_status_refresh_jobs
            await cancel_all_status_refresh_jobs(user.id)
        except Exception as status_error:
            logger.debug(f"Status refresh cancellation skipped: {status_error}")
    
    # Import and call the correct main menu handler
    try:
        from handlers.start import show_main_menu
        result = await show_main_menu(update, context)
        # Ensure effective_user exists
        logger.info(f"✅ Successfully returned user {update.effective_user.id} to main menu")
        return result
    except Exception as e:
        logger.error(f"❌ Error returning to main menu: {e}")
        # Branded fallback main menu
        header = make_header("Main Menu")
        if not query:
            return
        await query.edit_message_text(
            f"{header}\n\n"
            "🏠 **Main Menu**\n\n"
            "Welcome back!\n\n"
            f"{make_trust_footer()}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🤝 Create Trade", callback_data="menu_create")],
                [InlineKeyboardButton("💰 My Wallet", callback_data="menu_wallet")],
                [InlineKeyboardButton("💬 My Trades", callback_data="trades_messages_hub")]
            ]),
            parse_mode="Markdown"
        )
        return ConversationHandler.END

async def handle_back_to_address_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle back_to_address_list callback"""
    query = update.callback_query
    await safe_answer_callback_query(query, "Loading addresses...")
    
    # Redirect to wallet direct handler
    try:
        # Function not found in wallet_direct - using fallback
        if not query:
            return
        await query.edit_message_text(
            "📝 **Address Management**\n\n"
            "Address list is being updated.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💰 Wallet Menu", callback_data="menu_wallet")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
            ]),
            parse_mode="Markdown"
        )
        return
    except Exception as e:
        logger.error(f"Error in back_to_address_list: {e}")
        # Branded navigation error message for address list
        error_msg = BrandingUtils.get_branded_error_message("navigation", "Address list navigation error")
        if not query:
            return
        await query.edit_message_text(
            error_msg,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
            ]),
            parse_mode="Markdown"
        )

async def handle_back_to_bank_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle back_to_bank_list callback"""
    query = update.callback_query
    await safe_answer_callback_query(query, "Loading banks...")
    
    try:
        # Function not found in wallet_direct - using fallback
        if not query:
            return
        await query.edit_message_text(
            "🏦 **Bank Management**\n\n"
            "Bank list is being updated.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💰 Wallet Menu", callback_data="menu_wallet")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
            ]),
            parse_mode="Markdown"
        )
        return
    except Exception as e:
        logger.error(f"Error in back_to_bank_list: {e}")
        # Branded navigation error message for bank list
        error_msg = BrandingUtils.get_branded_error_message("navigation", "Bank list navigation error")
        if not query:
            return
        await query.edit_message_text(
            error_msg,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
            ]),
            parse_mode="Markdown"
        )

# ==================== CASHOUT HANDLERS ====================

async def handle_confirm_bank_cashout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle confirm_bank_cashout callback"""
    query = update.callback_query
    await safe_answer_callback_query(query, "Processing bank cashout...")
    
    try:
        # Function not found in wallet_direct - using fallback
        if not query:
            return
        await query.edit_message_text(
            "🏦 **Bank Cashout**\n\n"
            "Cashout processing is temporarily unavailable.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💰 Wallet Menu", callback_data="menu_wallet")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
            ]),
            parse_mode="Markdown"
        )
        return
    except Exception as e:
        logger.error(f"Error in confirm_bank_cashout: {e}")
        if not query:
            return
        await query.edit_message_text(
            "❌ Processing error. Please try again.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
            ])
        )

async def handle_confirm_crypto_cashout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle confirm_crypto_cashout callback"""
    query = update.callback_query
    await safe_answer_callback_query(query, "Processing crypto cashout...")
    
    try:
        from handlers.wallet_direct import handle_confirm_crypto_cashout
        await handle_confirm_crypto_cashout(update, context)
    except Exception as e:
        logger.error(f"Error in confirm_crypto_cashout: {e}")
        if not query:
            return
        await query.edit_message_text(
            "❌ Processing error. Please try again.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
            ])
        )

async def handle_confirm_ngn_cashout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle confirm_ngn_cashout callback"""
    query = update.callback_query
    await safe_answer_callback_query(query, "Processing NGN bank transfer...")
    
    try:
        from handlers.wallet_direct import handle_confirm_ngn_cashout
        await handle_confirm_ngn_cashout(update, context)
    except Exception as e:
        logger.error(f"Error in confirm_ngn_cashout: {e}")
        if not query:
            return
        await query.edit_message_text(
            "❌ Processing error. Please try again.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
            ])
        )

async def handle_cancel_cashout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle cancel_cashout callback"""
    query = update.callback_query
    await safe_answer_callback_query(query, "Cancelling...")
    
    # Clear wallet state
    if context.user_data:
        context.user_data.pop('pending_cashout', None)
        context.user_data.pop('verified_cashout', None)
        context.user_data.pop('cashout_data', None)
    
    if not query:
        return
    await query.edit_message_text(
        "❌ Cashout cancelled.\n\nNo funds were processed.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 Wallet", callback_data="menu_wallet")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
        ])
    )

# ==================== PAYMENT HANDLERS ====================

async def handle_payment_methods_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle payment_methods callback"""
    query = update.callback_query
    await safe_answer_callback_query(query, "Loading payment methods...")
    
    try:
        # Function not found in fincra_payment - using fallback
        if not query:
            return
        await query.edit_message_text(
            "💳 **Payment Methods**\n\n"
            "Payment methods are being updated.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💰 Wallet Menu", callback_data="menu_wallet")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
            ]),
            parse_mode="Markdown"
        )
        return
    except Exception as e:
        logger.error(f"Error in payment_methods: {e}")
        if not query:
            return
        await query.edit_message_text(
            "❌ Unable to load payment methods.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
            ])
        )

async def handle_cancel_fincra_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle cancel_fincra_payment callback"""
    query = update.callback_query
    await safe_answer_callback_query(query, "Cancelling payment...")
    if not query:
        return
    await query.edit_message_text("⏳ Cancelling payment...")  # Instant visual feedback
    
    # Clear payment data
    if context.user_data:
        context.user_data.pop('fincra_payment_data', None)
        context.user_data.pop('payment_amount', None)
    
    if not query:
        return
    await query.edit_message_text(
        "❌ Payment cancelled.\n\nNo charges were applied.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💰 Try Again", callback_data="payment_methods")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
        ])
    )

# ==================== ADMIN HANDLERS ====================

async def handle_admin_cashout_search_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle admin_cashout_search callback"""
    query = update.callback_query
    # Ensure effective_user exists
    user_id = update.effective_user.id if update.effective_user else 0
    
    if not is_admin(user_id):
        await safe_answer_callback_query(query, "Access denied")
        return
    
    await safe_answer_callback_query(query, "Loading search...")
    
    if not query:
        return
    await query.edit_message_text(
        "🔍 <b>Cashout Search</b>\n\n"
        "Search functionality under development.\n"
        "Use manual review for now.",
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💰 Back to Cashouts", callback_data="admin_trans_cashouts")],
            [InlineKeyboardButton("🏠 Admin Main", callback_data="admin_main")]
        ])
    )

async def handle_admin_cashout_analytics_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle admin_cashout_analytics callback"""
    query = update.callback_query
    # Ensure effective_user exists
    user_id = update.effective_user.id if update.effective_user else 0
    
    if not is_admin(user_id):
        await safe_answer_callback_query(query, "Access denied")
        return
    
    await safe_answer_callback_query(query, "Loading analytics...")
    
    if not query:
        return
    await query.edit_message_text(
        "📊 <b>Cashout Analytics</b>\n\n"
        "Analytics dashboard under development.\n"
        "Check transaction logs for detailed info.",
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💰 Back to Cashouts", callback_data="admin_trans_cashouts")],
            [InlineKeyboardButton("🏠 Admin Main", callback_data="admin_main")]
        ])
    )

async def handle_admin_emergency_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle admin_emergency callback"""
    query = update.callback_query
    # Ensure effective_user exists
    user_id = update.effective_user.id if update.effective_user else 0
    
    if not is_admin(user_id):
        await safe_answer_callback_query(query, "Access denied")
        return
    
    await safe_answer_callback_query(query, "Loading emergency controls...")
    
    if not query:
        return
    
    # Emergency controls temporarily unavailable
    if not query:
        return
    await query.edit_message_text(
        "🚨 <b>Emergency Controls</b>\n\n"
        "Emergency system temporarily unavailable.\n"
        "Contact system administrator.",
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 Admin Main", callback_data="admin_main")]
        ])
    )

# ==================== TRADE AND ESCROW HANDLERS ====================

async def handle_my_escrows(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle my_escrows callback - show user's trades with payment buttons for pending escrows"""
    query = update.callback_query
    
    # Answer callback first to prevent timeout
    if query:
        await safe_answer_callback_query(query, "Loading your trades...")
    
    try:
        from models import User, Escrow, ExchangeOrder
        from database import SessionLocal
        
        user = update.effective_user
        if not user:
            return
            
        # Get user from database with proper session management
        session = SessionLocal()
        try:
            db_user = session.query(User).filter(User.telegram_id == str(user.id)).first()
            if not db_user:
                if query:
                    await query.edit_message_text("❌ User not found. Please use /start to register.")
                return
                
            # Get user's escrows (both as buyer and seller)
            user_escrows = session.query(Escrow).filter(
                (Escrow.buyer_id == db_user.id) | (Escrow.seller_id == db_user.id)
            ).order_by(Escrow.created_at.desc()).limit(5).all()
            
            # Get user's exchange orders
            user_exchanges = session.query(ExchangeOrder).filter(
                ExchangeOrder.user_id == db_user.id
            ).order_by(ExchangeOrder.created_at.desc()).limit(5).all()
            
            if not user_escrows and not user_exchanges:
                keyboard = [[InlineKeyboardButton("🛡️ Create Trade", callback_data="menu_create")]]
                if Config.ENABLE_EXCHANGE_FEATURES:
                    keyboard.append([InlineKeyboardButton("🔄 Exchange Crypto", callback_data="exchange_crypto")])
                keyboard.append([InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")])
                
                if query:
                    await query.edit_message_text(
                        "📋 <b>My Trades</b>\n\n"
                        "You have no trades yet.\n"
                        "Create your first trade to get started!",
                        parse_mode='HTML',
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                return
            
            # Build combined trades list message
            message = "📋 <b>My Trades</b>\n\n"
            buttons = []
            
            # Process escrows first
            for escrow in user_escrows:
                # Determine user role
                role = "Buyer" if escrow.buyer_id == db_user.id else "Seller"
                
                # Check if escrow is still valid (use timezone-aware datetime for comparison)
                now = datetime.now(timezone.utc)  # Use timezone-aware datetime to match database
                escrow_valid = True
                escrow_expired = False
                
                if getattr(escrow, 'expires_at', None):
                    escrow_valid = now <= escrow.expires_at
                    escrow_expired = now > escrow.expires_at
                
                # Get status emoji and text with expiry consideration
                if getattr(escrow, 'status', None) == "payment_pending" and escrow_valid:
                    status_emoji = "⏳"
                    status_text = "PAYMENT PENDING"
                elif getattr(escrow, 'status', None) == "payment_pending" and escrow_expired:
                    status_emoji = "⏰"
                    status_text = "PAYMENT EXPIRED"
                elif getattr(escrow, 'status', None) == "active":
                    status_emoji = "🔒"
                    status_text = "ACTIVE"
                elif getattr(escrow, 'status', None) == "completed":
                    status_emoji = "✅"
                    status_text = "COMPLETED"
                elif getattr(escrow, 'status', None) == "cancelled":
                    status_emoji = "❌"
                    status_text = "CANCELLED"
                elif getattr(escrow, 'status', None) == "expired":
                    status_emoji = "⏰"
                    status_text = "EXPIRED"
                else:
                    status_emoji = "📋"
                    status_text = getattr(escrow, 'status', '').upper()
                
                # Get other party name
                if role == "Buyer":
                    other_user = session.query(User).filter(User.id == escrow.seller_id).first()
                    other_party = f"@{other_user.username}" if other_user and other_user.username else "Seller"
                else:
                    other_user = session.query(User).filter(User.id == escrow.buyer_id).first()
                    other_party = f"@{other_user.username}" if other_user and other_user.username else "Buyer"
                
                # Add escrow info to message
                message += f"<b>#{escrow.escrow_id}</b> {status_emoji} {status_text}\n"
                message += f"💰 ${escrow.amount} • {other_party}\n"
                message += f"🛡️ Escrow • Role: {role}\n\n"
                
                # Add appropriate button based on status, role, AND expiry validity
                if (getattr(escrow, 'status', None) == "payment_pending" and role == "Buyer" and 
                    escrow_valid):
                    # Only show Pay Now button if escrow hasn't expired
                    buttons.append([
                        InlineKeyboardButton(
                            f"💳 Pay #{escrow.escrow_id} (${escrow.amount})", 
                            callback_data=f"pay_escrow:{escrow.escrow_id}"
                        )
                    ])
                elif getattr(escrow, 'status', None) == "active":
                    buttons.append([
                        InlineKeyboardButton(
                            f"📋 View #{escrow.escrow_id}", 
                            callback_data=f"view_escrow:{escrow.escrow_id}"
                        )
                    ])
                elif (getattr(escrow, 'status', None) in ["payment_pending", "expired"] and 
                      escrow_expired and role == "Buyer"):
                    # Show "Create New Trade" button for expired escrows
                    buttons.append([
                        InlineKeyboardButton(
                            f"🛡️ New Trade (#{escrow.escrow_id} expired)", 
                            callback_data="menu_create"
                        )
                    ])
            
            # Process exchange orders 
            for exchange in user_exchanges:
                # Check if rate lock is still valid (use timezone-aware datetime for comparison)
                now = datetime.now(timezone.utc)  # Use timezone-aware datetime to match database
                rate_lock_valid = True
                rate_expired = False
                
                if getattr(exchange, 'rate_lock_expires_at', None):
                    rate_lock_valid = now <= exchange.rate_lock_expires_at
                    rate_expired = now > exchange.rate_lock_expires_at
                
                # Check if overall order is still valid
                order_valid = True
                if getattr(exchange, 'expires_at', None):
                    order_valid = now <= exchange.expires_at
                
                # Get status emoji and text for exchanges
                if getattr(exchange, 'status', None) in ["created", "awaiting_deposit"] and rate_lock_valid and order_valid:
                    status_emoji = "⏳"
                    status_text = "PAYMENT PENDING"
                elif getattr(exchange, 'status', None) in ["created", "awaiting_deposit"] and (rate_expired or not order_valid):
                    status_emoji = "⏰"
                    status_text = "RATE EXPIRED" if rate_expired else "ORDER EXPIRED"
                elif getattr(exchange, 'status', None) == "expired":
                    status_emoji = "⏰"
                    status_text = "EXPIRED"
                elif getattr(exchange, 'status', None) == "processing":
                    status_emoji = "🔄"
                    status_text = "PROCESSING"
                elif getattr(exchange, 'status', None) == "completed":
                    status_emoji = "✅"
                    status_text = "COMPLETED"
                elif getattr(exchange, 'status', None) == "failed":
                    status_emoji = "❌"
                    status_text = "FAILED"
                elif getattr(exchange, 'status', None) == "cancelled":
                    status_emoji = "❌"
                    status_text = "CANCELLED"
                else:
                    status_emoji = "🔄"
                    status_text = getattr(exchange, 'status', '').upper()
                
                # Add exchange info to message
                message += f"<b>#{exchange.exchange_id}</b> {status_emoji} {status_text}\n"
                message += f"🔄 {exchange.source_amount} {exchange.source_currency} → {exchange.target_amount} {exchange.target_currency}\n"
                message += "💱 Exchange\n\n"
                
                # Add appropriate button based on status AND rate lock validity
                if (getattr(exchange, 'status', None) in ["created", "awaiting_deposit"] and 
                    rate_lock_valid and order_valid):
                    # Only show Pay Now button if rate lock is still valid
                    buttons.append([
                        InlineKeyboardButton(
                            f"💳 Pay #{exchange.exchange_id} ({exchange.source_amount} {exchange.source_currency})", 
                            callback_data=f"pay_exchange:{exchange.exchange_id}"
                        )
                    ])
                elif getattr(exchange, 'status', None) in ["processing", "completed"]:
                    buttons.append([
                        InlineKeyboardButton(
                            f"📋 View #{exchange.exchange_id}", 
                            callback_data=f"view_exchange:{exchange.exchange_id}"
                        )
                    ])
                elif (getattr(exchange, 'status', None) in ["created", "awaiting_deposit", "expired"] and 
                      (rate_expired or not order_valid)):
                    # Show "Start New Exchange" button for expired rate locks (only if feature enabled)
                    if Config.ENABLE_EXCHANGE_FEATURES:
                        buttons.append([
                            InlineKeyboardButton(
                                f"🔄 New Exchange (#{exchange.exchange_id} expired)", 
                                callback_data="exchange_crypto"
                            )
                        ])
            
            # Add navigation buttons
            nav_row = [InlineKeyboardButton("🛡️ Create Trade", callback_data="menu_create")]
            if Config.ENABLE_EXCHANGE_FEATURES:
                nav_row.append(InlineKeyboardButton("🔄 Exchange", callback_data="exchange_crypto"))
            buttons.extend([
                nav_row,
                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
            ])
            
            if not query:
                return
            await query.edit_message_text(
                message,
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup(buttons)
            )
            
        finally:
            session.close()
            
    except Exception as e:
        logger.error(f"Error in handle_my_escrows: {e}")
        if not query:
            return
        await query.edit_message_text(
            "❌ Unable to load trades.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
            ])
        )

async def handle_pay_escrow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle pay_escrow callback - initiate payment for pending escrow"""
    query = update.callback_query
    
    if not query or not query.data:
        return
    logger.warning(f"🔴 PAY_ESCROW HANDLER CALLED! Callback data: {query.data if query else 'No query'}")
    
    # Answer callback first to prevent timeout
    if query:
        await safe_answer_callback_query(query, "⏳ Initiating payment...")
        try:
            await query.edit_message_text("⏳ Initiating payment...")  # Instant visual feedback
        except Exception:
            pass
    
    try:
        # Extract escrow ID from callback data - handle both patterns
        if not query or not query.data:
            return
        if 'pay_escrow_' in query.data:
            if not query or not query.data:
                return
            escrow_id = query.data.replace('pay_escrow_', '')
        if not query or not query.data:
            return
        elif 'pay_escrow:' in query.data:
            if not query or not query.data:
                return
            escrow_id = query.data.split(':')[1]
        else:
            if not query or not query.data:
                return
            logger.error(f"❌ Invalid pay_escrow callback format: {query.data}")
            if not query:
                return
            await query.edit_message_text("❌ Invalid payment request. Please try again.")
            return
            
        logger.warning(f"🔍 Extracted escrow ID: {escrow_id}")
        
        from models import User, Escrow
        from database import SessionLocal
        
        user = update.effective_user
        if not user:
            return
            
        session = SessionLocal()
        try:
            # Get user and escrow
            db_user = session.query(User).filter(User.telegram_id == str(user.id)).first()
            if not db_user:
                if not query:
                    return
                await query.edit_message_text("❌ User not found. Please use /start to register.")
                return
            
            escrow = session.query(Escrow).filter(Escrow.escrow_id == escrow_id).first()
            if not escrow:
                if not query:
                    return
                await query.edit_message_text("❌ Escrow not found.")
                return
                
            # Verify user is the buyer
            if escrow.buyer_id != db_user.id:
                if not query:
                    return
                await query.edit_message_text("❌ You are not authorized to pay for this escrow.")
                return
                
            # Verify escrow is in correct status
            if getattr(escrow, 'status', None) != "payment_pending":
                if not query:
                    return
                await query.edit_message_text("❌ This escrow is not pending payment.")
                return
            
            # Check if escrow is still valid (use naive datetime for comparison)
            now = datetime.now(timezone.utc)  # Use timezone-aware datetime to match database
            expires_at = getattr(escrow, 'expires_at', None)
            if expires_at:
                if now > expires_at:
                    if not query:
                        return
                    await query.edit_message_text(
                        f"⏰ <b>Payment Expired</b>\n\n"
                        f"The payment window for this trade has expired.\n"
                        f"Please create a new trade.\n\n"
                        f"<b>Expired Trade:</b> #{escrow_id}\n"
                        f"${escrow.amount}",
                        parse_mode='HTML',
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🛡️ Create New Trade", callback_data="menu_create")],
                            [InlineKeyboardButton("🔙 Back to My Trades", callback_data="my_escrows")]
                        ])
                    )
                    return
            
            # Get seller information
            seller = session.query(User).filter(User.id == escrow.seller_id).first()
            seller_name = f"@{seller.username}" if seller and seller.username else "Seller"
            
            # Calculate fees
            from services.fee_transparency import FeeTransparencyService
            from decimal import Decimal
            fee_calc = FeeTransparencyService.calculate_escrow_fees(
                getattr(escrow, 'amount', Decimal('0')), 
                getattr(escrow, 'buyer_id', 0), 
                getattr(escrow, 'seller_id', 0)
            )
            
            # Show payment options
            message = f"💳 <b>Pay for Escrow #{escrow_id}</b>\n\n"
            message += f"<b>Amount:</b> ${escrow.amount}\n"
            message += f"<b>Seller:</b> {seller_name}\n"
            message += f"<b>Platform Fee:</b> ${fee_calc['platform_fee']:.2f}\n"
            message += f"<b>Total to Pay:</b> ${fee_calc['total_amount']:.2f}\n\n"
            message += f"<b>Description:</b> {getattr(escrow, 'description', None) or 'No description'}\n\n"
            message += "Choose your payment method:"
            
            payment_buttons = [
                [InlineKeyboardButton("₿ Bitcoin (BTC)", callback_data=f"pay_crypto:{escrow_id}:BTC")],
                [InlineKeyboardButton("Ł Litecoin (LTC)", callback_data=f"pay_crypto:{escrow_id}:LTC")],
                [InlineKeyboardButton("💎 Ethereum (ETH)", callback_data=f"pay_crypto:{escrow_id}:ETH")],
                [InlineKeyboardButton("🔙 Back to My Trades", callback_data="my_escrows")]
            ]
            
            if not query:
                return
            await query.edit_message_text(
                message,
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup(payment_buttons)
            )
            
        finally:
            session.close()
            
    except Exception as e:
        logger.error(f"Error in handle_pay_escrow: {e}")
        if query:
            await query.edit_message_text(
                "❌ Unable to initiate payment.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back to My Trades", callback_data="my_escrows")]
                ])
            )

async def handle_pay_exchange(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle pay_exchange callback - initiate payment for pending exchange order"""
    query = update.callback_query
    
    # Answer callback first to prevent timeout
    if query:
        await safe_answer_callback_query(query, "⏳ Initiating exchange payment...")
        try:
            await query.edit_message_text("⏳ Initiating exchange payment...")  # Instant visual feedback
        except Exception:
            pass
    
    try:
        # Extract exchange ID from callback data
        if not query or not query.data:
            return
        if not query or not query.data:
            return
        if not query or not query.data:
            return
        exchange_id = query.data.split(':')[1]
        
        from models import User, ExchangeOrder
        from database import SessionLocal
        
        user = update.effective_user
        if not user:
            return
            
        session = SessionLocal()
        try:
            # Get user and exchange order
            db_user = session.query(User).filter(User.telegram_id == str(user.id)).first()
            if not db_user:
                if not query:
                    return
                await query.edit_message_text("❌ User not found. Please use /start to register.")
                return
            
            exchange = session.query(ExchangeOrder).filter(ExchangeOrder.exchange_id == exchange_id).first()
            if not exchange:
                if not query:
                    return
                await query.edit_message_text("❌ Exchange order not found.")
                return
                
            # Verify user owns this exchange
            if exchange.user_id != db_user.id:
                if not query:
                    return
                await query.edit_message_text("❌ You are not authorized to pay for this exchange.")
                return
                
            # Verify exchange is in correct status
            if getattr(exchange, 'status', None) not in ["created", "awaiting_deposit"]:
                if not query:
                    return
                await query.edit_message_text("❌ This exchange is not pending payment.")
                return
            
            # Check if rate lock is still valid (use naive datetime for comparison)
            now = datetime.now(timezone.utc)  # Use timezone-aware datetime to match database
            rate_lock_expires_at = getattr(exchange, 'rate_lock_expires_at', None)
            if rate_lock_expires_at:
                if now > rate_lock_expires_at:
                    if not query:
                        return
                    keyboard = []
                    if Config.ENABLE_EXCHANGE_FEATURES:
                        keyboard.append([InlineKeyboardButton("🔄 Start New Exchange", callback_data="exchange_crypto")])
                    keyboard.append([InlineKeyboardButton("🔙 Back to My Trades", callback_data="my_escrows")])
                    
                    if not query:
                        return
                    await query.edit_message_text(
                        f"⏰ <b>Rate Lock Expired</b>\n\n"
                        f"The exchange rate for this order has expired.\n"
                        f"Please start a new exchange to get current rates.\n\n"
                        f"<b>Original Order:</b> #{exchange_id}\n"
                        f"{exchange.source_amount} {exchange.source_currency} → {exchange.target_amount} {exchange.target_currency}",
                        parse_mode='HTML',
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                    return
            
            # Check if overall order is still valid
            expires_at = getattr(exchange, 'expires_at', None)
            if expires_at:
                if now > expires_at:
                    if not query:
                        return
                    keyboard = []
                    if Config.ENABLE_EXCHANGE_FEATURES:
                        keyboard.append([InlineKeyboardButton("🔄 Start New Exchange", callback_data="exchange_crypto")])
                    keyboard.append([InlineKeyboardButton("🔙 Back to My Trades", callback_data="my_escrows")])
                    
                    if not query:
                        return
                    await query.edit_message_text(
                        f"⏰ <b>Order Expired</b>\n\n"
                        f"This exchange order has expired.\n"
                        f"Please start a new exchange.\n\n"
                        f"<b>Expired Order:</b> #{exchange_id}",
                        parse_mode='HTML',
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                    return
            
            # Show payment details
            message = f"💱 <b>Pay for Exchange #{exchange_id}</b>\n\n"
            message += f"<b>You're Sending:</b> {exchange.source_amount} {exchange.source_currency}\n"
            message += f"<b>You'll Receive:</b> {exchange.target_amount} {exchange.target_currency}\n"
            message += f"<b>Exchange Rate:</b> {exchange.exchange_rate}\n"
            message += f"<b>Fee:</b> {exchange.fee_amount} {exchange.source_currency}\n\n"
            message += "Send your crypto to complete the exchange:"
            
            # Check if we have a crypto address for payment
            if exchange.crypto_address:
                payment_buttons = [
                    [InlineKeyboardButton("📱 Show QR Code", callback_data=f"show_qr:{exchange.exchange_id}")],
                    [InlineKeyboardButton("🔙 Back to My Trades", callback_data="my_escrows")]
                ]
                
                message += f"\n\n<b>Payment Address:</b>\n<code>{exchange.crypto_address}</code>"
            else:
                payment_buttons = [
                    [InlineKeyboardButton("🔄 Generate Payment Address", callback_data=f"generate_exchange_address:{exchange.exchange_id}")],
                    [InlineKeyboardButton("🔙 Back to My Trades", callback_data="my_escrows")]
                ]
            
            if not query:
                return
            await query.edit_message_text(
                message,
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup(payment_buttons)
            )
            
        finally:
            session.close()
            
    except Exception as e:
        logger.error(f"Error in handle_pay_exchange: {e}")
        if not query:
            return
        await query.edit_message_text(
            "❌ Unable to initiate exchange payment.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back to My Trades", callback_data="my_escrows")]
            ])
        )

async def handle_view_exchange(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle view_exchange callback - show detailed exchange information"""
    query = update.callback_query
    
    # Answer callback first to prevent timeout
    if query:
        await safe_answer_callback_query(query, "Loading exchange details...")
    
    try:
        # Extract exchange ID from callback data
        if not query or not query.data:
            return
        if not query or not query.data:
            return
        if not query or not query.data:
            return
        exchange_id = query.data.split(':')[1]
        
        from models import User, ExchangeOrder
        from database import SessionLocal
        
        user = update.effective_user
        if not user:
            return
            
        session = SessionLocal()
        try:
            # Get user and exchange order
            db_user = session.query(User).filter(User.telegram_id == str(user.id)).first()
            if not db_user:
                if not query:
                    return
                await query.edit_message_text("❌ User not found. Please use /start to register.")
                return
            
            exchange = session.query(ExchangeOrder).filter(ExchangeOrder.exchange_id == exchange_id).first()
            if not exchange:
                if not query:
                    return
                await query.edit_message_text("❌ Exchange order not found.")
                return
                
            # Verify user owns this exchange
            if exchange.user_id != db_user.id:
                if not query:
                    return
                await query.edit_message_text("❌ You are not authorized to view this exchange.")
                return
            
            # Get status emoji and text
            if getattr(exchange, 'status', None) in ["created", "awaiting_deposit"]:
                status_emoji = "⏳"
                status_text = "PAYMENT PENDING"
            elif getattr(exchange, 'status', None) == "processing":
                status_emoji = "🔄"
                status_text = "PROCESSING"
            elif getattr(exchange, 'status', None) == "completed":
                status_emoji = "✅"
                status_text = "COMPLETED"
            elif getattr(exchange, 'status', None) == "failed":
                status_emoji = "❌"
                status_text = "FAILED"
            elif getattr(exchange, 'status', None) == "cancelled":
                status_emoji = "❌"
                status_text = "CANCELLED"
            else:
                status_emoji = "🔄"
                status_text = getattr(exchange, 'status', '').upper()
            
            # Build detailed message
            message = f"<b>#{exchange_id}</b> {status_emoji} {status_text}\n\n"
            message += f"<b>Exchange Type:</b> {exchange.source_currency} → {exchange.target_currency}\n"
            message += f"<b>Sending:</b> {exchange.source_amount} {exchange.source_currency}\n"
            message += f"<b>Receiving:</b> {exchange.target_amount} {exchange.target_currency}\n"
            message += f"<b>Exchange Rate:</b> {exchange.exchange_rate}\n"
            message += f"<b>Fee:</b> {exchange.fee_amount} {exchange.source_currency}\n\n"
            
            # Comprehensive timestamp formatting matching escrow implementation
            def format_timestamp(timestamp, default="Unknown"):
                """Format timestamp in user-friendly format matching escrow display"""
                if timestamp:
                    try:
                        return timestamp.strftime("%b %d, %Y %I:%M %p")
                    except (AttributeError, ValueError):
                        return default
                return default
            
            # Build comprehensive timestamp information based on exchange status
            timestamp_info = ""
            
            # 1. Always show creation time
            created_display = format_timestamp(exchange.created_at, "Unknown")
            timestamp_info += f"\n🕐 <b>Created:</b> {created_display}"
            
            # 2. Add status-specific timestamps
            if getattr(exchange, 'status', None) == 'completed':
                if exchange.completed_at:
                    completed_display = format_timestamp(exchange.completed_at)
                    timestamp_info += f"\n✅ <b>Completed:</b> {completed_display}"
                    
            elif getattr(exchange, 'status', None) in ['failed', 'cancelled']:
                # For failed/cancelled exchanges, show when failure/cancellation occurred
                failed_at = exchange.completed_at or exchange.updated_at
                if failed_at:
                    failed_display = format_timestamp(failed_at)
                    emoji = "❌" if getattr(exchange, 'status', None) == 'failed' else "🚫"
                    status_label = "Failed" if getattr(exchange, 'status', None) == 'failed' else "Cancelled"
                    timestamp_info += f"\n{emoji} <b>{status_label}:</b> {failed_display}"
                    
            elif getattr(exchange, 'status', None) == 'processing':
                # Show when processing started (rate locked time)
                rate_locked_at = getattr(exchange, 'rate_locked_at', None)
                if rate_locked_at:
                    processing_display = format_timestamp(rate_locked_at)
                    timestamp_info += f"\n⚡ <b>Processing Started:</b> {processing_display}"
                    
            # 3. Add rate lock information for relevant statuses
            if getattr(exchange, 'status', None) in ['created', 'awaiting_deposit', 'processing']:
                rate_locked_at = getattr(exchange, 'rate_locked_at', None)
                rate_lock_expires_at = getattr(exchange, 'rate_lock_expires_at', None)
                
                if rate_locked_at and exchange.status != 'processing':  # Don't duplicate for processing
                    locked_display = format_timestamp(rate_locked_at)
                    timestamp_info += f"\n🔒 <b>Rate Locked:</b> {locked_display}"
                    
                if rate_lock_expires_at:
                    expiry_display = format_timestamp(rate_lock_expires_at)
                    # Check if rate lock is still valid
                    from datetime import datetime
                    current_time = datetime.utcnow()  # Use naive datetime to match database
                    if rate_lock_expires_at > current_time:
                        timestamp_info += f"\n⏰ <b>Rate Valid Until:</b> {expiry_display}"
                    else:
                        timestamp_info += f"\n⏰ <b>Rate Expired:</b> {expiry_display}"
                        
            # 4. Add order expiry information for active orders
            if getattr(exchange, 'status', None) in ['created', 'awaiting_deposit', 'processing']:
                expires_at = getattr(exchange, 'expires_at', None)
                if expires_at:
                    expiry_display = format_timestamp(expires_at)
                    # Check if order is still valid
                    from datetime import datetime
                    current_time = datetime.utcnow()  # Use naive datetime to match database
                    if expires_at > current_time:
                        timestamp_info += f"\n⏳ <b>Order Expires:</b> {expiry_display}"
                    else:
                        timestamp_info += f"\n⏳ <b>Order Expired:</b> {expiry_display}"
                        
            # 5. Always show last updated time
            updated_at = getattr(exchange, 'updated_at', None)
            if updated_at:
                updated_display = format_timestamp(updated_at)
                timestamp_info += f"\n🔄 <b>Last Updated:</b> {updated_display}"
                
            # Add comprehensive timestamp information to message
            message += timestamp_info + "\n"
            
            # Add action buttons based on status and rate lock validity
            action_buttons = []
            
            # Check if rate lock and order are still valid (use naive datetime for comparison)
            now = datetime.now(timezone.utc)  # Use timezone-aware datetime to match database
            rate_lock_valid = True
            order_valid = True
            
            if getattr(exchange, 'rate_lock_expires_at', None):
                rate_lock_valid = now <= exchange.rate_lock_expires_at
            
            if getattr(exchange, 'expires_at', None):
                order_valid = now <= exchange.expires_at
            
            if (getattr(exchange, 'status', None) in ["created", "awaiting_deposit"] and 
                rate_lock_valid and order_valid):
                action_buttons.append([
                    InlineKeyboardButton(f"💳 Pay Now ({exchange.source_amount} {exchange.source_currency})", 
                                       callback_data=f"pay_exchange:{exchange_id}")
                ])
            elif (getattr(exchange, 'status', None) in ["created", "awaiting_deposit"] and 
                  (not rate_lock_valid or not order_valid)):
                if Config.ENABLE_EXCHANGE_FEATURES:
                    action_buttons.append([
                        InlineKeyboardButton("🔄 Start New Exchange (Rate Expired)", 
                                           callback_data="exchange_crypto")
                    ])
            
            action_buttons.append([InlineKeyboardButton("🔙 Back to My Trades", callback_data="my_escrows")])
            
            if not query:
                return
            await query.edit_message_text(
                message,
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup(action_buttons)
            )
            
        finally:
            session.close()
            
    except Exception as e:
        logger.error(f"Error in handle_view_exchange: {e}")
        if not query:
            return
        await query.edit_message_text(
            "❌ Unable to load exchange details.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back to My Trades", callback_data="my_escrows")]
            ])
        )

async def handle_view_escrow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle view_escrow callback - show detailed escrow information"""
    query = update.callback_query
    
    # Answer callback first to prevent timeout
    if query:
        await safe_answer_callback_query(query, "Loading escrow details...")
    
    try:
        # Extract escrow ID from callback data
        if not query or not query.data:
            return
        if not query or not query.data:
            return
        if not query or not query.data:
            return
        escrow_id = query.data.split(':')[1]
        
        from models import User, Escrow
        from database import SessionLocal
        
        user = update.effective_user
        if not user:
            return
            
        session = SessionLocal()
        try:
            # Get user and escrow
            db_user = session.query(User).filter(User.telegram_id == str(user.id)).first()
            if not db_user:
                if not query:
                    return
                await query.edit_message_text("❌ User not found. Please use /start to register.")
                return
            
            escrow = session.query(Escrow).filter(Escrow.escrow_id == escrow_id).first()
            if not escrow:
                if not query:
                    return
                await query.edit_message_text("❌ Escrow not found.")
                return
                
            # Verify user is involved in this escrow
            if escrow.buyer_id != db_user.id and escrow.seller_id != db_user.id:
                if not query:
                    return
                await query.edit_message_text("❌ You are not authorized to view this escrow.")
                return
            
            # Determine user role
            role = "Buyer" if escrow.buyer_id == db_user.id else "Seller"
            
            # Get other party information
            if role == "Buyer":
                other_user = session.query(User).filter(User.id == escrow.seller_id).first()
                other_party = f"@{other_user.username}" if other_user and other_user.username else "Seller"
            else:
                other_user = session.query(User).filter(User.id == escrow.buyer_id).first()
                other_party = f"@{other_user.username}" if other_user and other_user.username else "Buyer"
            
            # Check if escrow is still valid (use naive datetime for comparison)
            now = datetime.now(timezone.utc)  # Use timezone-aware datetime to match database
            escrow_valid = True
            escrow_expired = False
            
            expires_at = getattr(escrow, 'expires_at', None)
            if expires_at:
                escrow_valid = now <= expires_at
                escrow_expired = now > expires_at
            
            # Get status emoji and text with expiry consideration
            if getattr(escrow, 'status', None) == "payment_pending" and escrow_valid:
                status_emoji = "⏳"
                status_text = "PAYMENT PENDING"
            elif getattr(escrow, 'status', None) == "payment_pending" and escrow_expired:
                status_emoji = "⏰"
                status_text = "PAYMENT EXPIRED"
            elif getattr(escrow, 'status', None) == "active":
                status_emoji = "🔒"
                status_text = "ACTIVE"
            elif getattr(escrow, 'status', None) == "completed":
                status_emoji = "✅"
                status_text = "COMPLETED"
            elif getattr(escrow, 'status', None) == "cancelled":
                status_emoji = "❌"
                status_text = "CANCELLED"
            elif getattr(escrow, 'status', None) == "expired":
                status_emoji = "⏰"
                status_text = "EXPIRED"
            else:
                status_emoji = "📋"
                status_text = getattr(escrow, 'status', '').upper()
            
            # Build detailed message
            message = f"<b>#{escrow_id}</b> {status_emoji} {status_text}\n\n"
            message += f"<b>Amount:</b> ${escrow.amount}\n"
            message += f"<b>{other_party}:</b> {other_party}\n"
            message += f"<b>Your Role:</b> {role}\n\n"
            
            description = getattr(escrow, 'description', None)
            if description:
                message += f"<b>Description:</b> {description}\n\n"
            
            message += f"<b>Created:</b> {escrow.created_at.strftime('%Y-%m-%d %H:%M UTC')}\n"
            
            # Add action buttons based on status, role, AND expiry validity
            action_buttons = []
            
            if (getattr(escrow, 'status', None) == "payment_pending" and role == "Buyer" and 
                escrow_valid):
                action_buttons.append([
                    InlineKeyboardButton(f"💳 Pay Now (${escrow.amount})", callback_data=f"pay_escrow:{escrow_id}")
                ])
            elif (getattr(escrow, 'status', None) in ["payment_pending", "expired"] and 
                  escrow_expired and role == "Buyer"):
                action_buttons.append([
                    InlineKeyboardButton("🛡️ Create New Trade (Payment Expired)", 
                                       callback_data="menu_create")
                ])
            elif getattr(escrow, 'status', None) == "active":
                if role == "Buyer":
                    action_buttons.append([
                        InlineKeyboardButton("🎯 Mark as Delivered", callback_data=f"mark_delivered:{escrow_id}")
                    ])
                else:  # Seller
                    action_buttons.append([
                        InlineKeyboardButton("💰 Release Funds", callback_data=f"release_funds:{escrow_id}")
                    ])
            
            action_buttons.append([InlineKeyboardButton("🔙 Back to My Trades", callback_data="my_escrows")])
            
            if not query:
                return
            await query.edit_message_text(
                message,
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup(action_buttons)
            )
            
        finally:
            session.close()
            
    except Exception as e:
        logger.error(f"Error in handle_view_escrow: {e}")
        if not query:
            return
        await query.edit_message_text(
            "❌ Unable to load escrow details.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back to My Trades", callback_data="my_escrows")]
            ])
        )

async def handle_menu_escrows(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle menu_escrows callback - show escrows/trades menu"""
    query = update.callback_query
    
    # Answer callback first to prevent timeout
    if query:
        await safe_answer_callback_query(query, "Loading trades menu...")
    
    try:
        if not query:
            return
        await query.edit_message_text(
            "⚡ <b>Active Trades</b>\n\n"
            "Manage your trading activities:",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔔 My Trades", callback_data="my_escrows")],
                [InlineKeyboardButton("🛡️ Create New Trade", callback_data="menu_create")],
                [InlineKeyboardButton("📜 Trade History", callback_data="wal_history")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
            ])
        )
        
    except Exception as e:
        logger.error(f"Error in handle_menu_escrows: {e}")
        if not query:
            return
        await query.edit_message_text(
            "❌ Unable to load trades menu.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
            ])
        )

async def handle_wal_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle wal_history callback - show wallet/transaction history"""
    query = update.callback_query
    
    # Answer callback first to prevent timeout
    if query:
        await safe_answer_callback_query(query, "Loading history...")
    
    try:
        if not query:
            return
        await query.edit_message_text(
            "📜 <b>Transaction History</b>\n\n"
            "Feature under development.\n"
            "Check individual wallets for recent activity.",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💳 Wallet", callback_data="menu_wallet")],
                [InlineKeyboardButton("🔙 Back", callback_data="menu_escrows")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
            ])
        )
        
    except Exception as e:
        logger.error(f"Error in handle_wal_history: {e}")
        if not query:
            return
        await query.edit_message_text(
            "❌ Unable to load history.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
            ])
        )

async def handle_withdrawal_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle withdrawal_history callback - show withdrawal history"""
    query = update.callback_query
    await safe_answer_callback_query(query, "Loading withdrawal history...")
    
    try:
        if not query:
            return
        await query.edit_message_text(
            "📊 <b>Withdrawal History</b>\n\n"
            "Feature under development.\n"
            "Check wallet for recent withdrawals.",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💳 Wallet", callback_data="menu_wallet")],
                [InlineKeyboardButton("🔙 Back", callback_data="wal_history")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
            ])
        )
        
    except Exception as e:
        logger.error(f"Error in handle_withdrawal_history: {e}")
        if not query:
            return
        await query.edit_message_text(
            "❌ Unable to load withdrawal history.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
            ])
        )

# ==================== TRADE HISTORY HANDLER ====================

async def handle_view_disputes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle view_disputes callback - show user's disputes"""
    query = update.callback_query
    
    # Answer callback to prevent timeout
    if query:
        await safe_answer_callback_query(query, "⚠️ Loading disputes...")
    
    user = update.effective_user
    if not user:
        return
    
    from database import async_managed_session
    from sqlalchemy import select
    
    async with async_managed_session() as session:
        try:
            # Get user from database
            stmt = select(User).where(User.telegram_id == user.id)
            result = await session.execute(stmt)
            db_user = result.scalar_one_or_none()
            
            if not db_user:
                if not query:
                    return
                await query.edit_message_text(
                    "❌ User Not Found\n\nPlease register first by typing /start",
                    parse_mode='Markdown'
                )
                return
            
            # Get all user's disputes (where user is initiator OR respondent)
            from models import Dispute
            from sqlalchemy import or_
            stmt = select(Dispute).where(
                or_(
                    Dispute.initiator_id == db_user.id,
                    Dispute.respondent_id == db_user.id
                )
            ).order_by(Dispute.created_at.desc())
            result = await session.execute(stmt)
            disputes = result.scalars().all()
            
            if not disputes:
                # No disputes
                header = make_header("My Disputes")
                message = f"""{header}

⚠️ **My Disputes**

✅ No Active Disputes

You currently have no disputes. This is great!

💡 **Good Trading:**
• Communicate clearly with trade partners
• Follow escrow terms carefully
• Contact support if needed

{make_trust_footer()}"""
                
                keyboard = [
                    [InlineKeyboardButton("💬 Active Trades", callback_data="view_active_trades")],
                    [InlineKeyboardButton("🆘 Support", callback_data="contact_support")],
                    [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
                ]
                
                if not query:
                    return
                await query.edit_message_text(
                    message,
                    parse_mode='Markdown',
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                return
            
            # Build disputes list
            header = make_header("My Disputes")
            message = f"""{header}

⚠️ **My Disputes** ({len(disputes)})

"""
            keyboard = []
            
            for dispute in disputes[:5]:  # Show max 5 recent disputes
                status_emoji = "🔄" if dispute.status == "open" else "✅" if dispute.status == "resolved" else "❌"
                created_date = dispute.created_at.strftime("%m/%d")
                
                message += f"{status_emoji} Dispute #{dispute.id}\n"
                message += f"📅 {created_date} | Status: {dispute.status}\n"
                message += f"💭 {dispute.reason[:30]}{'...' if len(getattr(dispute, 'reason', '')) > 30 else ''}\n\n"
                
                keyboard.append([
                    InlineKeyboardButton(f"📄 View Dispute #{dispute.id}", callback_data=f"view_dispute:{dispute.id}")
                ])
            
            message += f"{make_trust_footer()}"
            
            # Add navigation buttons
            keyboard.extend([
                [InlineKeyboardButton("💬 Active Trades", callback_data="view_active_trades")],
                [InlineKeyboardButton("🆘 Support", callback_data="contact_support")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
            ])
            
            if not query:
                return
            await query.edit_message_text(
                message,
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            
        except Exception as e:
            logger.error(f"Error in handle_view_disputes: {e}")
            if not query:
                return
            await query.edit_message_text(
                "❌ Unable to load disputes.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
                ])
            )

async def handle_trade_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle trade history viewing - show user's complete trade history with rating options"""
    query = update.callback_query
    
    # Answer callback to prevent timeout
    if query:
        await safe_answer_callback_query(query, "📋 Loading trade history...")
    
    user = update.effective_user
    if not user:
        return
    
    session = SessionLocal()
    try:
        # Get user from database
        db_user = session.query(User).filter(User.telegram_id == str(user.id)).first()
        if not db_user:
            if not query:
                return
            await query.edit_message_text(
                "❌ User Not Found\n\nPlease register first by typing /start",
                parse_mode='Markdown'
            )
            return
        
        # Get all user's escrows ordered by creation date (newest first)
        escrows = (
            session.query(Escrow)
            .filter(or_(Escrow.buyer_id == db_user.id, Escrow.seller_id == db_user.id))
            .distinct()
            .order_by(Escrow.created_at.desc())
            .all()
        )
        
        # Also get exchange orders to match dashboard behavior
        from models import ExchangeOrder
        exchange_orders = (
            session.query(ExchangeOrder)
            .filter(ExchangeOrder.user_id == db_user.id)
            .distinct()
            .order_by(ExchangeOrder.created_at.desc())
            .all()
        )
        
        # Combine both types of trades for consistent history display
        total_trades = len(escrows) + len(exchange_orders)
        
        if not escrows and not exchange_orders:
            # No trades at all
            message = """📋 My Trade History

❌ No Trades Found

You haven't created any trades yet.

💡 Get Started:
• Create your first escrow trade
• Start exchanging cryptocurrencies"""
            
            keyboard = [[InlineKeyboardButton("🤝 Create Trade", callback_data="menu_create")]]
            if Config.ENABLE_EXCHANGE_FEATURES:
                keyboard.append([InlineKeyboardButton("🔄 Exchange Crypto", callback_data="exchange_crypto")])
            keyboard.append([InlineKeyboardButton("🏠 Dashboard", callback_data="main_menu")])
        else:
            # Calculate trade statistics
            active_count = 0
            completed_count = 0
            cancelled_count = 0
            disputed_count = 0
            
            for escrow in escrows:
                status = str(getattr(escrow, 'status', '')).lower()
                if status in ["active", "pending_deposit", "payment_confirmed", "pending_acceptance"]:
                    active_count += 1
                elif status == "completed":
                    completed_count += 1
                elif status in ["cancelled", "refunded"]:
                    cancelled_count += 1
                elif status == "disputed":
                    disputed_count += 1
            
            # Check for unrated completed trades
            unrated_trades = []
            for escrow in escrows:
                if str(getattr(escrow, 'status', '')).lower() == "completed":
                    # Check if user has rated this trade
                    existing_rating = session.query(Rating).filter(
                        Rating.escrow_id == escrow.id,
                        Rating.rater_id == db_user.id
                    ).first()
                    
                    if not existing_rating:
                        unrated_trades.append(escrow)
            
            # Build header message
            message = f"""📋 My Trade History

📊 Statistics:
• Total Trades: {total_trades} ({len(escrows)} escrows, {len(exchange_orders)} exchanges)
• Active: {active_count}
• Completed: {completed_count}
• Cancelled: {cancelled_count}"""
            
            if disputed_count > 0:
                message += f"\n• Disputed: {disputed_count}"
            
            if unrated_trades:
                message += f"\n\n⭐ {len(unrated_trades)} unrated trade{'s' if len(unrated_trades) != 1 else ''} - complete your ratings!"
            
            message += "\n\n📋 Recent Trades:"
            
            # Combine and sort all trades (escrows + exchanges) like dashboard does
            all_recent_trades = []
            seen_trade_ids = set()
            
            # Add escrow trades with type identifier (with deduplication)
            for trade in escrows:
                trade_unique_id = f"escrow_{trade.id}"
                if trade_unique_id not in seen_trade_ids:
                    seen_trade_ids.add(trade_unique_id)
                    all_recent_trades.append({
                        'type': 'escrow',
                        'record': trade,
                        'created_at': trade.created_at
                    })
                
            # Add exchange trades with type identifier (with deduplication)
            for trade in exchange_orders:
                trade_unique_id = f"exchange_{trade.id}"
                if trade_unique_id not in seen_trade_ids:
                    seen_trade_ids.add(trade_unique_id)
                    all_recent_trades.append({
                        'type': 'exchange',
                        'record': trade,
                        'created_at': trade.created_at
                    })
            
            # Sort combined list by created_at and take top 5 (handle None values safely)
            all_recent_trades.sort(key=lambda x: x['created_at'] or datetime.min, reverse=True)
            recent_trades = all_recent_trades[:5]
            keyboard = []
            
            for trade_data in recent_trades:
                trade_type = trade_data['type']
                trade = trade_data['record']
                
                if trade_type == 'exchange':
                    # Handle exchange order display - FIXED: Use correct ExchangeOrder fields
                    amount = float(trade.source_amount) if trade.source_amount else 0
                    status = str(trade.status).title()
                    status_emoji = {
                        "Created": "🟡",
                        "Payment_Pending": "⏳", 
                        "Processing": "🔄",
                        "Completed": "✅",
                        "Cancelled": "❌",
                        "Expired": "⏰"
                    }.get(status.replace(" ", "_"), "📋")
                    
                    # Exchange button text - FIXED: Use correct field names and callback format
                    button_text = f"{status_emoji} Exchange #{trade.exchange_id[:8]} • ${amount:.0f} {trade.source_currency}→{trade.target_currency}"
                    keyboard.append([
                        InlineKeyboardButton(button_text, callback_data=f"view_exchange:{trade.id}")
                    ])
                else:
                    # Handle escrow trade display (existing logic)
                    # Get role and counterparty info
                    role = "Buyer" if trade.buyer_id == db_user.id else "Seller"
                    amount = float(trade.amount) if trade.amount else 0
                    
                    # Get counterparty name
                    counterparty_id = trade.seller_id if trade.buyer_id == db_user.id else trade.buyer_id
                    counterparty = session.query(User).filter(User.id == counterparty_id).first()
                    if counterparty:
                        pass
                    elif trade.seller_email and trade.buyer_id == db_user.id:
                        # Seller invited by email
                        trade.seller_email.split('@')[0]
                    
                    # Status formatting
                    status = str(trade.status).title()
                    status_emoji = {
                        "Active": "🟢",
                        "Completed": "✅", 
                        "Cancelled": "❌",
                        "Disputed": "⚖️",
                        "Pending_Deposit": "⏳",
                        "Payment_Confirmed": "💰",
                        "Pending_Acceptance": "📋"
                    }.get(status.replace(" ", "_"), "📋")
                    
                    # Check if this trade needs rating
                    needs_rating = ""
                    if str(trade.status).lower() == "completed":
                        existing_rating = session.query(Rating).filter(
                            Rating.escrow_id == trade.id,
                            Rating.rater_id == db_user.id
                        ).first()
                        if not existing_rating:
                            needs_rating = " ⭐"
                    
                    # Create button text
                    button_text = f"{status_emoji} #{trade.escrow_id[:12]} • ${amount:.0f} • {role}{needs_rating}"
                    keyboard.append([
                        InlineKeyboardButton(button_text, callback_data=f"view_trade_{trade.id}")
                    ])
            
            # FIXED: Track user interaction ONCE per page view (moved outside loop)
            try:
                from utils.unified_activity_monitor import track_user_activity
                track_user_activity(
                    user_id=db_user.id,
                    username=user.username,
                    action="view_trade_history",
                    details={
                        "trade_count": total_trades,
                        "escrow_count": len(escrows),
                        "exchange_count": len(exchange_orders),
                        "has_unrated": len(unrated_trades) > 0
                    }
                )
            except Exception as tracking_error:
                logger.warning(f"Failed to track user activity: {tracking_error}")
            
            # Note: Individual trade buttons above allow access to all trades
            # "View All" button removed - unnecessary since users can scroll through individual trades
            
            # Add quick rating access if there are unrated trades
            if unrated_trades:
                keyboard.append([
                    InlineKeyboardButton(f"⭐ Complete Ratings ({len(unrated_trades)})", callback_data="quick_rating_access")
                ])
            
            # Navigation
            nav_row = [InlineKeyboardButton("🤝 New Trade", callback_data="menu_create")]
            if Config.ENABLE_EXCHANGE_FEATURES:
                nav_row.append(InlineKeyboardButton("🔄 Exchange", callback_data="exchange_crypto"))
            keyboard.append(nav_row)
            keyboard.append([
                InlineKeyboardButton("🏠 Dashboard", callback_data="main_menu")
            ])
        
        # Send the message
        try:
            if not query:
                return
            await query.edit_message_text(
                message,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
            logger.info(f"✅ Trade history displayed for user {user.id}: {len(escrows) if escrows else 0} trades")
        except telegram.error.BadRequest as br_error:
            # Telegram rejects editing message with identical content - this is expected when user clicks same button twice
            if "message is not modified" in str(br_error).lower():
                logger.debug(f"ℹ️ Trade history already displayed (identical content) for user {user.id}")
                await safe_answer_callback_query(query, "")  # Just answer the callback silently
            else:
                raise  # Re-raise other BadRequest errors
        
    except Exception as e:
        logger.error(f"❌ Error handling trade history: {e}")
        try:
            if not query:
                return
            await query.edit_message_text(
                "❌ **Error Loading History**\n\n"
                "Sorry, there was an error loading your trade history.\n"
                "Please try again or contact support.",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Try Again", callback_data="view_trade_history")],
                    [InlineKeyboardButton("🏠 Dashboard", callback_data="trades_messages_hub")]
                ])
            )
        except telegram.error.BadRequest:
            # If even the error message fails to edit, just answer callback
            await safe_answer_callback_query(query, "⚠️ Please try again")
    finally:
        session.close()

# ==================== GENERIC FALLBACK HANDLERS ====================

async def handle_noop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle noop callback - do nothing but answer"""
    await safe_answer_callback_query(update.callback_query, "")

async def handle_exchange_crypto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle exchange crypto button from trade history"""
    query = update.callback_query
    if not query:
        return
    
    # Check if exchange features are enabled
    if not Config.ENABLE_EXCHANGE_FEATURES:
        await safe_answer_callback_query(query, "⚠️ Exchange unavailable")
        if not query:
            return
        await query.edit_message_text(
            "⚠️ Exchange features are currently unavailable. Please try again later.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 Dashboard", callback_data="main_menu")]
            ])
        )
        return
        
    try:
        await safe_answer_callback_query(query, "🔄 Opening Exchange...")
        
        # Redirect to exchange interface
        from handlers.exchange_handler import ExchangeHandler
        from utils.user_access_control import require_feature_access
        start_exchange_with_access = require_feature_access("exchange")(ExchangeHandler.start_exchange)
        await start_exchange_with_access(update, context)
        
        # Ensure effective_user exists
        logger.info(f"✅ Redirected user {update.effective_user.id} to exchange interface")
        
    except Exception as e:
        logger.error(f"❌ Error handling exchange_crypto: {e}")
        keyboard = []
        if Config.ENABLE_EXCHANGE_FEATURES:
            keyboard.append([InlineKeyboardButton("🔄 Try Again", callback_data="exchange_crypto")])
        keyboard.append([InlineKeyboardButton("🏠 Dashboard", callback_data="trades_messages_hub")])
        
        if not query:
            return
        await query.edit_message_text(
            "❌ **Exchange Unavailable**\n\n"
            "Sorry, the exchange service is temporarily unavailable.\n"
            "Please try again later or contact support.",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

async def handle_complete_trading(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle complete trading button"""
    query = update.callback_query
    if not query:
        return
        
    try:
        await safe_answer_callback_query(query, "🤝 Opening Create Trade...")
        
        # Redirect to create trade interface
        from handlers.escrow import handle_create_escrow_start
        await handle_create_escrow_start(update, context)
        
        # Ensure effective_user exists
        logger.info(f"✅ Redirected user {update.effective_user.id} to create trade interface")
        
    except Exception as e:
        logger.error(f"❌ Error handling complete_trading: {e}")
        if not query:
            return
        await query.edit_message_text(
            "❌ **Create Trade Unavailable**\n\n"
            "Sorry, the trade creation service is temporarily unavailable.\n"
            "Please try again later or contact support.",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Try Again", callback_data="complete_trading")],
                [InlineKeyboardButton("🏠 Dashboard", callback_data="trades_messages_hub")]
            ])
        )

async def handle_quick_rating_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle quick rating access button - redirect to rating interface"""
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    query = update.callback_query
    if not query:
        return
        
    try:
        await safe_answer_callback_query(query, "⭐ Opening Rating System...")
        
        # Get user's unrated trades and redirect to rating interface
        
        # Create mock data for direct rating handler (it expects rate_escrow_<id> pattern)
        # Ensure effective_user exists
        user_id = str(update.effective_user.id)  # Convert to string to match database type
        session = SessionLocal()
        
        try:
            # Find user's unrated trades
            user = session.query(User).filter(User.telegram_id == user_id).first()
            if not user:
                if not query:
                    return
                await query.edit_message_text("❌ User not found. Please try again.")
                return
                
            # Find completed trades where user was buyer or seller but hasn't rated yet
            from models import Rating, EscrowStatus
            
            # Get all completed escrows where user was buyer or seller
            user_escrows = session.query(Escrow).filter(
                or_(
                    Escrow.buyer_id == user.id,
                    Escrow.seller_id == user.id
                ),
                Escrow.status == EscrowStatus.COMPLETED.value
            ).all()
            
            # Filter out escrows where user has already given a rating
            unrated_trades = []
            for escrow in user_escrows:
                existing_rating = session.query(Rating).filter(
                    Rating.escrow_id == escrow.id,
                    Rating.rater_id == user.id
                ).first()
                
                if not existing_rating:
                    unrated_trades.append(escrow)
            
            if not unrated_trades:
                if not query:
                    return
                await query.edit_message_text(
                    "✅ **All Caught Up!**\n\n"
                    "You have no pending ratings to complete.\n"
                    "All your trades have been rated.",
                    parse_mode='Markdown',
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🏠 Dashboard", callback_data="trades_messages_hub")]
                    ])
                )
                return
            
            # Set up rating session for first unrated trade
            first_trade = unrated_trades[0]
            escrow_id = first_trade.id
            
            # Determine who is being rated
            if first_trade.buyer_id == user.id:
                # User is buyer, rating seller
                rated_user_id = first_trade.seller_id
                role = "seller"
                rated_user = session.query(User).filter(User.id == rated_user_id).first()
                rated_username = f"@{rated_user.username}" if rated_user and rated_user.username else "user"
            else:
                # User is seller, rating buyer
                rated_user_id = first_trade.buyer_id
                role = "buyer"
                rated_user = session.query(User).filter(User.id == rated_user_id).first()
                rated_username = f"@{rated_user.username}" if rated_user and rated_user.username else "user"
            
            # Store rating context in user_data (required by rating handlers)
            context.user_data['rating_escrow_id'] = escrow_id
            context.user_data['rating_role'] = role
            context.user_data['rated_user_id'] = rated_user_id
            
            # Set conversation state
            from handlers.user_rating_direct import set_user_rating_state
            await set_user_rating_state(user_id, "select", {"escrow_id": escrow_id}, context)
            
            # Show star selection UI
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            
            rating_buttons = [
                [InlineKeyboardButton("⭐⭐⭐⭐⭐ Excellent (5)", callback_data="rating_5")],
                [InlineKeyboardButton("⭐⭐⭐⭐ Good (4)", callback_data="rating_4")],
                [InlineKeyboardButton("⭐⭐⭐ Average (3)", callback_data="rating_3")],
                [InlineKeyboardButton("⭐⭐ Poor (2)", callback_data="rating_2")],
                [InlineKeyboardButton("⭐ Very Poor (1)", callback_data="rating_1")],
                [InlineKeyboardButton("🏠 Back to Dashboard", callback_data="trades_messages_hub")]
            ]
            
            trade_id = first_trade.escrow_id[:12] if first_trade.escrow_id else "Unknown"
            
            # Use plain text to avoid Markdown parsing errors with special characters in usernames
            if not query:
                return
            await query.edit_message_text(
                f"⭐ Rate {rated_username}\n\n"
                f"Trade: #{trade_id}\n\n"
                f"How would you rate this trade partner?\n"
                f"Your feedback helps build trust in the community.\n\n"
                f"Please select a rating:",
                reply_markup=InlineKeyboardMarkup(rating_buttons)
            )
            
            logger.info(f"✅ Rating session initialized for user {user_id}, trade {escrow_id}, rating {role}")
            
        finally:
            session.close()
        
    except Exception as e:
        logger.error(f"❌ Error handling quick_rating_access: {e}")
        if not query:
            return
        await query.edit_message_text(
            "❌ **Rating System Unavailable**\n\n"
            "Sorry, the rating system is temporarily unavailable.\n"
            "Please try again later or contact support.",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Try Again", callback_data="quick_rating_access")],
                [InlineKeyboardButton("🏠 Dashboard", callback_data="trades_messages_hub")]
            ])
        )

async def handle_generic_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generic fallback for unhandled callbacks"""
    query = update.callback_query
    if not query or not query.data:
        return
    callback_data = query.data if query else "unknown"
    
    # Ensure effective_user exists
    logger.warning(f"Unhandled callback: {callback_data} from user {update.effective_user.id if update.effective_user else 'unknown'}")
    
    await safe_answer_callback_query(query, "Feature not available")
    
    if not query:
        return
    await query.edit_message_text(
        "⚠️ Feature under development\n\n"
        "The requested action is not yet available.\n"
        "Please try a different option.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
        ])
    )

# ==================== HANDLER REGISTRATION LIST ====================

MISSING_HANDLERS = [
    # Navigation handlers
    {
        'pattern': '^main_menu$',
        'handler': handle_main_menu_callback,
        'description': 'Main menu navigation'
    },
    {
        'pattern': '^menu_support$',
        'handler': handle_menu_support,
        'description': 'Support menu access from main menu'
    },
    
    # Trade and escrow handlers
    {
        'pattern': '^my_escrows$',
        'handler': handle_my_escrows,
        'description': 'Show user trades/escrows'
    },
    {
        'pattern': '^menu_escrows$',
        'handler': handle_menu_escrows,
        'description': 'Show trades menu'
    },
    {
        'pattern': '^view_trade_history$',
        'handler': handle_trade_history,
        'description': 'Show complete trade history with rating access'
    },
    {
        'pattern': '^wal_history$',
        'handler': handle_wal_history,
        'description': 'Show transaction history'
    },
    {
        'pattern': '^withdrawal_history$',
        'handler': handle_withdrawal_history,
        'description': 'Show withdrawal history'
    },
    {
        'pattern': '^back_to_address_list$',
        'handler': handle_back_to_address_list,
        'description': 'Navigate back to address list'
    },
    {
        'pattern': '^back_to_bank_list$',
        'handler': handle_back_to_bank_list,
        'description': 'Navigate back to bank list'
    },
    
    # Cashout handlers
    {
        'pattern': '^confirm_bank_cashout$',
        'handler': handle_confirm_bank_cashout_callback,
        'description': 'Confirm NGN bank cashout'
    },
    {
        'pattern': '^confirm_crypto_cashout(?::.*)?$',
        'handler': handle_confirm_crypto_cashout_callback,
        'description': 'Confirm crypto cashout (supports both legacy and tokenized flows)'
    },
    {
        'pattern': '^cancel_cashout$',
        'handler': handle_cancel_cashout_callback,
        'description': 'Cancel any cashout process'
    },
    
    # Payment handlers
    {
        'pattern': '^payment_methods$',
        'handler': handle_payment_methods_callback,
        'description': 'Show payment methods'
    },
    {
        'pattern': '^cancel_fincra_payment$',
        'handler': handle_cancel_fincra_payment_callback,
        'description': 'Cancel Fincra payment'
    },
    
    # Admin handlers
    {
        'pattern': '^admin_cashout_search$',
        'handler': handle_admin_cashout_search_callback,
        'description': 'Admin cashout search'
    },
    {
        'pattern': '^admin_cashout_analytics$',
        'handler': handle_admin_cashout_analytics_callback,
        'description': 'Admin cashout analytics'
    },
    {
        'pattern': '^admin_emergency$',
        'handler': handle_admin_emergency_callback,
        'description': 'Admin emergency controls'
    },
    
    # Navigation handlers
    {
        'pattern': '^exchange_crypto$',
        'handler': handle_exchange_crypto,
        'description': 'Exchange crypto from trade history'
    },
    {
        'pattern': '^complete_trading$',
        'handler': handle_complete_trading,
        'description': 'Complete trading button'
    },
    
    # Rating handlers
    {
        'pattern': '^quick_rating_access$',
        'handler': handle_quick_rating_access,
        'description': 'Quick access to rating system for unrated trades'
    },
    
    # Utility handlers
    {
        'pattern': '^noop$',
        'handler': handle_noop_callback,
        'description': 'No operation callback'
    }
]