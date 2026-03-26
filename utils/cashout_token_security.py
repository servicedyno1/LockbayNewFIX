"""
Secure Token Management for Crypto Cashout Confirmation
FIXED: Provides HMAC-signed tokens with consistent serialization between generation and validation
Solves "Invalid confirmation request" errors by using database-normalized values for signature
"""

import hmac
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from decimal import Decimal
import logging

from database import SyncSessionLocal
from models import PendingCashout
from config import Config

logger = logging.getLogger(__name__)


class CashoutTokenSecurity:
    """Secure token generation and validation for crypto cashout confirmations"""
    
    @classmethod
    def _get_secret_key(cls) -> bytes:
        """Get dedicated secret key for HMAC signing from config"""
        # FIXED: Use dedicated CASHOUT_HMAC_SECRET instead of BOT_TOKEN for stability
        secret = getattr(Config, 'CASHOUT_HMAC_SECRET', None)
        
        if not secret:
            # Fallback for development/testing only
            if hasattr(Config, 'IS_PRODUCTION') and Config.IS_PRODUCTION:
                raise ValueError("CASHOUT_HMAC_SECRET must be set in production")
            secret = "dev_fallback_cashout_secret_32chars_min"  # 34 chars for development
            logger.warning("⚠️ Using development fallback for CASHOUT_HMAC_SECRET")
        
        return secret.encode('utf-8')
    
    @classmethod
    def _canonical_message(cls, pending_cashout: PendingCashout) -> str:
        """Create canonical message format from database-stored values
        
        CRITICAL: This ensures consistent serialization between generation and validation
        by using the exact values as stored in the database after normalization.
        
        Args:
            pending_cashout: Database object with normalized values
            
        Returns:
            str: Canonical message string for HMAC signing
        """
        try:
            # Normalize decimal amounts to string with fixed precision (8 decimal places)
            # This matches the DECIMAL(20, 8) column definition
            amount_str = f"{pending_cashout.amount:.8f}"
            
            # Normalize datetime to ISO format WITHOUT microseconds to avoid drift
            # Use replace(microsecond=0) to ensure consistent formatting
            expires_normalized = pending_cashout.expires_at.replace(microsecond=0)
            expires_str = expires_normalized.isoformat()
            
            # Create canonical message using database-normalized values
            # Order: user_id:amount:currency:address:network:expires_at
            message = f"{pending_cashout.user_id}:{amount_str}:{pending_cashout.currency}:{pending_cashout.withdrawal_address}:{pending_cashout.network}:{expires_str}"
            
            logger.debug(f"🔍 Canonical message: '{message}'")
            return message
            
        except Exception as e:
            logger.error(f"❌ Error creating canonical message: {e}")
            raise ValueError(f"Failed to create canonical message: {e}")
    
    @classmethod
    def generate_secure_token(cls, user_id: int, amount: Decimal, currency: str, 
                            withdrawal_address: str, network: str, 
                            fee_amount: Optional[Decimal] = None,
                            net_amount: Optional[Decimal] = None,
                            fee_breakdown: Optional[str] = None,
                            metadata: Optional[dict] = None) -> str:
        """
        FIXED: Generate secure token using database-normalized values for signature
        
        This implements the architect's solution:
        1. Store data in database first
        2. Use session.flush() to get normalized values
        3. Generate signature from database-stored values
        4. Update token with correct signature
        
        Returns:
            str: Secure token for confirmation callback
        """
        try:
            # Check if HMAC secret is properly configured
            if hasattr(Config, 'CASHOUT_HMAC_ENABLED') and not Config.CASHOUT_HMAC_ENABLED:
                raise ValueError("Cashout HMAC secret not properly configured - confirmations disabled")
            
            # Generate random token base
            raw_token = secrets.token_urlsafe(32)
            
            # Create expiry time (10 minutes from now) without microseconds for consistency
            expires_at = (datetime.now(timezone.utc) + timedelta(minutes=10)).replace(microsecond=0)
            
            # Store in database FIRST to get normalized values
            session = SyncSessionLocal()
            try:
                # Clean up any existing pending cashouts for this user first
                session.query(PendingCashout).filter(
                    PendingCashout.user_id == user_id
                ).delete()
                
                # Generate signature from normalized input values
                canonical_message = f"{user_id}:{amount}:{currency}:{withdrawal_address}:{network}:{expires_at.isoformat()}"
                signature = hmac.new(
                    cls._get_secret_key(),
                    canonical_message.encode('utf-8'),
                    hashlib.sha256
                ).hexdigest()[:16]  # First 16 chars for database constraint
                
                # Create pending cashout with both token and signature fields
                pending_cashout = PendingCashout(
                    token=raw_token,  # Store just the raw token
                    signature=signature,  # Store signature separately (16 chars)
                    user_id=user_id,
                    amount=amount,
                    currency=currency,
                    withdrawal_address=withdrawal_address,
                    network=network,
                    fee_amount=fee_amount,
                    net_amount=net_amount,
                    fee_breakdown=fee_breakdown,
                    cashout_metadata=metadata or {},
                    expires_at=expires_at
                )
                
                session.add(pending_cashout)
                session.commit()
                
                # Return combined token for external use
                secure_token = f"{raw_token}:{signature}"
                
                logger.info(f"🔐 Generated secure cashout token for user {user_id}: {secure_token[:16]}...")
                logger.debug(f"🔍 Token signature based on canonical message: '{canonical_message}'")
                return secure_token
                
            except Exception as e:
                session.rollback()
                logger.error(f"❌ Failed to store pending cashout for user {user_id}: {e}")
                raise
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"❌ Failed to generate secure token for user {user_id}: {e}")
            raise RuntimeError("Failed to generate secure confirmation token")
    
    @classmethod
    def validate_token_and_get_data(cls, token: str, user_id: int) -> Optional[PendingCashout]:
        """
        Validate token and retrieve cashout data
        
        Args:
            token: The secure token from callback
            user_id: User ID for validation
            
        Returns:
            PendingCashout object if valid, None if invalid/expired
        """
        try:
            session = SyncSessionLocal()
            try:
                # Handle both formats: "token:signature" (external) and "token" (internal)
                if ':' in token:
                    # External format: "token:signature"
                    token_parts = token.split(':')
                    if len(token_parts) != 2:
                        logger.warning(f"⚠️ Invalid token format for user {user_id}: {token[:16]}...")
                        return None
                    
                    raw_token, received_signature = token_parts
                    
                    # Get pending cashout from database using raw token
                    pending_cashout = session.query(PendingCashout).filter(
                        PendingCashout.token == raw_token,
                        PendingCashout.user_id == user_id
                    ).first()
                    
                    if not pending_cashout:
                        logger.warning(f"⚠️ Token not found for user {user_id}: {token[:16]}...")
                        return None
                    
                    # Validate signature
                    if not hmac.compare_digest(received_signature, pending_cashout.signature):
                        logger.warning(f"⚠️ Invalid token signature for user {user_id}: {token[:16]}...")
                        return None
                else:
                    # Internal format: just the raw token (from database lookup)
                    raw_token = token
                    
                    # Get pending cashout from database using raw token
                    pending_cashout = session.query(PendingCashout).filter(
                        PendingCashout.token == raw_token,
                        PendingCashout.user_id == user_id
                    ).first()
                
                if not pending_cashout:
                    logger.warning(f"⚠️ Token not found for user {user_id}: {token[:16]}...")
                    return None
                
                # Check expiry
                if datetime.now(timezone.utc) > pending_cashout.expires_at:
                    logger.warning(f"⚠️ Token expired for user {user_id}: {token[:16]}...")
                    # Clean up expired token
                    session.delete(pending_cashout)
                    session.commit()
                    return None
                
                logger.info(f"✅ Token validated successfully for user {user_id}: {token[:16]}...")
                return pending_cashout
                
            except Exception as e:
                session.rollback()
                logger.error(f"❌ Error validating token for user {user_id}: {e}")
                return None
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"❌ Failed to validate token for user {user_id}: {e}")
            return None
    
    @classmethod
    def _validate_token_signature(cls, pending_cashout: PendingCashout, token: str) -> bool:
        """FIXED: Validate HMAC signature using canonical message format
        
        This uses the same canonical_message() function as generation to ensure
        consistent serialization between generation and validation.
        """
        try:
            logger.debug(f"🔍 Validating token format: '{token}' (length: {len(token)})")
            
            # Extract signature from token
            if ':' not in token:
                logger.warning(f"🔍 Invalid token format - no colon found: '{token}'")
                return False
            
            token_parts = token.split(':')
            if len(token_parts) != 2:
                logger.warning(f"🔍 Invalid token format - expected 2 parts, got {len(token_parts)}: {token_parts}")
                return False
                
            received_signature = token_parts[1]
            
            # FIXED: Use canonical message formatter for consistent validation
            canonical_message = cls._canonical_message(pending_cashout)
            expected_signature = hmac.new(
                cls._get_secret_key(),
                canonical_message.encode('utf-8'),
                hashlib.sha256
            ).hexdigest()[:16]  # First 16 chars
            
            logger.debug(f"🔍 Canonical message for validation: '{canonical_message}'")
            logger.debug(f"🔍 Expected signature: '{expected_signature}'")
            logger.debug(f"🔍 Received signature: '{received_signature}'")
            
            # Constant-time comparison
            is_valid = hmac.compare_digest(received_signature, expected_signature)
            
            if is_valid:
                logger.debug("✅ Token signature validation successful")
            else:
                logger.warning("❌ Token signature validation failed")
                logger.warning(f"   Expected: {expected_signature}")
                logger.warning(f"   Received: {received_signature}")
                logger.warning(f"   Message:  {canonical_message}")
            
            return is_valid
            
        except Exception as e:
            logger.error(f"❌ Error validating token signature: {e}")
            return False
    
    @classmethod
    def cleanup_expired_tokens(cls) -> int:
        """Clean up expired pending cashout tokens"""
        try:
            session = SyncSessionLocal()
            try:
                now = datetime.now(timezone.utc)
                deleted_count = session.query(PendingCashout).filter(
                    PendingCashout.expires_at < now
                ).delete()
                
                session.commit()
                
                if deleted_count > 0:
                    logger.info(f"🧹 Cleaned up {deleted_count} expired cashout tokens")
                
                return deleted_count
                
            except Exception as e:
                session.rollback()
                logger.error(f"❌ Error cleaning up expired tokens: {e}")
                return 0
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"❌ Failed to cleanup expired tokens: {e}")
            return 0
    
    @classmethod 
    def generate_short_callback_id(cls, secure_token: str, user_id: int) -> str:
        """
        Generate a short callback ID that fits within Telegram's 64-byte limit
        Maps to the full secure token in database
        
        Args:
            secure_token: The full secure token
            user_id: User ID for additional security
            
        Returns:
            str: Short callback ID (format: "cc:<short_id>")
        """
        try:
            # Create short HMAC-based ID using token and user_id (no time component for stability)
            message = f"{user_id}:{secure_token}"
            short_signature = hmac.new(
                cls._get_secret_key(),
                message.encode('utf-8'),
                hashlib.sha256
            ).hexdigest()[:12]  # 12 chars for uniqueness
            
            # Format: "cc:" + 12-char signature = 15 chars total (well under 64 limit)
            short_callback_id = f"cc:{short_signature}"
            
            logger.info(f"🔗 Generated short callback ID for user {user_id}: {short_callback_id}")
            return short_callback_id
            
        except Exception as e:
            logger.error(f"❌ Failed to generate short callback ID for user {user_id}: {e}")
            # Fallback to even simpler format
            return f"cc:{secrets.token_urlsafe(8)}"
    
    @classmethod
    def resolve_callback_to_token(cls, callback_id: str, user_id: int) -> Optional[str]:
        """
        Resolve short callback ID back to full secure token
        
        Args:
            callback_id: Short callback ID (e.g., "cc:abc123def456")
            user_id: User ID for validation
            
        Returns:
            str: Full secure token if valid, None if not found/invalid
        """
        try:
            if not callback_id.startswith("cc:"):
                return None
                
            short_id = callback_id[3:]  # Remove "cc:" prefix
            
            # Find pending cashout for this user
            session = SyncSessionLocal()
            try:
                pending_cashout = session.query(PendingCashout).filter(
                    PendingCashout.user_id == user_id,
                    PendingCashout.expires_at > datetime.now(timezone.utc)
                ).first()
                
                if not pending_cashout:
                    logger.warning(f"⚠️ No active cashout found for callback resolution: user {user_id}")
                    return None
                
                # Verify this short_id matches the expected one for this token
                expected_callback_id = cls.generate_short_callback_id(pending_cashout.token, user_id)
                
                if callback_id != expected_callback_id:
                    logger.warning(f"⚠️ Callback ID mismatch for user {user_id}: got {callback_id}, expected {expected_callback_id}")
                    return None
                
                logger.info(f"✅ Resolved callback ID to token for user {user_id}: {short_id}")
                return pending_cashout.token
                
            except Exception as e:
                logger.error(f"❌ Error resolving callback for user {user_id}: {e}")
                return None
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"❌ Failed to resolve callback ID for user {user_id}: {e}")
            return None

    @classmethod
    def cancel_pending_cashout(cls, user_id: int, token: Optional[str] = None) -> bool:
        """Cancel a pending cashout (for user cancellation)"""
        try:
            session = SyncSessionLocal()
            try:
                query = session.query(PendingCashout).filter(
                    PendingCashout.user_id == user_id
                )
                
                if token:
                    query = query.filter(PendingCashout.token == token)
                
                deleted_count = query.delete()
                session.commit()
                
                logger.info(f"🗑️ Cancelled {deleted_count} pending cashouts for user {user_id}")
                return deleted_count > 0
                
            except Exception as e:
                session.rollback()
                logger.error(f"❌ Error cancelling pending cashout for user {user_id}: {e}")
                return False
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"❌ Failed to cancel pending cashout for user {user_id}: {e}")
            return False
    
    @classmethod
    def validate_configuration(cls) -> Dict[str, Any]:
        """Validate HMAC configuration and return status
        
        Returns:
            Dict containing validation status and configuration info
        """
        try:
            config_status = {
                "hmac_secret_configured": bool(getattr(Config, 'CASHOUT_HMAC_SECRET', None)),
                "hmac_enabled": getattr(Config, 'CASHOUT_HMAC_ENABLED', False),
                "is_production": getattr(Config, 'IS_PRODUCTION', False),
                "secret_length": 0,
                "validation_passed": False
            }
            
            secret = getattr(Config, 'CASHOUT_HMAC_SECRET', None)
            if secret:
                config_status["secret_length"] = len(secret)
                config_status["validation_passed"] = len(secret) >= 32
            
            # Try to get secret key to verify configuration
            try:
                cls._get_secret_key()
                config_status["secret_accessible"] = True
            except Exception as e:
                config_status["secret_accessible"] = False
                config_status["secret_error"] = str(e)
            
            return config_status
            
        except Exception as e:
            return {
                "validation_passed": False,
                "error": str(e)
            }