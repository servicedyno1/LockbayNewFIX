"""
Background Crypto Rate Refresh Job
Continuously refreshes crypto rates to ensure webhook handlers never need to make API calls
This eliminates the 600ms-3000ms delays in webhook response paths
"""

import logging
import asyncio
from datetime import datetime
from typing import Optional
from services.fastforex_service import fastforex_service
from utils.production_cache import set_cached

logger = logging.getLogger(__name__)

class CryptoRateBackgroundRefresh:
    """Background service to keep crypto rates fresh for webhook optimization"""
    
    # All cryptocurrencies that need to be kept fresh for webhook performance
    WEBHOOK_CRITICAL_CURRENCIES = [
        # Standard symbols frequently used in webhooks
        "BTC", "ETH", "LTC", "DOGE", "BCH", "BSC", "TRX", 
        "USDT-ERC20", "USDT-TRC20", "USD",
        
        # Kraken symbols (mapped internally but need caching)
        "XETH", "XXBT", "XLTC", "XXDG", "XBCH", "XTRX", "XUSDT", "ZUSD"
    ]
    
    @staticmethod
    async def refresh_crypto_rates_for_webhooks():
        """
        WEBHOOK OPTIMIZATION: Refresh all critical crypto rates in background
        This ensures webhook handlers always have fresh cached data available
        """
        start_time = datetime.now()
        logger.info("🔄 WEBHOOK_RATE_REFRESH: Starting background crypto rate refresh...")
        
        try:
            refresh_count = 0
            error_count = 0
            
            # Refresh all critical currencies concurrently
            tasks = []
            for currency in CryptoRateBackgroundRefresh.WEBHOOK_CRITICAL_CURRENCIES:
                task = asyncio.create_task(
                    CryptoRateBackgroundRefresh._refresh_single_rate(currency)
                )
                tasks.append((currency, task))
            
            # Wait for all refreshes to complete
            for currency, task in tasks:
                try:
                    rate = await task
                    if rate is not None:
                        refresh_count += 1
                        logger.debug(f"✅ REFRESHED: {currency} = ${rate:.4f}")
                        
                        # WEBHOOK OPTIMIZATION: Store in extended fallback cache
                        fallback_key = f"fallback_crypto_rate_{currency}_USD"
                        set_cached(fallback_key, rate, ttl=7200)  # 2 hours fallback
                    else:
                        error_count += 1
                        logger.warning(f"⚠️ FAILED: Could not refresh {currency}")
                except Exception as e:
                    error_count += 1
                    logger.error(f"❌ ERROR refreshing {currency}: {e}")
            
            # Also refresh NGN rate for fiat conversions
            try:
                ngn_rate = await fastforex_service.get_usd_to_ngn_rate_clean()
                if ngn_rate:
                    refresh_count += 1
                    # Store in extended fallback cache  
                    set_cached("fallback_forex_rate_USD_NGN", ngn_rate, ttl=7200)
                    logger.debug(f"✅ REFRESHED: USD-NGN = ₦{ngn_rate:,.2f}")
                else:
                    error_count += 1
            except Exception as e:
                error_count += 1
                logger.error(f"❌ ERROR refreshing NGN rate: {e}")
            
            duration = (datetime.now() - start_time).total_seconds()
            logger.info(
                f"✅ WEBHOOK_RATE_REFRESH: Completed in {duration:.2f}s - "
                f"Refreshed: {refresh_count}, Errors: {error_count}"
            )
            
        except Exception as e:
            duration = (datetime.now() - start_time).total_seconds()
            logger.error(f"❌ WEBHOOK_RATE_REFRESH: Failed after {duration:.2f}s - {e}")
    
    @staticmethod
    async def _refresh_single_rate(currency: str) -> Optional[float]:
        """Refresh a single currency rate"""
        try:
            # Use the standard API method (not webhook-optimized) to actually fetch fresh data
            rate = await fastforex_service.get_crypto_to_usd_rate(currency)
            return rate
        except Exception as e:
            logger.debug(f"Failed to refresh {currency}: {e}")
            return None

# Convenience function for job scheduler
async def run_crypto_rate_background_refresh():
    """Entry point for APScheduler job"""
    await CryptoRateBackgroundRefresh.refresh_crypto_rates_for_webhooks()