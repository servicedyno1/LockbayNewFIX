#!/usr/bin/env python3
"""
Migration Adapters for PaymentProcessor Integration

Provides backward compatibility during the migration from complex 100+ service
architecture to the new simplified PaymentProcessor system. These adapters
maintain the existing interfaces while using PaymentProcessor internally.

This allows for gradual migration without breaking existing functionality.
"""

import logging
from typing import Dict, Any, Optional, List, Union
from decimal import Decimal

# Import the new unified architecture
from services.core.payment_processor import PaymentProcessor
from services.core.payment_data_structures import (
    PayoutRequest, PaymentDestination, PaymentResult
)

# Import existing services for fallback compatibility
from services.fincra_service import FincraService
from services.kraken_service import KrakenService

logger = logging.getLogger(__name__)


class PaymentProcessorAdapter:
    """
    Global adapter that provides PaymentProcessor functionality
    with automatic initialization and caching
    """
    _instance = None
    _payment_processor = None
    
    @classmethod
    def get_instance(cls):
        """Get or create singleton PaymentProcessor instance"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def __init__(self):
        """Initialize the payment processor lazily"""
        self._payment_processor = None
        self._initialized = False
    
    @property
    def payment_processor(self) -> PaymentProcessor:
        """Lazy initialization of PaymentProcessor"""
        if self._payment_processor is None:
            try:
                self._payment_processor = PaymentProcessor()
                self._initialized = True
                logger.info("🔄 PaymentProcessor initialized via adapter")
            except Exception as e:
                logger.error(f"❌ Failed to initialize PaymentProcessor: {e}")
                # Return None to allow fallback to legacy services
                return None
        return self._payment_processor
    
    async def process_payout_unified(
        self,
        user_id: int,
        amount: Union[Decimal, float],
        currency: str,
        destination_info: Dict[str, Any],
        payment_type: str = "cashout",
        requires_otp: bool = True,
        priority: str = "normal"
    ) -> Dict[str, Any]:
        """
        Unified payout processing that replaces direct service calls
        
        This method provides a single entry point for all payout operations,
        replacing complex chains of service-specific calls.
        """
        try:
            processor = self.payment_processor
            if not processor:
                return {"success": False, "error": "PaymentProcessor not available"}
            
            # Convert destination info to PaymentDestination
            destination = self._convert_destination_info(destination_info, currency)
            
            # Create payout request
            request = PayoutRequest(
                user_id=user_id,
                amount=amount,
                currency=currency,
                destination=destination,
                payment_type=payment_type,
                requires_otp=requires_otp,
                priority=priority
            )
            
            # Process using PaymentProcessor
            result = await processor.process_payout(request)
            
            # Convert to legacy format for compatibility
            return self._convert_payment_result_to_legacy(result)
            
        except Exception as e:
            logger.error(f"❌ Unified payout processing failed: {e}")
            return {
                "success": False,
                "error": f"Payout processing failed: {str(e)}",
                "error_category": "technical"
            }
    
    async def check_balance_unified(
        self, 
        currencies: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Unified balance checking that replaces multiple service balance calls
        """
        try:
            processor = self.payment_processor
            if not processor:
                return {"success": False, "error": "PaymentProcessor not available"}
            
            result = await processor.check_balance(currencies)
            
            # Convert to legacy format
            return {
                "success": result.success,
                "balances": {
                    snapshot.currency: {
                        "available": float(snapshot.available_balance),
                        "total": float(snapshot.total_balance),
                        "locked": float(snapshot.locked_balance),
                        "provider": snapshot.provider.value
                    }
                    for snapshot in result.balances
                },
                "last_updated": result.last_updated.isoformat() if result.last_updated else None
            }
            
        except Exception as e:
            logger.error(f"❌ Unified balance check failed: {e}")
            return {"success": False, "error": f"Balance check failed: {str(e)}"}
    
    def _convert_destination_info(
        self, 
        destination_info: Dict[str, Any], 
        currency: str
    ) -> PaymentDestination:
        """Convert legacy destination format to PaymentDestination"""
        
        # Handle crypto destinations
        if "address" in destination_info:
            return PaymentDestination(
                type="crypto_address",
                address=destination_info.get("address"),
                currency=currency,
                network=destination_info.get("network"),
                memo=destination_info.get("memo")
            )
        
        # Handle bank destinations
        elif "account_number" in destination_info:
            return PaymentDestination(
                type="bank_account",
                bank_code=destination_info.get("bank_code"),
                account_number=destination_info.get("account_number"),
                account_name=destination_info.get("account_name"),
                currency=currency
            )
        
        # Handle saved address/bank IDs
        elif "saved_address_id" in destination_info:
            return PaymentDestination(
                type="saved_address",
                address=str(destination_info.get("saved_address_id")),
                currency=currency
            )
        
        else:
            raise ValueError(f"Unsupported destination format: {destination_info}")
    
    def _convert_payment_result_to_legacy(
        self, 
        result: PaymentResult
    ) -> Dict[str, Any]:
        """Convert PaymentResult to legacy service result format"""
        return {
            "success": result.success,
            "status": result.status.value,
            "transaction_id": result.transaction_id,
            "provider_transaction_id": result.provider_transaction_id,
            "error": result.error.value if result.error else None,
            "error_message": result.error_message,
            "error_code": result.error_code,
            "provider": result.provider.value if result.provider else None,
            "estimated_completion": result.estimated_completion,
            "payment_details": result.payment_details,
            "requires_otp": result.requires_otp,
            "requires_user_action": result.requires_user_action,
            "next_action": result.next_action,
            "actual_amount": float(result.actual_amount) if result.actual_amount else None,
            "fees_charged": float(result.fees_charged) if result.fees_charged else None,
            "exchange_rate": float(result.exchange_rate) if result.exchange_rate else None
        }


# Global adapter instance
payment_adapter = PaymentProcessorAdapter()


class FincraServiceAdapter:
    """
    Adapter for FincraService that routes to PaymentProcessor when possible,
    falls back to original FincraService for specialized operations
    """
    
    def __init__(self):
        self.legacy_service = None
        self.payment_adapter = payment_adapter
        
    @property
    def fincra_service(self) -> FincraService:
        """Lazy initialization of legacy FincraService for fallback"""
        if self.legacy_service is None:
            try:
                self.legacy_service = FincraService()
            except Exception as e:
                logger.error(f"❌ Failed to initialize legacy FincraService: {e}")
        return self.legacy_service
    
    async def process_ngn_cashout_unified(
        self,
        user_id: int,
        amount: Union[Decimal, float],
        bank_code: str,
        account_number: str,
        account_name: str,
        reference: str = None
    ) -> Dict[str, Any]:
        """
        Process NGN cashout using unified PaymentProcessor
        
        Replaces direct FincraService.process_payout() calls with
        PaymentProcessor routing.
        """
        try:
            destination_info = {
                "bank_code": bank_code,
                "account_number": account_number,
                "account_name": account_name
            }
            
            result = await self.payment_adapter.process_payout_unified(
                user_id=user_id,
                amount=amount,
                currency="NGN",
                destination_info=destination_info,
                payment_type="ngn_cashout"
            )
            
            # Add NGN-specific fields for backward compatibility
            if result.get("success"):
                result["disbursement_id"] = result.get("provider_transaction_id")
                result["bank_reference"] = reference
                
            return result
            
        except Exception as e:
            logger.error(f"❌ NGN cashout adapter failed: {e}")
            # Fallback to legacy service if available
            if self.fincra_service:
                try:
                    return await self.fincra_service.process_payout(
                        amount, bank_code, account_number, account_name, reference
                    )
                except Exception as fallback_error:
                    logger.error(f"❌ Legacy FincraService fallback failed: {fallback_error}")
            
            return {"success": False, "error": f"NGN cashout failed: {str(e)}"}
    
    async def get_ngn_balance_unified(self) -> Dict[str, Any]:
        """Get NGN balance using unified balance checking"""
        try:
            balance_result = await self.payment_adapter.check_balance_unified(["NGN"])
            
            if balance_result.get("success") and "NGN" in balance_result.get("balances", {}):
                ngn_balance = balance_result["balances"]["NGN"]
                return {
                    "success": True,
                    "balance": ngn_balance["available"],
                    "total_balance": ngn_balance["total"],
                    "locked_balance": ngn_balance["locked"],
                    "currency": "NGN"
                }
            
            return {"success": False, "error": "NGN balance not available"}
            
        except Exception as e:
            logger.error(f"❌ NGN balance adapter failed: {e}")
            # Fallback to legacy service
            if self.fincra_service:
                try:
                    return await self.fincra_service.get_balance()
                except Exception as fallback_error:
                    logger.error(f"❌ Legacy FincraService balance fallback failed: {fallback_error}")
            
            return {"success": False, "error": f"NGN balance check failed: {str(e)}"}


class KrakenServiceAdapter:
    """
    Adapter for KrakenService that routes to PaymentProcessor when possible,
    falls back to original KrakenService for specialized operations
    """
    
    def __init__(self):
        self.legacy_service = None
        self.payment_adapter = payment_adapter
    
    @property
    def kraken_service(self) -> KrakenService:
        """Lazy initialization of legacy KrakenService for fallback"""
        if self.legacy_service is None:
            try:
                self.legacy_service = KrakenService()
            except Exception as e:
                logger.error(f"❌ Failed to initialize legacy KrakenService: {e}")
        return self.legacy_service
    
    async def process_crypto_withdrawal_unified(
        self,
        user_id: int,
        amount: Union[Decimal, float],
        currency: str,
        address: str,
        network: str = None,
        memo: str = None
    ) -> Dict[str, Any]:
        """
        Process crypto withdrawal using unified PaymentProcessor
        
        Replaces direct KrakenService.withdraw() calls with
        PaymentProcessor routing.
        """
        try:
            destination_info = {
                "address": address,
                "network": network,
                "memo": memo
            }
            
            result = await self.payment_adapter.process_payout_unified(
                user_id=user_id,
                amount=amount,
                currency=currency,
                destination_info=destination_info,
                payment_type="crypto_withdrawal"
            )
            
            # Add crypto-specific fields for backward compatibility
            if result.get("success"):
                result["withdrawal_id"] = result.get("provider_transaction_id")
                result["address"] = address
                result["network"] = network
                
            return result
            
        except Exception as e:
            logger.error(f"❌ Crypto withdrawal adapter failed: {e}")
            # Fallback to legacy service if available
            if self.kraken_service:
                try:
                    return await self.kraken_service.withdraw(
                        currency, amount, address, memo
                    )
                except Exception as fallback_error:
                    logger.error(f"❌ Legacy KrakenService fallback failed: {fallback_error}")
            
            return {"success": False, "error": f"Crypto withdrawal failed: {str(e)}"}
    
    async def get_crypto_balances_unified(
        self, 
        currencies: List[str] = None
    ) -> Dict[str, Any]:
        """Get crypto balances using unified balance checking"""
        try:
            crypto_currencies = currencies or ["BTC", "ETH", "LTC", "USDT", "USD"]
            balance_result = await self.payment_adapter.check_balance_unified(crypto_currencies)
            
            if balance_result.get("success"):
                return {
                    "success": True,
                    "balances": balance_result["balances"],
                    "last_updated": balance_result["last_updated"]
                }
            
            return {"success": False, "error": "Crypto balances not available"}
            
        except Exception as e:
            logger.error(f"❌ Crypto balance adapter failed: {e}")
            # Fallback to legacy service
            if self.kraken_service:
                try:
                    return await self.kraken_service.get_balance()
                except Exception as fallback_error:
                    logger.error(f"❌ Legacy KrakenService balance fallback failed: {fallback_error}")
            
            return {"success": False, "error": f"Crypto balance check failed: {str(e)}"}


# Global adapter instances for easy import
fincra_adapter = FincraServiceAdapter()
kraken_adapter = KrakenServiceAdapter()


# Convenience functions that can replace direct service calls
async def process_unified_payout(
    user_id: int,
    amount: Union[Decimal, float],
    currency: str,
    destination_info: Dict[str, Any],
    payment_type: str = "cashout"
) -> Dict[str, Any]:
    """
    Universal payout function that replaces all direct service calls
    
    Example usage:
        # Instead of:
        # fincra_service.process_payout(...)
        # kraken_service.withdraw(...)
        
        # Use:
        result = await process_unified_payout(
            user_id=123,
            amount=100.0,
            currency="NGN",
            destination_info={"bank_code": "011", "account_number": "1234567890", "account_name": "John Doe"},
            payment_type="cashout"
        )
    """
    return await payment_adapter.process_payout_unified(
        user_id, amount, currency, destination_info, payment_type
    )


async def check_unified_balance(
    currencies: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    Universal balance checking function that replaces all direct service calls
    
    Example usage:
        # Instead of:
        # fincra_service.get_balance()
        # kraken_service.get_balance()
        
        # Use:
        balance = await check_unified_balance(["NGN", "BTC", "ETH"])
    """
    return await payment_adapter.check_balance_unified(currencies)


# Backward compatibility: Export adapters with original service names
# This allows existing imports to work without changes
FincraServiceUnified = fincra_adapter
KrakenServiceUnified = kraken_adapter
PaymentProcessorUnified = payment_adapter