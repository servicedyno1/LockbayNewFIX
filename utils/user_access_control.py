"""
User Access Control System
Implements user-level access restrictions for bot features
"""

import logging
from typing import Dict, Any, Optional
from utils.admin_security import is_admin_silent
from models import User

logger = logging.getLogger(__name__)


class UserAccessController:
    """
    Manages user access control for bot features
    Allows granular control over which users can access which features
    """
    
    # Special user configurations
    UNRESTRICTED_TELEGRAM_IDS = {
        # No users should bypass onboarding requirements
        # All users, including admins, must complete onboarding
    }
    
    # Feature access levels
    RESTRICTED_FEATURES = {
        # Exchange features are now unrestricted - all users can access
        # Add any future restricted features here
    }
    
    @classmethod
    def can_access_feature(cls, user_telegram_id: str, feature: str) -> bool:
        """
        Check if a user can access a specific feature
        
        Args:
            user_telegram_id: Telegram ID as string
            feature: Feature name to check access for
            
        Returns:
            bool: True if user can access feature, False otherwise
        """
        try:
            # Convert to string for consistent comparison
            user_id_str = str(user_telegram_id)
            
            # Admins get full access to everything
            try:
                user_id_int = int(user_id_str)
                if is_admin_silent(user_id_int):
                    logger.debug(f"Admin user {user_id_str} granted access to {feature}")
                    return True
            except (ValueError, TypeError):
                logger.warning(f"Could not convert user_id to int: {user_id_str}")
            
            # Special unrestricted users get full access
            if user_id_str in cls.UNRESTRICTED_TELEGRAM_IDS:
                logger.debug(f"Unrestricted user {user_id_str} granted access to {feature}")
                return True
            
            # Check if feature is restricted
            feature_lower = feature.lower()
            for restricted_feature in cls.RESTRICTED_FEATURES:
                if restricted_feature.lower() in feature_lower:
                    logger.info(f"User {user_id_str} denied access to restricted feature: {feature}")
                    return False
            
            # All other features are accessible to everyone
            logger.debug(f"User {user_id_str} granted access to unrestricted feature: {feature}")
            return True
            
        except Exception as e:
            logger.error(f"Error checking feature access for user {user_telegram_id}, feature {feature}: {e}")
            # Fail safely - deny access on error for security
            return False
    
    @classmethod
    def get_access_denied_message(cls, feature: str) -> str:
        """
        Get appropriate access denied message for a feature
        
        Args:
            feature: The feature that was denied
            
        Returns:
            str: User-friendly access denied message
        """
        if any(restricted in feature.lower() for restricted in cls.RESTRICTED_FEATURES):
            return (
                "🔒 **Exchange Feature Restricted**\n\n"
                "This exchange feature is currently in limited access mode.\n\n"
                "You can still use all other bot features including:\n"
                "• Escrow trading\n"
                "• Wallet management\n" 
                "• Cashouts\n"
                "• Transaction history\n\n"
                "Contact support if you need exchange access."
            )
        else:
            return "🔒 Access to this feature is currently restricted."
    
    @classmethod
    def is_unrestricted_user(cls, user_telegram_id: str) -> bool:
        """
        Check if user has unrestricted access to all features
        
        Args:
            user_telegram_id: Telegram ID as string
            
        Returns:
            bool: True if user has unrestricted access
        """
        try:
            user_id_str = str(user_telegram_id)
            
            # Check admin status
            try:
                user_id_int = int(user_id_str)
                if is_admin_silent(user_id_int):
                    return True
            except (ValueError, TypeError):
                pass
            
            # Check unrestricted list
            return user_id_str in cls.UNRESTRICTED_TELEGRAM_IDS
            
        except Exception as e:
            logger.error(f"Error checking unrestricted status for user {user_telegram_id}: {e}")
            return False
    
    @classmethod
    def get_user_permissions_summary(cls, user_telegram_id: str) -> Dict[str, Any]:
        """
        Get comprehensive permissions summary for a user
        
        Args:
            user_telegram_id: Telegram ID as string
            
        Returns:
            Dict with user permissions information
        """
        try:
            user_id_str = str(user_telegram_id)
            user_id_int = int(user_id_str) if user_id_str.isdigit() else None
            
            is_admin = is_admin_silent(user_id_int) if user_id_int else False
            is_unrestricted = user_id_str in cls.UNRESTRICTED_TELEGRAM_IDS
            
            # Determine access level
            if is_admin:
                access_level = "admin"
                access_description = "Full administrative access"
            elif is_unrestricted:
                access_level = "unrestricted"  
                access_description = "Full user access to all features"
            else:
                access_level = "standard"
                access_description = "Standard access (exchange features restricted)"
            
            # Check specific feature access
            feature_access = {}
            all_features = ["escrow", "wallet", "cashout", "exchange", "direct_exchange", "admin"]
            
            for feature in all_features:
                if feature == "admin":
                    feature_access[feature] = is_admin
                else:
                    feature_access[feature] = cls.can_access_feature(user_id_str, feature)
            
            return {
                "user_telegram_id": user_id_str,
                "is_admin": is_admin,
                "is_unrestricted": is_unrestricted,
                "access_level": access_level,
                "access_description": access_description,
                "feature_access": feature_access,
                "restricted_features": list(cls.RESTRICTED_FEATURES),
                "can_access_all": is_admin or is_unrestricted
            }
            
        except Exception as e:
            logger.error(f"Error getting permissions summary for user {user_telegram_id}: {e}")
            return {
                "user_telegram_id": str(user_telegram_id),
                "error": str(e),
                "access_level": "unknown"
            }


# Global access controller instance
access_controller = UserAccessController()


def check_feature_access(user_telegram_id: str, feature: str) -> bool:
    """
    Quick access check function - wrapper for the main access controller
    
    Args:
        user_telegram_id: Telegram ID as string
        feature: Feature to check access for
        
    Returns:
        bool: True if access allowed, False otherwise
    """
    return access_controller.can_access_feature(user_telegram_id, feature)


def get_access_denied_message(feature: str) -> str:
    """
    Quick wrapper to get access denied message
    
    Args:
        feature: Feature that was denied
        
    Returns:
        str: Access denied message
    """
    return access_controller.get_access_denied_message(feature)


def require_feature_access(feature: str):
    """
    Decorator to require feature access for handler functions
    
    Args:
        feature: Feature name that requires access
        
    Usage:
        @require_feature_access("exchange")
        async def exchange_handler(update, context):
            # Handler code here
    """
    def decorator(func):
        from functools import wraps
        
        @wraps(func)
        async def wrapper(update, context, *args, **kwargs):
            try:
                user = update.effective_user
                if not user:
                    return
                
                user_telegram_id = str(user.id)
                
                if not check_feature_access(user_telegram_id, feature):
                    logger.info(f"Access denied for user {user_telegram_id} to feature: {feature}")
                    
                    # Send access denied message
                    denied_message = get_access_denied_message(feature)
                    
                    if update.callback_query:
                        await update.callback_query.answer("🔒 Access restricted")
                        await update.callback_query.message.edit_text(denied_message)
                    elif update.message:
                        await update.message.reply_text(denied_message)
                    
                    return
                
                # Access granted, proceed with handler
                return await func(update, context, *args, **kwargs)
                
            except Exception as e:
                logger.error(f"Error in require_feature_access decorator: {e}")
                return
        
        return wrapper
    return decorator


def check_user_onboarding_status(user: Optional[User]) -> bool:
    """
    Check if a user has completed onboarding
    
    Args:
        user: User model instance
        
    Returns:
        bool: True if user has completed onboarding, False otherwise
    """
    if not user:
        return False
    
    return getattr(user, 'onboarding_completed', False)


def get_onboarding_required_message() -> str:
    """
    Get standard message for when onboarding is required
    
    Returns:
        str: Onboarding required message
    """
    return (
        "⚠️ **Complete Setup Required**\n\n"
        "You need to complete your account setup before accessing this feature.\n\n"
        "Use /start to complete your registration."
    )


def require_onboarding(func):
    """
    Decorator to require completed onboarding for handler functions
    
    Usage:
        @require_onboarding
        async def wallet_command(update, context):
            # Handler code here
    """
    from functools import wraps
    from database import SessionLocal
    from utils.repository import UserRepository
    
    @wraps(func)
    async def wrapper(update, context, *args, **kwargs):
        try:
            user = update.effective_user
            if not user:
                logger.warning("No effective user in onboarding-protected handler")
                return
            
            # Get user from database
            session = SessionLocal()
            try:
                db_user = UserRepository.get_user_by_telegram_id(session, user.id)
                
                if not db_user:
                    # User doesn't exist - redirect to /start
                    logger.info(f"🚨 ONBOARDING: User {user.id} not found - redirecting to /start")
                    message = "👋 Welcome! Please use /start to register first."
                    
                    if update.callback_query:
                        await update.callback_query.answer("⚠️ Registration required")
                        await update.callback_query.message.edit_text(message)
                    elif update.message:
                        await update.message.reply_text(message)
                    
                    return
                
                # Check onboarding status
                if not check_user_onboarding_status(db_user):
                    logger.info(f"🚨 ONBOARDING: User {user.id} (@{user.username or 'no_username'}) attempted to access {func.__name__} without completing onboarding")
                    
                    onboarding_message = get_onboarding_required_message()
                    
                    if update.callback_query:
                        await update.callback_query.answer("⚠️ Setup required")
                        await update.callback_query.message.edit_text(onboarding_message, parse_mode="Markdown")
                    elif update.message:
                        await update.message.reply_text(onboarding_message, parse_mode="Markdown")
                    
                    return
                
                # Onboarding completed, proceed with handler
                logger.debug(f"✅ ONBOARDING: User {user.id} verified for {func.__name__}")
                return await func(update, context, *args, **kwargs)
                
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"Error in require_onboarding decorator for {func.__name__}: {e}")
            return
    
    return wrapper