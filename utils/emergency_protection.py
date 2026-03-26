"""
Emergency Profit Protection System
Last line of defense against configuration errors that could cause profit loss
"""

import logging
from decimal import Decimal
from typing import Dict
from datetime import datetime

logger = logging.getLogger(__name__)


class EmergencyProfitProtection:
    """Emergency system to prevent profit loss from configuration errors"""
    
    # Emergency safe defaults (guaranteed profitable)
    EMERGENCY_EXCHANGE_MARKUP = Decimal("5.0")  # 5% safe default
    EMERGENCY_ESCROW_FEE = Decimal("5.0")       # 5% safe default
    
    # Critical thresholds
    MIN_SAFE_MARKUP = Decimal("0.01")  # 0.01% absolute minimum
    MAX_SAFE_MARKUP = Decimal("50.0")  # 50% maximum
    
    def __init__(self):
        self.protection_enabled = True
        self.emergency_mode = False
        self.last_check = None
    
    def emergency_markup_fallback(self, 
                                 env_var: str, 
                                 provided_value: str,
                                 fallback: str = "5.0") -> Decimal:
        """
        Emergency fallback for markup configuration
        Last resort protection against profit loss
        """
        try:
            # Try to parse provided value
            markup = Decimal(provided_value)
            
            # Check if value is dangerous
            if markup <= 0:
                logger.critical(f"🚨 EMERGENCY: {env_var}={markup:.1f}% would eliminate profit! Using emergency fallback {fallback:.1f}%")
                self._trigger_emergency_mode(env_var, markup, "zero_or_negative")
                return Decimal(fallback)
            
            if markup < self.MIN_SAFE_MARKUP:
                logger.error(f"⚠️ PROTECTION: {env_var}={markup:.1f}% below safe minimum. Using emergency fallback {fallback:.1f}%")
                self._trigger_emergency_mode(env_var, markup, "below_minimum")
                return Decimal(fallback)
            
            if markup > self.MAX_SAFE_MARKUP:
                logger.warning(f"📊 PROTECTION: {env_var}={markup:.1f}% exceeds maximum. Using emergency fallback {fallback:.1f}%")
                return Decimal(fallback)
            
            # Value is safe
            logger.info(f"✅ SAFE: {env_var}={markup:.1f}% validated")
            return markup
            
        except Exception as e:
            logger.critical(f"🚨 EMERGENCY: Invalid {env_var} value '{provided_value}': {e}. Using emergency fallback {fallback:.1f}%")
            self._trigger_emergency_mode(env_var, provided_value, "parse_error")
            return Decimal(fallback)
    
    def _trigger_emergency_mode(self, env_var: str, dangerous_value, reason: str):
        """Trigger emergency protection mode"""
        self.emergency_mode = True
        
        emergency_log = {
            "timestamp": datetime.utcnow().isoformat(),
            "variable": env_var,
            "dangerous_value": str(dangerous_value),
            "reason": reason,
            "protection_action": "fallback_to_safe_default"
        }
        
        logger.critical(f"🚨 EMERGENCY MODE ACTIVATED: {emergency_log}")
        
        # In production, this could:
        # 1. Send urgent admin alerts
        # 2. Temporarily disable new transactions
        # 3. Log to external monitoring system
        # 4. Create incident ticket
    
    def check_runtime_profit_safety(self) -> Dict[str, any]:
        """Runtime check for profit safety"""
        try:
            from config import Config
            
            issues = []
            
            # Check current configuration values
            if Config.EXCHANGE_MARKUP_PERCENTAGE <= 0:
                issues.append(f"Exchange markup: {Config.EXCHANGE_MARKUP_PERCENTAGE}% - PROFIT LOSS!")
            
            if Config.ESCROW_FEE_PERCENTAGE <= 0:
                issues.append(f"Escrow fee: {Config.ESCROW_FEE_PERCENTAGE}% - PROFIT LOSS!")
            
            self.last_check = datetime.utcnow()
            
            return {
                "is_safe": len(issues) == 0,
                "critical_issues": issues,
                "emergency_mode": self.emergency_mode,
                "last_check": self.last_check.isoformat(),
                "protection_enabled": self.protection_enabled
            }
            
        except Exception as e:
            logger.error(f"Error in runtime profit safety check: {e}")
            return {
                "is_safe": False,
                "critical_issues": [f"Safety check failed: {e}"],
                "emergency_mode": True,
                "error": str(e)
            }
    
    def get_safe_markup_for_operation(self, operation_type: str) -> Decimal:
        """Get guaranteed safe markup for any operation"""
        safe_markups = {
            "exchange": self.EMERGENCY_EXCHANGE_MARKUP,
            "escrow": self.EMERGENCY_ESCROW_FEE,
            "default": Decimal("5.0")
        }
        
        return safe_markups.get(operation_type, safe_markups["default"])
    
    def disable_protection(self, admin_override: bool = False):
        """Disable emergency protection (admin only)"""
        if admin_override:
            self.protection_enabled = False
            logger.warning("⚠️ Emergency profit protection DISABLED by admin override")
        else:
            logger.error("❌ Cannot disable protection without admin override")
    
    def enable_protection(self):
        """Re-enable emergency protection"""
        self.protection_enabled = True
        self.emergency_mode = False
        logger.info("✅ Emergency profit protection ENABLED")


# Global emergency protection instance
emergency_protection = EmergencyProfitProtection()