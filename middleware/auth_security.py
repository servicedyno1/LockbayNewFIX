"""
Authentication Security Middleware
Provides comprehensive protection against brute force, timing attacks, and session hijacking
"""

import logging
import time
from typing import Dict, Any, Optional, Tuple
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from functools import wraps

from utils.secure_crypto import SecureCrypto, rate_limiter
from models import EmailVerification, FailedAuthentication
from database import SessionLocal

logger = logging.getLogger(__name__)


@dataclass
class SecurityConfig:
    """Authentication security configuration"""
    
    # OTP Security
    otp_max_attempts: int = 5
    otp_lockout_minutes: int = 30
    otp_rate_limit_window: int = 15  # minutes
    
    # Session Security
    session_timeout_minutes: int = 480  # 8 hours
    session_regenerate_on_auth: bool = True
    
    # IP Rate Limiting
    ip_max_attempts: int = 10
    ip_lockout_minutes: int = 60
    
    # Account Security
    account_max_failed_attempts: int = 5
    account_lockout_minutes: int = 30
    
    # Timing Attack Prevention
    constant_time_delay_ms: int = 100


class AuthSecurityService:
    """Comprehensive authentication security service"""
    
    def __init__(self, config: SecurityConfig = None):
        self.config = config or SecurityConfig()
        self._failed_attempts: Dict[str, list] = {}
        self._locked_accounts: Dict[str, datetime] = {}
        self._session_registry: Dict[str, Dict[str, Any]] = {}
    
    def check_rate_limit(self, identifier: str, limit_type: str = "general") -> Tuple[bool, int]:
        """
        Check if identifier is rate limited
        
        Args:
            identifier: IP address, user ID, email, etc.
            limit_type: Type of rate limit (otp, login, general)
            
        Returns:
            Tuple of (is_limited: bool, remaining_attempts: int)
        """
        limits = {
            "otp": (self.config.otp_max_attempts, self.config.otp_rate_limit_window),
            "login": (self.config.account_max_failed_attempts, self.config.otp_rate_limit_window),
            "ip": (self.config.ip_max_attempts, 60),
            "general": (20, 15)
        }
        
        max_attempts, window_minutes = limits.get(limit_type, limits["general"])
        
        # Use the global rate limiter
        is_limited = rate_limiter.is_rate_limited(
            identifier=f"{limit_type}:{identifier}",
            max_attempts=max_attempts,
            window_minutes=window_minutes,
            lockout_minutes=self.config.otp_lockout_minutes
        )
        
        # Calculate remaining attempts
        attempts_key = f"{limit_type}:{identifier}"
        current_attempts = len(rate_limiter._attempts.get(attempts_key, []))
        remaining = max(0, max_attempts - current_attempts)
        
        return is_limited, remaining
    
    def record_failed_attempt(self, identifier: str, limit_type: str = "general") -> None:
        """Record a failed authentication attempt"""
        rate_limiter.record_attempt(f"{limit_type}:{identifier}")
        
        # Also record in database for audit trail
        try:
            with SessionLocal() as session:
                failed_auth = FailedAuthentication(
                    identifier=identifier,
                    attempt_type=limit_type,
                    timestamp=datetime.now(timezone.utc),
                    ip_address=identifier if limit_type == "ip" else None
                )
                session.add(failed_auth)
                session.commit()
        except Exception as e:
            logger.error(f"Failed to record authentication attempt: {e}")
    
    def record_successful_attempt(self, identifier: str, limit_type: str = "general") -> None:
        """Record successful authentication and reset rate limits"""
        rate_limiter.reset_attempts(f"{limit_type}:{identifier}")
        
        # Clear any existing failed attempts
        if identifier in self._failed_attempts:
            del self._failed_attempts[identifier]
        if identifier in self._locked_accounts:
            del self._locked_accounts[identifier]
    
    def verify_otp_with_security(self, user_id: int, submitted_otp: str, 
                                purpose: str, ip_address: str = None) -> Dict[str, Any]:
        """
        Verify OTP with comprehensive security checks
        
        Args:
            user_id: User ID
            submitted_otp: OTP submitted by user
            purpose: OTP purpose (onboarding, cashout, etc.)
            ip_address: User's IP address
            
        Returns:
            Dict with verification result and security info
        """
        # Rate limiting checks
        user_key = f"user:{user_id}"
        ip_key = f"ip:{ip_address}" if ip_address else "unknown"
        
        user_limited, user_remaining = self.check_rate_limit(user_key, "otp")
        ip_limited, ip_remaining = self.check_rate_limit(ip_key, "ip")
        
        if user_limited:
            logger.warning(f"User {user_id} OTP rate limited")
            return {
                "success": False,
                "error": "Too many attempts. Account temporarily locked.",
                "lockout_minutes": self.config.otp_lockout_minutes
            }
        
        if ip_limited:
            logger.warning(f"IP {ip_address} rate limited")
            return {
                "success": False,
                "error": "Too many attempts from this location. Please try again later.",
                "lockout_minutes": self.config.ip_lockout_minutes
            }
        
        # Timing attack prevention - always take the same time
        start_time = time.time()
        
        try:
            with SessionLocal() as session:
                # Find valid OTP
                verification = (
                    session.query(EmailVerification)
                    .filter(
                        EmailVerification.user_id == user_id,
                        EmailVerification.purpose == purpose,
                        EmailVerification.verified == False,
                        EmailVerification.expires_at > datetime.now(timezone.utc)
                    )
                    .first()
                )
                
                # Constant time comparison even if no verification found
                if verification:
                    stored_otp = verification.verification_code
                    is_valid = SecureCrypto.constant_time_compare(submitted_otp, stored_otp)
                else:
                    # Still do a comparison to maintain constant time
                    is_valid = SecureCrypto.constant_time_compare(submitted_otp, "000000")
                
                # Ensure minimum time elapsed to prevent timing attacks
                elapsed = (time.time() - start_time) * 1000  # Convert to ms
                if elapsed < self.config.constant_time_delay_ms:
                    time.sleep((self.config.constant_time_delay_ms - elapsed) / 1000)
                
                if not verification:
                    self.record_failed_attempt(user_key, "otp")
                    self.record_failed_attempt(ip_key, "ip")
                    return {
                        "success": False,
                        "error": "Invalid or expired verification code.",
                        "remaining_attempts": user_remaining - 1
                    }
                
                if not is_valid:
                    self.record_failed_attempt(user_key, "otp")
                    self.record_failed_attempt(ip_key, "ip")
                    return {
                        "success": False,
                        "error": "Invalid verification code.",
                        "remaining_attempts": user_remaining - 1
                    }
                
                # Mark as verified
                verification.verified = True
                verification.verified_at = datetime.now(timezone.utc)
                session.commit()
                
                # Reset rate limits on success
                self.record_successful_attempt(user_key, "otp")
                self.record_successful_attempt(ip_key, "ip")
                
                return {
                    "success": True,
                    "verification_id": verification.id,
                    "purpose": purpose
                }
                
        except Exception as e:
            logger.error(f"OTP verification error: {e}")
            return {
                "success": False,
                "error": "Verification system temporarily unavailable."
            }
    
    def create_secure_session(self, user_id: int, ip_address: str = None) -> Dict[str, str]:
        """
        Create secure session with proper entropy and tracking
        
        Args:
            user_id: User ID
            ip_address: User's IP address
            
        Returns:
            Dict with session info
        """
        session_id = SecureCrypto.generate_session_id()
        csrf_token = SecureCrypto.generate_csrf_token()
        
        session_data = {
            "user_id": user_id,
            "created_at": datetime.now(timezone.utc),
            "ip_address": ip_address,
            "csrf_token": csrf_token,
            "last_activity": datetime.now(timezone.utc)
        }
        
        self._session_registry[session_id] = session_data
        
        return {
            "session_id": session_id,
            "csrf_token": csrf_token
        }
    
    def validate_session(self, session_id: str, csrf_token: str = None) -> Dict[str, Any]:
        """
        Validate session and CSRF token
        
        Args:
            session_id: Session identifier
            csrf_token: CSRF token (optional)
            
        Returns:
            Dict with validation result
        """
        if session_id not in self._session_registry:
            return {"valid": False, "reason": "Session not found"}
        
        session_data = self._session_registry[session_id]
        
        # Check session timeout
        last_activity = session_data["last_activity"]
        timeout_delta = timedelta(minutes=self.config.session_timeout_minutes)
        
        if datetime.now(timezone.utc) - last_activity > timeout_delta:
            # Session expired
            del self._session_registry[session_id]
            return {"valid": False, "reason": "Session expired"}
        
        # Validate CSRF token if provided
        if csrf_token and not SecureCrypto.constant_time_compare(
            csrf_token, session_data["csrf_token"]
        ):
            return {"valid": False, "reason": "CSRF token invalid"}
        
        # Update last activity
        session_data["last_activity"] = datetime.now(timezone.utc)
        
        return {
            "valid": True,
            "user_id": session_data["user_id"],
            "session_data": session_data
        }
    
    def regenerate_session(self, old_session_id: str) -> Optional[Dict[str, str]]:
        """
        Regenerate session ID for security (after authentication)
        
        Args:
            old_session_id: Current session ID
            
        Returns:
            New session info or None if old session invalid
        """
        if old_session_id not in self._session_registry:
            return None
        
        old_session = self._session_registry[old_session_id]
        
        # Create new session with same data
        new_session_id = SecureCrypto.generate_session_id()
        new_csrf_token = SecureCrypto.generate_csrf_token()
        
        new_session_data = old_session.copy()
        new_session_data["csrf_token"] = new_csrf_token
        new_session_data["regenerated_at"] = datetime.now(timezone.utc)
        
        # Store new session and remove old
        self._session_registry[new_session_id] = new_session_data
        del self._session_registry[old_session_id]
        
        return {
            "session_id": new_session_id,
            "csrf_token": new_csrf_token
        }


# Decorator for rate-limited endpoints
def rate_limited(limit_type: str = "general", identifier_func=None):
    """
    Decorator to add rate limiting to functions
    
    Args:
        limit_type: Type of rate limit
        identifier_func: Function to extract identifier from args
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            auth_service = AuthSecurityService()
            
            # Extract identifier
            if identifier_func:
                identifier = identifier_func(*args, **kwargs)
            else:
                # Default to user ID from update object
                update = args[0] if args else None
                identifier = str(update.effective_user.id) if update and update.effective_user else "unknown"
            
            # Check rate limit
            is_limited, remaining = auth_service.check_rate_limit(identifier, limit_type)
            
            if is_limited:
                # Handle rate limit exceeded
                if hasattr(args[0], 'message') and hasattr(args[0].message, 'reply_text'):
                    await args[0].message.reply_text(
                        "⚠️ Too many attempts. Please wait before trying again."
                    )
                return None
            
            # Record attempt
            auth_service.record_failed_attempt(identifier, limit_type)
            
            try:
                result = await func(*args, **kwargs)
                # Reset on success
                auth_service.record_successful_attempt(identifier, limit_type)
                return result
            except Exception as e:
                # Don't reset on error
                logger.error(f"Rate limited function {func.__name__} failed: {e}")
                raise
        
        return wrapper
    return decorator


# Global auth security service instance
auth_security = AuthSecurityService()