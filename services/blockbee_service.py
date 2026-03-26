"""BlockBee Cryptocurrency Payment API Service"""

import asyncio
import aiohttp
import logging
import random
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
from decimal import Decimal
from config import Config
from utils.atomic_transactions import atomic_transaction
from utils.data_sanitizer import mask_api_key_safe
from utils.exchange_state_validator import ExchangeStateValidator
from models import Escrow, ExchangeOrder, CashoutErrorCode, ExchangeStatus
from services.api_adapter_retry import APIAdapterRetry
from sqlalchemy import select

logger = logging.getLogger(__name__)


class BlockBeeAPIError(Exception):
    """Custom exception for BlockBee API errors"""

    pass


class BlockBeeService(APIAdapterRetry):
    """Service for handling BlockBee cryptocurrency payments with unified retry system"""
    
    @staticmethod
    def _enforce_escrow_id_typing(x) -> Optional[int]:
        """
        CRITICAL: Strict escrow_id typing enforcement for BlockBee service
        FIXED: Returns integer for compatibility with Transaction.escrow_pk (Integer field)
        
        Args:
            x: Raw escrow_id value (could be int, str, None, "None", etc.)
            
        Returns:
            Properly typed escrow_id as integer or None
        """
        if x is None:
            return None
        
        # Handle string "None" values
        if isinstance(x, str) and x.lower() in ('none', 'null', ''):
            return None
            
        # Handle string digits - convert to integer
        if isinstance(x, str) and x.isdigit():
            return int(x)
            
        # Handle integer values
        if isinstance(x, int):
            return x
            
        # Invalid types - log warning and return None
        logger.warning(f"Invalid escrow_id type {type(x)}: {x} - converting to None")
        return None

    def __init__(self):
        # Initialize parent APIAdapterRetry
        super().__init__(service_name="blockbee", timeout=30)
        
        self.api_key = Config.BLOCKBEE_API_KEY
        self.base_url = Config.BLOCKBEE_BASE_URL
        self.callback_url = Config.BLOCKBEE_CALLBACK_URL

        if not self.api_key:
            logger.warning("BLOCKBEE_API_KEY not configured - API will not function")
        else:
            # SECURITY FIX: Use secure API key masking
            logger.info(
                f"BlockBee API initialized with key: {mask_api_key_safe(self.api_key)}"
            )
    
    def _map_provider_error_to_unified(self, exception: Exception, context: Optional[Dict] = None) -> CashoutErrorCode:
        """
        Map BlockBee-specific errors to unified error codes for intelligent retry
        """
        error_message = str(exception).lower()
        
        # BlockBee-specific error patterns
        if "blockbee timeout" in error_message or "blockbee timed out" in error_message:
            return CashoutErrorCode.API_TIMEOUT
        elif "blockbee api error" in error_message or "blockbee error" in error_message:
            return CashoutErrorCode.NETWORK_ERROR  # BlockBee errors often network related
        elif "invalid api key" in error_message or "authentication failed" in error_message:
            return CashoutErrorCode.API_AUTHENTICATION_FAILED
        elif "invalid currency" in error_message or "unsupported currency" in error_message:
            return CashoutErrorCode.API_INVALID_REQUEST
        elif "rate limit" in error_message or "too many requests" in error_message:
            return CashoutErrorCode.RATE_LIMIT_EXCEEDED
        elif "service unavailable" in error_message or "502" in error_message or "503" in error_message:
            return CashoutErrorCode.SERVICE_UNAVAILABLE
        elif "network error" in error_message or "connection error" in error_message:
            return CashoutErrorCode.NETWORK_ERROR
        
        # Default to generic classification for unknown BlockBee errors
        return CashoutErrorCode.UNKNOWN_ERROR
    
    def _get_circuit_breaker_name(self) -> str:
        """Return the circuit breaker name for BlockBee API"""
        return "blockbee"

    def _get_headers(self) -> Dict[str, str]:
        """Get headers for BlockBee API requests"""
        return {
            "Content-Type": "application/json",
            "User-Agent": "Telegram-Escrow-Bot/1.0",
        }

    def _get_params(
        self, additional_params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Get params for BlockBee API requests including API key"""
        if not self.api_key:
            logger.error("API key is None or empty - requests will fail")
        params = {"apikey": self.api_key}
        if additional_params:
            params.update(additional_params)
        # SECURITY FIX: Don't log parameter contents that might contain sensitive data
        logger.debug(f"BlockBee request prepared with {len(params)} parameters")
        return params

    def _map_currency_to_blockbee(self, currency: str) -> str:
        """Map internal currency format to BlockBee ticker format"""
        return Config.BLOCKBEE_CURRENCY_MAP.get(currency, currency.lower())

    async def get_supported_currencies(self) -> Dict[str, Any]:
        """Get list of supported cryptocurrencies from BlockBee"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.base_url}/info",
                    params=self._get_params(),
                    headers=self._get_headers(),
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        logger.info(
                            "Successfully retrieved supported currencies from BlockBee"
                        )
                        return data
                    else:
                        error_text = await response.text()
                        # SECURITY: Use secure error logging
                        from utils.secure_error_responses import safe_api_error

                        error_response = safe_api_error(
                            Exception(f"HTTP {response.status}: {error_text}"),
                            "BlockBee",
                        )
                        logger.error(
                            f"BlockBee API error: {error_response['error_id']}"
                        )
                        raise BlockBeeAPIError(
                            "BlockBee service temporarily unavailable"
                        )
        except aiohttp.ClientError as e:
            logger.error(f"Network error connecting to BlockBee: {e}")
            raise BlockBeeAPIError(f"Network error: {e}")
        except Exception as e:
            logger.error(f"Unexpected error in get_supported_currencies: {e}")
            raise BlockBeeAPIError(f"Unexpected error: {e}")

    async def get_currency_info(self, currency: str) -> Dict[str, Any]:
        """Get detailed information about a specific cryptocurrency"""
        ticker = self._map_currency_to_blockbee(currency)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.base_url}/info/{ticker}",
                    params=self._get_params(),
                    headers=self._get_headers(),
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        logger.info(
                            f"Retrieved currency info for {currency} ({ticker})"
                        )
                        return data
                    elif response.status == 404:
                        # Currency not found - return default info to prevent crashes
                        logger.info(
                            f"BlockBee currency {currency} not found, using safe defaults (fallback mode)"
                        )
                        return {
                            "minimum_transaction": 0.00001,
                            "fee": 0.5,
                            "fee_percent": 0.5,
                            "confirmations": 1,
                        }
                    else:
                        error_text = await response.text()
                        logger.error(
                            f"BlockBee currency info error: {response.status} - {error_text}"
                        )
                        raise BlockBeeAPIError(
                            f"Currency info error: {response.status}"
                        )
        except aiohttp.ClientError as e:
            # SECURITY: Use secure error handling
            from utils.secure_error_responses import safe_network_error

            error_response = safe_network_error(e)
            logger.error(f"BlockBee network error: {error_response['error_id']}")
            raise BlockBeeAPIError("BlockBee service temporarily unavailable")
        except Exception as e:
            logger.error(f"Unexpected error in get_currency_info for {currency}: {e}")
            raise BlockBeeAPIError(f"Unexpected error: {e}")

    async def _make_request_with_retry(
        self, url: str, params: Dict[str, Any], max_retries: int = 3
    ) -> Dict[str, Any]:
        """Make HTTP request with retry logic for rate limiting"""
        for attempt in range(max_retries):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        url, headers=self._get_headers(), params=params
                    ) as response:
                        if response.status == 200:
                            return await response.json()
                        elif response.status == 429:
                            if attempt < max_retries - 1:
                                # Exponential backoff with jitter for rate limiting
                                delay = (2**attempt) + random.uniform(0, 1)
                                logger.warning(
                                    f"Rate limited (429), retrying in {delay:.2f} seconds... (attempt {attempt + 1}/{max_retries})"
                                )
                                await asyncio.sleep(delay)
                                continue
                            else:
                                error_text = await response.text()
                                logger.error(
                                    f"Rate limit exceeded after {max_retries} attempts: {error_text}"
                                )
                                raise BlockBeeAPIError(
                                    "Service temporarily busy. Please try again in a few moments."
                                )
                        else:
                            error_text = await response.text()
                            logger.error(
                                f"BlockBee API error: {response.status} - {error_text}"
                            )
                            raise BlockBeeAPIError(f"API error: {response.status}")
            except aiohttp.ClientError as e:
                if attempt < max_retries - 1:
                    delay = (2**attempt) + random.uniform(0, 1)
                    logger.warning(
                        f"Network error, retrying in {delay:.2f} seconds... (attempt {attempt + 1}/{max_retries})"
                    )
                    await asyncio.sleep(delay)
                    continue
                else:
                    logger.error(f"Network error after {max_retries} attempts: {e}")
                    raise BlockBeeAPIError(f"Network error: {e}")

        raise BlockBeeAPIError("Maximum retry attempts exceeded")

    async def create_payment_address(
        self, currency: str, escrow_id: str, amount_usd: Decimal
    ) -> Dict[str, Any]:
        """Create a new payment address for escrow deposit"""
        # VALIDATION LOG: Confirm UTID is being used as escrow_id parameter
        logger.info(f"🔍 BLOCKBEE_UTID_VALIDATION: Creating payment with escrow_id='{escrow_id}' (should be UTID format)")
        ticker = self._map_currency_to_blockbee(currency)

        # For wallet deposits, we can skip callbacks since we poll for confirmations
        # For escrow payments, we need proper callbacks
        callback_url = None
        if self.callback_url:
            callback_url = f"{self.callback_url}/{escrow_id}"
        else:
            # For development/wallet deposits without webhook setup, skip callback
            logger.warning(
                f"No BLOCKBEE_CALLBACK_URL configured, creating address without callback for {escrow_id}"
            )

        # REMOVED: Address configuration logic - BlockBee uses dashboard-configured addresses
        # The address parameter should not be sent in API calls when using dashboard configuration

        # CRITICAL FIX: Remove address parameter - let BlockBee use dashboard-configured addresses
        # FIXED: Add required BlockBee parameters for proper webhook handling
        params_dict = {
            "post": "1",      # Send POST requests instead of GET
            "json": "1",     # Send JSON format instead of URL-encoded
            "pending": "1",  # Receive webhooks for unconfirmed transactions
            "convert": "1"   # Auto-convert to preferred currency
        }
        if callback_url:
            params_dict["callback"] = callback_url
        else:
            # Use production callback URL
            production_callback = f"https://lockbay.replit.app/blockbee/callback/{escrow_id}"
            params_dict["callback"] = production_callback
        
        # Log that we're using dashboard-configured addresses
        logger.info(f"Creating BlockBee address for {currency} using dashboard-configured forwarding address")

        params = self._get_params(params_dict)

        try:
            url = f"{self.base_url}/{ticker}/create"
            logger.info(f"Creating BlockBee address for {currency}: {url}")
            logger.debug("Creating payment address with BlockBee API")
            logger.debug(f"Using configured address for {currency}")
            data = await self._make_request_with_retry(url, params)

            # Validate the response and address
            address_in = data.get("address_in")
            address_out = data.get("address_out")

            # Log full response for debugging
            logger.info(f"BlockBee API Response for {escrow_id}: {data}")
            logger.info(f"Generated address_in: {address_in}")
            logger.info(f"Configured address_out: {address_out}")

            # Validate we got a real address
            if not address_in:
                raise BlockBeeAPIError("No payment address returned from BlockBee API")

            logger.info(f"BlockBee payment setup completed successfully for {currency}")
            logger.info(f"Payment will be forwarded to: {address_out}")

            return {
                "address": address_in,
                "qr_code_url": f"{self.base_url}/qrcode/?address={address_in}&value={data.get('minimum')}&coin={ticker}",
                "minimum_amount": data.get("minimum"),
                "fee_percent": data.get("fee_percent", 1.0),
                "callback_url": callback_url,
                "address_out": address_out,  # Where funds will be forwarded
                "pending": 0,
                "confirmations": 0,
            }
        except Exception as e:
            logger.error(f"Unexpected error creating payment address: {e}")
            raise BlockBeeAPIError(f"Unexpected error: {e}")

    async def check_address_logs(self, currency: str, address: str) -> Dict[str, Any]:
        """Check payment logs for a specific address"""
        ticker = self._map_currency_to_blockbee(currency)

        try:
            async with aiohttp.ClientSession() as session:
                params = self._get_params({"address": address})
                async with session.get(
                    f"{self.base_url}/{ticker}/logs/",
                    headers=self._get_headers(),
                    params=params,
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        logger.info(f"Retrieved address logs for {address}")
                        return data
                    else:
                        error_text = await response.text()
                        logger.error(
                            f"BlockBee address logs error: {response.status} - {error_text}"
                        )
                        raise BlockBeeAPIError(f"Address logs error: {response.status}")
        except aiohttp.ClientError as e:
            logger.error(f"Network error checking address logs: {e}")
            raise BlockBeeAPIError(f"Network error: {e}")
        except Exception as e:
            logger.error(f"Unexpected error checking address logs: {e}")
            raise BlockBeeAPIError(f"Unexpected error: {e}")

    async def create_checkout_payment(
        self, amount_usd: float, currency: str, escrow_id: str
    ) -> Dict[str, Any]:
        """Create a checkout payment link for easier user experience"""
        ticker = self._map_currency_to_blockbee(currency)

        payload = {
            "fiat": "USD",
            "value": amount_usd,
            "coin": ticker,
            "callback": (
                f"{self.callback_url}/blockbee/checkout/{escrow_id}"
                if self.callback_url
                else None
            ),
            "order_id": f"escrow_{escrow_id}_{int(datetime.utcnow().timestamp())}",
            "item_description": f"Escrow deposit for transaction #{escrow_id}",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/checkout/payment/",
                    headers=self._get_headers(),
                    json=payload,
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        logger.info(
                            f"Created checkout payment for escrow {escrow_id}: {data.get('payment_url')}"
                        )

                        return {
                            "payment_url": data.get("payment_url"),
                            "payment_id": data.get("payment_id"),
                            "address_in": data.get("address_in"),
                            "qr_code_url": data.get("qr_code"),
                            "min_confirmations": data.get("min_confirmations", 1),
                            "order_id": payload["order_id"],
                        }
                    else:
                        error_text = await response.text()
                        logger.error(
                            f"BlockBee checkout payment error: {response.status} - {error_text}"
                        )
                        raise BlockBeeAPIError(
                            f"Checkout payment error: {response.status}"
                        )
        except aiohttp.ClientError as e:
            logger.error(f"Network error creating checkout payment: {e}")
            raise BlockBeeAPIError(f"Network error: {e}")
        except Exception as e:
            logger.error(f"Unexpected error creating checkout payment: {e}")
            raise BlockBeeAPIError(f"Unexpected error: {e}")

    async def check_payment_logs(self, payment_id: str) -> Dict[str, Any]:
        """Check logs for a checkout payment"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.base_url}/checkout/payment/logs/",
                    headers=self._get_headers(),
                    params={"payment_id": payment_id},
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        logger.info(f"Retrieved payment logs for {payment_id}")
                        return data
                    else:
                        error_text = await response.text()
                        logger.error(
                            f"BlockBee payment logs error: {response.status} - {error_text}"
                        )
                        raise BlockBeeAPIError(f"Payment logs error: {response.status}")
        except aiohttp.ClientError as e:
            logger.error(f"Network error checking payment logs: {e}")
            raise BlockBeeAPIError(f"Network error: {e}")
        except Exception as e:
            logger.error(f"Unexpected error checking payment logs: {e}")
            raise BlockBeeAPIError(f"Unexpected error: {e}")

    def verify_callback_signature(self, callback_data: str, signature: str) -> bool:
        """Verify BlockBee callback signature using HMAC SHA256 for security"""
        try:
            import hmac
            import hashlib
            import json

            # BlockBee webhook security validation
            if not signature:
                logger.warning("No signature provided for BlockBee callback")
                return False

            # Get webhook secret from config
            webhook_secret = Config.BLOCKBEE_WEBHOOK_SECRET
            if not webhook_secret:
                logger.error(
                    "BLOCKBEE_WEBHOOK_SECRET not configured - cannot verify signatures"
                )
                # Production security: Reject unsigned webhooks
                logger.critical(
                    "SECURITY: BlockBee webhook rejected - no webhook secret configured"
                )
                return False

            # Validate signature format
            if not signature or len(signature) < 64:
                logger.warning(
                    f"Invalid BlockBee signature format: {signature[:20]}..."
                )
                return False

            try:
                # Parse callback data if it's a string
                if isinstance(callback_data, str):
                    try:
                        # Try to parse as JSON first
                        callback_dict = json.loads(callback_data)
                        # Sort keys for consistent signature generation
                        canonical_data = json.dumps(
                            callback_dict, sort_keys=True, separators=(",", ":")
                        )
                    except json.JSONDecodeError:
                        # Use raw string if not valid JSON
                        canonical_data = callback_data
                else:
                    # Convert dict to canonical JSON string
                    canonical_data = json.dumps(
                        callback_data, sort_keys=True, separators=(",", ":")
                    )

                # Calculate expected HMAC SHA256 signature
                expected_signature = hmac.new(
                    webhook_secret.encode("utf-8"),
                    canonical_data.encode("utf-8"),
                    hashlib.sha256,
                ).hexdigest()

                # Compare signatures using secure comparison
                is_valid = hmac.compare_digest(
                    signature.lower(), expected_signature.lower()
                )

                if is_valid:
                    logger.info(
                        f"BlockBee callback signature verification successful: {signature[:16]}..."
                    )
                    return True
                else:
                    logger.error(
                        "BlockBee callback signature verification FAILED - potential attack attempt!"
                    )
                    logger.error(f"Received: {signature[:16]}...")
                    logger.error(f"Expected: {expected_signature[:16]}...")
                    return False

            except Exception as e:
                logger.error(f"Error during BlockBee signature calculation: {e}")
                return False

        except Exception as e:
            logger.error(f"Error verifying BlockBee callback signature: {e}")
            return False

    async def process_callback(
        self, callback_data: Dict[str, Any], signature: Optional[str] = None
    ) -> bool:
        """Process BlockBee payment callback with distributed locking"""
        try:
            # Verify signature for security
            callback_str = str(callback_data) if callback_data else ""
            if signature and not self.verify_callback_signature(
                callback_str, signature
            ):
                logger.error("BlockBee callback signature verification failed")
                return False

            # Extract order information from callback (could be escrow or exchange)
            order_id = callback_data.get("params", {}).get("order_id")
            if not order_id:
                logger.error("No order_id found in BlockBee callback")
                return False
            
            # CRITICAL FIX: Add distributed locking for payment confirmations
            txid_in = callback_data.get("txid_in")
            if not txid_in:
                logger.error("No txid_in found in BlockBee callback")
                return False
            
            # Import distributed locking service
            from utils.distributed_lock import distributed_lock_service
            
            # Acquire distributed lock for this payment
            additional_data = {
                "callback_source": "blockbee",
                "value_coin": callback_data.get("value_coin", 0),
                "value_fiat": callback_data.get("value_fiat", 0),
                "confirmations": callback_data.get("confirmations", 0)
            }
            
            with distributed_lock_service.acquire_payment_lock(
                order_id=str(order_id),
                txid=txid_in,
                timeout=120,  # 2 minutes timeout for payment processing
                additional_data=additional_data
            ) as lock:
                
                if not lock.acquired:
                    logger.warning(
                        f"PAYMENT_RACE_CONDITION_PREVENTED: Could not acquire lock for "
                        f"order {order_id}, txid {txid_in}. Reason: {lock.error}"
                    )
                    return True  # Return success to prevent retries
                
                logger.critical(
                    f"DISTRIBUTED_LOCK_SUCCESS: Processing payment for order {order_id}, "
                    f"txid {txid_in} with exclusive lock"
                )
                
                # Process the payment - try escrow, exchange orders, or wallet deposits
                return await self._process_locked_payment(callback_data, order_id, txid_in)
                
        except Exception as e:
            logger.error(f"Unexpected error in BlockBee callback processing: {e}", exc_info=True)
            return False
    
    async def _process_locked_payment(self, callback_data: Dict[str, Any], order_id: str, txid_in: str) -> bool:
        """Process payment within distributed lock context"""
        try:
            with atomic_transaction() as session:
                # Check if this is a wallet deposit (ID starts with WALLET-)
                is_wallet_deposit = order_id and str(order_id).startswith('WALLET-')
                
                if is_wallet_deposit:
                    # Handle wallet deposit with 2% markup calculation
                    return await self._process_wallet_deposit_callback(order_id, callback_data, session)
                
                # First try to find as escrow using escrow_id field (string)
                escrow = session.query(Escrow).filter(Escrow.escrow_id == order_id).first()
                exchange_order = None
                
                if not escrow:
                    # Check if this is an exchange order (starts with EX)
                    if order_id.startswith('EX'):
                        try:
                            # Extract numeric ID from exchange order ID (EX000001 -> 1)
                            numeric_id = int(order_id[2:])
                            exchange_order = session.query(ExchangeOrder).filter(ExchangeOrder.id == numeric_id).first()
                            if exchange_order:
                                logger.info(f"Found exchange order {numeric_id} from ID {order_id}")
                        except (ValueError, TypeError):
                            logger.error(f"Invalid exchange order ID format: {order_id}")
                    else:
                        # FALLBACK: Try to find by internal ID for legacy callbacks
                        try:
                            numeric_order_id = int(order_id)
                            
                            # CRITICAL FIX: Check exchange orders FIRST to prevent misrouting
                            # Exchange orders are more commonly created than escrows
                            exchange_order = session.query(ExchangeOrder).filter(ExchangeOrder.id == numeric_order_id).first()
                            if exchange_order:
                                logger.info(f"✅ Found exchange order by numeric ID {numeric_order_id} (legacy)")
                            else:
                                # Only check escrow if no exchange order found
                                escrow = session.query(Escrow).filter(Escrow.id == numeric_order_id).first()
                                if escrow:
                                    logger.warning(f"Found escrow by internal ID {numeric_order_id}, should use escrow_id: {escrow.escrow_id}")
                        except (ValueError, TypeError):
                            # Not a numeric ID, skip numeric lookup
                            logger.info(f"Order ID {order_id} is not numeric, skipping numeric lookup")
                    
                if not escrow and not exchange_order:
                    logger.error(f"No escrow or exchange order found with ID {order_id}")
                    return False

                # Determine if this is an escrow or exchange payment
                is_escrow = escrow is not None
                
                # MONITORING: Log processing path determination
                logger.info(f"🔍 Payment processing path determination: is_escrow={is_escrow}, "
                          f"escrow_id={getattr(escrow, 'escrow_id', 'None')}, "
                          f"exchange_order_id={getattr(exchange_order, 'id', 'None')}")

                # Enhanced: Extract payment details with Decimal precision
                txid_in = callback_data.get("txid_in") or ""
                value_coin = Decimal(str(callback_data.get("value_coin", 0)))
                price_usd = Decimal(str(callback_data.get("price", 0)))
                
                # Calculate USD value if not provided by BlockBee
                value_fiat = Decimal(str(callback_data.get("value_fiat", 0)))
                if value_fiat == 0 and value_coin > 0 and price_usd > 0:
                    value_fiat = value_coin * price_usd
                    logger.info(f"Calculated USD value: {value_coin} * {price_usd} = ${value_fiat:.2f}")
                confirmations = int(callback_data.get("confirmations", 0))
                confirmed = callback_data.get("confirmed", False)

                if is_escrow:
                    logger.info(
                        f"Processing BlockBee callback for escrow {escrow.escrow_id}: "
                        f"Amount: {value_fiat} USD, Confirmations: {confirmations}, Confirmed: {confirmed}"
                    )
                elif exchange_order:
                    logger.info(
                        f"Processing BlockBee callback for exchange order {exchange_order.id}: "
                        f"Amount: {value_fiat} USD, Confirmations: {confirmations}, Confirmed: {confirmed}"
                    )

                # Enhanced: Idempotency check - prevent duplicate transaction processing
                from models import Transaction, TransactionType

                if is_escrow and escrow:
                    # CRITICAL FIX: Check for ANY transaction with this txid_in to prevent double processing
                    existing_payment = (
                        session.query(Transaction)
                        .filter(
                            Transaction.blockchain_address == txid_in,
                            Transaction.escrow_id == escrow.id,
                        )
                        .first()
                    )

                    if existing_payment:
                        logger.info(
                            f"Payment with txid {txid_in} already processed for escrow {escrow.escrow_id} "
                            f"(existing transaction: {existing_payment.transaction_id})"
                        )
                        return True

                    # Enhanced: Status transition guard - only update if in expected state
                    from models import EscrowStatus

                    # CRITICAL FIX: Handle both string and enum status types safely
                    current_status = escrow.status
                    if hasattr(current_status, 'value'):
                        current_status_value = current_status.value
                    else:
                        current_status_value = str(current_status)
                    
                    if current_status_value != EscrowStatus.PAYMENT_PENDING.value:
                        logger.warning(
                            f"Escrow {escrow.escrow_id} not in payment pending state, current status: {escrow.status}"
                        )
                        # Don't process duplicate callbacks for already confirmed payments
                        if current_status_value == EscrowStatus.PAYMENT_CONFIRMED.value:
                            logger.info(f"Ignoring duplicate callback for already confirmed escrow {escrow.escrow_id}")
                            return True  # Return success to prevent 400 errors
                        
                        # CRITICAL FIX: Handle payment to cancelled escrows
                        if current_status_value == EscrowStatus.CANCELLED.value:
                            logger.info(f"PAYMENT_EDGE_CASE: Crypto payment received for cancelled escrow {escrow.escrow_id}")
                            
                            # Credit USD wallet for cancelled escrow payment
                            success = await self._credit_wallet_for_cancelled_escrow_payment(
                                session, escrow, value_fiat, txid_in, callback_data
                            )
                            
                            if success:
                                logger.info(f"✅ Credited USD wallet for cancelled escrow payment: {escrow.escrow_id}, Amount ${value_fiat:.2f}")
                                return True
                            else:
                                logger.error(f"❌ Failed to credit wallet for cancelled escrow payment: {escrow.escrow_id}")
                                return False
                                
                        return False
                elif not is_escrow and exchange_order:
                    # Exchange order processing - Enhanced deduplication
                    existing_tx_hash = getattr(exchange_order, 'deposit_tx_hash', None)
                    if existing_tx_hash and existing_tx_hash == txid_in:
                        logger.info(
                            f"Payment with txid {txid_in} already processed for exchange order {exchange_order.id}"
                        )
                        return True

                    current_status = getattr(exchange_order, 'status', None)
                    if current_status not in ["created", "awaiting_deposit"]:
                        logger.warning(
                            f"Exchange order {exchange_order.id} not in valid state for payment, current status: {current_status}"
                        )
                        
                        # CRITICAL FIX: Special handling for cancelled orders - credit wallet instead
                        if current_status == "cancelled":
                            logger.info(f"PAYMENT_EDGE_CASE: Crypto payment received for cancelled exchange order {exchange_order.id}")
                            
                            # Credit USD wallet for cancelled order payment
                            success = await self._credit_wallet_for_cancelled_crypto_payment(
                                session, exchange_order, value_fiat, txid_in, callback_data
                            )
                            
                            if success:
                                logger.info(f"✅ Credited USD wallet for cancelled order payment: Order {exchange_order.id}, Amount ${value_fiat:.2f}")
                                return True
                            else:
                                logger.error(f"❌ Failed to credit wallet for cancelled order payment: Order {exchange_order.id}")
                                return False
                            
                        return False
                    
                    # PAYMENT VARIANCE HANDLING: Check for overpayment or underpayment
                    # NOTE: This logic applies ONLY to exchange orders, NOT to wallet deposits
                    # Wallet deposits credit the exact received amount without tolerance thresholds
                    expected_amount = Decimal(str(getattr(exchange_order, 'source_amount', 0)))
                    received_amount = value_coin
                    
                    if received_amount > expected_amount:
                        # OVERPAYMENT: Credit excess to wallet
                        logger.info(
                            f"Overpayment detected for order {exchange_order.id}: "
                            f"Expected {expected_amount}, Received {received_amount}"
                        )
                        
                        try:
                            from services.overpayment_service import overpayment_service
                            await overpayment_service.handle_exchange_overpayment(
                                user_id=getattr(exchange_order, 'user_id'),
                                order_id=exchange_order.id,  # type: ignore[arg-type]
                                expected_amount=expected_amount,
                                received_amount=received_amount,
                                crypto_currency=callback_data.get("coin", "UNKNOWN").upper(),
                                usd_rate=price_usd
                            )
                        except Exception as e:
                            logger.error(f"Failed to process overpayment for order {exchange_order.id}: {e}")
                    
                    elif received_amount < expected_amount:
                        # UNDERPAYMENT: Check tolerance and credit received amount to wallet
                        logger.info(
                            f"Underpayment detected for order {exchange_order.id}: "
                            f"Expected {expected_amount}, Received {received_amount}"
                        )
                        
                        try:
                            from services.overpayment_service import overpayment_service
                            underpayment_handled = await overpayment_service.handle_exchange_underpayment(
                                user_id=getattr(exchange_order, 'user_id'),
                                order_id=exchange_order.id,  # type: ignore[arg-type]
                                expected_amount=expected_amount,
                                received_amount=received_amount,
                                crypto_currency=callback_data.get("coin", "UNKNOWN").upper(),
                                usd_rate=price_usd
                            )
                            
                            if not underpayment_handled:
                                # Underpayment exceeds tolerance - order remains incomplete
                                logger.warning(f"Order {exchange_order.id} underpayment exceeds tolerance")
                                try:
                                    current_status = ExchangeStatus(exchange_order.status) if isinstance(exchange_order.status, str) else exchange_order.status
                                    new_status = ExchangeStatus.PAYMENT_INSUFFICIENT
                                    is_valid, reason = ExchangeStateValidator.validate_transition(current_status, new_status, str(exchange_order.id))
                                    if is_valid:
                                        setattr(exchange_order, 'status', 'payment_insufficient')
                                    else:
                                        logger.error(f"🚫 BLOCKBEE_EXCHANGE_TRANSITION_BLOCKED: {current_status.value}→{new_status.value} for {exchange_order.id}: {reason}")
                                except Exception as e:
                                    logger.error(f"🚫 BLOCKBEE_EXCHANGE_TRANSITION_BLOCKED: Error validating transition for {exchange_order.id}: {e}")
                                return True  # Don't process the exchange
                        except Exception as e:
                            logger.error(f"Failed to process underpayment for order {exchange_order.id}: {e}")

                # UNIFIED PAYMENT PROCESSING: Handle all escrow payment scenarios
                if is_escrow and escrow:
                    # Use FeeCalculator to get standardized payment amounts
                    from utils.fee_calculator import FeeCalculator
                    
                    fee_split = getattr(escrow, 'fee_split_option', 'buyer_pays')
                    base_amount = Decimal(str(getattr(escrow, 'amount', 0)))
                    
                    # Get standardized fee breakdown
                    fee_breakdown = FeeCalculator.calculate_escrow_breakdown(
                        escrow_amount=float(base_amount),
                        fee_split_option=fee_split
                    )
                    
                    # Use fee calculator result for consistent amount calculation
                    buyer_pays_usd = Decimal(str(fee_breakdown['buyer_total_payment']))
                    
                    logger.info(
                        f"Using FeeCalculator for escrow {escrow.escrow_id}: "
                        f"Base: ${base_amount}, Buyer pays: ${buyer_pays_usd} (fee split: {fee_split})"
                    )
                    
                    # CRITICAL FIX: Apply ESCROW markup structure: (Market Rate + 5% Markup) + Platform Fee
                    # The buyer_pays_usd already includes platform fee from FeeCalculator
                    # Now we need to apply the 5% markup to the market rate conversion
                    
                    if price_usd > 0:
                        # Apply markup to market rate: user needs more crypto due to markup
                        from config import Config
                        markup_percentage = Decimal(str(Config.EXCHANGE_MARKUP_PERCENTAGE)) / Decimal("100")
                        # Solve: crypto_amount * rate * (1 + markup) = buyer_pays_usd
                        expected_amount = buyer_pays_usd / (price_usd * (Decimal("1") + markup_percentage))
                        
                        logger.info(
                            f"ESCROW markup applied: {markup_percentage*100}% on market rate ${price_usd}, "
                            f"Total USD: ${buyer_pays_usd}, Crypto needed: {expected_amount:.8f}"
                        )
                    else:
                        expected_amount = Decimal("0")
                        logger.error(f"Invalid USD rate for escrow {escrow.escrow_id}: {price_usd}")
                    
                    received_amount = value_coin
                    
                    # Initialize processing flag
                    unified_processing_completed = False
                    
                    # UNIFIED PROCESSING: Use new unified processor for all payment scenarios
                    logger.info(f"📊 UNIFIED PROCESSOR - Processing payment for {escrow.escrow_id}")
                    
                    try:
                        from services.unified_payment_processor import unified_processor
                        
                        processing_result = await unified_processor.process_escrow_payment(
                            escrow=escrow,
                            received_amount=received_amount,
                            received_usd=float(value_fiat),
                            crypto_currency=callback_data.get("coin", "UNKNOWN").upper(),
                            tx_hash=txid_in,
                            price_usd=float(price_usd)
                        )
                        
                        # CRITICAL: Extract and log holding verification results for BlockBee crypto payments
                        processing_result.fund_breakdown.get('holding_verification', {})
                        holding_verified = processing_result.fund_breakdown.get('holding_verified', False)
                        holding_auto_recovered = processing_result.fund_breakdown.get('holding_auto_recovered', False)
                        
                        logger.info(
                            f"🔍 BLOCKBEE_HOLDING_VERIFICATION: {escrow.escrow_id} - "
                            f"Verified: {holding_verified}, Auto-recovered: {holding_auto_recovered}"
                        )
                        
                        if processing_result.success:
                            if processing_result.escrow_confirmed:
                                logger.info(f"✅ Unified processor confirmed escrow {escrow.escrow_id}")
                                
                                # CRITICAL FIX: Update escrow timing fields that unified processor doesn't set
                                # Without this, expires_at stays at the 15-min payment window instead of 24h seller acceptance
                                escrow.status = EscrowStatus.PAYMENT_CONFIRMED.value
                                escrow.payment_confirmed_at = datetime.utcnow()
                                from config import Config as BlockBeeConfig
                                escrow.expires_at = datetime.utcnow() + timedelta(minutes=BlockBeeConfig.SELLER_RESPONSE_TIMEOUT_MINUTES)
                                
                                # DELIVERY COUNTDOWN: Set delivery_deadline based on payment confirmation time
                                if escrow.pricing_snapshot and 'delivery_hours' in escrow.pricing_snapshot:
                                    delivery_hours = int(escrow.pricing_snapshot['delivery_hours'])
                                    escrow.delivery_deadline = datetime.utcnow() + timedelta(hours=delivery_hours)
                                    escrow.auto_release_at = escrow.delivery_deadline + timedelta(hours=24)
                                    logger.info(f"⏰ DELIVERY_DEADLINE_SET: Escrow {escrow.escrow_id} delivery countdown starts - {delivery_hours}h")
                                
                                logger.info(f"⏰ EXPIRES_AT_UPDATED: Escrow {escrow.escrow_id} seller has {BlockBeeConfig.SELLER_RESPONSE_TIMEOUT_MINUTES}m to accept")
                                
                                # Additional holding verification logging for BlockBee crypto payments
                                if holding_verified:
                                    if holding_auto_recovered:
                                        logger.warning(
                                            f"🔧 BLOCKBEE_AUTO_RECOVERY: Holding for {escrow.escrow_id} required auto-recovery "
                                            f"after crypto payment {txid_in} ({callback_data.get('coin', 'UNKNOWN')})"
                                        )
                                    else:
                                        logger.info(
                                            f"✅ BLOCKBEE_HOLDING_VERIFIED: Holding properly verified for {escrow.escrow_id} "
                                            f"after crypto payment {txid_in} ({callback_data.get('coin', 'UNKNOWN')})"
                                        )
                                else:
                                    logger.error(
                                        f"❌ BLOCKBEE_HOLDING_FAILED: Critical holding verification failure for {escrow.escrow_id} "
                                        f"after crypto payment {txid_in} ({callback_data.get('coin', 'UNKNOWN')})"
                                    )
                                
                                if processing_result.overpayment_handled:
                                    logger.info(f"💰 Overpayment handled: ${processing_result.fund_breakdown.get('overpayment_credited', 0):.2f}")
                            else:
                                logger.warning(f"⚠️ Escrow {escrow.escrow_id} payment processed but not confirmed (insufficient payment)")
                        else:
                            logger.error(f"❌ Unified processor failed for {escrow.escrow_id}: {processing_result.error_message}")
                            
                            # Fallback to legacy processing
                            logger.info(f"🔄 Falling back to legacy processing for {escrow.escrow_id}")
                            escrow.status = EscrowStatus.PAYMENT_INSUFFICIENT.value
                            
                        # CRITICAL FIX: Skip duplicate legacy processing path since unified processor handled everything
                        unified_processing_completed = True
                            
                    except Exception as e:
                        logger.error(f"Critical error in unified processor for {escrow.escrow_id}: {e}")
                        
                        # Emergency fallback: Credit to wallet and mark insufficient
                        try:
                            from services.crypto import CryptoService
                            
                            received_usd_value = float(received_amount * price_usd)
                            credit_success = CryptoService.credit_user_wallet_atomic(
                                user_id=getattr(escrow, 'buyer_id'),
                                amount=received_usd_value,
                                currency="USD",
                                transaction_type="emergency_recovery",
                                description=f"Emergency recovery for escrow {escrow.escrow_id} - processor failure"
                            )
                            
                            if credit_success:
                                logger.info(f"Emergency: Credited ${received_usd_value:.2f} to buyer's wallet for {escrow.escrow_id}")
                            
                            escrow.status = EscrowStatus.PAYMENT_INSUFFICIENT.value
                        
                        except Exception as recovery_error:
                            logger.error(f"CRITICAL: Recovery failed for escrow {escrow.escrow_id}: {recovery_error}")
                            # Last resort: mark escrow as payment failed but log for manual intervention
                            escrow.status = EscrowStatus.PAYMENT_FAILED.value

                # Update order status based on confirmation count (BlockBee confirmed flag is unreliable)
                if confirmations >= 1:
                    if is_escrow and escrow and not unified_processing_completed:
                        # Check if payment amount is acceptable before confirming
                        # Calculate expected crypto amount based on what buyer should pay
                        fee_split = getattr(escrow, 'fee_split_option', 'buyer_pays')
                        base_amount = Decimal(str(getattr(escrow, 'amount', 0)))
                        
                        # Use FeeCalculator to get standardized payment amounts
                        from utils.fee_calculator import FeeCalculator
                        
                        # Get standardized fee breakdown
                        fee_breakdown = FeeCalculator.calculate_escrow_breakdown(
                            escrow_amount=float(base_amount),
                            fee_split_option=fee_split
                        )
                        
                        # Use fee calculator result for consistent amount calculation
                        buyer_pays_usd = Decimal(str(fee_breakdown['buyer_total_payment']))
                        
                        logger.info(
                            f"Using FeeCalculator for fund segregation on escrow {escrow.escrow_id}: "
                            f"Base: ${base_amount}, Buyer pays: ${buyer_pays_usd} (fee split: {fee_split})"
                        )
                        
                        # CRITICAL FIX: Apply ESCROW markup structure for fund segregation
                        if price_usd > 0:
                            # Apply markup to market rate for escrow payments
                            from config import Config
                            markup_percentage = Decimal(str(Config.EXCHANGE_MARKUP_PERCENTAGE)) / Decimal("100")
                            expected_amount = buyer_pays_usd / (price_usd * (Decimal("1") + markup_percentage))
                            
                            logger.info(
                                f"Fund segregation markup applied: {markup_percentage*100}% on rate ${price_usd}"
                            )
                        else:
                            expected_amount = Decimal("0")
                            logger.error(f"Invalid USD rate for escrow {escrow.escrow_id}: {price_usd}")
                        
                        received_amount = value_coin
                        
                        if received_amount >= expected_amount or (received_amount < expected_amount and (float((expected_amount - received_amount) * price_usd) <= 1.0)):
                            # Full payment or underpayment within tolerance - proceed with escrow
                            from models import EscrowStatus
                            from services.escrow_fund_manager import EscrowFundManager
                            
                            # CRITICAL FIX: Proper fund segregation instead of crediting entire payment to wallet
                            logger.info(f"📊 PROCESSING PATH B - Fund Segregation Logic for {escrow.escrow_id}")
                            try:
                                fund_segregation_result = await EscrowFundManager.process_escrow_payment(
                                    escrow_id=escrow.escrow_id,
                                    total_received_usd=value_fiat,
                                    expected_total_usd=buyer_pays_usd,
                                    crypto_amount=received_amount,
                                    crypto_currency=callback_data.get("coin", "UNKNOWN").upper(),
                                    tx_hash=txid_in,
                                    funds_source="external_crypto"  # External crypto payment, no wallet freeze
                                )
                                
                                # CRITICAL FIX: Comprehensive fund segregation validation
                                if fund_segregation_result.get("success"):
                                    # Extract segregation amounts for validation
                                    escrow_held = Decimal(str(fund_segregation_result.get('escrow_held', 0)))
                                    platform_fee_collected = Decimal(str(fund_segregation_result.get('platform_fee_collected', 0)))
                                    overpayment_credited = Decimal(str(fund_segregation_result.get('overpayment_credited', 0)))
                                    Decimal(str(fund_segregation_result.get('underpayment_amount', 0)))
                                    
                                    # Calculate expected totals for validation
                                    expected_escrow_amount = base_amount
                                    expected_platform_fee = Decimal(str(escrow.fee_amount)) if getattr(escrow, 'fee_split_option', 'buyer_pays') == 'buyer_pays' else Decimal('0')
                                    
                                    # Validate fund segregation amounts
                                    validation_errors = []
                                    
                                    # 1. Validate escrow held amount matches expected base amount
                                    if abs(escrow_held - expected_escrow_amount) > Decimal('0.01'):
                                        validation_errors.append(f"Escrow held ${escrow_held} != expected ${expected_escrow_amount}")
                                    
                                    # 2. Validate platform fee matches expected fee (if buyer pays)
                                    if abs(platform_fee_collected - expected_platform_fee) > Decimal('0.01'):
                                        validation_errors.append(f"Platform fee ${platform_fee_collected} != expected ${expected_platform_fee}")
                                    
                                    # 3. Validate total segregated funds don't exceed received amount
                                    total_segregated = escrow_held + platform_fee_collected + overpayment_credited
                                    if total_segregated > value_fiat + Decimal('0.01'):  # Small tolerance for rounding
                                        validation_errors.append(f"Total segregated ${total_segregated} > received ${value_fiat}")
                                    
                                    # 4. Validate overpayment calculation is correct
                                    expected_total_payment = expected_escrow_amount + expected_platform_fee
                                    expected_overpayment = max(Decimal('0'), value_fiat - expected_total_payment)
                                    if abs(overpayment_credited - expected_overpayment) > Decimal('0.01'):
                                        validation_errors.append(f"Overpayment ${overpayment_credited} != expected ${expected_overpayment}")
                                    
                                    if not validation_errors:
                                        # All validations passed - proceed with payment confirmation
                                        escrow.status = EscrowStatus.PAYMENT_CONFIRMED.value
                                        escrow.payment_confirmed_at = datetime.utcnow()
                                        from config import Config as LegacyBlockBeeConfig
                                        escrow.expires_at = datetime.utcnow() + timedelta(minutes=LegacyBlockBeeConfig.SELLER_RESPONSE_TIMEOUT_MINUTES)
                                        
                                        # DELIVERY COUNTDOWN: Set delivery_deadline based on payment confirmation time
                                        # Delivery time starts counting AFTER payment, not at creation
                                        if escrow.pricing_snapshot and 'delivery_hours' in escrow.pricing_snapshot:
                                            delivery_hours = int(escrow.pricing_snapshot['delivery_hours'])
                                            escrow.delivery_deadline = datetime.utcnow() + timedelta(hours=delivery_hours)
                                            escrow.auto_release_at = escrow.delivery_deadline + timedelta(hours=24)  # 24h grace
                                            logger.info(f"⏰ DELIVERY_DEADLINE_SET: Escrow {escrow.escrow_id} delivery countdown starts now - {delivery_hours}h deadline")
                                        
                                        logger.info(f"✅ FUND_SEGREGATION_VALIDATED: Escrow {escrow.escrow_id} payment confirmed with validated fund segregation: "
                                                  f"Held: ${escrow_held:.2f}, Fee: ${platform_fee_collected:.2f}, Overpay: ${overpayment_credited:.2f} | Expires in 24h")
                                    else:
                                        # Validation failed - log errors and mark for manual review
                                        logger.error(f"❌ FUND_SEGREGATION_VALIDATION_FAILED: Escrow {escrow.escrow_id} segregation validation errors: {'; '.join(validation_errors)}")
                                        escrow.status = EscrowStatus.PAYMENT_INSUFFICIENT.value  # Mark for manual review
                                        
                                        # Alert admins about validation failure
                                        try:
                                            from services.consolidated_notification_service import consolidated_notification_service
                                            await consolidated_notification_service.send_admin_alert(
                                                f"🚨 FUND_SEGREGATION_VALIDATION_FAILED\n"
                                                f"Escrow: {escrow.escrow_id}\n"
                                                f"Received: ${value_fiat:.2f}\n"
                                                f"Errors: {'; '.join(validation_errors)}\n"
                                                f"Manual review required!"
                                            )
                                        except Exception as alert_error:
                                            logger.error(f"Failed to send fund segregation validation alert: {alert_error}")
                                else:
                                    logger.error(f"❌ Fund segregation failed for escrow {escrow.escrow_id}: {fund_segregation_result.get('error')}")
                                    escrow.status = EscrowStatus.PAYMENT_CONFIRMED.value  # Keep as payment_confirmed if segregation fails
                                    escrow.payment_confirmed_at = datetime.utcnow()
                                    escrow.expires_at = datetime.utcnow() + timedelta(minutes=LegacyBlockBeeConfig.SELLER_RESPONSE_TIMEOUT_MINUTES)
                                
                                    # DELIVERY COUNTDOWN: Set delivery_deadline based on payment confirmation time
                                    if escrow.pricing_snapshot and 'delivery_hours' in escrow.pricing_snapshot:
                                        delivery_hours = int(escrow.pricing_snapshot['delivery_hours'])
                                        escrow.delivery_deadline = datetime.utcnow() + timedelta(hours=delivery_hours)
                                        escrow.auto_release_at = escrow.delivery_deadline + timedelta(hours=24)
                                        logger.info(f"⏰ DELIVERY_DEADLINE_SET: Escrow {escrow.escrow_id} delivery countdown starts - {delivery_hours}h")
                            except Exception as segregation_error:
                                logger.error(f"❌ Critical error in fund segregation for escrow {escrow.escrow_id}: {segregation_error}")
                                # Fallback: Keep as payment_confirmed if segregation has errors
                                escrow.status = EscrowStatus.PAYMENT_CONFIRMED.value
                                escrow.payment_confirmed_at = datetime.utcnow()
                                escrow.expires_at = datetime.utcnow() + timedelta(minutes=LegacyBlockBeeConfig.SELLER_RESPONSE_TIMEOUT_MINUTES)
                                
                                # DELIVERY COUNTDOWN: Set delivery_deadline based on payment confirmation time
                                if escrow.pricing_snapshot and 'delivery_hours' in escrow.pricing_snapshot:
                                    delivery_hours = int(escrow.pricing_snapshot['delivery_hours'])
                                    escrow.delivery_deadline = datetime.utcnow() + timedelta(hours=delivery_hours)
                                    escrow.auto_release_at = escrow.delivery_deadline + timedelta(hours=24)
                        else:
                            # Insufficient payment - do not confirm escrow
                            from models import EscrowStatus
                            escrow.status = EscrowStatus.PAYMENT_INSUFFICIENT.value  # CRITICAL FIX: Use .value for database
                            logger.warning(f"Escrow {escrow.escrow_id} payment insufficient - escrow not confirmed")
                    elif not is_escrow and exchange_order:
                        # Determine order status based on payment amount
                        expected_amount = Decimal(str(getattr(exchange_order, 'source_amount', 0)))
                        received_amount = value_coin
                        
                        if received_amount >= expected_amount or (received_amount < expected_amount and (float((expected_amount - received_amount) * price_usd) <= 1.0)):
                            # Full payment or underpayment within tolerance - proceed with exchange
                            try:
                                current_status = ExchangeStatus(exchange_order.status) if isinstance(exchange_order.status, str) else exchange_order.status
                                new_status = ExchangeStatus.PAYMENT_RECEIVED
                                is_valid, reason = ExchangeStateValidator.validate_transition(current_status, new_status, str(exchange_order.id))
                                if is_valid:
                                    setattr(exchange_order, 'status', "payment_received")
                                else:
                                    logger.error(f"🚫 BLOCKBEE_EXCHANGE_TRANSITION_BLOCKED: {current_status.value}→{new_status.value} for {exchange_order.id}: {reason}")
                            except Exception as e:
                                logger.error(f"🚫 BLOCKBEE_EXCHANGE_TRANSITION_BLOCKED: Error validating transition for {exchange_order.id}: {e}")
                            setattr(exchange_order, 'deposit_tx_hash', txid_in)
                            # CRITICAL FIX: Do NOT set completed_at here - that's only for when the entire order is done!
                            # setattr(exchange_order, 'completed_at', datetime.utcnow())  # BUG: This was preventing processing!
                            
                            logger.info(f"Exchange order {exchange_order.id} payment confirmed - will be processed automatically by exchange monitor")
                            
                            # IMMEDIATE PROCESSING: Trigger NGN payout right away instead of waiting for scheduler
                            if getattr(exchange_order, 'order_type', None) == 'crypto_to_ngn':
                                logger.info(f"Immediately processing NGN payout for order {exchange_order.id}")
                                from jobs.exchange_monitor import process_ngn_payout
                                
                                # Mark as processing first to prevent duplicate processing
                                try:
                                    current_status = ExchangeStatus(exchange_order.status) if isinstance(exchange_order.status, str) else exchange_order.status
                                    new_status = ExchangeStatus.PROCESSING
                                    is_valid, reason = ExchangeStateValidator.validate_transition(current_status, new_status, str(exchange_order.id))
                                    if is_valid:
                                        setattr(exchange_order, 'status', 'processing')
                                    else:
                                        logger.error(f"🚫 BLOCKBEE_EXCHANGE_TRANSITION_BLOCKED: {current_status.value}→{new_status.value} for {exchange_order.id}: {reason}")
                                except Exception as e:
                                    logger.error(f"🚫 BLOCKBEE_EXCHANGE_TRANSITION_BLOCKED: Error validating transition for {exchange_order.id}: {e}")
                                session.commit()
                                
                                try:
                                    payout_result = await process_ngn_payout(session, exchange_order)
                                    if payout_result:
                                        logger.info(f"✅ NGN payout successful for order {exchange_order.id}")
                                        # Final completion notification will be sent automatically when order status becomes 'completed'
                                    else:
                                        logger.error(f"❌ NGN payout failed for order {exchange_order.id}, will retry via scheduler")
                                        # Revert to payment_received for scheduler retry
                                        try:
                                            current_status = ExchangeStatus(exchange_order.status) if isinstance(exchange_order.status, str) else exchange_order.status
                                            new_status = ExchangeStatus.PAYMENT_RECEIVED
                                            is_valid, reason = ExchangeStateValidator.validate_transition(current_status, new_status, str(exchange_order.id))
                                            if is_valid:
                                                setattr(exchange_order, 'status', 'payment_received')
                                            else:
                                                logger.warning(f"🚫 BLOCKBEE_EXCHANGE_TRANSITION_BLOCKED: {current_status.value}→{new_status.value} for {exchange_order.id}: {reason}")
                                        except Exception as val_e:
                                            logger.error(f"🚫 BLOCKBEE_EXCHANGE_TRANSITION_BLOCKED: Error validating transition for {exchange_order.id}: {val_e}")
                                except Exception as payout_error:
                                    logger.error(f"Error processing immediate payout for order {exchange_order.id}: {payout_error}")
                                    # Revert to payment_received for scheduler retry
                                    try:
                                        current_status = ExchangeStatus(exchange_order.status) if isinstance(exchange_order.status, str) else exchange_order.status
                                        new_status = ExchangeStatus.PAYMENT_RECEIVED
                                        is_valid, reason = ExchangeStateValidator.validate_transition(current_status, new_status, str(exchange_order.id))
                                        if is_valid:
                                            setattr(exchange_order, 'status', 'payment_received')
                                        else:
                                            logger.warning(f"🚫 BLOCKBEE_EXCHANGE_TRANSITION_BLOCKED: {current_status.value}→{new_status.value} for {exchange_order.id}: {reason}")
                                    except Exception as val_e:
                                        logger.error(f"🚫 BLOCKBEE_EXCHANGE_TRANSITION_BLOCKED: Error validating transition for {exchange_order.id}: {val_e}")
                                finally:
                                    # ASYNC FIX: Clean up any HTTP sessions used during payout processing
                                    try:
                                        from services.fincra_service import fincra_service
                                        await fincra_service.close_session()
                                    except Exception as cleanup_error:
                                        logger.debug(f"Session cleanup warning (non-critical): {cleanup_error}")
                        else:
                            # Insufficient payment - do not proceed with exchange
                            try:
                                current_status = ExchangeStatus(exchange_order.status) if isinstance(exchange_order.status, str) else exchange_order.status
                                new_status = ExchangeStatus.PAYMENT_INSUFFICIENT
                                is_valid, reason = ExchangeStateValidator.validate_transition(current_status, new_status, str(exchange_order.id))
                                if is_valid:
                                    setattr(exchange_order, 'status', "payment_insufficient")
                                else:
                                    logger.error(f"🚫 BLOCKBEE_EXCHANGE_TRANSITION_BLOCKED: {current_status.value}→{new_status.value} for {exchange_order.id}: {reason}")
                            except Exception as e:
                                logger.error(f"🚫 BLOCKBEE_EXCHANGE_TRANSITION_BLOCKED: Error validating transition for {exchange_order.id}: {e}")
                            setattr(exchange_order, 'deposit_tx_hash', txid_in)
                            
                            logger.warning(f"Exchange order {exchange_order.id} payment insufficient - order not processed")

                    # Create transaction record
                    if is_escrow:
                        # Enhanced: Create transaction record with Decimal precision
                        from utils.universal_id_generator import UniversalIDGenerator

                        # Check if transaction already exists to prevent duplicates
                        existing_transaction = session.query(Transaction).filter(
                            Transaction.blockchain_address == txid_in,
                            Transaction.user_id == escrow.buyer_id,
                            Transaction.transaction_type == TransactionType.DEPOSIT.value
                        ).first()
                        
                        if not existing_transaction:
                            transaction_utid = UniversalIDGenerator.generate_transaction_id()  # TX = Transaction records
                            
                            # Fix currency mapping from coin to proper currency
                            coin = callback_data.get("coin", "UNKNOWN").upper()
                            # DynoPay supported currencies only
                            currency_map = {
                                'BTC': 'BTC',
                                'ETH': 'ETH', 
                                'LTC': 'LTC',
                                'DOGE': 'DOGE',
                                'BCH': 'BCH',
                                'TRX': 'TRX',
                                'USDT_ERC20': 'USDT',
                                'USDT_TRC20': 'USDT'
                            }
                            proper_currency = currency_map.get(coin, coin)
                            
                            transaction = Transaction(
                                transaction_id=UniversalIDGenerator.generate_transaction_id(),
                                utid=transaction_utid,
                                escrow_id=escrow.id,
                                user_id=escrow.buyer_id,
                                transaction_type=TransactionType.DEPOSIT.value,
                                amount=value_fiat,  # Enhanced: Use Decimal directly
                                currency=proper_currency,
                                blockchain_address=txid_in,
                                status="completed",
                                description=f"🛡️ Trade Payment: ${value_fiat:.2f} held in escrow #{escrow.escrow_id}",
                                confirmed_at=datetime.utcnow(),
                            )
                            session.add(transaction)
                            logger.info(f"Created transaction record for escrow {escrow.escrow_id} with currency {proper_currency}")
                        else:
                            logger.info(f"Transaction already exists for blockchain address {txid_in}, skipping duplicate creation")
                    
                    session.commit()

                    # Send notifications for both escrow and exchange orders
                    if is_escrow and escrow:
                        # NOTIFICATION SERVICE INTEGRATION: Send seller notification using decoupled service
                        try:
                            from services.notification_service import notification_service
                            
                            # Send seller notification via decoupled service
                            # FIXED: Handle all seller types (username, email, phone)
                            seller_email = getattr(escrow, 'seller_email', None)
                            seller_phone = getattr(escrow, 'seller_phone', None)
                            seller_username = getattr(escrow, 'seller_username', None)
                            
                            # Determine seller type and identifier
                            if seller_email:
                                seller_identifier = seller_email
                                seller_type = 'email'
                            elif seller_phone:
                                seller_identifier = seller_phone
                                seller_type = 'phone'
                            elif seller_username:
                                seller_identifier = seller_username
                                seller_type = 'username'
                            else:
                                logger.error(f"No seller contact info found for escrow {escrow.escrow_id}")
                                # Skip notification if no seller info - but don't break the process
                                logger.info(f"Skipping seller notification for escrow {escrow.escrow_id} - no contact info")
                                seller_identifier = None
                                seller_type = None
                            
                            # Only send notification if we have seller info
                            if seller_identifier and seller_type:
                                await notification_service.send_seller_invitation(
                                    escrow_id=str(escrow.escrow_id),  # FIXED: Convert to string
                                    seller_identifier=seller_identifier,
                                    seller_type=seller_type,
                                    amount=float(getattr(escrow, 'total_amount', 0) or 0)  # FIXED: Safe attribute access
                                )
                            logger.info(f"Seller notification sent for escrow {escrow.escrow_id}")
                        except Exception as e:
                            logger.error(f"CRITICAL: Seller notification failed for escrow {escrow.escrow_id}: {e}")
                            # Implement notification retry logic
                            await self._retry_seller_notification(escrow, e)
                        
                        # Send buyer payment confirmation notification
                        try:
                            await self._send_escrow_buyer_confirmation(escrow, txid_in, value_fiat)
                            logger.info(f"Buyer payment confirmation sent for escrow {escrow.escrow_id}")
                        except Exception as e:
                            logger.error(f"CRITICAL: Buyer confirmation failed for escrow {escrow.escrow_id}: {e}")
                            # Implement notification retry logic
                            await self._retry_buyer_confirmation(escrow, e)
                    
                    elif not is_escrow and exchange_order:
                        # 1. Send immediate payment confirmation notification
                        try:
                            await self._send_exchange_payment_confirmation(exchange_order, txid_in, value_fiat)
                            logger.info(f"Exchange payment confirmation sent for order {exchange_order.id}")
                        except Exception as e:
                            logger.error(f"CRITICAL: Exchange confirmation failed for order {exchange_order.id}: {e}")
                            # Implement notification retry logic
                            await self._retry_exchange_confirmation(exchange_order, e)

                    if is_escrow and escrow:
                        logger.info(f"Successfully activated escrow {escrow.escrow_id} from crypto payment")
                        
                        # CRITICAL FIX: Send notifications for unified path (which skips legacy notification block)
                        if unified_processing_completed:
                            try:
                                session.commit()
                                logger.info(f"📋 UNIFIED_BLOCKBEE_NOTIFICATION: Sending notifications for {escrow.escrow_id}")
                                
                                from services.notification_service import notification_service
                                seller_email = getattr(escrow, 'seller_email', None)
                                seller_phone = getattr(escrow, 'seller_phone', None)
                                seller_username = getattr(escrow, 'seller_username', None)
                                
                                if seller_email:
                                    seller_identifier = seller_email
                                    seller_type = 'email'
                                elif seller_phone:
                                    seller_identifier = seller_phone
                                    seller_type = 'phone'
                                elif seller_username:
                                    seller_identifier = seller_username
                                    seller_type = 'username'
                                else:
                                    seller_identifier = None
                                    seller_type = None
                                
                                if seller_identifier and seller_type:
                                    await notification_service.send_seller_invitation(
                                        escrow_id=str(escrow.escrow_id),
                                        seller_identifier=seller_identifier,
                                        seller_type=seller_type,
                                        amount=float(getattr(escrow, 'total_amount', 0) or 0)
                                    )
                                    logger.info(f"✅ UNIFIED_BLOCKBEE: Seller notification sent for {escrow.escrow_id}")
                                
                                await self._send_escrow_buyer_confirmation(escrow, txid_in, value_fiat)
                                logger.info(f"✅ UNIFIED_BLOCKBEE: Buyer confirmation sent for {escrow.escrow_id}")
                            except Exception as notif_err:
                                logger.error(f"❌ UNIFIED_BLOCKBEE: Notification error for {escrow.escrow_id}: {notif_err}")
                    elif not is_escrow and exchange_order:
                        logger.info(f"Successfully processed exchange order {exchange_order.id} from crypto payment")
                else:
                    if is_escrow and escrow:
                        logger.info(f"Payment for escrow {escrow.escrow_id} pending confirmation: {confirmations} confirmations")
                    elif not is_escrow and exchange_order:
                        logger.info(f"Payment for exchange order {exchange_order.id} pending confirmation: {confirmations} confirmations")

                return True

        except Exception as e:
            logger.error(f"Error processing BlockBee callback: {e}")
            return False
    
    async def _retry_seller_notification(self, escrow, error):
        """Retry seller notification with exponential backoff"""
        import asyncio
        max_retries = 3
        for attempt in range(max_retries):
            try:
                await asyncio.sleep(2 ** attempt)  # Exponential backoff
                from services.notification_service import notification_service
                # FIXED: Handle all seller types in retry logic
                seller_email = getattr(escrow, 'seller_email', None)
                seller_phone = getattr(escrow, 'seller_phone', None)
                seller_username = getattr(escrow, 'seller_username', None)
                
                # Determine seller type and identifier
                if seller_email:
                    seller_identifier = seller_email
                    seller_type = 'email'
                elif seller_phone:
                    seller_identifier = seller_phone
                    seller_type = 'phone'
                elif seller_username:
                    seller_identifier = seller_username
                    seller_type = 'username'
                else:
                    logger.error(f"No seller contact info found for escrow {escrow.escrow_id} during retry")
                    return False
                
                await notification_service.send_seller_invitation(
                    escrow_id=str(escrow.escrow_id),
                    seller_identifier=seller_identifier,
                    seller_type=seller_type,
                    amount=float(getattr(escrow, 'total_amount', 0) or 0)
                )
                logger.info(f"Seller notification retry successful for escrow {escrow.escrow_id} (attempt {attempt + 1})")
                return True
            except Exception as retry_error:
                logger.warning(f"Seller notification retry {attempt + 1} failed for escrow {escrow.escrow_id}: {retry_error}")
        logger.error(f"All seller notification retries failed for escrow {escrow.escrow_id}")
        return False
    
    async def _retry_buyer_confirmation(self, escrow, error):
        """Retry buyer confirmation with exponential backoff"""
        import asyncio
        max_retries = 3
        for attempt in range(max_retries):
            try:
                await asyncio.sleep(2 ** attempt)
                await self._send_escrow_buyer_confirmation(escrow, "retry", 0)
                logger.info(f"Buyer confirmation retry successful for escrow {escrow.escrow_id} (attempt {attempt + 1})")
                return True
            except Exception as retry_error:
                logger.warning(f"Buyer confirmation retry {attempt + 1} failed for escrow {escrow.escrow_id}: {retry_error}")
        logger.error(f"All buyer confirmation retries failed for escrow {escrow.escrow_id}")
        return False
    
    async def _retry_exchange_confirmation(self, exchange_order, error):
        """Retry exchange confirmation with exponential backoff"""
        import asyncio
        max_retries = 3
        for attempt in range(max_retries):
            try:
                await asyncio.sleep(2 ** attempt)
                await self._send_exchange_payment_confirmation(exchange_order, "retry", 0)
                logger.info(f"Exchange confirmation retry successful for order {exchange_order.id} (attempt {attempt + 1})")
                return True
            except Exception as retry_error:
                logger.warning(f"Exchange confirmation retry {attempt + 1} failed for order {exchange_order.id}: {retry_error}")
        logger.error(f"All exchange confirmation retries failed for order {exchange_order.id}")
        return False
    
    async def _process_wallet_deposit_callback(self, wallet_txn_id: str, callback_data: Dict[str, Any], session) -> bool:
        """Process wallet deposit confirmation with enhanced double-crediting protection"""
        try:
            # Extract Telegram ID from wallet transaction ID format: WALLET-YYYYMMDD-HHMMSS-{telegram_id}
            try:
                telegram_id_str = wallet_txn_id.split('-')[-1]
                # CRITICAL FIX: Convert to int to match bigint database column
                telegram_id = int(telegram_id_str)
            except (ValueError, IndexError) as e:
                logger.error(f"Invalid wallet transaction ID format or invalid telegram_id: {wallet_txn_id}, error: {e}")
                return False
            
            # CRITICAL FIX: Look up database user ID from Telegram ID
            from models import User
            stmt = select(User).where(User.telegram_id == telegram_id)
            result = session.execute(stmt)
            user = result.scalar_one_or_none()
            
            if not user:
                logger.error(f"❌ User not found for Telegram ID {telegram_id} from wallet transaction {wallet_txn_id}")
                return False
            
            user_id = user.id
            logger.info(f"✅ BLOCKBEE: Mapped Telegram ID {telegram_id} → Database User ID {user_id} for wallet deposit")
            
            # Extract payment details
            txid_in = callback_data.get("txid_in")
            value_coin = Decimal(str(callback_data.get("value_coin", 0)))
            price_usd = Decimal(str(callback_data.get("price", 0)))
            
            # Calculate USD value if not provided by BlockBee (same logic as escrow)
            value_fiat = Decimal(str(callback_data.get("value_fiat", 0)))
            if value_fiat == 0 and value_coin > 0 and price_usd > 0:
                value_fiat = value_coin * price_usd
                logger.info(f"Calculated wallet deposit USD value: {value_coin} * {price_usd} = ${value_fiat:.2f}")
            
            currency = callback_data.get("coin", "UNKNOWN").upper()
            confirmations = int(callback_data.get("confirmations", 0))
            confirmed = callback_data.get("confirmed", False)
            
            logger.info(
                f"Processing wallet deposit for user {user_id}: "
                f"Amount: {value_fiat} USD ({value_coin} {currency}), "
                f"Confirmations: {confirmations}, Confirmed: {confirmed}"
            )
            
            # CRITICAL FIX: Check confirmation status BEFORE acquiring distributed lock
            # Only proceed if payment is confirmed by BlockBee (not just confirmations count)
            if not confirmed:
                logger.info(f"🏦 WALLET_DEPOSIT_DEFERRED: Payment not yet confirmed by BlockBee. Confirmations: {confirmations}, Confirmed: {confirmed}")
                # Return False to keep webhook status as processing, allowing reprocessing when confirmed
                return False
            
            logger.info(f"🏦 CONFIRMED: Payment confirmed by BlockBee with {confirmations} confirmations, proceeding with crediting")
            
            # ARCHITECTURE FIX: Remove nested distributed lock - outer lock already protects this flow
            # Process wallet deposit directly since we're already inside the distributed lock context
            logger.info("🏦 DIRECT_CALL: Processing wallet deposit directly (no nested lock needed)")
            logger.info(f"🏦 DIRECT_PARAMS: wallet_txn_id={wallet_txn_id}, txid_in={txid_in}, value_fiat={value_fiat}, value_coin={value_coin}, currency={currency}, user_id={user_id}")
            
            result = await self._process_locked_wallet_deposit(
                wallet_txn_id, txid_in, value_fiat, value_coin, currency, user_id, session
            )
            
            logger.info(f"🏦 DIRECT_RESULT: _process_locked_wallet_deposit returned: {result}")
            return result
                
        except Exception as e:
            logger.error(f"🚨 WALLET_DEPOSIT_ERROR: Failed to process locked wallet deposit: {e}", exc_info=True)
            return False

    async def _process_locked_wallet_deposit(
        self, 
        wallet_txn_id: str, 
        txid_in: str, 
        value_fiat: Decimal, 
        value_coin: Decimal,
        currency: str, 
        user_id: int,
        session
    ) -> bool:
        """Process wallet deposit within distributed lock context"""
        try:
            logger.info("🏦 WALLET_DEPOSIT: Starting locked wallet deposit processing")
            
            # CRITICAL FIX: Check for duplicate processing INSIDE the lock
            from models import Transaction, TransactionType
            from utils.atomic_transactions import atomic_transaction
            from datetime import datetime
            
            logger.info("🏦 WALLET_DEPOSIT: About to create atomic transaction")
            
            with atomic_transaction(session) as tx_session:
                # Check for existing transaction within atomic transaction
                existing_transaction = (
                    tx_session.query(Transaction)
                    .filter(
                        Transaction.blockchain_address == txid_in,
                        Transaction.user_id == user_id,
                        Transaction.transaction_type == TransactionType.WALLET_DEPOSIT.value,
                    )
                    .with_for_update()  # Row-level lock to prevent concurrent checks
                    .first()
                )
                
                if existing_transaction:
                    logger.info(f"DUPLICATE_PREVENTED: Wallet deposit already processed: {txid_in}")
                    return True
                
                # CRITICAL: For wallet deposits, apply 2% markup calculation
                if value_fiat <= 0:
                    logger.warning(f"Invalid wallet deposit amount: ${value_fiat}")
                    return False
                
                # Create transaction record FIRST to claim this payment
                from utils.universal_id_generator import UniversalIDGenerator
                
                transaction_utid = UniversalIDGenerator.generate_transaction_id()  # TX = Transaction records
                transaction = Transaction(
                    transaction_id=UniversalIDGenerator.generate_transaction_id(),
                    utid=transaction_utid,
                    user_id=user_id,
                    transaction_type=TransactionType.WALLET_DEPOSIT.value,
                    amount=value_fiat,
                    currency=currency,
                    blockchain_address=txid_in,
                    status="processing",  # Mark as processing first
                    description=f"Wallet deposit: {value_fiat} USD from {currency}",
                    # CRITICAL FIX: Explicitly set escrow fields to None for wallet deposits
                    escrow_pk=None,  # Not associated with any escrow
                    escrow_id=None,  # Not associated with any escrow
                )
                tx_session.add(transaction)
                tx_session.flush()  # Ensure transaction is persisted to claim the payment
                
                # Now process the actual wallet credit with markup calculation
                from services.crypto import CryptoServiceAtomic
                credit_success = await CryptoServiceAtomic.process_wallet_deposit_confirmation(
                    wallet_txn_id=wallet_txn_id,
                    tx_hash=txid_in,
                    received_amount_usd=float(value_fiat),
                    currency=currency
                )
            
                if not credit_success:
                    # Update transaction status to failed
                    transaction.status = "failed"
                    transaction.error_message = "Wallet credit failed"
                    tx_session.commit()
                    logger.error(f"Failed to credit wallet for user {user_id}")
                    return False
                
                # Update transaction status to completed
                transaction.status = "completed"
                transaction.confirmed_at = datetime.utcnow()
                tx_session.commit()
                
                logger.info("Wallet deposit processed successfully with 2% markup calculation")
                
                # Send wallet-specific confirmation notification
                try:
                    from services.wallet_notification_service import wallet_notification_service
                    notification_sent = await wallet_notification_service.send_crypto_deposit_confirmation(
                        user_id=user_id,
                        amount_crypto=value_coin,
                        currency=currency,
                        amount_usd=value_fiat,
                        txid_in=txid_in
                    )
                    
                    if notification_sent:
                        logger.info(f"✅ Wallet deposit confirmation sent to user {user_id}")
                    else:
                        logger.warning(f"⚠️ Failed to send wallet deposit confirmation to user {user_id}")
                        
                    # BACKUP: If notification service fails, send simple Telegram message
                    if not notification_sent:
                        try:
                            from main import application
                            if application and application.bot:
                                from models import User
                                user_obj = tx_session.query(User).filter(User.id == user_id).first()
                                if user_obj and user_obj.telegram_id:
                                    await application.bot.send_message(
                                        chat_id=user_obj.telegram_id,
                                        text=f"✅ Deposit Confirmed!\n\n💰 ${value_fiat:.2f} USD added to your wallet\n🪙 From: {value_coin} {currency}\n\nYour balance has been updated!"
                                    )
                                    logger.info(f"✅ Backup Telegram notification sent to user {user_id}")
                        except Exception as backup_error:
                            logger.error(f"❌ Backup notification also failed for user {user_id}: {backup_error}")
                    
                    logger.info(f"✅ Wallet deposit notification process completed for user {user_id}")
                except Exception as e:
                    logger.warning(f"Failed to send wallet deposit confirmation: {e}")
                
                logger.info(f"✅ Wallet deposit processed successfully: User {user_id} credited ${value_fiat:.2f}")
                return True
                
        except Exception as e:
            logger.error(f"Error processing locked wallet deposit: {e}", exc_info=True)
            return False

    async def _send_escrow_buyer_confirmation(self, escrow, txid_in: str, value_fiat):
        """Send payment confirmation notification to escrow buyer"""
        try:
            from telegram import Bot
            from config import Config
            from models import User
            from utils.atomic_transactions import atomic_transaction
            
            if not Config.BOT_TOKEN:
                logger.warning("Bot token not configured for buyer notifications")
                return
            
            # PRODUCTION FIX: Use shorter database connection to prevent timeout warnings
            # Get buyer information with optimized query
            with atomic_transaction() as session:
                buyer = session.query(User).filter(User.id == escrow.buyer_id).first()
                if not buyer or not buyer.telegram_id:
                    logger.warning(f"No buyer telegram ID found for escrow {escrow.escrow_id}")
                    return
                
                # Calculate actual escrow amount (total received minus platform fee)
                from utils.fee_calculator import FeeCalculator
                FeeCalculator()
                
                # Get platform fee percentage and calculate net escrow amount
                platform_fee_percentage = FeeCalculator.get_platform_fee_percentage()
                platform_fee_amount = float(value_fiat) * (platform_fee_percentage / 100)
                net_escrow_amount = float(value_fiat) - platform_fee_amount
                
                logger.info(
                    f"Escrow notification amounts: Total received ${value_fiat:.2f}, "
                    f"Platform fee ${platform_fee_amount:.2f} ({platform_fee_percentage}%), "
                    f"Net escrow ${net_escrow_amount:.2f}"
                )
                
                # Create mobile-friendly escrow confirmation message with NET amount
                message = (
                    f"✅ Deposit Received • Trade {escrow.escrow_id}\n\n"
                    f"${net_escrow_amount:.2f} confirmed in escrow\n"
                    f"TX: {txid_in[:8]}...{txid_in[-4:]}\n\n"
                    f"🔔 Seller notified • 24hr to accept"
                )
                
                # Send via bot
                bot = Bot(Config.BOT_TOKEN)
                await bot.send_message(
                    chat_id=int(buyer.telegram_id),
                    text=message,
                    parse_mode='HTML'
                )
                
                # ANTI-DUPLICATE FIX: Only send email for FIRST payment confirmation, not retries
                # Check if payment was just confirmed (within last 10 seconds)
                from datetime import datetime, timezone
                is_first_payment = False
                if escrow.payment_confirmed_at:
                    time_since_confirmation = (datetime.now(timezone.utc) - escrow.payment_confirmed_at.replace(tzinfo=timezone.utc)).total_seconds()
                    is_first_payment = time_since_confirmation < 10  # Just confirmed within last 10 seconds
                
                if is_first_payment:
                    # Send email notification for escrow payment confirmation (first time only)
                    await self._send_escrow_payment_email(escrow, buyer, net_escrow_amount, txid_in)
                else:
                    logger.info(f"⏭️ SKIP_DUPLICATE_EMAIL: Escrow {escrow.escrow_id} payment email already sent (retry payment detected)")
                
        except Exception as e:
            logger.error(f"Error sending escrow buyer confirmation: {e}")
            raise

    async def _send_escrow_payment_email(self, escrow, buyer_user, escrow_amount, txid_in):
        """Send email notification for escrow payment confirmation"""
        try:
            from utils.preferences import is_enabled
            
            # Check if user has email notifications enabled for escrows
            if not is_enabled(buyer_user, 'escrow_updates', 'email'):
                logger.info(f"Email notifications disabled for user {buyer_user.id} - skipping escrow payment confirmation email")
                return
                
            if not buyer_user.email:
                logger.info(f"No email available for user {buyer_user.id} - skipping escrow payment confirmation email")
                return
            
            # Prepare notification details
            notification_details = {
                "escrow_id": escrow.escrow_id,
                "escrow_amount": float(escrow_amount),
                "transaction_hash": txid_in,
                "seller_info": getattr(escrow, 'seller_username', None) or getattr(escrow, 'seller_email', None) or getattr(escrow, 'seller_phone', None) or "Unknown",
            }
            
            # Send email notification
            from services.email import EmailService
            email_service = EmailService()
            
            success = await email_service.send_escrow_notification(
                user_email=buyer_user.email,
                user_name=buyer_user.username or buyer_user.first_name or "User",
                escrow_id=escrow.escrow_id,
                notification_type="payment_confirmed",
                details=notification_details
            )
            
            if success:
                logger.info(f"✅ Escrow payment confirmation email sent to {buyer_user.email}")
            else:
                logger.warning(f"❌ Failed to send escrow payment confirmation email to {buyer_user.email}")
                
        except Exception as e:
            logger.error(f"Error sending escrow payment confirmation email: {e}")

    async def _send_exchange_payment_confirmation(self, exchange_order, txid_in: str, value_fiat):
        """Send IMMEDIATE payment confirmation notification to exchange user"""
        try:
            from telegram import Bot
            from config import Config
            from models import User
            from utils.atomic_transactions import atomic_transaction
            
            if not Config.BOT_TOKEN:
                logger.warning("Bot token not configured for exchange notifications")
                return
            
            # Get user information
            with atomic_transaction() as session:
                user = session.query(User).filter(User.id == exchange_order.user_id).first()
                if not user or not user.telegram_id:
                    logger.warning(f"No user telegram ID found for exchange order {exchange_order.id}")
                    return
                
                # Get exchange details with proper attribute access
                source_crypto = exchange_order.source_currency if hasattr(exchange_order, 'source_currency') else 'CRYPTO'
                target_currency = exchange_order.target_currency if hasattr(exchange_order, 'target_currency') else 'NGN'
                final_amount = exchange_order.final_amount if hasattr(exchange_order, 'final_amount') else 0
                
                logger.info(f"Preparing exchange notification for user {user.id}: {source_crypto} -> {target_currency}")
                
                # Create consistent payment confirmation message for both buy/sell crypto
                if source_crypto == 'NGN' and target_currency != 'NGN':
                    # Buy Crypto (NGN→crypto)
                    message = (
                        f"✅ NGN Received • Order #{getattr(exchange_order, 'utid', f'EX{exchange_order.id}')}\n\n"
                        f"₦{getattr(exchange_order, 'source_amount', 0):,.0f} confirmed\n"
                        f"Sending {final_amount} {target_currency}\n"
                        f"TX: {txid_in[:8]}...{txid_in[-4:]}\n\n"
                        f"⏱ Arriving: 2-10 min"
                    )
                elif target_currency == 'NGN':
                    # Sell Crypto (crypto→NGN)
                    message = (
                        f"✅ {source_crypto} Received • Order #{getattr(exchange_order, 'utid', f'EX{exchange_order.id}')}\n\n"
                        f"Sending ₦{final_amount:,.0f} to your bank\n"
                        f"TX: {txid_in[:8]}...{txid_in[-4:]}\n\n"
                        f"⏱ Arriving: 2-10 min"
                    )
                else:
                    # Fallback for any unexpected exchange types
                    message = (
                        f"✅ Payment Received • Order #{getattr(exchange_order, 'utid', f'EX{exchange_order.id}')}\n\n"
                        f"{source_crypto} confirmed • ${value_fiat:.2f}\n"
                        f"TX: {txid_in[:8]}...{txid_in[-4:]}\n\n"
                        f"⏱ Processing: 2-10 min"
                    )
                
                # Send via bot with proper error handling
                bot = Bot(Config.BOT_TOKEN)
                logger.info(f"Sending exchange completion notification to user {user.id} (telegram_id: {user.telegram_id})")
                
                await bot.send_message(
                    chat_id=int(user.telegram_id),
                    text=message,
                    parse_mode='Markdown'
                )
                
                logger.info(f"✅ Exchange payment confirmation sent successfully to user {user.id}")
                
                # Send email notification for payment confirmation
                await self._send_exchange_payment_email(exchange_order, user, source_crypto, target_currency, final_amount, txid_in)
                
        except Exception as e:
            logger.error(f"Error sending exchange payment confirmation: {e}")
            # Don't raise - this is non-critical for exchange processing
            logger.warning("Exchange payment confirmation failed but order processing continues")
            
    async def _send_exchange_payment_email(self, exchange_order, user, source_crypto, target_currency, final_amount, txid_in):
        """Send email notification for exchange payment confirmation"""
        try:
            from utils.preferences import is_enabled
            
            # Check if user has email notifications enabled for exchanges
            if not is_enabled(user, 'exchanges', 'email'):
                logger.info(f"Email notifications disabled for user {user.id} - skipping payment confirmation email")
                return
                
            if not user.email:
                logger.info(f"No email available for user {user.id} - skipping payment confirmation email")
                return
            
            # Prepare notification details
            notification_details = {
                "order_id": exchange_order.id,
                "source_currency": source_crypto,
                "target_currency": target_currency,
                "final_amount": float(final_amount),
                "transaction_hash": txid_in,
            }
            
            # Determine notification type based on exchange direction
            if source_crypto == 'NGN':
                notification_type = "payment_confirmed"  # NGN payment confirmed
            else:
                notification_type = "deposit_confirmed"  # Crypto deposit confirmed
            
            # Send email notification
            from services.email import EmailService
            email_service = EmailService()
            
            success = await email_service.send_exchange_notification(
                user_email=user.email,
                user_name=user.username or user.first_name or "User",
                order_id=getattr(exchange_order, 'utid', f'EX{exchange_order.id}'),
                notification_type=notification_type,
                details=notification_details
            )
            
            if success:
                logger.info(f"✅ Exchange payment confirmation email sent to {user.email}")
            else:
                logger.warning(f"❌ Failed to send payment confirmation email to {user.email}")
                
        except Exception as e:
            logger.error(f"Error sending exchange payment confirmation email: {e}")
            
    async def _send_exchange_completion_email(self, exchange_order, user, bank_reference):
        """Send email notification for exchange completion"""
        try:
            from utils.preferences import is_enabled
            
            # Check if user has email notifications enabled for exchanges
            if not is_enabled(user, 'exchanges', 'email'):
                logger.info(f"Email notifications disabled for user {user.id} - skipping completion email")
                return
                
            if not user.email:
                logger.info(f"No email available for user {user.id} - skipping completion email")
                return
            
            # Prepare notification details
            notification_details = {
                "order_id": exchange_order.id,
                "source_currency": getattr(exchange_order, 'source_currency', 'CRYPTO'),
                "target_currency": getattr(exchange_order, 'target_currency', 'NGN'),
                "final_amount": float(getattr(exchange_order, 'final_amount', 0)),
                "bank_reference": bank_reference,
            }
            
            # Send email notification
            from services.email import EmailService
            email_service = EmailService()
            
            success = await email_service.send_exchange_notification(
                user_email=user.email,
                user_name=user.username or user.first_name or "User",
                order_id=getattr(exchange_order, 'utid', f'EX{exchange_order.id}'),
                notification_type="exchange_completed",
                details=notification_details
            )
            
            if success:
                logger.info(f"✅ Exchange completion email sent to {user.email}")
            else:
                logger.warning(f"❌ Failed to send completion email to {user.email}")
                
        except Exception as e:
            logger.error(f"Error sending exchange completion email: {e}")
            
    async def _send_wallet_deposit_confirmation(self, user_id: int, amount_crypto: Decimal, currency: str, amount_usd: Decimal, txid_in: str):
        """Send wallet-specific deposit confirmation notification"""
        try:
            from telegram import Bot
            from config import Config
            from models import User
            from utils.atomic_transactions import atomic_transaction
            
            if not Config.BOT_TOKEN:
                logger.warning("Bot token not configured for wallet notifications")
                return
            
            # Get user information
            with atomic_transaction() as session:
                user = session.query(User).filter(User.id == user_id).first()
                if not user or not user.telegram_id:
                    logger.warning(f"No telegram ID found for user {user_id}")
                    return
                
                # Create wallet-specific confirmation message (clean, no overpayment mention)
                message = (
                    f"💰 Wallet: +${amount_usd:.2f}\n\n"
                    f"{amount_crypto} {currency} deposited\n"
                    f"TX: {txid_in[:8]}...{txid_in[-4:]}\n\n"
                    f"/wallet to view"
                )
                
                # Send via bot
                bot = Bot(Config.BOT_TOKEN)
                await bot.send_message(
                    chat_id=int(user.telegram_id),
                    text=message,
                    parse_mode='Markdown'
                )
                
        except Exception as e:
            logger.error(f"Error sending wallet deposit confirmation: {e}")
    
    # Removed: Bank transfer initiation notification to reduce message count
    
    async def _send_bank_transfer_completion_notification(self, exchange_order, bank_reference):
        """Send notification when bank transfer is completed - SAFE TRANSACTION PATTERN"""
        try:
            from telegram import Bot
            from config import Config
            from models import User
            from utils.atomic_transactions import atomic_transaction
            
            if not Config.BOT_TOKEN:
                logger.warning("Bot token not configured for bank transfer completion notifications")
                return
            
            # SAFE PATTERN: First, get user data in separate, quick transaction
            user_data = None
            with atomic_transaction() as session:
                user = session.query(User).filter(User.id == exchange_order.user_id).first()
                if not user:
                    logger.warning(f"User {exchange_order.user_id} not found for bank transfer completion notification")
                    return
                
                # Extract all needed user data while in transaction
                user_data = {
                    'telegram_id': user.telegram_id,
                    'user_id': user.id
                }
            # Transaction committed here - database lock released
            
            if not user_data['telegram_id']:
                logger.warning(f"No telegram ID found for user {exchange_order.user_id}")
                return
            
            # SAFE PATTERN: Now send notification OUTSIDE transaction context
            try:
                # Get order details (these are already loaded in memory)
                final_amount = getattr(exchange_order, 'final_amount', 0)
                getattr(exchange_order, 'source_currency', 'CRYPTO')
                
                # Create consistent final completion message for Sell Crypto (crypto→NGN)
                message = (
                    f"✅ Exchange Complete!\n\n"
                    f"Order #{getattr(exchange_order, 'utid', f'EX{exchange_order.id}')} • SUCCESS\n"
                    f"₦{final_amount:,.2f} → Your Bank\n"
                    f"🏦 Reference: `{bank_reference}`\n\n"
                    f"🎉 All done! Check your bank account"
                )
                
                # Send via bot
                bot = Bot(Config.BOT_TOKEN)
                await bot.send_message(
                    chat_id=int(user_data['telegram_id']),
                    text=message,
                    parse_mode='Markdown'
                )
                
                logger.info(f"✅ Bank transfer completion notification sent to user {user_data['user_id']}")
                
            except Exception as telegram_error:
                logger.error(f"❌ Failed to send bank transfer completion notification to user {user_data['user_id']}: {telegram_error}")
                
        except Exception as e:
            logger.error(f"Error sending bank transfer completion notification: {e}")
            raise

    async def estimate_network_fee(
        self, currency: str, addresses: int = 1
    ) -> Dict[str, Any]:
        """Estimate network fees for a transaction"""
        ticker = self._map_currency_to_blockbee(currency)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.base_url}/{ticker}/estimate/",
                    headers=self._get_headers(),
                    params={"addresses": addresses},
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        logger.info(f"Estimated network fee for {currency}: {data}")
                        return data
                    else:
                        error_text = await response.text()
                        logger.error(
                            f"BlockBee fee estimation error: {response.status} - {error_text}"
                        )
                        raise BlockBeeAPIError(
                            f"Fee estimation error: {response.status}"
                        )
        except aiohttp.ClientError as e:
            logger.error(f"Network error estimating fees for {currency}: {e}")
            raise BlockBeeAPIError(f"Network error: {e}")
    
    async def _credit_wallet_for_cancelled_crypto_payment(
        self, session, exchange_order, usd_amount: Decimal, txid_in: str, callback_data: dict
    ) -> bool:
        """Credit USD wallet for cancelled exchange order crypto payment"""
        try:
            from models import User, Transaction, TransactionType
            import uuid
            from datetime import datetime
            
            user_id = getattr(exchange_order, 'user_id')
            order_id = getattr(exchange_order, 'id')
            
            # Get user
            user = session.query(User).filter(User.id == user_id).first()
            if not user:
                logger.error(f"User {user_id} not found for cancelled order payment")
                return False
            
            # Credit user's USD wallet
            current_balance = Decimal(str(getattr(user, 'balance', 0)))
            new_balance = current_balance + usd_amount
            
            setattr(user, 'balance', float(new_balance))
            
            # Create transaction record
            crypto_currency = callback_data.get("coin", "CRYPTO").upper()
            transaction = Transaction(
                transaction_id=str(uuid.uuid4())[:12].upper(),
                user_id=user_id,
                transaction_type=TransactionType.DEPOSIT.value,
                amount=float(usd_amount),
                balance_after=float(new_balance),
                description=f"Refund for cancelled exchange order #{order_id} - {crypto_currency} payment received after cancellation",
                blockchain_address=txid_in,
                created_at=datetime.utcnow()
            )
            
            session.add(transaction)
            session.commit()
            
            logger.info(
                f"SECURITY: Credited ${usd_amount:.2f} USD to user {user_id} wallet "
                f"for cancelled exchange order {order_id} ({crypto_currency} payment)"
            )
            
            return True
            
        except Exception as e:
            logger.error(f"Error crediting wallet for cancelled exchange order payment: {e}")
            session.rollback()
            return False
    
    async def _credit_wallet_for_cancelled_escrow_payment(
        self, session, escrow, usd_amount: Decimal, txid_in: str, callback_data: dict
    ) -> bool:
        """Credit USD wallet for cancelled escrow crypto payment"""
        try:
            from models import User, Transaction, TransactionType
            import uuid
            from datetime import datetime
            
            user_id = getattr(escrow, 'buyer_id')  # Buyer pays for escrow
            escrow_id = getattr(escrow, 'escrow_id')
            
            # Get user
            user = session.query(User).filter(User.id == user_id).first()
            if not user:
                logger.error(f"User {user_id} not found for cancelled escrow payment")
                return False
            
            # Credit user's USD wallet
            current_balance = Decimal(str(getattr(user, 'balance', 0)))
            new_balance = current_balance + usd_amount
            
            setattr(user, 'balance', float(new_balance))
            
            # Create transaction record
            crypto_currency = callback_data.get("coin", "CRYPTO").upper()
            transaction = Transaction(
                transaction_id=str(uuid.uuid4())[:12].upper(),
                user_id=user_id,
                transaction_type=TransactionType.DEPOSIT.value,
                amount=float(usd_amount),
                balance_after=float(new_balance),
                description=f"Refund for cancelled escrow #{escrow_id} - {crypto_currency} payment received after cancellation",
                blockchain_address=txid_in,
                created_at=datetime.utcnow()
            )
            
            session.add(transaction)
            session.commit()
            
            logger.info(
                f"SECURITY: Credited ${usd_amount:.2f} USD to user {user_id} wallet "
                f"for cancelled escrow {escrow_id} ({crypto_currency} payment)"
            )
            
            return True
            
        except Exception as e:
            logger.error(f"Error crediting wallet for cancelled escrow payment: {e}")
            session.rollback()
            return False


# Global instance
blockbee_service = BlockBeeService()
