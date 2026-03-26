"""
Percentage-based Cashout Fee Service
Provides smart fee calculations for crypto cashouts with configurable percentages and limits
"""

import logging
from decimal import Decimal
from typing import Dict, Any
from config import Config

logger = logging.getLogger(__name__)


class PercentageCashoutFeeService:
    """Smart percentage-based fee service for crypto cashouts"""
    
    def __init__(self):
        # Default configuration values (can be overridden by Config)
        self.percentage_fee = getattr(Config, 'PERCENTAGE_CASHOUT_FEE', Decimal('2.0'))  # 2%
        self.min_fee_usd = getattr(Config, 'MIN_PERCENTAGE_FEE_USD', Decimal('2.0'))     # $2 minimum
        self.max_fee_usd = getattr(Config, 'MAX_PERCENTAGE_FEE_USD', Decimal('100.0'))  # $100 maximum
        
        logger.info(f"PercentageCashoutFeeService initialized: {self.percentage_fee}% fee, ${self.min_fee_usd}-${self.max_fee_usd} limits")
    
    def calculate_cashout_fee(self, amount: Decimal, network: str = "USDT") -> Dict[str, Any]:
        """
        Calculate percentage-based cashout fee with intelligent edge case handling
        
        Args:
            amount: Cashout amount in USD
            network: Network type (USDT, BTC, ETH, etc.)
            
        Returns:
            Dict with success, final_fee, net_amount, fee_percentage, min_fee, max_fee
        """
        try:
            amount = Decimal(str(amount))
            
            # Calculate percentage-based fee
            percentage_fee_amount = amount * (self.percentage_fee / Decimal('100'))
            
            # SMART FEE LOGIC: Handle edge cases for small amounts
            # For very small amounts, use a lower minimum fee to make cashouts viable
            effective_min_fee = self.min_fee_usd
            
            # If the standard minimum fee would leave less than $0.50, adjust it
            if amount - self.min_fee_usd < Decimal('0.50'):
                # For small amounts, use 50% of amount as max fee, with $0.25 minimum
                max_viable_fee = amount * Decimal('0.5')  # Max 50% of amount as fee
                effective_min_fee = max(Decimal('0.25'), min(max_viable_fee, self.min_fee_usd))
                
                logger.info(f"💡 Smart fee adjustment for small amount ${amount}: "
                          f"reduced minimum fee from ${self.min_fee_usd} to ${effective_min_fee}")
            
            # Apply min/max limits with effective minimum
            final_fee = max(effective_min_fee, min(percentage_fee_amount, self.max_fee_usd))
            
            # Calculate net amount after fee
            net_amount = amount - final_fee
            
            # Final validation: ensure net amount is reasonable
            if net_amount < Decimal('0.25'):  # Minimum $0.25 net payout
                # Calculate what the minimum viable cashout amount would be
                min_viable_amount = self.min_fee_usd + Decimal('0.50')  # Fee + $0.50 minimum net
                
                return {
                    'success': False,
                    'error': f'Amount too small for cashout. Need at least ${min_viable_amount:.2f} '
                            f'(${self.min_fee_usd} fee + ${Decimal("0.50")} minimum payout)',
                    'final_fee': final_fee,
                    'net_amount': net_amount,
                    'fee_percentage': self.percentage_fee,
                    'min_fee': self.min_fee_usd,
                    'max_fee': self.max_fee_usd,
                    'suggested_minimum': min_viable_amount,
                    'smart_fee_applied': effective_min_fee != self.min_fee_usd
                }
            
            logger.debug(f"Fee calculation for ${amount} {network}: ${final_fee} fee, ${net_amount} net")
            
            return {
                'success': True,
                'final_fee': final_fee,
                'net_amount': net_amount,
                'fee_percentage': self.percentage_fee,
                'min_fee': self.min_fee_usd,
                'max_fee': self.max_fee_usd,
                'applied_limit': 'smart_minimum' if effective_min_fee != self.min_fee_usd else
                                'minimum' if final_fee == self.min_fee_usd else 
                                'maximum' if final_fee == self.max_fee_usd else 'percentage',
                'smart_fee_applied': effective_min_fee != self.min_fee_usd,
                'effective_min_fee': effective_min_fee
            }
            
        except Exception as e:
            logger.error(f"Error calculating cashout fee for ${amount}: {e}")
            return {
                'success': False,
                'error': f'Fee calculation error: {str(e)}',
                'final_fee': self.min_fee_usd,  # Fallback to minimum
                'net_amount': Decimal('0'),
                'fee_percentage': self.percentage_fee,
                'min_fee': self.min_fee_usd,
                'max_fee': self.max_fee_usd
            }


# Global service instance
percentage_cashout_fee_service = PercentageCashoutFeeService()


def calculate_cashout_fee(amount: Decimal, network: str = "USDT") -> Dict[str, Any]:
    """Convenience function for direct fee calculation"""
    return percentage_cashout_fee_service.calculate_cashout_fee(amount, network)