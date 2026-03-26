"""Configuration management for the Telegram Escrow Bot"""

import os
import logging
from decimal import Decimal
from typing import Dict, Any, Optional
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

logger = logging.getLogger(__name__)


class Config:
    """Application configuration"""

    # Bot configuration with environment detection
    # Environment detection (order of precedence)
    # ENVIRONMENT takes absolute priority, then REPLIT_ENVIRONMENT, then deployment heuristics
    ENVIRONMENT = os.getenv("ENVIRONMENT", os.getenv("RAILWAY_ENVIRONMENT_NAME", "")).lower().strip()
    if ENVIRONMENT and ENVIRONMENT not in ["production", "development"]:
        # Standardize Railway environment names if they differ
        ENVIRONMENT = "production" if "prod" in ENVIRONMENT else "development"
    
    REPLIT_ENVIRONMENT = os.getenv("REPLIT_ENVIRONMENT", "").lower().strip()
    
    # Priority 1: ENVIRONMENT variable (manual override - absolute priority)
    if ENVIRONMENT:
        IS_PRODUCTION = (ENVIRONMENT == "production")
    # Priority 2: REPLIT_ENVIRONMENT (Replit sets this in deployments)
    elif REPLIT_ENVIRONMENT:
        IS_PRODUCTION = (REPLIT_ENVIRONMENT == "production")
    # Priority 3: Deployment heuristics (auto-detect)
    else:
        # Improved production detection for Replit deployments
        # A deployed Replit app has REPLIT_DOMAINS but NOT REPLIT_DEV_DOMAIN
        # Also check for REPLIT_DEPLOYMENT_TYPE which indicates deployment
        has_deployment_domains = bool(os.getenv("REPLIT_DOMAINS")) and not os.getenv("REPLIT_DEV_DOMAIN")
        has_deployment_type = bool(os.getenv("REPLIT_DEPLOYMENT_TYPE"))
        
        IS_PRODUCTION = (
            os.getenv("REPLIT_DEPLOYMENT") == "1" or  # Replit deployment indicator
            has_deployment_domains or  # Deployed app has domains without dev domain
            has_deployment_type or  # Deployment type indicates production
            bool(os.getenv("RAILWAY_PUBLIC_DOMAIN"))  # Railway deployment has custom domains
        )
    
    # Bot Token Configuration
    # Priority: TELEGRAM_BOT_TOKEN (production) > Environment-specific > Generic fallback
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")  # Main production token
    PRODUCTION_BOT_TOKEN = os.getenv("PRODUCTION_BOT_TOKEN")
    DEVELOPMENT_BOT_TOKEN = os.getenv("DEVELOPMENT_BOT_TOKEN", os.getenv("DEV_BOT_TOKEN"))
    GENERIC_BOT_TOKEN = os.getenv("BOT_TOKEN")  # Legacy fallback
    
    # Determine active bot token based on environment
    if IS_PRODUCTION:
        # In production, prioritize TELEGRAM_BOT_TOKEN, then PRODUCTION_BOT_TOKEN, then fallback
        BOT_TOKEN = TELEGRAM_BOT_TOKEN or PRODUCTION_BOT_TOKEN or GENERIC_BOT_TOKEN
        CURRENT_ENVIRONMENT = "production"
        if not BOT_TOKEN:
            logger.error("❌ Production environment detected but no TELEGRAM_BOT_TOKEN found!")
    else:
        BOT_TOKEN = DEVELOPMENT_BOT_TOKEN or TELEGRAM_BOT_TOKEN or GENERIC_BOT_TOKEN
        CURRENT_ENVIRONMENT = "development"
        if not BOT_TOKEN:
            logger.warning("⚠️ Development environment but no DEVELOPMENT_BOT_TOKEN found. Using fallback BOT_TOKEN")
    
    # Bot Username Configuration (environment-specific)
    PRODUCTION_BOT_USERNAME = os.getenv("PRODUCTION_BOT_USERNAME")
    DEVELOPMENT_BOT_USERNAME = os.getenv("DEVELOPMENT_BOT_USERNAME")
    GENERIC_BOT_USERNAME = os.getenv("BOT_USERNAME", "escrowprototype_bot")
    
    if IS_PRODUCTION:
        BOT_USERNAME = PRODUCTION_BOT_USERNAME or GENERIC_BOT_USERNAME
    else:
        BOT_USERNAME = DEVELOPMENT_BOT_USERNAME or GENERIC_BOT_USERNAME
        
    @staticmethod
    def log_environment_config():
        """Log current environment configuration for debugging"""
        logger.info("🔧 Bot Environment Configuration:")
        logger.info(f"   Environment: {Config.CURRENT_ENVIRONMENT.upper()}")
        logger.info(f"   Is Production: {Config.IS_PRODUCTION}")
        logger.info(f"   Bot Username: @{Config.BOT_USERNAME}")
        
        # Log production detection method for debugging
        if Config.IS_PRODUCTION:
            if os.getenv("ENVIRONMENT") == "production":
                logger.info("   Production detected via: ENVIRONMENT=production")
            elif os.getenv("REPLIT_ENVIRONMENT") == "production":
                logger.info("   Production detected via: REPLIT_ENVIRONMENT=production")
            elif os.getenv("REPLIT_DEPLOYMENT") == "1":
                logger.info("   Production detected via: REPLIT_DEPLOYMENT=1")
            elif bool(os.getenv("REPLIT_DOMAINS")) and not os.getenv("REPLIT_DEV_DOMAIN"):
                logger.info("   Production detected via: REPLIT_DOMAINS without REPLIT_DEV_DOMAIN")
            elif os.getenv("REPLIT_DEPLOYMENT_TYPE"):
                logger.info(f"   Production detected via: REPLIT_DEPLOYMENT_TYPE={os.getenv('REPLIT_DEPLOYMENT_TYPE')}")
            elif os.getenv("RAILWAY_PUBLIC_DOMAIN"):
                logger.info("   Production detected via: RAILWAY_PUBLIC_DOMAIN")
        else:
            logger.info("   Running in development mode")
        
        # Log token source (without revealing the actual token)
        if Config.IS_PRODUCTION:
            token_source = "PRODUCTION_BOT_TOKEN" if Config.PRODUCTION_BOT_TOKEN else "BOT_TOKEN (fallback)"
        else:
            token_source = "DEVELOPMENT_BOT_TOKEN" if Config.DEVELOPMENT_BOT_TOKEN else "BOT_TOKEN (fallback)"
        logger.info(f"   Token Source: {token_source}")
        
        # Log database source (CRITICAL for production verification)
        if Config.DATABASE_SOURCE == "Neon PostgreSQL (Production)":
            logger.info(f"   🚀 Database: {Config.DATABASE_SOURCE} (fast & optimized)")
        elif Config.DATABASE_SOURCE == "Neon PostgreSQL (Development)":
            logger.info(f"   💻 Database: {Config.DATABASE_SOURCE}")
        elif Config.DATABASE_SOURCE == "NOT CONFIGURED":
            logger.error(f"   ❌ Database: {Config.DATABASE_SOURCE}")
            if Config.IS_PRODUCTION:
                logger.error("   🚨 PRODUCTION DATABASE NOT CONFIGURED - Check DATABASE_URL in Replit secrets!")
            else:
                logger.error("   🚨 DEVELOPMENT DATABASE NOT CONFIGURED - Check DATABASE_URL!")
        else:
            logger.warning(f"   ⚠️  Database: {Config.DATABASE_SOURCE}")
        
        # Log environment detection factors
        env_factors = []
        if Config.ENVIRONMENT == "production":
            env_factors.append("ENVIRONMENT=production")
        if os.getenv("REPLIT_DEPLOYMENT") == "1":
            env_factors.append("REPLIT_DEPLOYMENT=1")
        if bool(os.getenv("REPLIT_DOMAINS")):
            env_factors.append("REPLIT_DOMAINS set")
        
        if env_factors:
            logger.info(f"   Production indicators: {', '.join(env_factors)}")
        else:
            logger.info("   No production indicators found - using development mode")
    
    @staticmethod
    def validate_bot_configuration():
        """Validate bot configuration and provide helpful error messages"""
        if not Config.BOT_TOKEN:
            error_msg = f"""
❌ Bot token configuration error for {Config.CURRENT_ENVIRONMENT} environment!

Required environment variables:
  • For production: PRODUCTION_BOT_TOKEN (recommended) or BOT_TOKEN (fallback)
  • For development: DEVELOPMENT_BOT_TOKEN (recommended) or BOT_TOKEN (fallback)

Current environment detection:
  • ENVIRONMENT: {os.getenv('ENVIRONMENT', 'not set')}
  • REPLIT_DEPLOYMENT: {os.getenv('REPLIT_DEPLOYMENT', 'not set')}
  • REPLIT_DOMAINS: {os.getenv('REPLIT_DOMAINS', 'not set')}
  • Detected as: {Config.CURRENT_ENVIRONMENT}

Available tokens:
  • PRODUCTION_BOT_TOKEN: {'✅ Set' if Config.PRODUCTION_BOT_TOKEN else '❌ Not set'}
  • DEVELOPMENT_BOT_TOKEN: {'✅ Set' if Config.DEVELOPMENT_BOT_TOKEN else '❌ Not set'}
  • BOT_TOKEN (fallback): {'✅ Set' if Config.GENERIC_BOT_TOKEN else '❌ Not set'}
"""
            logger.critical(error_msg)
            raise ValueError("Bot token not configured for current environment")
        
        return True

    @staticmethod
    def validate_webhook_urls():
        """
        Validate webhook URL configuration for payment processors.
        
        NOTE: This is a legacy validation function. Most validation is now handled
        by validate_production_config() which provides more comprehensive checks.
        
        This function only validates basic webhook URL presence.
        """
        # Check if BASE_WEBHOOK_URL or WEBHOOK_URL is configured
        if not hasattr(Config, 'BASE_WEBHOOK_URL') or not Config.BASE_WEBHOOK_URL:
            if not hasattr(Config, 'WEBHOOK_URL') or not Config.WEBHOOK_URL:
                logger.critical("❌ No webhook URL configured!")
                logger.critical("   Set WEBHOOK_URL environment variable")
                raise ValueError("Webhook URL not configured - webhooks will not work!")
        
        # Log successful configuration
        if hasattr(Config, 'BASE_WEBHOOK_URL') and Config.BASE_WEBHOOK_URL:
            logger.info(f"✅ Webhook base URL configured: {Config.BASE_WEBHOOK_URL}")
        
        return True
    
    @staticmethod
    def validate_retry_system_configuration():
        """Validate unified retry system configuration"""
        # Check if the unified retry system is disabled
        if not Config.UNIFIED_RETRY_ENABLED:
            logger.critical("🚨 CRITICAL SAFETY VIOLATION: Unified retry system is disabled!")
            logger.critical("   UNIFIED_RETRY_ENABLED=false")
            logger.critical("   This would leave failed transactions without any retry mechanism!")
            logger.critical("   🔧 AUTO-FIXING: Enabling UNIFIED_RETRY_ENABLED to prevent transaction failures")
            
            # Auto-enable unified retry as the safer default
            Config.UNIFIED_RETRY_ENABLED = True
            
            logger.warning("✅ SAFETY GUARD APPLIED: UNIFIED_RETRY_ENABLED auto-enabled")
            logger.warning("   Recommendation: Set UNIFIED_RETRY_ENABLED=true in environment variables")
        
        # Log current retry system state for observability
        logger.info("🔧 Unified Retry System Configuration:")
        logger.info(f"   UNIFIED_RETRY_ENABLED: {Config.UNIFIED_RETRY_ENABLED}")
        logger.info(f"   Processing Interval: {Config.UNIFIED_RETRY_PROCESSING_INTERVAL}s")
        logger.info(f"   Max Attempts: {Config.UNIFIED_RETRY_MAX_ATTEMPTS}")
        logger.info(f"   Base Delay: {Config.UNIFIED_RETRY_BASE_DELAY}s")
        logger.info("✅ Using single unified retry system (obsolete sweeper removed)")
        
        return True

    @staticmethod
    def validate_manual_refunds_configuration():
        """Validate manual refunds only configuration and log status"""
        logger.info("🔧 Manual Refunds Configuration:")
        logger.info(f"   MANUAL_REFUNDS_ONLY: {Config.MANUAL_REFUNDS_ONLY}")
        logger.info(f"   DISABLE_REFUND_JOBS: {Config.DISABLE_REFUND_JOBS}")
        logger.info(f"   DISABLE_WEBHOOK_AUTO_REFUNDS: {Config.DISABLE_WEBHOOK_AUTO_REFUNDS}")
        
        # Warn about manual refunds mode if enabled
        if Config.MANUAL_REFUNDS_ONLY:
            logger.warning("🔒 MANUAL_REFUNDS_ONLY is ENABLED - All automatic refunds are disabled")
            logger.warning("   Failed transactions will be set to admin_pending status")
            logger.warning("   Admins must manually review and process all refunds")
        
        if Config.DISABLE_WEBHOOK_AUTO_REFUNDS:
            logger.warning("🔒 DISABLE_WEBHOOK_AUTO_REFUNDS is ENABLED - Webhook auto-refunds disabled")
            logger.warning("   Payments to cancelled orders will require manual admin processing")
        
        if Config.DISABLE_REFUND_JOBS:
            logger.warning("🔒 DISABLE_REFUND_JOBS is ENABLED - Scheduled refund jobs disabled")
            logger.warning("   Background refund monitoring will not process refunds automatically")
        
        # Log current environment and recommended settings
        if not Config.MANUAL_REFUNDS_ONLY and not Config.DISABLE_WEBHOOK_AUTO_REFUNDS and not Config.DISABLE_REFUND_JOBS:
            logger.info("✅ Automatic refunds are ENABLED (default configuration)")
        else:
            logger.info("🔒 One or more manual refund flags are ENABLED - admin intervention required for refunds")
        
        return True

    # Branding (single source for all brand references)
    BRAND = os.getenv("BRAND", "Lockbay")
    PLATFORM_NAME = BRAND  # Unified brand reference
    PLATFORM_TAGLINE = os.getenv("PLATFORM_TAGLINE", "safe trading on Telegram!")
    SUPPORT_EMAIL = os.getenv("SUPPORT_EMAIL", f"support@{BRAND.lower()}.io")
    WEBAPP_URL = os.getenv("WEBAPP_URL", f"https://t.me/{BRAND.lower()}bot")
    HELP_URL = os.getenv("HELP_URL", f"https://{BRAND.lower()}.com/help")

    # Database configuration
    # UNIFIED DATABASE: Same database for both development and production
    DATABASE_URL = os.getenv("DATABASE_URL")  # Unified database
    NEON_PRODUCTION_DATABASE_URL = os.getenv("NEON_PRODUCTION_DATABASE_URL")  # Legacy reference (deprecated)
    RAILWAY_BACKUP_DB_URL = os.getenv("RAILWAY_BACKUP_DB_URL")  # Backup database storage
    
    # Use unified database for all environments
    if DATABASE_URL:
        DATABASE_SOURCE = "Neon PostgreSQL (Unified)"
        if IS_PRODUCTION:
            logger.info("✅ PRODUCTION: Using unified Neon PostgreSQL database")
        else:
            logger.info("✅ DEVELOPMENT: Using unified Neon PostgreSQL database")
    else:
        DATABASE_SOURCE = "NOT CONFIGURED"
        logger.error("❌ DATABASE_URL not configured! Please set DATABASE_URL environment variable.")

    # Redis Configuration for State Management
    # Redis URL with fallback based on environment
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    
    # Redis Security Configuration
    REDIS_PASSWORD = os.getenv("REDIS_PASSWORD")
    REDIS_USERNAME = os.getenv("REDIS_USERNAME", "default")
    REDIS_TLS_ENABLED = os.getenv("REDIS_TLS_ENABLED", "false").lower() == "true"
    REDIS_TLS_CERT_REQS = os.getenv("REDIS_TLS_CERT_REQS", "required")  # none, optional, required
    REDIS_TLS_CA_CERTS = os.getenv("REDIS_TLS_CA_CERTS")  # Path to CA certificates
    REDIS_TLS_CERTFILE = os.getenv("REDIS_TLS_CERTFILE")  # Path to client certificate
    REDIS_TLS_KEYFILE = os.getenv("REDIS_TLS_KEYFILE")   # Path to client private key
    
    # Redis Connection Pool Configuration
    REDIS_MAX_CONNECTIONS = int(os.getenv("REDIS_MAX_CONNECTIONS", "50"))
    REDIS_MIN_CONNECTIONS = int(os.getenv("REDIS_MIN_CONNECTIONS", "5"))
    REDIS_CONNECTION_TIMEOUT = int(os.getenv("REDIS_CONNECTION_TIMEOUT", "5"))  # seconds
    REDIS_SOCKET_TIMEOUT = int(os.getenv("REDIS_SOCKET_TIMEOUT", "5"))  # seconds
    REDIS_SOCKET_KEEPALIVE = os.getenv("REDIS_SOCKET_KEEPALIVE", "true").lower() == "true"
    REDIS_SOCKET_KEEPALIVE_OPTIONS: Dict[str, Any] = {}  # Can be configured via environment
    
    # Redis Retry Configuration  
    REDIS_RETRY_ON_TIMEOUT = os.getenv("REDIS_RETRY_ON_TIMEOUT", "true").lower() == "true"
    REDIS_RETRY_ON_ERROR = os.getenv("REDIS_RETRY_ON_ERROR", "true").lower() == "true"
    REDIS_MAX_RETRIES = int(os.getenv("REDIS_MAX_RETRIES", "3"))
    REDIS_RETRY_DELAY = float(os.getenv("REDIS_RETRY_DELAY", "0.1"))  # seconds
    REDIS_BACKOFF_FACTOR = float(os.getenv("REDIS_BACKOFF_FACTOR", "2.0"))
    
    # Redis Health Check Configuration
    REDIS_HEALTH_CHECK_INTERVAL = int(os.getenv("REDIS_HEALTH_CHECK_INTERVAL", "30"))  # seconds
    REDIS_PING_TIMEOUT = int(os.getenv("REDIS_PING_TIMEOUT", "1"))  # seconds
    
    # Redis TTL Defaults (in seconds)
    REDIS_DEFAULT_TTL = int(os.getenv("REDIS_DEFAULT_TTL", "3600"))  # 1 hour
    REDIS_SESSION_TTL = int(os.getenv("REDIS_SESSION_TTL", "1800"))  # 30 minutes  
    REDIS_CONVERSATION_TTL = int(os.getenv("REDIS_CONVERSATION_TTL", "3600"))  # 1 hour
    REDIS_TEMP_STATE_TTL = int(os.getenv("REDIS_TEMP_STATE_TTL", "300"))  # 5 minutes
    REDIS_IDEMPOTENCY_TTL = int(os.getenv("REDIS_IDEMPOTENCY_TTL", "86400"))  # 24 hours
    REDIS_PROCESSING_TIMEOUT = int(os.getenv("REDIS_PROCESSING_TIMEOUT", "300"))  # 5 minutes
    REDIS_CIRCUIT_BREAKER_TTL = int(os.getenv("REDIS_CIRCUIT_BREAKER_TTL", "3600"))  # 1 hour
    
    # CRITICAL SECURITY: Redis Fallback Configuration
    # Production safety controls to prevent split-brain scenarios in multi-instance deployments
    REDIS_FALLBACK_MODE = os.getenv("REDIS_FALLBACK_MODE", "FAIL_CLOSED" if IS_PRODUCTION else "DB_BACKED")
    ALLOW_IN_MEMORY_FALLBACK = os.getenv("ALLOW_IN_MEMORY_FALLBACK", "false" if IS_PRODUCTION else "true").lower() == "true"
    REDIS_REQUIRED_FOR_FINANCIAL_OPS = os.getenv("REDIS_REQUIRED_FOR_FINANCIAL_OPS", "true" if IS_PRODUCTION else "false").lower() == "true"
    
    # Redis fallback validation
    @staticmethod
    def validate_redis_fallback_configuration():
        """Validate Redis fallback configuration for financial safety"""
        logger.info("🔧 Redis Fallback Security Configuration:")
        logger.info(f"   REDIS_FALLBACK_MODE: {Config.REDIS_FALLBACK_MODE}")
        logger.info(f"   ALLOW_IN_MEMORY_FALLBACK: {Config.ALLOW_IN_MEMORY_FALLBACK}")
        logger.info(f"   REDIS_REQUIRED_FOR_FINANCIAL_OPS: {Config.REDIS_REQUIRED_FOR_FINANCIAL_OPS}")
        
        # Critical security check for production
        if Config.IS_PRODUCTION:
            if Config.ALLOW_IN_MEMORY_FALLBACK:
                logger.critical("🚨 CRITICAL SECURITY VIOLATION: ALLOW_IN_MEMORY_FALLBACK=true in production!")
                logger.critical("   This enables split-brain scenarios leading to double-spend risks!")
                logger.critical("   🔧 AUTO-FIXING: Disabling in-memory fallback for production safety")
                Config.ALLOW_IN_MEMORY_FALLBACK = False
                logger.warning("✅ SAFETY GUARD APPLIED: ALLOW_IN_MEMORY_FALLBACK force-disabled")
                
            if Config.REDIS_FALLBACK_MODE == "IN_MEMORY":
                logger.critical("🚨 CRITICAL SECURITY VIOLATION: REDIS_FALLBACK_MODE=IN_MEMORY in production!")
                logger.critical("   This creates split-brain vulnerability in multi-instance deployments!")
                logger.critical("   🔧 AUTO-FIXING: Switching to FAIL_CLOSED mode for financial safety")
                Config.REDIS_FALLBACK_MODE = "FAIL_CLOSED"
                logger.warning("✅ SAFETY GUARD APPLIED: REDIS_FALLBACK_MODE set to FAIL_CLOSED")
                
        # Validate fallback mode
        valid_modes = ["FAIL_CLOSED", "DB_BACKED", "IN_MEMORY"]
        if Config.REDIS_FALLBACK_MODE not in valid_modes:
            logger.critical(f"❌ Invalid REDIS_FALLBACK_MODE: {Config.REDIS_FALLBACK_MODE}")
            logger.critical(f"   Valid modes: {valid_modes}")
            logger.critical("   🔧 AUTO-FIXING: Using FAIL_CLOSED for safety")
            Config.REDIS_FALLBACK_MODE = "FAIL_CLOSED"
            
        # Log security posture
        if Config.REDIS_FALLBACK_MODE == "FAIL_CLOSED":
            logger.info("🔒 SECURITY POSTURE: Financial operations will fail if Redis is unavailable (safest)")
        elif Config.REDIS_FALLBACK_MODE == "DB_BACKED":
            logger.info("🛡️ SECURITY POSTURE: Database-backed fallback enabled (safe for coordination)")
        else:  # IN_MEMORY
            logger.warning("⚠️ SECURITY POSTURE: In-memory fallback enabled (DEVELOPMENT ONLY)")
            if Config.IS_PRODUCTION:
                logger.critical("🚨 PRODUCTION RISK: In-memory fallback should NEVER be used in production!")
                
        return True

    # Email configuration (Brevo - formerly SendinBlue)
    BREVO_API_KEY = os.getenv("BREVO_API_KEY")  # Only Brevo API supported
    BREVO_WEBHOOK_SECRET = os.getenv("BREVO_WEBHOOK_SECRET")  # SECURITY: Inbound webhook authentication
    FROM_EMAIL = os.getenv("FROM_EMAIL", "hi@lockbay.io")  # Verified Brevo sender
    FROM_NAME = os.getenv("FROM_NAME", BRAND)


    # Unified Retry System Configuration
    # Feature flag for gradual rollout
    UNIFIED_RETRY_ENABLED = os.getenv("UNIFIED_RETRY_ENABLED", "true").lower() == "true"
    
    
    # Legacy retry configuration (kept for backward compatibility)
    UNIFIED_RETRY_MAX_ATTEMPTS = int(os.getenv("UNIFIED_RETRY_MAX_ATTEMPTS", "6"))  # Increased from 3 to 6 for better coverage
    UNIFIED_RETRY_BASE_DELAY = int(os.getenv("UNIFIED_RETRY_BASE_DELAY", "300"))  # 5 minutes base delay for progressive scaling
    UNIFIED_RETRY_JITTER_PERCENT = int(os.getenv("UNIFIED_RETRY_JITTER_PERCENT", "20"))  # 20% jitter
    
    # Retry batch processing
    UNIFIED_RETRY_BATCH_SIZE = int(os.getenv("UNIFIED_RETRY_BATCH_SIZE", "20"))
    UNIFIED_RETRY_PROCESSING_INTERVAL = int(os.getenv("UNIFIED_RETRY_PROCESSING_INTERVAL", "120"))  # seconds
    
    # Streamlined Failure Handling System Configuration (NEW)
    # Simple "manual-first" approach with minimal automatic retry
    STREAMLINED_FAILURE_HANDLING = os.getenv("STREAMLINED_FAILURE_HANDLING", "true").lower() == "true"
    
    # Technical retry settings (only 4 error types: NETWORK_ERROR, API_TIMEOUT, SERVICE_UNAVAILABLE, RATE_LIMIT_EXCEEDED)
    UNIFIED_TECHNICAL_RETRY_ENABLED = os.getenv("UNIFIED_TECHNICAL_RETRY_ENABLED", "true").lower() == "true"
    STREAMLINED_RETRY_MAX_ATTEMPTS = int(os.getenv("STREAMLINED_RETRY_MAX_ATTEMPTS", "1"))  # Single retry only
    STREAMLINED_RETRY_DELAY_SECONDS = int(os.getenv("STREAMLINED_RETRY_DELAY_SECONDS", "600"))  # 10 minutes fixed delay
    
    # Everything else goes to admin review (user errors, insufficient funds, auth failures, etc.)
    
    # Phase 1 & 2: Admin Email Alert System Configuration
    ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "moxxcompany@gmail.com")
    ADMIN_EMAIL_ALERTS = os.getenv("ADMIN_EMAIL_ALERTS", "true").lower() == "true"
    
    # CRITICAL SECURITY: Admin Email Action Token Secret
    # This secret is used to generate secure HMAC tokens for admin email action buttons
    # MUST be set in production to prevent token forgery
    ADMIN_EMAIL_SECRET = os.getenv("ADMIN_EMAIL_SECRET")
    
    # CRITICAL SECURITY: Cashout HMAC Token Secret
    # Dedicated secret for crypto cashout confirmation tokens
    # Separate from BOT_TOKEN for security and stability
    # MUST be set in production to prevent token forgery
    CASHOUT_HMAC_SECRET = os.getenv("CASHOUT_HMAC_SECRET")
    
    # Security validation for admin email secret
    _admin_secret_secure = bool(ADMIN_EMAIL_SECRET and len(ADMIN_EMAIL_SECRET) >= 32)
    if not _admin_secret_secure:
        if IS_PRODUCTION:
            logger.critical("🚨 CRITICAL SECURITY: ADMIN_EMAIL_SECRET not set or too short in production!")
            logger.critical("🚨 Admin email action links DISABLED for security - tokens would be forgeable")
        else:
            logger.warning("⚠️ ADMIN_EMAIL_SECRET not set or too short - using development fallback")
            logger.warning("⚠️ This is acceptable in development but NEVER in production")
    
    # Flag to disable action links if secret is insecure
    ADMIN_EMAIL_ACTIONS_ENABLED = _admin_secret_secure
    
    # Security validation for cashout HMAC secret
    _cashout_secret_secure = bool(CASHOUT_HMAC_SECRET and len(CASHOUT_HMAC_SECRET) >= 32)
    if not _cashout_secret_secure:
        if IS_PRODUCTION:
            logger.critical("🚨 CRITICAL SECURITY: CASHOUT_HMAC_SECRET not set or too short in production!")
            logger.critical("🚨 Crypto cashout confirmations DISABLED for security - tokens would be forgeable")
        else:
            logger.warning("⚠️ CASHOUT_HMAC_SECRET not set or too short - using development fallback")
            logger.warning("⚠️ This is acceptable in development but NEVER in production")
            # Provide development fallback
            CASHOUT_HMAC_SECRET = CASHOUT_HMAC_SECRET or "dev_fallback_cashout_secret_32chars_min"
            # Re-evaluate security with fallback secret
            _cashout_secret_secure = bool(CASHOUT_HMAC_SECRET and len(CASHOUT_HMAC_SECRET) >= 32)
            if _cashout_secret_secure:
                logger.info("✅ Development fallback secret applied - cashout confirmations enabled")
    
    # Flag to disable cashout confirmations if secret is insecure
    CASHOUT_HMAC_ENABLED = _cashout_secret_secure
    ADMIN_TRANSACTION_EMAIL_THRESHOLD = Decimal(
        os.getenv("ADMIN_TRANSACTION_EMAIL_THRESHOLD", "0.0")
    )  # Send alerts for all transactions by default
    AUTO_CASHOUT_ADMIN_ALERTS = (
        os.getenv("AUTO_CASHOUT_ADMIN_ALERTS", "true").lower() == "true"
    )
    EMAIL_ALERT_FREQUENCY = os.getenv(
        "EMAIL_ALERT_FREQUENCY", "instant"
    )  # instant, hourly, daily

    # Platform Currency Configuration
    PLATFORM_CURRENCY = os.getenv(
        "PLATFORM_CURRENCY", "USD"
    ).upper()  # USD, EUR, GBP, etc.

    # Currency symbols and names
    CURRENCY_SYMBOLS = {
        "USD": "$",
        "EUR": "€",
        "GBP": "£",
        "CAD": "C$",
        "AUD": "A$",
        "JPY": "¥",
        "CHF": "CHF",
        "SEK": "kr",
        "NOK": "kr",
        "DKK": "kr",
    }

    CURRENCY_NAMES = {
        "USD": "US Dollar",
        "EUR": "Euro",
        "GBP": "British Pound",
        "CAD": "Canadian Dollar",
        "AUD": "Australian Dollar",
        "JPY": "Japanese Yen",
        "CHF": "Swiss Franc",
        "SEK": "Swedish Krona",
        "NOK": "Norwegian Krone",
        "DKK": "Danish Krone",
    }

    # Get platform currency symbol and name
    PLATFORM_CURRENCY_SYMBOL = CURRENCY_SYMBOLS.get(
        PLATFORM_CURRENCY, PLATFORM_CURRENCY
    )
    PLATFORM_CURRENCY_NAME = CURRENCY_NAMES.get(PLATFORM_CURRENCY, PLATFORM_CURRENCY)

    # Currency emojis are now centralized in utils/constants.py

    # Admin configuration
    # Admin configuration - prioritize ADMIN_IDS over ADMIN_USER_IDS
    _admin_ids_env = os.getenv("ADMIN_IDS", "").strip()
    _admin_user_ids_env = os.getenv("ADMIN_USER_IDS", "").strip()
    
    # Use ADMIN_IDS if available, fallback to ADMIN_USER_IDS for compatibility
    if _admin_ids_env:
        ADMIN_IDS = [int(uid.strip()) for uid in _admin_ids_env.split(",") if uid.strip()]
    elif _admin_user_ids_env:
        ADMIN_IDS = [int(uid.strip()) for uid in _admin_user_ids_env.split(",") if uid.strip()]
    else:
        ADMIN_IDS = []
    

    # Notification Group Configuration
    NOTIFICATION_GROUP_ID = os.getenv(
        "NOTIFICATION_GROUP_ID"
    )  # Telegram group chat ID for trade notifications

    # Webhook Security Configuration
    # BlockBee webhook signature verification
    BLOCKBEE_WEBHOOK_SECRET = os.getenv("BLOCKBEE_WEBHOOK_SECRET")
    
    # DynoPay webhook signature verification
    DYNOPAY_WEBHOOK_SECRET = os.getenv("DYNOPAY_WEBHOOK_SECRET")
    
    # Fincra webhook signature verification
    FINCRA_WEBHOOK_ENCRYPTION_KEY = os.getenv("FINCRA_WEBHOOK_ENCRYPTION_KEY")
    
    # Telegram webhook secret token
    WEBHOOK_SECRET_TOKEN = os.getenv("WEBHOOK_SECRET_TOKEN")
    
    # Webhook security validation
    @staticmethod
    def validate_webhook_security_configuration():
        """Validate webhook security configuration for production safety"""
        logger.info("🔧 Webhook Security Configuration:")
        
        # Check BlockBee webhook security
        if Config.BLOCKBEE_WEBHOOK_SECRET:
            logger.info("   BLOCKBEE_WEBHOOK_SECRET: ✅ Configured")
        else:
            if Config.IS_PRODUCTION:
                logger.critical("🚨 PRODUCTION_SECURITY_RISK: BLOCKBEE_WEBHOOK_SECRET not configured!")
                logger.critical("   BlockBee webhooks will be REJECTED in production without this secret")
            else:
                logger.warning("⚠️ BLOCKBEE_WEBHOOK_SECRET not configured - development fallback enabled")
        
        # Check DynoPay webhook security
        if Config.DYNOPAY_WEBHOOK_SECRET:
            logger.info("   DYNOPAY_WEBHOOK_SECRET: ✅ Configured")
        else:
            if Config.IS_PRODUCTION:
                logger.critical("🚨 PRODUCTION_SECURITY_RISK: DYNOPAY_WEBHOOK_SECRET not configured!")
                logger.critical("   DynoPay webhooks will be REJECTED in production without this secret")
            else:
                logger.warning("⚠️ DYNOPAY_WEBHOOK_SECRET not configured - development fallback enabled")
        
        # Check Fincra webhook security
        if Config.FINCRA_WEBHOOK_ENCRYPTION_KEY:
            logger.info("   FINCRA_WEBHOOK_ENCRYPTION_KEY: ✅ Configured")
        else:
            if Config.IS_PRODUCTION:
                logger.critical("🚨 PRODUCTION_SECURITY_RISK: FINCRA_WEBHOOK_ENCRYPTION_KEY not configured!")
                logger.critical("   Fincra webhooks will be REJECTED in production without this secret")
            else:
                logger.warning("⚠️ FINCRA_WEBHOOK_ENCRYPTION_KEY not configured - development fallback enabled")
        
        # Check Telegram webhook security
        if Config.WEBHOOK_SECRET_TOKEN:
            logger.info("   WEBHOOK_SECRET_TOKEN: ✅ Configured")
        else:
            logger.warning("⚠️ WEBHOOK_SECRET_TOKEN not configured - Telegram webhook security disabled")
        
        # Log security posture
        if Config.IS_PRODUCTION:
            logger.info("🔒 PRODUCTION SECURITY POSTURE: Fail-closed webhook validation enabled")
            logger.info("   Missing webhook secrets will cause 500 errors (misconfiguration)")
            logger.info("   Invalid signatures will cause 401 errors (unauthorized)")
        else:
            logger.info("🛡️ DEVELOPMENT SECURITY POSTURE: Fail-open webhook validation enabled")
            logger.info("   Missing webhook secrets will log warnings but allow processing")
        
        return True

    # Cashout and Wallet Fee Configuration
    CASHOUT_USD_TO_USDT_MARKUP = Decimal(os.getenv("CASHOUT_USD_TO_USDT_MARKUP", "5.0"))  # $5 markup for USDT cashouts
    WALLET_NGN_MARKUP_PERCENTAGE = Decimal(os.getenv("WALLET_NGN_MARKUP_PERCENTAGE", "2.0"))  # 2% markup for NGN wallet operations
    
    # Exchange markup configuration with validation
    @staticmethod
    def _validate_markup_percentage(env_var: str, default: str = "5.0", min_val: float = 0.01, max_val: float = 50.0) -> Decimal:
        """Validate markup percentage with bounds checking"""
        try:
            value_str = os.getenv(env_var, default)
            markup = Decimal(value_str)
            
            if markup < Decimal(str(min_val)):
                logger.error(f"❌ {env_var}={markup}% is below minimum {min_val}%. Using default {default}%")
                return Decimal(default)
            
            if markup > Decimal(str(max_val)):
                logger.error(f"❌ {env_var}={markup}% exceeds maximum {max_val}%. Using default {default}%")
                return Decimal(default)
            
            if markup == Decimal("0"):
                logger.error(f"❌ {env_var}=0% eliminates platform profit! Using default {default}%")
                return Decimal(default)
            
            logger.info(f"✅ {env_var}={markup:.1f}% validated successfully")
            return markup
            
        except Exception as e:
            logger.error(f"❌ Invalid {env_var} value '{os.getenv(env_var)}': {e}. Using default {default}%")
            return Decimal(default)

    # Escrow configuration with validation
    ESCROW_FEE_PERCENTAGE = _validate_markup_percentage(
        "ESCROW_FEE_PERCENTAGE", "5.0", 0.01, 20.0
    )  # Validated 5% escrow fee with 0.01%-20% bounds
    MIN_ESCROW_AMOUNT_USD = Decimal(
        os.getenv("MIN_ESCROW_AMOUNT_USD", "2.0")
    )  # $2 minimum
    
    # Unverified user cashout limit (NGN)
    UNVERIFIED_CASHOUT_LIMIT = Decimal("50000")  # ₦50,000 limit for unverified users
    MAX_ESCROW_AMOUNT_USD = Decimal(
        os.getenv("MAX_ESCROW_AMOUNT_USD", "500.0")
    )  # $500 maximum

    # Minimum Escrow Fee Configuration (for profitability on small transactions)
    MIN_ESCROW_FEE_AMOUNT = Decimal(
        os.getenv("MIN_ESCROW_FEE_AMOUNT", "0.0")
    )  # Minimum platform fee in USD (default 0 = disabled)
    MIN_ESCROW_FEE_THRESHOLD = Decimal(
        os.getenv("MIN_ESCROW_FEE_THRESHOLD", "100.0")
    )  # Apply minimum fee only to escrows below this amount

    # First trade free promotion configuration
    FIRST_TRADE_FREE_ENABLED = os.getenv("FIRST_TRADE_FREE_ENABLED", "false").lower() == "true"

    # Quick Exchange configuration
    MIN_EXCHANGE_AMOUNT_USD = Decimal(
        os.getenv("MIN_EXCHANGE_AMOUNT_USD", "2.0")
    )
    MAX_EXCHANGE_AMOUNT_USD = Decimal(
        os.getenv("MAX_EXCHANGE_AMOUNT_USD", "500.0")
    )  # Updated limit to $500

    # Timeouts (in hours)
    DEFAULT_DELIVERY_TIMEOUT = int(
        os.getenv("DEFAULT_DELIVERY_TIMEOUT", "72")
    )  # 72 hours
    MAX_DELIVERY_TIMEOUT = int(os.getenv("MAX_DELIVERY_TIMEOUT", "336"))  # 14 days

    # File upload limits
    MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "20"))
    ALLOWED_FILE_TYPES = [
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/webp",
        "application/pdf",
        "text/plain",
        "application/zip",
        "video/mp4",
        "audio/mpeg",
    ]

    # Cashout configuration - Streamlined "Always Success" approach
    # All cashouts provide immediate success UX with backend admin notifications for any issues
    
    # Admin notification preferences for cashout processing
    CASHOUT_ADMIN_NOTIFICATIONS = (
        os.getenv("CASHOUT_ADMIN_NOTIFICATIONS", "true").lower() == "true"
    )  # Send admin email notifications for processing issues
    
    CASHOUT_SUCCESS_NOTIFICATIONS = (
        os.getenv("CASHOUT_SUCCESS_NOTIFICATIONS", "true").lower() == "true"  
    )  # Send user success confirmations immediately
    
    # NGN to Crypto Exchange Processing Mode (automatic processing)
    AUTO_COMPLETE_NGN_TO_CRYPTO = (
        os.getenv("AUTO_COMPLETE_NGN_TO_CRYPTO", "true").lower() == "true"
    )  # Automatic crypto payout processing
    
    # DynoPay Configuration (Backup Payment Processor)
    DYNOPAY_API_KEY = os.getenv("DYNOPAY_API_KEY")
    DYNOPAY_WEBHOOK_SECRET = os.getenv("DYNOPAY_WEBHOOK_SECRET")  # SECURITY: Webhook signature verification
    DYNOPAY_WALLET_TOKEN = os.getenv("DYNOPAY_WALLET_TOKEN")
    DYNOPAY_BASE_URL = os.getenv("DYNOPAY_BASE_URL", "https://user-api.dynopay.com/api")
    DYNOPAY_WEBHOOK_URL = os.getenv("DYNOPAY_WEBHOOK_URL")  # e.g., https://yourdomain.com/webhook/dynopay
    
    # Payment Processor Configuration - DynoPay as default primary
    PRIMARY_PAYMENT_PROVIDER = os.getenv("PRIMARY_PAYMENT_PROVIDER", "dynopay")  # dynopay or blockbee
    BACKUP_PAYMENT_PROVIDER = os.getenv("BACKUP_PAYMENT_PROVIDER", "blockbee")   # blockbee or dynopay
    PAYMENT_FAILOVER_ENABLED = (
        os.getenv("PAYMENT_FAILOVER_ENABLED", "True").lower() == "true"
    )  # Enable automatic failover between providers
    
    # Provider Enable/Disable Controls
    BLOCKBEE_ENABLED = (
        os.getenv("BLOCKBEE_ENABLED", "True").lower() == "true"
    )  # Enable/disable BlockBee provider
    DYNOPAY_ENABLED = (
        os.getenv("DYNOPAY_ENABLED", "True").lower() == "true"
    )  # Enable/disable DynoPay provider
    
    # Admin Order Creation Notifications
    ADMIN_ORDER_CREATION_ALERTS = (
        os.getenv("ADMIN_ORDER_CREATION_ALERTS", "true").lower() == "true"
    )  # True = Notify admins when new NGN→Crypto orders are created, False = Only notify on payment
    
    # Admin Cashout Creation Notifications
    ADMIN_CASHOUT_CREATION_ALERTS = (
        os.getenv("ADMIN_CASHOUT_CREATION_ALERTS", "true").lower() == "true"
    )  # True = Notify admins when new cashout requests are created, False = Only notify when processed

    # Dispute Resolution Configuration
    DISPUTE_AUTO_RESOLUTION_ENABLED = (
        os.getenv("DISPUTE_AUTO_RESOLUTION_ENABLED", "false").lower() == "true"
    )  # True = Enable AI auto-resolution for disputes, False = Manual admin review only

    # Auto cashout global settings

    # Feature Toggles - Master switches to hide/show features system-wide
    ENABLE_NGN_FEATURES = (
        os.getenv("ENABLE_NGN_FEATURES", "False").lower() == "true"
    )  # True = Show NGN features (wallet deposit, escrow payment, cashout, exchange), False = Hide all NGN options
    
    ENABLE_EXCHANGE_FEATURES = (
        os.getenv("ENABLE_EXCHANGE_FEATURES", "True").lower() == "true"
    )  # True = Show exchange features (quick exchange in menu, /exchange command), False = Hide all exchange options
    
    ENABLE_AUTO_CASHOUT_FEATURES = (
        os.getenv("ENABLE_AUTO_CASHOUT_FEATURES", "False").lower() == "true"
    )  # True = Show auto cashout settings UI and run background jobs, False = Completely hide all auto cashout functionality
    
    # Separate auto cashout controls for different cashout types
    AUTO_CASHOUT_ENABLED_CRYPTO = (
        os.getenv("AUTO_CASHOUT_ENABLED_CRYPTO", "True").lower() == "true"
    )  # Auto cashout for crypto cashouts
    AUTO_CASHOUT_ENABLED_NGN = (
        os.getenv("AUTO_CASHOUT_ENABLED_NGN", "True").lower() == "true"
    )  # Auto cashout for NGN cashouts

    # Automatic Address Configuration Monitoring
    # When enabled, system automatically retries cashouts after admin adds addresses to Kraken
    # When disabled, admin must manually process pending_config cashouts via email alerts
    AUTO_ADDRESS_CONFIG_MONITORING_ENABLED = (
        os.getenv("AUTO_ADDRESS_CONFIG_MONITORING_ENABLED", "true").lower() == "true"
    )

    MIN_AUTO_CASHOUT_AMOUNT = Decimal(
        os.getenv("MIN_AUTO_CASHOUT_AMOUNT", "25.0")
    )  # Minimum amount for auto cashout in USD

    # Cashout limits
    MIN_CASHOUT_AMOUNT = Decimal(
        os.getenv("MIN_CASHOUT_AMOUNT", "1.0")
    )  # Minimum cashout amount in USD (reduced from $40 per user request)
    MAX_CASHOUT_AMOUNT = Decimal(
        os.getenv("MAX_CASHOUT_AMOUNT", "500.0")
    )  # Maximum cashout amount per transaction
    ADMIN_APPROVAL_THRESHOLD = Decimal(
        os.getenv("ADMIN_APPROVAL_THRESHOLD", "500.0")
    )  # Amount threshold for admin approval (when manual mode disabled)

    # NGN CashOut Settings (via Fincra bank transfers - uses brand markup)
    # Note: Now uses BRAND_NGN_EXCHANGE_MARKUP for consistency
    NGN_CASHOUT_MIN_FEE = Decimal(
        os.getenv("NGN_CASHOUT_MIN_FEE", "100.0")
    )  # ₦100 minimum
    NGN_CASHOUT_MAX_FEE = Decimal(
        os.getenv("NGN_CASHOUT_MAX_FEE", "2000.0")
    )  # ₦2000 maximum


    # Fee precision settings
    USD_DECIMAL_PLACES = int(os.getenv("USD_DECIMAL_PLACES", "2"))  # USD precision
    CRYPTO_DECIMAL_PLACES = int(
        os.getenv("CRYPTO_DECIMAL_PLACES", "8")
    )  # Crypto precision


    # Rate limiting
    MAX_ESCROWS_PER_USER_PER_DAY = int(os.getenv("MAX_ESCROWS_PER_USER_PER_DAY", "10"))
    MAX_MESSAGES_PER_HOUR = int(os.getenv("MAX_MESSAGES_PER_HOUR", "50"))
    MAX_CASHOUTS_PER_DAY = int(
        os.getenv("MAX_CASHOUTS_PER_DAY", "10")
    )  # Max cashout requests per user per day

    # Security and monitoring settings
    MAX_LOGIN_ATTEMPTS = int(
        os.getenv("MAX_LOGIN_ATTEMPTS", "5")
    )  # Max failed admin login attempts
    SECURITY_ALERT_THRESHOLD = int(
        os.getenv("SECURITY_ALERT_THRESHOLD", "10")
    )  # Failed attempts before alert
    AUTO_BAN_ENABLED = (
        os.getenv("AUTO_BAN_ENABLED", "True").lower() == "true"
    )  # Auto-ban suspicious users

    # Notification settings
    EMAIL_NOTIFICATIONS_ENABLED = (
        os.getenv("EMAIL_NOTIFICATIONS_ENABLED", "True").lower() == "true"
    )
    ADMIN_NOTIFICATION_ENABLED = (
        os.getenv("ADMIN_NOTIFICATION_ENABLED", "True").lower() == "true"
    )
    # DB-backed notification queue control (disabled by default to avoid model dependency issues)
    NOTIFICATION_DB_QUEUE_ENABLED = (
        os.getenv("NOTIFICATION_DB_QUEUE_ENABLED", "false").lower() == "true"
    )
    DISPUTE_ALERT_THRESHOLD = int(
        os.getenv("DISPUTE_ALERT_THRESHOLD", "24")
    )  # Hours before dispute escalation alert

    # Operational thresholds
    DAILY_VOLUME_ALERT = Decimal(
        os.getenv("DAILY_VOLUME_ALERT", "50000.0")
    )  # Daily transaction volume alert threshold

    # Forex and Rate Management Configuration
    FASTFOREX_API_KEY = os.getenv("FASTFOREX_API_KEY", "")  # FastForex API key (legacy, deprecated)
    TATUM_API_KEY = os.getenv("TATUM_API_KEY", "")  # Tatum API key (primary rate provider)
    BACKUP_FOREX_API_KEY = os.getenv(
        "BACKUP_FOREX_API_KEY", ""
    )  # Backup forex API key (optional)
    RATE_LOCK_DURATION_MINUTES = int(
        os.getenv("RATE_LOCK_DURATION_MINUTES", "10")
    )  # Rate lock duration - 10 minutes guaranteed rate protection
    ENABLE_RATE_LOCKING = (
        os.getenv("ENABLE_RATE_LOCKING", "true").lower() == "true"
    )  # Enable/disable rate locking

    # Exchange rate lock configuration
    CRYPTO_EXCHANGE_RATE_LOCK_MINUTES = int(
        os.getenv("CRYPTO_EXCHANGE_RATE_LOCK_MINUTES", "10")
    )  # Rate protection window - 10 minutes
    NGN_EXCHANGE_TIMEOUT_MINUTES = int(
        os.getenv("NGN_EXCHANGE_TIMEOUT_MINUTES", "15")
    )  # Timeout for NGN-to-crypto exchanges

    # Crypto Wallet Configuration (Issue #3 Fix: Configure missing wallet addresses)
    LTC_WALLET_ADDRESS = os.getenv("LTC_WALLET_ADDRESS", "LQopQQ2AANpV4RA2x2Rdkr2TPPKtNtMims")
    BTC_WALLET_ADDRESS = os.getenv("BTC_WALLET_ADDRESS", "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2")
    ETH_WALLET_ADDRESS = os.getenv("ETH_WALLET_ADDRESS", "0x742d35Cc6573C0532C07C3B7f5d2aF2f7d00e1A3")
    DOGE_WALLET_ADDRESS = os.getenv("DOGE_WALLET_ADDRESS", "DH5yaieqoZN36fDVciNyRueRGvGLR3mr7L")

    EXCHANGE_MARKUP_PERCENTAGE = _validate_markup_percentage(
        "EXCHANGE_MARKUP_PERCENTAGE", "5.0", 0.01, 50.0
    )  # Validated 5% markup with 0.01%-50% bounds
    
    # Unified crypto exchange markup (alias for compatibility)
    LOCKBAY_CRYPTO_EXCHANGE_MARKUP = _validate_markup_percentage(
        "LOCKBAY_CRYPTO_EXCHANGE_MARKUP", "2.0", 0.01, 50.0
    )  # Unified crypto cashout markup percentage
    
    # Additional unified crypto markup limits
    LOCKBAY_CRYPTO_MIN_MARKUP = Decimal(
        os.getenv("LOCKBAY_CRYPTO_MIN_MARKUP", "2.0")
    )  # Minimum crypto markup in USD
    LOCKBAY_CRYPTO_MAX_MARKUP = Decimal(
        os.getenv("LOCKBAY_CRYPTO_MAX_MARKUP", "100.0")
    )  # Maximum crypto markup in USD
    
    # Wallet deposit markup configuration - 2% profit when users add USD to wallet
    WALLET_DEPOSIT_MARKUP_PERCENTAGE = _validate_markup_percentage(
        "WALLET_DEPOSIT_MARKUP_PERCENTAGE", "2.0", 0.01, 10.0
    )  # Validated 2% wallet deposit markup with 0.01%-10% bounds
    
    # PAYMENT TOLERANCE SETTINGS
    UNDERPAYMENT_TOLERANCE_USD = float(os.getenv("UNDERPAYMENT_TOLERANCE_USD", "1.0"))  # $1 tolerance for underpayments
    DEFAULT_RATE_TOLERANCE = Decimal(
        os.getenv("DEFAULT_RATE_TOLERANCE", "0.05")
    )  # 5% tolerance for rate fluctuations

    # Replit Domain Configuration
    REPLIT_DOMAIN = os.getenv(
        "REPLIT_DOMAIN", "webhook-url.replit.app"
    )  # Auto-detected or configured domain

    # SIMPLIFIED REVENUE MODEL: Flat fees for crypto and NGN cashout
    # TRC20: Kraken blockchain fee + configurable markup
    TRC20_FLAT_FEE_USD = Decimal(
        os.getenv("TRC20_FLAT_FEE_USD", "4.0")
    )  # $4 configurable fee (plus Kraken blockchain fee)

    # ERC20: Kraken blockchain fee + configurable markup
    ERC20_FLAT_FEE_USD = Decimal(
        os.getenv("ERC20_FLAT_FEE_USD", "4.0")
    )  # $4 configurable fee (plus Kraken blockchain fee)

    # NGN: Fixed cashout fee
    NGN_FLAT_FEE_NAIRA = Decimal(
        os.getenv("NGN_FLAT_FEE_NAIRA", "2000.0")
    )  # ₦2000 configurable fee

    # Auto-release settings
    AUTO_RELEASE_ENABLED = os.getenv("AUTO_RELEASE_ENABLED", "True").lower() == "true"
    AUTO_RELEASE_HOURS = int(os.getenv("AUTO_RELEASE_HOURS", "72"))  # 72 hours default

    # Manual Refunds Only Configuration
    # When enabled, disables all automatic refunds and requires admin intervention
    MANUAL_REFUNDS_ONLY = os.getenv("MANUAL_REFUNDS_ONLY", "true").lower() == "true"  # ENABLED: Manual refunds only
    DISABLE_REFUND_JOBS = os.getenv("DISABLE_REFUND_JOBS", "true").lower() == "true"  # ENABLED: Disable scheduled refund jobs
    DISABLE_WEBHOOK_AUTO_REFUNDS = os.getenv("DISABLE_WEBHOOK_AUTO_REFUNDS", "true").lower() == "true"  # ENABLED: Disable webhook auto-refunds

    # ===== UNIFIED BALANCE MONITORING CONFIGURATION =====
    # Single source of truth for all balance monitoring across all services
    # Used by BalanceGuard, EnhancedBalanceProtection, BalanceMonitor, and related services
    
    # Base Balance Thresholds (these are the 100% reference points)
    FINCRA_LOW_BALANCE_THRESHOLD = Decimal(
        os.getenv("BALANCE_ALERT_FINCRA_THRESHOLD_NGN", "5000.0")
    )  # ₦5,000 realistic threshold for operational danger alerts
    
    KRAKEN_LOW_BALANCE_THRESHOLD_USD = Decimal(
        os.getenv("BALANCE_ALERT_KRAKEN_THRESHOLD_USD", "20.0")
    )  # $20 realistic threshold for combined crypto balance alerts
    
    # Multi-Tier Alert Level Percentages (applied to base thresholds)
    # These create graduated alerts as balances decline
    BALANCE_ALERT_WARNING_PERCENT = Decimal(os.getenv("BALANCE_ALERT_WARNING_PERCENT", "75.0"))    # 75% of base = Warning
    BALANCE_ALERT_CRITICAL_PERCENT = Decimal(os.getenv("BALANCE_ALERT_CRITICAL_PERCENT", "50.0"))   # 50% of base = Critical
    BALANCE_ALERT_EMERGENCY_PERCENT = Decimal(os.getenv("BALANCE_ALERT_EMERGENCY_PERCENT", "25.0"))  # 25% of base = Emergency
    BALANCE_ALERT_OPERATIONAL_PERCENT = Decimal(os.getenv("BALANCE_ALERT_OPERATIONAL_PERCENT", "10.0"))  # 10% of base = Operations Blocked
    
    # Per-Alert-Level Cooldown Periods (hours)
    # All levels set to 12 hours so each alert fires at most twice per day
    BALANCE_ALERT_COOLDOWN_WARNING_HOURS = int(os.getenv("BALANCE_ALERT_COOLDOWN_WARNING_HOURS", "24"))        # Warning: 24 hours (once daily)
    BALANCE_ALERT_COOLDOWN_CRITICAL_HOURS = int(os.getenv("BALANCE_ALERT_COOLDOWN_CRITICAL_HOURS", "24"))      # Critical: 24 hours (once daily)
    BALANCE_ALERT_COOLDOWN_EMERGENCY_HOURS = int(os.getenv("BALANCE_ALERT_COOLDOWN_EMERGENCY_HOURS", "24"))    # Emergency: 24 hours (once daily)
    BALANCE_ALERT_COOLDOWN_OPERATIONAL_HOURS = int(os.getenv("BALANCE_ALERT_COOLDOWN_OPERATIONAL_HOURS", "24")) # Operational: 24 hours (once daily)
    
    # Balance Check Intervals (minutes) - How often to check balances
    FINCRA_BALANCE_CHECK_INTERVAL = int(
        os.getenv("FINCRA_BALANCE_CHECK_INTERVAL", "30")
    )  # 30 minutes between Fincra balance checks
    
    KRAKEN_BALANCE_CHECK_INTERVAL = int(
        os.getenv("KRAKEN_BALANCE_CHECK_INTERVAL", "30")
    )  # 30 minutes between Kraken balance checks

    # Daily Balance Email Reports (separate from low balance alerts)
    # These are scheduled reports sent at specific times regardless of balance status
    BALANCE_EMAIL_ENABLED = os.getenv("BALANCE_EMAIL_ENABLED", "true").lower() == "true"  # Enable daily balance reports
    BALANCE_EMAIL_TIMES = os.getenv("BALANCE_EMAIL_TIMES", "09:00,21:00")  # Twice daily: 9 AM and 9 PM UTC
    BALANCE_EMAIL_FREQUENCY_HOURS = int(
        os.getenv("BALANCE_EMAIL_FREQUENCY_HOURS", "12")
    )  # Every 12 hours for twice daily reports
    
    # Configuration Validation and Helper Methods
    @staticmethod
    def get_fincra_balance_thresholds():
        """Get calculated Fincra balance thresholds for all alert levels"""
        base = Config.FINCRA_LOW_BALANCE_THRESHOLD
        return {
            'base': base,
            'warning': base * (Config.BALANCE_ALERT_WARNING_PERCENT / Decimal('100')),
            'critical': base * (Config.BALANCE_ALERT_CRITICAL_PERCENT / Decimal('100')),
            'emergency': base * (Config.BALANCE_ALERT_EMERGENCY_PERCENT / Decimal('100')),
            'operational': base * (Config.BALANCE_ALERT_OPERATIONAL_PERCENT / Decimal('100'))
        }
    
    @staticmethod
    def get_kraken_balance_thresholds():
        """Get calculated Kraken balance thresholds for all alert levels"""
        base = Config.KRAKEN_LOW_BALANCE_THRESHOLD_USD
        return {
            'base': base,
            'warning': base * (Config.BALANCE_ALERT_WARNING_PERCENT / Decimal('100')),
            'critical': base * (Config.BALANCE_ALERT_CRITICAL_PERCENT / Decimal('100')),
            'emergency': base * (Config.BALANCE_ALERT_EMERGENCY_PERCENT / Decimal('100')),
            'operational': base * (Config.BALANCE_ALERT_OPERATIONAL_PERCENT / Decimal('100'))
        }
    
    @staticmethod
    def get_balance_alert_cooldowns():
        """Get balance alert cooldown periods for all alert levels (in hours)"""
        return {
            'warning': Config.BALANCE_ALERT_COOLDOWN_WARNING_HOURS,
            'critical': Config.BALANCE_ALERT_COOLDOWN_CRITICAL_HOURS,
            'emergency': Config.BALANCE_ALERT_COOLDOWN_EMERGENCY_HOURS,
            'operational': Config.BALANCE_ALERT_COOLDOWN_OPERATIONAL_HOURS
        }
    
    @staticmethod
    def validate_balance_configuration():
        """Validate balance monitoring configuration and log settings"""
        logger.info("🔧 Unified Balance Monitoring Configuration:")
        logger.info(f"   Fincra Base Threshold: ₦{Config.FINCRA_LOW_BALANCE_THRESHOLD:,.2f}")
        logger.info(f"   Kraken Base Threshold: ${Config.KRAKEN_LOW_BALANCE_THRESHOLD_USD:,.2f} USD")
        
        # Log calculated thresholds
        fincra_thresholds = Config.get_fincra_balance_thresholds()
        kraken_thresholds = Config.get_kraken_balance_thresholds()
        
        logger.info("   Fincra Alert Levels:")
        logger.info(f"     Warning: ₦{fincra_thresholds['warning']:,.2f} ({Config.BALANCE_ALERT_WARNING_PERCENT}%)")
        logger.info(f"     Critical: ₦{fincra_thresholds['critical']:,.2f} ({Config.BALANCE_ALERT_CRITICAL_PERCENT}%)")
        logger.info(f"     Emergency: ₦{fincra_thresholds['emergency']:,.2f} ({Config.BALANCE_ALERT_EMERGENCY_PERCENT}%)")
        logger.info(f"     Operational: ₦{fincra_thresholds['operational']:,.2f} ({Config.BALANCE_ALERT_OPERATIONAL_PERCENT}%)")
        
        logger.info("   Kraken Alert Levels:")
        logger.info(f"     Warning: ${kraken_thresholds['warning']:,.2f} ({Config.BALANCE_ALERT_WARNING_PERCENT}%)")
        logger.info(f"     Critical: ${kraken_thresholds['critical']:,.2f} ({Config.BALANCE_ALERT_CRITICAL_PERCENT}%)")
        logger.info(f"     Emergency: ${kraken_thresholds['emergency']:,.2f} ({Config.BALANCE_ALERT_EMERGENCY_PERCENT}%)")
        logger.info(f"     Operational: ${kraken_thresholds['operational']:,.2f} ({Config.BALANCE_ALERT_OPERATIONAL_PERCENT}%)")
        
        # Log cooldown periods
        cooldowns = Config.get_balance_alert_cooldowns()
        logger.info("   Alert Cooldowns:")
        logger.info(f"     Warning: {cooldowns['warning']}h, Critical: {cooldowns['critical']}h")
        logger.info(f"     Emergency: {cooldowns['emergency']}h, Operational: {cooldowns['operational']}h")
        
        # Log check intervals
        logger.info(f"   Check Intervals: Fincra={Config.FINCRA_BALANCE_CHECK_INTERVAL}min, Kraken={Config.KRAKEN_BALANCE_CHECK_INTERVAL}min")
        
        logger.info("✅ Balance monitoring configuration validated")
        return True

    # Kraken Configuration for Alternative Withdrawals
    KRAKEN_API_KEY = os.getenv("KRAKEN_API_KEY")
    KRAKEN_SECRET_KEY = os.getenv("KRAKEN_SECRET_KEY") or os.getenv("KRAKEN_PRIVATE_KEY")
    KRAKEN_ENABLED = bool(KRAKEN_API_KEY and KRAKEN_SECRET_KEY)
    
    # Kraken minimum withdrawal amounts (in crypto units - from official Kraken docs)
    KRAKEN_MINIMUM_WITHDRAWALS_CRYPTO = {
        "BTC": Decimal("0.00021800"),  # Official: 0.00021800 BTC
        "ETH": Decimal("0.00500000"),  # Official: 0.005 ETH  
        "LTC": Decimal("0.00400000"),  # Official: 0.004 LTC
        "DOGE": Decimal("4.00000000"), # Official: 4 DOGE
        "USDT": Decimal("10.0000000"), # Official: 10 USDT (ERC-20)
        "TRX": Decimal("100.000000"),  # Official: 100 TRX
        # "XMR": Decimal("0.00100000"),  # Removed: FastForex API doesn't support XMR rates
    }
    
    # Withdrawal provider selection (Kraken only)
    WITHDRAWAL_PROVIDER = os.getenv("WITHDRAWAL_PROVIDER", "kraken")  # kraken only
    KRAKEN_WITHDRAWAL_TIMEOUT = int(os.getenv("KRAKEN_WITHDRAWAL_TIMEOUT", "30"))  # 30 seconds


    # Fincra Configuration for NGN Payments (Superior to Flutterwave - 1% fees capped at ₦250)
    FINCRA_SECRET_KEY = os.getenv("FINCRA_API_KEY", os.getenv("FINCRA_SECRET_KEY", ""))  # Use API_KEY as primary, fallback to SECRET_KEY
    FINCRA_PUBLIC_KEY = os.getenv(
        "FINCRA_PUBLIC_KEY", ""
    )  # Public key for API authentication
    FINCRA_WEBHOOK_KEY = os.getenv("FINCRA_WEBHOOK_KEY", "")  # Webhook/encryption key
    FINCRA_WEBHOOK_ENCRYPTION_KEY = os.getenv("FINCRA_WEBHOOK_ENCRYPTION_KEY", FINCRA_WEBHOOK_KEY)  # Fincra's webhook/encryption key for signature verification
    FINCRA_BUSINESS_ID = os.getenv("FINCRA_BUSINESS_ID", "")
    FINCRA_ENABLED = bool(FINCRA_SECRET_KEY and FINCRA_PUBLIC_KEY)
    FINCRA_TEST_MODE = os.getenv("FINCRA_TEST_MODE", "false").lower() == "true"
    # CRITICAL: Use production API for real payments (Updated to v2 API)
    FINCRA_BASE_URL = (
        "https://sandboxapi.fincra.com/v2"
        if FINCRA_TEST_MODE
        else "https://api.fincra.com/v2"
    )

    # SMS Configuration for Trade Invitations Only
    SMS_INVITATIONS_ENABLED = os.getenv("SMS_INVITATIONS_ENABLED", "true").lower() == "true"
    SMS_MIN_TRADING_VOLUME_USD = Decimal(os.getenv("SMS_MIN_TRADING_VOLUME_USD", "100.0"))  # Minimum $100 traded
    SMS_DAILY_LIMIT_PER_USER = int(os.getenv("SMS_DAILY_LIMIT_PER_USER", "2"))  # Max 2 SMS per user per day
    
    # Twilio Configuration (for SMS invitations only)
    TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
    TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
    TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "")
    TWILIO_ENABLED = bool(TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_PHONE_NUMBER and SMS_INVITATIONS_ENABLED)


    # Binance removed - now using Kraken for all crypto operations
    HIGH_VALUE_TRANSACTION = Decimal(
        os.getenv("HIGH_VALUE_TRANSACTION", "5000.0")
    )  # High value transaction alert
    
    # Maintenance Mode Configuration
    # Cache for maintenance mode to avoid database queries on every request
    _maintenance_mode_cache: Dict[str, Any] = {}
    _maintenance_mode_cache_ttl = 30  # Cache for 30 seconds
    
    # Performance telemetry helpers
    @staticmethod
    def _record_cache_hit():
        try:
            from utils.performance_telemetry import telemetry
            telemetry.record_cache_hit('maintenance_mode')
        except Exception:
            pass
    
    @staticmethod
    def _record_cache_miss():
        try:
            from utils.performance_telemetry import telemetry
            telemetry.record_cache_miss('maintenance_mode')
        except Exception:
            pass
    
    @staticmethod
    def get_maintenance_mode() -> bool:
        """
        Get maintenance mode status from database (runtime toggle)
        Falls back to environment variable if database unavailable
        Uses caching to minimize database queries
        """
        import time
        
        # Check cache first
        current_time = time.time()
        cache_key = "maintenance_mode"
        
        if (cache_key in Config._maintenance_mode_cache and 
            current_time - Config._maintenance_mode_cache.get("timestamp", 0) < Config._maintenance_mode_cache_ttl):
            return Config._maintenance_mode_cache[cache_key]
        
        # Try to fetch from database
        try:
            from sqlalchemy import text
            from database import SessionLocal
            
            with SessionLocal() as session:
                result = session.execute(
                    text("SELECT value FROM system_config WHERE key = 'maintenance_mode'")
                ).fetchone()
                
                if result:
                    is_maintenance = result[0].lower() == 'true'
                    # Update cache
                    Config._maintenance_mode_cache[cache_key] = is_maintenance
                    Config._maintenance_mode_cache["timestamp"] = current_time
                    return is_maintenance
        except Exception as e:
            logger.warning(f"Failed to fetch maintenance mode from database: {e}")
        
        # Fallback to environment variable
        env_value = os.getenv("MAINTENANCE_MODE", "False").lower() == "true"
        # Cache the fallback value
        Config._maintenance_mode_cache[cache_key] = env_value
        Config._maintenance_mode_cache["timestamp"] = current_time
        return env_value
    
    @staticmethod
    def set_maintenance_mode(enabled: bool, admin_user_id: Optional[int] = None) -> bool:
        """
        Set maintenance mode in database for runtime toggling
        
        Args:
            enabled: True to enable maintenance mode, False to disable
            admin_user_id: ID of admin user making the change
        
        Returns:
            bool: True if successfully updated, False otherwise
        """
        try:
            from sqlalchemy import text
            from database import SessionLocal
            
            with SessionLocal() as session:
                # UPSERT: Create row if it doesn't exist, update if it does
                # This ensures production databases without migration data still work
                session.execute(
                    text("""
                        INSERT INTO system_config (key, value, value_type, description, is_public, is_encrypted, updated_by, updated_at)
                        VALUES ('maintenance_mode', :value, 'boolean', 'Global maintenance mode - when true, only admins can access the bot', false, false, :admin_id, NOW())
                        ON CONFLICT (key) 
                        DO UPDATE SET value = :value, updated_at = NOW(), updated_by = :admin_id
                    """),
                    {"value": "true" if enabled else "false", "admin_id": admin_user_id}
                )
                session.commit()
                
                # Clear cache to force fresh read
                Config._maintenance_mode_cache.clear()
                
                logger.warning(
                    f"🛠️ MAINTENANCE MODE {'ENABLED' if enabled else 'DISABLED'} "
                    f"by admin {admin_user_id or 'system'}"
                )
                return True
        except Exception as e:
            logger.error(f"Failed to set maintenance mode in database: {e}")
            return False
    
    @staticmethod
    def get_maintenance_message() -> str:
        """Get the maintenance message to display to users (with caching)"""
        import time
        
        # Check cache first
        current_time = time.time()
        cache_key = "maintenance_message"
        
        if (cache_key in Config._maintenance_mode_cache and 
            current_time - Config._maintenance_mode_cache.get("msg_timestamp", 0) < Config._maintenance_mode_cache_ttl):
            Config._record_cache_hit()
            return Config._maintenance_mode_cache[cache_key]
        
        Config._record_cache_miss()
        
        try:
            from sqlalchemy import text
            from database import SessionLocal
            
            with SessionLocal() as session:
                result = session.execute(
                    text("SELECT value FROM system_config WHERE key = 'maintenance_message'")
                ).fetchone()
                
                if result:
                    # Update cache
                    Config._maintenance_mode_cache[cache_key] = result[0]
                    Config._maintenance_mode_cache["msg_timestamp"] = current_time
                    return result[0]
        except Exception as e:
            logger.warning(f"Failed to fetch maintenance message from database: {e}")
        
        # Fallback message
        fallback = "🔧 System maintenance in progress. We'll be back shortly!"
        Config._maintenance_mode_cache[cache_key] = fallback
        Config._maintenance_mode_cache["msg_timestamp"] = current_time
        return fallback
    
    @staticmethod
    def get_maintenance_duration() -> Optional[int]:
        """Get the maintenance duration in minutes (with caching)"""
        import time
        
        # Check cache first
        current_time = time.time()
        cache_key = "maintenance_duration"
        
        if (cache_key in Config._maintenance_mode_cache and 
            current_time - Config._maintenance_mode_cache.get("dur_timestamp", 0) < Config._maintenance_mode_cache_ttl):
            Config._record_cache_hit()
            return Config._maintenance_mode_cache[cache_key]
        
        Config._record_cache_miss()
        
        try:
            from sqlalchemy import text
            from database import SessionLocal
            
            with SessionLocal() as session:
                result = session.execute(
                    text("SELECT value FROM system_config WHERE key = 'maintenance_duration'")
                ).fetchone()
                
                if result and result[0]:
                    duration = int(result[0]) if result[0].strip() else None
                    Config._maintenance_mode_cache[cache_key] = duration
                    Config._maintenance_mode_cache["dur_timestamp"] = current_time
                    return duration
        except Exception as e:
            logger.warning(f"Failed to fetch maintenance duration from database: {e}")
        
        Config._maintenance_mode_cache[cache_key] = None
        Config._maintenance_mode_cache["dur_timestamp"] = current_time
        return None
    
    @staticmethod
    def get_maintenance_start_time() -> Optional[str]:
        """Get the maintenance start timestamp (with caching)"""
        import time
        
        # Check cache first
        current_time = time.time()
        cache_key = "maintenance_start_time"
        
        if (cache_key in Config._maintenance_mode_cache and 
            current_time - Config._maintenance_mode_cache.get("start_timestamp", 0) < Config._maintenance_mode_cache_ttl):
            Config._record_cache_hit()
            return Config._maintenance_mode_cache[cache_key]
        
        Config._record_cache_miss()
        
        try:
            from sqlalchemy import text
            from database import SessionLocal
            
            with SessionLocal() as session:
                result = session.execute(
                    text("SELECT value FROM system_config WHERE key = 'maintenance_start_time'")
                ).fetchone()
                
                if result and result[0]:
                    start_time = result[0] if result[0].strip() else None
                    Config._maintenance_mode_cache[cache_key] = start_time
                    Config._maintenance_mode_cache["start_timestamp"] = current_time
                    return start_time
        except Exception as e:
            logger.warning(f"Failed to fetch maintenance start time from database: {e}")
        
        Config._maintenance_mode_cache[cache_key] = None
        Config._maintenance_mode_cache["start_timestamp"] = current_time
        return None
    
    @staticmethod
    def get_maintenance_end_time() -> Optional[str]:
        """Get the maintenance end timestamp (with caching)"""
        import time
        
        # Check cache first
        current_time = time.time()
        cache_key = "maintenance_end_time"
        
        if (cache_key in Config._maintenance_mode_cache and 
            current_time - Config._maintenance_mode_cache.get("end_timestamp", 0) < Config._maintenance_mode_cache_ttl):
            Config._record_cache_hit()
            return Config._maintenance_mode_cache[cache_key]
        
        Config._record_cache_miss()
        
        try:
            from sqlalchemy import text
            from database import SessionLocal
            
            with SessionLocal() as session:
                result = session.execute(
                    text("SELECT value FROM system_config WHERE key = 'maintenance_end_time'")
                ).fetchone()
                
                if result and result[0]:
                    end_time = result[0] if result[0].strip() else None
                    Config._maintenance_mode_cache[cache_key] = end_time
                    Config._maintenance_mode_cache["end_timestamp"] = current_time
                    return end_time
        except Exception as e:
            logger.warning(f"Failed to fetch maintenance end time from database: {e}")
        
        Config._maintenance_mode_cache[cache_key] = None
        Config._maintenance_mode_cache["end_timestamp"] = current_time
        return None
    
    @staticmethod
    def get_maintenance_time_remaining() -> Optional[int]:
        """
        Calculate remaining time for maintenance in seconds
        Returns None if maintenance has no duration set or has expired
        """
        from datetime import datetime, timezone
        
        duration = Config.get_maintenance_duration()
        start_time_str = Config.get_maintenance_start_time()
        
        if not duration or not start_time_str:
            return None
        
        try:
            start_time = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
            current_time = datetime.now(timezone.utc)
            
            elapsed_seconds = (current_time - start_time).total_seconds()
            total_seconds = duration * 60
            remaining_seconds = int(total_seconds - elapsed_seconds)
            
            return remaining_seconds if remaining_seconds > 0 else 0
        except Exception as e:
            logger.warning(f"Failed to calculate maintenance time remaining: {e}")
            return None
    
    @staticmethod
    def set_maintenance_duration(duration_minutes: Optional[int], admin_user_id: Optional[int] = None) -> bool:
        """
        Set maintenance duration and timing information
        
        Args:
            duration_minutes: Duration in minutes (None for unspecified)
            admin_user_id: ID of admin user making the change
        
        Returns:
            bool: True if successfully updated, False otherwise
        """
        from datetime import datetime, timezone, timedelta
        
        try:
            from sqlalchemy import text
            from database import SessionLocal
            
            start_time = datetime.now(timezone.utc)
            end_time = start_time + timedelta(minutes=duration_minutes) if duration_minutes else None
            
            with SessionLocal() as session:
                # UPSERT: Create rows if they don't exist (for production databases without migration data)
                session.execute(
                    text("""
                        INSERT INTO system_config (key, value, value_type, description, is_public, is_encrypted, updated_by, updated_at)
                        VALUES ('maintenance_duration', :value, 'integer', 'Estimated maintenance duration in minutes (NULL for unspecified)', false, false, :admin_id, NOW())
                        ON CONFLICT (key) 
                        DO UPDATE SET value = :value, updated_at = NOW(), updated_by = :admin_id
                    """),
                    {"value": str(duration_minutes) if duration_minutes else "", "admin_id": admin_user_id}
                )
                
                session.execute(
                    text("""
                        INSERT INTO system_config (key, value, value_type, description, is_public, is_encrypted, updated_by, updated_at)
                        VALUES ('maintenance_start_time', :value, 'timestamp', 'Timestamp when maintenance mode was enabled', false, false, :admin_id, NOW())
                        ON CONFLICT (key) 
                        DO UPDATE SET value = :value, updated_at = NOW(), updated_by = :admin_id
                    """),
                    {"value": start_time.isoformat(), "admin_id": admin_user_id}
                )
                
                session.execute(
                    text("""
                        INSERT INTO system_config (key, value, value_type, description, is_public, is_encrypted, updated_by, updated_at)
                        VALUES ('maintenance_end_time', :value, 'timestamp', 'Calculated end time for maintenance (start + duration)', false, false, :admin_id, NOW())
                        ON CONFLICT (key) 
                        DO UPDATE SET value = :value, updated_at = NOW(), updated_by = :admin_id
                    """),
                    {"value": end_time.isoformat() if end_time else "", "admin_id": admin_user_id}
                )
                
                session.commit()
                
                duration_text = f"{duration_minutes} minutes" if duration_minutes else "unspecified duration"
                logger.info(
                    f"🛠️ MAINTENANCE DURATION SET: {duration_text} "
                    f"by admin {admin_user_id or 'system'}"
                )
                return True
        except Exception as e:
            logger.error(f"Failed to set maintenance duration in database: {e}")
            return False
    
    @staticmethod
    def clear_maintenance_duration() -> bool:
        """Clear maintenance duration and timing information when disabling maintenance"""
        try:
            from sqlalchemy import text
            from database import SessionLocal
            
            with SessionLocal() as session:
                session.execute(
                    text("""
                        UPDATE system_config 
                        SET value = '', updated_at = NOW()
                        WHERE key IN ('maintenance_duration', 'maintenance_start_time', 'maintenance_end_time')
                    """)
                )
                session.commit()
                
                logger.info("🛠️ MAINTENANCE DURATION CLEARED")
                return True
        except Exception as e:
            logger.error(f"Failed to clear maintenance duration in database: {e}")
            return False
    
    # Property for backward compatibility
    MAINTENANCE_MODE = property(lambda self: Config.get_maintenance_mode())

    # Fincra Nigeria Naira Payment Configuration (moved to main section above)
    # Configuration now uses FINCRA_SECRET_KEY and FINCRA_PUBLIC_KEY properly
    FINCRA_ENABLED = (
        os.getenv("FINCRA_ENABLED", "true").lower() == "true"
    )  # Enable Fincra payments
    FINCRA_MIN_AMOUNT_NGN = Decimal(
        os.getenv("FINCRA_MIN_AMOUNT_NGN", "100.00")
    )  # Minimum ₦100
    MIN_FINCRA_FUNDING_USD = Decimal(
        os.getenv("MIN_FINCRA_FUNDING_USD", "2.0")
    )  # Minimum $2 USD funding
    FINCRA_MAX_AMOUNT_NGN = Decimal(
        os.getenv("FINCRA_MAX_AMOUNT_NGN", "5000000.00")
    )  # Maximum ₦5M

    # Additional Configuration Variables
    DEFAULT_RATE_TOLERANCE = Decimal(os.getenv("DEFAULT_RATE_TOLERANCE", "0.05"))
    ENABLE_RATE_LOCKING = os.getenv("ENABLE_RATE_LOCKING", "true").lower() == "true"
    CRYPTO_EXCHANGE_RATE_LOCK_MINUTES = int(
        os.getenv("CRYPTO_EXCHANGE_RATE_LOCK_MINUTES", "10")
    )
    NGN_EXCHANGE_TIMEOUT_MINUTES = int(os.getenv("NGN_EXCHANGE_TIMEOUT_MINUTES", "15"))
    DISPUTE_ALERT_THRESHOLD = int(os.getenv("DISPUTE_ALERT_THRESHOLD", "24"))
    DAILY_VOLUME_ALERT = Decimal(os.getenv("DAILY_VOLUME_ALERT", "50000.0"))
    EMAIL_NOTIFICATIONS_ENABLED = (
        os.getenv("EMAIL_NOTIFICATIONS_ENABLED", "true").lower() == "true"
    )
    BACKUP_DAILY_RETENTION = int(os.getenv("BACKUP_DAILY_RETENTION", "7"))
    BACKUP_WEEKLY_RETENTION = int(os.getenv("BACKUP_WEEKLY_RETENTION", "4"))
    BACKUP_MONTHLY_RETENTION = int(os.getenv("BACKUP_MONTHLY_RETENTION", "6"))

    # FastForex Configuration
    FASTFOREX_API_KEY = os.getenv("FASTFOREX_API_KEY", "")

    # Application settings
    # Debug Configuration - Production optimized
    DEBUG = os.getenv("DEBUG", "False").lower() == "true"
    
    # Force debug off in production for performance
    if os.getenv("REPL_ID"):  # Running on Replit
        DEBUG = False
    
    # Force production mode
    if os.getenv("ENVIRONMENT") == "production":
        DEBUG = False
    BOT_NAME = os.getenv("BOT_NAME", BRAND)

    # Email Configuration  
    COMPANY_EMAIL = os.getenv(
        "ADMIN_EMAIL", os.getenv("COMPANY_EMAIL", "admin@example.com")
    )  # Admin email for daily reports - uses ADMIN_EMAIL secret
    SUPPORT_URL = os.getenv(
        "SUPPORT_URL", "https://t.me/lockbaybot"
    )  # Support/help URL

    # Email alert frequency: instant, hourly, daily
    EMAIL_ALERT_FREQUENCY = os.getenv("EMAIL_ALERT_FREQUENCY", "instant").lower()

    # Admin email for all alerts (defaults to COMPANY_EMAIL if not set)
    ADMIN_ALERT_EMAIL = os.getenv("ADMIN_ALERT_EMAIL", COMPANY_EMAIL)


    # Timeout configurations
    REGISTRATION_TIMEOUT_SECONDS = int(os.getenv("REGISTRATION_TIMEOUT_SECONDS", "30"))


    # Webhook configuration
    USE_WEBHOOK = os.getenv("USE_WEBHOOK", "true").lower() == "true"
    WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
    WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "5000"))
    WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "0.0.0.0")
    WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "")
    WEBHOOK_SECRET_TOKEN = os.getenv(
        "WEBHOOK_SECRET_TOKEN", ""
    )  # Telegram webhook secret token (disabled for development)

    # Supported currencies for escrow payments (DynoPay only)
    # Note: XMR removed due to FastForex API limitations
    SUPPORTED_CURRENCIES = [
        "BTC",
        "ETH", 
        "LTC",
        "USDT-ERC20",
        "USDT-TRC20",
    ]

    # Supported currencies for cashouts (all cryptocurrencies + NGN for consistency)
    CASHOUT_CURRENCIES = SUPPORTED_CURRENCIES + ["NGN"]

    # Currency networks (DynoPay supported)
    CURRENCY_NETWORKS = {
        "BTC": ["Bitcoin"],
        "ETH": ["Ethereum"],
        "LTC": ["Litecoin"],
        "USDT-ERC20": ["ERC20"],
        "USDT-TRC20": ["TRC20"],
        "NGN": ["Bank Transfer"],
    }


    # Configurable blockchain/network fees (USD equivalent) using Decimal precision
    NETWORK_FEES = {
        "BTC": Decimal(os.getenv("NETWORK_FEE_BTC", "25.0")),  # ~$25 Bitcoin fee
        "ETH": Decimal(os.getenv("NETWORK_FEE_ETH", "10.0")),  # ~$10 Ethereum fee
        "LTC": Decimal(os.getenv("NETWORK_FEE_LTC", "1.0")),  # ~$1 Litecoin fee
        "USDT-ERC20": Decimal(
            os.getenv("NETWORK_FEE_USDT_ERC20", "5.0")
        ),  # ~$5 ERC20 fee
        "USDT-TRC20": Decimal(
            os.getenv("NETWORK_FEE_USDT_TRC20", "1.0")
        ),  # ~$1 TRC20 fee
    }

    # BlockBee API Configuration
    BLOCKBEE_API_KEY = os.getenv("BLOCKBEE_API_KEY", "")
    BLOCKBEE_BASE_URL = os.getenv("BLOCKBEE_BASE_URL", "https://api.blockbee.io")
    BLOCKBEE_WEBHOOK_SECRET = os.getenv("BLOCKBEE_WEBHOOK_SECRET", "")
    BLOCKBEE_REQUIRED_CONFIRMATIONS = int(
        os.getenv("BLOCKBEE_REQUIRED_CONFIRMATIONS", "1")
    )
    # DEPLOYMENT-FIRST: Auto-configure callback URL prioritizing deployment environment
    REPLIT_DEV_DOMAIN = os.getenv("REPLIT_DEV_DOMAIN")  # Development only
    REPL_ID = os.getenv("REPL_ID")  # Available in both dev and deployment
    REPLIT_DOMAINS = os.getenv("REPLIT_DOMAINS")  # Deployment domains
    DEPLOYMENT_MODE = os.getenv("DEPLOYMENT_MODE", "auto")

    # Determine base URL for webhooks based on environment detection
    manual_webhook_url = os.getenv("WEBHOOK_URL", "")
    production_url = "https://flockbaypaymentfixing-production.up.railway.app/webhook"
    
    if manual_webhook_url:
        # Manual override takes highest priority
        BASE_WEBHOOK_URL = manual_webhook_url
        logger.info(f"🏠 Config: Using MANUAL URL: {BASE_WEBHOOK_URL}")
    elif IS_PRODUCTION:
        # Use production URL only if in production environment
        BASE_WEBHOOK_URL = production_url
        logger.info(f"🚀 Config: Using PRODUCTION URL: {BASE_WEBHOOK_URL}")
    elif REPLIT_DOMAINS:
        # Use current replit domain for development
        domain = REPLIT_DOMAINS.split(',')[0].strip()
        BASE_WEBHOOK_URL = f"https://{domain}/webhook"
        logger.info(f"🔧 Config: Using DEVELOPMENT URL: {BASE_WEBHOOK_URL}")
    elif REPLIT_DEV_DOMAIN:
        # Fallback to dev domain
        BASE_WEBHOOK_URL = f"https://{REPLIT_DEV_DOMAIN}/webhook"
        logger.info(f"🔧 Config: Using DEV FALLBACK: {BASE_WEBHOOK_URL}")
    else:
        logger.warning("⚠️  Config: No webhook URL configured")
        BASE_WEBHOOK_URL = ""

    # Set BlockBee callback URL - FIXED to match router path
    BLOCKBEE_CALLBACK_URL = os.getenv(
        "BLOCKBEE_CALLBACK_URL",
        f"{BASE_WEBHOOK_URL.replace('/webhook', '')}/blockbee/callback" if BASE_WEBHOOK_URL else "",
    )
    BLOCKBEE_USDT_TRC20_ADDRESS = os.getenv("BLOCKBEE_USDT_TRC20_ADDRESS", "")
    BLOCKBEE_USDT_ERC20_ADDRESS = os.getenv("BLOCKBEE_USDT_ERC20_ADDRESS", "")

    # Webhook URLs for all services
    WEBHOOK_URL = os.getenv("WEBHOOK_URL", BASE_WEBHOOK_URL)

    # Service-specific webhook URLs (ALL services use same base URL for consistency)
    # Note: WEBHOOK_URL already includes "/webhook" path for Telegram
    # Other services need paths appended to base URL without /webhook suffix
    TELEGRAM_WEBHOOK_URL = WEBHOOK_URL if WEBHOOK_URL else ""
    
    # Get base URL without /webhook suffix for other services
    BASE_URL_NO_WEBHOOK = WEBHOOK_URL.replace('/webhook', '') if WEBHOOK_URL else ""
    
    FINCRA_WEBHOOK_URL = f"{BASE_URL_NO_WEBHOOK}/webhook/api/fincra/webhook" if WEBHOOK_URL else ""
    TWILIO_WEBHOOK_URL = f"{BASE_URL_NO_WEBHOOK}/webhook/twilio" if WEBHOOK_URL else ""
    SMS_WEBHOOK_URL = f"{BASE_URL_NO_WEBHOOK}/webhook/sms" if WEBHOOK_URL else ""

    # Log webhook configuration for verification
    if WEBHOOK_URL:
        logger.info("✅ ALL WEBHOOKS CONFIGURED:")
        logger.info(f"   📱 Telegram: {TELEGRAM_WEBHOOK_URL}")
        logger.info(f"   💳 DynoPay: {DYNOPAY_WEBHOOK_URL if DYNOPAY_WEBHOOK_URL else 'Not configured'}")
        logger.info(f"   💰 BlockBee: {BLOCKBEE_CALLBACK_URL}")
        logger.info(f"   🏦 Fincra: {FINCRA_WEBHOOK_URL}")
        logger.info(f"   📞 Twilio: {TWILIO_WEBHOOK_URL}")
        logger.info(f"   📨 SMS: {SMS_WEBHOOK_URL}")
    else:
        logger.warning("⚠️  Warning: WEBHOOK_URL not set - webhooks disabled")
    
    # ===== ADMIN ACTION BASE URL (for persistent email action buttons) =====
    # This URL is used for admin email action buttons and MUST be persistent across restarts
    # Auto-detects environment URL using REPLIT_DOMAINS (same as webhook URLs)
    
    # Allow environment variable override for custom domains
    manual_admin_url = os.getenv("ADMIN_ACTION_BASE_URL", "")
    
    if manual_admin_url:
        # Manual override takes priority (for custom domains)
        ADMIN_ACTION_BASE_URL = manual_admin_url
        logger.info(f"🏠 Admin Action: Using MANUAL override: {ADMIN_ACTION_BASE_URL}")
    elif REPLIT_DOMAINS:
        # Auto-detect from REPLIT_DOMAINS (works in both dev and production)
        domain = REPLIT_DOMAINS.split(',')[0].strip()
        ADMIN_ACTION_BASE_URL = f"https://{domain}"
        logger.info(f"🔧 Admin Action: Auto-detected from environment: {ADMIN_ACTION_BASE_URL}")
    elif BASE_URL_NO_WEBHOOK:
        # Fallback to webhook base URL
        ADMIN_ACTION_BASE_URL = BASE_URL_NO_WEBHOOK
        logger.info(f"🔄 Admin Action: Using webhook base URL: {ADMIN_ACTION_BASE_URL}")
    else:
        # Last resort fallback (should rarely happen)
        ADMIN_ACTION_BASE_URL = "https://lockbayescrow-production.up.railway.app"
        logger.warning(f"⚠️  Admin Action: Using hardcoded fallback: {ADMIN_ACTION_BASE_URL}")
    
    # Validation: Ensure URL is properly configured
    if not ADMIN_ACTION_BASE_URL or ADMIN_ACTION_BASE_URL == "":
        raise ValueError("❌ CRITICAL: ADMIN_ACTION_BASE_URL could not be determined!")
    
    # Production validation
    if IS_PRODUCTION:
        # Ensure HTTPS in production
        if not ADMIN_ACTION_BASE_URL.startswith("https://"):
            raise ValueError(
                f"❌ CRITICAL: ADMIN_ACTION_BASE_URL must use HTTPS in production: {ADMIN_ACTION_BASE_URL}"
            )
        
        # Warn about ephemeral dev domains (but don't fail - REPLIT_DOMAINS should be persistent)
        if ".repl.co" in ADMIN_ACTION_BASE_URL:
            logger.warning(f"⚠️  Warning: Using .repl.co domain: {ADMIN_ACTION_BASE_URL}")
            logger.warning("   Note: Replit deployment domains (.replit.app) are persistent, .repl.co domains are not")
    
    # Development warning for ephemeral domains
    if REPLIT_DEV_DOMAIN and REPLIT_DEV_DOMAIN in ADMIN_ACTION_BASE_URL:
        logger.warning(f"⚠️  Development mode: Using ephemeral domain: {ADMIN_ACTION_BASE_URL}")
        logger.warning("   Email action buttons will break after restart in production!")
    
    # ===== PUBLIC PROFILE BASE URL (for customer-facing branded links) =====
    # This URL is used for public profile pages and referral landing pages
    # Auto-detects environment URL, but can use custom branded domain (e.g., lockbay.io)
    # IMPORTANT: This is SEPARATE from ADMIN_ACTION_BASE_URL which handles webhooks
    
    # Allow environment variable override for branded domains
    manual_profile_url = os.getenv("PUBLIC_PROFILE_BASE_URL", "")
    
    if manual_profile_url:
        # Manual override takes priority (for custom branded domains)
        PUBLIC_PROFILE_BASE_URL = manual_profile_url
        logger.info(f"🏠 Public Profile: Using MANUAL override: {PUBLIC_PROFILE_BASE_URL}")
        if manual_profile_url != ADMIN_ACTION_BASE_URL:
            logger.info(f"   → Branded domain detected. Ensure DNS routes to: {ADMIN_ACTION_BASE_URL}")
    else:
        # Auto-detect: use same URL as admin actions (no separate branded domain)
        PUBLIC_PROFILE_BASE_URL = ADMIN_ACTION_BASE_URL
        logger.info(f"🔧 Public Profile: Auto-detected (same as admin actions): {PUBLIC_PROFILE_BASE_URL}")
    
    # Validation: Warn about DNS requirements if using separate branded domain
    if PUBLIC_PROFILE_BASE_URL != ADMIN_ACTION_BASE_URL:
        logger.info("=" * 80)
        logger.info("📋 BRANDED DOMAIN DETECTED FOR PUBLIC PROFILES:")
        logger.info(f"   Public profiles use: {PUBLIC_PROFILE_BASE_URL}")
        logger.info(f"   Bot backend runs on: {ADMIN_ACTION_BASE_URL}")
        logger.info("")
        logger.info("   ✅ Ensure DNS/proxy is configured to route:")
        logger.info(f"      {PUBLIC_PROFILE_BASE_URL.replace('https://', '')} → {ADMIN_ACTION_BASE_URL.replace('https://', '')}")
        logger.info("")
        logger.info("   Common setup options:")
        logger.info("   1. DNS CNAME record")
        logger.info("   2. Reverse proxy (Cloudflare/Nginx/Caddy)")
        logger.info("=" * 80)

    # BlockBee currency mapping to their ticker format (DynoPay supported only)
    BLOCKBEE_CURRENCY_MAP = {
        "BTC": "btc",
        "ETH": "eth",
        "LTC": "ltc",
        "DOGE": "doge",
        "BCH": "bch",  # Bitcoin Cash
        "TRX": "trx",  # TRON
        "USDT-ERC20": "usdt_erc20",
        "USDT-TRC20": "usdt_trc20",
    }

    # Binance configuration removed - using Kraken instead

    # ===== DYNAMIC CONFIGURATION FOR PREVIOUSLY HARDCODED VALUES =====
    
    # Financial Thresholds and Limits
    NGN_FEE_CAP_NAIRA = Decimal(os.getenv("NGN_FEE_CAP_NAIRA", "250.0"))  # ₦250 default cap
    SECURE_TRADE_THRESHOLD_USD = Decimal(os.getenv("SECURE_TRADE_THRESHOLD_USD", "500.0"))  # $500 threshold for secure vs quick
    SMALL_TRADE_EXAMPLE_USD = Decimal(os.getenv("SMALL_TRADE_EXAMPLE_USD", "100.0"))  # $100 example for small trades
    MEDIUM_TRADE_EXAMPLE_USD = Decimal(os.getenv("MEDIUM_TRADE_EXAMPLE_USD", "500.0"))  # $500 example for medium trades
    LARGE_TRADE_EXAMPLE_USD = Decimal(os.getenv("LARGE_TRADE_EXAMPLE_USD", "500.0"))  # $500 max for large trades
    
    # Timeout and Expiry Settings  
    OTP_EXPIRY_MINUTES = int(os.getenv("OTP_EXPIRY_MINUTES", "10"))  # 10 minutes OTP expiry
    VERIFICATION_EXPIRY_MINUTES = int(os.getenv("VERIFICATION_EXPIRY_MINUTES", "15"))  # 15 minutes verification expiry
    USER_CACHE_TTL_MINUTES = int(os.getenv("USER_CACHE_TTL_MINUTES", "15"))  # 15 minutes user cache for better performance
    CONVERSATION_TIMEOUT_MINUTES = int(os.getenv("CONVERSATION_TIMEOUT_MINUTES", "30"))  # 30 minutes conversation timeout
    PAYMENT_TIMEOUT_MINUTES = int(os.getenv("PAYMENT_TIMEOUT_MINUTES", "60"))  # 60 minutes (1 hour) payment deadline
    SELLER_RESPONSE_TIMEOUT_MINUTES = int(os.getenv("SELLER_RESPONSE_TIMEOUT_MINUTES", "1440"))  # 24 hours for seller to accept after payment
    
    # UI and Display Limits
    EMAIL_PREVIEW_LENGTH = int(os.getenv("EMAIL_PREVIEW_LENGTH", "15"))  # 15 characters email preview
    DESCRIPTION_PREVIEW_LENGTH = int(os.getenv("DESCRIPTION_PREVIEW_LENGTH", "50"))  # 50 characters description preview
    COMMENT_PREVIEW_LENGTH = int(os.getenv("COMMENT_PREVIEW_LENGTH", "100"))  # 100 characters comment preview
    MAX_INPUT_LENGTH = int(os.getenv("MAX_INPUT_LENGTH", "100"))  # 100 characters max input
    MAX_EMAIL_LENGTH = int(os.getenv("MAX_EMAIL_LENGTH", "254"))  # RFC 5321 limit for emails
    
    # Performance and Monitoring Thresholds
    ADMIN_FAILURE_THRESHOLD_PER_HOUR = int(os.getenv("ADMIN_FAILURE_THRESHOLD_PER_HOUR", "10"))  # 10 admin failures per hour
    SUSPICIOUS_IP_THRESHOLD_PER_HOUR = int(os.getenv("SUSPICIOUS_IP_THRESHOLD_PER_HOUR", "5"))  # 5 suspicious IPs per hour
    CALLBACK_LOCKS_LIMIT = int(os.getenv("CALLBACK_LOCKS_LIMIT", "100"))  # 100 callback locks limit
    CALLBACK_LOCKS_CLEANUP_COUNT = int(os.getenv("CALLBACK_LOCKS_CLEANUP_COUNT", "50"))  # Keep last 50 locks
    
    # System Resource Limits
    LOG_FILE_SIZE_MB = int(os.getenv("LOG_FILE_SIZE_MB", "10"))  # 10MB log file size
    ERROR_LOG_SIZE_MB = int(os.getenv("ERROR_LOG_SIZE_MB", "5"))  # 5MB error log size
    
    # Security and Session Settings
    ADMIN_SESSION_TIMEOUT_HOURS = int(os.getenv("ADMIN_SESSION_TIMEOUT_HOURS", "8"))  # 8 hours admin session timeout (deprecated, use ADMIN_SESSION_TIMEOUT)
    ADMIN_SESSION_TIMEOUT = int(os.getenv("ADMIN_SESSION_TIMEOUT", "28800"))  # Admin session timeout in seconds (default: 28800 = 8 hours)
    LOCKOUT_DURATION_MINUTES = int(os.getenv("LOCKOUT_DURATION_MINUTES", "30"))  # 30 minutes lockout duration
    CIRCUIT_BREAKER_RECOVERY_TIMEOUT = float(os.getenv("CIRCUIT_BREAKER_RECOVERY_TIMEOUT", "60.0"))  # 60 seconds recovery timeout
    
    # Processing Time and Service Claims
    AVERAGE_PROCESSING_TIME_MINUTES = int(os.getenv("AVERAGE_PROCESSING_TIME_MINUTES", "5"))  # 5 minutes average processing
    MAX_DELIVERY_HOURS_CLAIM = int(os.getenv("MAX_DELIVERY_HOURS_CLAIM", "48"))  # 48 hours delivery claim
    
    # Example Phone Number Format (localized)
    EXAMPLE_PHONE_NUMBER = os.getenv("EXAMPLE_PHONE_NUMBER", "+1234567890")  # Default example phone
    
    # Marketing Statistics (should be real-time but configurable for now)
    PLATFORM_VOLUME_CLAIM = os.getenv("PLATFORM_VOLUME_CLAIM", "$500k+")  # Volume claim
    PLATFORM_USER_COUNT_CLAIM = os.getenv("PLATFORM_USER_COUNT_CLAIM", "10k+")  # User count claim  
    PLATFORM_UPTIME_CLAIM = os.getenv("PLATFORM_UPTIME_CLAIM", "99.9%")  # Uptime claim
    
    # Media and Branding URLs
    WELCOME_IMAGE_URL = os.getenv("WELCOME_IMAGE_URL", "https://images.unsplash.com/photo-1565514020179-026b92b84bb6?w=800&h=400&fit=crop&crop=center&auto=format&q=80")  # Welcome image
    
    # Support Response Time Claims
    SUPPORT_RESPONSE_TIME_HOURS = int(os.getenv("SUPPORT_RESPONSE_TIME_HOURS", "24"))  # 24 hours support response
    
    # Balance Monitoring Thresholds (Admin-Configurable via Secrets)
    BALANCE_ALERT_FINCRA_THRESHOLD_NGN = Decimal(os.getenv("BALANCE_ALERT_FINCRA_THRESHOLD_NGN", "5000"))  # ₦5,000 default
    BALANCE_ALERT_KRAKEN_THRESHOLD_USD = Decimal(os.getenv("BALANCE_ALERT_KRAKEN_THRESHOLD_USD", "20"))    # $20 default combined crypto

    @staticmethod
    def validate_production_config():
        """
        Validate critical production configuration and log warnings for common issues.
        This runs during startup to catch misconfigurations before they cause problems.
        """
        issues = []
        warnings = []
        
        logger.info("=" * 80)
        logger.info("🔍 PRODUCTION CONFIGURATION VALIDATION")
        logger.info("=" * 80)
        
        # 0. CRITICAL: Check database configuration (MUST BE FIRST)
        if Config.IS_PRODUCTION:
            if Config.DATABASE_SOURCE == "Neon PostgreSQL (Unified)":
                logger.info("✅ Database: Neon PostgreSQL (Unified) - configured and ready for production")
            elif Config.DATABASE_SOURCE == "Neon PostgreSQL (Production)":
                logger.info("✅ Database: Neon PostgreSQL (Production) - configured and ready")
            elif Config.DATABASE_SOURCE == "Railway PostgreSQL":
                logger.info("✅ Database: Railway PostgreSQL (production-optimized, persistent compute)")
            elif Config.DATABASE_SOURCE == "Neon PostgreSQL (FALLBACK)":
                warnings.append(
                    "⚠️ DATABASE FALLBACK ACTIVE: Using Neon instead of Railway!\n"
                    "   → Railway provides better performance (no cold starts)\n"
                    "   → Check that RAILWAY_DATABASE_URL is set in production secrets\n"
                    "   → Expected ~60% performance degradation with Neon serverless"
                )
            else:
                issues.append(
                    f"🚨 CRITICAL: Database not configured!\n"
                    f"   → DATABASE_SOURCE: {Config.DATABASE_SOURCE}\n"
                    f"   → Bot cannot start without database connection\n"
                    f"   → Set DATABASE_URL in Replit secrets (unified database for dev & production)"
                )
        
        # 1. Check webhook URL configuration
        if Config.IS_PRODUCTION:
            # Check if using wrong domain in webhook URLs (must point to actual server)
            wrong_domains_for_webhooks = [".repl.co"]  # Ephemeral domains only
            
            # WEBHOOK_URL: Must use actual server domain (not lockbay.io)
            if Config.BASE_WEBHOOK_URL:
                # Check for lockbay.io in webhook URL (webhooks need actual server)
                if "lockbay.io" in Config.BASE_WEBHOOK_URL:
                    issues.append(
                        f"🚨 CRITICAL: WEBHOOK_URL uses branded domain 'lockbay.io': {Config.BASE_WEBHOOK_URL}\n"
                        f"   → This is for customer-facing links, NOT webhooks!\n"
                        f"   → Crypto payment webhooks will NEVER arrive!\n"
                        f"   → Set WEBHOOK_URL=https://lockbayescrow-production.up.railway.app/webhook"
                    )
                
                # Check for ephemeral domains
                for wrong_domain in wrong_domains_for_webhooks:
                    if wrong_domain in Config.BASE_WEBHOOK_URL:
                        issues.append(
                            f"🚨 CRITICAL: WEBHOOK_URL uses ephemeral domain '{wrong_domain}': {Config.BASE_WEBHOOK_URL}\n"
                            f"   → Webhooks will break after restart!\n"
                            f"   → Set WEBHOOK_URL=https://lockbayescrow-production.up.railway.app/webhook"
                        )
            
            # ADMIN_ACTION_BASE_URL: Must use actual server domain (for email action buttons)
            if Config.ADMIN_ACTION_BASE_URL:
                # Check for lockbay.io in admin action URL (admin actions need actual server)
                if "lockbay.io" in Config.ADMIN_ACTION_BASE_URL:
                    issues.append(
                        f"🚨 CRITICAL: ADMIN_ACTION_BASE_URL uses branded domain 'lockbay.io': {Config.ADMIN_ACTION_BASE_URL}\n"
                        f"   → This is for customer-facing links, NOT admin webhooks!\n"
                        f"   → Email action buttons will break!\n"
                        f"   → Set ADMIN_ACTION_BASE_URL=https://lockbayescrow-production.up.railway.app"
                    )
                
                # Check for ephemeral domains
                for wrong_domain in wrong_domains_for_webhooks:
                    if wrong_domain in Config.ADMIN_ACTION_BASE_URL:
                        issues.append(
                            f"🚨 CRITICAL: ADMIN_ACTION_BASE_URL uses ephemeral domain '{wrong_domain}': {Config.ADMIN_ACTION_BASE_URL}\n"
                            f"   → Email action buttons will break after restart!\n"
                            f"   → Set ADMIN_ACTION_BASE_URL=https://lockbayescrow-production.up.railway.app"
                        )
            
            # PUBLIC_PROFILE_BASE_URL: Check configuration and DNS requirements
            if Config.PUBLIC_PROFILE_BASE_URL:
                # lockbay.io is ALLOWED and EXPECTED for public profiles (branded domain)
                if "lockbay.io" in Config.PUBLIC_PROFILE_BASE_URL:
                    logger.info("✅ PUBLIC_PROFILE_BASE_URL correctly uses branded domain: lockbay.io")
                    logger.info("   → Profile links will show as: https://lockbay.io/u/username")
                    
                    # Add DNS requirement warning (informational, not critical)
                    warnings.append(
                        "ℹ️  DNS REQUIREMENT for branded public profiles:\n"
                        f"   → PUBLIC_PROFILE_BASE_URL: {Config.PUBLIC_PROFILE_BASE_URL}\n"
                        f"   → Bot server: {Config.ADMIN_ACTION_BASE_URL}\n"
                        "   \n"
                        "   ✅ Ensure DNS routes lockbay.io → lockbayescrow-production.up.railway.app:\n"
                        "      Option 1: DNS CNAME record\n"
                        "      Option 2: Reverse proxy (Cloudflare/Nginx)\n"
                        "   \n"
                        "   ⚠️  If DNS is not configured, profile links will 404!"
                    )
                
                # Warn if using .repl.co for public profiles (works but not ideal)
                for wrong_domain in wrong_domains_for_webhooks:
                    if wrong_domain in Config.PUBLIC_PROFILE_BASE_URL:
                        warnings.append(
                            f"⚠️  WARNING: PUBLIC_PROFILE_BASE_URL uses ephemeral domain '{wrong_domain}'\n"
                            f"   → Profile: {Config.PUBLIC_PROFILE_BASE_URL}\n"
                            f"   → Links will break after restart!\n"
                            f"   → Recommended: Use branded domain (lockbay.io) or persistent domain"
                        )
            
            # Check if webhook URL is not set
            if not Config.BASE_WEBHOOK_URL:
                issues.append(
                    "🚨 CRITICAL: WEBHOOK_URL is not configured!\n"
                    "   → Crypto payments cannot be processed\n"
                    "   → Set WEBHOOK_URL=https://lockbayescrow-production.up.railway.app/webhook"
                )
        
        # 2. Check webhook security secrets
        if Config.IS_PRODUCTION:
            if not Config.DYNOPAY_WEBHOOK_SECRET:
                issues.append(
                    "🚨 CRITICAL: DYNOPAY_WEBHOOK_SECRET not configured!\n"
                    "   → DynoPay webhooks will be REJECTED for security\n"
                    "   → Crypto payments will fail even if webhook URL is correct\n"
                    "   → Get secret from DynoPay dashboard and set DYNOPAY_WEBHOOK_SECRET"
                )
            
            if not Config.BLOCKBEE_WEBHOOK_SECRET:
                warnings.append(
                    "⚠️  WARNING: BLOCKBEE_WEBHOOK_SECRET not configured\n"
                    "   → BlockBee webhooks will be rejected\n"
                    "   → Backup payment processor won't work\n"
                    "   → Get secret from BlockBee dashboard and set BLOCKBEE_WEBHOOK_SECRET"
                )
            
            if not Config.FINCRA_WEBHOOK_ENCRYPTION_KEY:
                warnings.append(
                    "⚠️  WARNING: FINCRA_WEBHOOK_ENCRYPTION_KEY not configured\n"
                    "   → Fincra webhooks cannot be validated\n"
                    "   → Bank payouts may fail\n"
                    "   → Get key from Fincra dashboard and set FINCRA_WEBHOOK_ENCRYPTION_KEY"
                )
        
        # 3. Check Redis configuration
        if Config.REDIS_URL == "redis://localhost:6379/0":
            # Only flag as critical if Redis is actually required (not using DB fallback)
            if Config.IS_PRODUCTION and Config.REDIS_FALLBACK_MODE != "DB_BACKED":
                issues.append(
                    "🚨 CRITICAL: REDIS_URL using localhost in production!\n"
                    "   → Redis connection will fail (localhost doesn't exist in production)\n"
                    "   → Wallet cashout sessions will break\n"
                    "   → Set REDIS_URL to your external Redis URL or use REDIS_FALLBACK_MODE=DB_BACKED"
                )
            elif Config.IS_PRODUCTION and Config.REDIS_FALLBACK_MODE == "DB_BACKED":
                logger.info("✅ Redis localhost detected but DB_BACKED fallback is configured - this is safe")
            elif not Config.IS_PRODUCTION:
                warnings.append(
                    "⚠️  INFO: REDIS_URL using localhost (default for development)\n"
                    "   → Make sure Redis is running locally or set external REDIS_URL"
                )
        
        # 4. Check database configuration
        if not Config.DATABASE_URL:
            issues.append(
                "🚨 CRITICAL: DATABASE_URL not configured!\n"
                "   → Bot cannot connect to database\n"
                "   → All operations will fail\n"
                "   → Replit should auto-configure this - check database setup"
            )
        
        # 5. Check admin email security
        if Config.IS_PRODUCTION and not Config.ADMIN_EMAIL_SECRET:
            issues.append(
                "🚨 CRITICAL: ADMIN_EMAIL_SECRET not configured!\n"
                "   → Admin email action buttons are vulnerable to forgery\n"
                "   → Generate random secret: openssl rand -hex 32\n"
                "   → Set ADMIN_EMAIL_SECRET to generated value"
            )
        
        # 6. Check Telegram bot token
        if not Config.TELEGRAM_BOT_TOKEN:
            issues.append(
                "🚨 CRITICAL: TELEGRAM_BOT_TOKEN not configured!\n"
                "   → Bot cannot connect to Telegram\n"
                "   → Get token from @BotFather and set TELEGRAM_BOT_TOKEN"
            )
        
        # 7. Check payment processor configuration
        if Config.IS_PRODUCTION:
            if not Config.DYNOPAY_API_KEY:
                warnings.append(
                    "⚠️  WARNING: DYNOPAY_API_KEY not configured\n"
                    "   → Primary payment processor disabled\n"
                    "   → Crypto payments may fail\n"
                    "   → Get API key from DynoPay dashboard"
                )
            
            if not Config.BLOCKBEE_API_KEY:
                warnings.append(
                    "⚠️  WARNING: BLOCKBEE_API_KEY not configured\n"
                    "   → Backup payment processor disabled\n"
                    "   → No failover if DynoPay fails"
                )
        
        # 8. Check email configuration
        if Config.IS_PRODUCTION and not Config.BREVO_API_KEY:
            issues.append(
                "🚨 CRITICAL: BREVO_API_KEY not configured in PRODUCTION!\n"
                "   → All email notifications are DISABLED\n"
                "   → OTP emails won't send (users can't verify accounts)\n"
                "   → Admin support alerts won't send (support tickets missed)\n"
                "   → Admin transaction alerts won't send\n"
                "   → 🔧 FIX: Add BREVO_API_KEY to production secrets and redeploy\n"
                "   → Get API key from Brevo dashboard: https://app.brevo.com"
            )
        
        # 9. Check Fincra configuration for NGN payouts
        if Config.IS_PRODUCTION:
            if not Config.FINCRA_SECRET_KEY or not Config.FINCRA_PUBLIC_KEY:
                warnings.append(
                    "⚠️  WARNING: Fincra API keys not configured\n"
                    "   → NGN bank payouts disabled\n"
                    "   → Bank account verification disabled\n"
                    "   → Get keys from Fincra dashboard"
                )
            elif Config.FINCRA_TEST_MODE:
                warnings.append(
                    "⚠️  WARNING: FINCRA_TEST_MODE=true in production!\n"
                    "   → Using Fincra sandbox (no real payouts)\n"
                    "   → Set FINCRA_TEST_MODE=false for real payments"
                )
        
        # 10. Check environment variable overrides vs fallbacks
        if Config.IS_PRODUCTION:
            if not os.getenv("WEBHOOK_URL"):
                warnings.append(
                    "ℹ️  INFO: WEBHOOK_URL not set explicitly\n"
                    f"   → Using fallback: {Config.BASE_WEBHOOK_URL}\n"
                    "   → Recommended: Set WEBHOOK_URL=https://lockbayescrow-production.up.railway.app/webhook explicitly"
                )
            
            if not os.getenv("ADMIN_ACTION_BASE_URL"):
                warnings.append(
                    "ℹ️  INFO: ADMIN_ACTION_BASE_URL not set explicitly\n"
                    f"   → Using fallback: {Config.ADMIN_ACTION_BASE_URL}\n"
                    "   → Recommended: Set ADMIN_ACTION_BASE_URL=https://lockbayescrow-production.up.railway.app explicitly"
                )
        
        # Print all issues and warnings
        if issues:
            logger.error("=" * 80)
            logger.error("🚨 CRITICAL CONFIGURATION ISSUES DETECTED")
            logger.error("=" * 80)
            for issue in issues:
                logger.error("")
                logger.error(issue)
                logger.error("")
            logger.error("=" * 80)
            logger.error("⚠️  PRODUCTION DEPLOYMENT WILL FAIL WITHOUT THESE FIXES!")
            logger.error("=" * 80)
            logger.error("")
        
        if warnings:
            logger.warning("=" * 80)
            logger.warning("⚠️  CONFIGURATION WARNINGS")
            logger.warning("=" * 80)
            for warning in warnings:
                logger.warning("")
                logger.warning(warning)
                logger.warning("")
            logger.warning("=" * 80)
            logger.warning("💡 Review PRODUCTION_DEPLOYMENT_CHECKLIST.md for full configuration guide")
            logger.warning("=" * 80)
            logger.warning("")
        
        # Success message if no issues
        if not issues and not warnings:
            logger.info("=" * 80)
            logger.info("✅ ALL PRODUCTION CONFIGURATION CHECKS PASSED")
            logger.info("=" * 80)
            logger.info("")
            logger.info("✓ Webhook URLs configured correctly")
            logger.info("✓ Webhook security secrets present")
            logger.info("✓ Database connection configured")
            logger.info("✓ Redis configuration valid")
            logger.info("✓ Admin security enabled")
            logger.info("✓ Payment processors configured")
            logger.info("✓ Email notifications enabled")
            logger.info("")
            logger.info("=" * 80)
            logger.info("🚀 READY FOR PRODUCTION DEPLOYMENT")
            logger.info("=" * 80)
        elif not issues:
            logger.info("=" * 80)
            logger.info("✅ CONFIGURATION VALIDATION COMPLETE (with warnings)")
            logger.info("=" * 80)
            logger.info("No critical issues - warnings can be addressed as needed")
            logger.info("=" * 80)
        
        return {"issues": issues, "warnings": warnings}

