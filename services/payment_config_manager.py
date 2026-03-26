"""Dynamic Payment Provider Configuration Manager"""

import logging
import os
from typing import Dict, Any
from services.payment_processor_manager import PaymentProvider

logger = logging.getLogger(__name__)


class PaymentConfigManager:
    """Manages dynamic payment provider configuration"""
    
    def __init__(self):
        self._config_cache = {}
        self._load_config()
    
    def _load_config(self):
        """Load current payment configuration - DynoPay as default primary"""
        self._config_cache = {
            'primary_provider': os.getenv('PRIMARY_PAYMENT_PROVIDER', 'dynopay').lower(),
            'backup_provider': os.getenv('BACKUP_PAYMENT_PROVIDER', 'blockbee').lower(),
            'failover_enabled': os.getenv('PAYMENT_FAILOVER_ENABLED', 'true').lower() == 'true',
            'blockbee_enabled': os.getenv('BLOCKBEE_ENABLED', 'true').lower() == 'true',
            'dynopay_enabled': os.getenv('DYNOPAY_ENABLED', 'true').lower() == 'true',
            'auto_failover': os.getenv('AUTO_FAILOVER', 'true').lower() == 'true'
        }
    
    def get_current_config(self) -> Dict[str, Any]:
        """Get current payment provider configuration"""
        return self._config_cache.copy()
    
    def set_primary_provider(self, provider: str, admin_user_id: int) -> Dict[str, Any]:
        """Set primary payment provider"""
        try:
            provider = provider.lower()
            if provider not in ['blockbee', 'dynopay']:
                return {"success": False, "error": "Invalid provider. Must be 'blockbee' or 'dynopay'"}
            
            # Validate provider is enabled
            if not self._is_provider_enabled(provider):
                return {"success": False, "error": f"Provider {provider} is not enabled"}
            
            old_primary = self._config_cache.get('primary_provider')
            self._config_cache['primary_provider'] = provider
            
            # Auto-adjust backup provider
            if provider == 'dynopay':
                self._config_cache['backup_provider'] = 'blockbee'
            else:
                self._config_cache['backup_provider'] = 'dynopay'
            
            # Apply the configuration
            self._apply_config()
            
            logger.info(f"Admin {admin_user_id} changed primary provider from {old_primary} to {provider}")
            
            return {
                "success": True,
                "primary_provider": provider,
                "backup_provider": self._config_cache['backup_provider'],
                "message": f"Primary provider switched to {provider.upper()}"
            }
            
        except Exception as e:
            logger.error(f"Error setting primary provider: {e}")
            return {"success": False, "error": str(e)}
    
    def toggle_failover(self, enabled: bool, admin_user_id: int) -> Dict[str, Any]:
        """Enable or disable automatic failover"""
        try:
            self._config_cache.get('failover_enabled')
            self._config_cache['failover_enabled'] = enabled
            self._apply_config()
            
            action = "enabled" if enabled else "disabled"
            logger.info(f"Admin {admin_user_id} {action} payment failover")
            
            return {
                "success": True,
                "failover_enabled": enabled,
                "message": f"Automatic failover {action}"
            }
            
        except Exception as e:
            logger.error(f"Error toggling failover: {e}")
            return {"success": False, "error": str(e)}
    
    def toggle_provider(self, provider: str, enabled: bool, admin_user_id: int) -> Dict[str, Any]:
        """Enable or disable a specific provider"""
        try:
            provider = provider.lower()
            if provider not in ['blockbee', 'dynopay']:
                return {"success": False, "error": "Invalid provider"}
            
            config_key = f'{provider}_enabled'
            old_state = self._config_cache.get(config_key)
            self._config_cache[config_key] = enabled
            
            # Validate we don't disable all providers
            if not self._has_enabled_provider():
                self._config_cache[config_key] = old_state  # Revert
                return {"success": False, "error": "Cannot disable all payment providers"}
            
            # If disabling primary provider, switch to backup
            if not enabled and self._config_cache['primary_provider'] == provider:
                backup = 'blockbee' if provider == 'dynopay' else 'dynopay'
                if self._is_provider_enabled(backup):
                    self._config_cache['primary_provider'] = backup
                    self._config_cache['backup_provider'] = provider
            
            self._apply_config()
            
            action = "enabled" if enabled else "disabled"
            logger.info(f"Admin {admin_user_id} {action} {provider} provider")
            
            return {
                "success": True,
                "provider": provider,
                "enabled": enabled,
                "message": f"{provider.upper()} provider {action}",
                "primary_provider": self._config_cache['primary_provider']
            }
            
        except Exception as e:
            logger.error(f"Error toggling provider: {e}")
            return {"success": False, "error": str(e)}
    
    def _is_provider_enabled(self, provider: str) -> bool:
        """Check if a provider is enabled"""
        return self._config_cache.get(f'{provider}_enabled', False)
    
    def _has_enabled_provider(self) -> bool:
        """Check if at least one provider is enabled"""
        return (self._config_cache.get('blockbee_enabled', False) or 
                self._config_cache.get('dynopay_enabled', False))
    
    def _apply_config(self):
        """Apply configuration to payment manager"""
        try:
            from services.payment_processor_manager import payment_manager
            
            # Update payment manager configuration
            primary = PaymentProvider(self._config_cache['primary_provider'])
            backup = PaymentProvider(self._config_cache['backup_provider'])
            
            payment_manager.primary_provider = primary
            payment_manager.backup_provider = backup
            payment_manager.failover_enabled = self._config_cache['failover_enabled']
            
            logger.info(f"Applied payment config: Primary={primary.value}, Backup={backup.value}, Failover={self._config_cache['failover_enabled']}")
            
        except Exception as e:
            logger.error(f"Error applying payment configuration: {e}")
    
    def get_provider_status_summary(self) -> Dict[str, Any]:
        """Get comprehensive provider status"""
        try:
            from services.payment_processor_manager import payment_manager
            from services.blockbee_service import blockbee_service
            from services.dynopay_service import dynopay_service
            
            status = {
                'configuration': self.get_current_config(),
                'providers': {
                    'blockbee': {
                        'configured': bool(blockbee_service.api_key),
                        'enabled': self._config_cache.get('blockbee_enabled', False),
                        'is_primary': self._config_cache.get('primary_provider') == 'blockbee'
                    },
                    'dynopay': {
                        'configured': dynopay_service.is_available(),
                        'enabled': self._config_cache.get('dynopay_enabled', False),
                        'is_primary': self._config_cache.get('primary_provider') == 'dynopay'
                    }
                },
                'operational_status': payment_manager.get_provider_status()
            }
            
            return status
            
        except Exception as e:
            logger.error(f"Error getting provider status: {e}")
            return {"error": str(e)}
    
    def validate_admin_access(self, user_id: int) -> bool:
        """Validate admin access for payment configuration"""
        try:
            from config import Config
            return user_id in Config.ADMIN_IDS
        except Exception:
            return False


# Global instance
payment_config_manager = PaymentConfigManager()