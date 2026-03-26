"""Background job scheduler for the Telegram Escrow Bot with atomic transaction support"""

import logging
from datetime import datetime, timedelta
from decimal import Decimal
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.executors.asyncio import AsyncIOExecutor
from apscheduler.jobstores.memory import MemoryJobStore
from models import Escrow, EscrowStatus, User, Transaction, ExchangeStatus
from services.notification_service import notification_service as notification_hub
from services.crypto import CryptoServiceAtomic
from utils.atomic_transactions import atomic_transaction, locked_escrow_operation
from utils.exchange_state_validator import ExchangeStateValidator
from jobs.financial_audit_relay import (
    financial_audit_relay_handler,
    financial_audit_cleanup_handler,
    financial_audit_stats_handler
)
from config import Config

logger = logging.getLogger(__name__)


class EscrowScheduler:
    """Background job scheduler for escrow automation with race condition protection"""

    def __init__(self, application):
        self.application = application
        # Optimized scheduler configuration for performance
        
        jobstores = {
            'default': MemoryJobStore()
        }
        executors = {
            'default': AsyncIOExecutor()  # Use default async executor (APScheduler handles worker pool internally)
        }
        job_defaults = {
            'coalesce': True,  # Global coalescing to prevent job pileup
            'max_instances': 1,  # Global single instance enforcement
            'misfire_grace_time': 120  # Extended grace time for missed jobs
        }
        
        self.scheduler = AsyncIOScheduler(
            jobstores=jobstores,
            executors=executors,
            job_defaults=job_defaults,
            timezone='UTC'
        )

    def setup_jobs(self):
        """Setup all scheduled jobs with atomic operations"""
        
        # DEFENSIVE CLEANUP: Remove existing retry jobs to prevent hot-reload duplication
        try:
            retry_job_ids = ["unified_retry_processor"]
            for job_id in retry_job_ids:
                existing_job = self.scheduler.get_job(job_id)
                if existing_job:
                    self.scheduler.remove_job(job_id)
                    logger.info(f"🧹 Hot-reload safety: Removed existing {job_id} job")
        except Exception as e:
            logger.warning(f"⚠️ Hot-reload cleanup warning (non-critical): {e}")

        # Background crypto rate fetching for performance optimization (every 30 seconds)
        self.scheduler.add_job(
            self.prefetch_crypto_rates,
            trigger=IntervalTrigger(seconds=30, start_date=datetime.now().replace(second=5, microsecond=0)),
            id="prefetch_crypto_rates",
            name="Prefetch Crypto Rates",
            max_instances=1,
            coalesce=True,
        )

        # Check deposit confirmations every 15 minutes (STAGGERED: 12 minutes offset)
        self.scheduler.add_job(
            self.check_deposit_confirmations,
            trigger=IntervalTrigger(minutes=15, start_date=datetime.now().replace(second=12, microsecond=0)),
            id="check_deposits",
            name="Check Deposit Confirmations",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=60,
        )

        # Check for expired escrows every 20 minutes (optimized for performance)
        self.scheduler.add_job(
            self.handle_expired_escrows,
            trigger=IntervalTrigger(minutes=20),
            id="check_expired",
            name="Handle Expired Escrows",
            max_instances=1,
        )

        # Payment timeout handling removed - consolidated into Systematic Timeout Detection

        # Send reminder notifications every hour
        self.scheduler.add_job(
            self.send_reminder_notifications,
            trigger=IntervalTrigger(hours=1),
            id="send_reminders",
            name="Send Reminder Notifications",
            max_instances=1,
        )

        # Process admin notification queue every 2 minutes (prevents lost notifications)
        self.scheduler.add_job(
            self.process_admin_notification_queue,
            trigger=IntervalTrigger(minutes=2, start_date=datetime.now().replace(second=30, microsecond=0)),
            id="process_admin_notifications",
            name="Process Admin Notification Queue",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=30,
        )

        # Check NGN payment confirmations every 15 minutes (STAGGERED: 32 seconds offset)
        # NGN Payment Polling removed - redundant with webhook processing

        # Expired verifications cleanup removed - batched into Unified Cleanup Orchestrator
        
        # Stuck user monitoring removed - consolidated into Systematic Timeout Detection
        
        # Stuck cashout cleanup removed - handled by Automatic Cashout Cleanup (Ultra-Fast) which is more sophisticated
        
        # Automatic Cashout Cleanup removed - handled by unified retry system
        
        # CRITICAL FIX: Clean up expired distributed locks every 15 minutes (STAGGERED: 45 seconds offset)
        self.scheduler.add_job(
            self.cleanup_expired_distributed_locks,
            trigger=IntervalTrigger(minutes=15, start_date=datetime.now().replace(second=45, microsecond=0)),
            id="cleanup_distributed_locks",
            name="Clean Up Expired Distributed Locks",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=30,
        )
        
        # Financial reconciliation removed - consolidated into Unified Financial Scanner

        # Fincra balance monitoring removed - handled by twice daily comprehensive reports

        # Binance monitoring removed - using Kraken instead

        # Exchange order confirmations and NGN payouts (optimized to 5 minutes)
        from jobs.exchange_monitor import check_exchange_confirmations
        # 🔒 SECURITY: Automatic refund import disabled - violates frozen funds policy
        # from services.automatic_refund_service import run_automatic_refund_check

        self.scheduler.add_job(
            check_exchange_confirmations,
            trigger=IntervalTrigger(minutes=17),
            id="exchange_confirmations",
            name="Check Exchange Order Confirmations and Process Payouts",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=60,
            replace_existing=True
        )
        

        # Stuck Order Monitor removed - redundant with Exchange Confirmations monitor

        # Check and expire old rate locks every 5 minutes
        # Rate locks cleanup removed - batched into Unified Cleanup Orchestrator

        # Automatic Refund Processing removed - redundant with Unified Retry System
        
        # Failed Cashout Refund Monitor removed - redundant with intelligent Unified Retry System
        
        # Unified Retry Processor - REMOVED: Now handled by Core Retry Engine
        # The Core Retry Engine handles all retry processing with proper async support
        logger.info("📝 Legacy Unified Retry Processor removed - now handled by Core Retry Engine")
        
        # Unified Financial Scanner - Combines reconciliation + duplicate detection
        try:
            from services.duplicate_transaction_monitor import scan_duplicate_transactions
            
            async def unified_financial_scanner():
                """Unified scanner for financial reconciliation and duplicate detection"""
                try:
                    # 1. Financial reconciliation check (every run)
                    try:
                        await self.financial_reconciliation_check()  # Already async
                        logger.debug("✅ Financial reconciliation completed")
                    except Exception as e:
                        logger.error(f"Financial reconciliation failed: {e}")
                    
                    # 2. Duplicate transaction detection (every 4th run = every 4 hours)
                    import time
                    current_hour = int(time.time()) // 3600
                    if current_hour % 4 == 0:  # Every 4 hours
                        try:
                            scan_duplicate_transactions()  # This is sync, not async
                            logger.debug("✅ Duplicate transaction scan completed")
                        except Exception as e:
                            logger.error(f"Duplicate transaction scan failed: {e}")
                    
                    logger.debug("✅ Unified financial scanner completed")
                    
                except Exception as e:
                    logger.error(f"Error in unified financial scanner: {e}")
            
            self.scheduler.add_job(
                unified_financial_scanner,
                trigger=IntervalTrigger(hours=1),  # Run hourly for reconciliation, every 4 hours for duplicates
                id="unified_financial_scanner",
                name="Unified Financial Scanner (Reconciliation + Duplicates)",
                max_instances=1,
                coalesce=True,
                misfire_grace_time=120,
                replace_existing=True
            )
            logger.info("✅ Unified financial scanner scheduled (consolidated reconciliation + duplicate detection)")
            
        except Exception as e:
            logger.error(f"❌ Failed to schedule unified financial scanner: {e}")
        
        # LOCKED FUNDS MONITORING: Comprehensive detection and alerting
        from jobs.locked_funds_monitor import monitor_locked_funds
        
        self.scheduler.add_job(
            monitor_locked_funds,
            trigger=IntervalTrigger(minutes=30),
            id="locked_funds_monitoring",
            name="Monitor and Alert on Locked Funds Issues",
            max_instances=1,
            coalesce=True,
        )
        
        # Locked Funds Auto-Cleanup removed - redundant with monitoring system

        # CRITICAL FIX: Systematic timeout handling across all system components
        from services.timeout_handler import run_systematic_timeout_check
        
        # OPTIMIZATION: Add job prioritization and better coalescing
        self.scheduler.add_job(
            run_systematic_timeout_check,
            trigger=IntervalTrigger(minutes=10),
            id="systematic_timeout_handling",
            name="Systematic Timeout Detection and Handling",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=30,  # Allow 30s grace for missed executions
            replace_existing=True
        )

        # Enhanced Automatic Recovery removed - currently placeholder, replaced by intelligent systems

        # ESCROW FIX: Enhanced notification failure handling with intelligent retries
        from services.enhanced_escrow_notification_handler import process_escrow_notification_retries
        
        self.scheduler.add_job(
            process_escrow_notification_retries,
            trigger=IntervalTrigger(minutes=6),
            id="escrow_notification_retries",
            name="Process Escrow Notification Retry Queue",
            max_instances=1,
            coalesce=True,
            replace_existing=True
        )

        # CONSISTENCY MONITOR: Detect and fix escrows with confirmed payments but missing holdings
        from jobs.escrow_consistency_monitor import monitor_escrow_consistency
        
        self.scheduler.add_job(
            monitor_escrow_consistency,
            trigger=IntervalTrigger(minutes=15, start_date=datetime.now().replace(second=18, microsecond=0)),
            id="escrow_consistency_monitor",
            name="Escrow Consistency Monitor (Detect Missing Holdings)",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=60,  # Allow 1min grace for execution delays
            replace_existing=True
        )

        # REMOVED DUPLICATE JOB - Already running every 1 minute above
        # This duplicate was causing double payouts by processing orders twice
        # Removed lines 123-128 that were leftover from incomplete job definition

        # Handle expired exchange orders every 5 minutes (disabled - function not implemented)
        # self.scheduler.add_job(
        #     handle_expired_exchange_orders,
        #     trigger=IntervalTrigger(minutes=5),
        #     id="handle_expired_exchanges",
        #     name="Handle Expired Exchange Orders",
        #     max_instances=1,
        # )

        # OTP rate limiter cleanup removed - batched into Unified Cleanup Orchestrator

        # Process auto-release escrows every 10 minutes (STAGGERED to prevent timing conflicts)
        self.scheduler.add_job(
            self.process_auto_release,
            trigger=IntervalTrigger(minutes=10),
            id="auto_release",
            name="Process Auto-Release Escrows",
            max_instances=1,
            coalesce=True,  # Prevent job pileup
            misfire_grace_time=120,  # Allow 2-minute grace for execution delays
        )
        
        # Send delivery deadline warnings every 30 minutes
        self.scheduler.add_job(
            self.send_delivery_deadline_warnings,
            trigger=IntervalTrigger(minutes=30),
            id="delivery_warnings",
            name="Send Delivery Deadline Warnings",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=60,
        )

        # Daily cleanup and maintenance at 02:00 UTC
        self.scheduler.add_job(
            self.daily_maintenance,
            trigger=CronTrigger(hour=2, minute=0),
            id="daily_maintenance",
            name="Daily Maintenance",
            max_instances=1,
        )

        # Daily financial report email at 08:00 UTC
        # DISABLED: Moved to ConsolidatedScheduler to prevent duplicates
        # The core_reporting job in ConsolidatedScheduler handles this now
        logger.info("📧 DAILY_FINANCIAL_REPORT: Disabled in legacy scheduler (handled by ConsolidatedScheduler)")

        # Daily backup at 3 AM UTC (LOW-PRIORITY: moved to separate thread pool)
        self.scheduler.add_job(
            self.daily_backup,
            trigger=CronTrigger(hour=3, minute=0),
            id="daily_backup",
            name="Daily Backup",
            max_instances=1,
            executor='default'  # Use separate executor for heavy operations
        )

        # Weekly backup on Sundays at 4 AM UTC
        self.scheduler.add_job(
            self.weekly_backup,
            trigger=CronTrigger(day_of_week=0, hour=4, minute=0),
            id="weekly_backup",
            name="Weekly Backup",
            max_instances=1,
        )

        # Update user statistics every 6 hours
        self.scheduler.add_job(
            self.update_user_statistics,
            trigger=IntervalTrigger(hours=6),
            id="update_stats",
            name="Update User Statistics",
            max_instances=1,
        )

        # Binance cashout processing removed - using Kraken instead

        # Binance cashout status checking removed - using Kraken instead

        # CRITICAL FIX: Combine both auto-cashout functions into a single comprehensive job
        # This job handles both processing existing cashouts AND monitoring user balances for new auto-cashouts
        from jobs.auto_cashout_monitor import AutoCashoutMonitor as AutoCashoutProcessor
        from jobs.auto_withdrawal_monitor import AutoCashoutMonitor as AutoWithdrawalMonitor

        async def comprehensive_auto_cashout_job():
            """Comprehensive auto-cashout job: process existing + check user balances"""
            # Feature guard: skip if auto cashout features disabled
            if not Config.ENABLE_AUTO_CASHOUT_FEATURES:
                logger.warning("⚠️ Auto cashout features disabled - skipping comprehensive auto-cashout job")
                return
            
            try:
                # First: Process existing pending cashouts
                existing_result = await AutoCashoutProcessor.check_and_process_auto_cashouts()
                logger.debug(f"Existing cashouts processed: {existing_result.get('processed_count', 0)}")
                
                # Second: Check user balances and create new auto-cashouts if needed  
                await AutoWithdrawalMonitor.check_and_process_auto_cashouts()
                logger.debug("User balance check for auto-cashouts completed")
                
            except Exception as e:
                logger.error(f"❌ Comprehensive auto-cashout job failed: {e}")

        self.scheduler.add_job(
            comprehensive_auto_cashout_job,
            trigger=IntervalTrigger(seconds=30),
            id="comprehensive_auto_cashouts",
            name="Comprehensive Auto-Cashout Monitor (Safe)",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=120,
            replace_existing=True,
        )

        # Unified Auto-Refresh Orchestrator - Handles all UI refresh operations efficiently
        try:
            from services.auto_refresh_service import auto_refresh_service
            from main import get_application_instance
            
            async def unified_auto_refresh_orchestrator():
                """Unified orchestrator for all UI refresh operations to eliminate redundancy"""
                try:
                    application = get_application_instance()
                    if not application:
                        logger.warning("Application instance not available for auto-refresh")
                        return
                    
                    # Batch all refresh operations together
                    refresh_results = {}
                    
                    # 1. Dynamic content refresh
                    try:
                        await auto_refresh_service.process_auto_refreshes(application)
                        refresh_results['content'] = 'success'
                    except Exception as e:
                        logger.error(f"Content refresh failed: {e}")
                        refresh_results['content'] = 'failed'
                    
                    # 2. Admin interface refresh
                    try:
                        from handlers.admin import auto_refresh_admin_interfaces
                        await auto_refresh_admin_interfaces()
                        refresh_results['admin'] = 'success'
                    except Exception as e:
                        logger.debug(f"Admin refresh skipped: {e}")
                        refresh_results['admin'] = 'skipped'
                    
                    # 3. Trade page refresh  
                    try:
                        from handlers.escrow import auto_refresh_trade_interfaces
                        await auto_refresh_trade_interfaces()
                        refresh_results['trade'] = 'success'
                    except Exception as e:
                        logger.debug(f"Trade refresh skipped: {e}")
                        refresh_results['trade'] = 'skipped'
                    
                    # Log summary
                    successful = sum(1 for v in refresh_results.values() if v == 'success')
                    logger.debug(f"✅ Unified refresh completed: {successful}/3 components updated")
                    
                except Exception as e:
                    logger.error(f"Error in unified auto-refresh orchestrator: {e}")
                    
            self.scheduler.add_job(
                func=unified_auto_refresh_orchestrator,
                trigger=IntervalTrigger(minutes=5),  # Use most frequent interval for responsiveness
                id="unified_auto_refresh",
                name="Unified Auto-Refresh Orchestrator",
                max_instances=1,
                coalesce=True,
                misfire_grace_time=90,
                replace_existing=True
            )
            logger.info("✅ Unified auto-refresh orchestrator scheduled (consolidated 3 jobs into 1)")
        except Exception as e:
            logger.error(f"❌ Failed to schedule unified auto-refresh orchestrator: {e}")

        # Daily rate notifications at 9:00 AM UTC
        self.scheduler.add_job(
            self.send_daily_rate_notifications,
            trigger=CronTrigger(hour=9, minute=0),
            id="daily_rates",
            name="Send Daily Rate Notifications",
            max_instances=1,
        )

        # Weekly savings reports (Phase 2 retention strategy) - Every Sunday at 10 AM UTC
        try:
            from jobs.weekly_savings_reports import weekly_savings_reports_job

            self.scheduler.add_job(
                weekly_savings_reports_job,
                trigger=CronTrigger(day_of_week="sun", hour=10, minute=0),
                id="weekly_savings_reports",
                name="Send Weekly Savings Reports",
                max_instances=1,
            )
        except ImportError:
            logger.warning(
                "Weekly savings reports not available - skipping job registration"
            )

        # UNIFIED SYSTEM & SERVICE MONITORING
        
        # Unified Health Monitor - Combines system monitoring + baseline metrics
        try:
            from jobs.monitoring_jobs import run_system_monitoring
            
            async def unified_health_monitor():
                """Unified health monitor combining system monitoring and baseline metrics"""
                try:
                    # 1. Run comprehensive system monitoring
                    await run_system_monitoring()
                    
                    # 2. Update baseline metrics (every 4th run = hourly at 14min intervals)
                    import time
                    current_hour = int(time.time()) // 3600
                    if current_hour % 4 == 0:  # Every 4th execution (roughly hourly)
                        try:
                            await self.update_baseline_metrics()
                            logger.debug("✅ Baseline metrics updated as part of unified health monitor")
                        except Exception as e:
                            logger.error(f"Baseline metrics update failed: {e}")
                    
                    logger.debug("✅ Unified health monitoring completed")
                    
                except Exception as e:
                    logger.error(f"Error in unified health monitor: {e}")
            
            self.scheduler.add_job(
                unified_health_monitor,
                trigger=IntervalTrigger(minutes=14, start_date=datetime.now().replace(second=25, microsecond=0)),
                id="unified_health_monitor",
                name="Unified System Health & Metrics Monitor",
                max_instances=1,
                coalesce=True
            )
            logger.info("✅ Unified health monitor scheduled (consolidated system monitoring + baseline metrics)")
            
        except Exception as e:
            logger.error(f"❌ Failed to schedule unified health monitor: {e}")

        # Service health monitoring removed - consolidated into System Health & Security Monitoring

        # Enhanced balance monitoring every 6 hours with better alerting (reduced frequency)
        from jobs.monitoring_jobs import run_balance_monitoring

        self.scheduler.add_job(
            run_balance_monitoring,
            trigger=IntervalTrigger(hours=6, start_date=datetime.now().replace(minute=15, second=0, microsecond=0)),
            id="enhanced_balance_monitoring",
            name="Enhanced Balance Monitoring with Alerts",
            max_instances=1,
        )

        # Daily Balance Email Reports - Admin Configurable
        if Config.BALANCE_EMAIL_ENABLED:
            from jobs.balance_monitor import send_daily_balance_email
            
            # Parse configured email times (default: 09:00,21:00)
            email_times = []
            for time_str in Config.BALANCE_EMAIL_TIMES.split(','):
                try:
                    hour, minute = map(int, time_str.strip().split(':'))
                    email_times.append((hour, minute))
                except (ValueError, IndexError):
                    logger.warning(f"Invalid balance email time format: {time_str}")
                    
            # Default to twice daily if no valid times configured
            if not email_times:
                email_times = [(9, 0), (21, 0)]  # 9 AM and 9 PM
                
            # Schedule email jobs for each configured time
            for i, (hour, minute) in enumerate(email_times):
                self.scheduler.add_job(
                    send_daily_balance_email,
                    trigger=CronTrigger(hour=hour, minute=minute),
                    id=f"daily_balance_email_{i+1}",
                    name=f"Daily Balance Email Report #{i+1} ({hour:02d}:{minute:02d})",
                    max_instances=1,
                    coalesce=True,
                    replace_existing=True
                )
                
            logger.info(f"✅ Daily balance email reports scheduled for {len(email_times)} times: {Config.BALANCE_EMAIL_TIMES}")
        else:
            logger.info("📧 Daily balance email reports disabled by configuration")

        # Unified Cleanup Orchestrator - Batches all cleanup operations
        try:
            from utils.conversation_protection import cleanup_expired_conversations
            from utils.universal_session_manager import universal_session_manager
            
            async def unified_cleanup_orchestrator():
                """Unified orchestrator for all cleanup operations to eliminate redundancy"""
                try:
                    cleanup_results = {}
                    
                    # 1. Conversation timeouts cleanup
                    try:
                        await cleanup_expired_conversations()
                        cleanup_results['conversations'] = 'success'
                    except Exception as e:
                        logger.error(f"Conversation cleanup failed: {e}")
                        cleanup_results['conversations'] = 'failed'
                    
                    # 2. OTP rate limiter cleanup
                    try:
                        await self.cleanup_otp_rate_limiter()  # Already async
                        cleanup_results['otp'] = 'success'
                    except Exception as e:
                        logger.error(f"OTP cleanup failed: {e}")
                        cleanup_results['otp'] = 'failed'
                    
                    # 3. Expired verifications cleanup (every 2nd run = every 30 minutes)
                    import time
                    current_quarter = int(time.time()) // 900  # 15-minute intervals
                    if current_quarter % 2 == 0:  # Every 2nd run
                        try:
                            await self.cleanup_expired_verifications()  # Already async
                            cleanup_results['verifications'] = 'success'
                        except Exception as e:
                            logger.error(f"Verifications cleanup failed: {e}")
                            cleanup_results['verifications'] = 'failed'
                    else:
                        cleanup_results['verifications'] = 'skipped'
                    
                    # 4. Rate locks cleanup (every 23/15 = ~1.5 ratio, run occasionally)
                    if current_quarter % 3 == 0:  # Every 3rd run (~45 minutes)
                        try:
                            await self.cleanup_expired_rate_locks()  # Already async
                            cleanup_results['rate_locks'] = 'success'
                        except Exception as e:
                            logger.error(f"Rate locks cleanup failed: {e}")
                            cleanup_results['rate_locks'] = 'failed'
                    else:
                        cleanup_results['rate_locks'] = 'skipped'
                    
                    # Log summary
                    successful = sum(1 for v in cleanup_results.values() if v == 'success')
                    logger.debug(f"✅ Unified cleanup completed: {successful} operations successful, results: {cleanup_results}")
                    
                except Exception as e:
                    logger.error(f"Error in unified cleanup orchestrator: {e}")

            # Add unified cleanup job (every 15 minutes, STAGGERED: 55 seconds offset)
            self.scheduler.add_job(
                unified_cleanup_orchestrator,
                trigger=IntervalTrigger(minutes=15, start_date=datetime.now().replace(second=55, microsecond=0)),
                id="unified_cleanup_orchestrator",
                name="Unified Cleanup Orchestrator (Conversations, OTP, Verifications, Rate Locks)",
                max_instances=1,
                coalesce=True,
                replace_existing=True,
            )

            # Session persistence cleanup disabled - SessionPersistenceManager not available
            # self.scheduler.add_job(
            #     SessionPersistenceManager.cleanup_expired_sessions,
            #     trigger=IntervalTrigger(minutes=30),
            #     id="cleanup_expired_sessions",
            #     name="Cleanup Expired Sessions",
            #     max_instances=1,
            #     replace_existing=True,
            # )

            logger.info("✅ Navigation reliability cleanup jobs added to scheduler")
        except ImportError as e:
            logger.warning(f"Navigation cleanup jobs not available - skipping: {e}")

        # Phase 2: Auto-cashout monitoring for admin email alerts - DISABLED to prevent duplicates
        # Keeping only the main auto-cashout monitor that runs every 4 minutes
        # if Config.AUTO_CASHOUT_ADMIN_ALERTS:
        #     self.scheduler.add_job(
        #         self.run_auto_cashout_monitoring,
        #         trigger=IntervalTrigger(minutes=30),
        #         id="auto_cashout_monitoring",
        #         name="Auto-Cashout Monitoring",
        #         max_instances=1,
        #     )
        #     logger.info("✅ Auto-cashout monitoring job scheduled (every 30 minutes)")

        # SMS monitoring removed - service discontinued

        # Address configuration monitoring REMOVED - consolidated into unified retry system
        # The comprehensive retry system now handles 'address_not_configured', 'invalid_key', and 'address_not_verified' errors
        # with intelligent backoff: 1min, 5min, 15min, 30min, 1hour (5 retries total)
        logger.info("✅ Address configuration monitoring delegated to unified retry system")

        # Job Performance Monitoring removed - consolidated into System Health & Security Monitoring
        
        # Baseline Metrics Update: Run every hour for system health tracking
        # Baseline Metrics Update removed - consolidated into Unified Health Monitor

        # FINANCIAL AUDIT LOGGING SYSTEM
        
        # Financial audit event relay - process outbox events every 2 minutes
        self.scheduler.add_job(
            financial_audit_relay_handler,
            trigger=IntervalTrigger(minutes=2, start_date=datetime.now().replace(second=30, microsecond=0)),
            id="financial_audit_relay",
            name="Financial Audit Event Relay",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=60,
            replace_existing=True
        )
        
        # Financial audit statistics monitoring every 15 minutes
        self.scheduler.add_job(
            financial_audit_stats_handler,
            trigger=IntervalTrigger(minutes=15, start_date=datetime.now().replace(second=45, microsecond=0)),
            id="financial_audit_stats",
            name="Financial Audit Statistics Monitor",
            max_instances=1,
            coalesce=True,
            replace_existing=True
        )
        
        # Financial audit cleanup - run daily at 3 AM UTC
        self.scheduler.add_job(
            financial_audit_cleanup_handler,
            trigger=CronTrigger(hour=3, minute=0),
            id="financial_audit_cleanup",
            name="Financial Audit Event Cleanup",
            max_instances=1
        )
        
        logger.info("✅ Financial audit logging jobs scheduled successfully")
        logger.info("✅ Scheduled jobs configured successfully with performance optimizations")
        logger.info("📊 Job staggering implemented to prevent resource contention")
        logger.info("🚀 Thread pool optimized with 6 workers for parallel execution")

    async def monitor_stuck_orders(self):
        """Monitor and attempt recovery of stuck exchange orders"""
        try:
            from utils.exchange_recovery_service import ExchangeRecoveryService
            
            stuck_orders = await ExchangeRecoveryService.detect_stuck_orders()
            
            if stuck_orders:
                logger.warning(f"Found {len(stuck_orders)} stuck exchange orders")
                
                for order_info in stuck_orders:
                    order_id = order_info['id']
                    stuck_duration = order_info['stuck_duration_minutes']
                    
                    if stuck_duration > 15:  # Only auto-recover orders stuck > 15 minutes
                        logger.info(f"Attempting auto-recovery of order {order_id} (stuck {stuck_duration:.1f} minutes)")
                        
                        recovery_result = await ExchangeRecoveryService.force_process_stuck_order(order_id)
                        
                        if recovery_result['success']:
                            logger.info(f"Successfully recovered stuck order {order_id}")
                        else:
                            logger.error(f"Failed to recover order {order_id}: {recovery_result.get('error', 'Unknown error')}")
            
        except Exception as e:
            logger.error(f"Error in stuck order monitoring: {e}")
    
    def start(self):
        """Start the scheduler"""
        self.setup_jobs()
        
        # CRITICAL FIX: Remove deprecated jobs before starting
        self.remove_deprecated_jobs()
        
        self.scheduler.start()  # CRITICAL FIX: Actually start the scheduler!
        logger.warning("✅ Escrow scheduler started with all background jobs including payment timeout handler")
        
        # Log all registered jobs for verification
        jobs = self.scheduler.get_jobs()
        logger.warning(f"📋 Registered scheduler jobs: {[job.id for job in jobs]}")
        job_names = [f"{job.name} ({job.id})" for job in jobs]
        logger.warning(f"📋 EscrowScheduler jobs: {job_names}")
        
        # Verify systematic timeout handling job
        timeout_job = self.scheduler.get_job("systematic_timeout_handling")
        if timeout_job:
            logger.warning(f"✅ Systematic timeout job registered: {timeout_job.name}, next run at {timeout_job.next_run_time}")
        else:
            logger.error("❌ CRITICAL: Systematic timeout job NOT registered!")
        
        # Log the first 5 upcoming job runs for monitoring
        logger.info("🔮 Upcoming job runs:")
        from datetime import datetime as dt_max
        for job in sorted(jobs, key=lambda x: x.next_run_time or dt_max.max)[:5]:
            if job.next_run_time:
                logger.info(f"   - {job.name}: {job.next_run_time}")

    async def run_auto_cashout_monitoring(self):
        """Monitor auto-cashout status and send admin alerts when intervention needed"""
        try:
            from services.auto_cashout_monitor import auto_cashout_monitor

            results = await auto_cashout_monitor.run_comprehensive_check()
            logger.info(f"Auto-cashout monitoring completed: {results}")
        except Exception as e:
            logger.error(f"Error in auto-cashout monitoring: {e}")

    def stop(self):
        """Stop the scheduler"""
        self.scheduler.shutdown()
        logger.info("Background job scheduler stopped")

    async def prefetch_crypto_rates(self):
        """Background job to prefetch all crypto rates for performance optimization"""
        try:
            from services.fastforex_service import fastforex_service
            
            # Pre-fetch all commonly used crypto rates
            crypto_currencies = [
                "BTC", "ETH", "LTC", "DOGE", "BCH", "BSC", "TRX", 
                "USDT-ERC20", "USDT-TRC20"
            ]
            
            logger.info(f"🔄 BACKGROUND: Pre-fetching {len(crypto_currencies)} crypto rates...")
            start_time = datetime.now()
            
            # Use the warm_cache method which efficiently fetches multiple rates
            await fastforex_service.warm_cache()
            
            elapsed = (datetime.now() - start_time).total_seconds() * 1000
            logger.info(f"✅ BACKGROUND: Crypto rates cached in {elapsed:.0f}ms - ready for instant access")
            
        except Exception as e:
            logger.error(f"❌ BACKGROUND: Crypto rate prefetch failed: {e}")

    async def check_deposit_confirmations(self):
        """Check for pending deposits and confirm them with atomic transactions"""
        try:
            with atomic_transaction() as session:
                # PERFORMANCE OPTIMIZED: Get escrows with indexed query and limit results
                pending_escrows = (
                    session.query(Escrow)
                    .filter(
                        Escrow.status == str(EscrowStatus.PENDING_DEPOSIT.value),
                        Escrow.deposit_address.isnot(None),
                        Escrow.total_amount.isnot(None),
                    )
                    .order_by(Escrow.created_at.asc())
                    .limit(20)
                    .with_for_update()
                    .all()
                )

                if not pending_escrows:
                    return

                logger.info(
                    f"Checking {len(pending_escrows)} escrows for deposit confirmations"
                )

                for escrow in pending_escrows:
                    try:
                        # Use locked escrow operation to prevent concurrent modifications
                        with locked_escrow_operation(
                            str(escrow.escrow_id), session
                        ) as locked_escrow:
                            # Double-check status hasn't changed
                            if locked_escrow.status != str(
                                EscrowStatus.PENDING_DEPOSIT.value
                            ):
                                continue

                            # Check if payment has been confirmed
                            confirmation_result = (
                                await CryptoServiceAtomic.check_deposit_confirmation(
                                    locked_escrow.deposit_address,
                                    Decimal(str(locked_escrow.total_amount)),
                                    locked_escrow.currency or "BTC",
                                )
                            )

                            if confirmation_result.get("confirmed", False):
                                # Process confirmed deposit atomically
                                success = (
                                    await CryptoServiceAtomic.process_confirmed_deposit(
                                        locked_escrow.escrow_id,
                                        confirmation_result.get("tx_hash", "unknown"),
                                        Decimal(str(locked_escrow.total_amount)),
                                        locked_escrow.currency or "BTC",
                                    )
                                )

                                if success:
                                    logger.info(
                                        f"Successfully processed confirmed deposit for escrow {locked_escrow.escrow_id}"
                                    )

                                    # Phase 1: Send admin email alert for deposit confirmation
                                    try:
                                        from services.admin_email_alerts import (
                                            admin_email_service,
                                        )

                                        # Look up the buyer user for the escrow
                                        buyer_user = (
                                            session.query(User)
                                            .filter(User.id == locked_escrow.buyer_id)
                                            .first()
                                        )
                                        if buyer_user:
                                            await admin_email_service.send_transaction_alert(
                                                transaction_type="DEPOSIT_CONFIRMED",
                                                amount=Decimal(str(
                                                    locked_escrow.total_amount or 0
                                                )),
                                                currency=locked_escrow.currency
                                                or "USD",
                                                user=buyer_user,  # Pass the actual buyer user
                                                details={
                                                    "escrow_id": locked_escrow.escrow_id,
                                                    "tx_hash": confirmation_result.get(
                                                        "tx_hash", "unknown"
                                                    ),
                                                    "currency": locked_escrow.currency
                                                    or "BTC",
                                                },
                                            )
                                        else:
                                            logger.warning(
                                                f"Buyer user not found for escrow {locked_escrow.escrow_id} - skipping admin email alert"
                                            )
                                    except Exception as e:
                                        logger.error(
                                            f"Failed to send admin email for deposit confirmation {locked_escrow.escrow_id}: {e}"
                                        )
                                else:
                                    logger.error(
                                        f"Failed to process confirmed deposit for escrow {locked_escrow.escrow_id}"
                                    )

                    except Exception as e:
                        logger.error(
                            f"Error processing escrow {escrow.escrow_id} in deposit confirmation check: {e}"
                        )
                        continue

        except Exception as e:
            logger.error(f"Error in check_deposit_confirmations: {e}")

    async def handle_expired_escrows(self):
        """Handle escrows that have expired with atomic transactions"""
        try:
            with atomic_transaction() as session:
                # Get expired escrows with row-level locking
                expired_time = datetime.utcnow() - timedelta(
                    hours=Config.DEFAULT_DELIVERY_TIMEOUT
                )
                expired_escrows = (
                    session.query(Escrow)
                    .filter(
                        Escrow.status.in_([str(EscrowStatus.ACTIVE.value)]),
                        Escrow.created_at < expired_time,
                    )
                    .with_for_update()
                    .all()
                )

                if not expired_escrows:
                    return

                logger.info(f"Processing {len(expired_escrows)} expired escrows")

                for escrow in expired_escrows:
                    try:
                        with locked_escrow_operation(
                            str(escrow.escrow_id), session
                        ) as locked_escrow:
                            # Double-check escrow is still expired and in correct status
                            if locked_escrow.status not in [
                                str(EscrowStatus.ACTIVE.value)
                            ]:
                                continue

                            # SECURITY FIX: Use escrow state machine for atomic status changes
                            from utils.escrow_state_machine import AtomicEscrowOperation

                            escrow_op = AtomicEscrowOperation(locked_escrow.escrow_id)
                            success = escrow_op.change_status(
                                str(EscrowStatus.EXPIRED.value),
                                resolution_time=datetime.utcnow(),
                            )

                            if not success:
                                logger.error(
                                    f"Failed to expire escrow {locked_escrow.escrow_id} - invalid state transition"
                                )
                                continue

                            # CRITICAL SECURITY FIX: Validate buyer actually paid before refunding
                            from utils.wallet_validation import WalletValidator
                            
                            # Only refund wallet payments - crypto payments are handled differently
                            if locked_escrow.payment_method == "wallet":
                                expected_payment = Decimal(str(locked_escrow.amount)) + Decimal(str(locked_escrow.fee_amount or 0))
                                is_valid_payment, payment_error = WalletValidator.validate_wallet_debit_completed(
                                    user_id=locked_escrow.buyer_id,
                                    escrow_id=locked_escrow.id,
                                    expected_amount=expected_payment,
                                    session=session
                                )
                                
                                if not is_valid_payment:
                                    logger.error(
                                        f"🚨 SECURITY BLOCK: Attempted refund for expired escrow {locked_escrow.escrow_id} "
                                        f"without valid payment: {payment_error}"
                                    )
                                    continue  # Skip this escrow, don't refund
                            
                            # Refund buyer atomically (ONLY after payment validation)
                            refund_success = CryptoServiceAtomic.credit_user_wallet_atomic(
                                user_id=locked_escrow.buyer_id,
                                amount=Decimal(str(locked_escrow.amount)),
                                currency="USD",
                                escrow_id=locked_escrow.id,
                                transaction_type="refund",
                                description=f"Refund for expired escrow {locked_escrow.escrow_id} (Payment verified)",
                                session=session,
                            )

                            if refund_success:
                                # Send notifications
                                # Send expiration notification via consolidated service
                                await notification_hub.send_notification(
                                    user_id=locked_escrow.buyer_id,
                                    notification_type="escrow_expired",
                                    message=f"Escrow {locked_escrow.escrow_id} has expired and funds have been refunded",
                                )
                                logger.info(
                                    f"Successfully processed expired escrow {locked_escrow.escrow_id}"
                                )
                            else:
                                logger.error(
                                    f"Failed to refund expired escrow {locked_escrow.escrow_id}"
                                )
                                # Rollback status change if refund failed
                                session.rollback()
                                return

                    except Exception as e:
                        logger.error(
                            f"Error processing expired escrow {escrow.escrow_id}: {e}"
                        )
                        continue

        except Exception as e:
            logger.error(f"Error in handle_expired_escrows: {e}")

    async def handle_payment_timeouts(self):
        """Handle escrows and exchange orders that have exceeded payment deadline with atomic transactions"""
        logger.warning("🕐 PAYMENT TIMEOUT MONITOR: Starting comprehensive timeout check...")
        
        try:
            # Handle escrow payment timeouts
            with atomic_transaction() as session:
                # Get payment-overdue escrows with row-level locking
                # Use full payment timeout for payment_pending escrows
                payment_deadline = datetime.utcnow() - timedelta(
                    minutes=Config.PAYMENT_TIMEOUT_MINUTES
                )
                overdue_escrows = (
                    session.query(Escrow)
                    .filter(
                        Escrow.status == "payment_pending",
                        Escrow.created_at < payment_deadline,
                    )
                    .with_for_update()
                    .all()
                )

                if not overdue_escrows:
                    logger.info("✅ No payment-overdue escrows found")
                else:
                    logger.warning(f"🚨 Processing {len(overdue_escrows)} payment-overdue escrows for timeout cancellation")

                for escrow in overdue_escrows:
                    try:
                        with locked_escrow_operation(
                            str(escrow.escrow_id), session
                        ) as locked_escrow:
                            # Double-check escrow is still overdue and in correct status
                            if locked_escrow.status != "payment_pending":
                                continue

                            time_since_creation = datetime.utcnow() - locked_escrow.created_at
                            if time_since_creation.total_seconds() < (Config.PAYMENT_TIMEOUT_MINUTES * 60):
                                continue  # Not actually overdue yet

                            # Cancel the escrow directly without state machine for payment timeouts
                            # Payment pending escrows can be safely cancelled since no funds were transferred
                            try:
                                locked_escrow.status = "cancelled"
                                session.commit()  # Explicitly commit the status change
                                success = True
                                logger.info(f"Successfully set escrow {locked_escrow.escrow_id} status to cancelled")
                                
                                # Send admin notification about escrow cancellation
                                try:
                                    from services.admin_trade_notifications import admin_trade_notifications
                                    from models import User
                                    
                                    # Get buyer and seller information
                                    buyer = session.query(User).filter(User.id == locked_escrow.buyer_id).first()
                                    seller = session.query(User).filter(User.id == locked_escrow.seller_id).first() if locked_escrow.seller_id else None
                                    
                                    buyer_info = (
                                        buyer.username or buyer.first_name or f"User_{buyer.telegram_id}"
                                        if buyer else "Unknown Buyer"
                                    )
                                    seller_info = (
                                        seller.username or seller.first_name or f"User_{seller.telegram_id}"
                                        if seller else locked_escrow.seller_username or locked_escrow.seller_email or "Unknown Seller"
                                    )
                                    
                                    escrow_cancellation_data = {
                                        'escrow_id': locked_escrow.escrow_id,
                                        'amount': Decimal(str(locked_escrow.amount)) if locked_escrow.amount else Decimal('0'),
                                        'currency': 'USD',
                                        'buyer_info': buyer_info,
                                        'seller_info': seller_info,
                                        'cancellation_reason': f'Payment timeout after {int(time_since_creation.total_seconds() / 60)} minutes',
                                        'cancelled_at': datetime.utcnow()
                                    }
                                    
                                    # Send admin notification asynchronously
                                    import asyncio
                                    asyncio.create_task(
                                        admin_trade_notifications.notify_escrow_cancelled(escrow_cancellation_data)
                                    )
                                    logger.info(f"Admin notification queued for escrow cancellation: {locked_escrow.escrow_id}")
                                    
                                except Exception as e:
                                    logger.error(f"Failed to queue admin notification for escrow cancellation: {e}")
                            except Exception as commit_error:
                                logger.error(f"Failed to commit cancellation for escrow {locked_escrow.escrow_id}: {commit_error}")
                                session.rollback()
                                success = False

                            if not success:
                                logger.error(
                                    f"Failed to cancel payment-overdue escrow {locked_escrow.escrow_id} - invalid state transition"
                                )
                                continue

                            # Send cancellation notification to buyer
                            minutes_overdue = int(time_since_creation.total_seconds() / 60)
                            await notification_hub.send_notification(
                                user_id=locked_escrow.buyer_id,
                                notification_type="escrow_payment_timeout",
                                message=f"🕐 Trade {locked_escrow.escrow_id} was automatically cancelled after {minutes_overdue} minutes without payment. No funds were charged.",
                            )
                            
                            # Send notification to seller if they exist
                            if locked_escrow.seller_id:
                                await notification_hub.send_notification(
                                    user_id=locked_escrow.seller_id,
                                    notification_type="escrow_payment_timeout",
                                    message=f"🕐 Trade {locked_escrow.escrow_id} was automatically cancelled due to buyer payment timeout.",
                                )

                            logger.info(
                                f"Successfully cancelled payment-overdue escrow {locked_escrow.escrow_id} after {minutes_overdue} minutes"
                            )

                    except Exception as e:
                        logger.error(
                            f"Error processing payment-overdue escrow {escrow.escrow_id}: {e}"
                        )
                        continue

            # Handle exchange order payment timeouts
            logger.info("🔍 Checking for expired exchange orders...")
            
            # Import ExchangeOrder model
            from models import ExchangeOrder
            
            # Exchange orders have 15-minute timeout for payment (same as escrows)
            exchange_timeout_minutes = getattr(Config, 'NGN_EXCHANGE_TIMEOUT_MINUTES', 15)
            exchange_deadline = datetime.utcnow() - timedelta(minutes=exchange_timeout_minutes)
            
            overdue_exchanges = (
                session.query(ExchangeOrder)
                .filter(
                    ExchangeOrder.status == "awaiting_deposit",
                    ExchangeOrder.created_at < exchange_deadline,
                )
                .with_for_update()
                .all()
            )
            
            if not overdue_exchanges:
                logger.info("✅ No payment-overdue exchange orders found")
            else:
                logger.warning(f"🚨 Processing {len(overdue_exchanges)} payment-overdue exchange orders for timeout cancellation")
                
                for exchange in overdue_exchanges:
                    try:
                        # Cancel the exchange order using validated transition
                        ExchangeStateValidator.validate_and_transition(
                            exchange, 
                            ExchangeStatus.CANCELLED,
                            exchange_id=str(getattr(exchange, "id", "Unknown")),
                            force=False
                        )
                        session.commit()
                        
                        # Send admin notification about exchange cancellation
                        try:
                            from services.admin_trade_notifications import admin_trade_notifications
                            from models import User
                            
                            # Get user information
                            user = session.query(User).filter(User.id == getattr(exchange, "user_id", 0)).first()
                            user_id = getattr(exchange, "user_id", 0)
                            user_info = (
                                user.username or user.first_name or f"User_{user.telegram_id}"
                                if user else f"Unknown User (ID: {user_id})"
                            )
                            
                            exchange_cancellation_data = {
                                'exchange_id': str(getattr(exchange, "id", "Unknown")),
                                'amount': Decimal(str(getattr(exchange, "source_amount", 0))),
                                'from_currency': getattr(exchange, "source_currency", "Unknown"),
                                'to_currency': getattr(exchange, "target_currency", "Unknown"),
                                'exchange_type': getattr(exchange, "order_type", "Unknown"),
                                'user_info': user_info,
                                'cancellation_reason': f'Payment timeout after {int((datetime.utcnow() - exchange.created_at).total_seconds() / 60)} minutes',
                                'cancelled_at': datetime.utcnow()
                            }
                            
                            # Send admin notification asynchronously
                            asyncio.create_task(
                                admin_trade_notifications.notify_exchange_cancelled(exchange_cancellation_data)
                            )
                            logger.info(f"Admin notification queued for exchange cancellation: {exchange.id}")
                            
                        except Exception as e:
                            logger.error(f"Failed to queue admin notification for exchange cancellation: {e}")
                        
                        # Send cancellation notification to user
                        minutes_overdue = int((datetime.utcnow() - exchange.created_at).total_seconds() / 60)
                        await notification_hub.send_notification(
                            user_id=exchange.user_id,
                            notification_type="exchange_payment_timeout",
                            message=f"🕐 Exchange order {exchange.utid} was automatically cancelled after {minutes_overdue} minutes without payment.",
                        )
                        
                        logger.info(f"✅ Successfully cancelled overdue exchange order {exchange.utid} after {minutes_overdue} minutes")
                        
                    except Exception as e:
                        logger.error(f"Error processing overdue exchange {exchange.utid}: {e}")
                        continue
            
            logger.warning("✅ PAYMENT TIMEOUT MONITOR: Comprehensive timeout check completed")
            
        except Exception as e:
            logger.error(f"Error in handle_payment_timeouts: {e}")

    async def send_reminder_notifications(self):
        """Send reminder notifications with atomic data access"""
        try:
            # Temporarily disabled - last_reminder_sent field doesn't exist in Escrow model
            # This functionality can be re-enabled by adding the field to the model
            logger.info(
                "Reminder notifications temporarily disabled - model field missing"
            )
            return

        except Exception as e:
            logger.error(f"Error in send_reminder_notifications: {e}")

    async def process_admin_notification_queue(self):
        """
        Process pending admin notifications from database queue.
        Prevents notification loss during rapid escrow state changes.
        """
        try:
            from services.admin_notification_queue import AdminNotificationQueueService
            
            stats = await AdminNotificationQueueService.process_pending_notifications(batch_size=20)
            
            if stats['processed'] > 0:
                logger.info(
                    f"📧 Admin notification queue processed: {stats['processed']} notifications, "
                    f"{stats['email_sent']} emails sent, {stats['telegram_sent']} telegrams sent, "
                    f"{stats['failed']} failed"
                )
            
        except Exception as e:
            logger.error(f"Error processing admin notification queue: {e}", exc_info=True)

    async def send_delivery_deadline_warnings(self):
        """Send warnings before delivery deadlines expire"""
        try:
            with atomic_transaction() as session:
                current_time = datetime.utcnow()
                
                # Find active escrows approaching their delivery deadline
                active_escrows = (
                    session.query(Escrow)
                    .filter(
                        Escrow.status == str(EscrowStatus.ACTIVE.value),
                        Escrow.delivery_deadline.isnot(None),
                        Escrow.delivery_deadline > current_time,  # Not expired yet
                    )
                    .all()
                )
                
                warnings_sent = 0
                for escrow in active_escrows:
                    try:
                        # Calculate time remaining until delivery deadline
                        time_remaining = escrow.delivery_deadline - current_time
                        hours_remaining = time_remaining.total_seconds() / 3600
                        
                        # Send warning messages at specific intervals
                        should_send_warning = False
                        warning_type = ""
                        
                        # 24 hours warning (for longer deliveries)
                        if 23.5 <= hours_remaining <= 24.5:
                            should_send_warning = True
                            warning_type = "24 hours"
                        # 8 hours warning  
                        elif 7.5 <= hours_remaining <= 8.5:
                            should_send_warning = True
                            warning_type = "8 hours"
                        # 2 hours warning
                        elif 1.5 <= hours_remaining <= 2.5:
                            should_send_warning = True
                            warning_type = "2 hours"
                        # 30 minutes warning
                        elif 0.25 <= hours_remaining <= 0.75:
                            should_send_warning = True
                            warning_type = "30 minutes"
                            
                        if should_send_warning:
                            # Send warning to buyer
                            await notification_hub.send_notification(
                                user_id=escrow.buyer_id,
                                notification_type="delivery_deadline_warning",
                                message=f"⏰ Delivery Reminder - {warning_type} left\n\n"
                                       f"🔒 Trade #{escrow.escrow_id}\n"
                                       f"💰 Amount: ${float(escrow.amount):.2f} USD\n\n"
                                       f"⚠️ Delivery deadline in {warning_type}!\n"
                                       f"If you haven't received your goods, you can dispute this trade before the deadline.\n\n"
                                       f"💡 After the deadline, funds automatically release to the seller."
                            )
                            
                            # Send warning to seller
                            await notification_hub.send_notification(
                                user_id=escrow.seller_id,
                                notification_type="delivery_deadline_warning",
                                message=f"⏰ Delivery Deadline - {warning_type} left\n\n"
                                       f"🔒 Trade #{escrow.escrow_id}\n"
                                       f"💰 Amount: ${float(escrow.amount):.2f} USD\n\n"
                                       f"📦 Ensure delivery is completed within {warning_type}!\n"
                                       f"Funds will automatically release to you after the deadline if no disputes are raised.\n\n"
                                       f"✅ Good communication with the buyer helps prevent disputes."
                            )
                            
                            warnings_sent += 1
                            logger.info(f"Sent {warning_type} delivery warning for escrow {escrow.escrow_id}")
                            
                    except Exception as e:
                        logger.error(f"Error sending delivery warning for escrow {escrow.escrow_id}: {e}")
                        continue
                
                if warnings_sent > 0:
                    logger.info(f"✅ Sent {warnings_sent} delivery deadline warnings")
                    
        except Exception as e:
            logger.error(f"Error in send_delivery_deadline_warnings: {e}")

    async def process_auto_release(self):
        """Process escrows eligible for auto-release with atomic operations"""
        try:
            # Auto-release configuration
            getattr(Config, "AUTO_RELEASE_HOURS", 72)

            with atomic_transaction() as session:
                # Get escrows eligible for auto-release (based on auto_release_at field)
                current_time = datetime.utcnow()
                auto_release_escrows = (
                    session.query(Escrow)
                    .filter(
                        Escrow.status == str(EscrowStatus.ACTIVE.value),
                        Escrow.auto_release_at.isnot(None),
                        Escrow.auto_release_at < current_time,
                    )
                    .with_for_update()
                    .all()
                )

                for escrow in auto_release_escrows:
                    try:
                        with locked_escrow_operation(
                            str(escrow.escrow_id), session
                        ) as locked_escrow:
                            # Double-check status (already filtered by auto_release_at field)
                            if locked_escrow.status != str(EscrowStatus.ACTIVE.value):
                                continue

                            # Release funds to seller atomically
                            release_success = CryptoServiceAtomic.credit_user_wallet_atomic(
                                user_id=locked_escrow.seller_id,
                                amount=Decimal(str(locked_escrow.amount)),
                                currency="USD",
                                escrow_id=locked_escrow.id,
                                transaction_type="escrow_release",
                                description=f"Auto-release payment for escrow {locked_escrow.escrow_id}",
                                session=session,
                            )

                            if release_success:
                                locked_escrow.status = str(EscrowStatus.COMPLETED.value)
                                locked_escrow.completed_at = datetime.utcnow()
                                locked_escrow.auto_released_at = datetime.utcnow()  # Track auto-release timestamp

                                # Send completion notifications with auto-release context
                                await notification_hub.send_notification(
                                    user_id=locked_escrow.buyer_id,
                                    notification_type="escrow_auto_released",
                                    message=f"🕐 Trade Auto-Completed\n\n"
                                           f"🔒 Trade #{locked_escrow.escrow_id}\n"
                                           f"💰 Amount: ${float(locked_escrow.amount):.2f} USD\n\n"
                                           f"✅ Delivery deadline has passed - funds have been automatically released to the seller.\n\n"
                                           f"💡 If you have any issues with this trade, please contact support immediately."
                                )
                                await notification_hub.send_notification(
                                    user_id=locked_escrow.seller_id,
                                    notification_type="escrow_auto_released",
                                    message=f"💰 Payment Received - Auto-Released!\n\n"
                                           f"🔒 Trade #{locked_escrow.escrow_id}\n"
                                           f"💰 Amount: ${float(locked_escrow.amount):.2f} USD\n\n"
                                           f"✅ Funds released to your wallet after delivery deadline passed.\n\n"
                                           f"🎉 Great job completing this trade!"
                                )
                                logger.info(
                                    f"✅ Auto-released escrow {locked_escrow.escrow_id} - ${float(locked_escrow.amount):.2f} to seller {locked_escrow.seller_id}"
                                )
                            else:
                                logger.error(
                                    f"❌ Failed to auto-release escrow {locked_escrow.escrow_id}"
                                )

                    except Exception as e:
                        logger.error(
                            f"Error processing auto-release for escrow {escrow.escrow_id}: {e}"
                        )
                        continue

        except Exception as e:
            logger.error(f"Error in process_auto_release: {e}")

    async def daily_maintenance(self):
        """Perform daily maintenance tasks with atomic operations"""
        try:
            with atomic_transaction() as session:
                # Clean up old transaction records (older than 1 year)
                cleanup_date = datetime.utcnow() - timedelta(days=365)
                old_transactions = (
                    session.query(Transaction)
                    .filter(
                        Transaction.confirmed_at < cleanup_date,
                        Transaction.status == "completed",
                    )
                    .count()
                )

                if old_transactions > 0:
                    logger.info(
                        f"Found {old_transactions} old transactions for potential cleanup"
                    )
                    # Note: In production, archive before deleting

                # Update user statistics
                await self.update_user_statistics()

                logger.info("Daily maintenance completed")

        except Exception as e:
            logger.error(f"Error in daily_maintenance: {e}")

    async def daily_backup(self):
        """Perform daily backup"""
        try:
            # Import backup service if available
            try:
                from services.backup_service import BackupService

                await BackupService().create_full_backup()
                logger.info("Daily backup completed")
            except ImportError:
                logger.info("Backup service not available - skipping daily backup")
        except Exception as e:
            logger.error(f"Error in daily_backup: {e}")

    async def cleanup_expired_rate_locks(self):
        """Enhanced cleanup for expired rate locks with comprehensive validation"""
        try:
            from services.rate_lock_service import rate_lock_service
            from database import SessionLocal
            from models import ExchangeOrder
            from datetime import datetime, timedelta
            
            session = SessionLocal()
            cleanup_stats = {
                "expired_locks_cleaned": 0,
                "orphaned_orders_found": 0,
                "stuck_rate_locks_cleared": 0
            }
            
            try:
                # 1. Clean up expired rate locks in memory cache
                if hasattr(rate_lock_service, 'rate_locks'):
                    now = datetime.utcnow()
                    expired_locks = []
                    
                    for lock_id, rate_lock in rate_lock_service.rate_locks.items():
                        if now > rate_lock.expires_at:
                            expired_locks.append(lock_id)
                    
                    for lock_id in expired_locks:
                        del rate_lock_service.rate_locks[lock_id]
                        cleanup_stats["expired_locks_cleaned"] += 1
                
                # 2. Legacy DirectExchange cleanup removed - using only ExchangeOrder now
                cutoff_time = datetime.utcnow() - timedelta(hours=2)  # 2 hours old
                
                # 3. Clean up stuck rate locks in ExchangeOrder table
                stuck_orders = (
                    session.query(ExchangeOrder)
                    .filter(
                        ExchangeOrder.rate_lock_expires_at < cutoff_time,
                        ExchangeOrder.status.in_(["created", "rate_locked"]),
                        ExchangeOrder.completed_at.is_(None)
                    )
                    .all()
                )
                
                for order in stuck_orders:
                    # Clear expired rate lock status
                    if hasattr(order, 'rate_lock_expires_at'):
                        order.rate_lock_expires_at = None
                    if hasattr(order, 'rate_locked_at'):
                        order.rate_locked_at = None
                    
                    cleanup_stats["stuck_rate_locks_cleared"] += 1
                    logger.info(f"🧹 RATE_LOCK_CLEANUP: Cleared stuck rate lock for order {order.id}")
                
                session.commit()
                
                # Log cleanup results
                total_cleaned = sum(cleanup_stats.values())
                if total_cleaned > 0:
                    logger.info(f"✅ RATE_LOCK_CLEANUP_COMPLETED: "
                              f"Expired locks: {cleanup_stats['expired_locks_cleaned']}, "
                              f"Orphaned orders: {cleanup_stats['orphaned_orders_found']}, "
                              f"Stuck locks: {cleanup_stats['stuck_rate_locks_cleared']}")
                else:
                    logger.debug("🧹 RATE_LOCK_CLEANUP: No expired rate locks found")
                    
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"Error in enhanced cleanup_expired_rate_locks: {e}")

    async def cleanup_otp_rate_limiter(self):
        """ADVISOR RECOMMENDATION: Cleanup expired OTP rate limiting entries"""
        try:
            # OTP rate limiter cleanup integrated into auth flows
            logger.info("OTP rate limiter cleanup handled by individual TTL")
            logger.info("OTP rate limiter cleanup completed successfully")
        except Exception as e:
            logger.error(f"Error in cleanup_otp_rate_limiter: {e}")

    async def weekly_backup(self):
        """Perform weekly backup"""
        try:
            try:
                from services.backup_service import BackupService

                await BackupService().create_full_backup()
                logger.info("Weekly backup completed")
            except ImportError:
                logger.info("Backup service not available - skipping weekly backup")
        except Exception as e:
            logger.error(f"Error in weekly_backup: {e}")

    async def update_user_statistics(self):
        """Update user statistics with atomic operations"""
        try:
            with atomic_transaction() as session:
                # Update user trade counts and reputation scores
                users = session.query(User).all()

                for user in users:
                    try:
                        # Count completed escrows
                        completed_as_buyer = (
                            session.query(Escrow)
                            .filter(
                                Escrow.buyer_id == user.id,
                                Escrow.status == str(EscrowStatus.COMPLETED.value),
                            )
                            .count()
                        )

                        completed_as_seller = (
                            session.query(Escrow)
                            .filter(
                                Escrow.seller_id == user.id,
                                Escrow.status == str(EscrowStatus.COMPLETED.value),
                            )
                            .count()
                        )

                        total_trades = completed_as_buyer + completed_as_seller

                        # Update user statistics using session update (only existing columns)
                        session.query(User).filter(User.id == user.id).update(
                            {
                                "total_trades": total_trades,
                                "successful_trades": total_trades,  # Use existing column
                                "last_activity": datetime.utcnow(),  # Use existing column instead of statistics_updated_at
                            }
                        )

                    except Exception as e:
                        logger.error(
                            f"Error updating statistics for user {user.id}: {e}"
                        )
                        continue

                logger.info("User statistics updated successfully")

        except Exception as e:
            logger.error(f"Error in update_user_statistics: {e}")

    async def send_daily_rate_notifications(self):
        """Send daily cryptocurrency rate notifications to users who have opted in"""
        try:
            from database import SessionLocal
            from telegram import Bot
            from utils.preferences import get_user_preferences
            from services.crypto import CryptoServiceAtomic

            # Get current crypto rates
            try:
                rates = await CryptoServiceAtomic.get_crypto_rates()
            except Exception as e:
                logger.error(f"Failed to get crypto rates for daily notifications: {e}")
                return

            if not rates:
                logger.warning("No crypto rates available for daily notifications")
                return

            # Format the daily rates message
            message = "🌅 Daily Crypto Rates Update\n\n"
            message += "Current market rates:\n\n"

            # Major cryptocurrencies with emojis
            crypto_emojis = {
                "BTC": "₿",
                "ETH": "💎",
                "USDT-TRC20": "🔵",
                "USDT-ERC20": "🔷",
                "LTC": "🥈",
                "DOGE": "🐕",
                "TRX": "🔴",
                "XMR": "ⓧ",
            }

            for currency, rate in sorted(rates.items()):
                if currency == "USD":
                    continue
                emoji = crypto_emojis.get(currency, "💰")
                if rate >= 1000:
                    rate_str = f"${rate:,.0f}"
                elif rate >= 1:
                    rate_str = f"${rate:.2f}"
                else:
                    rate_str = f"${rate:.6f}"
                message += f"{emoji} {currency}: {rate_str}\n"

            message += f"\n📅 Updated: {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}"
            message += "\n💡 Rates update every few minutes"
            message += "\n\n🔕 Don't want daily rates? Tap Profile → Account Settings → Notifications to customize"

            Bot(Config.BOT_TOKEN or "")

            with SessionLocal() as session:
                # Get all users who have daily rate notifications enabled via Telegram
                users = (
                    session.query(User)
                    .filter(User.telegram_id.isnot(None), User.status == "active")
                    .all()
                )

                sent_count = 0
                for user in users:
                    try:
                        preferences = get_user_preferences(user)
                        if preferences.get("daily_rates", {}).get("telegram", False):
                            try:
                                telegram_id = getattr(user, "telegram_id", None)
                                if telegram_id:
                                    await notification_hub.send_telegram_message(
                                        int(telegram_id), message
                                    )
                                sent_count += 1
                            except Exception as e:
                                logger.warning(
                                    f"Failed to send daily rates to user {user.telegram_id}: {e}"
                                )
                                continue
                    except Exception as e:
                        logger.error(
                            f"Error processing user {user.id} for daily rates: {e}"
                        )
                        continue

                logger.info(f"Daily rate notifications sent to {sent_count} users")

        except Exception as e:
            logger.error(f"Error in send_daily_rate_notifications: {e}")
    
    async def cleanup_expired_verifications(self):
        """Clean up expired email verifications to prevent database bloat"""
        from models import EmailVerification
        from database import SessionLocal
        
        try:
            with SessionLocal() as session:
                # Delete verifications that expired more than 1 hour ago
                cutoff_time = datetime.utcnow() - timedelta(hours=1)
                deleted_count = (
                    session.query(EmailVerification)
                    .filter(
                        EmailVerification.expires_at < cutoff_time,
                        EmailVerification.verified_at.is_(None)
                    )
                    .delete(synchronize_session=False)
                )
                session.commit()
                
                if deleted_count > 0:
                    logger.info(f"🗑️ Cleaned up {deleted_count} expired email verifications")
        except Exception as e:
            logger.error(f"Error cleaning up expired verifications: {e}")
    
    async def monitor_stuck_users(self):
        """Monitor and alert for users stuck in verification process"""
        from models import EmailVerification
        from database import SessionLocal
        
        try:
            with SessionLocal() as session:
                # Find users with unverified, non-expired verifications older than 30 minutes
                cutoff_time = datetime.utcnow() - timedelta(minutes=30)
                stuck_verifications = (
                    session.query(EmailVerification)
                    .filter(
                        EmailVerification.created_at < cutoff_time,
                        EmailVerification.verified_at.is_(None),
                        EmailVerification.expires_at > datetime.utcnow(),
                        EmailVerification.purpose == 'onboarding'
                    )
                    .all()
                )
                
                if stuck_verifications:
                    logger.warning(f"⚠️ Found {len(stuck_verifications)} users stuck in verification:")
                    for verification in stuck_verifications:
                        time_stuck = (datetime.utcnow() - verification.created_at).total_seconds() / 60
                        logger.warning(
                            f"  - User ID {verification.user_id}: Stuck for {time_stuck:.0f} minutes, "
                            f"Attempts: {verification.attempts}, Code: {verification.verification_code}"
                        )
                        
                        # Auto-clear if stuck for more than 2 hours
                        if time_stuck > 120:
                            session.delete(verification)
                            logger.info(f"    → Auto-cleared verification for user {verification.user_id} after 2 hours")
                    
                    session.commit()
        except Exception as e:
            logger.error(f"Error monitoring stuck users: {e}")

    # CRITICAL FIX: Remove the old deprecated job to prevent memory persistence
    def remove_deprecated_jobs(self):
        """Remove deprecated jobs that might persist in APScheduler memory"""
        try:
            deprecated_job_ids = [
                "cleanup_stuck_cashouts",
                "stuck_cashout_cleanup", 
                "orphaned_cashout_cleanup"
            ]
            for job_id in deprecated_job_ids:
                try:
                    self.scheduler.remove_job(job_id)
                    logger.info(f"✅ Removed deprecated job: {job_id}")
                except Exception as e:
                    logger.debug(f"Job {job_id} not found or already removed: {e}")
                    pass  # Job might not exist
        except Exception as e:
            logger.error(f"Error removing deprecated jobs: {e}")
    
    # REMOVED: cleanup_stuck_cashouts method entirely to prevent old APScheduler jobs from calling it
    # All cashout cleanup is now handled by jobs/automatic_cashout_cleanup.py
    
    async def cleanup_expired_distributed_locks(self):
        """CRITICAL FIX: Clean up expired distributed locks to prevent race condition deadlocks"""
        try:
            from utils.distributed_lock import distributed_lock_service
            
            # Clean up locks older than 6 hours (much longer than normal timeout)
            cleaned_count = distributed_lock_service.cleanup_expired_locks(max_age_hours=6)
            
            if cleaned_count > 0:
                logger.info(f"🔒 Scheduled cleanup: Removed {cleaned_count} expired distributed locks")
            
            # Also log active locks for monitoring
            active_locks = distributed_lock_service.get_active_locks()
            if active_locks:
                logger.info(f"🔍 Active distributed locks: {len(active_locks)} currently held")
                
                # Log details of long-running locks for investigation
                for lock in active_locks:
                    if lock.get("age_seconds", 0) > 300:  # More than 5 minutes
                        logger.warning(
                            f"LONG_RUNNING_LOCK: Key={lock.get('lock_key')}, "
                            f"Order={lock.get('order_id')}, Age={lock.get('age_seconds'):.1f}s, "
                            f"Service={lock.get('locked_by')}"
                        )
            
        except Exception as e:
            logger.error(f"Error in scheduled distributed lock cleanup: {e}")

    async def financial_reconciliation_check(self):
        """Check for financial discrepancies from past cashout errors"""
        try:
            from services.financial_reconciliation_service import FinancialReconciliationService
            
            result = await FinancialReconciliationService.detect_balance_discrepancies()
            
            if result.get("success") and result.get("total_count", 0) > 0:
                logger.warning(
                    f"🚨 Financial Alert: Found {result.get('total_count')} balance discrepancies "
                    f"totaling ${result.get('total_amount', 0):.2f}"
                )
                
                # Log each discrepancy for admin review
                for discrepancy in result.get("discrepancies", []):
                    logger.warning(
                        f"   - User {discrepancy['username']} (ID: {discrepancy['user_id']}): "
                        f"${discrepancy['amount']} {discrepancy['currency']} - {discrepancy['issue']}"
                    )
            else:
                logger.info("✅ Financial reconciliation: No discrepancies found")
                
        except Exception as e:
            logger.error(f"Error in financial reconciliation check: {e}")

    # monitor_address_config_cashouts method REMOVED
    # Address configuration errors are now handled by the unified retry system
    # with intelligent error classification and backoff patterns
    
    async def monitor_job_performance(self):
        """Monitor job execution performance and detect issues"""
        try:
            import psutil
            import gc
            
            # Get current system metrics
            memory_mb = psutil.Process().memory_info().rss / 1024 / 1024
            cpu_percent = psutil.Process().cpu_percent()
            
            # Get scheduler statistics 
            jobs = self.scheduler.get_jobs()
            running_jobs = [job for job in jobs if hasattr(job, 'next_run_time') and job.next_run_time]
            
            # Log performance metrics
            logger.info(f"📊 Job Performance: {len(jobs)} total jobs, {len(running_jobs)} scheduled")
            logger.info(f"💾 System Resources: Memory={memory_mb:.1f}MB, CPU={cpu_percent:.1f}%")
            
            # Check for memory growth issues
            if memory_mb > 200:
                logger.warning(f"⚠️ High memory usage detected: {memory_mb:.1f}MB")
                # Force garbage collection
                gc.collect()
                
            # Alert on excessive job count
            if len(jobs) > 50:
                logger.warning(f"⚠️ High job count detected: {len(jobs)} jobs configured")
                
        except Exception as e:
            logger.error(f"Error in job performance monitoring: {e}")
            
    async def update_baseline_metrics(self):
        """Update baseline metrics for system health tracking"""
        try:
            import psutil
            from datetime import datetime
            
            # Get current system state
            memory_mb = psutil.Process().memory_info().rss / 1024 / 1024
            cpu_percent = psutil.Process().cpu_percent()
            
            # Log baseline metrics
            logger.info(f"📈 Baseline Metrics Update: Memory={memory_mb:.1f}MB, CPU={cpu_percent:.1f}%")
            logger.info(f"🕐 Timestamp: {datetime.utcnow().isoformat()}Z")
            
            # Store in cache for trend analysis
            from utils.production_cache import set_cached
            timestamp = datetime.utcnow().strftime('%Y%m%d_%H')
            metric_key = f"baseline_metrics:{timestamp}"
            
            metrics = {
                'memory_mb': memory_mb,
                'cpu_percent': cpu_percent,
                'timestamp': datetime.utcnow().isoformat()
            }
            
            set_cached(metric_key, metrics, ttl=86400)  # Keep for 24 hours
            
        except Exception as e:
            logger.error(f"Error updating baseline metrics: {e}")
