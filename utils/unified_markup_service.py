"""
Unified Markup Service
Centralized markup calculation eliminating code duplication
"""

import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import Union, Dict
from config import Config
from utils.config_validator import ProfitProtector

logger = logging.getLogger(__name__)


class UnifiedMarkupService:
    """Centralized service for all markup calculations"""
    
    def __init__(self):
        # Validate configuration on initialization
        self._validate_markup_config()
    
    def _validate_markup_config(self) -> None:
        """Validate markup configuration on service initialization"""
        profit_check = ProfitProtector.emergency_profit_check()
        if not profit_check:
            logger.critical("🚨 EMERGENCY: Invalid markup configuration detected!")
            raise ValueError("Markup configuration validation failed - profit loss risk!")
    
    @property
    def exchange_markup_percentage(self) -> Decimal:
        """Get validated exchange markup percentage"""
        return Config.EXCHANGE_MARKUP_PERCENTAGE
    
    @property
    def escrow_fee_percentage(self) -> Decimal:
        """Get validated escrow fee percentage"""
        return Config.ESCROW_FEE_PERCENTAGE
    
    def apply_exchange_markup(
        self, 
        base_rate: Union[float, Decimal], 
        operation: str = "sell_crypto"
    ) -> Dict[str, Decimal]:
        """
        Apply exchange markup with profit guarantee
        
        Args:
            base_rate: Original exchange rate
            operation: 'buy_crypto' or 'sell_crypto'
            
        Returns:
            Dict with final_rate, markup_applied, profit_margin
        """
        try:
            base_rate = Decimal(str(base_rate))
            margin = self.exchange_markup_percentage / Decimal("100")
            
            # Emergency profit protection
            if margin <= 0:
                logger.error(f"🚨 EMERGENCY: Zero/negative markup {margin}% - using emergency 5%")
                margin = Decimal("0.05")  # Emergency fallback
            
            if operation == "buy_crypto":
                # User pays more fiat for crypto
                final_rate = base_rate * (Decimal("1") + margin)
                markup_applied = base_rate * margin
            elif operation == "sell_crypto":
                # User gets less fiat for crypto
                final_rate = base_rate / (Decimal("1") + margin)
                markup_applied = base_rate - final_rate
            else:
                final_rate = base_rate
                markup_applied = Decimal("0")
            
            return {
                "final_rate": final_rate.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP),
                "markup_applied": markup_applied.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP),
                "markup_percentage": self.exchange_markup_percentage,
                "profit_margin": markup_applied,
                "base_rate": base_rate,
                "operation": operation
            }
            
        except Exception as e:
            logger.error(f"Error applying exchange markup: {e}")
            raise ValueError(f"Markup calculation failed: {str(e)}")
    
    def calculate_escrow_fee(self, escrow_amount: Union[float, Decimal]) -> Dict[str, Decimal]:
        """
        Calculate escrow fee with profit guarantee
        
        Args:
            escrow_amount: Base escrow amount
            
        Returns:
            Dict with fee_amount, net_amount, fee_percentage
        """
        try:
            escrow_amount = Decimal(str(escrow_amount))
            fee_percentage = self.escrow_fee_percentage / Decimal("100")
            
            # Emergency profit protection
            if fee_percentage <= 0:
                logger.error(f"🚨 EMERGENCY: Zero/negative escrow fee {fee_percentage}% - using emergency 5%")
                fee_percentage = Decimal("0.05")  # Emergency fallback
            
            fee_amount = (escrow_amount * fee_percentage).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            
            net_amount = escrow_amount - fee_amount
            
            return {
                "fee_amount": fee_amount,
                "net_amount": net_amount,
                "fee_percentage": self.escrow_fee_percentage,
                "base_amount": escrow_amount,
                "profit_margin": fee_amount
            }
            
        except Exception as e:
            logger.error(f"Error calculating escrow fee: {e}")
            raise ValueError(f"Escrow fee calculation failed: {str(e)}")
    
    def convert_to_fiat_with_markup(
        self,
        crypto_amount: Union[float, Decimal],
        base_rate: Union[float, Decimal]
    ) -> Dict[str, Decimal]:
        """Convert crypto to fiat with markup applied"""
        markup_result = self.apply_exchange_markup(base_rate, "sell_crypto")
        
        crypto_amount = Decimal(str(crypto_amount))
        fiat_amount = crypto_amount * markup_result["final_rate"]
        
        return {
            "fiat_amount": fiat_amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            "crypto_amount": crypto_amount,
            "effective_rate": markup_result["final_rate"],
            "markup_applied": markup_result["markup_applied"],
            "profit_margin": crypto_amount * markup_result["markup_applied"]
        }
    
    def convert_to_crypto_with_markup(
        self,
        fiat_amount: Union[float, Decimal],
        base_rate: Union[float, Decimal]
    ) -> Dict[str, Decimal]:
        """Convert fiat to crypto with markup applied"""
        markup_result = self.apply_exchange_markup(base_rate, "buy_crypto")
        
        fiat_amount = Decimal(str(fiat_amount))
        crypto_amount = fiat_amount / markup_result["final_rate"]
        
        return {
            "crypto_amount": crypto_amount.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP),
            "fiat_amount": fiat_amount,
            "effective_rate": markup_result["final_rate"],
            "markup_applied": markup_result["markup_applied"],
            "profit_margin": fiat_amount - (fiat_amount / (Decimal("1") + self.exchange_markup_percentage / Decimal("100")))
        }
    
    def validate_profit_margins(self) -> Dict[str, any]:
        """Validate that all markup configurations ensure profit"""
        return ProfitProtector.validate_profit_configurations()


# Global unified markup service instance
unified_markup_service = UnifiedMarkupService()