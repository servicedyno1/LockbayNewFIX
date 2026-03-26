"""
Secure Amount Parser for Financial Input Validation
Prevents dangerous parsing vulnerabilities like "2,1" → "21"

This module provides secure validation for monetary amounts with proper
format checking to prevent financial losses from ambiguous inputs.
"""

import re
import logging
from decimal import Decimal, InvalidOperation
from typing import Tuple

logger = logging.getLogger(__name__)

class AmountValidationError(Exception):
    """Raised when amount validation fails with specific error message"""
    pass

class SecureAmountParser:
    """
    Secure parser for monetary amounts with comprehensive validation
    
    Designed to prevent financial vulnerabilities by:
    - Validating format BEFORE parsing
    - Rejecting ambiguous inputs like "2,1" 
    - Supporting proper thousands separators
    - Providing clear error messages
    """
    
    # Regex patterns for validation
    VALID_PATTERNS = {
        # Integer: 100, 1000, 50000
        'integer': re.compile(r'^[1-9]\d{0,2}(?:,\d{3})*$|^[1-9]\d*$|^0$'),
        
        # Decimal: 100.50, 1000.99, 50000.00  
        'decimal': re.compile(r'^[1-9]\d{0,2}(?:,\d{3})*\.\d{1,2}$|^[1-9]\d*\.\d{1,2}$|^0\.\d{1,2}$'),
        
        # Combined pattern for full validation
        'full': re.compile(r'^(?:[1-9]\d{0,2}(?:,\d{3})*(?:\.\d{1,2})?|[1-9]\d*(?:\.\d{1,2})?|0(?:\.\d{1,2})?)$')
    }
    
    @staticmethod
    def validate_and_parse(input_text: str, currency_symbol: str = "$") -> Tuple[Decimal, str]:
        """
        Securely validate and parse amount input with comprehensive error checking
        
        Args:
            input_text: Raw user input (e.g., "$100.50", "1,000", "2,1")
            currency_symbol: Expected currency symbol (default: "$")
            
        Returns:
            Tuple[Decimal, str]: (parsed_amount, validation_message)
            
        Raises:
            AmountValidationError: If input is invalid with specific error message
            
        Examples:
            ✅ Valid: "100", "100.50", "1,000", "1,000.50", "$12,345.67"
            ❌ Invalid: "2,1", "12,34,56", "1,23", "12..34", "12,000,000.50.25"
        """
        if not input_text or not isinstance(input_text, str):
            raise AmountValidationError("Amount cannot be empty")
        
        # Clean and normalize input
        original_input = input_text
        cleaned = input_text.strip()
        
        # Remove currency symbol if present
        if cleaned.startswith(currency_symbol):
            cleaned = cleaned[len(currency_symbol):].strip()
        
        # Log the parsing attempt for audit trail
        logger.info(f"🔒 SECURE_PARSER: Validating input '{original_input}' → cleaned: '{cleaned}'")
        
        # Basic format checks
        if not cleaned:
            raise AmountValidationError(f"Please enter an amount after the {currency_symbol} symbol")
        
        # Check for multiple decimal points
        if cleaned.count('.') > 1:
            raise AmountValidationError("Invalid format: Multiple decimal points found. Use format like '100.50'")
        
        # Check for invalid characters
        if not re.match(r'^[\d,\.]+$', cleaned):
            invalid_chars = set(cleaned) - set('0123456789,.')
            raise AmountValidationError(f"Invalid characters found: {', '.join(invalid_chars)}. Use only numbers, commas, and decimal point")
        
        # Split on decimal point to validate integer and decimal parts separately
        if '.' in cleaned:
            integer_part, decimal_part = cleaned.split('.')
            
            # Validate decimal part
            if len(decimal_part) > 2:
                raise AmountValidationError("Too many decimal places. Use format like '100.50' (max 2 decimal places)")
            
            if len(decimal_part) == 0:
                raise AmountValidationError("Invalid decimal format. Use '100.50' instead of '100.'")
            
            if not decimal_part.isdigit():
                raise AmountValidationError("Decimal part must contain only digits")
        else:
            integer_part = cleaned
            decimal_part = None
        
        # Validate thousands separators in integer part
        if ',' in integer_part:
            validation_result = SecureAmountParser._validate_thousands_separators(integer_part)
            if not validation_result[0]:
                raise AmountValidationError(validation_result[1])
        
        # Final pattern validation
        if not SecureAmountParser.VALID_PATTERNS['full'].match(cleaned):
            raise AmountValidationError(
                "Invalid amount format. Examples of valid formats:\n"
                "• Simple: 100, 250.50\n"
                "• With commas: 1,000, 12,500.75\n"
                "• Avoid: 2,1 or 12,34,56"
            )
        
        try:
            # Convert to Decimal for precise financial calculations
            # Remove commas for parsing (safe now after validation)
            clean_for_parsing = cleaned.replace(',', '')
            amount = Decimal(clean_for_parsing)
            
            # Additional business rule validations
            if amount < 0:
                raise AmountValidationError("Amount cannot be negative")
            
            if amount == 0:
                raise AmountValidationError("Amount must be greater than zero")
            
            # Check for reasonable upper limits (prevent overflow)
            max_amount = Decimal('999999999.99')  # ~1 billion limit
            if amount > max_amount:
                raise AmountValidationError(f"Amount exceeds maximum limit of {currency_symbol}{max_amount:,}")
            
            success_msg = f"✅ Parsed {currency_symbol}{amount:,} from input '{original_input}'"
            logger.info(f"🔒 SECURE_PARSER: {success_msg}")
            
            return amount, success_msg
            
        except InvalidOperation:
            raise AmountValidationError("Invalid number format. Please enter a valid amount like '100.50'")
    
    @staticmethod
    def _validate_thousands_separators(integer_part: str) -> Tuple[bool, str]:
        """
        Validate that thousands separators (commas) are used correctly
        
        Valid: "1,000", "12,000", "1,000,000"
        Invalid: "1,00", "12,34", "1,0000", "2,1"
        
        Returns:
            Tuple[bool, str]: (is_valid, error_message)
        """
        # Split by commas and validate each group
        groups = integer_part.split(',')
        
        # First group can be 1-3 digits
        first_group = groups[0]
        if not first_group or not first_group.isdigit() or len(first_group) > 3:
            return False, "Invalid comma placement. First group should be 1-3 digits (e.g., '1,000' not ',100')"
        
        if len(first_group) == 0:
            return False, "Invalid format: comma cannot be at the beginning"
        
        # All subsequent groups must be exactly 3 digits
        for i, group in enumerate(groups[1:], 1):
            if len(group) != 3:
                if len(group) < 3:
                    return False, f"Invalid comma placement. Use format like '1,000' not '{integer_part}' (group {i+1} has {len(group)} digits, need 3)"
                else:
                    return False, f"Invalid comma placement. Too many digits in group {i+1}"
            
            if not group.isdigit():
                return False, f"Invalid characters in thousands group {i+1}"
        
        # Special check for ambiguous cases like "2,1" 
        if len(groups) == 2 and len(groups[1]) == 1:
            return False, "Ambiguous format. Use '2.1' for decimals or '2,100' for thousands. '2,1' is not allowed"
        
        return True, "Valid thousands separator format"
    
    @staticmethod
    def quick_validate(input_text: str) -> bool:
        """
        Quick validation check without full parsing
        
        Args:
            input_text: Raw user input
            
        Returns:
            bool: True if format appears valid, False otherwise
        """
        try:
            SecureAmountParser.validate_and_parse(input_text)
            return True
        except AmountValidationError:
            return False
    
    @staticmethod
    def get_format_examples() -> str:
        """
        Get user-friendly examples of valid formats
        
        Returns:
            str: Formatted examples for user guidance
        """
        return (
            "💡 Valid Amount Formats:\n"
            "• Simple: `100`, `250.50`\n" 
            "• With commas: `1,000`, `12,500.75`\n"
            "• Large amounts: `1,000,000.00`\n\n"
            "❌ Invalid Formats:\n"
            "• `2,1` (ambiguous - use `2.10` or `21`)\n"
            "• `12,34,56` (incorrect comma placement)\n"
            "• `100.` (incomplete decimal)\n"
            "• `100.999` (too many decimal places)"
        )

# Convenience functions for backward compatibility
def parse_secure_amount(input_text: str, currency_symbol: str = "$") -> Decimal:
    """
    Legacy-compatible function that returns just the parsed amount
    
    Raises AmountValidationError on invalid input
    """
    amount, _ = SecureAmountParser.validate_and_parse(input_text, currency_symbol)
    return amount

def validate_amount_format(input_text: str) -> bool:
    """
    Legacy-compatible function for quick validation
    """
    return SecureAmountParser.quick_validate(input_text)