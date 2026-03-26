"""
CSRF Protection Middleware
Provides Cross-Site Request Forgery protection for webhook endpoints
"""

import logging
import hmac
import hashlib
from typing import Optional, Dict
from datetime import datetime, timedelta, timezone

from utils.secure_crypto import SecureCrypto
from config import Config

logger = logging.getLogger(__name__)


class CSRFProtection:
    """CSRF protection for webhook and API endpoints"""
    
    def __init__(self, secret_key: Optional[str] = None):
        self.secret_key = secret_key or getattr(Config, 'CSRF_SECRET_KEY', SecureCrypto.generate_secure_token(32))
        self._token_cache: Dict[str, datetime] = {}
        self.token_expiry_minutes = 60  # CSRF tokens expire after 1 hour
    
    def generate_csrf_token(self, session_id: str) -> str:
        """
        Generate CSRF token for a session
        
        Args:
            session_id: Session identifier
            
        Returns:
            CSRF token string
        """
        timestamp = str(int(datetime.now(timezone.utc).timestamp()))
        
        # Create token with session ID and timestamp
        message = f"{session_id}:{timestamp}"
        signature = hmac.new(
            self.secret_key.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        token = f"{timestamp}.{signature}"
        
        # Cache token
        self._token_cache[token] = datetime.now(timezone.utc)
        
        return token
    
    def validate_csrf_token(self, token: str, session_id: str) -> bool:
        """
        Validate CSRF token
        
        Args:
            token: CSRF token to validate
            session_id: Session identifier
            
        Returns:
            True if token is valid
        """
        try:
            # Check token format
            if '.' not in token:
                return False
            
            timestamp_str, signature = token.split('.', 1)
            
            # Check if token is expired
            try:
                token_time = datetime.fromtimestamp(int(timestamp_str), tz=timezone.utc)
                if datetime.now(timezone.utc) - token_time > timedelta(minutes=self.token_expiry_minutes):
                    return False
            except (ValueError, OverflowError):
                return False
            
            # Verify signature
            message = f"{session_id}:{timestamp_str}"
            expected_signature = hmac.new(
                self.secret_key.encode('utf-8'),
                message.encode('utf-8'),
                hashlib.sha256
            ).hexdigest()
            
            # Constant time comparison
            return hmac.compare_digest(signature, expected_signature)
            
        except Exception as e:
            logger.error(f"CSRF token validation error: {e}")
            return False
    
    def cleanup_expired_tokens(self) -> None:
        """Clean up expired tokens from cache"""
        now = datetime.now(timezone.utc)
        expired_tokens = [
            token for token, created_at in self._token_cache.items()
            if now - created_at > timedelta(minutes=self.token_expiry_minutes)
        ]
        
        for token in expired_tokens:
            del self._token_cache[token]
    
    def get_csrf_header_name(self) -> str:
        """Get the name of the CSRF header"""
        return "X-CSRF-Token"


class WebhookCSRFValidator:
    """CSRF validation specifically for webhook endpoints"""
    
    def __init__(self):
        self.webhook_secret = getattr(Config, 'WEBHOOK_SECRET_KEY', None)
    
    def validate_telegram_webhook(self, request_data: bytes, signature: str) -> bool:
        """
        Validate Telegram webhook signature
        
        Args:
            request_data: Raw request data
            signature: Telegram signature header
            
        Returns:
            True if signature is valid
        """
        if not self.webhook_secret:
            logger.warning("Webhook secret not configured - skipping signature validation")
            return True
        
        try:
            # Telegram uses HMAC-SHA256
            expected_signature = hmac.new(
                self.webhook_secret.encode('utf-8'),
                request_data,
                hashlib.sha256
            ).hexdigest()
            
            # Remove 'sha256=' prefix if present
            if signature.startswith('sha256='):
                signature = signature[7:]
            
            return hmac.compare_digest(signature, expected_signature)
            
        except Exception as e:
            logger.error(f"Webhook signature validation error: {e}")
            return False
    
    def validate_origin(self, origin: str, allowed_origins: list = None) -> bool:
        """
        Validate request origin
        
        Args:
            origin: Request origin header
            allowed_origins: List of allowed origins
            
        Returns:
            True if origin is allowed
        """
        if not allowed_origins:
            allowed_origins = [
                'https://api.telegram.org',
                'https://core.telegram.org'
            ]
        
        return origin in allowed_origins
    
    def generate_webhook_token(self) -> str:
        """Generate secure webhook token"""
        return SecureCrypto.generate_secure_token(48)


# Global instances
csrf_protection = CSRFProtection()
webhook_csrf_validator = WebhookCSRFValidator()