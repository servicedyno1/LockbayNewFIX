#!/usr/bin/env python3
"""
Clean Deterministic Startup - LockBay Telegram Bot

Eliminates:
- Global variables and emergency patterns
- Complex lazy loading and parallel startup systems
- Emergency registration and fallback patterns

Implements:
- Simple, deterministic startup sequence
- Explicit dependency management
- Clear error handling without emergency fallbacks
"""

import logging
import asyncio
import sys
from typing import Optional
from telegram.ext import Application

# Core dependencies only
from config import Config
from database import create_tables

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class CleanStartupManager:
    """
    Clean startup manager with deterministic sequence.
    No globals, no emergency patterns, no complex dependencies.
    """
    
    def __init__(self):
        self.application: Optional[Application] = None
        self.startup_complete = False
        self.startup_errors = []
    
    async def initialize_database(self) -> bool:
        """Initialize database with clean error handling."""
        try:
            logger.info("🗄️ Initializing database...")
            
            # Test database connection first
            from database import test_connection
            if not test_connection():
                raise Exception("Database connection test failed")
            
            # Ensure performance optimizations are applied
            logger.info("🔧 Applying database performance optimizations...")
            from database.migration_runner import ensure_performance_optimizations
            optimization_success = ensure_performance_optimizations()
            if not optimization_success:
                logger.warning("⚠️ Some database optimizations may not be fully applied")
            
            # Initialize query performance monitoring
            logger.info("📊 Enabling database performance monitoring...")
            from database.query_performance_monitor import query_monitor
            query_monitor.enable_monitoring()
            
            # Create all missing tables automatically
            success = create_tables()
            if not success:
                raise Exception("Table creation failed")
                
            logger.info("✅ Database initialization complete - all tables created")
            return True
        except Exception as e:
            logger.error(f"❌ Database initialization failed: {e}")
            self.startup_errors.append(f"Database: {e}")
            return False
    
    async def create_application(self) -> bool:
        """Create Telegram application with clean configuration."""
        try:
            logger.info("🤖 Creating Telegram application...")
            
            if not Config.BOT_TOKEN:
                raise ValueError("BOT_TOKEN not configured")
            
            # Create application with minimal configuration
            self.application = (
                Application.builder()
                .token(Config.BOT_TOKEN)
                .build()
            )
            
            logger.info("✅ Telegram application created")
            return True
            
        except Exception as e:
            logger.error(f"❌ Application creation failed: {e}")
            self.startup_errors.append(f"Application: {e}")
            return False
    
    async def register_handlers(self) -> bool:
        """Register handlers with explicit imports and clear error handling."""
        try:
            logger.info("📋 Registering handlers...")
            
            if not self.application:
                raise ValueError("Application not initialized")
            
            # Import and register handlers explicitly
            from handlers.start import register_start_handlers
            from handlers.wallet_direct import register_wallet_handlers
            from handlers.escrow import register_escrow_handlers
            from handlers.admin import register_admin_handlers
            from handlers.missing_handlers import register_missing_handlers
            
            # Register each handler group with clear error boundaries
            handler_groups = [
                ("Start", register_start_handlers),
                ("Wallet", register_wallet_handlers),
                ("Escrow", register_escrow_handlers),
                ("Admin", register_admin_handlers),
                ("Missing", register_missing_handlers),
            ]
            
            for group_name, register_func in handler_groups:
                try:
                    register_func(self.application)
                    logger.info(f"✅ {group_name} handlers registered")
                except Exception as e:
                    logger.error(f"❌ {group_name} handlers failed: {e}")
                    self.startup_errors.append(f"{group_name} handlers: {e}")
                    # Continue with other handlers instead of emergency fallback
            
            logger.info("✅ Handler registration complete")
            return True
            
        except Exception as e:
            logger.error(f"❌ Handler registration failed: {e}")
            self.startup_errors.append(f"Handlers: {e}")
            return False
    
    async def initialize_services(self) -> bool:
        """Initialize core services with explicit dependencies."""
        try:
            logger.info("⚙️ Initializing core services...")
            
            # Initialize services explicitly without global state
            services_initialized = []
            
            # Database connection verification
            try:
                from database import get_db_session
                with get_db_session() as session:
                    # Test database connection
                    session.execute("SELECT 1")
                services_initialized.append("Database connection")
            except Exception as e:
                logger.warning(f"⚠️ Database connection test failed: {e}")
                self.startup_errors.append(f"Database connection: {e}")
            
            # Email service verification
            try:
                from services.email import EmailService
                email_service = EmailService()
                if email_service.enabled:
                    services_initialized.append("Email service")
                else:
                    logger.info("📧 Email service disabled by configuration")
            except Exception as e:
                logger.warning(f"⚠️ Email service verification failed: {e}")
                self.startup_errors.append(f"Email service: {e}")
            
            # Payment services verification
            try:
                from services.blockbee_service import BlockBeeService
                BlockBeeService()
                services_initialized.append("BlockBee service")
            except Exception as e:
                logger.warning(f"⚠️ BlockBee service verification failed: {e}")
                self.startup_errors.append(f"BlockBee service: {e}")
            
            logger.info(f"✅ Services initialized: {', '.join(services_initialized)}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Service initialization failed: {e}")
            self.startup_errors.append(f"Services: {e}")
            return False
    
    async def start_application(self) -> bool:
        """Start the application in the configured mode."""
        try:
            if not self.application:
                raise ValueError("Application not initialized")
            
            if Config.USE_WEBHOOK:
                logger.info("🔗 Starting in webhook mode...")
                # Webhook mode will be handled by webhook_server.py
                await self.application.initialize()
                logger.info("✅ Application initialized for webhook mode")
            else:
                logger.info("📡 Starting in polling mode...")
                await self.application.initialize()
                await self.application.start()
                await self.application.updater.start_polling()
                logger.info("✅ Application started in polling mode")
            
            self.startup_complete = True
            return True
            
        except Exception as e:
            logger.error(f"❌ Application start failed: {e}")
            self.startup_errors.append(f"Application start: {e}")
            return False
    
    async def startup_sequence(self) -> bool:
        """Execute clean startup sequence without emergency patterns."""
        logger.info("🚀 Starting LockBay bot with clean startup sequence...")
        
        startup_steps = [
            ("Database", self.initialize_database),
            ("Application", self.create_application),
            ("Handlers", self.register_handlers),
            ("Services", self.initialize_services),
            ("Start", self.start_application),
        ]
        
        for step_name, step_func in startup_steps:
            logger.info(f"▶️ Executing step: {step_name}")
            success = await step_func()
            
            if not success:
                logger.error(f"❌ Step '{step_name}' failed")
                if step_name in ["Database", "Application"]:
                    # Critical steps - cannot continue
                    logger.error("🚨 Critical step failed - cannot continue startup")
                    return False
                else:
                    # Non-critical steps - log and continue
                    logger.warning(f"⚠️ Non-critical step '{step_name}' failed - continuing startup")
        
        if self.startup_errors:
            logger.warning(f"⚠️ Startup completed with {len(self.startup_errors)} warnings:")
            for error in self.startup_errors:
                logger.warning(f"  - {error}")
        else:
            logger.info("✅ Clean startup sequence completed successfully")
        
        return self.startup_complete
    
    def get_application(self) -> Optional[Application]:
        """Get the application instance (replaces global access)."""
        return self.application


# Global startup manager instance (single, well-defined global)
startup_manager = CleanStartupManager()


def get_application() -> Optional[Application]:
    """
    Get application instance without global variables.
    Replaces the problematic get_application_instance() pattern.
    """
    return startup_manager.get_application()


async def main_clean():
    """Main function with clean startup - no emergency patterns."""
    try:
        success = await startup_manager.startup_sequence()
        
        if not success:
            logger.error("❌ Startup failed - exiting")
            sys.exit(1)
        
        logger.info("🎉 LockBay bot startup complete!")
        
        # Keep running if in polling mode
        if not Config.USE_WEBHOOK:
            logger.info("📡 Running in polling mode - keeping alive...")
            try:
                # Keep the application running
                await startup_manager.application.updater.idle()
            except KeyboardInterrupt:
                logger.info("👋 Bot stopped by user")
            finally:
                await startup_manager.application.stop()
                await startup_manager.application.shutdown()
        
    except Exception as e:
        logger.error(f"❌ Fatal startup error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    """Clean entry point - no complex startup systems."""
    try:
        # Simple, deterministic startup
        asyncio.run(main_clean())
    except KeyboardInterrupt:
        logger.info("👋 Bot stopped by user")
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}")
        sys.exit(1)