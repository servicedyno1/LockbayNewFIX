"""Context Validation Middleware for Conversation State Integrity"""

import logging
from typing import Dict, Any, List, Callable
from functools import wraps
from telegram import Update
from telegram.ext import ContextTypes
from utils.navigation import safe_navigation_fallback
from utils.conversation_protection import ConversationTimeout

logger = logging.getLogger(__name__)


def _require_user_data_v1(*required_fields):
    """
    Decorator to validate user_data before executing handler

    Args:
        *required_fields: Fields that must be present in user_data

    Returns:
        Decorated function with validation
    """

    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
            try:
                # Validate basic context
                ContextValidator.validate_user_context(update, context)

                # Validate user data
                ContextValidator.validate_user_data(context, list(required_fields))

                # Execute original function
                return await func(update, context)

            except ContextValidationError as e:
                logger.warning(f"Context validation failed in {func.__name__}: {e}")
                return await safe_navigation_fallback(
                    update,
                    context,
                    message="⚠️ **Session Error**\n\nThere was an issue with your session data.\n\nReturning to main menu for a fresh start...",
                    cleanup_conversation=True,
                )
            except Exception as e:
                logger.error(
                    f"Unexpected error in validated handler {func.__name__}: {e}"
                )
                return await safe_navigation_fallback(update, context)

        return wrapper

    return decorator


async def _validate_and_recover_context_v1(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    required_data: Dict[str, List[str]] = None,
) -> bool:
    """
    Validate context and attempt recovery if possible

    Args:
        update: Telegram update object
        context: Bot context
        required_data: Dict mapping data keys to required fields

    Returns:
        True if context is valid or recovered, False if recovery failed
    """
    try:
        # Validate basic user context
        ContextValidator.validate_user_context(update, context)

        # Validate required data if specified
        if required_data:
            for data_key, fields in required_data.items():
                if data_key == "user_data":
                    ContextValidator.validate_user_data(context, fields)
                else:
                    ContextValidator.validate_conversation_data(
                        context, data_key, fields
                    )

        return True

    except ContextValidationError as e:
        logger.warning(f"Context validation failed, attempting recovery: {e}")

        # Attempt basic recovery
        try:
            # Ensure user_data exists
            if not context.user_data:
                # Fix: Cannot assign new value to user_data - use clear() if exists
                if hasattr(context, 'user_data') and context.user_data is not None:
                    context.user_data.clear()
                else:
                    # If user_data is None, we need to work with what we have
                    pass

            # Add basic user info if missing
            if update.effective_user:
                context.user_data.setdefault("user_id", update.effective_user.id)
                context.user_data.setdefault("telegram_id", update.effective_user.id)
                context.user_data.setdefault("username", update.effective_user.username)
                context.user_data.setdefault(
                    "first_name", update.effective_user.first_name
                )

            logger.info("Basic context recovery completed")
            return True

        except Exception as recovery_error:
            logger.error(f"Context recovery failed: {recovery_error}")
            return False

    except Exception as e:
        logger.error(f"Unexpected error in context validation: {e}")
        return False


class ContextValidationError(Exception):
    """Raised when context validation fails"""

    pass


class ContextValidator:
    """Validates conversation context and state integrity"""

    @staticmethod
    def validate_user_data(
        context: ContextTypes.DEFAULT_TYPE,
        required_fields: List[str] = None,
        optional_fields: List[str] = None,
    ) -> Dict[str, Any]:
        """
        Validate user_data structure and required fields

        Args:
            context: Bot context
            required_fields: Fields that must be present
            optional_fields: Fields that are optional but validated if present

        Returns:
            Validated user_data dictionary

        Raises:
            ContextValidationError: If validation fails
        """
        if not context.user_data:
            raise ContextValidationError("user_data is None or empty")

        if required_fields:
            for field in required_fields:
                if field not in context.user_data:
                    raise ContextValidationError(
                        f"Required field '{field}' missing from user_data"
                    )

                # Check for None values in required fields
                if context.user_data[field] is None:
                    raise ContextValidationError(f"Required field '{field}' is None")

        return context.user_data

    @staticmethod
    def validate_conversation_data(
        context: ContextTypes.DEFAULT_TYPE,
        conversation_key: str,
        required_fields: List[str] = None,
    ) -> Dict[str, Any]:
        """
        Validate specific conversation data structure

        Args:
            context: Bot context
            conversation_key: Key for conversation data (e.g., 'escrow_data')
            required_fields: Required fields within conversation data

        Returns:
            Validated conversation data

        Raises:
            ContextValidationError: If validation fails
        """
        user_data = ContextValidator.validate_user_data(context, [conversation_key])
        conversation_data = user_data[conversation_key]

        if not isinstance(conversation_data, dict):
            raise ContextValidationError(
                f"Conversation data '{conversation_key}' is not a dictionary"
            )

        if required_fields:
            for field in required_fields:
                if field not in conversation_data:
                    raise ContextValidationError(
                        f"Required field '{field}' missing from {conversation_key}"
                    )

        return conversation_data

    @staticmethod
    def validate_user_context(
        update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> bool:
        """
        Validate basic user context requirements

        Args:
            update: Telegram update object
            context: Bot context

        Returns:
            True if context is valid

        Raises:
            ContextValidationError: If validation fails
        """
        if not update.effective_user:
            raise ContextValidationError("No effective user in update")

        if not update.effective_chat:
            raise ContextValidationError("No effective chat in update")

        # Check conversation timeout
        user_id = update.effective_user.id
        if ConversationTimeout.is_expired(user_id):
            raise ContextValidationError(f"Conversation expired for user {user_id}")

        return True


def require_user_data(*required_fields):
    """
    Decorator to validate user_data before executing handler

    Args:
        *required_fields: Fields that must be present in user_data

    Returns:
        Decorated function with validation
    """

    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
            try:
                # Validate basic context
                ContextValidator.validate_user_context(update, context)

                # Validate user data
                ContextValidator.validate_user_data(context, list(required_fields))

                # Execute original function
                return await func(update, context)

            except ContextValidationError as e:
                logger.warning(f"Context validation failed in {func.__name__}: {e}")
                return await safe_navigation_fallback(
                    update,
                    context,
                    message="⚠️ **Session Error**\n\nThere was an issue with your session data.\n\nReturning to main menu for a fresh start...",
                    cleanup_conversation=True,
                )
            except Exception as e:
                logger.error(
                    f"Unexpected error in validated handler {func.__name__}: {e}"
                )
                return await safe_navigation_fallback(update, context)

        return wrapper

    return decorator


def require_conversation_data(conversation_key: str, *required_fields):
    """
    Decorator to validate conversation-specific data

    Args:
        conversation_key: Key for conversation data (e.g., 'escrow_data')
        *required_fields: Required fields within conversation data

    Returns:
        Decorated function with conversation data validation
    """

    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
            try:
                # Validate basic context
                ContextValidator.validate_user_context(update, context)

                # Validate conversation data
                ContextValidator.validate_conversation_data(
                    context, conversation_key, list(required_fields)
                )

                # Execute original function
                return await func(update, context)

            except ContextValidationError as e:
                logger.warning(
                    f"Conversation validation failed in {func.__name__}: {e}"
                )
                return await safe_navigation_fallback(
                    update,
                    context,
                    message=f"⚠️ **Session Error**\n\nYour {conversation_key.replace('_', ' ')} session has expired or is corrupted.\n\nPlease start over from the main menu.",
                    cleanup_conversation=True,
                )
            except Exception as e:
                logger.error(
                    f"Unexpected error in conversation handler {func.__name__}: {e}"
                )
                return await safe_navigation_fallback(update, context)

        return wrapper

    return decorator


def safe_context_access(
    context: ContextTypes.DEFAULT_TYPE, key_path: str, default: Any = None
) -> Any:
    """
    Safely access nested context data with fallback

    Args:
        context: Bot context
        key_path: Dot-separated path to data (e.g., 'user_data.escrow_data.amount')
        default: Default value if path doesn't exist

    Returns:
        Value at key_path or default
    """
    try:
        keys = key_path.split(".")
        current = context

        for key in keys:
            if hasattr(current, key):
                current = getattr(current, key)
            elif isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return default

        return current

    except Exception as e:
        logger.warning(f"Error accessing context path '{key_path}': {e}")
        return default


def clean_conversation_state(
    context: ContextTypes.DEFAULT_TYPE, preserve_keys: List[str] = None
) -> None:
    """
    Clean conversation state while preserving essential data

    Args:
        context: Bot context
        preserve_keys: Keys to preserve during cleanup
    """
    if not context.user_data:
        return

    # Default keys to preserve
    default_preserve = ["user_id", "telegram_id", "username", "first_name", "last_name"]
    preserve_keys = preserve_keys or default_preserve

    # Create backup of preserved data
    preserved_data = {}
    for key in preserve_keys:
        if key in context.user_data:
            preserved_data[key] = context.user_data[key]

    # Clear all data
    context.user_data.clear()

    # Restore preserved data
    context.user_data.update(preserved_data)

    logger.info(
        f"Cleaned conversation state, preserved keys: {list(preserved_data.keys())}"
    )


async def validate_and_recover_context(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    required_data: Dict[str, List[str]] = None,
) -> bool:
    """
    Validate context and attempt recovery if possible

    Args:
        update: Telegram update object
        context: Bot context
        required_data: Dict mapping data keys to required fields

    Returns:
        True if context is valid or recovered, False if recovery failed
    """
    try:
        # Validate basic user context
        ContextValidator.validate_user_context(update, context)

        # Validate required data if specified
        if required_data:
            for data_key, fields in required_data.items():
                if data_key == "user_data":
                    ContextValidator.validate_user_data(context, fields)
                else:
                    ContextValidator.validate_conversation_data(
                        context, data_key, fields
                    )

        return True

    except ContextValidationError as e:
        logger.warning(f"Context validation failed, attempting recovery: {e}")

        # Attempt basic recovery
        try:
            # Ensure user_data exists
            if not context.user_data:
                # Fix: Cannot assign new value to user_data - use clear() if exists
                if hasattr(context, 'user_data') and context.user_data is not None:
                    context.user_data.clear()
                else:
                    # If user_data is None, we need to work with what we have
                    pass

            # Add basic user info if missing
            if update.effective_user:
                context.user_data.setdefault("user_id", update.effective_user.id)
                context.user_data.setdefault("telegram_id", update.effective_user.id)
                context.user_data.setdefault("username", update.effective_user.username)
                context.user_data.setdefault(
                    "first_name", update.effective_user.first_name
                )

            logger.info("Basic context recovery completed")
            return True

        except Exception as recovery_error:
            logger.error(f"Context recovery failed: {recovery_error}")
            return False

    except Exception as e:
        logger.error(f"Unexpected error in context validation: {e}")
        return False
