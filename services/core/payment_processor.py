"""
Core PaymentProcessor - Unified Payment Architecture

Single entry point for all payment operations, replacing the current 100+ service architecture
with a clean, simple interface that handles all payin and payout operations.

Key Features:
- Single entry points: process_payin(), process_payout(), check_balance()
- Automatic provider routing based on currency and operation type
- Standardized request/response interfaces
- Integration with existing database models
- Simplified error handling and status management
"""

import logging
from typing import Dict, Optional, List, Union
from decimal import Decimal

from database import async_managed_session
from models import (
    User, UnifiedTransaction, UnifiedTransactionStatus, UnifiedTransactionType,
    Wallet
)

from .payment_data_structures import (
    PayinRequest, PayoutRequest, PaymentResult, BalanceCheckResult, PaymentProvider, TransactionStatus, PaymentError, PaymentDirection,
    PaymentDestination, create_error_result
)

from .unified_error_handler import (
    unified_error_handler, UnifiedPaymentError, UnifiedErrorCategory
)

from .payment_provider_interface import (
    PaymentProviderInterface, FincraProviderAdapter, 
    KrakenProviderAdapter, BlockBeeProviderAdapter
)

from .state_manager import (
    StateTransitionContext, state_manager
)

logger = logging.getLogger(__name__)


class PaymentProcessor:
    """
    Unified Payment Processor - Single entry point for all payment operations
    
    Replaces the complex 100+ service architecture with a clean, simple interface.
    Automatically routes payments to the appropriate provider based on currency and operation type.
    """
    
    def __init__(self):
        """Initialize the payment processor with all providers"""
        self.providers: Dict[PaymentProvider, PaymentProviderInterface] = {}
        self.state_manager = state_manager  # Use global state manager instance
        
        # Initialize provider adapters
        try:
            self.providers[PaymentProvider.FINCRA] = FincraProviderAdapter()
            logger.info("✅ Fincra provider adapter initialized")
        except Exception as e:
            logger.warning(f"⚠️ Fincra provider adapter failed to initialize: {e}")
        
        try:
            self.providers[PaymentProvider.KRAKEN] = KrakenProviderAdapter()
            logger.info("✅ Kraken provider adapter initialized")
        except Exception as e:
            logger.warning(f"⚠️ Kraken provider adapter failed to initialize: {e}")
        
        try:
            self.providers[PaymentProvider.BLOCKBEE] = BlockBeeProviderAdapter()
            logger.info("✅ BlockBee provider adapter initialized")
        except Exception as e:
            logger.warning(f"⚠️ BlockBee provider adapter failed to initialize: {e}")
        
        # Provider routing rules
        self.payin_routing = {
            "NGN": PaymentProvider.FINCRA,
            "BTC": PaymentProvider.BLOCKBEE,
            "ETH": PaymentProvider.BLOCKBEE,
            "LTC": PaymentProvider.BLOCKBEE,
            "USDT": PaymentProvider.BLOCKBEE,
            "USDC": PaymentProvider.BLOCKBEE,
            "TRX": PaymentProvider.BLOCKBEE,
            "DOGE": PaymentProvider.BLOCKBEE,
        }
        
        self.payout_routing = {
            "NGN": PaymentProvider.FINCRA,
            "BTC": PaymentProvider.KRAKEN,
            "ETH": PaymentProvider.KRAKEN,
            "LTC": PaymentProvider.KRAKEN,
            "USDT": PaymentProvider.KRAKEN,
            "USD": PaymentProvider.KRAKEN,
        }
        
        logger.info(f"🚀 PaymentProcessor initialized with {len(self.providers)} providers")
    
    async def process_payin(self, request: PayinRequest) -> PaymentResult:
        """
        Process incoming payment request
        
        Handles all types of incoming payments:
        - Crypto deposits (via BlockBee)
        - NGN deposits (via Fincra virtual accounts)
        - Escrow payments
        - Exchange buy orders
        
        Args:
            request: PayinRequest with payment details
            
        Returns:
            PaymentResult with processing outcome
        """
        try:
            logger.info(
                f"🔄 PAYIN: Processing {request.currency} {request.amount} "
                f"for user {request.user_id} ({request.payment_type})"
            )
            
            # Validate request
            validation_result = await self._validate_payin_request(request)
            if not validation_result.success:
                return validation_result
            
            # Route to appropriate provider
            provider_type = self._route_payin(request)
            if not provider_type:
                return create_error_result(
                    PaymentError.BUSINESS,
                    f"No provider available for {request.currency} payins"
                )
            
            provider = self.providers.get(provider_type)
            if not provider:
                return create_error_result(
                    PaymentError.TECHNICAL,
                    f"Provider {provider_type.value} not initialized"
                )
            
            # Check provider availability
            if not await provider.is_available():
                return create_error_result(
                    PaymentError.TECHNICAL,
                    f"Provider {provider_type.value} is currently unavailable"
                )
            
            # Create database record
            unified_transaction = await self._create_unified_transaction(
                request, PaymentDirection.PAYIN, provider_type
            )
            
            # Transition to PROCESSING state
            context = StateTransitionContext(
                transaction_id=unified_transaction.transaction_id,
                transaction_type=request.payment_type,
                user_id=request.user_id,
                reason="Starting payin processing",
                metadata={"provider": provider_type.value},
                provider=provider_type
            )
            await self.state_manager.transition_state(context, TransactionStatus.PROCESSING)
            
            # Process with provider using unified error handling
            try:
                result = await self._process_with_unified_error_handling(
                    provider, "process_payin", request, provider_type.value
                )
            except UnifiedPaymentError as e:
                # Convert unified error to PaymentResult
                result = self._convert_unified_error_to_result(e)
                # Update transaction state to failed
                context.reason = f"Unified error: {e.get_category().value}"
                context.metadata.update({"error_category": e.get_category().value})
                await self.state_manager.transition_state(context, TransactionStatus.FAILED)
            
            # Update database with result using state manager
            await self._update_transaction_with_state_manager(unified_transaction.transaction_id, result, request.payment_type, request.user_id)
            
            # Set transaction ID in result  
            result.transaction_id = unified_transaction.transaction_id
            
            logger.info(
                f"✅ PAYIN: {result.status.value} - {request.currency} {request.amount} "
                f"via {provider_type.value} (tx: {unified_transaction.id})"
            )
            
            return result
            
        except Exception as e:
            logger.error(f"❌ PAYIN: Error processing {request.currency} {request.amount}: {e}")
            return create_error_result(
                PaymentError.TECHNICAL,
                f"Internal processing error: {str(e)}"
            )
    
    async def process_payout(self, request: PayoutRequest) -> PaymentResult:
        """
        Process outgoing payment request
        
        Handles all types of outgoing payments:
        - Crypto withdrawals (via Kraken)
        - NGN withdrawals (via Fincra bank transfers)
        - Refunds
        - Escrow releases
        
        Args:
            request: PayoutRequest with payment details
            
        Returns:
            PaymentResult with processing outcome
        """
        try:
            logger.info(
                f"🔄 PAYOUT: Processing {request.currency} {request.amount} "
                f"for user {request.user_id} ({request.payment_type})"
            )
            
            # Validate request
            validation_result = await self._validate_payout_request(request)
            if not validation_result.success:
                return validation_result
            
            # Check OTP requirement
            if request.requires_otp and not await self._verify_otp_if_required(request):
                return PaymentResult(
                    success=False,
                    status=TransactionStatus.AWAITING,
                    requires_otp=True,
                    next_action="verify_otp",
                    error_message="OTP verification required for this payout"
                )
            
            # Route to appropriate provider
            provider_type = self._route_payout(request)
            if not provider_type:
                return create_error_result(
                    PaymentError.BUSINESS,
                    f"No provider available for {request.currency} payouts"
                )
            
            provider = self.providers.get(provider_type)
            if not provider:
                return create_error_result(
                    PaymentError.TECHNICAL,
                    f"Provider {provider_type.value} not initialized"
                )
            
            # Check provider availability
            if not await provider.is_available():
                return create_error_result(
                    PaymentError.TECHNICAL,
                    f"Provider {provider_type.value} is currently unavailable"
                )
            
            # Check sufficient balance
            balance_check = await self._check_sufficient_balance(request)
            if not balance_check:
                return create_error_result(
                    PaymentError.BUSINESS,
                    f"Insufficient {request.currency} balance for payout"
                )
            
            # Create database record
            unified_transaction = await self._create_unified_transaction(
                request, PaymentDirection.PAYOUT, provider_type
            )
            
            # Transition to PROCESSING state
            context = StateTransitionContext(
                transaction_id=unified_transaction.transaction_id,
                transaction_type=request.payment_type,
                user_id=request.user_id,
                reason="Starting payout processing",
                metadata={"provider": provider_type.value, "destination": request.destination.to_dict()},
                provider=provider_type
            )
            await self.state_manager.transition_state(context, TransactionStatus.PROCESSING)
            
            # Process with provider using unified error handling
            try:
                result = await self._process_with_unified_error_handling(
                    provider, "process_payout", request, provider_type.value
                )
            except UnifiedPaymentError as e:
                # Convert unified error to PaymentResult
                result = self._convert_unified_error_to_result(e)
                # Update transaction state to failed
                context.reason = f"Unified error: {e.get_category().value}"
                context.metadata.update({"error_category": e.get_category().value})
                await self.state_manager.transition_state(context, TransactionStatus.FAILED)
            
            # Update database with result using state manager
            await self._update_transaction_with_state_manager(unified_transaction.transaction_id, result, request.payment_type, request.user_id)
            
            # Set transaction ID in result  
            result.transaction_id = unified_transaction.transaction_id
            
            logger.info(
                f"✅ PAYOUT: {result.status.value} - {request.currency} {request.amount} "
                f"via {provider_type.value} (tx: {unified_transaction.id})"
            )
            
            return result
            
        except Exception as e:
            logger.error(f"❌ PAYOUT: Error processing {request.currency} {request.amount}: {e}")
            return create_error_result(
                PaymentError.TECHNICAL,
                f"Internal processing error: {str(e)}"
            )
    
    async def check_balance(self, currencies: Optional[List[str]] = None) -> BalanceCheckResult:
        """
        Check balances across all providers and currencies
        
        Args:
            currencies: Optional list of specific currencies to check
            
        Returns:
            BalanceCheckResult with balance information from all providers
        """
        try:
            logger.info(f"🔍 BALANCE: Checking balances for {currencies or 'all currencies'}")
            
            all_balances = []
            total_usd_value = Decimal('0')
            
            # Get balances from all available providers
            for provider_type, provider in self.providers.items():
                try:
                    if not await provider.is_available():
                        continue
                    
                    balances = await provider.get_balance()
                    for balance in balances:
                        if currencies and balance.currency not in currencies:
                            continue
                        
                        all_balances.append(balance)
                        
                        # Convert to USD for total calculation using FastForex service
                        try:
                            usd_value = await self._convert_to_usd(
                                balance.available_balance, balance.currency
                            )
                            total_usd_value += usd_value
                        except Exception as e:
                            logger.warning(f"Failed to convert {balance.currency} to USD: {e}")
                            # Continue without USD conversion for this currency
                        
                except Exception as e:
                    logger.warning(f"Failed to get balance from {provider_type.value}: {e}")
                    continue
            
            logger.info(
                f"✅ BALANCE: Retrieved {len(all_balances)} balances, "
                f"total value ~${total_usd_value:.2f}"
            )
            
            return BalanceCheckResult(
                success=True,
                balances=all_balances,
                total_usd_value=total_usd_value
            )
            
        except Exception as e:
            logger.error(f"❌ BALANCE: Error checking balances: {e}")
            return BalanceCheckResult(
                success=False,
                error_message=str(e)
            )
    
    def _route_payin(self, request: PayinRequest) -> Optional[PaymentProvider]:
        """Route payin request to appropriate provider"""
        # Check for explicit provider preference
        if request.preferred_provider:
            provider = self.providers.get(request.preferred_provider)
            if provider and provider.supports_payin:
                return request.preferred_provider
        
        # Use default routing
        return self.payin_routing.get(request.currency.upper())
    
    def _route_payout(self, request: PayoutRequest) -> Optional[PaymentProvider]:
        """Route payout request to appropriate provider"""
        # Check for explicit provider preference
        if request.preferred_provider:
            provider = self.providers.get(request.preferred_provider)
            if provider and provider.supports_payout:
                return request.preferred_provider
        
        # Use default routing
        return self.payout_routing.get(request.currency.upper())
    
    async def _validate_payin_request(self, request: PayinRequest) -> PaymentResult:
        """Validate payin request"""
        if request.amount <= 0:
            return create_error_result(
                PaymentError.BUSINESS,
                "Amount must be greater than zero"
            )
        
        if not request.currency:
            return create_error_result(
                PaymentError.BUSINESS,
                "Currency is required"
            )
        
        # Check if user exists
        async with async_managed_session() as session:
            user = session.query(User).filter(User.id == request.user_id).first()
            if not user:
                return create_error_result(
                    PaymentError.BUSINESS,
                    "User not found"
                )
        
        return PaymentResult(success=True, status=TransactionStatus.PENDING)
    
    async def _validate_payout_request(self, request: PayoutRequest) -> PaymentResult:
        """Validate payout request"""
        if request.amount <= 0:
            return create_error_result(
                PaymentError.BUSINESS,
                "Amount must be greater than zero"
            )
        
        if not request.currency:
            return create_error_result(
                PaymentError.BUSINESS,
                "Currency is required"
            )
        
        if not request.destination:
            return create_error_result(
                PaymentError.BUSINESS,
                "Destination is required for payouts"
            )
        
        # Validate destination based on type
        if request.destination.type == "crypto_address" and not request.destination.address:
            return create_error_result(
                PaymentError.BUSINESS,
                "Crypto address is required"
            )
        
        if request.destination.type == "bank_account":
            if not all([request.destination.bank_code, request.destination.account_number]):
                return create_error_result(
                    PaymentError.BUSINESS,
                    "Bank code and account number are required"
                )
        
        return PaymentResult(success=True, status=TransactionStatus.PENDING)
    
    async def _create_unified_transaction(
        self, 
        request, 
        direction: PaymentDirection,
        provider: PaymentProvider
    ) -> UnifiedTransaction:
        """Create UnifiedTransaction record for tracking"""
        async with async_managed_session() as session:
            # Map payment types to UnifiedTransactionType
            type_mapping = {
                "escrow": UnifiedTransactionType.ESCROW,
                "exchange_buy": UnifiedTransactionType.EXCHANGE_BUY_CRYPTO,
                "exchange_sell": UnifiedTransactionType.EXCHANGE_SELL_CRYPTO,
                "cashout": UnifiedTransactionType.WALLET_CASHOUT,
                "wallet_deposit": UnifiedTransactionType.WALLET_CASHOUT,  # Use closest match
                "refund": UnifiedTransactionType.WALLET_CASHOUT,  # Use closest match
            }
            
            from utils.universal_id_generator import UniversalIDGenerator
            
            transaction = UnifiedTransaction(
                id=UniversalIDGenerator.generate_transaction_id(),
                user_id=request.user_id,
                transaction_type=type_mapping.get(request.payment_type, UnifiedTransactionType.WALLET_CASHOUT),
                amount=request.amount,
                currency=request.currency,
                status=UnifiedTransactionStatus.PENDING,
                metadata={
                    "payment_direction": direction.value,
                    "provider": provider.value,
                    "payment_type": request.payment_type,
                    "reference_id": getattr(request, 'reference_id', None),
                    "original_request": {
                        "amount": str(Decimal(str(request.amount))),
                        "currency": request.currency,
                        "metadata": getattr(request, 'metadata', {})
                    }
                }
            )
            
            session.add(transaction)
            session.commit()
            session.refresh(transaction)
            
            return transaction
    
    async def _update_transaction_with_state_manager(
        self, 
        transaction_id: str, 
        result: PaymentResult, 
        transaction_type: str, 
        user_id: int
    ):
        """Update transaction using StateManager with proper state transitions"""
        try:
            # Create transition context with result information
            context = StateTransitionContext(
                transaction_id=transaction_id,
                transaction_type=transaction_type,
                user_id=user_id,
                reason=f"Provider result: {result.status.value}",
                metadata={
                    "provider_transaction_id": result.provider_transaction_id,
                    "provider_reference": result.provider_reference,
                    "error_message": result.error_message,
                    "error_code": result.error_code,
                    "payment_details": result.payment_details,
                    "actual_amount": str(Decimal(str(result.actual_amount))) if result.actual_amount else None,
                    "fees_charged": str(Decimal(str(result.fees_charged))) if result.fees_charged else None,
                    "exchange_rate": str(Decimal(str(result.exchange_rate))) if result.exchange_rate else None,
                    "estimated_completion": result.estimated_completion.isoformat() if result.estimated_completion else None
                },
                provider=result.provider,
                external_reference=result.provider_reference
            )
            
            # Transition to the appropriate state using StateManager
            success = await self.state_manager.transition_state(context, result.status)
            
            if not success:
                logger.error(
                    f"❌ Failed to transition {transaction_id} to {result.status.value}"
                )
            
        except Exception as e:
            logger.error(
                f"❌ Error updating transaction {transaction_id} with state manager: {e}"
            )
    
    async def _update_transaction_from_result(self, transaction_id: str, result: PaymentResult):
        """Legacy method - maintained for backward compatibility"""
        logger.warning(
            f"⚠️ Using legacy _update_transaction_from_result for {transaction_id}. "
            "Consider migrating to _update_transaction_with_state_manager."
        )
        
        async with async_managed_session() as session:
            transaction = session.query(UnifiedTransaction).filter(
                UnifiedTransaction.transaction_id == transaction_id
            ).first()
            
            if transaction:
                # Use StateManager for the transition
                context = StateTransitionContext(
                    transaction_id=transaction_id,
                    transaction_type="legacy_update",
                    user_id=transaction.user_id,
                    reason="Legacy result update",
                    metadata={
                        "provider_transaction_id": result.provider_transaction_id,
                        "provider_reference": result.provider_reference,
                        "error_message": result.error_message,
                        "payment_details": result.payment_details
                    },
                    provider=result.provider
                )
                
                await self.state_manager.transition_state(context, result.status)
    
    async def _verify_otp_if_required(self, request: PayoutRequest) -> bool:
        """Check if OTP verification is required and valid"""
        # Integrate with existing OTP verification system
        from services.conditional_otp_service import ConditionalOTPService
        
        try:
            # Check if OTP is required for this operation
            otp_required = ConditionalOTPService.is_otp_required_for_operation(
                user_id=request.user_id,
                operation_type="cashout",
                amount=request.amount,
                currency=request.currency
            )
            
            if not otp_required:
                return True
            
            # If OTP is required but not provided in metadata, return False
            otp_code = request.metadata.get("otp_code")
            if not otp_code:
                return False
            
            # Verify the OTP
            return ConditionalOTPService.verify_otp(
                user_id=request.user_id,
                otp_code=otp_code,
                operation_type="cashout"
            )
            
        except Exception as e:
            logger.error(f"OTP verification error: {e}")
            return False
    
    async def _check_sufficient_balance(self, request: PayoutRequest) -> bool:
        """Check if user has sufficient balance for payout"""
        async with async_managed_session() as session:
            wallet = session.query(Wallet).filter(
                Wallet.user_id == request.user_id,
                Wallet.currency == request.currency
            ).first()
            
            if not wallet:
                return False
            
            return wallet.available_balance >= request.amount
    
    async def _convert_to_usd(self, amount: Decimal, currency: str) -> Decimal:
        """
        Convert amount to USD using existing FastForex service
        
        Args:
            amount: Amount to convert
            currency: Source currency code
            
        Returns:
            USD equivalent amount
        """
        try:
            # USD is already USD
            if currency.upper() == "USD":
                return amount
            
            # Use existing FastForex service for conversion
            from services.fastforex_service import fastforex_service
            
            # Get rate for this currency to USD
            if currency.upper() == "NGN":
                # For NGN, we need USD/NGN rate, then invert it
                rate_data = await fastforex_service.get_live_rate("USD", "NGN")
                if rate_data and rate_data.get("success"):
                    ngn_per_usd = Decimal(str(rate_data.get("rate", 1)))
                    if ngn_per_usd > 0:
                        usd_amount = amount / ngn_per_usd
                        logger.debug(f"💱 Converted {amount} NGN to ${usd_amount:.2f} USD (rate: {ngn_per_usd})")
                        return usd_amount
            else:
                # For crypto currencies, get direct rate to USD
                rate_data = await fastforex_service.get_crypto_rate(currency.upper())
                if rate_data and rate_data.get("success"):
                    usd_per_crypto = Decimal(str(rate_data.get("rate", 0)))
                    if usd_per_crypto > 0:
                        usd_amount = amount * usd_per_crypto
                        logger.debug(f"💱 Converted {amount} {currency} to ${usd_amount:.2f} USD (rate: {usd_per_crypto})")
                        return usd_amount
            
            # Fallback: return zero if conversion fails
            logger.warning(f"Could not convert {currency} to USD, using $0")
            return Decimal('0')
            
        except Exception as e:
            logger.error(f"Error converting {currency} to USD: {e}")
            return Decimal('0')
    
    async def _process_with_unified_error_handling(
        self, 
        provider: PaymentProviderInterface, 
        method_name: str, 
        request, 
        provider_name: str
    ):
        """
        Process provider request with unified error handling.
        
        Args:
            provider: Provider instance
            method_name: Method to call on provider ('process_payin' or 'process_payout')
            request: Payment request
            provider_name: Provider name for error classification
            
        Returns:
            PaymentResult from provider
            
        Raises:
            UnifiedPaymentError: If error occurs and is classified
        """
        try:
            # Call the provider method
            method = getattr(provider, method_name)
            return await method(request)
            
        except Exception as e:
            # Classify error using unified handler
            classification = unified_error_handler.classify_error(
                exception=e,
                provider=provider_name,
                context={
                    "method": method_name,
                    "amount": str(Decimal(str(request.amount))),
                    "currency": request.currency,
                    "user_id": request.user_id,
                    "payment_type": request.payment_type
                }
            )
            
            # Log the classification
            logger.error(
                f"❌ UNIFIED_ERROR: {provider_name}.{method_name} failed - "
                f"{classification.category.value}: {classification.admin_message}",
                extra=classification.to_dict()
            )
            
            # Raise unified error
            raise UnifiedPaymentError(classification) from e
    
    def _convert_unified_error_to_result(self, unified_error: UnifiedPaymentError) -> PaymentResult:
        """
        Convert UnifiedPaymentError to PaymentResult for consistency.
        
        Args:
            unified_error: The unified payment error
            
        Returns:
            PaymentResult with error information
        """
        classification = unified_error.classification
        
        # Map unified category to PaymentError enum (for legacy compatibility)
        if classification.category == UnifiedErrorCategory.TECHNICAL:
            payment_error = PaymentError.TECHNICAL
            status = TransactionStatus.FAILED  # Will be retried by unified system
        elif classification.category == UnifiedErrorCategory.BUSINESS:
            payment_error = PaymentError.BUSINESS
            status = TransactionStatus.FAILED
        else:  # PERMANENT
            payment_error = PaymentError.PERMANENT
            status = TransactionStatus.FAILED
        
        return PaymentResult(
            success=False,
            status=status,
            error=payment_error,
            error_message=classification.user_message,
            error_code=classification.error_code,
            provider=classification.provider,
            payment_details={
                "unified_error_category": classification.category.value,
                "should_retry": classification.should_retry,
                "max_retries": classification.max_retries,
                "retry_delay_seconds": classification.retry_delay_seconds,
                "admin_message": classification.admin_message,
                "original_error": classification.original_error
            }
        )


# Singleton instance
payment_processor = PaymentProcessor()


# Convenience functions for easy migration from existing code
async def process_payin(
    user_id: int,
    amount: Union[Decimal, float],
    currency: str,
    payment_type: str,
    reference_id: Optional[str] = None,
    **kwargs
) -> PaymentResult:
    """Convenience function for payin processing"""
    request = PayinRequest(
        user_id=user_id,
        amount=amount,
        currency=currency,
        payment_type=payment_type,
        reference_id=reference_id,
        metadata=kwargs
    )
    return await payment_processor.process_payin(request)


async def process_payout(
    user_id: int,
    amount: Union[Decimal, float],
    currency: str,
    destination: PaymentDestination,
    payment_type: str,
    reference_id: Optional[str] = None,
    **kwargs
) -> PaymentResult:
    """Convenience function for payout processing"""
    request = PayoutRequest(
        user_id=user_id,
        amount=amount,
        currency=currency,
        destination=destination,
        payment_type=payment_type,
        reference_id=reference_id,
        metadata=kwargs
    )
    return await payment_processor.process_payout(request)


async def check_balances(currencies: Optional[List[str]] = None) -> BalanceCheckResult:
    """Convenience function for balance checking"""
    return await payment_processor.check_balance(currencies)