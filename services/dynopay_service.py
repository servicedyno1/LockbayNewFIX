"""DynoPay Cryptocurrency Payment API Service - Backup to BlockBee"""

import aiohttp
import logging
from typing import Dict, Any
from decimal import Decimal
from config import Config
from utils.data_sanitizer import mask_api_key_safe
from services.external_api_optimizer import optimized_http_session

logger = logging.getLogger(__name__)


class DynoPayAPIError(Exception):
    """Custom exception for DynoPay API errors"""
    pass


class DynoPayService:
    """Service for handling DynoPay cryptocurrency payments as BlockBee backup"""

    def __init__(self):
        self.api_key = getattr(Config, 'DYNOPAY_API_KEY', None)
        self.wallet_token = getattr(Config, 'DYNOPAY_WALLET_TOKEN', None)
        self.base_url = getattr(Config, 'DYNOPAY_BASE_URL', 'https://user-api.dynopay.com/api')
        self.webhook_url = getattr(Config, 'DYNOPAY_WEBHOOK_URL', None)
        
        if not self.api_key or not self.wallet_token:
            logger.warning("DynoPay API credentials not configured - service will not function")
        else:
            logger.info(f"DynoPay API initialized with key: {mask_api_key_safe(self.api_key)}")

    def _get_headers(self) -> Dict[str, str]:
        """Get headers for DynoPay API requests"""
        return {
            'accept': 'application/json',
            'content-type': 'application/json',
            'x-api-key': self.api_key,
            'Authorization': f'Bearer {self.wallet_token}'
        }

    def _map_currency_to_dynopay(self, currency: str) -> str:
        """Map internal currency format to DynoPay ticker format"""
        currency_map = {
            'BTC': 'BTC',
            'ETH': 'ETH', 
            'LTC': 'LTC',
            'DOGE': 'DOGE',
            'TRX': 'TRX',
            'USDT': 'USDT',
            'USDT-TRC20': 'USDT-TRC20',
            # 'XMR': 'XMR'  # Removed: FastForex API doesn't support XMR
        }
        return currency_map.get(currency.upper(), currency.upper())

    async def get_supported_currencies(self) -> Dict[str, Any]:
        """Get list of supported cryptocurrencies from DynoPay"""
        try:
            # PERFORMANCE OPTIMIZATION: Use optimized HTTP session with connection pooling
            async with optimized_http_session() as session:
                async with session.get(
                    f"{self.base_url}/getSupportedCurrency",
                    headers=self._get_headers(),
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        logger.info("Successfully retrieved supported currencies from DynoPay")
                        return data.get('data', {})
                    else:
                        error_text = await response.text()
                        logger.error(f"DynoPay API error: HTTP {response.status}: {error_text}")
                        raise DynoPayAPIError("DynoPay service temporarily unavailable")
        except aiohttp.ClientError as e:
            logger.error(f"Network error connecting to DynoPay: {e}")
            raise DynoPayAPIError(f"Network error: {e}")
        except Exception as e:
            logger.error(f"Unexpected error in get_supported_currencies: {e}")
            raise DynoPayAPIError(f"Unexpected error: {e}")

    # Crypto fee padding: $2 flat added to non-USDT payments to cover network fees
    CRYPTO_FEE_PADDING_USD = Decimal("2")
    CRYPTO_FEE_PADDING_EXEMPT = {"USDT", "USDT_TRC20", "USDT_ERC20", "USDT_BEP20"}

    async def create_payment_address(
        self, 
        currency: str, 
        amount: Decimal, 
        callback_url: str,
        reference_id: str,
        metadata: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """Create crypto payment address for escrow deposit"""
        try:
            dynopay_currency = self._map_currency_to_dynopay(currency)
            
            # Apply $2 crypto fee padding for non-USDT currencies (exempt wallet deposits)
            original_amount = Decimal(str(amount))
            is_wallet_deposit = reference_id and reference_id.startswith('WALLET-')
            if is_wallet_deposit:
                padded_amount = original_amount
                logger.info(f"💰 CRYPTO_FEE_PADDING: Wallet deposit exempt - no padding applied (${original_amount}, ref: {reference_id})")
            elif currency.upper() not in self.CRYPTO_FEE_PADDING_EXEMPT:
                padded_amount = original_amount + self.CRYPTO_FEE_PADDING_USD
                logger.info(f"💰 CRYPTO_FEE_PADDING: ${original_amount} + ${self.CRYPTO_FEE_PADDING_USD} padding = ${padded_amount} (currency: {currency})")
            else:
                padded_amount = original_amount
                logger.info(f"💰 CRYPTO_FEE_PADDING: USDT exempt - no padding applied (${original_amount})")
            
            payment_amount = float(padded_amount)
            
            # VALIDATION LOG: Confirm UTID is being used as reference_id
            logger.info(f"🔍 DYNOPAY_UTID_VALIDATION: Creating payment with reference_id='{reference_id}' (should be UTID format)")
            if metadata:
                utid_from_metadata = metadata.get('utid')
                escrow_id_from_metadata = metadata.get('escrow_id')
                logger.info(f"🔍 DYNOPAY_METADATA_VALIDATION: utid='{utid_from_metadata}', escrow_id='{escrow_id_from_metadata}'")
                # Only warn about mismatch if it's not a wallet deposit (WALLET-* format is expected)
                if reference_id != utid_from_metadata and not (reference_id and reference_id.startswith('WALLET-')):
                    logger.warning(f"⚠️ DYNOPAY_ID_MISMATCH: reference_id '{reference_id}' != metadata utid '{utid_from_metadata}'")
            
            # Prepare metadata for escrow tracking
            meta_data = {
                "product_name": "escrow_deposit",
                "refId": reference_id,
                "user_id": metadata.get('user_id') if metadata else None,
                "escrow_id": metadata.get('escrow_id') if metadata else None
            }
            
            payload = {
                "amount": payment_amount,  # Padded amount for crypto fee protection
                "currency": dynopay_currency,
                "redirect_uri": callback_url,
                "webhook_url": self.webhook_url,
                "callback_url": callback_url,
                "customer_reference": reference_id,
                "meta_data": meta_data
            }
            
            # PERFORMANCE OPTIMIZATION: Use optimized HTTP session with connection pooling
            async with optimized_http_session() as session:
                async with session.post(
                    f"{self.base_url}/user/cryptoPayment",
                    headers=self._get_headers(),
                    json=payload
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        result = data.get('data', {})
                        
                        logger.info(f"DynoPay address created for {currency}: {reference_id}")
                        
                        # Return in BlockBee-compatible format with address field for escrow handler compatibility
                        return {
                            'address_in': result.get('address'),
                            'address': result.get('address'),  # CRITICAL FIX: Add direct 'address' field for escrow handler
                            'address_out': None,  # DynoPay handles this internally
                            'callback_url': callback_url,
                            'qr_code': result.get('qr_code'),
                            'qr_code_svg': None,
                            'minimum_transaction': 0.00001,  # Default minimum
                            'fee_percent': 1.0,  # DynoPay's fee
                            'reference_id': reference_id
                        }
                    else:
                        error_text = await response.text()
                        logger.error(f"DynoPay address creation failed: HTTP {response.status}: {error_text}")
                        raise DynoPayAPIError(f"Failed to create payment address: {error_text}")
                        
        except aiohttp.ClientError as e:
            logger.error(f"Network error creating DynoPay address: {e}")
            raise DynoPayAPIError(f"Network error: {e}")
        except Exception as e:
            logger.error(f"Unexpected error creating DynoPay address: {e}")
            raise DynoPayAPIError(f"Unexpected error: {e}")

    async def get_payment_status(self, address: str) -> Dict[str, Any]:
        """Check payment status for a given address"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.base_url}/user/getCryptoTransaction/{address}",
                    headers=self._get_headers(),
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data.get('data', {})
                    else:
                        logger.warning(f"DynoPay payment status check failed for {address}")
                        return {}
        except Exception as e:
            logger.error(f"Error checking DynoPay payment status: {e}")
            return {}

    async def get_transaction_details(self, transaction_id: str) -> Dict[str, Any]:
        """Get detailed transaction information"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.base_url}/user/getSingleTransaction/{transaction_id}",
                    headers=self._get_headers(),
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data.get('data', {})
                    else:
                        logger.warning(f"DynoPay transaction details failed for {transaction_id}")
                        return {}
        except Exception as e:
            logger.error(f"Error getting DynoPay transaction details: {e}")
            return {}

    def is_available(self) -> bool:
        """Check if DynoPay service is properly configured"""
        return bool(self.api_key and self.wallet_token)


# Global instance
dynopay_service = DynoPayService()