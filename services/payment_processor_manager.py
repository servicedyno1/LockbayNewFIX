"""Payment Processor Manager - Single provider configuration (no failover)"""

import logging
from typing import Dict, Any, Tuple
from enum import Enum
from config import Config
from services.blockbee_service import blockbee_service, BlockBeeAPIError
from services.dynopay_service import dynopay_service, DynoPayAPIError

logger = logging.getLogger(__name__)


class PaymentProvider(Enum):
    BLOCKBEE = "blockbee"
    DYNOPAY = "dynopay"


class PaymentProcessorManager:
    """Manages payment processor configuration with failover support"""

    def __init__(self):
        # Load configuration - with failover support
        primary_env = getattr(Config, 'PRIMARY_PAYMENT_PROVIDER', 'dynopay').lower()
        backup_env = getattr(Config, 'BACKUP_PAYMENT_PROVIDER', 'blockbee').lower()
        
        try:
            self.primary_provider = PaymentProvider(primary_env)
        except ValueError:
            logger.warning(f"Invalid primary provider '{primary_env}', using DynoPay")
            self.primary_provider = PaymentProvider.DYNOPAY
            
        try:
            self.backup_provider = PaymentProvider(backup_env)
        except ValueError:
            logger.warning(f"Invalid backup provider '{backup_env}', using BlockBee")
            self.backup_provider = PaymentProvider.BLOCKBEE
            
        # Enable failover functionality
        self.failover_enabled = getattr(Config, 'PAYMENT_FAILOVER_ENABLED', True)
        
        logger.info(f"Payment Manager initialized: Primary={self.primary_provider.value}, Backup={self.backup_provider.value}, Failover={'ENABLED' if self.failover_enabled else 'DISABLED'}")
        
    def _get_provider_service(self, provider: PaymentProvider):
        """Get the service instance for a provider"""
        if provider == PaymentProvider.BLOCKBEE:
            return blockbee_service
        elif provider == PaymentProvider.DYNOPAY:
            return dynopay_service
        else:
            raise ValueError(f"Unknown payment provider: {provider}")

    async def create_payment_address(
        self, 
        currency: str, 
        amount: float,
        callback_url: str,
        reference_id: str,
        metadata: Dict[str, Any] = None
    ) -> Tuple[Dict[str, Any], PaymentProvider]:
        """Create payment address using primary provider with automatic failover to backup"""
        
        # Try primary provider first
        try:
            primary_service = self._get_provider_service(self.primary_provider)
            
            if hasattr(primary_service, 'is_available') and not primary_service.is_available():
                raise Exception(f"{self.primary_provider.value} provider not available")
            
            if self.primary_provider == PaymentProvider.BLOCKBEE:
                # Use BlockBee's payment address creation method
                result = await primary_service.create_payment_address(
                    currency=currency,
                    escrow_id=reference_id,
                    amount_usd=amount
                )
                logger.info(f"✅ Payment address created via BlockBee for {currency}")
                return result, self.primary_provider
                
            elif self.primary_provider == PaymentProvider.DYNOPAY:
                # Use DynoPay's payment address creation method
                result = await primary_service.create_payment_address(
                    currency=currency,
                    amount=amount,
                    callback_url=callback_url,
                    reference_id=reference_id,
                    metadata=metadata
                )
                logger.info(f"✅ Payment address created via DynoPay for {currency}")
                return result, self.primary_provider
                
        except (BlockBeeAPIError, DynoPayAPIError, Exception) as e:
            logger.error(f"Primary payment provider ({self.primary_provider.value}) failed: {e}")
            
            # Try backup provider if failover is enabled
            if self.failover_enabled and self.backup_provider:
                logger.info(f"Attempting failover to backup provider: {self.backup_provider.value}")
                try:
                    backup_service = self._get_provider_service(self.backup_provider)
                    
                    if hasattr(backup_service, 'is_available') and not backup_service.is_available():
                        raise Exception(f"{self.backup_provider.value} backup provider not available")
                    
                    if self.backup_provider == PaymentProvider.BLOCKBEE:
                        # Use BlockBee's payment address creation method
                        result = await backup_service.create_payment_address(
                            currency=currency,
                            escrow_id=reference_id,
                            amount_usd=amount
                        )
                        logger.info(f"✅ Payment address created via BlockBee (backup) for {currency}")
                        return result, self.backup_provider
                        
                    elif self.backup_provider == PaymentProvider.DYNOPAY:
                        # Use DynoPay's payment address creation method
                        result = await backup_service.create_payment_address(
                            currency=currency,
                            amount=amount,
                            callback_url=callback_url,
                            reference_id=reference_id,
                            metadata=metadata
                        )
                        logger.info(f"✅ Payment address created via DynoPay (backup) for {currency}")
                        return result, self.backup_provider
                        
                except (BlockBeeAPIError, DynoPayAPIError, Exception) as backup_e:
                    logger.error(f"Backup payment provider ({self.backup_provider.value}) also failed: {backup_e}")
            
            # Both providers failed or failover disabled
            raise Exception(f"Payment provider {self.primary_provider.value} failed and {'no backup available' if not self.failover_enabled else 'backup also failed'}")

    async def get_supported_currencies(self, provider: PaymentProvider = None) -> Dict[str, Any]:
        """Get supported currencies from configured provider"""
        target_provider = provider or self.primary_provider
        
        try:
            service = self._get_provider_service(target_provider)
            return await service.get_supported_currencies()
        except Exception as e:
            logger.error(f"Failed to get supported currencies from {target_provider.value}: {e}")
            raise

    async def check_payment_status(self, address: str, currency: str) -> Dict[str, Any]:
        """Check payment status using configured primary provider"""
        try:
            service = self._get_provider_service(self.primary_provider)
            
            if self.primary_provider == PaymentProvider.BLOCKBEE:
                return await service.check_address_logs(currency=currency, address=address)
            elif self.primary_provider == PaymentProvider.DYNOPAY:
                return await service.check_payment_status(address=address, currency=currency)
                
        except Exception as e:
            logger.error(f"Failed to check payment status via {self.primary_provider.value}: {e}")
            return {"confirmed": False, "error": str(e)}

    async def get_currency_info(self, currency: str) -> Dict[str, Any]:
        """Get currency info from configured primary provider"""
        try:
            service = self._get_provider_service(self.primary_provider)
            return await service.get_currency_info(currency)
        except Exception as e:
            logger.error(f"Failed to get currency info for {currency} from {self.primary_provider.value}: {e}")
            return {"minimum_transaction": 10.0}  # Safe fallback

    async def get_payment_status(self, address: str, provider: PaymentProvider) -> Dict[str, Any]:
        """Get payment status from specific provider"""
        try:
            service = self._get_provider_service(provider)
            return await service.get_payment_status(address)
        except Exception as e:
            logger.error(f"Failed to get payment status from {provider.value}: {e}")
            return {}

    def get_provider_priority(self) -> list:
        """Get configured provider (single provider only)"""
        return [self.primary_provider]

    def is_failover_enabled(self) -> bool:
        """Check if failover is enabled"""
        return self.failover_enabled

    def get_provider_status(self) -> Dict[str, Dict[str, Any]]:
        """Get status of configured payment provider"""
        status = {}
        
        try:
            service = self._get_provider_service(self.primary_provider)
            status[self.primary_provider.value] = {
                'available': hasattr(service, 'is_available') and service.is_available(),
                'configured': bool(service.api_key if hasattr(service, 'api_key') else False),
                'type': 'primary'
            }
        except Exception as e:
            status[self.primary_provider.value] = {
                'available': False,
                'configured': False,
                'error': str(e),
                'type': 'primary'
            }
        
        return status


# Global instance
payment_manager = PaymentProcessorManager()