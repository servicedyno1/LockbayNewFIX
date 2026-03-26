"""
Rates Provider Interface for UTE

Standardizes exchange rate operations across different providers (FastForex, CoinGecko).
"""

from abc import abstractmethod
from typing import List
from decimal import Decimal
from datetime import datetime
from enum import Enum

from .base import BaseProvider, ProviderResult


class RateType(Enum):
    """Types of exchange rates"""
    SPOT = "spot"           # Current market rate
    BUY = "buy"            # Rate for buying (higher)
    SELL = "sell"          # Rate for selling (lower)
    AVERAGE = "average"     # Average of buy/sell


class RatesProvider(BaseProvider):
    """
    Abstract interface for exchange rate providers
    
    Standardizes exchange rate operations across FastForex (fiat)
    and other providers for consistent rate fetching and caching.
    """
    
    @abstractmethod
    async def get_supported_currencies(self) -> ProviderResult:
        """
        Get list of currencies supported by this provider
        
        Returns:
            ProviderResult with data containing List[str] of currency codes
        """
        pass
    
    @abstractmethod
    async def get_supported_currency_pairs(self) -> ProviderResult:
        """
        Get list of currency pairs supported by this provider
        
        Returns:
            ProviderResult with data containing List[Tuple[str, str]] of (base, quote) pairs
        """
        pass
    
    @abstractmethod
    async def get_exchange_rate(
        self,
        base_currency: str,
        quote_currency: str,
        rate_type: RateType = RateType.SPOT,
        amount: Decimal = None
    ) -> ProviderResult:
        """
        Get exchange rate between two currencies
        
        Args:
            base_currency: Base currency code (e.g., 'USD')
            quote_currency: Quote currency code (e.g., 'NGN')
            rate_type: Type of rate to fetch
            amount: Optional amount for amount-specific rates
            
        Returns:
            ProviderResult with exchange rate data
        """
        pass
    
    @abstractmethod
    async def get_multiple_rates(
        self,
        base_currency: str,
        quote_currencies: List[str],
        rate_type: RateType = RateType.SPOT
    ) -> ProviderResult:
        """
        Get exchange rates for multiple currency pairs at once
        
        Args:
            base_currency: Base currency code
            quote_currencies: List of quote currency codes
            rate_type: Type of rates to fetch
            
        Returns:
            ProviderResult with multiple exchange rate data
        """
        pass
    
    @abstractmethod
    async def get_historical_rate(
        self,
        base_currency: str,
        quote_currency: str,
        date: datetime,
        rate_type: RateType = RateType.SPOT
    ) -> ProviderResult:
        """
        Get historical exchange rate for a specific date
        
        Args:
            base_currency: Base currency code
            quote_currency: Quote currency code
            date: Date for historical rate
            rate_type: Type of rate to fetch
            
        Returns:
            ProviderResult with historical exchange rate data
        """
        pass
    
    @abstractmethod
    async def convert_amount(
        self,
        amount: Decimal,
        from_currency: str,
        to_currency: str,
        rate_type: RateType = RateType.SPOT
    ) -> ProviderResult:
        """
        Convert an amount from one currency to another
        
        Args:
            amount: Amount to convert
            from_currency: Source currency code
            to_currency: Target currency code
            rate_type: Type of rate to use for conversion
            
        Returns:
            ProviderResult with converted amount and rate used
        """
        pass
    
    @abstractmethod
    async def get_rate_with_markup(
        self,
        base_currency: str,
        quote_currency: str,
        markup_percentage: Decimal,
        rate_type: RateType = RateType.SPOT
    ) -> ProviderResult:
        """
        Get exchange rate with markup applied
        
        Args:
            base_currency: Base currency code
            quote_currency: Quote currency code
            markup_percentage: Markup percentage to apply (e.g., 2.5 for 2.5%)
            rate_type: Base rate type before markup
            
        Returns:
            ProviderResult with marked-up exchange rate
        """
        pass
    
    @abstractmethod
    async def validate_currency_pair(
        self,
        base_currency: str,
        quote_currency: str
    ) -> ProviderResult:
        """
        Validate that a currency pair is supported
        
        Args:
            base_currency: Base currency code
            quote_currency: Quote currency code
            
        Returns:
            ProviderResult indicating if currency pair is supported
        """
        pass
    
    # Helper methods for common operations
    
    def supports_currency(self, currency: str) -> bool:
        """
        Check if this provider supports a specific currency
        
        Args:
            currency: Currency code to check
            
        Returns:
            True if currency is supported, False otherwise
        """
        # This should be implemented by checking cached supported currencies
        # Default implementation assumes provider doesn't support the currency
        return False
    
    def supports_currency_pair(self, base_currency: str, quote_currency: str) -> bool:
        """
        Check if this provider supports a specific currency pair
        
        Args:
            base_currency: Base currency code
            quote_currency: Quote currency code
            
        Returns:
            True if currency pair is supported, False otherwise
        """
        # This should be implemented by checking cached supported currency pairs
        # Default implementation assumes provider doesn't support the currency pair
        return False