"""
API Performance Integration Helper
Provides optimized patterns for integrating external API services with performance enhancements
"""

import logging
from typing import Dict, Any, Callable, List
from services.external_api_optimizer import get_api_optimizer

logger = logging.getLogger(__name__)


class APIPerformanceIntegrator:
    """
    Helper class for integrating external API services with performance optimizations
    Provides common patterns for all external API services
    """
    
    def __init__(self, service_name: str):
        self.service_name = service_name
        self.optimizer = None
        
    async def _get_optimizer(self):
        """Get the optimizer instance lazily"""
        if self.optimizer is None:
            self.optimizer = await get_api_optimizer()
        return self.optimizer
        
    async def cached_rate_call(
        self,
        cache_key: str,
        api_call_func: Callable,
        cache_ttl: int = 600,
        force_refresh: bool = False
    ) -> Any:
        """
        Execute rate API call with intelligent caching
        Optimized for cryptocurrency and forex rate calls
        """
        optimizer = await self._get_optimizer()
        return await optimizer.cached_api_call(
            service_name=self.service_name,
            cache_key=cache_key,
            api_call_func=api_call_func,
            cache_ttl=cache_ttl,
            force_refresh=force_refresh
        )
    
    async def batch_rate_calls(
        self,
        symbols: List[str],
        rate_func: Callable[[str], Any],
        max_concurrent: int = 5
    ) -> Dict[str, Any]:
        """
        Execute multiple rate calls concurrently with controlled concurrency
        Optimized for fetching multiple cryptocurrency/forex rates
        """
        optimizer = await self._get_optimizer()
        
        # Prepare batch calls
        calls = []
        for symbol in symbols:
            calls.append({
                'func': lambda s=symbol: rate_func(s),
                'cache_key': f"{self.service_name}_rate_{symbol}",
                'cache_ttl': 300  # 5 minutes for rate data
            })
        
        results = await optimizer.batch_api_calls(
            service_name=self.service_name,
            calls=calls,
            max_concurrent=max_concurrent
        )
        
        # Map results back to symbols
        rate_results = {}
        for i, symbol in enumerate(symbols):
            if i < len(results) and not isinstance(results[i], Exception):
                rate_results[symbol] = results[i]
            else:
                logger.warning(f"Failed to get rate for {symbol}")
                
        return rate_results
    
    async def rate_limited_payment_call(
        self,
        api_call_func: Callable,
        calls_per_second: float = 5.0
    ) -> Any:
        """
        Execute payment-related API call with rate limiting
        Optimized for payment processor calls (slower rate limit)
        """
        optimizer = await self._get_optimizer()
        return await optimizer.rate_limited_call(
            service_name=self.service_name,
            api_call_func=api_call_func,
            calls_per_second=calls_per_second
        )
    
    def get_performance_stats(self) -> Dict[str, Any]:
        """Get performance statistics for this service"""
        if self.optimizer:
            stats = self.optimizer.get_performance_stats()
            stats['service_name'] = self.service_name
            return stats
        return {'service_name': self.service_name, 'optimizer_not_initialized': True}


# Service-specific integrators
class FastForexIntegrator(APIPerformanceIntegrator):
    """FastForex-specific performance optimizations"""
    
    def __init__(self):
        super().__init__("fastforex")
    
    async def get_crypto_rate_optimized(self, crypto_symbol: str) -> float:
        """Get crypto rate with performance optimizations"""
        cache_key = f"crypto_rate_{crypto_symbol}_USD"
        
        async def fetch_rate():
            # This would call the actual FastForex API
            # Implementation would be moved here from the service
            pass
            
        return await self.cached_rate_call(
            cache_key=cache_key,
            api_call_func=fetch_rate,
            cache_ttl=600  # 10 minutes
        )
    
    async def get_multiple_crypto_rates_optimized(self, symbols: List[str]) -> Dict[str, float]:
        """Get multiple crypto rates with batch optimization"""
        async def get_single_rate(symbol: str) -> float:
            return await self.get_crypto_rate_optimized(symbol)
        
        return await self.batch_rate_calls(
            symbols=symbols,
            rate_func=get_single_rate,
            max_concurrent=3  # Conservative for FastForex
        )


class PaymentProcessorIntegrator(APIPerformanceIntegrator):
    """Payment processor-specific performance optimizations"""
    
    def __init__(self, processor_name: str):
        super().__init__(processor_name)
    
    async def create_payment_address_optimized(
        self,
        create_func: Callable,
        currency: str,
        escrow_id: str
    ) -> Dict[str, Any]:
        """Create payment address with rate limiting"""
        return await self.rate_limited_payment_call(
            api_call_func=create_func,
            calls_per_second=2.0  # Conservative rate for payment creation
        )
    
    async def check_payment_status_optimized(
        self,
        status_func: Callable,
        payment_id: str
    ) -> Dict[str, Any]:
        """Check payment status with caching"""
        cache_key = f"payment_status_{payment_id}"
        
        return await self.cached_rate_call(
            cache_key=cache_key,
            api_call_func=status_func,
            cache_ttl=30,  # 30 seconds for payment status
            force_refresh=False
        )


# Global integrators for easy access
fastforex_integrator = FastForexIntegrator()
dynopay_integrator = PaymentProcessorIntegrator("dynopay")
blockbee_integrator = PaymentProcessorIntegrator("blockbee")


async def log_all_performance_stats():
    """Log performance statistics for all integrated services"""
    integrators = [
        fastforex_integrator,
        dynopay_integrator,
        blockbee_integrator
    ]
    
    logger.info("📊 API PERFORMANCE SUMMARY:")
    for integrator in integrators:
        stats = integrator.get_performance_stats()
        if 'optimizer_not_initialized' not in stats:
            logger.info(
                f"  {stats['service_name']}: {stats['total_api_calls']} calls, "
                f"{stats['cache_hit_rate']:.1%} cache hit rate"
            )
        else:
            logger.info(f"  {stats['service_name']}: not yet active")


# Cleanup function for integration
async def cleanup_all_integrators():
    """Clean up all integrators"""
    from services.external_api_optimizer import cleanup_api_optimizer
    await cleanup_api_optimizer()
    logger.info("🧹 All API integrators cleaned up")