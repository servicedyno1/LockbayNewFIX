"""
UTID Generator - Unified Trade ID System
Generates unique escrow and trade identifiers

DEPRECATED: This module now delegates to UniversalIDGenerator for consistency.
Use UniversalIDGenerator directly for new code.
"""

from typing import Optional
from utils.universal_id_generator import UniversalIDGenerator

class UTIDGenerator:
    """DEPRECATED: Unified Trade ID Generator - now delegates to UniversalIDGenerator"""
    
    @staticmethod
    def generate_escrow_utid(prefix: str = "ES") -> str:
        """
        Generate unique escrow trade ID using UniversalIDGenerator
        
        DEPRECATED: Use UniversalIDGenerator.generate_escrow_id() directly.
        """
        # Delegate to UniversalIDGenerator for consistent ID generation
        return UniversalIDGenerator.generate_escrow_id()
    
    @staticmethod
    def generate_transaction_utid(prefix: str = "TX") -> str:
        """
        Generate unique transaction ID using UniversalIDGenerator
        
        DEPRECATED: Use UniversalIDGenerator.generate_transaction_id() directly.
        """
        return UniversalIDGenerator.generate_transaction_id()
    
    @staticmethod
    def generate_cashout_utid(prefix: str = "CO") -> str:
        """
        Generate unique cashout ID using UniversalIDGenerator
        
        DEPRECATED: Use UniversalIDGenerator.generate_cashout_id() directly.
        """
        return UniversalIDGenerator.generate_cashout_id()
    
    @staticmethod
    def generate_exchange_utid(prefix: str = "EX") -> str:
        """
        Generate unique exchange order ID using UniversalIDGenerator
        
        DEPRECATED: Use UniversalIDGenerator.generate_exchange_id() directly.
        """
        return UniversalIDGenerator.generate_exchange_id()
    
    @staticmethod
    def generate_refund_utid(prefix: str = "RF") -> str:
        """
        Generate unique refund ID using UniversalIDGenerator
        
        DEPRECATED: Use UniversalIDGenerator.generate_refund_id() directly.
        """
        return UniversalIDGenerator.generate_refund_id()
    
    @staticmethod
    def validate_utid_format(utid: str) -> bool:
        """
        Validate UTID format: {PREFIX}{MMDDYY}{XXXX}
        Returns True if format is valid, False otherwise
        """
        if not utid or len(utid) < 10:
            return False
        
        # Check if it has a 2-letter prefix
        if len(utid) < 10 or not utid[:2].isalpha():
            return False
        
        # Extract parts
        utid[:2]
        date_part = utid[2:8]
        suffix = utid[8:]
        
        # Validate date part (should be 6 digits)
        if len(date_part) != 6 or not date_part.isdigit():
            return False
        
        # Validate suffix (should be 4 alphanumeric characters)
        if len(suffix) != 4 or not suffix.isalnum():
            return False
        
        # Validate date format MMDDYY
        try:
            month = int(date_part[:2])
            day = int(date_part[2:4])
            int(date_part[4:6])
            
            # Basic date validation
            if month < 1 or month > 12:
                return False
            if day < 1 or day > 31:
                return False
            # Year validation (assume 20XX for years 00-99)
            
        except ValueError:
            return False
        
        return True
    
    @staticmethod
    def extract_date_from_utid(utid: str) -> Optional[str]:
        """
        Extract date from UTID in MMDDYY format
        Returns date string or None if invalid
        """
        if not UTIDGenerator.validate_utid_format(utid):
            return None
        
        date_part = utid[2:8]  # Extract MMDDYY
        
        # Format as MM/DD/YY for readability
        month = date_part[:2]
        day = date_part[2:4]
        year = date_part[4:6]
        
        return f"{month}/{day}/{year}"
    
    @staticmethod
    def get_prefix_from_utid(utid: str) -> Optional[str]:
        """
        Extract prefix from UTID
        Returns prefix or None if invalid
        """
        if not UTIDGenerator.validate_utid_format(utid):
            return None
        
        return utid[:2]

# Backward compatibility aliases - now delegate to UniversalIDGenerator
def generate_escrow_utid() -> str:
    """Legacy function for backward compatibility - delegates to UniversalIDGenerator"""
    return UniversalIDGenerator.generate_escrow_id()

def generate_transaction_utid() -> str:
    """Legacy function for backward compatibility - delegates to UniversalIDGenerator"""
    return UniversalIDGenerator.generate_transaction_id()

def generate_refund_id() -> str:
    """Generate unique refund ID - delegates to UniversalIDGenerator"""
    return UniversalIDGenerator.generate_refund_id()