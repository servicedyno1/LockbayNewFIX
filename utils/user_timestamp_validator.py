"""
SQLAlchemy event listener to automatically ensure User timestamps are timezone-naive.

This prevents timezone-aware datetimes from being inserted into User.created_at, 
User.updated_at, and User.last_activity fields, which are defined as 
DateTime(timezone=False) and expect naive datetimes.

This is a defensive safeguard that automatically converts timezone-aware datetimes
to naive UTC datetimes before they reach the database, preventing errors like:
"invalid input for query argument: can't subtract offset-naive and offset-aware datetimes"
"""

from sqlalchemy import event
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)


def ensure_naive_datetime_auto(dt):
    """
    Automatically convert timezone-aware datetime to naive UTC datetime.
    
    Args:
        dt: Datetime that may be timezone-aware or naive
        
    Returns:
        Naive datetime in UTC
    """
    if dt is None:
        return None
        
    if isinstance(dt, datetime) and dt.tzinfo is not None:
        # Convert to UTC and remove timezone info
        logger.warning(
            f"⚠️ AUTO_CONVERSION: Converting timezone-aware datetime to naive UTC. "
            f"Original timezone: {dt.tzinfo}. This may indicate a bug in the calling code."
        )
        utc_dt = dt.astimezone(timezone.utc)
        return utc_dt.replace(tzinfo=None)
    
    return dt


def register_user_timestamp_validator():
    """
    Register SQLAlchemy event listeners to validate and convert User timestamps.
    
    This function should be called once during application initialization.
    It adds before_insert and before_update event listeners to the User model
    that automatically convert timezone-aware datetimes to naive datetimes.
    """
    from models import User
    
    @event.listens_for(User, 'before_insert')
    def validate_user_insert_timestamps(mapper, connection, target):
        """Validate timestamps before inserting a new User"""
        target.created_at = ensure_naive_datetime_auto(target.created_at)
        target.updated_at = ensure_naive_datetime_auto(target.updated_at)
        if hasattr(target, 'last_activity') and target.last_activity is not None:
            target.last_activity = ensure_naive_datetime_auto(target.last_activity)
    
    @event.listens_for(User, 'before_update')
    def validate_user_update_timestamps(mapper, connection, target):
        """Validate timestamps before updating a User"""
        target.updated_at = ensure_naive_datetime_auto(target.updated_at)
        if hasattr(target, 'last_activity') and target.last_activity is not None:
            target.last_activity = ensure_naive_datetime_auto(target.last_activity)
        # Don't modify created_at on updates
    
    logger.info("✅ USER_TIMESTAMP_VALIDATOR: Registered SQLAlchemy event listeners for User model")
