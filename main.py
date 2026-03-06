#!/usr/bin/env python3
"""
Optimized Telegram Escrow Bot - Fast Startup Implementation
Uses lazy loading and parallel operations for sub-5-second startup
"""

import logging
import asyncio
import time
import os
from decimal import Decimal
from telegram import Update
from telegram.ext import Application, ContextTypes

# Import critical infrastructure only
from config import Config
from database import SessionLocal, create_tables

# Import basic monitoring only
from utils.performance_monitor import PerformanceMonitor

logger = logging.getLogger(__name__)

# Clean startup manager - replaces global variables
from main_clean_startup import startup_manager

# Import background and deferred managers for email queue initialization
from utils.lazy_loader import background_manager
from utils.parallel_startup import deferred_manager

def get_application_instance():
    """Get the application instance from webhook server"""
    try:
        from webhook_server import _bot_application
        return _bot_application
    except ImportError:
        logger.error("Failed to import bot application from webhook_server")
        return None


async def setup_critical_only(application):
    """Setup only critical components for immediate bot functionality"""
    logger.info("🚀 Setting up critical infrastructure only...")
    
    # CRITICAL FIX: Initialize State Manager first for financial security
    try:
        from services.state_manager import initialize_state_manager
        logger.info("🔒 Initializing State Manager for financial security...")
        state_manager_success = await initialize_state_manager()
        if state_manager_success:
            logger.info("✅ State Manager initialized successfully")
        else:
            logger.warning("⚠️ State Manager initialization failed - financial operations may be limited")
    except Exception as e:
        logger.error(f"❌ State Manager initialization error: {e}")
        logger.warning("⚠️ Continuing startup with limited financial coordination")
    
    # Simple critical setup - no complex optimization systems
    logger.info("✅ Critical infrastructure setup complete (simplified)")
    
    # Escrow handler will be registered once in register_all_handlers
    
    logger.info("✅ Critical infrastructure ready - bot can start")
    return True


async def register_handlers_directly(application):
    """Register handlers directly without lazy loading complexity"""
    logger.info("📋 Registering handlers directly...")
    
    # Import and register handlers that actually exist
    try:
        from handlers.transaction_history import register_transaction_history_handlers
        
        # Register transaction history handlers (the critical fix for button functionality)
        register_transaction_history_handlers(application)
        
        logger.info("✅ All handlers registered directly")
    except Exception as e:
        logger.error(f"❌ Handler registration failed: {e}")
        raise e


async def start_background_systems():
    """Simplified background systems - no complex optimization"""
    logger.info("✅ Background systems disabled per simplification requirements")
    
    # CRITICAL: Initialize webhook processing system FIRST to prevent lost webhook events
    try:
        logger.info("🔧 BACKGROUND: Initializing webhook processing system...")
        from services.webhook_startup_service import webhook_startup_service
        await webhook_startup_service.initialize_webhook_system()

        logger.info("✅ BACKGROUND: Webhook processing system started successfully")
    except Exception as e:
        logger.error(f"❌ CRITICAL: Webhook processing system failed to start: {e}")
    
    # CRITICAL: Pre-warm crypto rates BEFORE webhooks can be processed
    try:
        logger.info("🔥 BACKGROUND: Pre-warming critical crypto rates for webhook reliability...")
        from services.fastforex_service import startup_prewarm_critical_rates
        prewarm_success = await startup_prewarm_critical_rates()
        
        if prewarm_success:
            logger.info("✅ BACKGROUND: Crypto rates pre-warmed successfully - webhooks can process immediately")
        else:
            logger.warning("⚠️ BACKGROUND: Some crypto rates failed to pre-warm - emergency fallback enabled")
    except Exception as e:
        logger.error(f"❌ CRITICAL: Crypto rate pre-warming failed: {e}")
        logger.warning("⚠️ Continuing with emergency fallback enabled for webhook processing")
    
    # CRITICAL: Initialize background email queue for OTP delivery
    try:
        logger.info("🔧 BACKGROUND: Initializing background email queue...")
        from services.background_email_queue import initialize_email_queue
        email_queue_success = await initialize_email_queue()
        
        if email_queue_success:
            logger.info("✅ BACKGROUND: Background email queue started successfully")
        else:
            logger.error("❌ BACKGROUND: Background email queue initialization failed")
    except Exception as e:
        logger.error(f"❌ CRITICAL: Background email queue system failed to start: {e}")
    
    # Start background tasks for heavy operations
    await background_manager.start_background_tasks()
    
    # Start deferred operations  
    await deferred_manager.start_deferred_operations()
    
    logger.info("✅ Background systems startup completed")
    
    # Note: Scheduler systems disabled per task requirements
    logger.info("✅ Scheduler systems disabled per simplification requirements")
    
    logger.info("✅ Background systems started")


async def run_commands_migration(application):
    """
    Run bot commands migration for existing onboarded users.
    This fixes the critical bug where existing users don't get full command menu.
    """
    try:
        logger.info("🔧 MIGRATION: Starting bot commands migration for existing onboarded users...")
        from utils.bot_commands_migration import migrate_onboarded_user_commands
        
        result = await migrate_onboarded_user_commands(application)
        
        if result.get("success"):
            migrated = result.get("migrated_count", 0)
            total = result.get("total_users", 0)
            errors = result.get("error_count", 0)
            
            if errors > 0:
                logger.warning(
                    f"⚠️ MIGRATION: Completed with errors - "
                    f"Success: {migrated}/{total}, Errors: {errors}"
                )
            else:
                logger.info(
                    f"✅ MIGRATION: Successfully restored commands for "
                    f"{migrated}/{total} onboarded users"
                )
        else:
            logger.error(
                f"❌ MIGRATION: Failed - {result.get('error', 'Unknown error')}"
            )
    except Exception as e:
        logger.error(f"❌ MIGRATION: Unexpected error during commands migration: {e}")
        # Don't crash the bot - migration failure is not critical to operations


def main_new_debug():
    """Optimized bot startup with sub-5-second target"""
    
    # Initialize performance monitoring
    from utils.performance_monitor import PerformanceMonitor
    monitor = PerformanceMonitor()
    monitor.start_startup_monitoring()
    
    from config import Config
    
    logger.info(f"Starting bot - USE_WEBHOOK: {Config.USE_WEBHOOK}")
    logger.info("⚡ Starting OPTIMIZED bot with lazy loading...")
    
    if Config.USE_WEBHOOK:
        # Webhook mode with optimizations
        logger.info("Starting webhook optimized mode")
        asyncio.run(run_webhook_optimized(monitor))
    else:
        # Polling mode with optimizations
        logger.info("Starting polling optimized mode")  
        asyncio.run(run_polling_optimized(monitor))


# REMOVED: Orphaned code block that referenced undefined 'application' variable
# All handler registration is now done within the run_webhook_optimized and run_polling_optimized functions
    


def register_emergency_handlers(application):
    """Register emergency callback handlers for critical functionality"""
    from telegram.ext import CallbackQueryHandler
    from handlers.messages_hub import show_trades_messages_hub, show_active_trades, open_trade_chat, handle_dispute_trade, handle_dispute_reason
    from handlers.escrow import handle_view_trade, handle_buyer_cancel_trade, handle_seller_accept_trade, handle_seller_decline_trade, handle_confirm_seller_decline_trade, handle_cancel_escrow, handle_buyer_cancel_confirmed, handle_keep_trade
    from handlers.missing_handlers import handle_main_menu_callback, handle_trade_history, handle_menu_support, handle_view_disputes  
    from handlers.ux_improvements import handle_contact_support
    from handlers.exchange_handler import ExchangeHandler
    from handlers.multi_dispute_manager_direct import direct_select_dispute
    from handlers.payment_recovery_handler import PaymentRecoveryHandler
    from handlers.wallet_direct import show_crypto_funding_options
    
    # Create wrapper functions for handlers that need parameter adaptation
    async def handle_escrow_history_wrapper(update, context):
        """Wrapper for escrow history that fetches user internally"""
        from handlers.menu import show_escrow_history
        from models import User
        from database import SessionLocal
        
        if not update.effective_user:
            return
            
        session = SessionLocal()
        try:
            from utils.repository import UserRepository
            user = UserRepository.get_user_by_telegram_id(session, update.effective_user.id)
            if user:
                await show_escrow_history(update, context, user)
        finally:
            session.close()
    
    emergency_handlers = [
        (show_trades_messages_hub, '^trades_messages_hub$'),
        (show_active_trades, '^view_active_trades.*$'),
        (handle_trade_history, '^view_trade_history$'),
        (handle_view_trade, '^view_trade_.*$'),  # FIX: Missing critical handler
        (open_trade_chat, '^trade_chat_open:.*$'),  # ✅ FIXED: Chat handler imported and registered
        (handle_contact_support, '^contact_support$'),  # ✅ FIXED: Contact support handler
        (handle_view_disputes, '^view_disputes$'),  # ✅ FIXED: View disputes handler
        (direct_select_dispute, '^view_dispute:.*$'),  # ✅ FIXED: View individual dispute handler
        # REMOVED: handle_dispute_trade - Registered in critical handlers section to avoid duplicate
        (handle_dispute_reason, '^dispute_reason:.*$'),  # FIX: Missing dispute reason handler - creates actual dispute
        (handle_buyer_cancel_trade, '^buyer_cancel_.*$'),  # FIX: Missing buyer cancel handler
        (handle_buyer_cancel_confirmed, '^confirm_buyer_cancel_.*$'),  # FIX: Missing cancel confirmation handler
        (handle_keep_trade, '^keep_trade_.*$'),  # FIX: Handler for "No, Keep Trade" button with clear feedback
        (handle_cancel_escrow, '^cancel_escrow$'),  # FIXED: Pattern matches button callback_data (cancel_escrow)
        (handle_seller_accept_trade, '^accept_trade:.*$'),  # FIXED: Pattern matches button callback_data
        (handle_seller_decline_trade, '^decline_trade:.*$'),  # FIXED: Pattern matches button callback_data
        (handle_confirm_seller_decline_trade, '^confirm_seller_decline_.*$'),  # FIX: Missing decline confirmation handler
        (show_active_trades, '^messages_trade_list$'),  # FIX: Missing trade list handler for "My Trades" button
        (handle_escrow_history_wrapper, '^escrow_history$'),  # FIX: Missing escrow history handler
        (ExchangeHandler.show_exchange_history, '^exchange_history$'),  # FIX: Missing exchange history handler (static method)
        (handle_main_menu_callback, '^main_menu$'),  # FIX: Missing main menu handler
        (handle_menu_support, '^menu_support$'),  # FIX: Missing support button handler
        (show_crypto_funding_options, '^(crypto_funding_start|crypto_funding_start_direct|crypto_funding_options)$'), # FIX: Crypto selection
        # ✅ PAYMENT RECOVERY HANDLERS: For underpayment action buttons
        (PaymentRecoveryHandler.handle_complete_payment, '^pay_complete:'),  # Complete payment button
        (PaymentRecoveryHandler.handle_proceed_partial, '^pay_partial:'),  # Proceed with partial amount button
        (PaymentRecoveryHandler.handle_cancel_and_refund, '^pay_cancel:'),  # Cancel & refund to wallet button
    ]
    
    # Apply blocking check to all emergency handlers
    from utils.conversation_protection import create_blocking_aware_handler
    
    for func, pattern in emergency_handlers:
        try:
            blocked_aware_func = create_blocking_aware_handler(func)
            application.add_handler(CallbackQueryHandler(blocked_aware_func, pattern=pattern), group=0)
            logger.info(f"✅ EMERGENCY: Registered {func.__name__} with pattern {pattern} (blocking-aware)")
        except Exception as e:
            logger.error(f"❌ EMERGENCY: Failed to register {func.__name__}: {e}")
    
    logger.info("🚨 EMERGENCY HANDLER REGISTRATION COMPLETE")
    return True


async def run_webhook_optimized(monitor):
    """Run bot in optimized webhook mode"""
    # Clean application management without globals
    
    logger.info("Webhook mode enabled - starting optimized bot")
    
    # CRITICAL FIX: Initialize State Manager first for financial security
    try:
        from services.state_manager import initialize_state_manager
        logger.info("🔒 Initializing State Manager for financial security...")
        state_manager_success = await initialize_state_manager()
        if state_manager_success:
            logger.info("✅ State Manager initialized successfully")
        else:
            logger.warning("⚠️ State Manager initialization failed - financial operations may be limited")
    except Exception as e:
        logger.error(f"❌ State Manager initialization error: {e}")
        logger.warning("⚠️ Continuing startup with limited financial coordination")
    
    # CRITICAL FIX: Initialize Background Email Queue for OTP delivery
    try:
        from main_startup_integration import initialize_background_email_system
        logger.info("📧 Initializing Background Email Queue...")
        email_queue_success = await initialize_background_email_system()
        if email_queue_success:
            logger.info("✅ Background Email Queue initialized successfully")
        else:
            logger.warning("⚠️ Background Email Queue initialization failed - OTP emails may not send")
    except Exception as e:
        logger.error(f"❌ Background Email Queue initialization error: {e}")
        logger.warning("⚠️ Continuing startup without email queue")
    
    # AUTOMATIC DATABASE INITIALIZATION: Create all tables that don't exist
    try:
        from database import test_connection, create_tables
        logger.info("🗄️ Initializing database - creating tables automatically...")
        
        # Test database connection first
        if not test_connection():
            raise ConnectionError("Database connection test failed")
            
        # Create all missing tables automatically
        tables_created = create_tables()
        if tables_created:
            logger.info("✅ Database initialization complete - all tables verified/created")
        else:
            logger.error("❌ Database table creation failed")
            raise RuntimeError("Database table creation failed")
            
    except Exception as e:
        logger.error(f"❌ Database initialization failed: {e}")
        logger.error("🚨 CRITICAL: Database initialization failed - bot may not function properly")
        # Continue startup anyway since some components might still work
    
    # Validate critical configuration before creating application
    Config.validate_retry_system_configuration()
    Config.validate_manual_refunds_configuration()
    Config.validate_webhook_urls()
    
    # Create application with hardened HTTPXRequest for Replit environment
    from telegram.request import HTTPXRequest
    
    # Validate BOT_TOKEN before using it
    if not Config.BOT_TOKEN:
        raise ValueError("BOT_TOKEN environment variable is required but not set")
    
    # Hardened HTTP client to handle Replit network connectivity issues
    request = HTTPXRequest(
        connect_timeout=30.0,    # Extended timeout for slow connections
        read_timeout=30.0,       # Extended timeout for slow responses  
        write_timeout=30.0,      # Extended timeout for uploads
        connection_pool_size=32  # Maximum connection pool size
    )
    
    application = Application.builder().token(Config.BOT_TOKEN).request(request).build()
    # Application stored in clean startup manager
    
    logger.info("🔧 Application created for webhook mode - about to register emergency handlers")
    
    # ✅ CRITICAL FIX: Register emergency handlers for button responsiveness
    register_emergency_handlers(application)
    logger.info("✅ Emergency handlers registered successfully")
    
    # ✅ Register update interceptor for maintenance mode and comprehensive logging
    from utils.update_interceptor import register_update_interceptor
    register_update_interceptor(application)
    logger.info("✅ Update interceptor registered - maintenance mode and audit logging active")

    # GROUP CHAT GUARD: Ignore all messages/commands in groups/supergroups
    # Allow event triggers (my_chat_member, chat_member) to pass through.
    from telegram.ext import MessageHandler as _MH
    from telegram.ext.filters import ALL as _ALL

    async def _group_chat_guard(update, context):
        from telegram.ext import ApplicationHandlerStop as _Stop
        if update.effective_chat and update.effective_chat.type in ("group", "supergroup"):
            if update.my_chat_member or update.chat_member:
                return
            raise _Stop

    application.add_handler(_MH(_ALL, _group_chat_guard), group=-99)
    logger.info("✅ Group chat guard registered at priority -99")
    
    monitor.log_stage("Application_created")
    
    # Setup critical infrastructure (database, services, etc.) but not handlers
    logger.info("🚀 Setting up critical infrastructure...")
    from utils.startup_optimizer import StartupOptimizer
    from utils.background_operations import CriticalOperationsManager
    
    StartupOptimizer.enable_lazy_imports()
    StartupOptimizer.optimize_startup_performance()
    
    # Setup critical operations
    critical_success = await CriticalOperationsManager.setup_critical_infrastructure(application)
    if not critical_success:
        raise RuntimeError("Critical infrastructure setup failed")
    
    logger.info("✅ Critical infrastructure ready")
    
    # Register critical handlers for webhook mode
    logger.info("Registering all handlers for webhook mode")
    
    # CRITICAL FIX: Call the register_handlers_directly function
    await register_handlers_directly(application)
    
    # Import required handlers
    from telegram.ext import CallbackQueryHandler, MessageHandler, filters
    
    # CRITICAL FIX: Register missing hamburger menu handler with blocking check
    from handlers.menu import show_hamburger_menu, show_partner_program
    from utils.conversation_protection import create_blocking_aware_handler
    
    blocked_hamburger = create_blocking_aware_handler(show_hamburger_menu)
    blocked_partner = create_blocking_aware_handler(show_partner_program)
    
    application.add_handler(CallbackQueryHandler(blocked_hamburger, pattern='^hamburger_menu$'), group=1)
    logger.info("✅ HAMBURGER_MENU: Settings & Help button handler registered (blocking-aware)")
    application.add_handler(CallbackQueryHandler(blocked_partner, pattern='^partner_program$'), group=1)
    logger.info("✅ PARTNER_PROGRAM: Whitelabel info handler registered (blocking-aware)")
    
    # Register unified callback dispatcher (replaces scattered callback handlers)
    from utils.callback_dispatcher import initialize_callback_system
    unified_callback_handler = initialize_callback_system()
    application.add_handler(unified_callback_handler, group=0)
    logger.info("✅ UNIFIED_CALLBACKS: Consolidated callback dispatcher registered (replaces scattered handlers)")
    
    # CRITICAL FIX: Register missing handlers directly here
    from handlers.messages_hub import show_active_trades
    from handlers.start import handle_start_email_input, handle_invitation_decide_later, onboarding_conversation
    from handlers.support_chat import create_support_conversation_handler, view_support_tickets
    from handlers.admin_support import (
        admin_support_dashboard, admin_assign_ticket, admin_support_chat,
        admin_unassigned_tickets, admin_my_tickets, admin_reply_ticket,
        admin_resolve_ticket, admin_close_ticket
    )
    from telegram.ext import CallbackQueryHandler
    
    # REMOVED: Duplicate show_active_trades handler - now handled by emergency_handlers registration above
    # REMOVED: Duplicate start_email_input handler - now handled by onboarding conversation
    
    # CRITICAL FIX: Register missing show_help_onboarding handler with blocking
    from handlers.start import show_help_from_onboarding_callback, start_handler, navigate_to_dashboard
    from utils.conversation_protection import create_blocking_aware_handler
    
    blocked_help = create_blocking_aware_handler(show_help_from_onboarding_callback)
    application.add_handler(CallbackQueryHandler(blocked_help, pattern='^show_help_onboarding$'), group=0)
    
    # GLOBAL FIX: Register continue_to_dashboard globally with blocking
    async def global_continue_to_dashboard(update, context):
        """Global handler for continue_to_dashboard callback - works outside onboarding flow"""
        return await navigate_to_dashboard(update, context, source="quick_guide")
    blocked_dashboard = create_blocking_aware_handler(global_continue_to_dashboard)
    application.add_handler(CallbackQueryHandler(blocked_dashboard, pattern='^continue_to_dashboard$'), group=0)
    logger.info("✅ GLOBAL_FIX: continue_to_dashboard handler registered for all users (blocking-aware)")
    
    # CRITICAL FIX: Register cancel_email_setup with blocking
    blocked_cancel = create_blocking_aware_handler(start_handler)
    application.add_handler(CallbackQueryHandler(blocked_cancel, pattern='^cancel_email_setup$'), group=0)
    
    # CRITICAL FIX: Register view_pending_invitations with blocking
    from handlers.start import handle_view_pending_invitations, handle_view_individual_invitation
    blocked_invites = create_blocking_aware_handler(handle_view_pending_invitations)
    blocked_invite_view = create_blocking_aware_handler(handle_view_individual_invitation)
    application.add_handler(CallbackQueryHandler(blocked_invites, pattern='^view_pending_invitations$'), group=0)
    application.add_handler(CallbackQueryHandler(blocked_invite_view, pattern='^view_invitation:'), group=0)
    logger.info("✅ CRITICAL FIX: View pending invitations handlers registered (blocking-aware)")
    
    # CRITICAL FIX: Register payment handlers with HIGHEST PRIORITY and blocking
    from handlers.escrow import handle_make_payment
    blocked_payment = create_blocking_aware_handler(handle_make_payment)
    application.add_handler(CallbackQueryHandler(blocked_payment, pattern='^make_payment_'), group=-10)
    application.add_handler(CallbackQueryHandler(blocked_payment, pattern='^pay_escrow_'), group=-10)
    application.add_handler(CallbackQueryHandler(blocked_payment, pattern='^pay_escrow:'), group=-10)
    logger.info("✅ CRITICAL FIX: ALL Pay now button patterns registered (blocking-aware)")
    
    # DISABLED: Old onboarding conversation handler replaced by stateless onboarding router
    # application.add_handler(onboarding_conversation, group=0)
    
    # Register support chat conversation handler
    support_conversation = create_support_conversation_handler()
    application.add_handler(support_conversation, group=1)
    
    # CRITICAL FIX: Register rating conversation handler for user feedback system
    from handlers.user_rating import create_rating_conversation_handler
    rating_conversation = create_rating_conversation_handler()
    application.add_handler(rating_conversation, group=1)
    logger.info("✅ Rating ConversationHandler registered for user feedback system")
    
    # Register support callback handlers (removed duplicate start_support_chat - already in ConversationHandler)
    application.add_handler(CallbackQueryHandler(view_support_tickets, pattern='^view_support_tickets$'), group=0)
    
    # CRITICAL FIX: Register missing support_chat_open handler for "Continue Chat" button
    from handlers.support_chat import open_support_chat, user_support_close_ticket
    application.add_handler(CallbackQueryHandler(open_support_chat, pattern='^support_chat_open:'), group=0)
    
    # CRITICAL FIX: Register close ticket handler for both inside AND outside ConversationHandler
    # This allows users to close tickets from ticket details view (outside conversation)
    # AND from within active chat (ConversationHandler fallback also handles this)
    application.add_handler(CallbackQueryHandler(user_support_close_ticket, pattern='^support_close_ticket:'), group=0)
    
    # CRITICAL FIX: Register admin support handlers at HIGHEST priority to prevent lazy loading conflicts
    application.add_handler(CallbackQueryHandler(admin_support_dashboard, pattern='^admin_support_dashboard$'), group=-1)
    application.add_handler(CallbackQueryHandler(admin_assign_ticket, pattern='^admin_assign_ticket:'), group=-1)
    application.add_handler(CallbackQueryHandler(admin_support_chat, pattern='^admin_support_chat:'), group=-1)
    application.add_handler(CallbackQueryHandler(admin_reply_ticket, pattern='^admin_reply_ticket:'), group=-1)
    application.add_handler(CallbackQueryHandler(admin_resolve_ticket, pattern='^admin_resolve_ticket:'), group=-1)
    application.add_handler(CallbackQueryHandler(admin_close_ticket, pattern='^admin_close_ticket:'), group=-1)
    application.add_handler(CallbackQueryHandler(admin_unassigned_tickets, pattern='^admin_unassigned_tickets$'), group=0)
    application.add_handler(CallbackQueryHandler(admin_my_tickets, pattern='^admin_my_tickets$'), group=0)
    
    # CRITICAL FIX: Register admin failures handlers for transaction management
    from handlers.admin_failures import admin_failures_handler
    application.add_handler(CallbackQueryHandler(admin_failures_handler.show_failures_dashboard, pattern='^admin_failures_dashboard$'), group=-1)
    application.add_handler(CallbackQueryHandler(admin_failures_handler.show_failures_list, pattern='^admin_failures_list:.*$'), group=-1)
    application.add_handler(CallbackQueryHandler(admin_failures_handler.show_failures_list, pattern='^admin_failures_priority$'), group=-1)
    application.add_handler(CallbackQueryHandler(admin_failures_handler.show_failure_detail, pattern='^admin_failure_detail:.*$'), group=-1)
    application.add_handler(CallbackQueryHandler(admin_failures_handler.handle_failure_action, pattern='^admin_failure_action:.*$'), group=-1)
    application.add_handler(CallbackQueryHandler(admin_failures_handler.confirm_failure_action, pattern='^admin_failure_confirm:.*$'), group=-1)
    application.add_handler(CallbackQueryHandler(admin_failures_handler.send_failure_email_alert, pattern='^admin_failure_email:.*$'), group=-1)
    application.add_handler(CallbackQueryHandler(admin_failures_handler.show_failures_stats, pattern='^admin_failures_stats$'), group=-1)
    logger.info("✅ Admin failures handlers registered for transaction management")
    
    logger.warning("✅ CRITICAL FIX: view_active_trades, start_email_input, onboarding conversation, and support chat handlers registered directly in webhook mode!")
    
    # URGENT FIX: Add emergency /cancel command to help users escape stuck cashout states
    async def emergency_cancel_command(update, context):
        """Emergency command to clear stuck cashout states and return to main menu"""
        try:
            user_id = update.effective_user.id
            
            # Use unified cleanup function for consistent state clearing
            from utils.conversation_cleanup import clear_user_conversation_state
            cleanup_success = await clear_user_conversation_state(
                user_id=user_id,
                context=context,
                trigger="cancel_command"
            )
            
            if cleanup_success:
                # Send confirmation - user can use /start to return to main menu
                await update.message.reply_text(
                    "✅ **Session Reset Complete**\n\n"
                    "All conversation data has been cleared. You can now use the bot normally.\n\n"
                    "Use /start to return to the main menu.",
                    parse_mode="Markdown"
                )
                logger.info(f"✅ EMERGENCY_CANCEL: User {user_id} successfully reset session state")
            else:
                await update.message.reply_text(
                    "⚠️ **Partial Reset**\n\n"
                    "Some data was cleared, but there may be residual state. "
                    "Please use /start to return to the main menu.",
                    parse_mode="Markdown"
                )
                logger.warning(f"⚠️ EMERGENCY_CANCEL: Partial cleanup for user {user_id}")
            
        except Exception as e:
            logger.error(f"❌ EMERGENCY_CANCEL: Failed for user {update.effective_user.id}: {e}")
            await update.message.reply_text(
                "❌ Reset failed. Please contact support or restart the bot with /start"
            )
    
    # Register the emergency cancel command with blocking check
    from telegram.ext import CommandHandler
    from utils.conversation_protection import create_blocking_aware_command_handler
    
    blocked_cancel = create_blocking_aware_command_handler(emergency_cancel_command)
    application.add_handler(CommandHandler("cancel", blocked_cancel), group=0)
    logger.info("✅ EMERGENCY: /cancel command registered with blocking check")
    
    # Register /start command handler with blocking check
    from handlers.start import start_handler
    blocked_start = create_blocking_aware_command_handler(start_handler)
    application.add_handler(CommandHandler("start", blocked_start), group=0)
    logger.info("✅ STARTUP: /start command registered with blocking check")
    
    # Register admin commands with blocking check
    from handlers.admin import admin_command, handle_broadcast_command
    from utils.admin_telemetry_viewer import view_telemetry_stats
    blocked_admin = create_blocking_aware_command_handler(admin_command)
    blocked_broadcast = create_blocking_aware_command_handler(handle_broadcast_command)
    blocked_telemetry = create_blocking_aware_command_handler(view_telemetry_stats)
    application.add_handler(CommandHandler("admin", blocked_admin), group=0)
    application.add_handler(CommandHandler("broadcast", blocked_broadcast), group=0)
    application.add_handler(CommandHandler("telemetry", blocked_telemetry), group=0)
    logger.info("✅ ADMIN: /admin, /broadcast, and /telemetry commands registered with blocking check")
    
    # Register menu/wallet/escrow/profile/help/orders/settings/support command handlers with blocking check
    from handlers.commands import (
        menu_command, wallet_command, escrow_command, profile_command, help_command,
        orders_command, settings_command, support_command
    )
    blocked_menu = create_blocking_aware_command_handler(menu_command)
    blocked_wallet = create_blocking_aware_command_handler(wallet_command)
    blocked_escrow = create_blocking_aware_command_handler(escrow_command)
    blocked_profile = create_blocking_aware_command_handler(profile_command)
    blocked_help = create_blocking_aware_command_handler(help_command)
    blocked_orders = create_blocking_aware_command_handler(orders_command)
    blocked_settings = create_blocking_aware_command_handler(settings_command)
    blocked_support = create_blocking_aware_command_handler(support_command)
    application.add_handler(CommandHandler("menu", blocked_menu), group=0)
    application.add_handler(CommandHandler("wallet", blocked_wallet), group=0)
    application.add_handler(CommandHandler("escrow", blocked_escrow), group=0)
    application.add_handler(CommandHandler("profile", blocked_profile), group=0)
    application.add_handler(CommandHandler("help", blocked_help), group=0)
    application.add_handler(CommandHandler("orders", blocked_orders), group=0)
    application.add_handler(CommandHandler("settings", blocked_settings), group=0)
    application.add_handler(CommandHandler("support", blocked_support), group=0)
    logger.info("✅ MENU_COMMANDS: /menu, /wallet, /escrow, /profile, /help, /orders, /settings, /support registered with blocking check")
    
    # Register ALL other critical handlers (condensed from polling mode)
    from handlers.wallet_direct import (
        show_crypto_funding_options, start_add_funds, handle_bank_selection, handle_deposit_currency_selection,
        show_deposit_qr, handle_save_bank_account, handle_cancel_bank_save, handle_add_new_bank,
        show_saved_bank_accounts_management, show_saved_crypto_addresses_management,
        show_comprehensive_transaction_history, handle_back_to_main, handle_ngn_bank_account_input,
        handle_wallet_menu, handle_wallet_cashout, handle_auto_cashout_bank_selection,
        handle_auto_cashout_crypto_selection, handle_toggle_auto_cashout,
        handle_set_auto_cashout_bank, handle_set_auto_cashout_crypto,
        handle_confirm_unverified_cashout
    )
    from handlers.fincra_payment import FincraPaymentHandler
    from handlers.commands import profile_command, show_account_settings, show_cashout_settings, show_notification_settings
    from handlers.start import show_help_from_onboarding_callback, handle_demo_exchange, handle_demo_escrow
    from handlers.missing_handlers import (
        handle_main_menu_callback, handle_my_escrows, handle_menu_escrows, 
        handle_wal_history, handle_withdrawal_history, handle_exchange_crypto, handle_complete_trading,
        handle_quick_rating_access, handle_settings_verify_email, handle_start_email_verification
    )
    from handlers.ux_improvements import handle_contact_support
    from handlers.messages_hub import show_trades_messages_hub, handle_start_dispute, handle_dispute_trade
    from handlers.escrow import (
        start_secure_trade, handle_escrow_crypto_selection, handle_payment_method_selection,
        handle_release_funds, handle_cancel_release_funds, handle_confirm_release_funds, handle_mark_delivered
    )
    from handlers.referral import handle_invite_friends, handle_referral_stats, handle_referral_leaderboard
    from services.fee_transparency import FeeTransparencyService
    from handlers.contact_management import ContactManagementHandler
    from handlers.admin import (
        admin_command, handle_broadcast_command,
        handle_admin_main, handle_admin_analytics, handle_admin_disputes, handle_admin_reports,
        handle_admin_manual_ops, handle_admin_manual_cashouts, handle_admin_health
    )
    
    # Register all critical handlers
    critical_handlers = [
        (show_crypto_funding_options, '^(crypto_funding_start|crypto_funding_start_direct|crypto_funding_options)$'),
        (FincraPaymentHandler.start_wallet_funding, '^fincra_start_payment$'),
        (handle_bank_selection, '^wallet_select_bank:.*$'),
        (handle_deposit_currency_selection, '^deposit_currency:'),
        (show_deposit_qr, '^show_deposit_qr$'),
        (handle_save_bank_account, '^save_bank_account$'),
        (handle_add_new_bank, '^add_new_bank$'),
        (profile_command, '^menu_profile$'),
        (show_account_settings, '^user_settings$'),
        (show_cashout_settings, '^cashout_settings$'),
        # CRITICAL FIX: Support both wallet_history and wal_history patterns
        (show_comprehensive_transaction_history, '^wallet_history$'),
        (handle_wal_history, '^wal_history$'),
        (show_saved_bank_accounts_management, '^manage_bank_accounts$'),
        (show_saved_crypto_addresses_management, '^manage_crypto_addresses$'),
        (show_help_from_onboarding_callback, '^menu_help$'),
        (handle_main_menu_callback, '^main_menu$'),
        (handle_back_to_main, '^back_to_main$'),
        # CRITICAL FIX: Support both trades hub patterns
        (show_trades_messages_hub, '^trades_messages_hub$'),
        (start_secure_trade, '^(start_secure_trade|create_escrow)$'),
        (handle_demo_exchange, '^demo_exchange$'),
        (handle_demo_escrow, '^demo_escrow$'),
        (handle_invite_friends, '^invite_friends$'),
        (handle_referral_stats, '^referral_stats$'),
        (handle_referral_leaderboard, '^referral_leaderboard$'),
        # CRITICAL: Missing navigation handlers that were causing unresponsive buttons
        (handle_my_escrows, '^my_escrows$'),
        (handle_menu_escrows, '^menu_escrows$'),
        (handle_withdrawal_history, '^withdrawal_history$'),
        # FIX 1-3: WALLET HANDLERS - Critical wallet functionality
        (handle_wallet_menu, '^menu_wallet$'),
        (handle_deposit_currency_selection, '^wallet_deposit$'),
        (handle_wallet_cashout, '^wallet_withdraw$'),
        # FIX 4: CONTACT MENU HANDLER
        (ContactManagementHandler().contact_management_menu, '^contact_menu$'),
        # FIX 5: ADMIN HANDLERS (most critical ones)
        (handle_admin_main, '^admin_main$'),
        (handle_admin_analytics, '^admin_analytics$'),
        (handle_admin_disputes, '^admin_disputes$'),
        (handle_admin_reports, '^admin_reports$'),
        (handle_admin_manual_ops, '^admin_manual_ops$'),
        (handle_admin_manual_cashouts, '^admin_manual_cashouts$'),
        (handle_admin_health, '^admin_health$'),
        # FIX 6: ADDITIONAL CRITICAL MISSING HANDLERS
        # CRITICAL FIX: Disabled legacy escrow handlers that conflict with direct handlers
        # (handle_escrow_crypto_selection, '^crypto_(BTC|ETH|LTC|USDT|USDC|ADA|DOT|AVAX|MATIC|SOL).*$'),  # DISABLED: Causes auto-confirmation bug
        (handle_payment_method_selection, '^payment_.*$'),  # RE-ENABLED: Required for wallet button callbacks
        (handle_payment_method_selection, '^wallet_insufficient$'),  # Handle insufficient wallet button
        # Fee transparency handlers - removed non-existent methods
        # HIGH-FREQUENCY MISSING PATTERNS
        (start_add_funds, '^wallet_add_funds$'),
        (start_secure_trade, '^menu_create$'),
        (show_cashout_settings, '^cashout_settings$'),
        (show_cashout_settings, '^auto_cashout_settings$'),  # Auto cashout settings screen
        (handle_toggle_auto_cashout, '^toggle_auto_cashout$'),  # Toggle auto cashout enable/disable
        (handle_auto_cashout_bank_selection, '^auto_cashout_set_bank$'),  # Show bank selection for auto-cashout
        (handle_auto_cashout_crypto_selection, '^auto_cashout_set_crypto$'),  # Show crypto selection for auto-cashout
        (handle_set_auto_cashout_bank, '^set_auto_bank:.*$'),  # Save selected bank as auto-cashout destination
        (handle_set_auto_cashout_crypto, '^set_auto_crypto:.*$'),  # Save selected crypto as auto-cashout destination
        (handle_wallet_menu, '^wallet_menu$'),  # Alternative wallet pattern
        (show_saved_crypto_addresses_management, '^manage_crypto_addresses$'),  # Already added but ensuring coverage
        # CRITICAL FIX: Register missing Release Funds handlers
        (handle_release_funds, '^release_funds_.*$'),
        (handle_cancel_release_funds, '^cancel_release_.*$'),
        (handle_confirm_release_funds, '^confirm_release_.*$'),
        (handle_mark_delivered, '^mark_delivered_.*$'),
        # CRITICAL FIX: Register missing Dispute handlers  
        (handle_start_dispute, '^start_dispute$'),
        (handle_dispute_trade, '^dispute_trade:.*$'),
        # CRITICAL FIX: Register missing navigation handlers from trade history
        (handle_exchange_crypto, '^exchange_crypto$'),
        (handle_complete_trading, '^complete_trading$'),
        (handle_quick_rating_access, '^quick_rating_access$'),
        # EMAIL VERIFICATION HANDLERS - Optional verification system
        (handle_settings_verify_email, '^settings_verify_email$'),
        (handle_start_email_verification, '^start_email_verification$'),
        # UNVERIFIED CASHOUT HANDLER - Conditional OTP system
        (handle_confirm_unverified_cashout, '^confirm_unverified_cashout_.*$'),
    ]
    
    # Debug logging for handler registration
    logger.info(f"🎯 Starting registration of {len(critical_handlers)} critical handlers...")
    
    # Apply blocking check to all critical handlers
    from utils.conversation_protection import create_blocking_aware_handler
    
    registered_count = 0
    for handler_func, pattern in critical_handlers:
        try:
            # Wrap handler with blocking check
            blocked_aware_func = create_blocking_aware_handler(handler_func)
            handler = CallbackQueryHandler(blocked_aware_func, pattern=pattern)
            application.add_handler(handler, group=0)
            registered_count += 1
            # Use INFO level so we can see it in logs
            logger.info(f"✅ Registered handler {registered_count}/{len(critical_handlers)}: {handler_func.__name__} with pattern {pattern} (blocking-aware)")
        except Exception as e:
            logger.error(f"❌ Failed to register handler {handler_func.__name__}: {e}")
    
    logger.info(f"🎯 Successfully registered {registered_count}/{len(critical_handlers)} critical handlers")
    
    # Specifically log our problem handlers
    logger.info(f"📍 Key handlers registered: handle_my_escrows={handle_my_escrows}, handle_menu_escrows={handle_menu_escrows}, handle_wal_history={handle_wal_history}")
    
    # UNIFIED TEXT ROUTING: Replace conflicting handlers with single unified router
    logger.info("🎯 CRITICAL ARCHITECTURAL FIX: Registering Unified Text Router to eliminate handler conflicts")
    
    # Bank account input handler (specific pattern - keep for specific number inputs) with blocking
    from utils.conversation_protection import create_blocking_aware_handler
    blocked_bank_input = create_blocking_aware_handler(handle_ngn_bank_account_input)
    application.add_handler(
        MessageHandler(filters.Regex(r"^\d{10}$") & ~filters.COMMAND, blocked_bank_input),
        group=0
    )
    
    # UNIFIED TEXT ROUTER: Single handler that routes based on user state
    from handlers.text_router import create_unified_text_handler
    unified_text_handler = create_unified_text_handler()
    application.add_handler(unified_text_handler, group=0)  # HIGHEST priority for all text messages - prevents handler conflicts
    logger.info("✅ UNIFIED TEXT ROUTER: Registered with group=0 (HIGHEST PRIORITY) to prevent duplicate message handling")
    
    # REMOVED: Conflicting individual text handlers (handle_support_message_input, handle_message_input)
    # These are now routed through the unified system based on user state
    
    # CRITICAL FIX: Register OTP verification handler with UnifiedTextRouter
    logger.info("🔐 Registering OTP verification handler with UnifiedTextRouter...")
    try:
        from utils.unified_text_router import unified_text_router
        from handlers.otp_verification import handle_otp_verification
        unified_text_router.register_conversation_handler("otp_verification", handle_otp_verification)
        logger.info("✅ OTP verification handler registered with UnifiedTextRouter")
    except Exception as e:
        logger.error(f"❌ Failed to register OTP verification handler: {e}")
    
    logger.info("All critical handlers registered successfully")
    monitor.log_stage("Critical_setup_complete")
    
    # Note: DIRECT_WALLET_HANDLERS are already registered in background_operations.py
    # to avoid duplication. Additional handlers that aren't in DIRECT_WALLET_HANDLERS
    # are registered below.
    
    # Register additional handlers not covered by DIRECT_WALLET_HANDLERS
    from handlers.exchange_handler import ExchangeHandler
    application.add_handler(CallbackQueryHandler(ExchangeHandler.start_exchange, pattern='^start_exchange$'), group=0)
    application.add_handler(CallbackQueryHandler(ExchangeHandler.handle_bank_switch_selection, pattern='^exchange_bank_switch_'), group=0)
    application.add_handler(CallbackQueryHandler(ExchangeHandler.handle_pre_confirmation_bank_switch, pattern='^exchange_bank_switch_pre$'), group=0)
    
    from handlers.wallet_direct import handle_retry_bank_verification
    application.add_handler(CallbackQueryHandler(handle_retry_bank_verification, pattern='^retry_bank_verification$'), group=0)
    
    # CRITICAL: Register ALL direct handlers BEFORE general message handler to ensure priority
    # This achieves 100% ConversationHandler elimination!
    try:
        # 1. Core Trading & Financial Handlers
        from handlers.escrow_direct import DIRECT_ESCROW_HANDLERS
        for handler in DIRECT_ESCROW_HANDLERS:
            application.add_handler(handler, group=-1)  # Higher priority group
        logger.info("✅ DIRECT HANDLERS: Escrow handlers registered")
        
        # 2. User Rating System
        from handlers.user_rating_direct import DIRECT_RATING_HANDLERS
        for handler in DIRECT_RATING_HANDLERS:
            application.add_handler(handler, group=-1)
        logger.info("✅ DIRECT HANDLERS: User rating handlers registered")
        
        # 3. Contact Management
        from handlers.contact_management_direct import DIRECT_CONTACT_HANDLERS
        for handler in DIRECT_CONTACT_HANDLERS:
            application.add_handler(handler, group=-1)
        logger.info("✅ DIRECT HANDLERS: Contact management handlers registered")
        
        # 4. Admin Functions
        from handlers.admin_cashout_direct import DIRECT_ADMIN_CASHOUT_HANDLERS
        for handler in DIRECT_ADMIN_CASHOUT_HANDLERS:
            application.add_handler(handler, group=-1)
        logger.info("✅ DIRECT HANDLERS: Admin cashout handlers registered")
        
        from handlers.admin_transactions_direct import DIRECT_ADMIN_TRANSACTION_HANDLERS
        for handler in DIRECT_ADMIN_TRANSACTION_HANDLERS:
            application.add_handler(handler, group=-1)
        logger.info("✅ DIRECT HANDLERS: Admin transaction handlers registered")
        
        from handlers.admin_broadcast_direct import DIRECT_ADMIN_BROADCAST_HANDLERS
        for handler in DIRECT_ADMIN_BROADCAST_HANDLERS:
            application.add_handler(handler, group=1)  # Higher priority than unified text router (group=0)
        logger.info("✅ DIRECT HANDLERS: Admin broadcast handlers registered (group=1 - higher priority)")
        
        from handlers.admin_rating_direct import DIRECT_ADMIN_RATING_HANDLERS
        for handler in DIRECT_ADMIN_RATING_HANDLERS:
            application.add_handler(handler, group=-1)
        logger.info("✅ DIRECT HANDLERS: Admin rating handlers registered")
        
        # Seller Profile Handlers (Rating System)
        from handlers.seller_profile import SELLER_PROFILE_HANDLERS
        for handler in SELLER_PROFILE_HANDLERS:
            application.add_handler(handler, group=-1)
        logger.info("✅ DIRECT HANDLERS: Seller profile handlers registered")
        
        # Rating UI Enhancement Handlers
        from handlers.rating_ui_enhancements import RATING_UI_HANDLERS
        for handler in RATING_UI_HANDLERS:
            application.add_handler(handler, group=-1)
        logger.info("✅ DIRECT HANDLERS: Rating UI enhancement handlers registered")
        
        from handlers.admin_comprehensive_config_direct import DIRECT_ADMIN_CONFIG_HANDLERS
        for handler in DIRECT_ADMIN_CONFIG_HANDLERS:
            application.add_handler(handler, group=-1)
        logger.info("✅ DIRECT HANDLERS: Admin config handlers registered")
        
        # Admin Maintenance Mode Control
        from handlers.admin_maintenance import register_maintenance_handlers
        register_maintenance_handlers(application)
        logger.info("✅ DIRECT HANDLERS: Admin maintenance mode handlers registered")
        
        # 6. UX Improvement Handlers (including support)
        from handlers.ux_improvements import UX_IMPROVEMENT_HANDLERS
        for handler in UX_IMPROVEMENT_HANDLERS:
            application.add_handler(handler, group=-1)
        logger.info("✅ DIRECT HANDLERS: UX improvement handlers registered")
        
        # 5. Communication Systems
        from handlers.dispute_chat_direct import DIRECT_DISPUTE_HANDLERS
        for handler in DIRECT_DISPUTE_HANDLERS:
            application.add_handler(handler, group=-1)
        logger.info("✅ DIRECT HANDLERS: Dispute resolution handlers registered")
        
        from handlers.messages_hub_direct import DIRECT_MESSAGES_HANDLERS
        for handler in DIRECT_MESSAGES_HANDLERS:
            application.add_handler(handler, group=-1)
        logger.info("✅ DIRECT HANDLERS: Messages hub handlers registered")
        
        from handlers.multi_dispute_manager_direct import DIRECT_MULTI_DISPUTE_HANDLERS
        for handler in DIRECT_MULTI_DISPUTE_HANDLERS:
            application.add_handler(handler, group=-1)
        logger.info("✅ DIRECT HANDLERS: Multi-dispute manager handlers registered")
        
        # 6. Session Management
        from handlers.session_ui_direct import DIRECT_SESSION_UI_HANDLERS
        for handler in DIRECT_SESSION_UI_HANDLERS:
            application.add_handler(handler, group=-1)
        logger.info("✅ DIRECT HANDLERS: Session UI handlers registered")
        
        # 7. Onboarding System - Using newer router system only (old direct handlers removed)
        
        logger.warning("🎉 ARCHITECTURE COMPLETE: 100% ConversationHandler elimination achieved!")
        logger.warning("✅ ALL SYSTEMS: Using direct handlers for maximum reliability and performance")
        
        # Register Fincra payment handlers including NGN amount input
        from handlers.fincra_payment import register_fincra_handlers
        register_fincra_handlers(application)
        logger.info("✅ Fincra payment handlers registered")
        
        # PHASE 2A: Register new stateless onboarding router
        logger.info("🚀 Registering stateless onboarding router...")
        try:
            from handlers.onboarding_router import register_onboarding_handlers
            register_onboarding_handlers(application)
            logger.info("✅ Stateless onboarding router registered successfully")
        except Exception as e:
            logger.error(f"❌ Failed to register onboarding router: {e}")
            # Don't raise - fallback to existing system
        
        # Register group management handlers (bot added/removed from groups)
        logger.info("🚀 Registering group management handlers...")
        try:
            from handlers.group_handler import register_group_handlers
            register_group_handlers(application)
            logger.info("✅ Group management handlers registered successfully")
        except Exception as e:
            logger.error(f"❌ Failed to register group handlers: {e}")
        
        # CRITICAL FIX: Register refund command handlers directly to ensure they're available
        logger.info("🔄 Registering refund command handlers directly...")
        try:
            from handlers.refund_command_registry import RefundCommandRegistry
            refund_success = RefundCommandRegistry.register_all_commands(application)
            if refund_success:
                logger.info("✅ RefundCommandRegistry registered all commands successfully in main.py")
            else:
                logger.error("❌ RefundCommandRegistry registration failed in main.py")
        except Exception as refund_error:
            logger.error(f"❌ Failed to register refund commands: {refund_error}")
        
        # ENHANCED: Register admin retry command handlers for comprehensive observability
        logger.info("🔄 Registering admin retry command handlers...")
        try:
            from handlers.admin_retry_command_registry import AdminRetryCommandRegistry
            retry_success = AdminRetryCommandRegistry.register_all_commands(application)
            if retry_success:
                logger.info("✅ AdminRetryCommandRegistry registered all commands successfully in main.py")
            else:
                logger.error("❌ AdminRetryCommandRegistry registration failed in main.py")
        except Exception as retry_error:
            logger.error(f"❌ Failed to register admin retry commands: {retry_error}")
        
    except Exception as e:
        logger.error(f"❌ CRITICAL: Failed to register direct handlers: {e}")
        raise
    
    logger.info("✅ Additional handlers registered successfully")
    
    monitor.log_stage("Critical_setup_complete")
    
    # Initialize lazy loading system (target: <1s)
    # Direct handler registration completed - no lazy loading needed
    logger.info("✅ All handlers registered directly - lazy loading not required")
    
    # CRITICAL FIX: Register unified text router handlers at startup to prevent race conditions
    logger.info("🔧 Registering unified text router handlers at startup...")
    try:
        from utils.unified_text_router import unified_text_router
        from handlers.wallet_text_input import handle_wallet_text_input
        
        # Register wallet text input handler for both conversation types
        unified_text_router.register_conversation_handler("wallet_input", handle_wallet_text_input)
        unified_text_router.register_conversation_handler("cashout_flow", handle_wallet_text_input)
        
        logger.info("✅ Unified text router handlers registered at startup - race condition fixed")
    except Exception as e:
        logger.error(f"❌ Failed to register text router handlers: {e}")
    
    monitor.log_stage("Direct_handlers_ready")
    
    # Setup post-init callback for remaining operations
    async def post_init(app):
        """Handle remaining setup after bot is running"""
        try:
            # CRITICAL FIX: Register webhook with Telegram immediately after startup
            try:
                logger.info("🔗 Registering webhook with Telegram...")
                webhook_url = Config.TELEGRAM_WEBHOOK_URL
                
                if webhook_url:
                    result = await app.bot.set_webhook(
                        url=webhook_url,
                        allowed_updates=["message", "callback_query", "my_chat_member"],
                        drop_pending_updates=True  # CRITICAL FIX: Drop stale pending updates to prevent 503 errors
                    )
                    
                    if result:
                        logger.info(f"✅ Webhook registered: {webhook_url}")
                        
                        # Verify webhook info
                        webhook_info = await app.bot.get_webhook_info()
                        logger.info(f"📊 Webhook: {webhook_info.pending_update_count} pending, max_connections={webhook_info.max_connections}")
                        
                        if webhook_info.last_error_message:
                            logger.warning(f"⚠️ Last webhook error: {webhook_info.last_error_message}")
                    else:
                        logger.error("❌ Webhook registration failed")
                    
            except Exception as e:
                logger.error(f"❌ Webhook registration error: {e}")
            
            # Setup bot commands menu first
            try:
                from utils.bot_commands import initialize_bot_commands
                commands_success = await initialize_bot_commands(app)
                if commands_success:
                    logger.info("✅ Bot commands menu initialized successfully")
                else:
                    logger.warning("⚠️ Bot commands menu setup failed")
            except Exception as e:
                logger.error(f"❌ Bot commands setup error: {e}")
            
            # Scheduler systems disabled per task requirements
            scheduler = None
            if True:  # Re-enabled scheduler to fix auto-cancellation bug
                from jobs.consolidated_scheduler import ConsolidatedScheduler
                scheduler = ConsolidatedScheduler(app)
                scheduler.start()
                # Scheduler disabled per simplification
                logger.warning("✅ CRITICAL: ConsolidatedScheduler started - 5 core jobs active")
                
                # Verify critical jobs are registered
                jobs = scheduler.scheduler.get_jobs()
                job_names = [job.name for job in jobs]
                logger.warning(f"🔍 ConsolidatedScheduler jobs: {job_names}")
                
                # Check core jobs are active
                core_job_ids = ['core_workflow_runner', 'core_retry_engine', 'core_reconciliation', 'core_cleanup_expiry', 'core_reporting_hourly']
                active_core_jobs = [job for job in jobs if job.id in core_job_ids]
                logger.warning(f"✅ {len(active_core_jobs)}/5 core jobs active")
            else:
                logger.info("📋 SCHEDULER_DISABLED: ConsolidatedScheduler disabled per simplified architecture requirements")
            
            # Background optimization systems will be started separately as async task
            # (Removed redundant call to avoid scope issues - background systems start via asyncio.create_task)
            
            # All handlers loaded directly - no lazy loading needed
            logger.info("✅ All handlers loaded directly")
            
        except Exception as e:
            logger.error(f"Post-init setup failed: {e}")
    
    application.post_init = post_init
    
    # Initialize application with exponential backoff for network issues
    
    # Retry initialization with exponential backoff to handle Replit connectivity issues
    import random
    import asyncio
    from telegram.error import TimedOut
    
    max_attempts = 6
    base_delay = 2.0
    
    for attempt in range(1, max_attempts + 1):
        try:
            logger.info(f"🔄 Initialization attempt {attempt}/{max_attempts}")
            await application.initialize()
            logger.info("✅ Application initialized successfully!")
            break
        except (TimedOut, Exception) as e:
            if attempt == max_attempts:
                logger.error(f"❌ Failed to initialize after {max_attempts} attempts: {e}")
                raise
            
            # Exponential backoff with jitter: 2s, 4s, 8s, 16s, 32s
            delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 1)
            logger.warning(f"⚠️ Initialization attempt {attempt} failed: {e}")
            logger.info(f"🔄 Retrying in {delay:.1f}s...")
            await asyncio.sleep(delay)
    await application.start()
    logger.info("✅ Application started successfully!")
    
    # CRITICAL FIX: Manually call post_init since it's not being called automatically
    logger.warning("🔧 CRITICAL: Manually starting post-init operations including ConsolidatedScheduler...")
    try:
        await post_init(application)
        logger.info("✅ Post-init operations completed successfully")
    except Exception as e:
        logger.error(f"❌ Post-init failed: {e}")
        import traceback
        logger.error(f"❌ Post-init traceback: {traceback.format_exc()}")
        raise
    
    monitor.log_stage("Bot_ready")
    monitor.finish_startup_monitoring()
    
    # OPTIMIZATION: Removed duplicate handler registration - already handled in main flow
    
    logger.info("🚀 Bot started in webhook mode - accepting connections")

    logger.info("🚀 Bot started in webhook mode - ready for uvicorn startup")
    
    # CRITICAL FIX: Start uvicorn IMMEDIATELY, move all monitoring to background
    import uvicorn
    from webhook_server import app, set_bot_application, initialize_webhook_systems_in_background
    
    # FAST STARTUP: Only set bot application reference (no heavy operations)
    await set_bot_application(application)
    
    # FAST STARTUP: Configure uvicorn with minimal settings for immediate binding
    config = uvicorn.Config(
        app,
        host=Config.WEBHOOK_HOST,
        port=Config.WEBHOOK_PORT,
        log_level="info",
        # Essential settings only
        workers=4,  # Use 4 workers to handle concurrent requests (reserves 1 CPU for system)
        loop="asyncio",
        http="httptools",
        access_log=False,
        # Reduced limits for faster startup
        limit_concurrency=50,
        timeout_keep_alive=30,
        server_header=False,
        date_header=True,
    )
    server = uvicorn.Server(config)
    
    # Start background systems AFTER uvicorn starts
    async def start_background_systems():
        """Start all monitoring and optimization systems in background"""
        import asyncio
        
        # Wait for uvicorn to complete binding
        await asyncio.sleep(2)
        
        logger.info("✅ BACKGROUND: Monitoring systems simplified")
        
        try:
            # CRITICAL: Initialize webhook processing system FIRST to prevent lost webhook events
            logger.info("🔧 BACKGROUND: Initializing webhook processing system...")
            from services.webhook_startup_service import webhook_startup_service
            await webhook_startup_service.initialize_webhook_system()
    
            logger.info("✅ BACKGROUND: Webhook processing system started successfully")
            
            # CRITICAL: Initialize background email queue for OTP delivery
            try:
                logger.info("🔧 BACKGROUND: Initializing background email queue...")
                from services.background_email_queue import initialize_email_queue
                email_queue_success = await initialize_email_queue()
                
                if email_queue_success:
                    logger.info("✅ BACKGROUND: Background email queue started successfully")
                else:
                    logger.error("❌ BACKGROUND: Background email queue initialization failed")
            except Exception as e:
                logger.error(f"❌ CRITICAL: Background email queue system failed to start: {e}")
            
            # Initialize webhook systems
            await initialize_webhook_systems_in_background()
            
            # OPTIMIZATION: Realtime monitoring gated behind ENABLE_DEEP_MONITORING
            if os.environ.get("ENABLE_DEEP_MONITORING", "false").lower() == "true":
                from utils.realtime_monitor import start_realtime_monitoring
                start_realtime_monitoring(application.bot)
                logger.info("✅ BACKGROUND: Real-time monitoring active (ENABLE_DEEP_MONITORING=true)")
            else:
                logger.info("✅ BACKGROUND: Real-time monitoring skipped (set ENABLE_DEEP_MONITORING=true to enable)")
            
            # Initialize unified activity monitoring
            from utils.unified_activity_monitor import unified_monitor
            from utils.update_interceptor import set_unified_monitor
            set_unified_monitor(unified_monitor)
            unified_monitor.track_system_event(
                "Bot Started",
                "Background systems initialized after uvicorn startup",
                {"startup_complete": True, "timestamp": time.time()}
            )
            logger.info("✅ BACKGROUND: Unified activity monitoring ready")
            
            # Start auto-release system for delivery deadlines and auto-release processing
            try:
                from services.auto_release_task_runner import start_auto_release_background_task
                await start_auto_release_background_task()
                logger.info("✅ BACKGROUND: Auto-release system started - delivery warnings and auto-release processing active")
            except Exception as e:
                logger.error(f"❌ BACKGROUND: Auto-release system startup failed: {e}")
                # This is critical functionality, but don't crash the bot
                logger.warning("⚠️ BACKGROUND: Auto-release system is not running - manual intervention may be required")
            
            logger.info("✅ BACKGROUND: Simplified systems ready")
            
            # CRITICAL FIX: Run bot commands migration for existing onboarded users
            try:
                logger.info("🔧 MIGRATION: Running commands migration for existing onboarded users...")
                await run_commands_migration(application)
            except Exception as e:
                logger.error(f"⚠️ MIGRATION: Commands migration failed: {e}")
                # Don't crash the bot - migration failure is not critical
            
        except Exception as e:
            logger.error(f"⚠️ BACKGROUND: Non-critical monitoring system error: {e}")
            # Don't fail - these are optimization features
    
    # Schedule background systems to start after uvicorn
    import asyncio
    asyncio.create_task(start_background_systems())
    
    # CRITICAL: Start uvicorn server immediately
    logger.info(f"🚀 FAST_STARTUP: Starting uvicorn server on {Config.WEBHOOK_HOST}:{Config.WEBHOOK_PORT}")
    logger.info(f"⚡ FAST_STARTUP: Starting with minimal config for immediate binding")
    
    try:
        # Try to bind and start uvicorn with timeout handling
        import signal
        import asyncio
        
        # RESILIENCE FIX: Remove artificial timeout and implement proper binding with retries
        async def uvicorn_with_resilience():
            logger.info("🚀 RESILIENT_STARTUP: Starting uvicorn with resilience patterns...")
            max_attempts = 3
            base_delay = 2.0
            
            for attempt in range(1, max_attempts + 1):
                try:
                    logger.info(f"🔄 BINDING_ATTEMPT_{attempt}: Starting server.serve()...")
                    await server.serve()
                    logger.info("✅ SUCCESS: server.serve() completed successfully!")
                    return True
                    
                except Exception as e:
                    if attempt < max_attempts:
                        delay = base_delay * (2 ** (attempt - 1))
                        logger.warning(f"⚠️ BINDING_RETRY_{attempt}: Server bind failed: {e}")
                        logger.info(f"🔄 RETRY_DELAY: Waiting {delay}s before retry {attempt + 1}/{max_attempts}")
                        await asyncio.sleep(delay)
                        continue
                    else:
                        logger.error(f"❌ BINDING_FAILED: All {max_attempts} attempts failed: {e}")
                        raise
            
            return False
        
        # Run with resilience (no artificial timeout)
        await uvicorn_with_resilience()
        logger.info("🎯 RESILIENT_SUCCESS: Uvicorn server started with resilience!")
        
    except Exception as server_error:
        logger.error(f"❌ RESILIENT_BINDING_ERROR: Server startup failed: {server_error}")
        import traceback
        logger.error(f"Full traceback: {traceback.format_exc()}")
        
        # Implement graceful fallback and recovery
        # Note: FastAPI app doesn't have a stop() method
        # Bot application cleanup handled elsewhere if needed
        logger.info("⚠️ CLEANUP: Server startup failed, exiting")
        
        # Enhanced error recovery with port check
        logger.info("🔍 RECOVERY: Checking port availability and system state...")
        
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            result = sock.connect_ex((Config.WEBHOOK_HOST, Config.WEBHOOK_PORT))
            sock.close()
            
            if result == 0:
                logger.warning(f"⚠️ PORT_CONFLICT: Port {Config.WEBHOOK_PORT} is already in use")
            else:
                logger.info(f"✅ PORT_AVAILABLE: Port {Config.WEBHOOK_PORT} is available")
        except Exception as port_check_error:
            logger.warning(f"⚠️ PORT_CHECK_FAILED: {port_check_error}")
        
        # Implement circuit breaker pattern for server startup
        logger.error("🚨 CIRCUIT_BREAKER: Server startup failed - activating fallback mode")
        logger.error("💡 RECOVERY_OPTIONS: 1) Check network connectivity 2) Verify port availability 3) Check system resources")
        
        # Clean exit with proper error code
        import sys
        logger.error("🔄 RESILIENT_EXIT: Exiting with error recovery information")
        sys.exit(1)


async def run_polling_optimized(monitor):
    """Run bot in optimized polling mode"""
    # Clean application management without globals
    
    # Validate critical configuration before creating application
    Config.validate_retry_system_configuration()
    Config.validate_manual_refunds_configuration()
    Config.validate_webhook_urls()
    
    # Create application with hardened HTTPXRequest for Replit environment
    from telegram.request import HTTPXRequest
    
    # Validate BOT_TOKEN before using it
    if not Config.BOT_TOKEN:
        raise ValueError("BOT_TOKEN environment variable is required but not set")
    
    # Hardened HTTP client to handle Replit network connectivity issues
    request = HTTPXRequest(
        connect_timeout=30.0,    # Extended timeout for slow connections
        read_timeout=30.0,       # Extended timeout for slow responses  
        write_timeout=30.0,      # Extended timeout for uploads
        connection_pool_size=32  # Maximum connection pool size
    )
    
    application = Application.builder().token(Config.BOT_TOKEN).request(request).build()
    # Application stored in clean startup manager
    
    monitor.log_stage("Application_created")
    
    # Setup only critical infrastructure
    await setup_critical_only(application)
    monitor.log_stage("Critical_setup_complete")
    
    # Note: Lazy loading system removed - all handlers loaded directly
    monitor.log_stage("Lazy_loading_ready")
    
    # Setup bot commands menu
    try:
        from utils.bot_commands import initialize_bot_commands
        commands_success = await initialize_bot_commands(application)
        if commands_success:
            logger.info("✅ Bot commands menu initialized successfully")
        else:
            logger.warning("⚠️ Bot commands menu setup failed")
    except Exception as e:
        logger.error(f"❌ Bot commands setup error: {e}")
    
    monitor.log_stage("Bot_ready")
    monitor.finish_startup_monitoring()
    
    logger.info("🚀 Bot ready for polling - startup optimizations complete")
    
    # Start polling with retry logic for network issues
    # Note: run_polling() is blocking, so background tasks must be started by the Application itself
    import random
    from telegram.error import TimedOut
    
    max_attempts = 6
    base_delay = 2.0
    
    # Initialize background systems before starting polling
    async def init_background():
        await start_background_systems()
        
        # Start scheduler
        try:
            from jobs.consolidated_scheduler import ConsolidatedScheduler
            scheduler = ConsolidatedScheduler(application)
            scheduler.start()
            logger.info("✅ Consolidated Scheduler started")
        except Exception as e:
            logger.error(f"Scheduler startup failed: {e}")
    
    # Run background initialization
    asyncio.create_task(init_background())
    
    for attempt in range(1, max_attempts + 1):
        try:
            logger.info(f"🔄 Polling startup attempt {attempt}/{max_attempts}")
            # Note: run_polling() is blocking and will not return until stopped
            application.run_polling(allowed_updates=Update.ALL_TYPES)
            break  # If successful, exit the loop
        except (TimedOut, Exception) as e:
            if attempt == max_attempts:
                logger.error(f"❌ Failed to start polling after {max_attempts} attempts: {e}")
                raise
            
            # Exponential backoff with jitter: 2s, 4s, 8s, 16s, 32s
            delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 1)
            logger.warning(f"⚠️ Polling attempt {attempt} failed: {e}")
            logger.info(f"🔄 Retrying in {delay:.1f}s...")
            await asyncio.sleep(delay)


def main():
    """Main entry point for the bot"""
    # Force webhook mode for production
    import os
    os.environ["USE_WEBHOOK"] = "true"
    
    logger.info("🚀 MAIN ENTRY POINT: Starting bot with clean startup sequence...")
    
    # Call the actual main function
    return main_new_debug()

if __name__ == "__main__":
    main()
