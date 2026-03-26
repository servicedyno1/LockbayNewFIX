"""
Exchange rate service using Tatum API for real-time cryptocurrency and forex rates.
Replaces CoinGecko + FastForex with unified Tatum provider.

Tatum API: GET https://api.tatum.io/v4/data/rate/symbol?symbol=BTC&basePair=USD
"""

import aiohttp
import asyncio
import logging
import os
import time
from decimal import Decimal
from typing import Dict, Optional
from config import Config
from utils.data_sanitizer import safe_error_log
from utils.production_cache import get_cached, set_cached, delete_cached
from services.api_adapter_retry import APIAdapterRetry
from models import CashoutErrorCode

logger = logging.getLogger(__name__)

# Tatum API configuration
TATUM_API_URL = "https://api.tatum.io/v4/data/rate/symbol"
TATUM_API_KEY = os.environ.get("TATUM_API_KEY", "")

# FastForex Legacy API Circuit Breaker
# Stops calling expired/broken FastForex API after consecutive failures
_fastforex_circuit_open = False
_fastforex_failure_count = 0
_fastforex_circuit_opened_at = 0.0
_FASTFOREX_FAILURE_THRESHOLD = 3        # Open circuit after 3 consecutive failures
_FASTFOREX_CIRCUIT_COOLDOWN = 1800      # 30 minutes before retrying

# Symbol mapping: internal/Kraken symbols → standard Tatum symbols
TATUM_SYMBOL_MAP = {
    "BTC": "BTC", "ETH": "ETH", "LTC": "LTC", "DOGE": "DOGE",
    "BCH": "BCH", "BNB": "BNB", "TRX": "TRX", "USDT": "USDT",
    "XRP": "XRP", "ADA": "ADA", "DOT": "DOT", "XLM": "XLM",
    "XMR": "XMR", "ZEC": "ZEC", "LINK": "LINK", "XTZ": "XTZ",
    "REP": "REP", "SOL": "SOL", "AVAX": "AVAX", "MATIC": "MATIC",
    # Kraken X-prefixed mappings
    "XETH": "ETH", "XXBT": "BTC", "XLTC": "LTC", "XXDG": "DOGE",
    "XBCH": "BCH", "XTRX": "TRX", "XXRP": "XRP", "XADA": "ADA",
    "XDOT": "DOT", "XXLM": "XLM", "XXMR": "XMR", "XZEC": "ZEC",
    "XREP": "REP", "XXTZ": "XTZ", "XLINK": "LINK", "XUSDT": "USDT",
    # Other mappings
    "BSC": "BNB", "USDT-ERC20": "USDT", "USDT-TRC20": "USDT",
    "ZUSD": "USD", "ZEUR": "EUR",
}

# Legacy aliases for backward compatibility
COINGECKO_SYMBOL_MAP = TATUM_SYMBOL_MAP
COINGECKO_API_URL = TATUM_API_URL
COINGECKO_API_KEY = TATUM_API_KEY


class FastForexAPIError(Exception):
    """Custom exception for rate API errors"""
    pass


class FastForexService(APIAdapterRetry):
    """Service for fetching real-time exchange rates from Tatum API.
    
    Class name kept as FastForexService for backward compatibility with 87+ consumers.
    Internally uses Tatum API for all crypto and fiat rate lookups.
    """

    def __init__(self):
        super().__init__(service_name="fastforex", timeout=30)
        
        # Primary: Tatum API key
        self.tatum_api_key = Config.TATUM_API_KEY or TATUM_API_KEY
        # Legacy fallback: FastForex (deprecated)
        self.api_key = Config.FASTFOREX_API_KEY
        self.base_url = "https://api.fastforex.io"
        
        # Cache configuration
        self.cache_ttl = 3600        # 1 hour
        self.fallback_cache_ttl = 7200  # 2 hours
        self.rapid_cache_ttl = 600   # 10 minutes
        self.session_cache_ttl = 300 # 5 minutes
        self.webhook_cache_ttl = 1800  # 30 minutes
        
        if self.tatum_api_key:
            logger.info("Tatum API key configured - using Tatum as primary rate provider")
        elif self.api_key:
            logger.warning("TATUM_API_KEY not configured, falling back to FastForex")
        else:
            logger.warning("No rate API key configured - rates will not be available")
    
    def _map_provider_error_to_unified(self, exception: Exception, context: Optional[Dict] = None) -> CashoutErrorCode:
        error_message = str(exception).lower()
        if "timeout" in error_message:
            return CashoutErrorCode.API_TIMEOUT
        elif "authentication" in error_message or "invalid api key" in error_message or "401" in error_message:
            return CashoutErrorCode.API_AUTHENTICATION_FAILED
        elif "not supported" in error_message or "invalid" in error_message:
            return CashoutErrorCode.API_INVALID_REQUEST
        elif "rate limit" in error_message or "429" in error_message:
            return CashoutErrorCode.RATE_LIMIT_EXCEEDED
        elif "unavailable" in error_message or "502" in error_message or "503" in error_message:
            return CashoutErrorCode.SERVICE_UNAVAILABLE
        elif "network" in error_message or "connection" in error_message:
            return CashoutErrorCode.NETWORK_ERROR
        return CashoutErrorCode.UNKNOWN_ERROR
    
    def _get_circuit_breaker_name(self) -> str:
        return "fastforex"
    
    def _map_symbol(self, crypto_symbol: str) -> str:
        """Map any symbol variant to standard Tatum symbol."""
        return TATUM_SYMBOL_MAP.get(crypto_symbol, crypto_symbol)

    # ──────────────────────────────────────────────
    # Tatum API helpers
    # ──────────────────────────────────────────────

    async def _fetch_tatum_rate(self, symbol: str, base_pair: str = "USD") -> Optional[Decimal]:
        """Fetch a single rate from Tatum API.
        
        Args:
            symbol: The asset to price (e.g. BTC, ETH, USD)
            base_pair: The quote currency (e.g. USD, NGN)
        
        Returns:
            Decimal rate or None on failure.
        """
        if not self.tatum_api_key:
            return None
        try:
            headers = {"x-api-key": self.tatum_api_key}
            params = {"symbol": symbol, "basePair": base_pair}
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(TATUM_API_URL, params=params, timeout=timeout) as response:
                    if response.status == 200:
                        data = await response.json()
                        if "value" in data:
                            rate = Decimal(str(data["value"]))
                            logger.info(f"Tatum {symbol}/{base_pair}: {float(rate):.4f}")
                            return rate
                        logger.warning(f"Tatum unexpected response for {symbol}/{base_pair}: {data}")
                    elif response.status == 429:
                        logger.warning("Tatum rate-limited")
                    elif response.status == 401:
                        logger.error("Tatum API key invalid or expired")
                    else:
                        logger.warning(f"Tatum API error: {response.status}")
        except asyncio.TimeoutError:
            logger.warning(f"Tatum API timeout for {symbol}/{base_pair}")
        except Exception as e:
            logger.warning(f"Tatum API error for {symbol}/{base_pair}: {e}")
        return None

    async def _fetch_tatum_batch_rates(self, symbols: list) -> Dict[str, Decimal]:
        """Fetch multiple crypto→USD rates from Tatum (concurrent single calls)."""
        if not self.tatum_api_key:
            return {}
        
        tasks = {}
        for sym in symbols:
            if sym in ("USD", "USDT"):
                continue
            tasks[sym] = asyncio.create_task(self._fetch_tatum_rate(sym, "USD"))
        
        rates = {}
        for sym, task in tasks.items():
            try:
                rate = await task
                if rate is not None:
                    rates[sym] = rate
            except Exception as e:
                logger.warning(f"Tatum batch error for {sym}: {e}")
        
        if rates:
            logger.info(f"Tatum batch: {len(rates)} rates fetched ({', '.join(rates.keys())})")
        return rates

    # Legacy aliases for backward compatibility
    async def _fetch_coingecko_crypto_rate(self, mapped_symbol: str) -> Optional[Decimal]:
        """Fetch crypto rate from Tatum (replaces CoinGecko)."""
        return await self._fetch_tatum_rate(mapped_symbol, "USD")

    async def _fetch_coingecko_batch_rates(self, symbols: list) -> Dict[str, Decimal]:
        """Batch-fetch crypto rates from Tatum (replaces CoinGecko)."""
        return await self._fetch_tatum_batch_rates(symbols)

    # ──────────────────────────────────────────────
    # Core rate methods (same interface as before)
    # ──────────────────────────────────────────────

    async def _get_crypto_rate_direct(self, crypto_symbol: str, session: aiohttp.ClientSession) -> Decimal:
        """Direct crypto rate fetch with provided session (cross-loop safe)."""
        mapped_symbol = self._map_symbol(crypto_symbol)
        if mapped_symbol == "USD":
            return Decimal("1.0")
        
        # Try Tatum first
        rate = await self._fetch_tatum_rate(mapped_symbol, "USD")
        if rate is not None:
            return rate
        
        # Legacy FastForex fallback
        if self.api_key:
            url = f"{self.base_url}/fetch-one"
            params = {"from": mapped_symbol, "to": "USD", "api_key": self.api_key}
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    if "result" in data and "USD" in data["result"]:
                        return Decimal(str(data["result"]["USD"]))
        
        raise FastForexAPIError(f"All rate sources failed for {crypto_symbol}")

    async def _get_ngn_rate_direct(self, session: aiohttp.ClientSession) -> Decimal:
        """Direct NGN rate fetch with provided session (cross-loop safe)."""
        # Try Tatum: USD→NGN
        rate = await self._fetch_tatum_rate("USD", "NGN")
        if rate is not None:
            markup_percentage = Decimal(str(Config.EXCHANGE_MARKUP_PERCENTAGE))
            return rate * (Decimal("1") - markup_percentage / Decimal("100"))
        
        # Legacy FastForex fallback
        if self.api_key:
            url = f"{self.base_url}/fetch-one"
            params = {"from": "USD", "to": "NGN", "api_key": self.api_key}
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    if "result" in data and "NGN" in data["result"]:
                        fastforex_rate = Decimal(str(data["result"]["NGN"]))
                        markup_percentage = Decimal(str(Config.EXCHANGE_MARKUP_PERCENTAGE))
                        return fastforex_rate * (Decimal("1") - markup_percentage / Decimal("100"))
        
        raise FastForexAPIError("All rate sources failed for USD→NGN")

    def get_crypto_rate(self, crypto_symbol: str) -> Decimal:
        """Synchronous wrapper for get_crypto_to_usd_rate."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                def run_in_thread():
                    return asyncio.run(self.get_crypto_to_usd_rate(crypto_symbol))
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(run_in_thread)
                    return future.result(timeout=30)
            else:
                return asyncio.run(self.get_crypto_to_usd_rate(crypto_symbol))
        except Exception as e:
            logger.error(f"CRYPTO_RATE_ERROR: {crypto_symbol} - {e}")
            raise e

    def get_ngn_to_usd_rate(self) -> Decimal:
        """Synchronous wrapper for NGN to USD rate."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                def run_in_thread():
                    return asyncio.run(self.get_ngn_to_usd_rate_with_markup())
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(run_in_thread)
                    result = future.result(timeout=30)
                    return result if result is not None else Decimal("0")
            else:
                result = asyncio.run(self.get_ngn_to_usd_rate_with_markup())
                return result if result is not None else Decimal("0")
        except Exception as e:
            logger.error(f"NGN_RATE_ERROR: Failed to get NGN to USD rate - {e}")
            raise e

    async def get_crypto_to_usd_rate(self, crypto_symbol: str) -> Decimal:
        """Get cryptocurrency to USD exchange rate with intelligent caching.
        
        Source priority: Cache -> Tatum -> FastForex (legacy) -> Error
        """
        try:
            mapped_symbol = self._map_symbol(crypto_symbol)
            
            # Check caches
            cache_key = f"crypto_rate_{mapped_symbol}_USD"
            cached_rate = get_cached(cache_key)
            if cached_rate is not None:
                return Decimal(str(cached_rate))
            
            rapid_cache_key = f"rapid_crypto_rate_{mapped_symbol}_USD"
            rapid_cached_rate = get_cached(rapid_cache_key)
            if rapid_cached_rate is not None:
                return Decimal(str(rapid_cached_rate))

            if mapped_symbol == "USD":
                return Decimal("1.0")
            if mapped_symbol == "USDT":
                rate = Decimal("1.0")
                set_cached(cache_key, rate, ttl=self.cache_ttl)
                return rate

            # === SOURCE 1: Tatum API (primary) ===
            tatum_rate = await self._fetch_tatum_rate(mapped_symbol, "USD")
            if tatum_rate is not None:
                set_cached(cache_key, tatum_rate, ttl=self.cache_ttl)
                set_cached(rapid_cache_key, tatum_rate, ttl=self.rapid_cache_ttl)
                set_cached(f"fallback_crypto_rate_{mapped_symbol}_USD", tatum_rate, ttl=self.fallback_cache_ttl)
                return tatum_rate

            # === SOURCE 2: FastForex (legacy fallback with circuit breaker) ===
            if self.api_key and not _fastforex_circuit_open:
                try:
                    timeout = aiohttp.ClientTimeout(total=5)
                    async with aiohttp.ClientSession() as session:
                        url = f"{self.base_url}/fetch-one"
                        params = {"from": mapped_symbol, "to": "USD", "api_key": self.api_key}
                        async with session.get(url, params=params, timeout=timeout) as response:
                            if response.status == 200:
                                data = await response.json()
                                if "result" in data and "USD" in data["result"]:
                                    rate = Decimal(str(data["result"]["USD"]))
                                    set_cached(cache_key, rate, ttl=self.cache_ttl)
                                    set_cached(rapid_cache_key, rate, ttl=self.rapid_cache_ttl)
                                    set_cached(f"fallback_crypto_rate_{mapped_symbol}_USD", rate, ttl=self.fallback_cache_ttl)
                                    logger.info(f"FastForex fallback {crypto_symbol}: ${float(rate):.4f} USD")
                                    return rate
                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    logger.warning(f"FastForex fallback timeout/error for {crypto_symbol}: {e}")

            raise FastForexAPIError(f"All rate sources failed for {crypto_symbol}")

        except aiohttp.ClientError as e:
            logger.error(f"Network error fetching {crypto_symbol} rate: {e}")
            raise FastForexAPIError(f"Network error for {crypto_symbol}: {e}")
        except FastForexAPIError:
            raise
        except Exception as e:
            safe_error = safe_error_log(e)
            logger.error(f"Unexpected error fetching {crypto_symbol} rate: {safe_error}")
            raise FastForexAPIError("Unexpected error occurred")

    async def _fetch_fastforex_single_rate(self, mapped_symbol: str) -> Optional[Decimal]:
        """Fetch a single rate - tries Tatum first, then FastForex with circuit breaker."""
        global _fastforex_circuit_open, _fastforex_failure_count, _fastforex_circuit_opened_at
        
        # Tatum primary
        rate = await self._fetch_tatum_rate(mapped_symbol, "USD")
        if rate is not None:
            return rate
        # FastForex fallback with circuit breaker
        if not self.api_key:
            return None
        
        # Circuit breaker check
        if _fastforex_circuit_open:
            elapsed = time.time() - _fastforex_circuit_opened_at
            if elapsed < _FASTFOREX_CIRCUIT_COOLDOWN:
                return None
            else:
                _fastforex_circuit_open = False
                _fastforex_failure_count = 0
        
        try:
            timeout = aiohttp.ClientTimeout(total=5)  # 5s aggressive timeout
            async with aiohttp.ClientSession() as session:
                url = f"{self.base_url}/fetch-one"
                params = {"from": mapped_symbol, "to": "USD", "api_key": self.api_key}
                async with session.get(url, params=params, timeout=timeout) as response:
                    if response.status == 200:
                        data = await response.json()
                        if "result" in data and "USD" in data["result"]:
                            rate = Decimal(str(data["result"]["USD"]))
                            _fastforex_failure_count = 0
                            logger.info(f"FastForex fallback {mapped_symbol}: ${float(rate):.4f} USD")
                            return rate
                    else:
                        _fastforex_failure_count += 1
                        if _fastforex_failure_count >= _FASTFOREX_FAILURE_THRESHOLD:
                            _fastforex_circuit_open = True
                            _fastforex_circuit_opened_at = time.time()
                            logger.warning(f"FastForex circuit OPENED in single_rate after {_fastforex_failure_count} failures")
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.warning(f"FastForex single rate timeout/error for {mapped_symbol}: {e}")
            _fastforex_failure_count += 1
            if _fastforex_failure_count >= _FASTFOREX_FAILURE_THRESHOLD:
                _fastforex_circuit_open = True
                _fastforex_circuit_opened_at = time.time()
        except Exception as e:
            logger.warning(f"FastForex single rate error for {mapped_symbol}: {e}")
        return None

    async def get_multiple_rates(self, symbols: list) -> Dict[str, Decimal]:
        """Get multiple real-time cryptocurrency rates efficiently."""
        try:
            logger.info(f"Fetching rates for: {symbols}")
            
            rates = {}
            uncached_symbols = []
            
            # Step 1: Collect cached rates
            for sym in symbols:
                mapped = self._map_symbol(sym)
                if mapped == "USD":
                    rates[sym] = Decimal("1.0")
                    continue
                if mapped == "USDT":
                    rates[sym] = Decimal("1.0")
                    continue
                cache_key = f"crypto_rate_{mapped}_USD"
                cached = get_cached(cache_key)
                if cached is not None:
                    rates[sym] = Decimal(str(cached))
                else:
                    uncached_symbols.append((sym, mapped))
            
            if not uncached_symbols:
                return rates
            
            # Step 2: Batch fetch from Tatum
            unique_mapped = list(set(m for _, m in uncached_symbols))
            tatum_rates = await self._fetch_tatum_batch_rates(unique_mapped)
            
            remaining = []
            for orig_sym, mapped in uncached_symbols:
                if mapped in tatum_rates:
                    rate = tatum_rates[mapped]
                    cache_key = f"crypto_rate_{mapped}_USD"
                    set_cached(cache_key, rate, ttl=self.cache_ttl)
                    set_cached(f"rapid_crypto_rate_{mapped}_USD", rate, ttl=self.rapid_cache_ttl)
                    set_cached(f"fallback_crypto_rate_{mapped}_USD", rate, ttl=self.fallback_cache_ttl)
                    rates[orig_sym] = rate
                else:
                    remaining.append((orig_sym, mapped))
            
            # Step 3: FastForex fallback for any misses
            if remaining and self.api_key:
                tasks = []
                for orig_sym, mapped in remaining:
                    task = asyncio.create_task(self._fetch_fastforex_single_rate(mapped))
                    tasks.append((orig_sym, mapped, task))
                for orig_sym, mapped, task in tasks:
                    try:
                        rate = await task
                        if rate:
                            cache_key = f"crypto_rate_{mapped}_USD"
                            set_cached(cache_key, rate, ttl=self.cache_ttl)
                            set_cached(f"rapid_crypto_rate_{mapped}_USD", rate, ttl=self.rapid_cache_ttl)
                            set_cached(f"fallback_crypto_rate_{mapped}_USD", rate, ttl=self.fallback_cache_ttl)
                            rates[orig_sym] = rate
                    except Exception as e:
                        logger.error(f"Failed to get rate for {orig_sym}: {e}")
            
            return rates

        except Exception as e:
            logger.error(f"Error fetching multiple rates: {e}")
            raise FastForexAPIError(f"Multiple rates error: {e}")

    async def convert_crypto_to_usd(self, amount: Decimal, crypto_symbol: str) -> Decimal:
        """Convert cryptocurrency amount to USD"""
        rate = await self.get_crypto_to_usd_rate(crypto_symbol)
        return amount * rate

    async def convert_usd_to_crypto(self, usd_amount: Decimal, crypto_symbol: str) -> Decimal:
        """Convert USD amount to cryptocurrency"""
        rate = await self.get_crypto_to_usd_rate(crypto_symbol)
        if rate == Decimal("0"):
            raise FastForexAPIError(f"Invalid rate (0) for {crypto_symbol}")
        return usd_amount / rate

    async def _make_request(self, endpoint: str, params: dict) -> Optional[dict]:
        """Make request - tries Tatum first, then FastForex with circuit breaker."""
        global _fastforex_circuit_open, _fastforex_failure_count, _fastforex_circuit_opened_at
        
        # For fiat rate requests, use Tatum
        if self.tatum_api_key and endpoint == "fetch-one":
            from_currency = params.get("from", "")
            to_currency = params.get("to", "")
            rate = await self._fetch_tatum_rate(from_currency, to_currency)
            if rate is not None:
                return {"result": {to_currency: str(rate)}}
        
        # FastForex fallback with circuit breaker
        if not self.api_key:
            return None
        
        # Circuit breaker check for FastForex
        if _fastforex_circuit_open:
            elapsed = time.time() - _fastforex_circuit_opened_at
            if elapsed < _FASTFOREX_CIRCUIT_COOLDOWN:
                logger.debug(f"FastForex circuit OPEN - skipping call ({elapsed:.0f}s / {_FASTFOREX_CIRCUIT_COOLDOWN}s cooldown)")
                return None
            else:
                logger.info("FastForex circuit half-open - attempting recovery probe")
                _fastforex_circuit_open = False
                _fastforex_failure_count = 0
        
        try:
            timeout = aiohttp.ClientTimeout(total=5)  # 5s aggressive timeout (was unlimited)
            async with aiohttp.ClientSession() as session:
                url = f"{self.base_url}/{endpoint}"
                params["api_key"] = self.api_key
                async with session.get(url, params=params, timeout=timeout) as response:
                    if response.status == 200:
                        _fastforex_failure_count = 0  # Reset on success
                        return await response.json()
                    else:
                        error_text = await response.text()
                        logger.error(f"FastForex API error: {response.status} - {error_text}")
                        # Track failures for circuit breaker
                        _fastforex_failure_count += 1
                        if _fastforex_failure_count >= _FASTFOREX_FAILURE_THRESHOLD:
                            _fastforex_circuit_open = True
                            _fastforex_circuit_opened_at = time.time()
                            logger.warning(
                                f"FastForex circuit OPENED after {_fastforex_failure_count} consecutive failures "
                                f"(status {response.status}) - will retry in {_FASTFOREX_CIRCUIT_COOLDOWN}s"
                            )
                        return None
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.warning(f"FastForex network/timeout error: {e}")
            _fastforex_failure_count += 1
            if _fastforex_failure_count >= _FASTFOREX_FAILURE_THRESHOLD:
                _fastforex_circuit_open = True
                _fastforex_circuit_opened_at = time.time()
                logger.warning(f"FastForex circuit OPENED after {_fastforex_failure_count} consecutive failures (timeout/network)")
            return None
        except Exception as e:
            logger.error(f"Error making FastForex request: {e}")
            return None

    async def get_usd_to_ngn_rate_clean(self) -> Optional[Decimal]:
        """Get USD to NGN rate with intelligent caching."""
        try:
            cache_key = "forex_rate_USD_NGN"
            cached_rate = get_cached(cache_key)
            if cached_rate is not None:
                return Decimal(str(cached_rate))
            
            # Primary: Tatum API
            tatum_rate = await self._fetch_tatum_rate("USD", "NGN")
            if tatum_rate is not None:
                set_cached(cache_key, tatum_rate, ttl=self.cache_ttl)
                logger.info(f"Fresh USD-NGN (Tatum): {float(tatum_rate):,.2f} (cached for {self.cache_ttl}s)")
                return tatum_rate
            
            # Fallback: FastForex
            if self.api_key:
                try:
                    response = await self._make_request("fetch-one", {"from": "USD", "to": "NGN"})
                    if response and response.get("result"):
                        fastforex_rate = Decimal(str(response["result"]["NGN"]))
                        set_cached(cache_key, fastforex_rate, ttl=self.cache_ttl)
                        logger.info(f"Fresh USD-NGN (FastForex fallback): {float(fastforex_rate):,.2f}")
                        return fastforex_rate
                except Exception as e:
                    logger.warning(f"FastForex fallback failed: {e}")
            
            raise FastForexAPIError("All rate sources failed for USD-NGN")

        except FastForexAPIError:
            raise
        except Exception as e:
            logger.error(f"Critical error getting USD to NGN rate: {e}")
            raise FastForexAPIError(f"USD-NGN rate error: {e}")

    async def get_usd_to_ngn_rate(self) -> Optional[Decimal]:
        """Backward compatibility — delegates to get_usd_to_ngn_rate_clean()."""
        return await self.get_usd_to_ngn_rate_clean()

    async def get_usd_to_ngn_rate_with_markup(self) -> Optional[Decimal]:
        """Get USD to NGN rate with platform markup."""
        base_rate = await self.get_usd_to_ngn_rate_clean()
        if base_rate:
            markup_percentage = Decimal(str(Config.EXCHANGE_MARKUP_PERCENTAGE))
            user_rate = base_rate * (Decimal("1") - markup_percentage / Decimal("100"))
            logger.info(f"USD->NGN: Base {float(base_rate):,.2f}, user gets {float(user_rate):,.2f} (base - {float(markup_percentage)}%)")
            return user_rate
        raise FastForexAPIError("Failed to get clean USD to NGN rate")

    async def get_usd_to_ngn_rate_with_wallet_markup(self) -> Optional[Decimal]:
        """Get USD to NGN rate with 2% wallet markup."""
        base_rate = await self.get_usd_to_ngn_rate_clean()
        if base_rate:
            markup_percentage = Decimal(str(Config.WALLET_NGN_MARKUP_PERCENTAGE))
            user_rate = base_rate * (Decimal("1") - markup_percentage / Decimal("100"))
            logger.info(f"USD->NGN Wallet: Base {float(base_rate):,.2f}, user gets {float(user_rate):,.2f} (base - {float(markup_percentage)}%)")
            return user_rate
        raise FastForexAPIError("Failed to get clean USD to NGN rate")

    async def get_ngn_to_usd_rate_with_markup(self) -> Optional[Decimal]:
        """Get NGN to USD rate with platform markup."""
        base_usd_to_ngn = await self.get_usd_to_ngn_rate_clean()
        if base_usd_to_ngn:
            base_ngn_to_usd = Decimal("1") / base_usd_to_ngn
            markup_percentage = Decimal(str(Config.EXCHANGE_MARKUP_PERCENTAGE))
            user_rate = base_ngn_to_usd * (Decimal("1") - markup_percentage / Decimal("100"))
            logger.info(f"NGN->USD: Base {float(base_ngn_to_usd):.6f}, user gets {float(user_rate):.6f} (base - {float(markup_percentage)}%)")
            return user_rate
        raise FastForexAPIError("Failed to get clean USD to NGN rate")

    async def get_ngn_to_usd_rate_with_wallet_markup(self) -> Optional[Decimal]:
        """Get NGN to USD rate with 2% wallet markup."""
        base_usd_to_ngn = await self.get_usd_to_ngn_rate_clean()
        if base_usd_to_ngn:
            base_ngn_to_usd = Decimal("1") / base_usd_to_ngn
            markup_percentage = Decimal(str(Config.WALLET_NGN_MARKUP_PERCENTAGE))
            user_rate = base_ngn_to_usd * (Decimal("1") - markup_percentage / Decimal("100"))
            logger.info(f"NGN->USD Wallet: Base {float(base_ngn_to_usd):.6f}, user gets {float(user_rate):.6f} (base - {float(markup_percentage)}%)")
            return user_rate
        raise FastForexAPIError("Failed to get clean USD to NGN rate")

    async def convert_usd_to_ngn_with_markup(self, usd_amount: Decimal) -> Optional[Decimal]:
        """Convert USD to NGN with platform markup."""
        rate = await self.get_usd_to_ngn_rate_with_markup()
        if rate:
            ngn_amount = usd_amount * rate
            logger.info(f"User selling ${float(usd_amount):.2f} USD gets {float(ngn_amount):,.2f} NGN at rate {float(rate):,.2f}")
            return ngn_amount
        return None

    async def convert_ngn_to_usd_with_markup(self, ngn_amount: Decimal) -> Optional[Decimal]:
        """Convert NGN to USD with platform markup."""
        rate = await self.get_ngn_to_usd_rate_with_markup()
        if rate:
            usd_amount = ngn_amount * rate
            logger.info(f"User selling {float(ngn_amount):,.2f} NGN gets ${float(usd_amount):.2f} USD at rate {float(rate):.6f}")
            return usd_amount
        return None

    def invalidate_rate_cache(self, crypto_symbol: Optional[str] = None, force_all: bool = False):
        """Invalidate cached rates."""
        if force_all:
            rate_keys = [
                "crypto_rate_BTC_USD", "crypto_rate_ETH_USD", "crypto_rate_LTC_USD",
                "crypto_rate_DOGE_USD", "crypto_rate_USDT-ERC20_USD", "crypto_rate_USDT-TRC20_USD",
                "crypto_rate_XETH_USD", "crypto_rate_XXBT_USD", "crypto_rate_XLTC_USD",
                "crypto_rate_XXDG_USD", "crypto_rate_XUSDT_USD",
                "forex_rate_USD_NGN"
            ]
            for key in rate_keys:
                delete_cached(key)
            logger.info("Cleared all exchange rate caches")
        elif crypto_symbol:
            cache_key = f"crypto_rate_{crypto_symbol}_USD"
            delete_cached(cache_key)
            logger.info(f"Cleared cache for {crypto_symbol}")

    async def get_crypto_to_usd_rate_webhook_optimized(self, crypto_symbol: str) -> Optional[Decimal]:
        """WEBHOOK OPTIMIZATION: Get crypto rate from cache only — no API calls."""
        try:
            mapped_symbol = self._map_symbol(crypto_symbol)
            if mapped_symbol == "USD":
                return Decimal("1.0")
            
            for prefix in ["crypto_rate_", "rapid_crypto_rate_", "fallback_crypto_rate_"]:
                cache_key = f"{prefix}{mapped_symbol}_USD"
                cached_rate = get_cached(cache_key)
                if cached_rate is not None:
                    return Decimal(str(cached_rate))
            
            logger.error(f"WEBHOOK_CACHE_MISS: No cached rate for {crypto_symbol} (mapped to {mapped_symbol})")
            return None
        except Exception as e:
            logger.error(f"WEBHOOK_RATE_ERROR: {crypto_symbol} - {e}")
            return None

    async def get_ngn_rate_webhook_optimized(self) -> Optional[Decimal]:
        """WEBHOOK OPTIMIZATION: Get NGN rate from cache only — no API calls."""
        try:
            for key in ["forex_rate_USD_NGN", "fallback_forex_rate_USD_NGN"]:
                cached_rate = get_cached(key)
                if cached_rate is not None:
                    return Decimal(str(cached_rate))
            logger.error("WEBHOOK_NGN_CACHE_MISS: No cached NGN rate")
            return None
        except Exception as e:
            logger.error(f"WEBHOOK_NGN_ERROR: {e}")
            return None

    async def warm_cache(self):
        """Pre-warm cache with commonly requested rates."""
        try:
            common_symbols = [
                "BTC", "ETH", "LTC", "DOGE", "USDT", "BCH", "TRX", "XRP",
            ]
            logger.info(f"Warming cache for {len(common_symbols)} crypto rates via Tatum...")
            
            tasks = []
            for symbol in common_symbols:
                task = asyncio.create_task(self.get_crypto_to_usd_rate(symbol))
                tasks.append((symbol, task))
            
            ngn_task = asyncio.create_task(self.get_usd_to_ngn_rate_clean())
            
            warmed_count = 0
            for symbol, task in tasks:
                try:
                    rate = await task
                    warmed_count += 1
                    set_cached(f"fallback_crypto_rate_{symbol}_USD", rate, ttl=self.fallback_cache_ttl)
                except Exception as e:
                    logger.warning(f"Failed to warm cache for {symbol}: {e}")
            
            try:
                ngn_rate = await ngn_task
                if ngn_rate is not None:
                    warmed_count += 1
                    set_cached("fallback_forex_rate_USD_NGN", ngn_rate, ttl=self.fallback_cache_ttl)
            except Exception as e:
                logger.warning(f"Failed to warm USD-NGN cache: {e}")
            
            logger.info(f"Cache warming complete: {warmed_count}/{len(common_symbols) + 1} rates cached")
        except Exception as e:
            logger.error(f"Cache warming failed: {e}")


# Global instance
fastforex_service = FastForexService()


async def warm_fastforex_cache():
    """Convenience function to warm cache."""
    await fastforex_service.warm_cache()


async def startup_prewarm_critical_rates():
    """Pre-warm critical crypto rates on startup before webhooks are processed."""
    from jobs.crypto_rate_background_refresh import CryptoRateBackgroundRefresh
    
    logger.info("STARTUP_PREWARM: Fetching critical crypto rates via Tatum...")
    start_time = time.time()
    
    try:
        critical_currencies = CryptoRateBackgroundRefresh.WEBHOOK_CRITICAL_CURRENCIES
        success_count = 0
        error_count = 0
        
        tasks = []
        for currency in critical_currencies:
            task = asyncio.create_task(fastforex_service.get_crypto_to_usd_rate(currency))
            tasks.append((currency, task))
        
        ngn_task = asyncio.create_task(fastforex_service.get_usd_to_ngn_rate_clean())
        
        for currency, task in tasks:
            try:
                rate = await task
                if rate is not None:
                    success_count += 1
                    set_cached(f"crypto_rate_{currency}_USD", rate, ttl=fastforex_service.cache_ttl)
                    set_cached(f"fallback_crypto_rate_{currency}_USD", rate, ttl=7200)
                else:
                    error_count += 1
            except Exception as e:
                error_count += 1
                logger.error(f"PREWARM_ERROR: {currency} - {e}")
        
        try:
            ngn_rate = await ngn_task
            if ngn_rate:
                success_count += 1
                set_cached("forex_rate_USD_NGN", ngn_rate, ttl=fastforex_service.cache_ttl)
                set_cached("fallback_forex_rate_USD_NGN", ngn_rate, ttl=7200)
            else:
                error_count += 1
        except Exception as e:
            error_count += 1
            logger.error(f"PREWARM_NGN_ERROR: {e}")
        
        duration = time.time() - start_time
        total = len(critical_currencies) + 1
        logger.info(f"STARTUP_PREWARM_COMPLETE: {success_count}/{total} rates cached in {duration:.2f}s (Errors: {error_count})")
        return success_count > 0
    except Exception as e:
        duration = time.time() - start_time
        logger.error(f"STARTUP_PREWARM_FAILED: Critical error after {duration:.2f}s - {e}")
        return False


async def emergency_fetch_rate_with_circuit_breaker(currency: str) -> Optional[Decimal]:
    """Emergency fallback: attempt live rate fetch when ALL caches are empty."""
    try:
        from services.circuit_breaker import circuit_breakers, CircuitBreaker
        
        breaker_name = "fastforex_emergency"
        if breaker_name not in circuit_breakers:
            circuit_breakers[breaker_name] = CircuitBreaker(
                name=breaker_name, failure_threshold=3, recovery_timeout=60,
                expected_exception=FastForexAPIError
            )
        
        breaker = circuit_breakers[breaker_name]
        breaker_state = await breaker.get_state()
        if breaker_state['state'] == "open":
            logger.warning(f"EMERGENCY_FETCH_BLOCKED: Circuit breaker open for {currency}")
            return None
        
        logger.warning(f"EMERGENCY_FETCH: Attempting live rate fetch for {currency}")
        try:
            rate = await asyncio.wait_for(
                fastforex_service.get_crypto_to_usd_rate(currency),
                timeout=5.0
            )
            if rate is not None:
                logger.info(f"EMERGENCY_SUCCESS: Fetched {currency} = ${float(rate):.4f}")
                set_cached(f"crypto_rate_{currency}_USD", rate, ttl=fastforex_service.cache_ttl)
                set_cached(f"fallback_crypto_rate_{currency}_USD", rate, ttl=7200)
                await breaker._on_success()
                return rate
            else:
                await breaker._on_failure()
                return None
        except asyncio.TimeoutError:
            logger.error(f"EMERGENCY_TIMEOUT: Rate fetch timeout for {currency}")
            await breaker._on_failure()
            return None
        except Exception as fetch_error:
            logger.error(f"EMERGENCY_ERROR: Rate fetch failed for {currency} - {fetch_error}")
            await breaker._on_failure()
            return None
    except Exception as e:
        logger.error(f"EMERGENCY_CRITICAL: Circuit breaker error - {e}")
        return None
