"""
ConditionalOTPService - Determines OTP requirements for unified transactions

This service implements the exact document specification:
- OTP Required: ONLY for wallet balance cashouts (both buyer and seller wallets)
- No OTP Required: Exchange deposits, escrow releases, escrow refunds

Key simplifications from document:
- No risk-based controls needed
- No amount thresholds  
- No user verification levels
- Simple binary decision: wallet cashout = OTP, everything else = no OTP
"""

import logging
from typing import Dict, Any
from enum import Enum

# Import from models for type safety
from models import UnifiedTransactionType, UnifiedTransactionStatus

logger = logging.getLogger(__name__)


class OTPRequirementReason(Enum):
    """Reasons for OTP requirement decisions"""
    WALLET_CASHOUT_REQUIRED = "wallet_cashout_required"
    EXCHANGE_NO_OTP = "exchange_no_otp" 
    ESCROW_NO_OTP = "escrow_no_otp"
    UNKNOWN_TYPE = "unknown_type"


class ConditionalOTPService:
    """
    Service for determining OTP requirements based on transaction type
    
    According to document specification:
    - WALLET_CASHOUT: Requires OTP (users withdrawing from their wallet balance)
    - EXCHANGE_SELL_CRYPTO: No OTP (crypto → fiat exchange)
    - EXCHANGE_BUY_CRYPTO: No OTP (fiat → crypto exchange)  
    - ESCROW: No OTP (escrow releases and refunds)
    """
    
    @staticmethod
    def requires_otp(transaction_type: str) -> bool:
        """
        Determine if OTP is required for a transaction type
        
        Args:
            transaction_type: UnifiedTransactionType enum value as string
            
        Returns:
            bool: True if OTP required, False otherwise
        """
        try:
            # Convert string to enum for type safety
            tx_type = UnifiedTransactionType(transaction_type)
            
            # Simple binary logic per document specification
            if tx_type == UnifiedTransactionType.WALLET_CASHOUT:
                logger.debug(f"🔐 OTP Required: {transaction_type} - wallet balance cashout")
                return True
            else:
                logger.debug(f"✅ No OTP Required: {transaction_type} - exchange/escrow transaction")
                return False
                
        except ValueError:
            logger.warning(f"⚠️ Unknown transaction type: {transaction_type}, defaulting to no OTP")
            return False
    
    @staticmethod
    def requires_otp_enum(transaction_type: UnifiedTransactionType) -> bool:
        """
        Determine if OTP is required for a transaction type (enum version)
        
        Args:
            transaction_type: UnifiedTransactionType enum
            
        Returns:
            bool: True if OTP required, False otherwise
        """
        # Simple binary logic per document specification
        if transaction_type == UnifiedTransactionType.WALLET_CASHOUT:
            logger.debug(f"🔐 OTP Required: {transaction_type.value} - wallet balance cashout")
            return True
        else:
            logger.debug(f"✅ No OTP Required: {transaction_type.value} - exchange/escrow transaction")
            return False
    
    @staticmethod
    def get_otp_flow_status(transaction_type: str) -> str:
        """
        Get the next status for transaction based on OTP requirement
        
        Args:
            transaction_type: UnifiedTransactionType enum value as string
            
        Returns:
            str: Next status ("otp_pending" or "processing")
        """
        if ConditionalOTPService.requires_otp(transaction_type):
            return UnifiedTransactionStatus.OTP_PENDING.value
        else:
            return UnifiedTransactionStatus.PROCESSING.value
    
    @staticmethod
    def get_otp_flow_status_enum(transaction_type: UnifiedTransactionType) -> UnifiedTransactionStatus:
        """
        Get the next status for transaction based on OTP requirement (enum version)
        
        Args:
            transaction_type: UnifiedTransactionType enum
            
        Returns:
            UnifiedTransactionStatus: Next status enum
        """
        if ConditionalOTPService.requires_otp_enum(transaction_type):
            return UnifiedTransactionStatus.OTP_PENDING
        else:
            return UnifiedTransactionStatus.PROCESSING
    
    @staticmethod
    def get_requirement_reason(transaction_type: str) -> OTPRequirementReason:
        """
        Get the reason for OTP requirement decision
        
        Args:
            transaction_type: UnifiedTransactionType enum value as string
            
        Returns:
            OTPRequirementReason: Reason for the decision
        """
        try:
            tx_type = UnifiedTransactionType(transaction_type)
            
            if tx_type == UnifiedTransactionType.WALLET_CASHOUT:
                return OTPRequirementReason.WALLET_CASHOUT_REQUIRED
            elif tx_type in [UnifiedTransactionType.EXCHANGE_SELL_CRYPTO, UnifiedTransactionType.EXCHANGE_BUY_CRYPTO]:
                return OTPRequirementReason.EXCHANGE_NO_OTP
            elif tx_type == UnifiedTransactionType.ESCROW:
                return OTPRequirementReason.ESCROW_NO_OTP
            else:
                return OTPRequirementReason.UNKNOWN_TYPE
                
        except ValueError:
            return OTPRequirementReason.UNKNOWN_TYPE
    
    @staticmethod
    def get_otp_decision_summary(transaction_type: str) -> Dict[str, Any]:
        """
        Get comprehensive OTP decision summary for logging/debugging
        
        Args:
            transaction_type: UnifiedTransactionType enum value as string
            
        Returns:
            Dict containing decision details
        """
        requires_otp = ConditionalOTPService.requires_otp(transaction_type)
        next_status = ConditionalOTPService.get_otp_flow_status(transaction_type)
        reason = ConditionalOTPService.get_requirement_reason(transaction_type)
        
        return {
            "transaction_type": transaction_type,
            "requires_otp": requires_otp,
            "next_status": next_status,
            "reason": reason.value,
            "summary": f"{'OTP Required' if requires_otp else 'No OTP Required'} - {reason.value}"
        }


# Convenience functions for backward compatibility and direct usage
def requires_otp_for_transaction(transaction_type: str) -> bool:
    """Convenience function for OTP requirement check"""
    return ConditionalOTPService.requires_otp(transaction_type)


def get_next_status_after_authorization(transaction_type: str) -> str:
    """Convenience function for getting next status after authorization phase"""
    return ConditionalOTPService.get_otp_flow_status(transaction_type)


# Export main class and convenience functions
__all__ = [
    'ConditionalOTPService',
    'OTPRequirementReason',
    'requires_otp_for_transaction',
    'get_next_status_after_authorization'
]