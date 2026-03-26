"""
Rate Lock System for NGN Cashout Flow

Provides comprehensive rate locking functionality to prevent rate changes
between cashout confirmation and OTP verification.

Features:
- 15-minute rate locks with unique tokens
- Expiry validation and countdown display
- Security binding for OTP flow integration
- Automatic cleanup of expired locks
- Graceful error handling and user feedback
"""

import logging
import uuid
import hashlib
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
from decimal import Decimal, DecimalException


logger = logging.getLogger(__name__)


class RateLockError(Exception):
    """Custom exception for rate lock system errors"""
    pass


class RateLock:
    """
    Comprehensive rate lock system for NGN cashout flow
    
    Manages rate locking, validation, expiry handling, and security binding
    to ensure consistent rates between confirmation and OTP verification.
    """
    
    # Rate lock configuration
    LOCK_DURATION_MINUTES = 15
    LOCK_DURATION_SECONDS = LOCK_DURATION_MINUTES * 60
    RATE_TOLERANCE_PERCENT = 0.5  # Allow 0.5% rate variation for validation
    
    @classmethod
    def create_rate_lock(
        cls,
        user_id: int,
        usd_amount: Decimal,
        ngn_amount: Decimal,
        exchange_rate: float,
        cashout_context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Create a new rate lock for NGN cashout
        
        Args:
            user_id: User ID
            usd_amount: USD amount being cashed out
            ngn_amount: Equivalent NGN amount at locked rate
            exchange_rate: USD to NGN exchange rate being locked
            cashout_context: Additional cashout context for security binding
            
        Returns:
            Dict containing rate lock details
        """
        try:
            current_time = datetime.utcnow()
            expiry_time = current_time + timedelta(minutes=cls.LOCK_DURATION_MINUTES)
            
            # Generate unique lock token for security
            lock_token = cls._generate_lock_token(user_id, usd_amount, ngn_amount, current_time)
            
            # Create rate lock object with Decimal precision
            rate_lock = {
                'token': lock_token,
                'user_id': user_id,
                'usd_amount': str(usd_amount),  # Store as Decimal string for exact precision
                'ngn_amount': str(ngn_amount),  # Store as Decimal string for exact precision
                'exchange_rate': str(Decimal(str(exchange_rate))),  # Convert float to Decimal string
                'created_at': current_time.isoformat(),
                'expires_at': expiry_time.isoformat(),
                'is_active': True,
                'cashout_context': cashout_context,
                'lock_type': 'ngn_cashout'
            }
            
            logger.info(
                f"🔒 Rate lock created - User: {user_id}, Rate: ₦{exchange_rate:.2f}, "
                f"Amount: ${usd_amount} → ₦{ngn_amount:,.2f}, Token: {lock_token[:8]}..., "
                f"Expires: {expiry_time.strftime('%H:%M:%S UTC')}"
            )
            
            return {
                'success': True,
                'rate_lock': rate_lock,
                'expires_in_minutes': cls.LOCK_DURATION_MINUTES,
                'expires_at_formatted': expiry_time.strftime('%H:%M:%S UTC')
            }
            
        except Exception as e:
            logger.error(f"❌ Failed to create rate lock for user {user_id}: {e}")
            return {
                'success': False,
                'error': f'Failed to create rate lock: {str(e)}'
            }
    
    @classmethod
    def validate_rate_lock(
        cls,
        rate_lock: Dict[str, Any],
        user_id: int,
        expected_token: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Validate rate lock is still active and not expired
        
        Args:
            rate_lock: Rate lock object to validate
            user_id: Expected user ID
            expected_token: Expected lock token for security validation
            
        Returns:
            Dict with validation result and details
        """
        try:
            if not rate_lock or not rate_lock.get('is_active'):
                return {
                    'valid': False,
                    'error': 'Rate lock is not active',
                    'error_code': 'LOCK_INACTIVE'
                }
            
            # Verify user ID
            if rate_lock.get('user_id') != user_id:
                logger.warning(f"🚨 Rate lock user ID mismatch - Expected: {user_id}, Got: {rate_lock.get('user_id')}")
                return {
                    'valid': False,
                    'error': 'Rate lock user mismatch',
                    'error_code': 'USER_MISMATCH'
                }
            
            # Verify token if provided
            if expected_token and rate_lock.get('token') != expected_token:
                logger.warning(f"🚨 Rate lock token mismatch - User: {user_id}")
                return {
                    'valid': False,
                    'error': 'Rate lock token invalid',
                    'error_code': 'TOKEN_INVALID'
                }
            
            # Check expiry
            expires_at = datetime.fromisoformat(rate_lock['expires_at'])
            current_time = datetime.utcnow()
            
            if current_time > expires_at:
                time_expired = current_time - expires_at
                logger.warning(
                    f"⏰ Rate lock expired - User: {user_id}, "
                    f"Expired {time_expired.total_seconds():.0f}s ago"
                )
                return {
                    'valid': False,
                    'error': 'Rate lock has expired',
                    'error_code': 'LOCK_EXPIRED',
                    'expired_seconds': int(time_expired.total_seconds())
                }
            
            # Calculate remaining time
            time_remaining = expires_at - current_time
            remaining_seconds = int(time_remaining.total_seconds())
            remaining_minutes = remaining_seconds // 60
            
            # Safely format the exchange rate (could be string or numeric)
            try:
                rate_display = f"₦{float(rate_lock['exchange_rate']):.2f}"
            except (ValueError, TypeError):
                rate_display = f"₦{rate_lock['exchange_rate']}"
            
            logger.info(
                f"✅ Rate lock valid - User: {user_id}, "
                f"Rate: {rate_display}, "
                f"Remaining: {remaining_minutes}m {remaining_seconds % 60}s"
            )
            
            return {
                'valid': True,
                'remaining_seconds': remaining_seconds,
                'remaining_minutes': remaining_minutes,
                'expires_at': expires_at,
                'rate_lock': rate_lock
            }
            
        except Exception as e:
            logger.error(f"❌ Error validating rate lock for user {user_id}: {e}")
            return {
                'valid': False,
                'error': f'Rate lock validation failed: {str(e)}',
                'error_code': 'VALIDATION_ERROR'
            }
    
    @classmethod
    def get_countdown_display(cls, rate_lock: Dict[str, Any]) -> str:
        """
        Get user-friendly countdown display for rate lock
        
        Args:
            rate_lock: Rate lock object
            
        Returns:
            Formatted countdown string
        """
        try:
            expires_at = datetime.fromisoformat(rate_lock['expires_at'])
            current_time = datetime.utcnow()
            
            if current_time > expires_at:
                return "⏰ Expired"
            
            time_remaining = expires_at - current_time
            remaining_seconds = int(time_remaining.total_seconds())
            remaining_minutes = remaining_seconds // 60
            remaining_sec_display = remaining_seconds % 60
            
            if remaining_minutes > 0:
                return f"⏱️ {remaining_minutes}m {remaining_sec_display}s remaining"
            else:
                return f"⏱️ {remaining_sec_display}s remaining"
                
        except Exception as e:
            logger.error(f"❌ Error calculating countdown: {e}")
            return "⏱️ Time remaining"
    
    @classmethod
    def _safe_decimal_convert(cls, value) -> Decimal:
        """
        Safely convert stored value to Decimal, handling both float and string formats
        
        Args:
            value: Value to convert (float, string, or Decimal)
            
        Returns:
            Decimal representation of the value
        """
        try:
            if isinstance(value, str):
                return Decimal(value)
            elif isinstance(value, (int, float)):
                return Decimal(str(value))
            elif isinstance(value, Decimal):
                return value
            else:
                return Decimal('0')
        except (ValueError, TypeError, DecimalException):
            return Decimal('0')
    
    @classmethod
    def format_locked_rate_display(cls, rate_lock: Dict[str, Any]) -> Dict[str, str]:
        """
        Format rate lock details for user display with Decimal precision
        
        Args:
            rate_lock: Rate lock object
            
        Returns:
            Dict with formatted display strings
        """
        try:
            # Convert stored values to Decimal for precise formatting
            exchange_rate = cls._safe_decimal_convert(rate_lock['exchange_rate'])
            usd_amount = cls._safe_decimal_convert(rate_lock['usd_amount'])
            ngn_amount = cls._safe_decimal_convert(rate_lock['ngn_amount'])
            
            countdown = cls.get_countdown_display(rate_lock)
            
            return {
                'rate_display': f"₦{exchange_rate:,.2f} (locked)",
                'amount_display': f"${usd_amount:,.2f} → ₦{ngn_amount:,.2f}",
                'countdown_display': countdown,
                'lock_status': "🔒 Rate Locked",
                'expires_at_display': datetime.fromisoformat(rate_lock['expires_at']).strftime('%H:%M:%S UTC')
            }
            
        except Exception as e:
            logger.error(f"❌ Error formatting rate display: {e}")
            return {
                'rate_display': "Rate locked",
                'amount_display': "Amount locked", 
                'countdown_display': "⏱️ Time remaining",
                'lock_status': "🔒 Rate Locked",
                'expires_at_display': "Expires soon"
            }
    
    @classmethod
    def invalidate_rate_lock(cls, rate_lock: Dict[str, Any], reason: str = "manual") -> None:
        """
        Invalidate a rate lock
        
        Args:
            rate_lock: Rate lock object to invalidate
            reason: Reason for invalidation
        """
        try:
            if rate_lock:
                rate_lock['is_active'] = False
                rate_lock['invalidated_at'] = datetime.utcnow().isoformat()
                rate_lock['invalidation_reason'] = reason
                
                logger.info(
                    f"🔓 Rate lock invalidated - User: {rate_lock.get('user_id')}, "
                    f"Reason: {reason}, Token: {rate_lock.get('token', 'unknown')[:8]}..."
                )
                
        except Exception as e:
            logger.error(f"❌ Error invalidating rate lock: {e}")
    
    @classmethod
    def create_security_fingerprint(cls, rate_lock: Dict[str, Any]) -> str:
        """
        Create security fingerprint for OTP flow integration
        
        Args:
            rate_lock: Rate lock object
            
        Returns:
            Security fingerprint string
        """
        try:
            # Convert amounts to string for consistent fingerprint generation
            fingerprint_data = {
                'token': rate_lock.get('token', ''),
                'user_id': rate_lock.get('user_id', 0),
                'exchange_rate': str(cls._safe_decimal_convert(rate_lock.get('exchange_rate', 0))),
                'ngn_amount': str(cls._safe_decimal_convert(rate_lock.get('ngn_amount', 0))),
                'created_at': rate_lock.get('created_at', '')
            }
            
            # Create deterministic fingerprint
            fingerprint_str = str(sorted(fingerprint_data.items()))
            fingerprint = hashlib.sha256(fingerprint_str.encode()).hexdigest()[:16]
            
            return fingerprint
            
        except Exception as e:
            logger.error(f"❌ Error creating security fingerprint: {e}")
            return "unknown_fingerprint"
    
    @classmethod
    def _generate_lock_token(
        cls,
        user_id: int,
        usd_amount: Decimal,
        ngn_amount: Decimal,
        timestamp: datetime
    ) -> str:
        """
        Generate unique secure token for rate lock
        
        Args:
            user_id: User ID
            usd_amount: USD amount
            ngn_amount: NGN amount  
            timestamp: Lock creation timestamp
            
        Returns:
            Unique lock token
        """
        try:
            # Create unique token based on user, amounts, and timestamp
            token_data = f"{user_id}:{usd_amount}:{ngn_amount}:{timestamp.isoformat()}:{uuid.uuid4()}"
            token_hash = hashlib.sha256(token_data.encode()).hexdigest()
            
            # Return first 32 characters for manageable token size
            return token_hash[:32]
            
        except Exception as e:
            logger.error(f"❌ Error generating lock token: {e}")
            return str(uuid.uuid4()).replace('-', '')[:32]
    
    @classmethod
    def get_rate_lock_from_context(cls, context) -> Optional[Dict[str, Any]]:
        """
        Safely extract rate lock from user context
        
        Args:
            context: Telegram context object
            
        Returns:
            Rate lock object or None
        """
        try:
            if not context or not context.user_data:
                return None
                
            return context.user_data.get('rate_lock')
            
        except Exception as e:
            logger.error(f"❌ Error getting rate lock from context: {e}")
            return None
    
    @classmethod
    def store_rate_lock_in_context(cls, context, rate_lock: Dict[str, Any]) -> bool:
        """
        Safely store rate lock in user context
        
        Args:
            context: Telegram context object
            rate_lock: Rate lock object to store
            
        Returns:
            True if stored successfully, False otherwise
        """
        try:
            if not context:
                return False
                
            if not context.user_data:
                context.user_data = {}
                
            context.user_data['rate_lock'] = rate_lock
            
            logger.debug(
                f"📦 Rate lock stored in context - User: {rate_lock.get('user_id')}, "
                f"Token: {rate_lock.get('token', 'unknown')[:8]}..."
            )
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Error storing rate lock in context: {e}")
            return False
    
    @classmethod
    def cleanup_expired_locks(cls, context) -> None:
        """
        Clean up expired rate locks from context
        
        Args:
            context: Telegram context object
        """
        try:
            rate_lock = cls.get_rate_lock_from_context(context)
            if not rate_lock:
                return
                
            validation_result = cls.validate_rate_lock(
                rate_lock, 
                rate_lock.get('user_id', 0)
            )
            
            if not validation_result['valid']:
                cls.invalidate_rate_lock(rate_lock, "expired_cleanup")
                if context and context.user_data:
                    context.user_data.pop('rate_lock', None)
                    
                logger.info(f"🧹 Cleaned up expired rate lock - User: {rate_lock.get('user_id')}")
                
        except Exception as e:
            logger.error(f"❌ Error cleaning up expired locks: {e}")