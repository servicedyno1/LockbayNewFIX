"""
Bridge server for Emergent platform.
Bootstraps the LockBay Telegram bot and exposes the webhook FastAPI app on port 8001.
Supervisor runs: uvicorn server:app --host 0.0.0.0 --port 8001
"""

import sys
import os
import logging
import asyncio

# Ensure the project root is on the Python path
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _project_root)
os.chdir(_project_root)

# Load .env from BOTH backend dir and project root (backend first, root overrides)
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))
load_dotenv(os.path.join(_project_root, '.env'), override=True)

# Force webhook mode and port
os.environ["USE_WEBHOOK"] = "true"
os.environ["WEBHOOK_PORT"] = "8001"
os.environ["WEBHOOK_HOST"] = "0.0.0.0"

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('telegram.request').setLevel(logging.WARNING)
logging.getLogger('telegram.ext').setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# Import the webhook FastAPI app from the project root
from webhook_server import app, set_bot_application

# Track if bot has been initialized
_bot_initialized = False

async def initialize_bot():
    """Initialize the Telegram bot application and register all handlers."""
    global _bot_initialized
    if _bot_initialized:
        return

    logger.info("Initializing LockBay Telegram bot...")

    from config import Config
    from database import test_connection, create_tables

    # Test and initialize database
    logger.info("Testing database connection...")
    if test_connection():
        logger.info("Database connection OK")
        create_tables()
    else:
        logger.error("Database connection FAILED - bot may not function properly")

    # Validate configuration
    try:
        Config.validate_retry_system_configuration()
        Config.validate_manual_refunds_configuration()
        Config.validate_webhook_urls()
    except Exception as e:
        logger.warning(f"Config validation warning: {e}")

    # Initialize State Manager
    try:
        from services.state_manager import initialize_state_manager
        await initialize_state_manager()
        logger.info("State Manager initialized")
    except Exception as e:
        logger.warning(f"State Manager init failed: {e}")

    # Initialize Background Email Queue
    try:
        from main_startup_integration import initialize_background_email_system
        await initialize_background_email_system()
        logger.info("Background Email Queue initialized")
    except Exception as e:
        logger.warning(f"Email queue init failed: {e}")

    # Create Telegram bot application
    from telegram.request import HTTPXRequest

    if not Config.BOT_TOKEN:
        raise ValueError("BOT_TOKEN not set")

    request = HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=30.0,
        write_timeout=30.0,
        connection_pool_size=32
    )

    from telegram.ext import Application
    application = Application.builder().token(Config.BOT_TOKEN).request(request).build()
    logger.info("Telegram application created")

    # Register all handlers (from main.py logic)
    from main import register_emergency_handlers, register_handlers_directly
    register_emergency_handlers(application)
    await register_handlers_directly(application)
    logger.info("Emergency + direct handlers registered")

    # Register critical handlers (condensed version)
    try:
        _register_all_critical_handlers(application)
    except Exception as e:
        logger.error(f"Critical handler registration failed: {e}")

    # Initialize the application
    import random
    from telegram.error import TimedOut

    for attempt in range(1, 4):
        try:
            logger.info(f"Initializing Telegram application (attempt {attempt}/3)...")
            await application.initialize()
            logger.info("Telegram application initialized")
            break
        except (TimedOut, Exception) as e:
            if attempt == 3:
                logger.error(f"Failed to initialize after 3 attempts: {e}")
                raise
            delay = 2 * (2 ** (attempt - 1)) + random.uniform(0, 1)
            logger.warning(f"Attempt {attempt} failed: {e}, retrying in {delay:.1f}s...")
            await asyncio.sleep(delay)

    await application.start()
    logger.info("Telegram application started")

    # Connect bot to webhook server
    await set_bot_application(application)
    logger.info("Bot application connected to webhook server")

    # Register webhook with Telegram
    try:
        webhook_url = Config.TELEGRAM_WEBHOOK_URL
        if webhook_url:
            result = await application.bot.set_webhook(
                url=webhook_url,
                allowed_updates=["message", "callback_query", "my_chat_member"],
                drop_pending_updates=True
            )
            if result:
                logger.info(f"Webhook registered: {webhook_url}")
                info = await application.bot.get_webhook_info()
                logger.info(f"Webhook info: pending={info.pending_update_count}")
            else:
                logger.error("Webhook registration failed")
        else:
            logger.warning("No TELEGRAM_WEBHOOK_URL configured")
    except Exception as e:
        logger.error(f"Webhook registration error: {e}")

    # Setup bot commands
    try:
        from utils.bot_commands import initialize_bot_commands
        await initialize_bot_commands(application)
        logger.info("Bot commands initialized")
    except Exception as e:
        logger.warning(f"Bot commands setup failed: {e}")

    # Start consolidated scheduler
    try:
        from jobs.consolidated_scheduler import ConsolidatedScheduler
        scheduler = ConsolidatedScheduler(application)
        scheduler.start()
        logger.info("ConsolidatedScheduler started")
    except Exception as e:
        logger.warning(f"Scheduler start failed: {e}")

    # Start background systems
    asyncio.create_task(_start_background_systems(application))

    _bot_initialized = True
    logger.info("LockBay bot fully initialized and ready!")


def _register_all_critical_handlers(application):
    """Register all critical callback/command handlers."""
    from telegram.ext import CallbackQueryHandler, CommandHandler, MessageHandler, filters
    from utils.conversation_protection import create_blocking_aware_handler, create_blocking_aware_command_handler

    # Menu and navigation
    from handlers.menu import show_hamburger_menu, show_partner_program
    application.add_handler(CallbackQueryHandler(create_blocking_aware_handler(show_hamburger_menu), pattern='^hamburger_menu$'), group=1)
    application.add_handler(CallbackQueryHandler(create_blocking_aware_handler(show_partner_program), pattern='^partner_program$'), group=1)

    # Unified callback dispatcher
    from utils.callback_dispatcher import initialize_callback_system
    application.add_handler(initialize_callback_system(), group=0)

    # Start command
    from handlers.start import start_handler, show_help_from_onboarding_callback, navigate_to_dashboard, handle_view_pending_invitations, handle_view_individual_invitation
    application.add_handler(CommandHandler("start", create_blocking_aware_command_handler(start_handler)), group=0)
    application.add_handler(CallbackQueryHandler(create_blocking_aware_handler(show_help_from_onboarding_callback), pattern='^show_help_onboarding$'), group=0)

    async def global_continue_to_dashboard(update, context):
        return await navigate_to_dashboard(update, context, source="quick_guide")
    application.add_handler(CallbackQueryHandler(create_blocking_aware_handler(global_continue_to_dashboard), pattern='^continue_to_dashboard$'), group=0)
    application.add_handler(CallbackQueryHandler(create_blocking_aware_handler(start_handler), pattern='^cancel_email_setup$'), group=0)
    application.add_handler(CallbackQueryHandler(create_blocking_aware_handler(handle_view_pending_invitations), pattern='^view_pending_invitations$'), group=0)
    application.add_handler(CallbackQueryHandler(create_blocking_aware_handler(handle_view_individual_invitation), pattern='^view_invitation:'), group=0)

    # Payment handlers
    from handlers.escrow import handle_make_payment
    application.add_handler(CallbackQueryHandler(create_blocking_aware_handler(handle_make_payment), pattern='^make_payment_'), group=-10)
    application.add_handler(CallbackQueryHandler(create_blocking_aware_handler(handle_make_payment), pattern='^pay_escrow_'), group=-10)
    application.add_handler(CallbackQueryHandler(create_blocking_aware_handler(handle_make_payment), pattern='^pay_escrow:'), group=-10)

    # Support chat
    from handlers.support_chat import create_support_conversation_handler, view_support_tickets, open_support_chat, user_support_close_ticket
    application.add_handler(create_support_conversation_handler(), group=1)
    application.add_handler(CallbackQueryHandler(view_support_tickets, pattern='^view_support_tickets$'), group=0)
    application.add_handler(CallbackQueryHandler(open_support_chat, pattern='^support_chat_open:'), group=0)
    application.add_handler(CallbackQueryHandler(user_support_close_ticket, pattern='^support_close_ticket:'), group=0)

    # Rating system
    from handlers.user_rating import create_rating_conversation_handler
    application.add_handler(create_rating_conversation_handler(), group=1)

    # Admin support
    from handlers.admin_support import (
        admin_support_dashboard, admin_assign_ticket, admin_support_chat,
        admin_unassigned_tickets, admin_my_tickets, admin_reply_ticket,
        admin_resolve_ticket, admin_close_ticket
    )
    for handler_func, pattern in [
        (admin_support_dashboard, '^admin_support_dashboard$'),
        (admin_assign_ticket, '^admin_assign_ticket:'),
        (admin_support_chat, '^admin_support_chat:'),
        (admin_reply_ticket, '^admin_reply_ticket:'),
        (admin_resolve_ticket, '^admin_resolve_ticket:'),
        (admin_close_ticket, '^admin_close_ticket:'),
    ]:
        application.add_handler(CallbackQueryHandler(handler_func, pattern=pattern), group=-1)
    application.add_handler(CallbackQueryHandler(admin_unassigned_tickets, pattern='^admin_unassigned_tickets$'), group=0)
    application.add_handler(CallbackQueryHandler(admin_my_tickets, pattern='^admin_my_tickets$'), group=0)

    # Admin failures
    from handlers.admin_failures import admin_failures_handler
    for pattern in ['^admin_failures_dashboard$', '^admin_failures_list:.*$', '^admin_failures_priority$',
                    '^admin_failure_detail:.*$', '^admin_failure_action:.*$', '^admin_failure_confirm:.*$',
                    '^admin_failure_email:.*$', '^admin_failures_stats$']:
        method_name = 'show_failures_dashboard' if 'dashboard' in pattern else \
                     'show_failures_list' if 'list' in pattern or 'priority' in pattern else \
                     'show_failure_detail' if 'detail' in pattern else \
                     'handle_failure_action' if 'action' in pattern else \
                     'confirm_failure_action' if 'confirm' in pattern else \
                     'send_failure_email_alert' if 'email' in pattern else 'show_failures_stats'
        application.add_handler(CallbackQueryHandler(getattr(admin_failures_handler, method_name), pattern=pattern), group=-1)

    # Cancel command
    async def emergency_cancel_command(update, context):
        from utils.conversation_cleanup import clear_user_conversation_state
        cleanup_success = await clear_user_conversation_state(user_id=update.effective_user.id, context=context, trigger="cancel_command")
        if cleanup_success:
            await update.message.reply_text("Session reset. Use /start to return to main menu.", parse_mode="Markdown")
        else:
            await update.message.reply_text("Partial reset. Use /start to return to main menu.", parse_mode="Markdown")
    application.add_handler(CommandHandler("cancel", create_blocking_aware_command_handler(emergency_cancel_command)), group=0)

    # Admin commands
    from handlers.admin import admin_command, handle_broadcast_command, handle_admin_main, handle_admin_analytics, handle_admin_disputes, handle_admin_reports, handle_admin_manual_ops, handle_admin_manual_cashouts, handle_admin_health
    from utils.admin_telemetry_viewer import view_telemetry_stats
    application.add_handler(CommandHandler("admin", create_blocking_aware_command_handler(admin_command)), group=0)
    application.add_handler(CommandHandler("broadcast", create_blocking_aware_command_handler(handle_broadcast_command)), group=0)
    application.add_handler(CommandHandler("telemetry", create_blocking_aware_command_handler(view_telemetry_stats)), group=0)

    # Menu commands
    from handlers.commands import menu_command, wallet_command, escrow_command, profile_command, help_command, orders_command, settings_command, support_command, show_account_settings, show_cashout_settings
    for cmd, func in [("menu", menu_command), ("wallet", wallet_command), ("escrow", escrow_command),
                      ("profile", profile_command), ("help", help_command), ("orders", orders_command),
                      ("settings", settings_command), ("support", support_command)]:
        application.add_handler(CommandHandler(cmd, create_blocking_aware_command_handler(func)), group=0)

    # Critical wallet/escrow/navigation callback handlers
    from handlers.wallet_direct import (
        show_crypto_funding_options, start_add_funds, handle_bank_selection, handle_deposit_currency_selection,
        show_deposit_qr, handle_save_bank_account, handle_add_new_bank,
        show_saved_bank_accounts_management, show_saved_crypto_addresses_management,
        show_comprehensive_transaction_history, handle_back_to_main, handle_ngn_bank_account_input,
        handle_wallet_menu, handle_wallet_cashout, handle_toggle_auto_cashout,
        handle_auto_cashout_bank_selection, handle_auto_cashout_crypto_selection,
        handle_set_auto_cashout_bank, handle_set_auto_cashout_crypto,
        handle_confirm_unverified_cashout, handle_retry_bank_verification
    )
    from handlers.fincra_payment import FincraPaymentHandler, register_fincra_handlers
    from handlers.start import handle_demo_exchange, handle_demo_escrow
    from handlers.missing_handlers import (
        handle_main_menu_callback, handle_my_escrows, handle_menu_escrows,
        handle_wal_history, handle_withdrawal_history, handle_exchange_crypto, handle_complete_trading,
        handle_quick_rating_access, handle_settings_verify_email, handle_start_email_verification
    )
    from handlers.messages_hub import show_trades_messages_hub, handle_start_dispute, handle_dispute_trade
    from handlers.escrow import (
        start_secure_trade, handle_payment_method_selection,
        handle_release_funds, handle_cancel_release_funds, handle_confirm_release_funds, handle_mark_delivered
    )
    from handlers.referral import handle_invite_friends, handle_referral_stats, handle_referral_leaderboard
    from handlers.contact_management import ContactManagementHandler

    critical_callbacks = [
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
        (show_comprehensive_transaction_history, '^wallet_history$'),
        (handle_wal_history, '^wal_history$'),
        (show_saved_bank_accounts_management, '^manage_bank_accounts$'),
        (show_saved_crypto_addresses_management, '^manage_crypto_addresses$'),
        (show_help_from_onboarding_callback, '^menu_help$'),
        (handle_main_menu_callback, '^main_menu$'),
        (handle_back_to_main, '^back_to_main$'),
        (show_trades_messages_hub, '^trades_messages_hub$'),
        (start_secure_trade, '^(start_secure_trade|create_escrow)$'),
        (handle_demo_exchange, '^demo_exchange$'),
        (handle_demo_escrow, '^demo_escrow$'),
        (handle_invite_friends, '^invite_friends$'),
        (handle_referral_stats, '^referral_stats$'),
        (handle_referral_leaderboard, '^referral_leaderboard$'),
        (handle_my_escrows, '^my_escrows$'),
        (handle_menu_escrows, '^menu_escrows$'),
        (handle_withdrawal_history, '^withdrawal_history$'),
        (handle_wallet_menu, '^menu_wallet$'),
        (handle_deposit_currency_selection, '^wallet_deposit$'),
        (handle_wallet_cashout, '^wallet_withdraw$'),
        (ContactManagementHandler().contact_management_menu, '^contact_menu$'),
        (handle_admin_main, '^admin_main$'),
        (handle_admin_analytics, '^admin_analytics$'),
        (handle_admin_disputes, '^admin_disputes$'),
        (handle_admin_reports, '^admin_reports$'),
        (handle_admin_manual_ops, '^admin_manual_ops$'),
        (handle_admin_manual_cashouts, '^admin_manual_cashouts$'),
        (handle_admin_health, '^admin_health$'),
        (handle_payment_method_selection, '^payment_.*$'),
        (handle_payment_method_selection, '^wallet_insufficient$'),
        (start_add_funds, '^wallet_add_funds$'),
        (start_secure_trade, '^menu_create$'),
        (show_cashout_settings, '^auto_cashout_settings$'),
        (handle_toggle_auto_cashout, '^toggle_auto_cashout$'),
        (handle_auto_cashout_bank_selection, '^auto_cashout_set_bank$'),
        (handle_auto_cashout_crypto_selection, '^auto_cashout_set_crypto$'),
        (handle_set_auto_cashout_bank, '^set_auto_bank:.*$'),
        (handle_set_auto_cashout_crypto, '^set_auto_crypto:.*$'),
        (handle_wallet_menu, '^wallet_menu$'),
        (handle_release_funds, '^release_funds_.*$'),
        (handle_cancel_release_funds, '^cancel_release_.*$'),
        (handle_confirm_release_funds, '^confirm_release_.*$'),
        (handle_mark_delivered, '^mark_delivered_.*$'),
        (handle_start_dispute, '^start_dispute$'),
        (handle_dispute_trade, '^dispute_trade:.*$'),
        (handle_exchange_crypto, '^exchange_crypto$'),
        (handle_complete_trading, '^complete_trading$'),
        (handle_quick_rating_access, '^quick_rating_access$'),
        (handle_settings_verify_email, '^settings_verify_email$'),
        (handle_start_email_verification, '^start_email_verification$'),
        (handle_confirm_unverified_cashout, '^confirm_unverified_cashout_.*$'),
    ]

    for handler_func, pattern in critical_callbacks:
        try:
            application.add_handler(CallbackQueryHandler(create_blocking_aware_handler(handler_func), pattern=pattern), group=0)
        except Exception as e:
            logger.error(f"Failed to register {handler_func.__name__}: {e}")

    # Bank input handler
    application.add_handler(MessageHandler(filters.Regex(r"^\d{10}$") & ~filters.COMMAND, create_blocking_aware_handler(handle_ngn_bank_account_input)), group=0)

    # Unified text router
    from handlers.text_router import create_unified_text_handler
    application.add_handler(create_unified_text_handler(), group=0)

    # OTP verification
    try:
        from utils.unified_text_router import unified_text_router
        from handlers.otp_verification import handle_otp_verification
        unified_text_router.register_conversation_handler("otp_verification", handle_otp_verification)
    except Exception as e:
        logger.warning(f"OTP handler registration failed: {e}")

    # Direct handler groups
    handler_modules = [
        ('handlers.escrow_direct', 'DIRECT_ESCROW_HANDLERS', -1),
        ('handlers.user_rating_direct', 'DIRECT_RATING_HANDLERS', -1),
        ('handlers.contact_management_direct', 'DIRECT_CONTACT_HANDLERS', -1),
        ('handlers.admin_cashout_direct', 'DIRECT_ADMIN_CASHOUT_HANDLERS', -1),
        ('handlers.admin_transactions_direct', 'DIRECT_ADMIN_TRANSACTION_HANDLERS', -1),
        ('handlers.admin_broadcast_direct', 'DIRECT_ADMIN_BROADCAST_HANDLERS', 1),
        ('handlers.admin_rating_direct', 'DIRECT_ADMIN_RATING_HANDLERS', -1),
        ('handlers.seller_profile', 'SELLER_PROFILE_HANDLERS', -1),
        ('handlers.rating_ui_enhancements', 'RATING_UI_HANDLERS', -1),
        ('handlers.admin_comprehensive_config_direct', 'DIRECT_ADMIN_CONFIG_HANDLERS', -1),
        ('handlers.ux_improvements', 'UX_IMPROVEMENT_HANDLERS', -1),
        ('handlers.dispute_chat_direct', 'DIRECT_DISPUTE_HANDLERS', -1),
        ('handlers.messages_hub_direct', 'DIRECT_MESSAGES_HANDLERS', -1),
        ('handlers.multi_dispute_manager_direct', 'DIRECT_MULTI_DISPUTE_HANDLERS', -1),
        ('handlers.session_ui_direct', 'DIRECT_SESSION_UI_HANDLERS', -1),
    ]

    import importlib
    for module_name, attr_name, group in handler_modules:
        try:
            mod = importlib.import_module(module_name)
            handlers = getattr(mod, attr_name)
            for h in handlers:
                application.add_handler(h, group=group)
            logger.info(f"Registered {attr_name}")
        except Exception as e:
            logger.warning(f"Failed to register {attr_name}: {e}")

    # Admin maintenance
    try:
        from handlers.admin_maintenance import register_maintenance_handlers
        register_maintenance_handlers(application)
    except Exception as e:
        logger.warning(f"Maintenance handlers failed: {e}")

    # Exchange handler
    from handlers.exchange_handler import ExchangeHandler
    application.add_handler(CallbackQueryHandler(ExchangeHandler.start_exchange, pattern='^start_exchange$'), group=0)
    application.add_handler(CallbackQueryHandler(ExchangeHandler.handle_bank_switch_selection, pattern='^exchange_bank_switch_'), group=0)
    application.add_handler(CallbackQueryHandler(ExchangeHandler.handle_pre_confirmation_bank_switch, pattern='^exchange_bank_switch_pre$'), group=0)
    application.add_handler(CallbackQueryHandler(handle_retry_bank_verification, pattern='^retry_bank_verification$'), group=0)

    # Fincra handlers
    register_fincra_handlers(application)

    # Onboarding router
    try:
        from handlers.onboarding_router import register_onboarding_handlers
        register_onboarding_handlers(application)
    except Exception as e:
        logger.warning(f"Onboarding router failed: {e}")

    # Refund commands
    try:
        from handlers.refund_command_registry import RefundCommandRegistry
        RefundCommandRegistry.register_all_commands(application)
    except Exception as e:
        logger.warning(f"Refund commands failed: {e}")

    # Admin retry commands
    try:
        from handlers.admin_retry_command_registry import AdminRetryCommandRegistry
        AdminRetryCommandRegistry.register_all_commands(application)
    except Exception as e:
        logger.warning(f"Admin retry commands failed: {e}")

    # Wallet text input
    try:
        from utils.unified_text_router import unified_text_router
        from handlers.wallet_text_input import handle_wallet_text_input
        unified_text_router.register_conversation_handler("wallet_input", handle_wallet_text_input)
        unified_text_router.register_conversation_handler("cashout_flow", handle_wallet_text_input)
    except Exception as e:
        logger.warning(f"Wallet text input registration failed: {e}")

    # Update interceptor
    from utils.update_interceptor import register_update_interceptor
    register_update_interceptor(application)

    logger.info("All critical handlers registered")


async def _start_background_systems(application):
    """Start background systems after bot initialization."""
    await asyncio.sleep(2)
    try:
        from services.webhook_startup_service import webhook_startup_service
        await webhook_startup_service.initialize_webhook_system()
        logger.info("Webhook processing system started")
    except Exception as e:
        logger.warning(f"Webhook system init failed: {e}")

    try:
        from services.background_email_queue import initialize_email_queue
        await initialize_email_queue()
        logger.info("Email queue started")
    except Exception as e:
        logger.warning(f"Email queue failed: {e}")

    # OPTIMIZATION: Realtime monitoring gated behind ENABLE_DEEP_MONITORING
    if os.environ.get("ENABLE_DEEP_MONITORING", "false").lower() == "true":
        try:
            from utils.realtime_monitor import start_realtime_monitoring
            start_realtime_monitoring(application.bot)
            logger.info("Realtime monitoring started (ENABLE_DEEP_MONITORING=true)")
        except Exception as e:
            logger.warning(f"Realtime monitoring failed: {e}")
    else:
        logger.info("Realtime monitoring skipped (set ENABLE_DEEP_MONITORING=true to enable)")

    try:
        from services.auto_release_task_runner import start_auto_release_background_task
        await start_auto_release_background_task()
        logger.info("Auto-release system started")
    except Exception as e:
        logger.warning(f"Auto-release system failed: {e}")

    logger.info("Background systems initialized")


# Patch the lifespan to include bot initialization (non-blocking)
from contextlib import asynccontextmanager

_original_lifespan = app.router.lifespan_context

@asynccontextmanager
async def _patched_lifespan(app_instance):
    """Initialize bot only when not in Emergent preview (bot blocks event loop)."""
    # Skip heavy bot initialization in preview environments to keep server responsive
    is_preview = 'preview.emergentagent.com' in os.environ.get('WEBHOOK_URL', '')
    if is_preview:
        logger.info("Emergent preview detected - skipping Telegram bot initialization (server-only mode)")
    else:
        try:
            await initialize_bot()
        except Exception as e:
            logger.error(f"Bot initialization failed (server will continue): {e}")
    async with _original_lifespan(app_instance) as state:
        yield state

app.router.lifespan_context = _patched_lifespan
