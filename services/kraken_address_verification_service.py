"""
Kraken Address Verification Service
Smart routing for crypto withdrawals based on address availability
"""

import logging
from typing import Dict, List
from decimal import Decimal
from services.kraken_withdrawal_service import get_kraken_withdrawal_service
from utils.production_cache import get_cached, set_cached, delete_cached

logger = logging.getLogger(__name__)

class KrakenAddressVerificationService:
    """Service to verify if withdrawal addresses exist in Kraken and route accordingly"""
    
    def __init__(self):
        self.kraken_service = get_kraken_withdrawal_service()
        self.cache_ttl = 300  # 5 minutes cache for address lists
        
    async def verify_withdrawal_address(
        self, 
        crypto_currency: str, 
        withdrawal_address: str
    ) -> Dict[str, any]:
        """
        Verify if a withdrawal address is configured in Kraken
        
        Args:
            crypto_currency: Currency like 'BTC', 'ETH', 'USDT'
            withdrawal_address: The actual address user wants to withdraw to
            
        Returns:
            {
                'address_exists': bool,
                'address_key': str or None,
                'requires_configuration': bool,
                'route_to_admin': bool,
                'message': str
            }
        """
        try:
            logger.info(f"🔍 Verifying Kraken address availability for {crypto_currency}: {withdrawal_address[:10]}...")
            
            # Get cached or fresh address list
            addresses = await self._get_kraken_addresses_cached(crypto_currency)
            
            # Check if the exact address exists in Kraken
            matching_address = None
            for addr in addresses:
                if addr.get('address', '').lower() == withdrawal_address.lower():
                    matching_address = addr
                    break
            
            if matching_address:
                # Address exists in Kraken
                logger.info(f"✅ Address found in Kraken with key: {matching_address.get('key')}")
                
                is_verified = matching_address.get('verified', False)
                
                return {
                    'address_exists': True,
                    'address_key': matching_address.get('key'),
                    'is_verified': is_verified,
                    'requires_configuration': False,
                    'route_to_admin': not is_verified,  # Route to admin if unverified
                    'message': 'Address verified, ready for automatic processing' if is_verified 
                              else 'Address exists but needs verification in Kraken',
                    'routing_reason': 'automatic' if is_verified else 'address_needs_verification'
                }
            else:
                # Address doesn't exist in Kraken - needs admin setup
                logger.warning(f"❌ Address {withdrawal_address[:10]}... not found in Kraken configuration")
                
                return {
                    'address_exists': False,
                    'address_key': None,
                    'is_verified': False,
                    'requires_configuration': True,
                    'route_to_admin': True,
                    'message': 'Address needs to be added to Kraken dashboard before withdrawal',
                    'routing_reason': 'address_needs_configuration',
                    'admin_instructions': {
                        'step1': 'Login to Kraken dashboard',
                        'step2': 'Navigate to Funding > Withdraw',
                        'step3': f'Add new {crypto_currency} address: {withdrawal_address}',
                        'step4': 'Verify the address via email/SMS',
                        'step5': 'Return to admin panel and complete withdrawal'
                    }
                }
                
        except Exception as e:
            logger.error(f"❌ Error verifying address: {str(e)}")
            
            # On error, route to admin for manual handling
            return {
                'address_exists': False,
                'address_key': None,
                'is_verified': False,
                'requires_configuration': True,
                'route_to_admin': True,
                'message': 'Unable to verify address. Routing to admin for manual processing.',
                'routing_reason': 'verification_error',
                'error': str(e)
            }
    
    async def _get_kraken_addresses_cached(self, crypto_currency: str) -> List[Dict]:
        """Get Kraken addresses with caching to reduce API calls"""
        
        cache_key = f"kraken_addresses_{crypto_currency.lower()}"
        
        # Try cache first
        cached_addresses = get_cached(cache_key)
        if cached_addresses is not None:
            logger.debug(f"📦 Using cached addresses for {crypto_currency}")
            return cached_addresses
        
        try:
            # Fetch fresh addresses from Kraken
            logger.debug(f"🔄 Fetching fresh addresses for {crypto_currency} from Kraken")
            addresses = await self.kraken_service.get_withdrawal_addresses_for_currency(crypto_currency)
            
            # Cache the result
            set_cached(cache_key, addresses, ttl=self.cache_ttl)
            
            logger.info(f"✅ Cached {len(addresses)} {crypto_currency} addresses")
            return addresses
            
        except Exception as e:
            logger.error(f"❌ Failed to fetch Kraken addresses: {str(e)}")
            # Return empty list on error - will trigger admin routing
            return []
    
    def invalidate_address_cache(self, crypto_currency: str = None):
        """
        Invalidate address cache after admin adds new address
        Call this after admin completes address setup in Kraken
        """
        if crypto_currency:
            cache_key = f"kraken_addresses_{crypto_currency.lower()}"
            delete_cached(cache_key)
            logger.info(f"🗑️ Invalidated address cache for {crypto_currency}")
        else:
            # Clear all address caches
            cache_keys = [
                'kraken_addresses_btc',
                'kraken_addresses_eth', 
                'kraken_addresses_usdt',
                'kraken_addresses_ltc',
                'kraken_addresses_doge'
            ]
            for key in cache_keys:
                delete_cached(key)
            logger.info("🗑️ Invalidated all Kraken address caches")
    
    async def get_routing_decision(
        self, 
        crypto_currency: str, 
        withdrawal_address: str,
        amount: Decimal
    ) -> Dict[str, any]:
        """
        Make intelligent routing decision for crypto withdrawal
        
        Returns routing decision with full context for cashout processing
        """
        
        verification_result = await self.verify_withdrawal_address(crypto_currency, withdrawal_address)
        
        # Enhance with additional context
        routing_decision = {
            **verification_result,
            'crypto_currency': crypto_currency,
            'withdrawal_address': withdrawal_address,
            'amount_usd': amount,
            'recommended_action': self._get_recommended_action(verification_result),
            'admin_priority': self._get_admin_priority(amount),
            'estimated_processing_time': self._get_processing_time_estimate(verification_result)
        }
        
        logger.info(f"🎯 Routing decision for {crypto_currency} withdrawal: {routing_decision['recommended_action']}")
        return routing_decision
    
    def _get_recommended_action(self, verification_result: Dict) -> str:
        """Get recommended action based on verification result"""
        
        if verification_result['address_exists'] and verification_result.get('is_verified'):
            return 'process_automatically'
        elif verification_result['address_exists'] and not verification_result.get('is_verified'):
            return 'admin_verify_address'
        else:
            return 'admin_configure_address'
    
    def _get_admin_priority(self, amount: Decimal) -> str:
        """Determine admin priority based on amount"""
        
        if amount >= Decimal('1000'):
            return 'high'
        elif amount >= Decimal('100'):
            return 'medium'
        else:
            return 'low'
    
    def _get_processing_time_estimate(self, verification_result: Dict) -> str:
        """Estimate processing time for user communication"""
        
        if verification_result.get('address_exists') and verification_result.get('is_verified'):
            return '5-15 minutes'
        elif verification_result.get('address_exists'):
            return '15-30 minutes'  
        else:
            return '30-60 minutes'

# Global instance
kraken_address_verification_service = KrakenAddressVerificationService()