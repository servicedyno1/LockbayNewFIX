"""
Payment Provider Interface for UTE

Standardizes payment operations across different providers (Fincra, Kraken, BlockBee, DynoPay).
"""

from abc import abstractmethod
from typing import Dict, Any
from decimal import Decimal
from enum import Enum

from .base import BaseProvider, ProviderResult


class PaymentType(Enum):
    """Types of payments supported by providers"""
    CRYPTO_WITHDRAWAL = "crypto_withdrawal"   # Kraken, BlockBee
    FIAT_WITHDRAWAL = "fiat_withdrawal"       # Fincra, DynoPay
    CRYPTO_DEPOSIT = "crypto_deposit"         # BlockBee
    FIAT_DEPOSIT = "fiat_deposit"             # Fincra, DynoPay


class PaymentProvider(BaseProvider):
    """
    Abstract interface for payment providers
    
    Standardizes payment operations across Fincra (NGN), Kraken (crypto),
    BlockBee (crypto deposits), and DynoPay (international).
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
    async def get_supported_payment_types(self) -> ProviderResult:
        """
        Get list of payment types supported by this provider
        
        Returns:
            ProviderResult with data containing List[PaymentType]
        """
        pass
    
    @abstractmethod
    async def validate_address(self, currency: str, address: str) -> ProviderResult:
        """
        Validate a destination address for the given currency
        
        Args:
            currency: Currency code (e.g., 'BTC', 'ETH', 'NGN')
            address: Destination address to validate
            
        Returns:
            ProviderResult indicating if address is valid
        """
        pass
    
    @abstractmethod
    async def get_balance(self, currency: str) -> ProviderResult:
        """
        Get current balance for a specific currency
        
        Args:
            currency: Currency code to check balance for
            
        Returns:
            ProviderResult with balance data
        """
        pass
    
    @abstractmethod
    async def estimate_fees(
        self,
        payment_type: PaymentType,
        currency: str,
        amount: Decimal,
        destination: str = None
    ) -> ProviderResult:
        """
        Estimate fees for a payment operation
        
        Args:
            payment_type: Type of payment operation
            currency: Currency code
            amount: Amount to send/receive
            destination: Destination address/account (if applicable)
            
        Returns:
            ProviderResult with fee estimation data
        """
        pass
    
    @abstractmethod
    async def create_withdrawal(
        self,
        currency: str,
        amount: Decimal,
        destination: str,
        memo: str = None,
        idempotency_key: str = None
    ) -> ProviderResult:
        """
        Create a withdrawal transaction
        
        Args:
            currency: Currency to withdraw
            amount: Amount to withdraw
            destination: Destination address/account
            memo: Optional memo/tag for the transaction
            idempotency_key: Unique key to prevent duplicate processing
            
        Returns:
            ProviderResult with transaction details
        """
        pass
    
    @abstractmethod
    async def check_withdrawal_status(self, external_reference: str) -> ProviderResult:
        """
        Check the status of a withdrawal transaction
        
        Args:
            external_reference: Provider's reference for the withdrawal
            
        Returns:
            ProviderResult with current transaction status
        """
        pass
    
    @abstractmethod
    async def generate_deposit_address(
        self,
        currency: str,
        callback_url: str = None,
        metadata: Dict[str, Any] = None
    ) -> ProviderResult:
        """
        Generate a deposit address for receiving payments
        
        Args:
            currency: Currency to generate address for
            callback_url: Webhook URL for deposit notifications
            metadata: Additional metadata to associate with address
            
        Returns:
            ProviderResult with generated address details
        """
        pass
    
    @abstractmethod
    async def process_webhook(
        self,
        webhook_data: Dict[str, Any],
        signature: str = None,
        headers: Dict[str, str] = None
    ) -> ProviderResult:
        """
        Process an incoming webhook from the provider
        
        Args:
            webhook_data: Raw webhook payload
            signature: Webhook signature for verification
            headers: HTTP headers from webhook request
            
        Returns:
            ProviderResult with processed webhook information
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
    
    def supports_payment_type(self, payment_type: PaymentType) -> bool:
        """
        Check if this provider supports a specific payment type
        
        Args:
            payment_type: Payment type to check
            
        Returns:
            True if payment type is supported, False otherwise
        """
        # This should be implemented by checking cached supported payment types
        # Default implementation assumes provider doesn't support the payment type
        return False