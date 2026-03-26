"""
Type conversion utilities for database integrity and performance

Provides safe, consistent type conversions to prevent runtime errors
and ensure efficient database operations.

Key Functions:
- normalize_telegram_id(): Converts Telegram user IDs to BigInteger for database storage
- normalize_user_id(): Handles user_id foreign key operations 
- normalize_chat_id(): Prepares chat_id for Telegram API calls
- safe_amount_conversion(): Safely converts payment amounts for financial operations
- safe_int_conversion(): Generic integer conversion with descriptive errors

Usage:
    from utils.type_converters import normalize_telegram_id
    
    # Database query - use integer directly (no string conversion)
    user = session.query(User).filter(User.telegram_id == normalize_telegram_id(telegram_id)).first()
    
    # Telegram API call - ensure integer type
    await bot.send_message(chat_id=normalize_chat_id(user.telegram_id), text="Hello")
"""

from typing import Union, Optional
from decimal import Decimal
import logging

logger = logging.getLogger(__name__)


def normalize_telegram_id(value: Union[str, int, None]) -> Optional[int]:
    """
    Normalize Telegram ID to integer for consistent database operations.
    
    Database stores telegram_id as BigInteger, but Telegram API provides integers.
    This utility ensures consistent integer handling across the codebase.
    
    Args:
        value: Telegram user ID from API (int) or database query result (str/int)
        
    Returns:
        Integer telegram_id for database operations, None if invalid
        
    Examples:
        normalize_telegram_id(123456789)      # Returns: 123456789
        normalize_telegram_id("123456789")    # Returns: 123456789
        normalize_telegram_id(None)           # Returns: None
        normalize_telegram_id("")             # Returns: None
    """
    if value is None:
        return None
        
    if isinstance(value, int):
        if value <= 0:
            logger.warning(f"Invalid telegram_id: {value} (must be positive)")
            return None
        return value
        
    if isinstance(value, str):
        if not value.strip():
            return None
            
        try:
            normalized = int(value.strip())
            if normalized <= 0:
                logger.warning(f"Invalid telegram_id: {normalized} (must be positive)")
                return None
            return normalized
        except (ValueError, TypeError) as e:
            logger.error(f"Failed to convert telegram_id '{value}' to integer: {e}")
            return None
    
    logger.error(f"Unsupported telegram_id type: {type(value)} (value: {value})")
    return None


def normalize_user_id(value: Union[str, int, None]) -> Optional[int]:
    """
    Normalize user ID for database foreign key operations.
    
    Ensures consistent integer handling for user_id foreign keys that reference
    the BigInteger users.id primary key.
    
    Args:
        value: User ID from various sources
        
    Returns:
        Integer user_id for foreign key operations, None if invalid
    """
    if value is None:
        return None
        
    if isinstance(value, int):
        if value <= 0:
            logger.warning(f"Invalid user_id: {value} (must be positive)")
            return None
        return value
        
    if isinstance(value, str):
        if not value.strip():
            return None
            
        try:
            normalized = int(value.strip())
            if normalized <= 0:
                logger.warning(f"Invalid user_id: {normalized} (must be positive)")
                return None
            return normalized
        except (ValueError, TypeError) as e:
            logger.error(f"Failed to convert user_id '{value}' to integer: {e}")
            return None
    
    logger.error(f"Unsupported user_id type: {type(value)} (value: {value})")
    return None


def normalize_chat_id(value: Union[str, int, None]) -> Optional[int]:
    """
    Normalize chat ID for Telegram API operations.
    
    Telegram API requires integer chat_id, but database may store as string.
    This ensures consistent integer handling for all Telegram operations.
    
    Args:
        value: Chat ID from database or Telegram API
        
    Returns:
        Integer chat_id for Telegram API, None if invalid
    """
    if value is None:
        return None
        
    if isinstance(value, int):
        # Chat IDs can be negative for groups/channels
        return value
        
    if isinstance(value, str):
        if not value.strip():
            return None
            
        try:
            return int(value.strip())
        except (ValueError, TypeError) as e:
            logger.error(f"Failed to convert chat_id '{value}' to integer: {e}")
            return None
    
    logger.error(f"Unsupported chat_id type: {type(value)} (value: {value})")
    return None


def safe_amount_conversion(amount_str: Union[str, int, float, Decimal, None]) -> Optional[Decimal]:
    """
    Safely convert payment amounts to Decimal for precise financial operations.
    
    Handles webhook data that arrives as strings but needs numeric processing.
    Uses Decimal to prevent floating point precision errors in financial calculations.
    
    Args:
        amount_str: Amount from webhook or API (string/numeric)
        
    Returns:
        Decimal amount for precise calculations, None if invalid
        
    Raises:
        ValueError: If amount is not convertible to valid positive number
        
    Examples:
        safe_amount_conversion("99.99")     # Returns: Decimal('99.99')
        safe_amount_conversion(100)         # Returns: Decimal('100')
        safe_amount_conversion("$1,234.56") # Returns: Decimal('1234.56')
    """
    if amount_str is None:
        return None
        
    if isinstance(amount_str, Decimal):
        if amount_str < 0:
            raise ValueError(f"Amount cannot be negative: {amount_str}")
        return amount_str
        
    if isinstance(amount_str, (int, float)):
        if amount_str < 0:
            raise ValueError(f"Amount cannot be negative: {amount_str}")
        return Decimal(str(amount_str))  # Convert through string to avoid float precision issues
        
    if isinstance(amount_str, str):
        if not amount_str.strip():
            return None
            
        try:
            # Handle common formatting
            cleaned = amount_str.strip().replace(',', '').replace('$', '')
            amount = Decimal(cleaned)
            if amount < 0:
                raise ValueError(f"Amount cannot be negative: {amount}")
            return amount
        except (ValueError, TypeError) as e:
            raise ValueError(f"Invalid amount format '{amount_str}': {e}")
    
    raise ValueError(f"Unsupported amount type: {type(amount_str)} (value: {amount_str})")


def safe_int_conversion(value: Union[str, int, None], field_name: str = "value") -> Optional[int]:
    """
    Safely convert mixed types to integers with descriptive error messages.
    
    Generic utility for any field that should be an integer but may arrive
    as string from external APIs or user input.
    
    Args:
        value: Value to convert
        field_name: Field name for error messages
        
    Returns:
        Integer value, None if invalid
        
    Raises:
        ValueError: If value is not convertible to integer
    """
    if value is None:
        return None
        
    if isinstance(value, int):
        return value
        
    if isinstance(value, str):
        if not value.strip():
            return None
            
        try:
            return int(value.strip())
        except (ValueError, TypeError) as e:
            raise ValueError(f"Invalid {field_name} format '{value}': {e}")
    
    raise ValueError(f"Unsupported {field_name} type: {type(value)} (value: {value})")