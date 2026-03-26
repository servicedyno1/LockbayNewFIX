"""Enhanced Crypto Service with DynoPay Failover Support"""

import logging
from typing import Dict, Any
from services.crypto import CryptoServiceAtomic
from services.payment_processor_manager import payment_manager, PaymentProvider
from config import Config

logger = logging.getLogger(__name__)


class CryptoServiceEnhanced(CryptoServiceAtomic):
    """Enhanced crypto service with automatic failover between payment processors"""

    @classmethod
    async def generate_escrow_deposit_address_with_failover(
        cls,
        currency: str,
        amount_usd: float,
        escrow_id: str,
        user_id: int,
        callback_data: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """Generate escrow deposit address with automatic failover to DynoPay"""
        try:
            # Prepare callback URL
            base_webhook_url = Config.WEBHOOK_URL or Config.BLOCKBEE_CALLBACK_URL
            if not base_webhook_url:
                raise ValueError("No webhook URL configured for payment processing")
            
            # Determine callback URL based on provider that will be used
            callback_data = callback_data or {}
            callback_data.update({
                'escrow_id': escrow_id,
                'user_id': user_id,
                'amount_usd': amount_usd
            })
            
            # Get the primary provider to determine correct callback URL
            primary_provider = payment_manager.primary_provider
            
            # Set callback URL based on the primary provider configuration
            if primary_provider == PaymentProvider.DYNOPAY:
                callback_url = f"{base_webhook_url}/dynopay/escrow"
            else:
                callback_url = f"{base_webhook_url}/blockbee/callback/{escrow_id}"
            
            # Try to create payment address with failover
            result, provider_used = await payment_manager.create_payment_address(
                currency=currency,
                amount=amount_usd,
                callback_url=callback_url,
                reference_id=escrow_id,
                metadata=callback_data
            )
            
            # Add provider information to result
            result['payment_provider'] = provider_used.value
            result['failover_used'] = provider_used != PaymentProvider.BLOCKBEE
            
            if result.get('failover_used'):
                logger.warning(f"⚠️ Using backup payment provider ({provider_used.value}) for escrow {escrow_id}")
            else:
                logger.info(f"✅ Using primary payment provider ({provider_used.value}) for escrow {escrow_id}")
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to generate deposit address for escrow {escrow_id}: {e}")
            raise

    @classmethod
    async def get_payment_processor_status(cls) -> Dict[str, Any]:
        """Get comprehensive status of all payment processors"""
        try:
            status = payment_manager.get_provider_status()
            
            # Add additional context
            status['failover_enabled'] = payment_manager.is_failover_enabled()
            status['provider_priority'] = [p.value for p in payment_manager.get_provider_priority()]
            
            # Check if at least one provider is available
            available_providers = [name for name, info in status.items() 
                                 if isinstance(info, dict) and info.get('available', False)]
            
            status['service_available'] = len(available_providers) > 0
            status['available_providers'] = available_providers
            
            return status
            
        except Exception as e:
            logger.error(f"Error getting payment processor status: {e}")
            return {
                'service_available': False,
                'error': str(e)
            }

    @classmethod
    async def check_payment_status(cls, address: str, provider: str) -> Dict[str, Any]:
        """Check payment status on specific provider"""
        try:
            provider_enum = PaymentProvider(provider.lower())
            return await payment_manager.get_payment_status(address, provider_enum)
        except Exception as e:
            logger.error(f"Error checking payment status on {provider}: {e}")
            return {}

    @classmethod
    async def get_supported_currencies_all_providers(cls) -> Dict[str, Any]:
        """Get supported currencies from all available providers"""
        try:
            all_currencies = {}
            
            for provider in [PaymentProvider.BLOCKBEE, PaymentProvider.DYNOPAY]:
                try:
                    currencies = await payment_manager.get_supported_currencies(provider)
                    all_currencies[provider.value] = currencies
                except Exception as e:
                    logger.warning(f"Failed to get currencies from {provider.value}: {e}")
                    all_currencies[provider.value] = {"error": str(e)}
            
            # Determine which currencies are available across providers
            combined_support = {}
            for provider, currencies in all_currencies.items():
                if isinstance(currencies, dict) and "error" not in currencies:
                    for currency in currencies:
                        if currency not in combined_support:
                            combined_support[currency] = []
                        combined_support[currency].append(provider)
            
            return {
                'providers': all_currencies,
                'combined_support': combined_support,
                'total_currencies': len(combined_support)
            }
            
        except Exception as e:
            logger.error(f"Error getting supported currencies: {e}")
            return {"error": str(e)}


# Export enhanced service as the main crypto service
CryptoService = CryptoServiceEnhanced