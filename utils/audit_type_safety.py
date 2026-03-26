"""
Type Safety Utilities for Audit Logging
Provides comprehensive type guards and safe operations to prevent iteration errors
"""

import logging
from typing import Any, Dict, List, Set, Union

logger = logging.getLogger(__name__)


class TypeSafeOperations:
    """Provides safe operations that never fail on type mismatches"""
    
    @staticmethod
    def safe_in_check(item: Any, container: Any) -> bool:
        """
        Safely check if item is in container, handling all types
        
        Args:
            item: Item to check for
            container: Container to check in
            
        Returns:
            True if item is in container, False otherwise (including on errors)
        """
        try:
            # Convert item to string for string containers
            if isinstance(container, str):
                str_item = str(item) if not isinstance(item, str) else item
                return str_item in container
            
            # Check if container is actually iterable (not string, float, int, etc.)
            if hasattr(container, '__iter__') and not isinstance(container, (str, bytes)):
                return item in container
            
            # For non-iterable containers, always return False
            return False
            
        except (TypeError, AttributeError) as e:
            logger.debug(f"safe_in_check failed: item={type(item)}, container={type(container)}, error={e}")
            return False
    
    @staticmethod
    def safe_string_in(substring: Any, text: Any) -> bool:
        """
        Safely check if substring is in text, converting both to strings
        
        Args:
            substring: Substring to search for
            text: Text to search in
            
        Returns:
            True if substring is found in text, False otherwise
        """
        try:
            str_substring = str(substring) if not isinstance(substring, str) else substring
            str_text = str(text) if not isinstance(text, str) else text
            return str_substring in str_text
        except (TypeError, AttributeError) as e:
            logger.debug(f"safe_string_in failed: substring={type(substring)}, text={type(text)}, error={e}")
            return False
    
    @staticmethod
    def safe_lower(text: Any) -> str:
        """
        Safely convert text to lowercase
        
        Args:
            text: Text to convert
            
        Returns:
            Lowercase string or empty string on error
        """
        try:
            str_text = str(text) if not isinstance(text, str) else text
            return str_text.lower()
        except (TypeError, AttributeError) as e:
            logger.debug(f"safe_lower failed: text={type(text)}, error={e}")
            return ""
    
    @staticmethod
    def safe_startswith(text: Any, prefix: Any) -> bool:
        """
        Safely check if text starts with prefix
        
        Args:
            text: Text to check
            prefix: Prefix to check for
            
        Returns:
            True if text starts with prefix, False otherwise
        """
        try:
            str_text = str(text) if not isinstance(text, str) else text
            str_prefix = str(prefix) if not isinstance(prefix, str) else prefix
            return str_text.startswith(str_prefix)
        except (TypeError, AttributeError) as e:
            logger.debug(f"safe_startswith failed: text={type(text)}, prefix={type(prefix)}, error={e}")
            return False
    
    @staticmethod
    def safe_dict_iterate(data: Any) -> Dict[str, Any]:
        """
        Safely iterate over dictionary-like data, ensuring all keys are strings
        
        Args:
            data: Dictionary-like data to iterate
            
        Returns:
            Dictionary with guaranteed string keys
        """
        result = {}
        
        try:
            # Handle None
            if data is None:
                return {}
            
            # Handle actual dictionaries
            if isinstance(data, dict):
                for key, value in data.items():
                    str_key = str(key) if not isinstance(key, str) else key
                    result[str_key] = value
                return result
            
            # Handle objects with to_dict method
            if hasattr(data, 'to_dict') and callable(data.to_dict):
                dict_data = data.to_dict()
                if isinstance(dict_data, dict):
                    for key, value in dict_data.items():
                        str_key = str(key) if not isinstance(key, str) else key
                        result[str_key] = value
                    return result
            
            # Handle primitive types
            if isinstance(data, (str, int, float, bool)):
                return {
                    "raw_value": str(data),
                    "type": type(data).__name__
                }
            
            # Fallback for unknown types
            return {
                "raw_value": str(data),
                "type": type(data).__name__,
                "conversion_fallback": True
            }
            
        except Exception as e:
            logger.debug(f"safe_dict_iterate failed: data={type(data)}, error={e}")
            return {
                "error": f"iteration_failed: {str(e)}",
                "type": type(data).__name__
            }
    
    @staticmethod
    def safe_key_check(key: Any, allowed_keys: Union[List[str], Set[str]]) -> bool:
        """
        Safely check if key is in allowed keys list/set
        
        Args:
            key: Key to check
            allowed_keys: List or set of allowed keys
            
        Returns:
            True if key is allowed, False otherwise
        """
        try:
            str_key = str(key) if not isinstance(key, str) else key
            
            # Ensure allowed_keys is iterable
            if isinstance(allowed_keys, (list, set, tuple)):
                return str_key in allowed_keys
            
            # If allowed_keys is not properly typed, return False
            return False
            
        except (TypeError, AttributeError) as e:
            logger.debug(f"safe_key_check failed: key={type(key)}, error={e}")
            return False
    
    @staticmethod
    def ensure_string(value: Any, default: str = "") -> str:
        """
        Ensure value is a string
        
        Args:
            value: Value to convert
            default: Default value if conversion fails
            
        Returns:
            String representation of value or default
        """
        try:
            if isinstance(value, str):
                return value
            if value is None:
                return default
            return str(value)
        except Exception as e:
            logger.debug(f"ensure_string failed: value={type(value)}, error={e}")
            return default
    
    @staticmethod
    def safe_split(text: Any, delimiter: str = " ") -> List[str]:
        """
        Safely split text by delimiter
        
        Args:
            text: Text to split
            delimiter: Delimiter to split by
            
        Returns:
            List of split strings or empty list on error
        """
        try:
            str_text = str(text) if not isinstance(text, str) else text
            return str_text.split(delimiter)
        except (TypeError, AttributeError) as e:
            logger.debug(f"safe_split failed: text={type(text)}, error={e}")
            return []
    
    @staticmethod
    def safe_len(obj: Any) -> int:
        """
        Safely get length of object
        
        Args:
            obj: Object to get length of
            
        Returns:
            Length of object or 0 on error
        """
        try:
            if hasattr(obj, '__len__'):
                return len(obj)
            return 0
        except (TypeError, AttributeError) as e:
            logger.debug(f"safe_len failed: obj={type(obj)}, error={e}")
            return 0


# Global instance for easy access
type_safe = TypeSafeOperations()