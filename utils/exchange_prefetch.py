#!/usr/bin/env python3
"""
Exchange Rate Fallback System
Comprehensive multi-tier rate fetching with circuit breaker and stale data tolerance
"""

import logging
import aiohttp
import time
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Any, Tuple
from decimal import Decimal
from dataclasses import dataclass
from enum import Enum

from database import SessionLocal
from config import Config
from utils.production_cache import get_cached, set_cached

logger = logging.getLogger(__name__)

@dataclass
class PrefetchData:
    """Container for prefetched exchange context"""
    user_id: int
    wallets: list
    saved_destinations: list
    prefetch_duration_ms: float
    timestamp: datetime = datetime.utcnow()

async def prefetch_exchange_context(user_id: int, session: Any) -> Optional[PrefetchData]:
    """Prefetch all data needed for exchange operations in parallel"""
    start_time = time.time()
    try:
        from models import Wallet, SavedAddress, SavedBankAccount
        from sqlalchemy import select
        
        # Parallel fetch wallets and destinations
        wallets_stmt = select(Wallet).where(Wallet.user_id == user_id)
        addresses_stmt = select(SavedAddress).where(SavedAddress.user_id == user_id)
        banks_stmt = select(SavedBankAccount).where(SavedBankAccount.user_id == user_id)
        
        results = await asyncio.gather(
            session.execute(wallets_stmt),
            session.execute(addresses_stmt),
            session.execute(banks_stmt),
            return_exceptions=True
        )
        
        if any(isinstance(r, Exception) for r in results):
            for r in results:
                if isinstance(r, Exception):
                    logger.error(f"Prefetch error: {r}")
            return None
            
        wallets = [r[0] for r in results[0].all()]
        addresses = [r[0] for r in results[1].all()]
        banks = [r[0] for r in results[2].all()]
        
        duration = (time.time() - start_time) * 1000
        return PrefetchData(
            user_id=user_id,
            wallets=wallets,
            saved_destinations=addresses + banks,
            prefetch_duration_ms=duration
        )
    except Exception as e:
        logger.error(f"Critical error in prefetch: {e}")
        return None

def get_cached_exchange_data(user_data: dict) -> Optional[PrefetchData]:
    """Get cached prefetch data if valid"""
    if not user_data: return None
    data = user_data.get("_exchange_prefetch")
    if data and (datetime.utcnow() - data.timestamp).total_seconds() < 300:
        return data
    return None

def cache_exchange_data(user_data: dict, data: PrefetchData):
    """Cache prefetch data in user context"""
    if user_data:
        user_data["_exchange_prefetch"] = data

def invalidate_exchange_cache(user_data: dict):
    """Clear cached prefetch data"""
    if user_data:
        user_data.pop("_exchange_prefetch", None)

class RateSource(Enum):
    """Rate source providers"""

    TATUM = "tatum"
    FASTFOREX = "fastforex"
    COINGECKO = "coingecko"
    COINAPI = "coinapi"
    EXCHANGERATES = "exchangerates"
    CACHE = "cache"
    STALE_CACHE = "stale_cache"
    DEFAULT = "default"


@dataclass
class RateResult:
    """Rate fetch result with metadata"""

    rate: Optional[Decimal]
    source: RateSource
    timestamp: datetime
    currency_pair: str
    confidence: float  # 0.0 to 1.0
    is_stale: bool = False
    error_message: Optional[str] = None


@dataclass
class CircuitBreakerState:
    """Circuit breaker state for API endpoints"""

    failure_count: int = 0
    last_failure: Optional[datetime] = None
    is_open: bool = False
    recovery_timeout: int = 300  # 5 minutes
    failure_threshold: int = 3


class ExchangeRateFallbackService:
    """Comprehensive exchange rate service with multiple fallbacks and circuit breakers"""

    def __init__(self):
        self.cache_duration = 300  # 5 minutes normal cache
        self.stale_tolerance = 3600  # 1 hour stale data tolerance
        self.default_rates = self._load_default_rates()
        self.circuit_breakers: Dict[str, CircuitBreakerState] = {}

        # Rate source configurations
        self.sources_config = {
            RateSource.TATUM: {"timeout": 10, "retry_count": 2, "confidence": 0.95},
            RateSource.FASTFOREX: {"timeout": 10, "retry_count": 2, "confidence": 0.90},
            RateSource.COINGECKO: {"timeout": 8, "retry_count": 2, "confidence": 0.85},
            RateSource.COINAPI: {"timeout": 12, "retry_count": 1, "confidence": 0.85},
            RateSource.EXCHANGERATES: {
                "timeout": 10,
                "retry_count": 1,
                "confidence": 0.80,
            },
        }

    def _load_default_rates(self) -> Dict[str, Decimal]:
        """Load conservative default rates for emergency fallback"""
        return {
            # Crypto to USD (conservative estimates)
            "BTC": Decimal("35000.00"),
            "ETH": Decimal("2000.00"),
            "LTC": Decimal("70.00"),
            "DOGE": Decimal("0.08"),
            "BCH": Decimal("250.00"),
            "BNB": Decimal("220.00"),
            "TRX": Decimal("0.06"),
            "USDT": Decimal("1.00"),
            # USD to Fiat
            "USD_NGN": Decimal("1600.00"),  # Conservative NGN rate
            "USD_EUR": Decimal("0.85"),
            "USD_GBP": Decimal("0.75"),
        }

    def _get_circuit_breaker(self, source: str) -> CircuitBreakerState:
        """Get or create circuit breaker for a source"""
        if source not in self.circuit_breakers:
            self.circuit_breakers[source] = CircuitBreakerState()
        return self.circuit_breakers[source]

    def _is_circuit_open(self, source: str) -> bool:
        """Check if circuit breaker is open for a source"""
        cb = self._get_circuit_breaker(source)

        if not cb.is_open:
            return False

        # Check if recovery timeout has passed
        if cb.last_failure and datetime.utcnow() - cb.last_failure > timedelta(
            seconds=cb.recovery_timeout
        ):
            cb.is_open = False
            cb.failure_count = 0
            logger.info(f"Circuit breaker reset for {source}")
            return False

        return True

    def _record_failure(self, source: str, error: str):
        """Record API failure and potentially open circuit breaker"""
        cb = self._get_circuit_breaker(source)
        cb.failure_count += 1
        cb.last_failure = datetime.utcnow()

        if cb.failure_count >= cb.failure_threshold:
            cb.is_open = True
            logger.warning(
                f"Circuit breaker opened for {source} after {cb.failure_count} failures"
            )

        logger.warning(f"API failure recorded for {source}: {error}")

    def _record_success(self, source: str):
        """Record API success and reset circuit breaker"""
        cb = self._get_circuit_breaker(source)
        if cb.failure_count > 0 or cb.is_open:
            logger.info(f"API recovery for {source}, resetting circuit breaker")

        cb.failure_count = 0
        cb.is_open = False
        cb.last_failure = None

    async def get_crypto_to_usd_rate(self, crypto: str) -> RateResult:
        """Get crypto to USD rate with comprehensive fallbacks"""
        currency_pair = f"{crypto.upper()}_USD"

        # Try multiple sources in order of preference
        sources = [
            (RateSource.TATUM, self._fetch_tatum_crypto_rate),
            (RateSource.FASTFOREX, self._fetch_fastforex_crypto_rate),
            (RateSource.COINAPI, self._fetch_coinapi_crypto_rate),
        ]

        for source, fetch_func in sources:
            if self._is_circuit_open(source.value):
                logger.debug(f"Skipping {source.value} - circuit breaker open")
                continue

            try:
                result = await fetch_func(crypto)
                if result and result.rate:
                    self._record_success(source.value)
                    await self._cache_rate(currency_pair, result.rate, source)
                    return result
            except Exception as e:
                self._record_failure(source.value, str(e))
                continue

        # Try cached data (fresh then stale)
        cached_result = await self._get_cached_rate(currency_pair)
        if cached_result:
            return cached_result

        # Last resort: default rate
        default_rate = self.default_rates.get(crypto.upper())
        if default_rate:
            logger.warning(f"Using default rate for {crypto}: ${default_rate}")
            return RateResult(
                rate=default_rate,
                source=RateSource.DEFAULT,
                timestamp=datetime.utcnow(),
                currency_pair=currency_pair,
                confidence=0.5,
                is_stale=True,
                error_message="All APIs failed, using default rate",
            )

        # Complete failure
        return RateResult(
            rate=None,
            source=RateSource.DEFAULT,
            timestamp=datetime.utcnow(),
            currency_pair=currency_pair,
            confidence=0.0,
            error_message="All rate sources failed",
        )

    async def get_usd_to_ngn_rate(self) -> RateResult:
        """Get USD to NGN rate with comprehensive fallbacks"""
        currency_pair = "USD_NGN"

        # Try multiple sources
        sources = [
            (RateSource.TATUM, self._fetch_tatum_usd_ngn),
            (RateSource.FASTFOREX, self._fetch_fastforex_usd_ngn),
            (RateSource.EXCHANGERATES, self._fetch_exchangerates_usd_ngn),
        ]

        for source, fetch_func in sources:
            if self._is_circuit_open(source.value):
                continue

            try:
                result = await fetch_func()
                if result and result.rate:
                    self._record_success(source.value)
                    await self._cache_rate(currency_pair, result.rate, source)
                    return result
            except Exception as e:
                self._record_failure(source.value, str(e))
                continue

        # Try cached data
        cached_result = await self._get_cached_rate(currency_pair)
        if cached_result:
            return cached_result

        # Default NGN rate
        default_rate = self.default_rates.get("USD_NGN")
        if default_rate:
            logger.warning(f"Using default USD to NGN rate: ₦{default_rate}")
            return RateResult(
                rate=default_rate,
                source=RateSource.DEFAULT,
                timestamp=datetime.utcnow(),
                currency_pair=currency_pair,
                confidence=0.6,  # Higher confidence for fiat rates
                is_stale=True,
                error_message="All APIs failed, using default rate",
            )

        return RateResult(
            rate=None,
            source=RateSource.DEFAULT,
            timestamp=datetime.utcnow(),
            currency_pair=currency_pair,
            confidence=0.0,
            error_message="All USD to NGN sources failed",
        )

    async def _fetch_tatum_crypto_rate(self, crypto: str) -> Optional[RateResult]:
        """Fetch crypto rate from Tatum API (primary source)"""
        tatum_key = Config.TATUM_API_KEY
        if not tatum_key:
            return None
        try:
            url = "https://api.tatum.io/v4/data/rate/symbol"
            params = {"symbol": crypto.upper(), "basePair": "USD"}
            headers = {"x-api-key": tatum_key}
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            ) as session:
                async with session.get(url, params=params, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        if "value" in data:
                            rate = Decimal(str(data["value"]))
                            return RateResult(
                                rate=rate, source=RateSource.TATUM,
                                timestamp=datetime.utcnow(),
                                currency_pair=f"{crypto.upper()}_USD", confidence=0.95,
                            )
        except Exception as e:
            logger.error(f"Tatum crypto rate error: {e}")
            raise
        return None

    async def _fetch_tatum_usd_ngn(self) -> Optional[RateResult]:
        """Fetch USD to NGN rate from Tatum API"""
        tatum_key = Config.TATUM_API_KEY
        if not tatum_key:
            return None
        try:
            url = "https://api.tatum.io/v4/data/rate/symbol"
            params = {"symbol": "USD", "basePair": "NGN"}
            headers = {"x-api-key": tatum_key}
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            ) as session:
                async with session.get(url, params=params, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        if "value" in data:
                            rate = Decimal(str(data["value"]))
                            return RateResult(
                                rate=rate, source=RateSource.TATUM,
                                timestamp=datetime.utcnow(),
                                currency_pair="USD_NGN", confidence=0.95,
                            )
        except Exception as e:
            logger.error(f"Tatum USD to NGN error: {e}")
            raise
        return None

    async def _fetch_fastforex_crypto_rate(self, crypto: str) -> Optional[RateResult]:
        """Fetch crypto rate from FastForex API (legacy fallback)"""
        if not Config.FASTFOREX_API_KEY:
            return None

        try:
            url = "https://api.fastforex.io/convert"
            params = {
                "api_key": Config.FASTFOREX_API_KEY,
                "from": crypto.upper(),
                "to": "USD",
                "amount": "1",
            }

            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            ) as session:
                async with session.get(url, params=params) as response:
                    if response.status == 200:
                        data = await response.json()
                        if "result" in data and "USD" in data["result"]:
                            rate = Decimal(str(data["result"]["USD"]))
                            return RateResult(
                                rate=rate,
                                source=RateSource.FASTFOREX,
                                timestamp=datetime.utcnow(),
                                currency_pair=f"{crypto.upper()}_USD",
                                confidence=0.95,
                            )
        except Exception as e:
            logger.error(f"FastForex crypto rate error: {e}")
            raise

        return None

    async def _fetch_coingecko_crypto_rate(self, crypto: str) -> Optional[RateResult]:
        """Fetch crypto rate from CoinGecko API"""
        try:
            # Map crypto symbols to CoinGecko IDs
            crypto_ids = {
                "BTC": "bitcoin",
                "ETH": "ethereum",
                "LTC": "litecoin",
                "DOGE": "dogecoin",
                "BCH": "bitcoin-cash",
                "BNB": "binancecoin",
                "TRX": "tron",
                "USDT": "tether",
            }

            crypto_id = crypto_ids.get(crypto.upper())
            if not crypto_id:
                return None

            url = "https://api.coingecko.com/api/v3/simple/price"
            params = {"ids": crypto_id, "vs_currencies": "usd"}

            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=8)
            ) as session:
                async with session.get(url, params=params) as response:
                    if response.status == 200:
                        data = await response.json()
                        if crypto_id in data and "usd" in data[crypto_id]:
                            rate = Decimal(str(data[crypto_id]["usd"]))
                            return RateResult(
                                rate=rate,
                                source=RateSource.COINGECKO,
                                timestamp=datetime.utcnow(),
                                currency_pair=f"{crypto.upper()}_USD",
                                confidence=0.90,
                            )
        except Exception as e:
            logger.error(f"CoinGecko crypto rate error: {e}")
            raise

        return None

    async def _fetch_coinapi_crypto_rate(self, crypto: str) -> Optional[RateResult]:
        """Fetch crypto rate from CoinAPI (if API key available)"""
        api_key = getattr(Config, "COINAPI_KEY", None)
        if not api_key:
            return None

        try:
            url = f"https://rest.coinapi.io/v1/exchangerate/{crypto.upper()}/USD"
            headers = {"X-CoinAPI-Key": api_key}

            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=12)
            ) as session:
                async with session.get(url, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        if "rate" in data:
                            rate = Decimal(str(data["rate"]))
                            return RateResult(
                                rate=rate,
                                source=RateSource.COINAPI,
                                timestamp=datetime.utcnow(),
                                currency_pair=f"{crypto.upper()}_USD",
                                confidence=0.85,
                            )
        except Exception as e:
            logger.error(f"CoinAPI crypto rate error: {e}")
            raise

        return None

    async def _fetch_fastforex_usd_ngn(self) -> Optional[RateResult]:
        """Fetch USD to NGN rate from FastForex"""
        if not Config.FASTFOREX_API_KEY:
            return None

        try:
            url = "https://api.fastforex.io/convert"
            params = {
                "api_key": Config.FASTFOREX_API_KEY,
                "from": "USD",
                "to": "NGN",
                "amount": "1",
            }

            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            ) as session:
                async with session.get(url, params=params) as response:
                    if response.status == 200:
                        data = await response.json()
                        if "result" in data and "NGN" in data["result"]:
                            rate = Decimal(str(data["result"]["NGN"]))
                            return RateResult(
                                rate=rate,
                                source=RateSource.FASTFOREX,
                                timestamp=datetime.utcnow(),
                                currency_pair="USD_NGN",
                                confidence=0.95,
                            )
        except Exception as e:
            logger.error(f"FastForex USD to NGN error: {e}")
            raise

        return None

    async def _fetch_exchangerates_usd_ngn(self) -> Optional[RateResult]:
        """Fetch USD to NGN rate from ExchangeRates API (backup)"""
        try:
            url = "https://api.exchangerate-api.com/v4/latest/USD"

            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            ) as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        if "rates" in data and "NGN" in data["rates"]:
                            rate = Decimal(str(data["rates"]["NGN"]))
                            return RateResult(
                                rate=rate,
                                source=RateSource.EXCHANGERATES,
                                timestamp=datetime.utcnow(),
                                currency_pair="USD_NGN",
                                confidence=0.80,
                            )
        except Exception as e:
            logger.error(f"ExchangeRates USD to NGN error: {e}")
            raise

        return None

    async def _cache_rate(self, currency_pair: str, rate: Decimal, source: RateSource):
        """Cache rate in production cache system and database for fallback"""
        try:
            # Update production cache for fast access
            set_cached(f"fallback_rate_{currency_pair}", float(rate), ttl=self.cache_duration)
            
            # Import here to avoid circular dependency
            from models import ExchangeRateCache

            with SessionLocal() as session:
                # Remove old cache entries for this pair
                session.query(ExchangeRateCache).filter(
                    ExchangeRateCache.currency_pair == currency_pair
                ).delete()

                # Add new cache entry
                cache_entry = ExchangeRateCache(
                    currency_pair=currency_pair,
                    rate=rate,
                    source=source.value,
                    cached_at=datetime.utcnow(),
                    expires_at=datetime.utcnow()
                    + timedelta(seconds=self.cache_duration),
                )
                session.add(cache_entry)
                session.commit()

                logger.debug(f"Cached rate {currency_pair}: {rate} from {source.value}")
        except Exception as e:
            logger.error(f"Error caching rate: {e}")

    async def _get_cached_rate(self, currency_pair: str) -> Optional[RateResult]:
        """Get cached rate from production cache or database"""
        try:
            # Try production cache first (fastest)
            cached_val = get_cached(f"fallback_rate_{currency_pair}")
            if cached_val is not None:
                return RateResult(
                    rate=Decimal(str(cached_val)),
                    source=RateSource.CACHE,
                    timestamp=datetime.utcnow(),
                    currency_pair=currency_pair,
                    confidence=0.85,
                    is_stale=False,
                )

            from models import ExchangeRateCache

            with SessionLocal() as session:
                now = datetime.utcnow()

                # Try fresh cache first
                fresh_cache = (
                    session.query(ExchangeRateCache)
                    .filter(
                        ExchangeRateCache.currency_pair == currency_pair,
                        ExchangeRateCache.expires_at > now,
                    )
                    .first()
                )

                if fresh_cache:
                    logger.debug(f"Using fresh cached rate for {currency_pair}")
                    return RateResult(
                        rate=(
                            Decimal(str(fresh_cache.rate)) if fresh_cache.rate else None
                        ),
                        source=RateSource.CACHE,
                        timestamp=(
                            datetime.fromisoformat(str(fresh_cache.cached_at))
                            if fresh_cache.cached_at
                            else datetime.now(timezone.utc)
                        ),
                        currency_pair=currency_pair,
                        confidence=0.85,
                        is_stale=False,
                    )

                # Try stale cache within tolerance
                stale_cache = (
                    session.query(ExchangeRateCache)
                    .filter(
                        ExchangeRateCache.currency_pair == currency_pair,
                        ExchangeRateCache.cached_at
                        > now - timedelta(seconds=self.stale_tolerance),
                    )
                    .first()
                )

                if stale_cache:
                    logger.warning(f"Using stale cached rate for {currency_pair}")
                    return RateResult(
                        rate=(
                            Decimal(str(stale_cache.rate)) if stale_cache.rate else None
                        ),
                        source=RateSource.STALE_CACHE,
                        timestamp=(
                            datetime.fromisoformat(str(stale_cache.cached_at))
                            if stale_cache.cached_at
                            else datetime.now(timezone.utc)
                        ),
                        currency_pair=currency_pair,
                        confidence=0.70,
                        is_stale=True,
                    )
        except Exception as e:
            logger.error(f"Error getting cached rate: {e}")

        return None

    async def get_rate_with_confidence(
        self, crypto: str, target_currency: str = "USD"
    ) -> Tuple[Optional[Decimal], float, str]:
        """Get rate with confidence score and source info for UI display"""
        if target_currency == "USD":
            result = await self.get_crypto_to_usd_rate(crypto)
        elif target_currency == "NGN":
            # Get crypto->USD then USD->NGN
            crypto_result = await self.get_crypto_to_usd_rate(crypto)
            if not crypto_result.rate:
                return None, 0.0, "Crypto rate unavailable"

            ngn_result = await self.get_usd_to_ngn_rate()
            if not ngn_result.rate:
                return None, 0.0, "NGN rate unavailable"

            # Combine rates and confidence scores
            combined_rate = crypto_result.rate * ngn_result.rate
            combined_confidence = min(crypto_result.confidence, ngn_result.confidence)
            combined_source = f"{crypto_result.source.value}/{ngn_result.source.value}"

            return combined_rate, combined_confidence, combined_source
        else:
            return None, 0.0, f"Unsupported target currency: {target_currency}"

        if result.rate:
            status = "stale" if result.is_stale else "live"
            return result.rate, result.confidence, f"{status} ({result.source.value})"
        else:
            return None, 0.0, result.error_message or "Rate unavailable"

    async def health_check(self) -> Dict[str, Any]:
        """Check health of all rate sources"""
        health_status = {
            "timestamp": datetime.utcnow().isoformat(),
            "sources": {},
            "circuit_breakers": {},
        }

        # Check each source
        for source in [
            RateSource.TATUM,
            RateSource.FASTFOREX,
            RateSource.EXCHANGERATES,
        ]:
            cb = self._get_circuit_breaker(source.value)
            health_status["circuit_breakers"][source.value] = {
                "is_open": cb.is_open,
                "failure_count": cb.failure_count,
                "last_failure": (
                    cb.last_failure.isoformat() if cb.last_failure else None
                ),
            }

        # Test a sample rate fetch
        try:
            btc_result = await self.get_crypto_to_usd_rate("BTC")
            health_status["sample_btc_rate"] = {
                "rate": float(btc_result.rate) if btc_result.rate else None,
                "source": btc_result.source.value,
                "confidence": btc_result.confidence,
                "is_stale": btc_result.is_stale,
            }
        except Exception as e:
            health_status["sample_btc_rate"] = {"error": str(e)}

        return health_status


# Global instance
exchange_rate_fallback_service = ExchangeRateFallbackService()

# Backward compatibility alias
rate_service = exchange_rate_fallback_service
