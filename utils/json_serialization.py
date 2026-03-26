"""
JSON Serialization Utilities for LockBay
Handles conversion of non-JSON-serializable types (Decimal, datetime, etc.) to JSON-safe formats
"""

import json
from decimal import Decimal
from datetime import datetime, date
from typing import Any, Dict


def ensure_json_safe(data: Any) -> Any:
    """
    Recursively convert non-JSON-serializable types to JSON-safe formats
    
    Args:
        data: Any data structure (dict, list, primitive, etc.)
        
    Returns:
        JSON-safe version of the data
        
    Conversions:
        - Decimal -> str (preserves precision)
        - datetime/date -> ISO format string
        - None, bool, int, float, str -> unchanged
        - dict -> recursively processed
        - list/tuple -> recursively processed
        - other -> str representation
    """
    if data is None:
        return None
    
    if isinstance(data, (bool, int, float, str)):
        return data
    
    if isinstance(data, Decimal):
        return str(data)
    
    if isinstance(data, (datetime, date)):
        return data.isoformat()
    
    if isinstance(data, dict):
        return {key: ensure_json_safe(value) for key, value in data.items()}
    
    if isinstance(data, (list, tuple)):
        return [ensure_json_safe(item) for item in data]
    
    # For any other type, convert to string representation
    return str(data)


def sanitize_for_json_column(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Sanitize a dictionary for storage in a PostgreSQL JSON/JSONB column
    
    Args:
        data: Dictionary that may contain non-JSON-serializable values
        
    Returns:
        JSON-safe dictionary ready for database storage
    """
    if not data:
        return {}
    
    return ensure_json_safe(data)


def test_json_serialization(data: Any) -> bool:
    """
    Test if data can be JSON serialized (useful for debugging)
    
    Args:
        data: Data to test
        
    Returns:
        True if serializable, False otherwise
    """
    try:
        json.dumps(data)
        return True
    except (TypeError, ValueError):
        return False
