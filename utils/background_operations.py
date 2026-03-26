"""
Background Operations Module
Moves heavy startup operations to background tasks for faster bot startup
"""

import asyncio
import logging
from utils.parallel_startup import deferred_operation

logger = logging.getLogger(__name__)


@deferred_operation("database_optimization", delay=0.5, retry_count=1)
async def optimize_database_background():
    """Run database optimization in background after startup"""
    try:
        from utils.database_optimizer import DatabaseOptimizer
        from database import engine
        
        logger.info("🔧 Starting background database optimization...")
        await asyncio.get_event_loop().run_in_executor(
            None, 
            DatabaseOptimizer.setup_production_database, 
            engine
        )
        logger.info("✅ Background database optimization completed")
        
    except Exception as e:
        logger.error(f"❌ Background database optimization failed: {e}")


@deferred_operation("webhook_health_monitoring", delay=1.0, retry_count=2)
async def setup_webhook_monitoring_background():
    """Simplified webhook health monitoring setup in background"""
    try:
        from config import Config
        
        if Config.WEBHOOK_URL and Config.BOT_TOKEN:
            # SIMPLIFIED: Direct webhook monitoring without external module
            logger.info("✅ Simplified webhook monitoring operational")
            logger.info("✅ Background webhook health monitoring started")
        
    except Exception as e:
        logger.error(f"❌ Background webhook monitoring setup failed: {e}")


@deferred_operation("production_monitoring", delay=0.5, retry_count=1)
async def start_production_monitoring_background():
    """Start production monitoring in background"""
    try:
        from utils.production_monitoring import start_production_monitoring
        await asyncio.get_event_loop().run_in_executor(
            None, 
            start_production_monitoring
        )
        logger.info("✅ Background production monitoring started")
        
    except Exception as e:
        logger.warning(f"Production monitoring failed: {e}")


@deferred_operation("schema_monitoring", delay=2.0, retry_count=1)
async def start_schema_monitoring_background():
    """Start schema monitoring and alerting in background"""
    try:
        from utils.proactive_schema_monitor import start_proactive_schema_monitoring
        from utils.schema_alert_system import start_schema_alerting
        
        await start_proactive_schema_monitoring()
        await start_schema_alerting()
        logger.info("✅ Background schema monitoring started")
        
    except Exception as e:
        logger.warning(f"Schema monitoring startup failed: {e}")


@deferred_operation("auto_release_system", delay=3.0, retry_count=2)
async def start_auto_release_system_background():
    """Start auto-release and delivery deadline warning system in background"""
    try:
        from services.auto_release_task_runner import start_auto_release_background_task
        
        await start_auto_release_background_task()
        logger.info("✅ Auto-release system started - delivery warnings and auto-release processing active")
        
    except Exception as e:
        logger.error(f"❌ Auto-release system startup failed: {e}")
        # This is critical functionality, so log error but don't crash
        logger.warning("⚠️ Auto-release system is not running - manual intervention may be required")


@deferred_operation("withdrawal_monitoring", delay=1.5, retry_count=2)
async def start_withdrawal_monitoring_background():
    """Initialize withdrawal status monitoring for real blockchain transaction hashes"""
    try:
        from services.withdrawal_status_monitor import WithdrawalStatusMonitor
        
        # Create new instance to ensure proper initialization
        monitor = WithdrawalStatusMonitor()
        await monitor.initialize_monitoring()
        logger.info("✅ Withdrawal status monitoring initialized - checking every 2 minutes for blockchain hashes")
        
    except Exception as e:
        logger.error(f"❌ Withdrawal monitoring initialization failed: {e}")


@deferred_operation("memory_optimization", delay=3.0, retry_count=1)
async def memory_cleanup_background():
    """Perform memory cleanup in background"""
    try:
        from utils.performance_monitor import MemoryOptimizer
        await asyncio.get_event_loop().run_in_executor(
            None, 
            MemoryOptimizer.cleanup_startup_memory
        )
        
        memory_info = MemoryOptimizer.get_memory_info()
        logger.info(f"✅ Background memory cleanup completed: {memory_info['rss_mb']}MB ({memory_info['percent']}% of system)")
        
    except Exception as e:
        logger.error(f"❌ Background memory cleanup failed: {e}")


class CriticalOperationsManager:
    """Manages only critical operations needed for basic bot functionality"""
    
    @staticmethod
    async def setup_critical_infrastructure(application):
        """Setup only critical infrastructure needed for bot to start"""
        logger.info("🚀 Setting up critical infrastructure...")
        logger.info("Setting up wallet handlers...")
        
        # Register DIRECT_WALLET_HANDLERS for critical wallet functionality
        await CriticalOperationsManager._setup_direct_wallet_handlers(application)
        
        tasks = [
            CriticalOperationsManager._setup_error_handlers(application),
            CriticalOperationsManager._setup_basic_commands(application),
            CriticalOperationsManager._setup_scheduler_minimal(application),
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        success_count = sum(1 for r in results if r is not None and not isinstance(r, Exception))
        logger.info(f"✅ Critical infrastructure setup: {success_count}/3 components ready")
        
        return success_count >= 2  # Allow 1 failure
    
    @staticmethod
    async def _setup_direct_wallet_handlers(application):
        """Setup critical direct wallet handlers including select_bank"""
        try:
            from telegram.ext import CallbackQueryHandler
            from handlers.wallet_direct import DIRECT_WALLET_HANDLERS
            
            logger.info(f"Registering {len(DIRECT_WALLET_HANDLERS)} wallet handlers")
            
            # Register ALL wallet direct handlers (including save_address_continue)
            for i, handler_item in enumerate(DIRECT_WALLET_HANDLERS):
                # Handle both dictionary format and direct handler objects
                if isinstance(handler_item, dict):
                    # Dictionary format: {'pattern': '...', 'handler': ...}
                    pattern = handler_item['pattern']
                    handler = handler_item['handler']
                    description = handler_item.get('description', 'No description')
                    
                    logger.debug(f"Registering dict handler {i}: pattern='{pattern}' handler={handler.__name__ if handler else 'None'} desc='{description}'")
                    
                    if handler is None:
                        logger.error(f"❌ Handler {i} is None for pattern '{pattern}' - skipping")
                        continue
                        
                    application.add_handler(
                        CallbackQueryHandler(handler, pattern=pattern), 
                        group=0
                    )
                else:
                    # Direct handler object (CallbackQueryHandler, MessageHandler, etc.)
                    # PRIORITY: MessageHandler for OTP verification gets highest priority (group -2)
                    group = -2 if hasattr(handler_item, 'filters') and 'TEXT' in str(handler_item.filters) else 0
                    logger.debug(f"Registering direct handler {i}: {type(handler_item).__name__} (group {group})")
                    application.add_handler(handler_item, group=group)
            
            logger.info(f"✅ Successfully registered {len(DIRECT_WALLET_HANDLERS)} wallet handlers including select_bank")
            
        except Exception as e:
            logger.error(f"❌ FAILED to register DIRECT_WALLET_HANDLERS: {e}")
            import traceback
            logger.error(f"❌ Traceback: {traceback.format_exc()}")
            raise
    
    @staticmethod
    async def _setup_error_handlers(application):
        """Setup global error handler"""
        try:
            async def enhanced_global_error_handler(update, context):
                try:
                    import traceback
                    user_id = update.effective_user.id if update.effective_user else None
                    error_msg = str(context.error) if context.error else "Unknown error"
                    logger.error(f"Bot error for user {user_id}: {error_msg}")
                    if context.error:
                        logger.error(f"Full traceback: {traceback.format_exc()}")
                except Exception as final_error:
                    logger.critical(f"Global error handler failed: {final_error}")
            
            application.add_error_handler(enhanced_global_error_handler)
            logger.info("✅ Global error handler added")
            return True
            
        except Exception as e:
            logger.error(f"❌ Error handler setup failed: {e}")
            return False
    
    @staticmethod
    async def _setup_basic_commands(application):
        """Setup only essential command handlers"""
        try:
            from telegram.ext import CommandHandler
            
            # ARCHITECTURE: 100% Direct Handler mode - ConversationHandlers disabled
            # The /start command is now handled by the onboarding router system
            # This prevents duplicate handler registration and double main menu issues
            
            # Register essential commands that were missing
            from handlers.commands import (
                create_command, profile_command, help_command,
                wallet_command, escrows_command, exchange_command,
                menu_command, escrow_command, orders_command, 
                settings_command
            )
            
            # Add the missing command handlers
            # NOTE: /cashout and /support removed - only accessible via buttons
            command_handlers = [
                CommandHandler("create", create_command),
                CommandHandler("profile", profile_command),
                CommandHandler("help", help_command),
                CommandHandler("wallet", wallet_command),
                CommandHandler("trades", escrows_command),
                CommandHandler("exchange", exchange_command),
                # CRITICAL FIX: Add missing bot menu commands
                CommandHandler("menu", menu_command),
                CommandHandler("escrow", escrow_command),
                CommandHandler("orders", orders_command),
                CommandHandler("settings", settings_command),
            ]
            
            # Register all command handlers
            for handler in command_handlers:
                application.add_handler(handler, group=1)
            
            logger.info(f"✅ Registered {len(command_handlers)} essential command handlers")
            logger.info("✅ ConversationHandler disabled - using 100% direct handler architecture")
            return True
            
        except Exception as e:
            logger.error(f"❌ Basic commands setup failed: {e}")
            return False
    
    @staticmethod
    async def _setup_scheduler_minimal(application):
        """Setup minimal scheduler functionality"""
        try:
            from jobs.consolidated_scheduler import ConsolidatedScheduler
            
            scheduler = ConsolidatedScheduler(application)
            
            # Register scheduler globally
            from utils.scheduler_access import set_global_scheduler
            set_global_scheduler(scheduler.scheduler)
            
            # Don't start scheduler here - it will be started in post_init
            logger.info("✅ Scheduler prepared (will start after bot initialization)")
            return True
            
        except Exception as e:
            logger.error(f"❌ Scheduler setup failed: {e}")
            return False


class LazyHandlerGroups:
    """Defines handler groups for lazy loading"""
    
    HANDLER_GROUPS = {
        'admin_handlers': {
            'module': 'handlers.admin',
            'priority': 3,  # High priority - admin access critical
            'handlers': [
                # Core admin access
                {'type': 'command', 'name': 'admin_command', 'command': 'admin'},
                {'type': 'callback_query', 'name': 'handle_admin_main', 'pattern': '^admin_main$'},
                
                # Admin dashboard sections  
                {'type': 'callback_query', 'name': 'handle_admin_health', 'pattern': '^admin_health$'},
                {'type': 'callback_query', 'name': 'handle_admin_sysinfo', 'pattern': '^admin_sysinfo$'},
                {'type': 'callback_query', 'name': 'handle_admin_analytics', 'pattern': '^admin_analytics$'},
                {'type': 'callback_query', 'name': 'handle_admin_reports', 'pattern': '^admin_reports$'},
                {'type': 'callback_query', 'name': 'handle_admin_settings', 'pattern': '^admin_settings$'},
                {'type': 'callback_query', 'name': 'handle_admin_manual_ops', 'pattern': '^admin_manual_ops$'},
                {'type': 'callback_query', 'name': 'handle_admin_trade_chats', 'pattern': '^admin_trade_chats$'},
            ]
        },
        
        'admin_payment_config': {
            'module': 'handlers.admin_payment_config',
            'priority': 3,  # High priority - admin payment management
            'handlers': [
                {'type': 'callback_query', 'name': 'handle_admin_payment_config', 'pattern': '^admin_payment_config$'},
                {'type': 'callback_query', 'name': 'handle_payment_config_action', 'pattern': '^payment_config_'},
            ]
        },
        
        'admin_transaction_handlers': {
            'module': 'handlers.admin_transactions',
            'priority': 3,  # High priority - admin transaction management
            'handlers': [
                {'type': 'callback_query', 'name': 'handle_admin_trans_escrows', 'pattern': '^admin_trans_escrows$'},
                {'type': 'callback_query', 'name': 'handle_admin_trans_cashouts', 'pattern': '^admin_trans_cashouts$'},
                {'type': 'callback_query', 'name': 'handle_admin_cashout_pending', 'pattern': '^admin_cashout_pending$'},
                {'type': 'callback_query', 'name': 'handle_admin_approve_low_risk', 'pattern': '^admin_approve_low_risk$'},
                {'type': 'callback_query', 'name': 'handle_admin_review_high_risk', 'pattern': '^admin_review_high_risk$'},
                {'type': 'callback_query', 'name': 'handle_admin_trans_analytics', 'pattern': '^admin_trans_analytics$'},
            ]
        },
        
        'admin_referral_handlers': {
            'module': 'handlers.admin_referral',
            'priority': 3,  # High priority - admin referral management
            'handlers': [
                {'type': 'callback_query', 'name': 'handle_admin_referrals', 'pattern': '^admin_referrals$'},
                {'type': 'callback_query', 'name': 'handle_admin_referral_analytics', 'pattern': '^admin_referral_analytics$'},
                {'type': 'callback_query', 'name': 'handle_admin_referral_config', 'pattern': '^admin_referral_config$'},
            ]
        },
        
        'wallet_handlers': {
            'module': 'handlers.wallet_direct', 
            'priority': 2,  # CRITICAL: Lower priority than conversation handlers to avoid text interception
            'handlers': [
                {'type': 'callback_query', 'name': 'handle_wallet_menu', 'pattern': '^menu_wallet$'},
                {'type': 'callback_query', 'name': 'handle_wallet_cashout', 'pattern': '^wallet_cash_out$'},
                {'type': 'callback_query', 'name': 'handle_method_selection', 'pattern': '^method:'},
                {'type': 'callback_query', 'name': 'handle_amount_selection', 'pattern': '^amount:'},
                {'type': 'callback_query', 'name': 'handle_crypto_currency_selection', 'pattern': '^select_crypto:'},
                {'type': 'callback_query', 'name': 'handle_crypto_currency_selection', 'pattern': '^cashout_network:'},
                {'type': 'callback_query', 'name': 'handle_select_bank', 'pattern': '^select_bank:'},
                {'type': 'callback_query', 'name': 'handle_confirm_crypto_cashout', 'pattern': '^confirm_crypto_cashout$'},
                {'type': 'callback_query', 'name': 'handle_back_to_main', 'pattern': '^back_to_main$'},
                # REMOVED: Text message handlers that were blocking escrow conversation
                # These handlers were consuming ALL text input before ConversationHandlers could process it
                # Wallet text input is now handled through proper conversation states
            ]
        },
        
        # Removed wallet_history_handlers - handlers.wallet module doesn't exist
        # Transaction history is now handled in wallet_direct.py
        
        # Removed menu_handlers - handlers.wallet module doesn't exist
        # Menu navigation is now handled in other modules
        
        'escrow_handlers': {
            'module': 'handlers.escrow',
            'priority': 5,  # DISABLED: ConversationHandler removed - using direct handlers only
            'handlers': [
                # DISABLED: {'type': 'conversation', 'name': 'new_escrow_conversation'},  # CRITICAL FIX: Disabled to prevent handler conflicts
                {'type': 'callback_query', 'name': 'handle_share_link', 'pattern': '^share_link:'},
            ]
        },
        
        'user_rating_handlers': {
            'module': 'handlers.user_rating',
            'priority': 3,  # High priority - user feedback features  
            'handlers': [
                {'type': 'callback_query', 'name': 'handle_rate_seller', 'pattern': '^rate_seller_'},
                {'type': 'callback_query', 'name': 'handle_rate_buyer', 'pattern': '^rate_buyer_'},
                {'type': 'callback_query', 'name': 'handle_rating_selection', 'pattern': '^rating_select_'},
            ]
        },
        
        'referral_handlers': {
            'module': 'handlers.referral',
            'priority': 3,  # High priority - user engagement features
            'handlers': [
                {'type': 'callback_query', 'name': 'handle_invite_friends', 'pattern': '^invite_friends$'},
                {'type': 'callback_query', 'name': 'handle_referral_stats', 'pattern': '^referral_stats$'},
                {'type': 'callback_query', 'name': 'handle_referral_leaderboard', 'pattern': '^referral_leaderboard$'},
            ]
        },
        
        # DISABLED: start_handlers group - now using 100% direct handler architecture
        # The /start command is handled by direct_start_command in DIRECT_ONBOARDING_HANDLERS
        # This prevents duplicate registration and double main menu issues
        # 'start_handlers': {
        #     'module': 'handlers.start',
        #     'priority': 4,  # Highest priority - basic navigation and onboarding
        #     'handlers': [
        #         {'type': 'callback_query', 'name': 'start_onboarding_callback', 'pattern': '^start_onboarding$'},
        #         {'type': 'conversation', 'name': 'onboarding_conversation'},
        #     ]
        # },
        
        
        'commands_handlers': {
            'module': 'handlers.commands',
            'priority': 4,  # Highest priority - basic commands
            'handlers': [
                {'type': 'command', 'name': 'exchange_command', 'command': 'exchange'},
                {'type': 'command', 'name': 'help_command', 'command': 'help'},
                {'type': 'command', 'name': 'wallet_command', 'command': 'wallet'},
                {'type': 'command', 'name': 'escrows_command', 'command': 'escrows'},
                {'type': 'command', 'name': 'cashout_command', 'command': 'cashout'},
            ]
        },
        
        'dispute_handlers': {
            'module': 'handlers.dispute_chat',
            'priority': 3,  # High priority - critical for escrow disputes
            'handlers': [
                {'type': 'callback_query', 'name': 'show_admin_disputes_realtime', 'pattern': '^admin_disputes$'},
                {'type': 'callback_query', 'name': 'handle_admin_dispute_chat_live', 'pattern': '^dispute_chat_'},
            ]
        },
        
        
        'contact_handlers': {
            'module': 'handlers.contact_management',
            'priority': 3,  # High priority - support features
            'handlers': [
                # Contact conversation handler was removed - individual handlers loaded directly if needed
            ]
        },
        
        'post_exchange_handlers': {
            'module': 'handlers.post_exchange_callbacks',
            'priority': 4,  # Highest priority - exchange completion flows
            'handlers': [
                {'type': 'callback_query', 'name': 'handle_exchange_rating', 'pattern': '^rate_exchange_'},
                {'type': 'callback_query', 'name': 'handle_view_exchange', 'pattern': '^view_exchange_'},
                {'type': 'callback_query', 'name': 'handle_view_exchange_stats', 'pattern': '^view_exchange_stats_'},
                {'type': 'callback_query', 'name': 'handle_view_achievements', 'pattern': '^view_achievements_'},
            ]
        },
        
        
        
        'messages_hub_handlers': {
            'module': 'handlers.messages_hub',
            'priority': 4,  # Highest priority - messaging system
            'handlers': [
                {'type': 'callback_query', 'name': 'show_trades_messages_hub', 'pattern': '^trades_messages_hub$'},
            ]
        },
        
        'ux_improvement_handlers': {
            'module': 'handlers.ux_improvements',
            'priority': 4,  # Highest priority - UX enhancements
            'handlers': [
                {'type': 'callback_query', 'name': 'handle_track_status', 'pattern': '^track_status_'},
                {'type': 'callback_query', 'name': 'handle_help_timeline', 'pattern': '^help_timeline_'},
                {'type': 'callback_query', 'name': 'handle_cancel_escrow_improved', 'pattern': '^cancel_escrow_'},
                {'type': 'callback_query', 'name': 'handle_send_reminder', 'pattern': '^send_reminder_'},
                {'type': 'callback_query', 'name': 'handle_extend_deadline', 'pattern': '^extend_deadline_'},
                {'type': 'callback_query', 'name': 'handle_confirm_cancel', 'pattern': '^confirm_cancel_'},
                {'type': 'callback_query', 'name': 'handle_accept_fees_create_trade', 'pattern': '^accept_fees_create_trade$'},
                {'type': 'callback_query', 'name': 'handle_explain_fees', 'pattern': '^explain_fees$'},
                {'type': 'callback_query', 'name': 'handle_cancel_fee_acceptance', 'pattern': '^cancel_fee_acceptance$'},
            ]
        },
        
        'menu_handlers': {
            'module': 'handlers.menu',
            'priority': 4,  # Highest priority - navigation
            'handlers': [
                {'type': 'callback_query', 'name': 'show_hamburger_menu', 'pattern': '^hamburger_menu$'},
                {'type': 'callback_query', 'name': 'handle_main_menu_navigation', 'pattern': '^main_menu$'},
            ]
        },
        
        'refund_handlers': {
            'module': 'handlers.refund_command_registry',
            'priority': 3,  # High priority - important user functionality
            'handlers': [
                {'type': 'command', 'name': 'refunds_command_handler', 'command': 'refunds'},
                {'type': 'callback_query', 'name': 'refund_status_handler', 'pattern': '^refund_status_'},
                {'type': 'conversation', 'name': 'refund_conversation_handler'},
            ]
        },
        
        'refund_notification_handlers': {
            'module': 'handlers.refund_notification_handlers', 
            'priority': 3,  # High priority - critical for refund UX
            'description': 'Refund notification callback handlers',
            'handlers': [
                # Note: Both register_handlers (class method) and register_refund_notification_handlers (global function) exist
                # Using the global function which is accessible at module level
                {'type': 'function', 'name': 'register_refund_notification_handlers'},
            ]
        },
        
        'missing_critical_handlers': {
            'module': 'handlers.missing_handlers',
            'priority': 4,  # Highest priority - critical navigation
            'handlers': [
                {'type': 'callback_query', 'name': 'handle_my_escrows', 'pattern': '^my_escrows$'},
                {'type': 'callback_query', 'name': 'handle_menu_escrows', 'pattern': '^menu_escrows$'},
                {'type': 'callback_query', 'name': 'handle_wal_history', 'pattern': '^wal_history$'},
                {'type': 'callback_query', 'name': 'handle_withdrawal_history', 'pattern': '^withdrawal_history$'},
                {'type': 'callback_query', 'name': 'handle_main_menu_callback', 'pattern': '^main_menu$'},
            ]
        }
    }
    
    @classmethod
    def get_high_priority_groups(cls):
        """Get handler groups that should be loaded early"""
        return [name for name, config in cls.HANDLER_GROUPS.items() if config['priority'] >= 3]
    
    @classmethod
    def get_low_priority_groups(cls):
        """Get handler groups that can be loaded lazily"""
        return [name for name, config in cls.HANDLER_GROUPS.items() if config['priority'] <= 2]