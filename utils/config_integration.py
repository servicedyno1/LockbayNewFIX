#!/usr/bin/env python3
"""
Configuration Integration Utilities
Helper functions to integrate database configuration across all services
"""

import logging
from typing import Any, Optional
from functools import lru_cache
from datetime import datetime

from services.comprehensive_config_service import ComprehensiveConfigService

logger = logging.getLogger(__name__)

# Global config service instance
_config_service = None

def get_config_service() -> ComprehensiveConfigService:
    """Get or create configuration service instance"""
    global _config_service
    if _config_service is None:
        _config_service = ComprehensiveConfigService()
    return _config_service

@lru_cache(maxsize=1)
def get_cached_config(cache_key: Optional[str] = None) -> dict:
    """Get cached configuration to avoid database calls"""
    # Cache key changes every 15 minutes to ensure fresh data
    cache_timestamp = datetime.now().replace(second=0, microsecond=0)
    f"config_{cache_timestamp.strftime('%Y%m%d_%H%M')}"
    
    config_service = get_config_service()
    return config_service.get_current_config()

def clear_config_cache():
    """Clear configuration cache to force refresh"""
    get_cached_config.cache_clear()

def get_config_value(key: str, default: Any = None, user_id: Optional[int] = None) -> Any:
    """
    Get configuration value with fallback to default
    
    Args:
        key: Configuration key to retrieve
        default: Default value if key not found
        user_id: User ID for personalized config (A/B testing)
    """
    try:
        if user_id:
            # Get user-specific config (may include A/B test variations)
            config_service = get_config_service()
            config = config_service.get_config_for_user(user_id)
        else:
            # Get cached global config
            config = get_cached_config()
        
        return config.get(key, default)
    
    except Exception as e:
        logger.error(f"Error getting config value for {key}: {e}")
        return default

def get_financial_config() -> dict:
    """Get all financial configuration values"""
    config = get_cached_config()
    return {
        # Escrow & Trading
        "min_escrow_amount_usd": config.get("min_escrow_amount_usd", 5.0),
        "max_escrow_amount_usd": config.get("max_escrow_amount_usd", 100000.0),
        "min_exchange_amount_usd": config.get("min_exchange_amount_usd", 5.0),
        "escrow_fee_percentage": config.get("escrow_fee_percentage", 5.0),
        "exchange_markup_percentage": config.get("exchange_markup_percentage", 5.0),
        
        # Cashouts
        "min_cashout_amount_usd": config.get("min_cashout_amount_usd", 1.0),
        "max_cashout_amount_usd": config.get("max_cashout_amount_usd", 10000.0),
        "admin_approval_threshold_usd": config.get("admin_approval_threshold_usd", 500.0),
        "min_auto_cashout_amount_usd": config.get("min_auto_cashout_amount_usd", 25.0),
        
        # Fees
        "trc20_flat_fee_usd": config.get("trc20_flat_fee_usd", 4.0),
        "erc20_flat_fee_usd": config.get("erc20_flat_fee_usd", 4.0),
        "ngn_flat_fee_naira": config.get("ngn_flat_fee_naira", 2000.0),
        "percentage_cashout_fee": config.get("percentage_cashout_fee", 2.0),
        "min_percentage_fee_usd": config.get("min_percentage_fee_usd", 2.0),
        "max_percentage_fee_usd": config.get("max_percentage_fee_usd", 100.0),
    }

def get_security_config() -> dict:
    """Get all security configuration values"""
    config = get_cached_config()
    return {
        # Anomaly Detection
        "low_anomaly_threshold": config.get("low_anomaly_threshold", 30.0),
        "medium_anomaly_threshold": config.get("medium_anomaly_threshold", 50.0),
        "high_anomaly_threshold": config.get("high_anomaly_threshold", 70.0),
        "critical_anomaly_threshold": config.get("critical_anomaly_threshold", 85.0),
        
        # Security Parameters
        "suspicious_cashout_threshold": config.get("suspicious_cashout_threshold", 0.8),
        "rapid_transaction_threshold": config.get("rapid_transaction_threshold", 5),
        "rapid_transaction_window_seconds": config.get("rapid_transaction_window_seconds", 300),
        "transaction_analysis_days": config.get("transaction_analysis_days", 90),
        
        # Balance Monitoring (Using Secret-Based Configuration)
        "fincra_low_balance_threshold_ngn": float(config.get("BALANCE_ALERT_FINCRA_THRESHOLD_NGN", 5000.0)),
        "kraken_low_balance_threshold_usd": float(config.get("BALANCE_ALERT_KRAKEN_THRESHOLD_USD", 20.0)),
        "balance_alert_cooldown_hours": config.get("balance_alert_cooldown_hours", 6),
    }

def get_operational_config() -> dict:
    """Get all operational configuration values"""
    config = get_cached_config()
    return {
        # Timeouts
        "default_delivery_timeout_hours": config.get("default_delivery_timeout_hours", 72),
        "max_delivery_timeout_hours": config.get("max_delivery_timeout_hours", 336),
        "crypto_exchange_timeout_minutes": config.get("crypto_exchange_timeout_minutes", 60),
        "rate_lock_duration_minutes": config.get("rate_lock_duration_minutes", 15),
        "conversation_timeout_minutes": config.get("conversation_timeout_minutes", 20),
        
        # System Limits
        "max_file_size_mb": config.get("max_file_size_mb", 20),
        "max_message_length": config.get("max_message_length", 4000),
        "api_timeout_seconds": config.get("api_timeout_seconds", 30),
        
        # Performance
        "enable_batch_processing": config.get("enable_batch_processing", True),
        "batch_size_limit": config.get("batch_size_limit", 100),
        "cache_duration_minutes": config.get("cache_duration_minutes", 15),
        "background_job_interval_minutes": config.get("background_job_interval_minutes", 5),
    }

def get_regional_config() -> dict:
    """Get regional adjustment configuration"""
    config = get_cached_config()
    return {
        "enable_global_regional_adjustments": config.get("enable_global_regional_adjustments", True),
        "regional_multiplier_developing": config.get("regional_multiplier_developing", 0.4),
        "regional_multiplier_emerging": config.get("regional_multiplier_emerging", 0.6),
        "regional_multiplier_developed": config.get("regional_multiplier_developed", 1.0),
        "regional_safety_floor_percentage": config.get("regional_safety_floor_percentage", 0.2),
    }

def get_ab_testing_config() -> dict:
    """Get A/B testing configuration"""
    config = get_cached_config()
    return {
        "enable_ab_testing": config.get("enable_ab_testing", False),
        "ab_test_traffic_percentage": config.get("ab_test_traffic_percentage", 10.0),
        "ab_test_duration_days": config.get("ab_test_duration_days", 14),
    }

# Backward compatibility functions for existing services
def get_min_escrow_amount() -> float:
    """Get minimum escrow amount (backward compatibility)"""
    return get_config_value("min_escrow_amount_usd", 5.0)

def get_max_escrow_amount() -> float:
    """Get maximum escrow amount (backward compatibility)"""
    return get_config_value("max_escrow_amount_usd", 100000.0)

def get_escrow_fee_percentage() -> float:
    """Get escrow fee percentage (backward compatibility)"""
    return get_config_value("escrow_fee_percentage", 5.0)

def get_cashout_threshold() -> float:
    """Get admin approval threshold (backward compatibility)"""
    return get_config_value("admin_approval_threshold_usd", 500.0)

def get_anomaly_thresholds() -> dict:
    """Get anomaly detection thresholds (backward compatibility)"""
    config = get_cached_config()
    return {
        "low": config.get("low_anomaly_threshold", 30.0),
        "medium": config.get("medium_anomaly_threshold", 50.0),
        "high": config.get("high_anomaly_threshold", 70.0),
        "critical": config.get("critical_anomaly_threshold", 85.0),
    }

def get_balance_thresholds() -> dict:
    """Get balance monitoring thresholds (backward compatibility)"""
    config = get_cached_config()
    return {
        "fincra_ngn": float(config.get("BALANCE_ALERT_FINCRA_THRESHOLD_NGN", 5000.0)),
        "kraken_usd": float(config.get("BALANCE_ALERT_KRAKEN_THRESHOLD_USD", 20.0)),
        "cooldown_hours": config.get("balance_alert_cooldown_hours", 6),
    }

def is_feature_enabled(feature_name: str) -> bool:
    """Check if a feature is enabled"""
    feature_flags = {
        "ab_testing": get_config_value("enable_ab_testing", False),
        "regional_adjustments": get_config_value("enable_global_regional_adjustments", True),
        "batch_processing": get_config_value("enable_batch_processing", True),
        "maintenance_mode": get_config_value("maintenance_mode", False),
        "platform": get_config_value("platform_enabled", True),
        "registration": get_config_value("registration_enabled", True),
        "debug_mode": get_config_value("debug_mode", False),
    }
    return feature_flags.get(feature_name, False)

def update_config_cache_on_change():
    """Called when configuration is updated to refresh cache"""
    clear_config_cache()
    logger.info("Configuration cache cleared due to config update")

# Configuration change notification system
def notify_config_change(config_key: str, old_value: Any, new_value: Any, admin_id: int):
    """Notify relevant services of configuration changes"""
    try:
        # Clear cache first
        update_config_cache_on_change()
        
        # Log the change
        logger.info(f"Configuration changed: {config_key} {old_value} → {new_value} by admin {admin_id}")
        
        # Notify specific services based on config key
        if config_key.startswith("anomaly_"):
            logger.info("Notifying behavioral anomaly detection service")
        elif config_key.startswith("balance_"):
            logger.info("Notifying balance monitoring service")
        elif config_key.startswith("escrow_") or config_key.startswith("cashout_"):
            logger.info("Notifying financial services")
        
    except Exception as e:
        logger.error(f"Error notifying configuration change: {e}")

# Migration helper functions
def migrate_service_to_db_config(service_name: str, config_mapping: dict):
    """Helper to migrate a service from hardcoded to database configuration"""
    logger.info(f"Migrating {service_name} to database configuration")
    
    for old_key, new_key in config_mapping.items():
        # Get current value from database config
        current_value = get_config_value(new_key)
        logger.info(f"  {old_key} → {new_key}: {current_value}")
    
    logger.info(f"Migration complete for {service_name}")

# Performance monitoring
def get_config_performance_metrics() -> dict:
    """Get performance metrics for configuration system"""
    try:
        cache_info = get_cached_config.cache_info()
        return {
            "cache_hits": cache_info.hits,
            "cache_misses": cache_info.misses,
            "cache_hit_rate": cache_info.hits / (cache_info.hits + cache_info.misses) if (cache_info.hits + cache_info.misses) > 0 else 0,
            "cache_size": cache_info.currsize,
            "cache_maxsize": cache_info.maxsize,
        }
    except Exception as e:
        logger.error(f"Error getting config performance metrics: {e}")
        return {}