#!/usr/bin/env python3
"""
Unified Balance Checker Service

Replaces multiple competing balance monitoring systems with a single
entry point that uses PaymentProcessor for all balance operations.

This consolidates:
- Multiple service-specific balance checkers
- Overlapping balance validation logic
- Complex balance aggregation systems

Into a single, clean interface that uses the PaymentProcessor architecture.
"""

import logging
from typing import Dict, Any, Optional, List
from decimal import Decimal

# Import the new unified architecture
from services.migration_adapters import payment_adapter, check_unified_balance

logger = logging.getLogger(__name__)


class UnifiedBalanceChecker:
    """
    Simplified balance checking service using PaymentProcessor
    
    Replaces complex multi-service balance checking with a single,
    unified interface that provides consistent results across all providers.
    """
    
    def __init__(self):
        """Initialize the unified balance checker"""
        self.payment_adapter = payment_adapter
        logger.info("🚀 UnifiedBalanceChecker initialized with PaymentProcessor")
    
    async def check_all_balances(
        self, 
        currencies: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Check balances across all providers and currencies
        
        Replaces multiple service-specific balance calls with a single
        unified operation using PaymentProcessor.
        
        Args:
            currencies: Optional list of specific currencies to check
            
        Returns:
            Dict with balance information across all providers
        """
        try:
            logger.info(f"🔍 UNIFIED_BALANCE_CHECK: Checking balances for {currencies or 'all'} currencies")
            
            # Use unified balance checking
            result = await check_unified_balance(currencies)
            
            if result.get("success"):
                logger.info(
                    f"✅ UNIFIED_BALANCE_SUCCESS: Retrieved balances for "
                    f"{len(result.get('balances', {}))} currencies"
                )
                return {
                    "success": True,
                    "balances": result["balances"],
                    "provider_breakdown": self._create_provider_breakdown(result["balances"]),
                    "total_usd_equivalent": await self._calculate_total_usd_equivalent(result["balances"]),
                    "last_updated": result.get("last_updated"),
                    "unified_check": True
                }
            else:
                logger.error(f"❌ UNIFIED_BALANCE_FAILED: {result.get('error')}")
                return {
                    "success": False,
                    "error": result.get("error", "Balance check failed"),
                    "unified_check": True
                }
                
        except Exception as e:
            logger.error(f"❌ UNIFIED_BALANCE_ERROR: {e}")
            return {
                "success": False,
                "error": f"Balance check failed: {str(e)}",
                "unified_check": True
            }
    
    async def check_currency_balance(
        self, 
        currency: str
    ) -> Dict[str, Any]:
        """
        Check balance for a specific currency
        
        Simplified interface for single currency balance checking.
        
        Args:
            currency: Currency to check (BTC, ETH, NGN, USD, etc.)
            
        Returns:
            Dict with balance information for the currency
        """
        try:
            result = await self.check_all_balances([currency])
            
            if result.get("success") and currency in result.get("balances", {}):
                currency_balance = result["balances"][currency]
                return {
                    "success": True,
                    "currency": currency,
                    "available": currency_balance["available"],
                    "total": currency_balance["total"],
                    "locked": currency_balance["locked"],
                    "provider": currency_balance["provider"],
                    "last_updated": result.get("last_updated")
                }
            else:
                return {
                    "success": False,
                    "currency": currency,
                    "error": f"Balance not available for {currency}",
                    "available": 0.0,
                    "total": 0.0,
                    "locked": 0.0
                }
                
        except Exception as e:
            logger.error(f"❌ Error checking {currency} balance: {e}")
            return {
                "success": False,
                "currency": currency,
                "error": f"Balance check failed: {str(e)}",
                "available": 0.0,
                "total": 0.0,
                "locked": 0.0
            }
    
    async def validate_sufficient_balance(
        self, 
        currency: str, 
        required_amount: Decimal
    ) -> Dict[str, Any]:
        """
        Validate if sufficient balance is available for an operation
        
        Simplified balance validation that uses unified balance checking.
        
        Args:
            currency: Currency to check
            required_amount: Required amount for the operation
            
        Returns:
            Dict with validation result and details
        """
        try:
            balance_result = await self.check_currency_balance(currency)
            
            if not balance_result.get("success"):
                return {
                    "sufficient": False,
                    "error": f"Could not check {currency} balance",
                    "available": Decimal('0'),
                    "required": required_amount,
                    "shortfall": required_amount
                }
            
            available_balance = Decimal(str(balance_result["available"]))
            sufficient = available_balance >= required_amount
            shortfall = max(Decimal('0'), required_amount - available_balance)
            
            logger.info(
                f"💰 BALANCE_VALIDATION: {currency} - Required: {required_amount}, "
                f"Available: {available_balance}, Sufficient: {sufficient}"
            )
            
            return {
                "sufficient": sufficient,
                "currency": currency,
                "available": available_balance,
                "required": required_amount,
                "shortfall": shortfall,
                "provider": balance_result.get("provider"),
                "validation_successful": True
            }
            
        except Exception as e:
            logger.error(f"❌ Error validating {currency} balance: {e}")
            return {
                "sufficient": False,
                "error": f"Balance validation failed: {str(e)}",
                "available": Decimal('0'),
                "required": required_amount,
                "shortfall": required_amount
            }
    
    def _create_provider_breakdown(
        self, 
        balances: Dict[str, Any]
    ) -> Dict[str, Dict[str, float]]:
        """
        Create a breakdown of balances by provider
        
        Organizes balance data by provider for easier analysis.
        """
        try:
            provider_breakdown = {}
            
            for currency, balance_info in balances.items():
                provider = balance_info.get("provider", "unknown")
                
                if provider not in provider_breakdown:
                    provider_breakdown[provider] = {}
                
                provider_breakdown[provider][currency] = {
                    "available": balance_info["available"],
                    "total": balance_info["total"],
                    "locked": balance_info["locked"]
                }
            
            return provider_breakdown
            
        except Exception as e:
            logger.error(f"❌ Error creating provider breakdown: {e}")
            return {}
    
    async def _calculate_total_usd_equivalent(
        self, 
        balances: Dict[str, Any]
    ) -> float:
        """
        Calculate total USD equivalent of all balances
        
        Uses exchange rates to convert all balances to USD equivalent.
        """
        try:
            total_usd = 0.0
            
            for currency, balance_info in balances.items():
                available = balance_info["available"]
                
                if currency == "USD":
                    total_usd += available
                elif currency == "NGN":
                    # Convert NGN to USD (approximate rate)
                    total_usd += available / 1500  # Rough NGN/USD rate
                else:
                    # For crypto currencies, would need live exchange rates
                    # For now, just add as-is (could integrate with FastForex)
                    total_usd += available
            
            return round(total_usd, 2)
            
        except Exception as e:
            logger.error(f"❌ Error calculating USD equivalent: {e}")
            return 0.0


# Global instance for easy access
unified_balance_checker = UnifiedBalanceChecker()


# Convenience functions for backward compatibility
async def check_all_provider_balances(currencies: Optional[List[str]] = None) -> Dict[str, Any]:
    """Backward compatibility function for checking all balances"""
    return await unified_balance_checker.check_all_balances(currencies)


async def check_single_currency_balance(currency: str) -> Dict[str, Any]:
    """Backward compatibility function for checking single currency balance"""
    return await unified_balance_checker.check_currency_balance(currency)


async def validate_balance_for_operation(currency: str, amount: Decimal) -> Dict[str, Any]:
    """Backward compatibility function for balance validation"""
    return await unified_balance_checker.validate_sufficient_balance(currency, amount)


# Export main class and functions
__all__ = [
    'UnifiedBalanceChecker',
    'unified_balance_checker',
    'check_all_provider_balances',
    'check_single_currency_balance', 
    'validate_balance_for_operation'
]