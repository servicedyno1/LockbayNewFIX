"""
Shared Timestamp Utilities for Exchange and Escrow Systems
Provides consistent timezone-aware datetime handling to prevent compatibility errors
"""

import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


def get_current_utc() -> datetime:
    """
    Get current time in timezone-aware UTC format
    
    Returns:
        datetime: Current time in UTC with timezone information
    """
    return datetime.now(timezone.utc)


def format_timestamp(timestamp: Optional[datetime], default: str = "Unknown") -> str:
    """
    Format timestamp in user-friendly format matching escrow/exchange display
    
    Args:
        timestamp: The datetime to format (can be timezone-aware or naive)
        default: Default value if timestamp is None or invalid
        
    Returns:
        str: Formatted timestamp string
    """
    if timestamp:
        try:
            return timestamp.strftime("%b %d, %Y %I:%M %p")
        except (AttributeError, ValueError):
            return default
    return default


def is_expired(expiry_time: Optional[datetime]) -> bool:
    """
    Check if a given timestamp has expired (safely handles timezone compatibility)
    
    Args:
        expiry_time: The expiry timestamp to check
        
    Returns:
        bool: True if expired, False if still valid or None
    """
    if not expiry_time:
        return False
        
    try:
        current_time = get_current_utc()
        
        # Ensure both timestamps are timezone-aware for comparison
        if expiry_time.tzinfo is None:
            # If expiry_time is naive, assume UTC
            logger.warning(f"Naive datetime detected in expiry check: {expiry_time}. Assuming UTC.")
            expiry_time = expiry_time.replace(tzinfo=timezone.utc)
            
        return current_time > expiry_time
        
    except Exception as e:
        logger.error(f"Error checking expiry for {expiry_time}: {e}")
        return False  # Fail safe - assume not expired if we can't determine


def is_valid(expiry_time: Optional[datetime]) -> bool:
    """
    Check if a given timestamp is still valid (opposite of is_expired)
    
    Args:
        expiry_time: The expiry timestamp to check
        
    Returns:
        bool: True if still valid, False if expired or None
    """
    return not is_expired(expiry_time)


def safe_compare_timestamps(timestamp1: Optional[datetime], 
                          timestamp2: Optional[datetime],
                          operation: str = "greater") -> bool:
    """
    Safely compare two timestamps handling timezone compatibility
    
    Args:
        timestamp1: First timestamp
        timestamp2: Second timestamp  
        operation: Comparison operation ("greater", "less", "equal")
        
    Returns:
        bool: Result of comparison, False if either timestamp is None
    """
    if not timestamp1 or not timestamp2:
        return False
        
    try:
        # Ensure both are timezone-aware
        if timestamp1.tzinfo is None:
            timestamp1 = timestamp1.replace(tzinfo=timezone.utc)
        if timestamp2.tzinfo is None:
            timestamp2 = timestamp2.replace(tzinfo=timezone.utc)
            
        if operation == "greater":
            return timestamp1 > timestamp2
        elif operation == "less":
            return timestamp1 < timestamp2
        elif operation == "equal":
            return timestamp1 == timestamp2
        else:
            logger.warning(f"Unknown comparison operation: {operation}")
            return False
            
    except Exception as e:
        logger.error(f"Error comparing timestamps {timestamp1} and {timestamp2}: {e}")
        return False


def get_status_display_info(status: str, 
                           expiry_time: Optional[datetime] = None,
                           rate_lock_expiry: Optional[datetime] = None) -> dict:
    """
    Get comprehensive status display information for exchanges/escrows
    
    Args:
        status: Current status string
        expiry_time: Order/escrow expiry time
        rate_lock_expiry: Rate lock expiry time (for exchanges)
        
    Returns:
        dict: Status display information with emoji, text, and validity flags
    """
    # Base status mapping
    status_mapping = {
        'payment_pending': {'emoji': '⏳', 'text': 'PAYMENT PENDING'},
        'active': {'emoji': '🔒', 'text': 'ACTIVE'},
        'completed': {'emoji': '✅', 'text': 'COMPLETED'},
        'cancelled': {'emoji': '❌', 'text': 'CANCELLED'},
        'expired': {'emoji': '⏰', 'text': 'EXPIRED'},
        'failed': {'emoji': '❌', 'text': 'FAILED'},
        'created': {'emoji': '📋', 'text': 'CREATED'},
        'awaiting_deposit': {'emoji': '⏳', 'text': 'AWAITING DEPOSIT'},
        'processing': {'emoji': '⚡', 'text': 'PROCESSING'},
    }
    
    base_info = status_mapping.get(status, {'emoji': '📋', 'text': status.upper()})
    
    # Check expiry status
    is_order_expired = is_expired(expiry_time)
    is_rate_expired = is_expired(rate_lock_expiry)
    
    # Modify status based on expiry
    if status == 'payment_pending' and is_order_expired:
        base_info = {'emoji': '⏰', 'text': 'PAYMENT EXPIRED'}
    elif status in ['created', 'awaiting_deposit'] and is_rate_expired:
        base_info = {'emoji': '⏰', 'text': 'RATE EXPIRED'}
    
    return {
        'emoji': base_info['emoji'],
        'text': base_info['text'],
        'is_order_expired': is_order_expired,
        'is_rate_expired': is_rate_expired,
        'is_valid': not is_order_expired,
        'rate_valid': not is_rate_expired
    }