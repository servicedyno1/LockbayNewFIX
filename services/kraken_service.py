"""Kraken API integration for crypto withdrawals"""

import os
import hmac
import hashlib
import base64
import time
import urllib.parse
import json
import aiohttp
import asyncio
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
from services.api_adapter_retry import APIAdapterRetry
from models import CashoutErrorCode
from services.financial_operation_protection import require_balance_protection

logger = logging.getLogger(__name__)

# Kraken API Circuit Breaker
# Prevents cascading failures when Kraken API is unreachable
_kraken_circuit_open = False
_kraken_failure_count = 0
_kraken_circuit_opened_at = 0.0
_KRAKEN_FAILURE_THRESHOLD = 5          # Open after 5 consecutive failures
_KRAKEN_CIRCUIT_COOLDOWN = 300         # 5 minutes cooldown


class KrakenService(APIAdapterRetry):
    """Kraken API client for crypto withdrawals with unified retry system"""
    
    def __init__(self):
        # Initialize parent APIAdapterRetry
        super().__init__(service_name="kraken", timeout=60)
        
        self.api_key = os.getenv('KRAKEN_API_KEY')
        self.secret_key = os.getenv('KRAKEN_PRIVATE_KEY')
        self.base_url = 'https://api.kraken.com'
        
        # Cache for withdrawal info to prevent redundant API calls
        self._withdrawal_info_cache = {}
        self._cache_expiry = 30  # Cache for 30 seconds
        
        # CRITICAL FIX: Shared balance cache for cross-operation reuse
        self._balance_cache = None
        self._balance_cache_timestamp = None
        self._balance_cache_expiry_seconds = 45  # 45-second cache for operation-scoped reuse
        self._balance_cache_lock = asyncio.Lock()  # Thread-safe cache access
        
        # CRITICAL FIX: Thread-safe nonce management to prevent API collisions
        self._last_nonce = 0
        self._nonce_lock = asyncio.Lock()
        
        if not self.api_key or not self.secret_key:
            raise ValueError("Kraken API credentials not found in environment variables")
        
        logger.info("🦑 Kraken service initialized")
    
    def _map_provider_error_to_unified(self, exception: Exception, context: Optional[Dict] = None) -> CashoutErrorCode:
        """
        Map Kraken-specific errors to unified error codes for intelligent retry
        """
        error_message = str(exception).lower()
        
        # Kraken-specific error patterns
        if "eapi:invalid key" in error_message or "unknown withdraw key" in error_message:
            return CashoutErrorCode.KRAKEN_ADDR_NOT_FOUND  # Keep specific Kraken error for detailed tracking
        elif "eapi:invalid nonce" in error_message or "nonce" in error_message:
            return CashoutErrorCode.API_TIMEOUT  # Nonce issues are often timing related
        elif "kraken timeout" in error_message or "kraken timed out" in error_message:
            return CashoutErrorCode.API_TIMEOUT
        elif "kraken api error" in error_message:
            return CashoutErrorCode.KRAKEN_API_ERROR  # Keep specific Kraken error for detailed tracking
        elif "insufficient funds" in error_message or "balance too low" in error_message:
            return CashoutErrorCode.API_INSUFFICIENT_FUNDS
        elif "invalid address" in error_message or "address format" in error_message:
            return CashoutErrorCode.API_INVALID_REQUEST
        elif "service unavailable" in error_message or "502" in error_message or "503" in error_message:
            return CashoutErrorCode.SERVICE_UNAVAILABLE
        elif "rate limit" in error_message or "too many requests" in error_message:
            return CashoutErrorCode.RATE_LIMIT_EXCEEDED
        elif "network error" in error_message or "connection error" in error_message:
            return CashoutErrorCode.NETWORK_ERROR
        
        # Default to generic classification for unknown Kraken errors
        return CashoutErrorCode.UNKNOWN_ERROR
    
    def _get_circuit_breaker_name(self) -> str:
        """Return the circuit breaker name for Kraken API"""
        return "kraken"
    
    async def _kraken_api_call_unified(self, operation: str, api_func: callable, *args, **kwargs) -> Any:
        """
        Unified API call wrapper for Kraken operations with retry logic
        Example usage pattern for external API integrations
        """
        @self.api_retry(max_attempts=3, context={"operation": operation, "service": "kraken"})
        async def _wrapped_call():
            return await api_func(*args, **kwargs)
        
        return await _wrapped_call()
    
    def is_available(self) -> bool:
        """Check if Kraken service is available (credentials configured)"""
        return bool(self.api_key and self.secret_key)
    
    def _generate_signature(self, urlpath: str, data: str, nonce: str) -> str:
        """Generate Kraken API signature"""
        postdata = urllib.parse.urlencode(data) if isinstance(data, dict) else data
        encoded = (nonce + postdata).encode()
        message = urlpath.encode() + hashlib.sha256(encoded).digest()
        
        mac = hmac.new(
            base64.b64decode(self.secret_key),
            message,
            hashlib.sha512
        )
        sigdigest = base64.b64encode(mac.digest())
        return sigdigest.decode()
    
    def _get_headers(self, signature: str) -> Dict[str, str]:
        """Get request headers for Kraken API"""
        return {
            'API-Key': self.api_key,
            'API-Sign': signature,
            'User-Agent': 'LockBay-Kraken/1.0.0',
            'Content-Type': 'application/x-www-form-urlencoded'
        }
    
    async def _get_unique_nonce(self) -> str:
        """Generate unique, strictly increasing nonce for Kraken API"""
        async with self._nonce_lock:
            # CRITICAL FIX: Kraken nonce must be 16 digits or less to avoid EAPI:Invalid nonce
            # Use seconds with microseconds but keep under 16 digits
            current_time_micro = int(time.time() * 1000000)
            
            # Ensure we stay under 16 digits (max: 9999999999999999)
            max_safe_nonce = 9999999999999999
            if current_time_micro > max_safe_nonce:
                # Fallback to milliseconds if microseconds exceed limit
                current_time_micro = int(time.time() * 1000)
            
            # Ensure nonce is always strictly increasing
            if current_time_micro <= self._last_nonce:
                self._last_nonce += 1
            else:
                self._last_nonce = current_time_micro
            
            # Double-check final nonce is safe
            final_nonce = min(self._last_nonce, max_safe_nonce)
            
            return str(final_nonce)
    
    async def _make_request(self, endpoint: str, params: Dict = None) -> Dict:
        """Make authenticated request to Kraken API with circuit breaker"""
        global _kraken_circuit_open, _kraken_failure_count, _kraken_circuit_opened_at
        
        # Circuit breaker check
        if _kraken_circuit_open:
            elapsed = time.time() - _kraken_circuit_opened_at
            if elapsed < _KRAKEN_CIRCUIT_COOLDOWN:
                raise Exception(f"Kraken circuit OPEN - skipping call ({elapsed:.0f}s / {_KRAKEN_CIRCUIT_COOLDOWN}s cooldown)")
            else:
                logger.info("Kraken circuit half-open - attempting recovery probe")
                _kraken_circuit_open = False
                _kraken_failure_count = 0
        
        if params is None:
            params = {}
        
        # CRITICAL FIX: Use thread-safe nonce generation
        nonce = await self._get_unique_nonce()
        params['nonce'] = nonce
        
        # Create URL path
        urlpath = f'/0/private/{endpoint}'
        
        # Generate signature
        signature = self._generate_signature(urlpath, params, nonce)
        headers = self._get_headers(signature)
        
        # Prepare POST data
        postdata = urllib.parse.urlencode(params)
        
        url = f'{self.base_url}{urlpath}'
        
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(
                    url,
                    data=postdata,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=20)  # Reduced to prevent webhook timeouts
                ) as response:
                    
                    response_text = await response.text()
                    
                    if response.status != 200:
                        logger.error(f"❌ Kraken API HTTP error: {response.status}")
                        raise Exception(f"HTTP {response.status}: {response_text}")
                    
                    try:
                        result = await response.json()
                    except json.JSONDecodeError as e:
                        logger.error(f"❌ Invalid JSON response: {response_text}, error: {e}")
                        raise Exception(f"Invalid JSON response: {response_text}")
                    
                    # Check for Kraken API errors
                    if result and result.get('error'):
                        error_msgs = result.get('error', [])
                        logger.error(f"Kraken API error: {error_msgs}")
                        _kraken_failure_count += 1
                        if _kraken_failure_count >= _KRAKEN_FAILURE_THRESHOLD:
                            _kraken_circuit_open = True
                            _kraken_circuit_opened_at = time.time()
                            logger.warning(f"Kraken circuit OPENED after {_kraken_failure_count} consecutive API errors")
                        raise Exception(f"Kraken API error: {', '.join(error_msgs) if error_msgs else 'Unknown error'}")
                    
                    _kraken_failure_count = 0  # Reset on success
                    return result.get('result', {})
                    
            except Exception as e:
                logger.error(f"Kraken API request failed: {str(e)}")
                _kraken_failure_count += 1
                if _kraken_failure_count >= _KRAKEN_FAILURE_THRESHOLD:
                    _kraken_circuit_open = True
                    _kraken_circuit_opened_at = time.time()
                    logger.warning(f"Kraken circuit OPENED after {_kraken_failure_count} consecutive failures - cooldown {_KRAKEN_CIRCUIT_COOLDOWN}s")
                raise
    
    async def test_credentials(self) -> Dict[str, Any]:
        """Test Kraken API credentials"""
        try:
            logger.info("🔐 Testing Kraken API credentials...")
            
            # Get account balance to test authentication
            result = await self._make_request('Balance')
            
            logger.info("✅ Kraken credentials valid")
            return {
                'valid': True,
                'balance_data': result
            }
            
        except Exception as e:
            logger.error(f"❌ Kraken credential test failed: {str(e)}")
            return {
                'valid': False,
                'error': str(e)
            }
    
    async def get_withdrawal_methods(self, asset: str) -> List[Dict]:
        """Get available withdrawal methods for a specific asset"""
        try:
            logger.info(f"📋 Getting Kraken withdrawal methods for {asset}...")
            result = await self._make_request('WithdrawMethods', {'asset': asset.upper()})
            
            # Convert to list if it's a dict with methods
            if isinstance(result, dict):
                methods_list = list(result.values()) if result else []
            else:
                methods_list = result if isinstance(result, list) else []
            
            logger.info(f"✅ Found {len(methods_list)} withdrawal methods for {asset}")
            return methods_list
            
        except Exception as e:
            logger.error(f"❌ Failed to get withdrawal methods for {asset}: {str(e)}")
            raise
    
    async def get_withdrawal_addresses(self, asset: str = None, method: str = None) -> List[Dict[str, Any]]:
        """Get pre-configured withdrawal addresses - FIXED to use proper Kraken API params"""
        try:
            logger.info(f"📍 Getting Kraken withdrawal addresses (asset={asset}, method={method})...")
            
            params = {}
            if asset:
                params['asset'] = asset.upper()
            if method:
                params['method'] = method
            
            result = await self._make_request('WithdrawAddresses', params)
            
            # CRITICAL FIX: Kraken returns list directly, not a dict
            if isinstance(result, list):
                logger.info(f"✅ Found {len(result)} withdrawal addresses")
                return result
            else:
                logger.warning(f"⚠️ Unexpected result format from Kraken: {type(result)}")
                return []
            
        except Exception as e:
            logger.error(f"❌ Failed to get withdrawal addresses: {str(e)}")
            raise
    
    async def get_all_withdrawal_addresses(self, assets: Optional[List[str]] = None, force_fresh: bool = False) -> List[Dict[str, Any]]:
        """Get ALL withdrawal addresses across specified assets (OPTIMIZED: with caching)"""
        try:
            # Check cache first (5 minute TTL to avoid repeated API calls)
            cache_key = f"all_addresses_{','.join(sorted(assets or []))}"
            current_time = time.time()
            
            # CRITICAL FIX: Skip cache if force_fresh=True (for admin retry after address config)
            if not force_fresh:
                if hasattr(self, '_address_cache'):
                    if cache_key in self._address_cache:
                        cached_data, timestamp = self._address_cache[cache_key]
                        if current_time - timestamp < 300:  # 5 minute cache
                            return cached_data
                else:
                    self._address_cache = {}
            else:
                if not hasattr(self, '_address_cache'):
                    self._address_cache = {}
            
            logger.info("📍 Getting ALL Kraken withdrawal addresses...")
            
            # CRITICAL FIX: Use specific asset list with all Kraken internal asset codes
            assets = assets or ['XXBT', 'XBT', 'XETH', 'USDT', 'XLTC', 'XXDG', 'TRX']
            
            all_addresses = []
            processed_combinations = set()
            
            for asset in assets:
                try:
                    # Get withdrawal methods for this specific asset
                    methods = await self.get_withdrawal_methods(asset)
                    
                    # OPTIMIZATION: Make parallel calls for all methods to reduce latency
                    method_tasks = []
                    
                    for method_info in methods:
                        method_name = method_info.get('method', '')
                        
                        # Skip if we've already processed this combination
                        combination = f"{asset}:{method_name}"
                        if combination in processed_combinations:
                            continue
                        processed_combinations.add(combination)
                        
                        # Create task for parallel execution
                        method_tasks.append({
                            'asset': asset,
                            'method': method_name,
                            'coro': self.get_withdrawal_addresses(asset=asset, method=method_name)
                        })
                    
                    # CRITICAL FIX: Execute sequentially to avoid Kraken API nonce conflicts
                    # Parallel requests cause "Invalid nonce" errors - must be sequential
                    if method_tasks:
                        for task_info in method_tasks:
                            try:
                                result = await task_info['coro']
                                
                                # Add asset and method info to each address
                                for addr in result:
                                    addr['_resolved_asset'] = task_info['asset']
                                    addr['_resolved_method'] = task_info['method']
                                    all_addresses.append(addr)
                                
                                logger.info(f"✅ Found {len(result)} addresses for {task_info['asset']}/{task_info['method']}")
                            except Exception as e:
                                logger.warning(f"Could not get addresses for {task_info['asset']}/{task_info['method']}: {e}")
                                continue
                            
                except Exception as e:
                    logger.warning(f"Could not get methods for asset {asset}: {str(e)}")
                    continue
            
            # Cache the results
            self._address_cache[cache_key] = (all_addresses, current_time)
            
            logger.info(f"✅ Retrieved {len(all_addresses)} total withdrawal addresses (cached)")
            return all_addresses
            
        except Exception as e:
            logger.error(f"❌ Failed to get all withdrawal addresses: {str(e)}")
            return []
    
    async def get_withdrawal_info(self, asset: str, key: str, amount: str) -> Dict[str, Any]:
        """Get withdrawal information including fees (with caching to prevent redundant calls)"""
        # Create cache key
        cache_key = f"{asset.upper()}:{key}:{amount}"
        current_time = time.time()
        
        # Check cache first
        if cache_key in self._withdrawal_info_cache:
            cached_data, timestamp = self._withdrawal_info_cache[cache_key]
            if current_time - timestamp < self._cache_expiry:
                return cached_data
        
        try:
            logger.info(f"💰 Getting withdrawal info for {amount} {asset}...")
            
            params = {
                'asset': asset.upper(),
                'key': key,
                'amount': str(amount)
            }
            
            result = await self._make_request('WithdrawInfo', params)
            
            # Cache the result
            self._withdrawal_info_cache[cache_key] = (result, current_time)
            
            # Clean up old cache entries
            self._cleanup_cache()
            
            logger.info(f"✅ Withdrawal info retrieved - Fee: {result.get('fee', 'unknown')}")
            return result
            
        except Exception as e:
            logger.error(f"❌ Failed to get withdrawal info: {str(e)}")
            raise
    
    def _cleanup_cache(self):
        """Remove expired cache entries"""
        current_time = time.time()
        expired_keys = [
            key for key, (_, timestamp) in self._withdrawal_info_cache.items()
            if current_time - timestamp > self._cache_expiry
        ]
        for key in expired_keys:
            del self._withdrawal_info_cache[key]
    
    @require_balance_protection("crypto_withdrawal", currency_param="asset", amount_param="amount")
    async def withdraw(self, asset: str, key: str, amount: str, _bypass_validation: bool = False, 
                      session=None, cashout_id: str = None, transaction_id: str = None) -> Dict[str, Any]:
        """PROTECTED: Execute cryptocurrency withdrawal - requires validated key unless bypassed by high-level service"""
        try:
            # CRITICAL FINANCIAL SAFETY: Context validation is MANDATORY for all withdrawals
            if session is None or cashout_id is None or transaction_id is None:
                logger.error("🚨 CONTEXT_VALIDATION_FAILED: Kraken withdrawal requires session, cashout_id, and transaction_id for idempotency protection")
                return {
                    'success': False,
                    'error': 'Missing required context for withdrawal - session, cashout_id, and transaction_id are mandatory',
                    'error_type': 'context_validation_failed',
                    'actionable_message': 'All Kraken withdrawals must include database session, cashout_id, and transaction_id for proper idempotency protection. This prevents duplicate withdrawals.',
                    'provider': 'kraken',
                    'required_context': ['session', 'cashout_id', 'transaction_id'],
                    'missing_context': [param for param, value in [('session', session), ('cashout_id', cashout_id), ('transaction_id', transaction_id)] if value is None]
                }
            
            # CRITICAL ENFORCEMENT: Block raw addresses and unvalidated keys
            if not _bypass_validation:
                # Check if this looks like a raw address instead of a Kraken key
                # CRITICAL FIX: Use startswith() to check prefixes, not substring match
                is_likely_address = (
                    len(key) > 30 or  # Very long strings are likely addresses (Kraken keys are typically <20 chars)
                    key.startswith(('0x', 'bc1', 'ltc1')) or  # Definitive crypto address prefixes
                    (len(key) > 25 and any(key.startswith(p) for p in ['1', '3', 'T', 'D', 'M', 'L']))  # Legacy address formats (only if long enough)
                )
                
                if is_likely_address:
                    logger.error(f"🚨 VALIDATION_BLOCKED: Raw address '{key[:10]}...' rejected - use KrakenWithdrawalService.execute_withdrawal() for proper validation")
                    return {
                        'success': False,
                        'error': 'Raw addresses not allowed - use validated withdrawal service',
                        'error_type': 'validation_required',
                        'actionable_message': 'Please use the KrakenWithdrawalService.execute_withdrawal() method which includes proper address validation.',
                        'provider': 'kraken'
                    }
                
                # Validate that the key exists and is verified
                try:
                    validation = await self.validate_withdrawal_key(asset, key)
                    # CRITICAL FIX: Check 'success' field, not 'valid' (which doesn't exist)
                    if not validation.get('success'):
                        logger.error(f"🚨 VALIDATION_BLOCKED: Invalid key '{key}' for {asset}")
                        return {
                            'success': False,
                            'error': validation.get('error', 'Invalid withdrawal key'),
                            'error_type': 'invalid_key',
                            'actionable_message': validation.get('actionable_message', 'The withdrawal key is not valid or verified in your Kraken account.'),
                            'provider': 'kraken'
                        }
                except Exception as validation_error:
                    logger.error(f"🚨 VALIDATION_ERROR: Could not validate key '{key}': {str(validation_error)}")
                    return {
                        'success': False,
                        'error': f'Key validation failed: {str(validation_error)}',
                        'error_type': 'validation_failed',
                        'actionable_message': 'Could not validate withdrawal key. Please check your Kraken account configuration.',
                        'provider': 'kraken'
                    }
            
            logger.info(f"💸 IDEMPOTENCY_PROTECTED: Executing Kraken withdrawal: {amount} {asset} to validated key '{key}' (transaction_id: {transaction_id})")
            
            params = {
                'asset': asset.upper(),
                'key': key,
                'amount': str(amount)
            }
            
            # CRITICAL FINANCIAL PROTECTION: Use idempotency for external API calls (context is guaranteed to exist)
            from utils.external_api_idempotency import KrakenIdempotencyWrapper
            
            with KrakenIdempotencyWrapper.ensure_single_withdrawal(
                session=session,
                cashout_id=cashout_id,
                currency=asset,
                amount=str(amount),
                address_key=key,
                transaction_id=transaction_id
            ) as api_guard:
                
                if api_guard.should_execute:
                    # Make the actual external API call
                    api_result = await self._make_request('Withdraw', params)
                    
                    # Store the successful result
                    api_guard.store_result({
                        'success': True,
                        'api_result': api_result,
                        'withdrawal_id': api_result.get('refid', 'unknown')
                    })
                
                # Get the result (either cached or newly created)
                final_result = api_guard.get_result()
                
                if final_result and final_result.get('success'):
                    api_result = final_result.get('api_result', {})
                    withdrawal_id = final_result.get('withdrawal_id', 'unknown')
                    
                    logger.info(f"✅ IDEMPOTENCY_SUCCESS: Kraken withdrawal completed - ID: {withdrawal_id} (transaction_id: {transaction_id})")
                    
                    # CRITICAL FIX: Invalidate balance cache after successful withdrawal (balance changed)
                    self.invalidate_balance_cache("successful_withdrawal")
                    
                    return {
                        'success': True,
                        'withdrawal_id': withdrawal_id,
                        'result': api_result,
                        'idempotency_protected': True,
                        'transaction_id': transaction_id
                    }
                else:
                    # Handle cached error result
                    logger.error(f"❌ IDEMPOTENCY_CACHED_ERROR: Kraken withdrawal failed (cached): {final_result.get('error')} (transaction_id: {transaction_id})")
                    return {
                        'success': False,
                        'error': final_result.get('error'),
                        'cached_result': True,
                        'idempotency_protected': True,
                        'transaction_id': transaction_id
                    }
            
        except Exception as e:
            logger.error(f"❌ Kraken withdrawal failed: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    async def get_cached_account_balance(self, force_fresh: bool = False, target_currency: Optional[str] = None) -> Dict[str, Any]:
        """Get account balance with intelligent caching and reliability safeguards
        
        Args:
            force_fresh: Force fresh fetch even if cache is valid (for reliability safeguards)
            target_currency: Target currency for optimization context (for logging only)
        
        Returns:
            Dict with balance data in standard format
        """
        async with self._balance_cache_lock:
            now = datetime.now(timezone.utc)
            
            # Check cache validity and force fresh conditions
            cache_valid = (
                self._balance_cache is not None and 
                self._balance_cache_timestamp is not None and
                (now - self._balance_cache_timestamp).total_seconds() < self._balance_cache_expiry_seconds
            )
            
            # Use cached result if valid and not forced fresh
            if cache_valid and not force_fresh:
                cache_age = int((now - self._balance_cache_timestamp).total_seconds())
                target_info = f" (target: {target_currency})" if target_currency else ""
                logger.info(f"🚀 BALANCE_CACHE_HIT: Reusing Kraken balance from {cache_age}s ago{target_info}")
                return self._balance_cache
            
            # Cache miss or forced fresh - fetch new data
            fresh_reason = "FORCED_FRESH" if force_fresh else "CACHE_MISS" if self._balance_cache is None else "CACHE_EXPIRED"
            target_info = f" (target: {target_currency})" if target_currency else ""
            logger.info(f"💰 BALANCE_FETCH_{fresh_reason}: Getting fresh Kraken balance{target_info}")
            
            # Fetch fresh balance data
            fresh_balance = await self.get_account_balance()
            
            # Cache the result if successful
            if fresh_balance and fresh_balance.get('success'):
                self._balance_cache = fresh_balance
                self._balance_cache_timestamp = now
                logger.info(f"💾 BALANCE_CACHED: Fresh Kraken balance cached for {self._balance_cache_expiry_seconds}s reuse")
            else:
                # Return stale cache if available and fresh fetch failed
                if self._balance_cache is not None:
                    cache_age = int((now - self._balance_cache_timestamp).total_seconds())
                    logger.warning(f"⚠️ BALANCE_STALE_FALLBACK: Using stale cache ({cache_age}s old) - fresh fetch failed")
                    return self._balance_cache
                else:
                    logger.error("❌ BALANCE_FETCH_FAILED: No cached data available and fresh fetch failed")
            
            return fresh_balance
    
    def invalidate_balance_cache(self, reason: str = "manual"):
        """Invalidate balance cache to force fresh fetch on next request
        
        Args:
            reason: Reason for invalidation (for logging)
        """
        logger.info(f"🗑️ BALANCE_CACHE_INVALIDATED: {reason}")
        self._balance_cache = None
        self._balance_cache_timestamp = None
    
    async def get_account_balance(self) -> Dict[str, Any]:
        """Get withdrawal available balance for all currencies (direct API call - use get_cached_account_balance() instead)"""
        try:
            logger.info("💰 Getting Kraken withdrawal available balance (direct API call)...")
            # CRITICAL FIX: Use BalanceEx endpoint to get withdrawal available balances
            # BalanceEx returns extended balance info including held amounts and actual available
            result = await self._make_request('BalanceEx')
            
            # Kraken currency mapping to standard symbols
            kraken_currency_map = {
                'XXBT': 'BTC',
                'XETH': 'ETH', 
                'ETH.F': 'ETH',  # Fix: Map ETH.F (staked ETH) to ETH for balance monitoring
                'XLTC': 'LTC',
                'XXDG': 'DOGE',
                'TRX': 'TRX',
                'USDT': 'USDT',
                'ZUSD': 'USD',
                'ZEUR': 'EUR',
                # Add more mappings as needed
            }
            
            # CRITICAL CHANGE: Parse BalanceEx response format for withdrawal available balance
            # BalanceEx returns extended balance info including held amounts and actual available for withdrawal
            
            # Transform BalanceEx format to our standard format with WITHDRAWAL AVAILABLE balance
            balances = {}
            
            # Extract balance details from BalanceEx response structure
            balance_data = result if isinstance(result, dict) else {}
            
            for kraken_currency, balance_info in balance_data.items():
                try:
                    # BalanceEx might return different structures depending on Kraken API version
                    if isinstance(balance_info, str):
                        # Simple string balance - treat as withdrawal available (similar to old Balance)
                        total_balance = float(balance_info)
                        available_balance = total_balance
                        held_balance = 0.0
                    elif isinstance(balance_info, dict):
                        # Extended balance info with held amounts
                        total_balance = float(balance_info.get('balance', '0.0'))
                        held_balance = float(balance_info.get('hold_trade', '0.0'))
                        
                        # CRITICAL: Calculate actual withdrawal available balance
                        # Available = Total - Held (funds locked in orders, withdrawal holds, etc.)
                        available_balance = max(0.0, total_balance - held_balance)
                        
                        logger.debug(f"📊 {kraken_currency}: Total={total_balance:.8f}, Held={held_balance:.8f}, Available={available_balance:.8f}")
                    else:
                        # Fallback: treat as string
                        total_balance = float(str(balance_info))
                        available_balance = total_balance
                        held_balance = 0.0
                    
                    # Map Kraken currency to standard symbol
                    standard_currency = kraken_currency_map.get(kraken_currency, kraken_currency)
                    
                    # Consolidate balances for currencies that map to the same standard symbol
                    if standard_currency in balances:
                        # Add to existing balance instead of overwriting
                        existing_total = balances[standard_currency]['total']
                        existing_available = balances[standard_currency]['available']
                        existing_locked = balances[standard_currency]['locked']
                        
                        consolidated_total = existing_total + total_balance
                        consolidated_available = existing_available + available_balance
                        consolidated_locked = existing_locked + held_balance
                        
                        balances[standard_currency] = {
                            'total': consolidated_total,
                            'available': consolidated_available,  # CRITICAL: This is now withdrawal available!
                            'locked': consolidated_locked
                        }
                        
                        if available_balance > 0:
                            logger.info(f"💰 Kraken balance: {kraken_currency} → {standard_currency}: Available={available_balance:.8f} (consolidated: {consolidated_available:.8f})")
                    else:
                        # Create new balance entry with WITHDRAWAL AVAILABLE balance
                        balances[standard_currency] = {
                            'total': total_balance,
                            'available': available_balance,  # CRITICAL: This is withdrawal available, not total!
                            'locked': held_balance
                        }
                        
                        if available_balance > 0:
                            logger.info(f"💰 Kraken withdrawal available: {kraken_currency} → {standard_currency}: {available_balance:.8f} (total: {total_balance:.8f}, held: {held_balance:.8f})")
                        elif total_balance > 0:
                            logger.warning(f"⚠️ Kraken funds locked: {kraken_currency} → {standard_currency}: Total={total_balance:.8f}, Available={available_balance:.8f}, Held={held_balance:.8f}")
                        
                except (ValueError, TypeError, KeyError) as e:
                    logger.warning(f"Could not parse BalanceEx data for {kraken_currency}: {balance_info} - {str(e)}")
                    continue
            
            # Log summary of withdrawal available balances
            available_currencies = [currency for currency, data in balances.items() if data and data.get('available', 0) > 0]
            locked_currencies = [currency for currency, data in balances.items() if data and data.get('locked', 0) > 0]
            
            logger.info(f"✅ Retrieved Kraken WITHDRAWAL AVAILABLE balances for {len(result)} currencies")
            logger.info(f"💰 Currencies with withdrawal available funds: {available_currencies}")
            if locked_currencies:
                logger.info(f"🔒 Currencies with locked funds: {locked_currencies}")
            logger.debug(f"Kraken currency mapping: {len(result)} symbols → {len(balances)} standard currencies")
            
            return {
                'success': True,
                'balances': balances
            }
            
        except Exception as e:
            logger.error(f"❌ Failed to get Kraken account balance: {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'balances': {}
            }
    
    async def get_withdrawal_status(self, asset: str = None) -> Dict[str, Any]:
        """Get status of recent withdrawals"""
        try:
            logger.info("📊 Getting Kraken withdrawal status...")
            
            params = {}
            if asset:
                params['asset'] = asset.upper()
            
            result = await self._make_request('WithdrawStatus', params)
            
            logger.info("✅ Withdrawal status retrieved")
            return result
            
        except Exception as e:
            logger.error(f"❌ Failed to get withdrawal status: {str(e)}")
            raise
    
    async def cancel_withdrawal(self, asset: str, refid: str) -> Dict[str, Any]:
        """Cancel a pending withdrawal"""
        try:
            logger.info(f"🛑 Cancelling Kraken withdrawal: {refid}")
            
            params = {
                'asset': asset.upper(),
                'refid': refid
            }
            
            result = await self._make_request('WithdrawCancel', params)
            
            logger.info(f"✅ Withdrawal cancelled: {refid}")
            return result
            
        except Exception as e:
            logger.error(f"❌ Failed to cancel withdrawal: {str(e)}")
            raise

    async def validate_withdrawal_key(self, currency: str, key: str) -> Dict[str, Any]:
        """Validate that a withdrawal key exists in Kraken account - STANDARDIZED ERROR FORMAT"""
        try:
            logger.info(f"🔍 Validating Kraken withdrawal key '{key}' for {currency}")
            
            # Find matching key for this currency
            kraken_currency_map = {
                'BTC': ['XXBT', 'XBT'],
                'ETH': ['XETH'], 
                'LTC': ['XLTC'],
                'DOGE': ['XXDG'],
                'USDT': ['USDT'],
                'TRX': ['TRX']
            }
            
            valid_assets = kraken_currency_map.get(currency.upper(), [currency.upper()])
            
            # PERFORMANCE OPTIMIZATION: Only get addresses for this specific currency
            addresses = await self.get_all_withdrawal_addresses(assets=valid_assets)
            
            for address_data in addresses:
                # CRITICAL FIX: Use _resolved_asset (the API asset code like XETH) instead of asset field from response
                kraken_asset = address_data.get('_resolved_asset', address_data.get('asset', ''))
                
                if (address_data.get('key') == key and 
                    kraken_asset.upper() in valid_assets):
                    
                    is_verified = address_data.get('verified', False)
                    
                    logger.info(f"✅ Withdrawal key '{key}' found for {currency} - Verified: {is_verified}")
                    return {
                        'success': True,
                        'verified': is_verified,
                        'address': address_data.get('address'),
                        'method': address_data.get('method'),
                        'error_type': None
                    }
            
            logger.error(f"❌ Withdrawal key '{key}' not found for {currency}")
            return {
                'success': False,
                'error': f'Withdrawal key "{key}" not configured in Kraken for {currency}',
                'error_type': 'invalid_key',
                'actionable_message': f'The withdrawal key "{key}" is not configured in your Kraken account for {currency}. Please add this address in Kraken dashboard under Funding > Withdraw.',
                'setup_instructions': {
                    'step1': 'Login to your Kraken account',
                    'step2': 'Go to Funding > Withdraw',
                    'step3': f'Select {currency}',
                    'step4': f'Add withdrawal address for key "{key}"',
                    'step5': 'Complete email/SMS verification',
                    'step6': 'Retry the withdrawal'
                }
            }
            
        except Exception as e:
            logger.error(f"❌ Error validating withdrawal key: {str(e)}")
            return {
                'success': False,
                'error': f'Unable to validate withdrawal key: {str(e)}',
                'error_type': 'validation_failed',
                'actionable_message': 'Could not verify withdrawal key due to API error. Please check your Kraken account and try again.',
                'setup_instructions': {
                    'step1': 'Login to your Kraken account',
                    'step2': 'Verify your withdrawal configuration',
                    'step3': 'Contact support if the issue persists'
                }
            }

    async def find_withdrawal_key_for_address(self, currency: str, target_address: str, force_fresh: bool = False) -> Dict[str, Any]:
        """Find the Kraken withdrawal key for a specific address - STANDARDIZED ERROR FORMAT"""
        try:
            logger.info(f"🔍 Finding Kraken withdrawal key for {currency} address: {target_address[:10]}...")
            
            # Map currency to Kraken asset codes
            kraken_currency_map = {
                'BTC': ['XXBT', 'XBT'],
                'ETH': ['XETH'], 
                'LTC': ['XLTC'],
                'DOGE': ['XXDG'],
                'USDT': ['USDT'],
                'TRX': ['TRX']
            }
            
            valid_assets = kraken_currency_map.get(currency.upper(), [currency.upper()])
            
            # PERFORMANCE OPTIMIZATION: Only get addresses for this specific currency
            # CRITICAL FIX: Pass force_fresh to bypass cache when retrying after address config
            addresses = await self.get_all_withdrawal_addresses(assets=valid_assets, force_fresh=force_fresh)
            
            # Search for matching address
            logger.info(f"🔍 DEBUG: Searching for target address: {target_address}")
            logger.info(f"🔍 DEBUG: Valid assets for {currency}: {valid_assets}")
            logger.info(f"🔍 DEBUG: Total addresses to search: {len(addresses)}")
            
            for idx, address_data in enumerate(addresses):
                kraken_address = address_data.get('address', '')
                # CRITICAL FIX: Use _resolved_asset (the API asset code like XETH) instead of asset field from response
                kraken_asset = address_data.get('_resolved_asset', address_data.get('asset', ''))
                
                logger.info(f"🔍 DEBUG Address {idx}: {kraken_address} (asset: {kraken_asset}, _resolved_asset: {address_data.get('_resolved_asset')}, key: {address_data.get('key')})")
                
                if (kraken_address.lower() == target_address.lower() and
                    kraken_asset.upper() in valid_assets):
                    
                    key = address_data.get('key')
                    is_verified = address_data.get('verified', False)
                    
                    logger.info(f"✅ Found withdrawal key '{key}' for address {target_address[:10]}... - Verified: {is_verified}")
                    
                    return {
                        'success': True,
                        'key': key,
                        'verified': is_verified,
                        'address': address_data.get('address'),
                        'asset': address_data.get('asset'),
                        'method': address_data.get('method'),
                        'error_type': None
                    }
            
            logger.warning(f"❌ No withdrawal key found for {currency} address: {target_address[:10]}...")
            return {
                'success': False,
                'error': f'Address {target_address} not configured in Kraken account for {currency}',
                'error_type': 'address_not_configured',
                'actionable_message': f'The address {target_address} is not configured in your Kraken account. Please add it in Kraken dashboard under Funding > Withdraw > {currency}, then try again.',
                'setup_instructions': {
                    'step1': 'Login to your Kraken account',
                    'step2': 'Go to Funding > Withdraw',
                    'step3': f'Select {currency}',
                    'step4': f'Add new address: {target_address}',
                    'step5': 'Complete email/SMS verification',
                    'step6': 'Retry the withdrawal'
                }
            }
            
        except Exception as e:
            logger.error(f"❌ Error finding withdrawal key: {str(e)}")
            return {
                'success': False,
                'error': f'Unable to find withdrawal key: {str(e)}',
                'error_type': 'api_error',
                'actionable_message': 'Could not search for withdrawal addresses due to API error. Please check your Kraken account and try again.',
                'setup_instructions': {
                    'step1': 'Login to your Kraken account',
                    'step2': 'Verify API access and credentials',
                    'step3': 'Contact support if the issue persists'
                }
            }

    @require_balance_protection("crypto_cashout", currency_param="currency", amount_param="amount")
    async def withdraw_crypto(self, currency: str, amount: float, address: str, reference: str = None,
                             session=None, cashout_id: str = None, transaction_id: str = None, force_fresh: bool = False) -> Dict[str, Any]:
        """Enhanced wrapper method for crypto withdrawals with proper key validation and error handling"""
        try:
            # CRITICAL FINANCIAL SAFETY: Context validation is MANDATORY for all withdrawals
            if session is None or cashout_id is None or transaction_id is None:
                logger.error("🚨 CONTEXT_VALIDATION_FAILED: Kraken withdraw_crypto requires session, cashout_id, and transaction_id for idempotency protection")
                return {
                    'success': False,
                    'error': 'Missing required context for withdrawal - session, cashout_id, and transaction_id are mandatory',
                    'error_type': 'context_validation_failed',
                    'actionable_message': 'All Kraken withdrawals must include database session, cashout_id, and transaction_id for proper idempotency protection. This prevents duplicate withdrawals.',
                    'provider': 'kraken',
                    'required_context': ['session', 'cashout_id', 'transaction_id'],
                    'missing_context': [param for param, value in [('session', session), ('cashout_id', cashout_id), ('transaction_id', transaction_id)] if value is None]
                }
            
            logger.info(f"🔄 IDEMPOTENCY_PROTECTED: Kraken withdraw_crypto: {amount} {currency} to {address[:12]}... (transaction_id: {transaction_id})")
            
            # CRITICAL FIX: Better heuristics for detecting keys vs addresses
            is_likely_key = (
                len(address) < 20 or  # Short strings are likely keys
                (address.isalnum() and len(address) < 30) or  # Alphanumeric and short
                not any(c in address for c in ['1', '3', '0x', 'bc1', 'ltc1', 'T', 'D'])  # No crypto address prefixes
            )
            
            if is_likely_key:
                # Likely a Kraken key (keys are usually short alphanumeric strings)
                key = address
                logger.info(f"   Detected as Kraken key: '{key}'")
                
                # Validate the key exists
                validation = await self.validate_withdrawal_key(currency, key)
                # CRITICAL FIX: Check 'success' field, not 'valid' (which doesn't exist)
                if not validation.get('success'):
                    logger.error(f"❌ Invalid withdrawal key: {validation.get('error')}")
                    return {
                        'success': False,
                        'error': validation.get('error'),
                        'error_type': validation.get('error_type', 'invalid_key'),
                        'actionable_message': validation.get('actionable_message'),
                        'provider': 'kraken'
                    }
                    
            else:
                # This is a raw address - need to find corresponding key
                logger.info(f"   Detected as raw address: {address[:10]}... - finding Kraken key")
                
                # CRITICAL FIX: Pass force_fresh to bypass cache when retrying after address config
                key_result = await self.find_withdrawal_key_for_address(currency, address, force_fresh=force_fresh)
                if not key_result.get('success'):
                    logger.error(f"❌ Address not configured in Kraken: {key_result.get('error')}")
                    return {
                        'success': False,
                        'error': key_result.get('error'),
                        'error_type': key_result.get('error_type', 'address_not_configured'),
                        'actionable_message': key_result.get('actionable_message'),
                        'setup_instructions': key_result.get('setup_instructions'),
                        'provider': 'kraken'
                    }
                
                key = key_result.get('key')
                
                # Check if address is verified
                if not key_result.get('verified'):
                    logger.warning("⚠️ Address exists but not verified in Kraken")
                    return {
                        'success': False,
                        'error': f'Address {address} exists in Kraken but is not verified',
                        'error_type': 'address_not_verified',
                        'actionable_message': 'The address is in your Kraken account but needs verification. Please check your email/SMS and verify the address, then try again.',
                        'provider': 'kraken'
                    }
            
            # Execute withdrawal with validated key
            asset = currency.upper()
            amount_str = str(amount)
            
            logger.info(f"✅ Executing withdrawal: {amount} {asset} to verified key '{key}' (transaction_id: {transaction_id})")
            result = await self.withdraw(
                asset=asset, 
                key=key, 
                amount=amount_str,
                session=session,
                cashout_id=cashout_id,
                transaction_id=transaction_id
            )
            
            if result.get('success'):
                logger.info("✅ Kraken withdrawal successful")
                
                # CRITICAL FIX: Invalidate balance cache after successful withdrawal (balance changed)
                self.invalidate_balance_cache("kraken_withdrawal_success")
                
                return {
                    'success': True,
                    'withdrawal_id': result.get('withdrawal_id'),
                    'tx_hash': None,  # Kraken doesn't provide immediate tx hash
                    'reference': reference or result.get('withdrawal_id'),
                    'key_used': key,
                    'provider': 'kraken'
                }
            else:
                # Withdrawal failed - analyze error for better handling
                error_msg = result.get('error', 'Kraken withdrawal failed')
                logger.error(f"❌ Kraken withdrawal failed: {error_msg}")
                
                # CRITICAL FIX: Detect specific Kraken errors for better routing
                error_type = 'withdrawal_failed'
                actionable_message = f'Withdrawal failed: {error_msg}'
                
                # Check for specific error patterns
                error_lower = error_msg.lower()
                if 'unknown withdraw key' in error_lower or 'invalid key' in error_lower:
                    error_type = 'invalid_key'
                    actionable_message = 'The withdrawal key is not configured in your Kraken account. Please check your Kraken withdrawal addresses.'
                elif 'insufficient' in error_lower:
                    error_type = 'insufficient_balance'
                    actionable_message = 'Insufficient balance in your Kraken account for this withdrawal. Admin funding is required.'
                elif 'limit' in error_lower or 'minimum' in error_lower:
                    error_type = 'amount_limit'
                    actionable_message = 'The withdrawal amount exceeds limits or is below minimum. Please check Kraken withdrawal limits.'
                elif 'verified' in error_lower or 'verification' in error_lower:
                    error_type = 'verification_required'
                    actionable_message = 'Additional verification required in your Kraken account before this withdrawal can be processed.'
                
                return {
                    'success': False,
                    'error': error_msg,
                    'error_type': error_type,
                    'actionable_message': actionable_message,
                    'provider': 'kraken'
                }
                
        except Exception as e:
            import traceback
            error_msg = str(e)
            error_trace = traceback.format_exc()
            logger.error(f"❌ Kraken withdraw_crypto exception: {error_msg}")
            logger.error(f"🔍 FULL_TRACEBACK:\n{error_trace}")
            
            # CRITICAL FIX: Better exception analysis for error routing
            error_type = 'execution_error'
            actionable_message = f'Withdrawal could not be executed: {error_msg}'
            
            # Check for specific exception patterns
            if 'unknown withdraw key' in error_msg.lower():
                error_type = 'invalid_key'
                actionable_message = 'The withdrawal key is not configured in your Kraken account. Please add the withdrawal address in Kraken dashboard.'
            elif 'api' in error_msg.lower() or 'connection' in error_msg.lower():
                error_type = 'api_error'
                actionable_message = 'Temporary API error. Please try again in a few minutes.'
            
            return {
                'success': False,
                'error': error_msg,
                'error_type': error_type,
                'actionable_message': actionable_message,
                'provider': 'kraken'
            }

    async def process_auto_cashout(self, cashout, session) -> Dict[str, Any]:
        """Process automatic crypto cashout for background processing"""
        try:
            logger.info(f"🔄 Processing auto-cashout {cashout.id} via Kraken")
            
            # Extract cashout details
            amount = float(cashout.amount)
            # CRITICAL FIX: Use cashout_id instead of non-existent reference field  
            destination = getattr(cashout, 'destination', None) or getattr(cashout, 'blockchain_tx_id', None)
            reference = getattr(cashout, 'cashout_id', None) or str(cashout.id)
            
            # CRITICAL FIX: Determine target crypto currency from destination address format
            # The cashout.currency is now "USD" for wallet deduction, but we need the actual crypto
            if not destination:
                logger.error(f"❌ No destination address found for cashout {cashout.id}")
                return {
                    'success': False,
                    'error': 'No destination address specified',
                    'status': 'failed'
                }
            
            # Detect cryptocurrency from address format
            if destination.startswith('0x') and len(destination) == 42:
                currency = "ETH"  # Ethereum address
            elif destination.startswith(('1', '3', 'bc1')):
                currency = "BTC"  # Bitcoin address
            elif destination.startswith(('L', 'M', 'ltc1')):
                currency = "LTC"  # Litecoin address
            elif destination.startswith('D'):
                currency = "DOGE"  # Dogecoin address
            elif destination.startswith('T'):
                currency = "TRX"  # Tron address
            else:
                # Fallback: check if it looks like a withdrawal key instead of address
                if len(destination) < 30 and destination.isalnum():
                    currency = cashout.currency.upper()  # Use original currency for keys
                else:
                    logger.warning(f"⚠️ Unknown address format: {destination[:10]}... using USD")
                    currency = "ETH"  # Default to ETH for unknown formats
            
            # Process crypto withdrawal via Kraken
            result = await self.withdraw_crypto(
                currency=currency,
                amount=amount,
                address=destination,
                reference=reference
            )
            
            if result and result.get('success'):
                logger.info(f"✅ Auto-cashout {cashout.id} processed successfully via Kraken")
                
                # CRITICAL FIX: Invalidate balance cache after successful auto-cashout (balance changed)
                self.invalidate_balance_cache("kraken_auto_cashout_success")
                
                return {
                    'success': True,
                    'status': 'completed',
                    'reference': result.get('withdrawal_id', reference),
                    'tx_hash': result.get('tx_hash'),
                    'amount': amount,
                    'currency': currency
                }
            else:
                error_msg = result.get('error', 'Unknown Kraken error') if result else 'No response from Kraken'
                logger.error(f"❌ Auto-cashout {cashout.id} failed: {error_msg}")
                return {
                    'success': False,
                    'error': error_msg,
                    'status': 'failed'
                }
                
        except Exception as e:
            logger.error(f"❌ Error in auto-cashout processing for {cashout.id}: {e}")
            return {
                'success': False,
                'error': str(e),
                'status': 'failed'
            }


# Global instance
kraken_service = None

def get_kraken_service() -> KrakenService:
    """Get or create Kraken service instance"""
    global kraken_service
    if kraken_service is None:
        kraken_service = KrakenService()
    return kraken_service