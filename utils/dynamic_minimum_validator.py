"""Dynamic minimum validation system for consistent USD equivalent checks"""

from decimal import Decimal
from typing import Dict, Optional, Tuple
import logging
from config import Config

logger = logging.getLogger(__name__)

class DynamicMinimumValidator:
    """Unified dynamic minimum validation for all financial operations"""
    
    @staticmethod
    async def validate_crypto_amount(
        crypto: str, 
        amount: Decimal, 
        min_usd: Decimal,
        operation_type: str = "transaction"
    ) -> Tuple[bool, str, Optional[Decimal]]:
        """Validate crypto amount meets USD minimum requirement
        
        Returns:
            (is_valid, error_message, required_minimum_crypto)
        """
        try:
            from services.financial_gateway import financial_gateway
            
            # Get current crypto-to-USD rate
            crypto_usd_rate = await financial_gateway.get_crypto_to_usd_rate(crypto)
            
            if not crypto_usd_rate:
                return False, f"❌ Unable to get {crypto} rate. Please try again.", None
                
            # Calculate USD equivalent of entered amount
            usd_equivalent = amount * Decimal(str(crypto_usd_rate))
            
            # Calculate minimum crypto needed
            min_crypto_needed = min_usd / Decimal(str(crypto_usd_rate))
            
            if usd_equivalent < min_usd:
                error_msg = (
                    f"❌ *Minimum {operation_type.title()}: ${min_usd:.2f} USD*\n\n"
                    f"Your amount: {amount} {crypto} = ${usd_equivalent:.2f} USD\n\n"
                    f"Required minimum: {min_crypto_needed:.6f} {crypto} (≈ ${min_usd:.2f} USD)"
                )
                return False, error_msg, min_crypto_needed
                
            return True, "", min_crypto_needed
            
        except Exception as e:
            logger.error(f"Error validating {crypto} amount: {e}")
            return False, "❌ Validation error. Please try again.", None
    
    @staticmethod
    async def validate_ngn_amount(
        amount: Decimal,
        min_usd: Decimal,
        operation_type: str = "transaction"
    ) -> Tuple[bool, str, Optional[Decimal]]:
        """Validate NGN amount meets USD minimum requirement"""
        try:
            from services.financial_gateway import financial_gateway
            
            # Get current USD-to-NGN rate
            usd_ngn_rate = await financial_gateway.get_usd_to_ngn_rate_clean()
            
            if not usd_ngn_rate:
                return False, "❌ Unable to get NGN rate. Please try again.", None
                
            # Calculate USD equivalent of entered NGN amount
            usd_equivalent = amount / Decimal(str(usd_ngn_rate))
            
            # Calculate minimum NGN needed
            min_ngn_needed = min_usd * Decimal(str(usd_ngn_rate))
            
            if usd_equivalent < min_usd:
                error_msg = (
                    f"❌ *Minimum {operation_type.title()}: ${min_usd:.2f} USD*\n\n"
                    f"Your amount: ₦{amount:,.0f} = ${usd_equivalent:.2f} USD\n\n"
                    f"Required minimum: ₦{min_ngn_needed:,.0f} (≈ ${min_usd:.2f} USD)"
                )
                return False, error_msg, min_ngn_needed
                
            return True, "", min_ngn_needed
            
        except Exception as e:
            logger.error(f"Error validating NGN amount: {e}")
            return False, "❌ Validation error. Please try again.", None
    
    @staticmethod
    async def get_minimum_crypto_amounts() -> Dict[str, Decimal]:
        """Get dynamic minimum crypto amounts for all supported currencies"""
        minimums = {}
        min_usd = Config.MIN_EXCHANGE_AMOUNT_USD
        
        try:
            from services.financial_gateway import financial_gateway
            
            for crypto in Config.SUPPORTED_CURRENCIES:
                if crypto == "NGN":
                    continue
                    
                crypto_usd_rate = await financial_gateway.get_crypto_to_usd_rate(crypto)
                if crypto_usd_rate:
                    minimums[crypto] = min_usd / Decimal(str(crypto_usd_rate))
                else:
                    # Fallback to conservative minimums
                    fallback_minimums = {
                        "BTC": Decimal("0.0002"),
                        "ETH": Decimal("0.002"),
                        "LTC": Decimal("0.06"),
                        "DOGE": Decimal("50"),
                        "BCH": Decimal("0.02"),
                        "BSC": Decimal("0.002"),
                        "TRX": Decimal("500"),
                        "USDT-ERC20": Decimal("5"),
                        "USDT-TRC20": Decimal("5"),
                    }
                    minimums[crypto] = fallback_minimums.get(crypto, Decimal("0.01"))
                    
        except Exception as e:
            logger.error(f"Error calculating dynamic minimums: {e}")
            
        return minimums

    @staticmethod
    async def calculate_adaptive_security_threshold(base_amount: Decimal, multiplier: float = 3.0, minimum_usd: Decimal = Decimal("100.00")) -> Decimal:
        """Calculate adaptive security threshold based on amount and multiplier"""
        try:
            adaptive_threshold = base_amount * Decimal(str(multiplier))
            return max(minimum_usd, adaptive_threshold)
        except Exception as e:
            logger.error(f"Error calculating adaptive threshold: {e}")
            return minimum_usd

    @staticmethod
    async def get_network_fee_aware_minimum(network: str = "TRC20") -> Decimal:
        """Get network-fee-aware minimum for operations"""
        try:
            # Base minimums per network considering fees
            network_minimums = {
                "BTC": Decimal("15.00"),      # High fees
                "ETH": Decimal("10.00"),      # Moderate fees  
                "ERC20": Decimal("8.00"),     # Gas costs
                "TRC20": Decimal("3.00"),     # Low fees
                "LTC": Decimal("3.00"),       # Low fees
                "DOGE": Decimal("2.00"),      # Very low fees
                "BCH": Decimal("3.00"),       # Low fees
                "TRX": Decimal("2.00"),       # Very low fees
            }
            
            return network_minimums.get(network.upper(), Decimal("5.00"))
        except Exception as e:
            logger.error(f"Error getting network-aware minimum for {network}: {e}")
            return Decimal("5.00")
