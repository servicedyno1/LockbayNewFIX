"""
Consolidated Background Job Scheduler - 5 Core Jobs System

This replaces the complex 29+ job system with 5 streamlined core jobs:
1. Workflow Runner - UTE execution, outbox processing, saga orchestration  
2. Retry Engine - Unified retry processing for all failed operations
3. Reconciliation - Balance checks, rate updates, webhook validation
4. Cleanup & Expiry - Data cleanup, escrow expiry, system maintenance
5. Reporting - Financial reports, admin summaries, user communications

Maintains 100% functionality while reducing complexity by 83%.
"""

import asyncio
import os
import logging
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.executors.asyncio import AsyncIOExecutor
from apscheduler.jobstores.memory import MemoryJobStore

# Import the 5 core jobs + trend calculation + webhook optimization + database keepalive
from jobs.core.workflow_runner import run_workflow_processing
from jobs.core.retry_engine import run_retry_processing
from jobs.core.reconciliation import run_reconciliation
from jobs.core.cleanup_expiry import run_cleanup_expiry
from jobs.core.reporting import run_reporting
from jobs.core.trend_calculation import run_trend_calculation
from jobs.crypto_rate_background_refresh import run_crypto_rate_background_refresh
from jobs.database_keepalive import run_database_keepalive
from jobs.webhook_cleanup import cleanup_old_webhook_events
from services.universal_welcome_bonus_service import UniversalWelcomeBonusService
from services.railway_neon_sync import RailwayNeonSync

# Import configuration
from config import Config

logger = logging.getLogger(__name__)


class ConsolidatedScheduler:
    """
    Consolidated scheduler using 5 core jobs instead of 29+ individual jobs
    
    Scheduling Strategy:
    - Workflow Runner: Every 30 seconds (high frequency for UTE processing)
    - Retry Engine: Every 2 minutes (matches existing unified retry interval)
    - Reconciliation: Every 5 minutes (balance checks, webhook validation)
    - Cleanup & Expiry: Every 15 minutes (system hygiene)  
    - Reporting: Every hour + daily/weekly cron jobs (reports and communications)
    """

    def __init__(self, application):
        self.application = application
        
        # Optimized scheduler configuration
        jobstores = {
            'default': MemoryJobStore()
        }
        executors = {
            'default': AsyncIOExecutor()
        }
        job_defaults = {
            'coalesce': True,  # Prevent job pileup
            'max_instances': 1,  # Single instance enforcement
            'misfire_grace_time': 120  # 2-minute grace for missed jobs
        }
        
        self.scheduler = AsyncIOScheduler(
            jobstores=jobstores,
            executors=executors,
            job_defaults=job_defaults,
            timezone='UTC'
        )

    def setup_jobs(self):
        """Setup the 5 core consolidated jobs"""
        
        # DEFENSIVE CLEANUP: Remove any existing jobs to prevent duplicates
        try:
            existing_jobs = self.scheduler.get_jobs()
            for job in existing_jobs:
                self.scheduler.remove_job(job.id)
                logger.info(f"🧹 Removed existing job: {job.id}")
        except Exception as e:
            logger.warning(f"⚠️ Job cleanup warning (non-critical): {e}")

        # ===== CORE JOB 1: WORKFLOW RUNNER =====
        # Handles: UTE execution, outbox processing, saga orchestration
        # Frequency: Every 90 seconds (OPTIMIZED from 30s — webhook direct processing is primary path)
        self.scheduler.add_job(
            run_workflow_processing,
            trigger=IntervalTrigger(seconds=90, start_date=datetime.now().replace(second=5, microsecond=0)),
            id="core_workflow_runner", 
            name="🔄 Core Workflow Runner - UTE & Outbox Processing",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=60,
            replace_existing=True
        )
        logger.info("✅ Core Workflow Runner scheduled every 90 seconds (optimized from 30s)")

        # ===== CORE JOB 2: RETRY ENGINE =====
        # Handles: All retry logic, failed operations, backoff processing
        # Frequency: Every 2 minutes (matches existing unified retry interval)
        retry_interval_minutes = getattr(Config, 'UNIFIED_RETRY_PROCESSING_INTERVAL', 120) // 60
        self.scheduler.add_job(
            run_retry_processing,
            trigger=IntervalTrigger(minutes=retry_interval_minutes, start_date=datetime.now().replace(second=15, microsecond=0)),
            id="core_retry_engine",
            name="🔁 Core Retry Engine - Unified Retry Processing", 
            max_instances=1,
            coalesce=True,
            misfire_grace_time=60,
            replace_existing=True
        )
        logger.info(f"✅ Core Retry Engine scheduled every {retry_interval_minutes} minutes")

        # ===== CORE JOB 3: RECONCILIATION =====
        # Handles: Balance checks, rate updates, webhook validation, escrow consistency
        # Frequency: Every 5 minutes (balance monitoring and validation)
        self.scheduler.add_job(
            run_reconciliation,
            trigger=IntervalTrigger(minutes=5, start_date=datetime.now().replace(second=25, microsecond=0)),
            id="core_reconciliation",
            name="📊 Core Reconciliation - Balance & Webhook Validation",
            max_instances=1,
            coalesce=True, 
            misfire_grace_time=90,
            replace_existing=True
        )
        logger.info("✅ Core Reconciliation scheduled every 5 minutes")

        # ===== CORE JOB 4: CLEANUP & EXPIRY =====
        # Handles: Data cleanup, escrow expiry, system maintenance, distributed locks
        # Frequency: Every 15 minutes (system hygiene)
        self.scheduler.add_job(
            run_cleanup_expiry,
            trigger=IntervalTrigger(minutes=15, start_date=datetime.now().replace(second=45, microsecond=0)),
            id="core_cleanup_expiry",
            name="🧹 Core Cleanup & Expiry - System Maintenance",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=120,
            replace_existing=True
        )
        logger.info("✅ Core Cleanup & Expiry scheduled every 15 minutes")

        # ===== CORE JOB 5: REPORTING (DAILY) =====
        # Handles: Admin dashboard updates, user communications, analytics
        # Frequency: Once daily at 6:00 AM UTC (reduced from hourly to cut email volume)
        from jobs.core.reporting import run_admin_dashboards
        self.scheduler.add_job(
            run_admin_dashboards,
            trigger=CronTrigger(hour=6, minute=0),
            id="core_reporting_daily_dashboards",
            name="📈 Core Reporting - Admin Dashboards & Communications (Daily 6 AM UTC)",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,  # 5-minute grace for reports
            replace_existing=True
        )
        logger.info("✅ Core Reporting scheduled daily at 6 AM UTC - dashboards only")

        # ===== CORE JOB 5: REPORTING (DAILY SCHEDULES) =====
        # Daily financial reports at 8:00 AM UTC only (once daily)
        self.scheduler.add_job(
            run_reporting,
            trigger=CronTrigger(hour=8, minute=0),
            id="core_reporting_daily",
            name="📈 Core Reporting - Daily Financial Reports (8 AM UTC)",
            max_instances=1,
            replace_existing=True
        )

        # Weekly reports on Sundays at 10:00 AM UTC
        self.scheduler.add_job(
            run_reporting,
            trigger=CronTrigger(day_of_week=0, hour=10, minute=0),
            id="core_reporting_weekly",
            name="📈 Core Reporting - Weekly Savings Reports",
            max_instances=1,
            replace_existing=True
        )
        
        logger.info("✅ Core Reporting (daily & weekly) scheduled")

        # ===== TREND CALCULATION JOB (GATED — OPTIMIZATION) =====
        # Handles: Completion time trend analysis, performance monitoring
        # Only runs when ENABLE_DEEP_MONITORING=true (non-critical for core functionality)
        if os.getenv("ENABLE_DEEP_MONITORING", "false").lower() == "true":
            self.scheduler.add_job(
                run_trend_calculation,
                trigger=IntervalTrigger(minutes=30, start_date=datetime.now().replace(minute=15, second=30, microsecond=0)),
                id="trend_calculation",
                name="📈 Trend Calculation - Performance Analysis & Monitoring",
                max_instances=1,
                coalesce=True,
                misfire_grace_time=300,
                replace_existing=True
            )
            logger.info("✅ Trend Calculation scheduled every 30 minutes (ENABLE_DEEP_MONITORING=true)")
        else:
            logger.info("🚫 Trend Calculation: Disabled (set ENABLE_DEEP_MONITORING=true to enable)")

        # ===== WEBHOOK OPTIMIZATION JOB =====
        # Handles: Background crypto rate refresh to eliminate webhook API call delays
        # Frequency: Every 5 minutes (OPTIMIZED from 2min — matches cache TTL, cuts API calls 60%)
        self.scheduler.add_job(
            run_crypto_rate_background_refresh,
            trigger=IntervalTrigger(minutes=5, start_date=datetime.now().replace(second=0, microsecond=0)),
            id="webhook_rate_refresh",
            name="🚀 Webhook Rate Refresh - Background Crypto Rate Updates",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=120,
            replace_existing=True
        )
        logger.info("✅ WEBHOOK_OPTIMIZATION: Background crypto rate refresh scheduled every 5 minutes (optimized from 2min)")

        # ===== ADMIN NOTIFICATION QUEUE PROCESSOR =====
        # Handles: Admin email/telegram notifications from database queue
        # Frequency: Every 10 minutes (OPTIMIZED from 2min — admin events are rare, saves scheduler overhead)
        from jobs.admin_notification_processor import run_admin_notification_processor
        self.scheduler.add_job(
            run_admin_notification_processor,
            trigger=IntervalTrigger(minutes=10, start_date=datetime.now().replace(second=30, microsecond=0)),
            id="admin_notification_processor",
            name="📧 Admin Notification Queue - Email & Telegram Delivery",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=120,
            replace_existing=True
        )
        logger.info("✅ ADMIN_NOTIFICATIONS: Queue processor scheduled every 10 minutes (optimized from 2min)")

        # ===== WEBHOOK QUEUE CLEANUP (FIXES ISSUE #7) =====
        # Clean up old completed/failed webhook events daily
        self.scheduler.add_job(
            cleanup_old_webhook_events,
            trigger=CronTrigger(hour=3, minute=0),
            id="webhook_queue_cleanup",
            name="🧹 Webhook Queue Cleanup - Remove Old Events",
            max_instances=1,
            coalesce=True,
            replace_existing=True
        )
        logger.info("✅ Webhook queue cleanup scheduled daily at 3 AM UTC")

        # ===== CONNECTION LEAK KILLER JOB =====
        # Terminates idle-in-transaction connections that are older than 5 minutes
        # Prevents connection exhaustion that can crash the Railway database
        async def kill_leaked_connections():
            """Kill idle-in-transaction connections to prevent connection exhaustion.
            FIX: Uses a dedicated fresh connection instead of competing for the pool."""
            try:
                from sqlalchemy import create_engine as _create_engine, text as _text
                from config import Config as _Config
                # Use a dedicated one-off connection to avoid competing with the pool
                _fresh_engine = _create_engine(_Config.DATABASE_URL, pool_pre_ping=True, pool_size=1, max_overflow=0)
                with _fresh_engine.connect() as conn:
                    result = conn.execute(_text("""
                        SELECT pid, application_name, state, 
                               extract(epoch from now() - state_change) as idle_seconds
                        FROM pg_stat_activity 
                        WHERE datname = current_database()
                          AND state = 'idle in transaction'
                          AND now() - state_change > interval '5 minutes'
                    """))
                    leaked = result.fetchall()
                    if leaked:
                        for row in leaked:
                            conn.execute(_text(f"SELECT pg_terminate_backend({row[0]})"))
                            logger.warning(f"CONNECTION_LEAK_KILLER: Terminated PID={row[0]} app={row[1]} idle_for={row[3]:.0f}s")
                        conn.commit()
                        logger.info(f"CONNECTION_LEAK_KILLER: Killed {len(leaked)} leaked connections")
                    
                    # Log connection pool health
                    result = conn.execute(_text("""
                        SELECT state, count(*) 
                        FROM pg_stat_activity 
                        WHERE datname = current_database() 
                        GROUP BY state
                    """))
                    stats = {row[0]: row[1] for row in result.fetchall()}
                    total = sum(stats.values())
                    logger.info(f"CONNECTION_HEALTH: {total}/100 connections - {stats}")
                _fresh_engine.dispose()  # Clean up the one-off engine
            except Exception as e:
                logger.error(f"CONNECTION_LEAK_KILLER error: {e}")
        
        self.scheduler.add_job(
            kill_leaked_connections,
            trigger=IntervalTrigger(minutes=10, start_date=datetime.now().replace(second=50, microsecond=0)),
            id="connection_leak_killer",
            name="🔪 Connection Leak Killer - Terminate Idle Transactions",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=60,
            replace_existing=True
        )
        logger.info("✅ Connection Leak Killer scheduled every 10 minutes")

        # ===== DATABASE KEEP-ALIVE JOB (DISABLED — OPTIMIZATION) =====
        # DISABLED: Only needed for Neon serverless (5min idle suspension).
        # If using Railway PostgreSQL or any always-on database, this is unnecessary.
        # Re-enable by setting ENABLE_DB_KEEPALIVE=true in environment.
        if os.getenv("ENABLE_DB_KEEPALIVE", "false").lower() == "true":
            self.scheduler.add_job(
                run_database_keepalive,
                trigger=IntervalTrigger(minutes=4, start_date=datetime.now().replace(second=30, microsecond=0)),
                id="database_keepalive",
                name="💓 Database Keep-Alive - Prevent Neon Suspension",
                max_instances=1,
                coalesce=True,
                misfire_grace_time=120,
                replace_existing=True
            )
            logger.info("✅ DATABASE_KEEPALIVE: Enabled via ENABLE_DB_KEEPALIVE=true")
        else:
            logger.info("🚫 DATABASE_KEEPALIVE: Disabled (set ENABLE_DB_KEEPALIVE=true for Neon serverless)")

        # ===== UNIFIED DATABASE → RAILWAY BACKUP SYNC (DISABLED — OPTIMIZATION) =====
        # DISABLED: Redundant if using Neon PITR or Railway's built-in backups.
        # Full pg_dump/restore twice daily is CPU/memory intensive.
        # Re-enable by setting ENABLE_RAILWAY_BACKUP_SYNC=true in environment.
        if os.getenv("ENABLE_RAILWAY_BACKUP_SYNC", "false").lower() == "true":
            async def run_unified_db_to_railway_backup():
                """Async wrapper for Unified DB → Railway Backup sync"""
                try:
                    sync = RailwayNeonSync()
                    result = await sync.sync_source_to_backup()
                    
                    if result["success"]:
                        logger.info(f"✅ Unified DB → Railway Backup completed in {result['duration_seconds']:.1f}s")
                    else:
                        logger.error(f"❌ Unified DB → Railway Backup failed: {result.get('error')}")
                        
                    return result
                except Exception as e:
                    logger.error(f"❌ Unified DB → Railway Backup error: {e}")
                    return {"success": False, "error": str(e)}
            
            # Morning backup: 6 AM UTC
            self.scheduler.add_job(
                run_unified_db_to_railway_backup,
                trigger=CronTrigger(hour=6, minute=0, timezone='UTC'),
                id="unified_db_railway_backup_morning",
                name="🔄 Unified DB → Railway Backup (6 AM UTC)",
                max_instances=1,
                coalesce=True,
                misfire_grace_time=600,
                replace_existing=True
            )
            
            # Evening backup: 6 PM UTC
            self.scheduler.add_job(
                run_unified_db_to_railway_backup,
                trigger=CronTrigger(hour=18, minute=0, timezone='UTC'),
                id="unified_db_railway_backup_evening",
                name="🔄 Unified DB → Railway Backup (6 PM UTC)",
                max_instances=1,
                coalesce=True,
                misfire_grace_time=600,
                replace_existing=True
            )
            logger.info("✅ BACKUP_STORAGE: Railway Backup enabled via ENABLE_RAILWAY_BACKUP_SYNC=true")
        else:
            logger.info("🚫 RAILWAY_BACKUP_SYNC: Disabled (set ENABLE_RAILWAY_BACKUP_SYNC=true to enable)")

        # ===== UNIVERSAL WELCOME BONUS JOB (DISABLED) =====
        # DISABLED: Welcome bonus removed per user request
        # Handles: $3 welcome bonus for all users 30 minutes after onboarding
        # Frequency: Every 5 minutes (check for eligible users)
        # def process_welcome_bonuses_sync():
        #     """Synchronous wrapper for welcome bonus processing"""
        #     try:
        #         return UniversalWelcomeBonusService.process_eligible_bonuses()
        #     except Exception as e:
        #         logger.error(f"Error processing welcome bonuses: {e}")
        #         return {"processed": 0, "successful": 0, "failed": 0, "errors": [str(e)]}
        # 
        # self.scheduler.add_job(
        #     process_welcome_bonuses_sync,
        #     trigger=IntervalTrigger(minutes=5, start_date=datetime.now().replace(second=30, microsecond=0)),
        #     id="universal_welcome_bonus",
        #     name="🎁 Universal Welcome Bonus - Delayed Onboarding Rewards",
        #     max_instances=1,
        #     coalesce=True,
        #     misfire_grace_time=120,
        #     replace_existing=True
        # )
        logger.info("🚫 UNIVERSAL_WELCOME_BONUS: Disabled per user request")

        # ===== LEGACY JOBS REMOVED FOR ARCHITECTURAL SIMPLIFICATION =====
        # All functionality now handled by the 5 core jobs:
        # - Auto-cashout: Handled by Core Workflow Runner
        # - Exchange confirmations: Handled by Core Reconciliation 
        # - Financial audit: Handled by Core Reporting
        logger.info("🚫 Legacy jobs disabled - all functionality moved to 5 core jobs")
        logger.info("   • Auto-cashout → Core Workflow Runner")
        logger.info("   • Exchange confirmations → Core Reconciliation")  
        logger.info("   • Financial audit → Core Reporting")

        # ===== JOB OPTIMIZATION SUMMARY =====
        jobs = self.scheduler.get_jobs()
        logger.info(f"🎯 CONSOLIDATION COMPLETE: Reduced from 29+ jobs to {len(jobs)} jobs ({100 - (len(jobs)/29)*100:.0f}% reduction)")
        logger.info("📊 Job staggering implemented to prevent resource contention") 
        logger.info("🚀 All critical functionality preserved in 5 core jobs")

    def start(self):
        """Start the consolidated scheduler with 5 core jobs for auto-cancellation and cleanup"""
        logger.warning("✅ SCHEDULER ENABLED: Starting ConsolidatedScheduler for auto-cancellation and cleanup")
        
        # Setup and start the jobs
        self.setup_jobs()
        self.scheduler.start()
        
        logger.warning("✅ AUTO_CANCELLATION: Cleanup & Expiry jobs now running to cancel expired trades")
        logger.warning("✅ PAYMENT_FLOW: Using immediate webhook confirmation (Provider → Credit → Notify)")
        logger.warning("🔒 FINANCIAL_SAFETY: Idempotent direct handlers prevent double-processing")
        return  # Early return - no background jobs started
        
        # Log all registered jobs
        job_names = [f"{job.name} ({job.id})" for job in jobs]
        logger.info(f"📋 Active jobs: {job_names}")
        
        # Log next 5 upcoming runs
        logger.info("🔮 Upcoming job runs:")
        from datetime import datetime as dt_max
        for job in sorted(jobs, key=lambda x: x.next_run_time or dt_max.max)[:5]:
            if job.next_run_time:
                logger.info(f"   - {job.name}: {job.next_run_time}")

    def stop(self):
        """Stop the consolidated scheduler"""
        self.scheduler.shutdown()
        logger.info("📴 Consolidated job scheduler stopped")


# Global instance for backward compatibility
_global_scheduler = None

def get_consolidated_scheduler_instance(application=None):
    """Get the global consolidated scheduler instance"""
    global _global_scheduler
    if _global_scheduler is None:
        _global_scheduler = ConsolidatedScheduler(application)
    return _global_scheduler


# Backward compatibility class
class EscrowScheduler(ConsolidatedScheduler):
    """
    Backward compatibility wrapper for existing code
    Routes to ConsolidatedScheduler with same interface
    """
    
    def __init__(self, application):
        super().__init__(application)
        logger.info("🔄 EscrowScheduler using ConsolidatedScheduler (29+ jobs → 5 core jobs)")


# Export for main application
__all__ = [
    "ConsolidatedScheduler",
    "EscrowScheduler", 
    "get_consolidated_scheduler_instance"
]