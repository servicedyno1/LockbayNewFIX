"""
Secure Cryptographic Utilities
Provides cryptographically secure random generation for all security-critical operations
"""

import secrets
import hmac
import logging
from typing import Optional, Dict
from datetime import datetime, timedelta, timezone
import base64

logger = logging.getLogger(__name__)


class SecureCrypto:
    """Cryptographically secure utilities for authentication and tokens"""

    @staticmethod
    def generate_secure_otp(length: int = 6) -> str:
        """
        Generate cryptographically secure OTP
        
        Args:
            length: OTP length (default 6 digits)
            
        Returns:
            Secure random OTP string
        """
        if length < 4 or length > 12:
            raise ValueError("OTP length must be between 4 and 12")
            
        # Use secrets.randbelow for cryptographically secure random integers
        max_value = 10 ** length
        otp_int = secrets.randbelow(max_value)
        
        # Format with leading zeros
        return f"{otp_int:0{length}d}"
    
    @staticmethod
    def generate_secure_token(length: int = 32) -> str:
        """
        Generate cryptographically secure token using URL-safe base64
        
        Args:
            length: Token length in characters (will be base64 encoded)
            
        Returns:
            Secure random token string
        """
        if length < 16 or length > 128:
            raise ValueError("Token length must be between 16 and 128")
            
        # Generate URL-safe token
        return secrets.token_urlsafe(length)
    
    @staticmethod
    def generate_invitation_token() -> str:
        """Generate secure invitation token with high entropy"""
        # Use 32 bytes = 256 bits of entropy, base64 encoded
        return secrets.token_urlsafe(32)
    
    @staticmethod
    def generate_session_id() -> str:
        """Generate secure session identifier"""
        # Use 48 bytes = 384 bits of entropy for session IDs
        return secrets.token_urlsafe(48)
    
    @staticmethod
    def generate_csrf_token() -> str:
        """Generate CSRF protection token"""
        # Use 24 bytes = 192 bits of entropy for CSRF tokens
        return secrets.token_urlsafe(24)
    
    @staticmethod
    def hash_sensitive_data(data: str, salt: Optional[str] = None) -> Dict[str, str]:
        """
        Hash sensitive data with salt for secure storage
        
        Args:
            data: Sensitive data to hash
            salt: Optional salt (will generate if not provided)
            
        Returns:
            Dict with 'hash' and 'salt' keys
        """
        if salt is None:
            salt = secrets.token_hex(32)
        
        # Use PBKDF2 with SHA-256 for key derivation
        from hashlib import pbkdf2_hmac
        hash_bytes = pbkdf2_hmac('sha256', data.encode('utf-8'), salt.encode('utf-8'), 100000)
        
        return {
            'hash': base64.b64encode(hash_bytes).decode('utf-8'),
            'salt': salt
        }
    
    @staticmethod
    def verify_hash(data: str, stored_hash: str, salt: str) -> bool:
        """
        Verify data against stored hash
        
        Args:
            data: Data to verify
            stored_hash: Stored hash value
            salt: Salt used for hashing
            
        Returns:
            True if data matches hash
        """
        try:
            computed = SecureCrypto.hash_sensitive_data(data, salt)
            return hmac.compare_digest(computed['hash'], stored_hash)
        except Exception as e:
            logger.error(f"Hash verification error: {e}")
            return False
    
    @staticmethod
    def constant_time_compare(a: str, b: str) -> bool:
        """
        Constant-time string comparison to prevent timing attacks
        
        Args:
            a: First string
            b: Second string
            
        Returns:
            True if strings are equal
        """
        return hmac.compare_digest(a.encode('utf-8'), b.encode('utf-8'))
    
    @staticmethod
    def generate_random_id(prefix: str = "", length: int = 16) -> str:
        """
        Generate cryptographically secure random ID
        
        Args:
            prefix: Optional prefix for the ID
            length: Random part length
            
        Returns:
            Secure random ID string
        """
        random_part = secrets.token_hex(length // 2)  # hex gives 2 chars per byte
        return f"{prefix}{random_part}" if prefix else random_part
    
    @staticmethod
    def create_secure_backup_codes(count: int = 10) -> list[str]:
        """
        Generate secure backup codes for 2FA
        
        Args:
            count: Number of backup codes to generate
            
        Returns:
            List of secure backup codes
        """
        codes = []
        for _ in range(count):
            # Generate 8-character alphanumeric codes
            code = secrets.token_hex(4).upper()  # 4 bytes = 8 hex chars
            # Format as XXXX-XXXX for readability
            formatted = f"{code[:4]}-{code[4:]}"
            codes.append(formatted)
        
        return codes


class RateLimiter:
    """Memory-based rate limiter for security operations"""
    
    def __init__(self):
        self._attempts: Dict[str, list] = {}
        self._lockouts: Dict[str, datetime] = {}
    
    def is_rate_limited(self, identifier: str, max_attempts: int = 5, 
                       window_minutes: int = 15, lockout_minutes: int = 30) -> bool:
        """
        Check if identifier is rate limited
        
        Args:
            identifier: Unique identifier (IP, user ID, etc.)
            max_attempts: Maximum attempts allowed in window
            window_minutes: Time window for rate limiting
            lockout_minutes: Lockout duration after exceeding limit
            
        Returns:
            True if rate limited
        """
        now = datetime.now(timezone.utc)
        
        # Check if currently locked out
        if identifier in self._lockouts:
            lockout_until = self._lockouts[identifier]
            if now < lockout_until:
                return True
            else:
                # Lockout expired, remove it
                del self._lockouts[identifier]
        
        # Clean old attempts outside window
        if identifier in self._attempts:
            window_start = now - timedelta(minutes=window_minutes)
            self._attempts[identifier] = [
                attempt for attempt in self._attempts[identifier]
                if attempt > window_start
            ]
        
        # Check if within rate limit
        attempts_count = len(self._attempts.get(identifier, []))
        if attempts_count >= max_attempts:
            # Set lockout
            self._lockouts[identifier] = now + timedelta(minutes=lockout_minutes)
            return True
        
        return False
    
    def record_attempt(self, identifier: str) -> None:
        """Record an attempt for rate limiting"""
        now = datetime.now(timezone.utc)
        
        if identifier not in self._attempts:
            self._attempts[identifier] = []
        
        self._attempts[identifier].append(now)
    
    def reset_attempts(self, identifier: str) -> None:
        """Reset attempts for identifier (on successful auth)"""
        if identifier in self._attempts:
            del self._attempts[identifier]
        if identifier in self._lockouts:
            del self._lockouts[identifier]


# Global rate limiter instance
rate_limiter = RateLimiter()