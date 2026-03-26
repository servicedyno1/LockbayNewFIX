"""
Shared Cashout OTP Flow Utility

Provides unified OTP verification flow for all cashout types (NGN, crypto)
with security context binding and comprehensive error handling.

Features:
- Unified interface for NGN and crypto cashout OTP verification
- Security context fingerprinting to prevent OTP reuse across different transactions
- Built-in rate limiting and cooldown management via EmailVerificationService
- Clean start(), verify(), resend() interface for easy integration
- Session validation and audit logging for enhanced security
"""

import logging
import hashlib
from typing import Dict, Any, Optional
from datetime import datetime, timedelta, timezone

from services.email_verification_service import EmailVerificationService
from models import User, EmailVerification
from database import managed_session
from sqlalchemy import select, and_

logger = logging.getLogger(__name__)


# Standardized error codes and messages for OTP operations
STANDARD_OTP_ERRORS = {
    "cooldown_active": "Please wait {seconds} seconds before requesting another code",
    "session_expired": "Your session has expired. Please start over",
    "session_not_found": "No active session found",
    "invalid_step": "Not at correct verification step",
    "email_mismatch": "Email address doesn't match session",
    "rate_limit_exceeded": "Too many requests. Please try again later",
    "daily_limit_exceeded": "Daily verification limit exceeded. Please try again tomorrow"
}


class CashoutOTPFlowError(Exception):
    """Custom exception for cashout OTP flow errors"""
    pass


class CashoutOTPFlow:
    """
    Shared utility for managing OTP verification flows for all cashout types
    
    Provides unified interface for:
    - NGN bank cashouts with account details binding
    - Crypto cashouts with asset/network/address/amount/fee binding
    - Context fingerprinting for security
    - Rate limiting and abuse prevention
    """
    
    @classmethod
    def _create_context_fingerprint(cls, context: Dict[str, Any]) -> str:
        """
        Create secure fingerprint of cashout context for verification binding - OPTIMIZED VERSION
        
        This prevents OTP codes from being reused across different cashout attempts
        by hashing the essential cashout parameters with improved performance.
        """
        # PERFORMANCE OPTIMIZATION: Use tuple for faster hashing instead of JSON
        cashout_type = context.get('cashout_type', '')
        
        # Build fingerprint components as tuple for efficient hashing
        fingerprint_components = [
            cashout_type,
            str(context.get('amount', '')),
            str(context.get('fee', ''))
        ]
        
        # NGN-specific fields (optimized)
        if cashout_type == 'ngn':
            bank_account = context.get('bank_account', {})
            fingerprint_components.extend([
                bank_account.get('bank_code', ''),
                bank_account.get('account_number', ''),
                bank_account.get('account_name', ''),
                str(context.get('rate_lock_token', '')),
                str(context.get('locked_rate', '')),
                str(context.get('locked_ngn_amount', ''))
            ])
                
        # Crypto-specific fields (optimized)
        elif cashout_type == 'crypto':
            fingerprint_components.extend([
                context.get('asset', ''),
                context.get('network', ''),
                context.get('address', ''),
                str(context.get('gross_amount', '')),
                str(context.get('net_amount', ''))
            ])
        
        # Create fingerprint from tuple (much faster than JSON)
        fingerprint_data = '|'.join(fingerprint_components)
        fingerprint = hashlib.sha256(fingerprint_data.encode()).hexdigest()[:16]
        
        logger.debug(f"Created optimized context fingerprint: {fingerprint} for type: {cashout_type}")
        return fingerprint
    
    @classmethod
    async def start_otp_verification(
        cls,
        user_id: int,
        email: str, 
        channel: str,
        context: Dict[str, Any],
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Start OTP verification flow for cashout
        
        Args:
            user_id: User ID
            email: Email address to send OTP to
            channel: Cashout channel ('ngn' or 'crypto')
            context: Cashout context containing transaction details
            ip_address: User's IP address for rate limiting
            user_agent: User's browser agent
            
        Returns:
            Dict with success status, verification_id, and context fingerprint
        """
        try:
            # Validate channel
            if channel not in ['ngn', 'crypto']:
                raise CashoutOTPFlowError(f"Invalid cashout channel: {channel}")
                
            # Add channel to context for fingerprinting
            context_with_channel = {**context, 'cashout_type': channel}
            
            # Create security fingerprint
            fingerprint = cls._create_context_fingerprint(context_with_channel)
            
            # PERFORMANCE OPTIMIZATION: Prepare lightweight cashout context
            # Only include essential fields to reduce memory footprint and processing time
            essential_fields = ['amount', 'fee', 'cashout_type']
            if channel == 'ngn':
                essential_fields.extend(['bank_account', 'rate_lock_token', 'locked_rate', 'locked_ngn_amount'])
            elif channel == 'crypto':
                essential_fields.extend(['asset', 'network', 'address', 'gross_amount', 'net_amount'])
            
            cashout_context = {
                'channel': channel,
                'fingerprint': fingerprint,
                'timestamp': datetime.utcnow().isoformat()
            }
            
            # Add only essential context fields
            for field in essential_fields:
                if field in context_with_channel:
                    cashout_context[field] = context_with_channel[field]
            
            # Send OTP using EmailVerificationService with proper session
            from database import async_managed_session
            async with async_managed_session() as session:
                result = await EmailVerificationService.send_otp_async(
                    session=session,
                    user_id=user_id,
                    email=email,
                    purpose='cashout',
                    ip_address=ip_address,
                    user_agent=user_agent,
                    cashout_context=cashout_context
                )
            
            if result['success']:
                logger.info(f"✅ OTP verification started for {channel} cashout - User: {user_id}, Fingerprint: {fingerprint}")
                return {
                    'success': True,
                    'verification_id': result['verification_id'],
                    'fingerprint': fingerprint,
                    'channel': channel,
                    'message': f"Security code sent to {email}",
                    'expires_at': result.get('expires_at')
                }
            else:
                logger.error(f"❌ Failed to start OTP verification for {channel} cashout - User: {user_id}, Error: {result.get('error')}")
                return {
                    'success': False,
                    'error': result.get('error', 'Failed to send verification code'),
                    'can_retry': result.get('can_retry', True),
                    'retry_after': result.get('retry_after')
                }
                
        except Exception as e:
            logger.error(f"❌ Exception in start_otp_verification: {e}")
            return {
                'success': False,
                'error': f'System error: {str(e)}',
                'can_retry': False
            }
    
    @classmethod
    def verify_otp_code(
        cls,
        user_id: int,
        otp_code: str,
        expected_fingerprint: str,
        channel: str
    ) -> Dict[str, Any]:
        """
        Verify OTP code with context fingerprint validation
        
        Args:
            user_id: User ID
            otp_code: OTP code entered by user
            expected_fingerprint: Expected context fingerprint from start_otp_verification
            channel: Expected cashout channel
            
        Returns:
            Dict with success status and verification details
        """
        try:
            # Prepare context for verification (fingerprint will be validated by EmailVerificationService)
            verification_context = {
                'expected_fingerprint': expected_fingerprint,
                'channel': channel
            }
            
            # Verify OTP using EmailVerificationService
            result = EmailVerificationService.verify_otp(
                user_id=user_id,
                otp_code=otp_code,
                purpose='cashout',
                cashout_context=verification_context
            )
            
            if result['success']:
                # Additional fingerprint validation for extra security
                stored_fingerprint = result.get('cashout_context', {}).get('fingerprint')
                if stored_fingerprint and stored_fingerprint != expected_fingerprint:
                    logger.error(f"🚨 Fingerprint mismatch - User: {user_id}, Expected: {expected_fingerprint}, Got: {stored_fingerprint}")
                    return {
                        'success': False,
                        'error': 'Security validation failed - cashout context changed',
                        'can_retry': False
                    }
                
                logger.info(f"✅ OTP verified successfully for {channel} cashout - User: {user_id}, Fingerprint: {expected_fingerprint}")
                
                # Extract verification_id from nested context_data (BUGFIX)
                verification_id = result.get('context_data', {}).get('verification_id')
                
                return {
                    'success': True,
                    'verification_id': verification_id,
                    'message': 'Verification successful',
                    'cashout_context': result.get('cashout_context', {})
                }
            else:
                logger.warning(f"❌ OTP verification failed for {channel} cashout - User: {user_id}, Error: {result.get('error')}")
                return {
                    'success': False,
                    'error': result.get('error', 'Invalid verification code'),
                    'attempts_remaining': result.get('attempts_remaining', 0),
                    'can_retry': result.get('can_retry', True)
                }
                
        except Exception as e:
            logger.error(f"❌ Exception in verify_otp_code: {e}")
            return {
                'success': False,
                'error': f'System error: {str(e)}',
                'can_retry': False
            }
    
    @classmethod
    def resend_otp_code(
        cls,
        user_id: int,
        email: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Resend OTP code with session validation, cooldown checks, and audit logging
        
        Security Features:
        - Validates active cashout session exists
        - Checks session hasn't expired
        - Verifies email matches session
        - Comprehensive audit logging
        
        Args:
            user_id: User ID
            email: Email address
            ip_address: User's IP address for rate limiting
            user_agent: User's browser agent
            
        Returns:
            Dict with success status and resend details in unified format:
            {
                "success": bool,
                "error": str (if failed),
                "message": str,
                "remaining_seconds": int (if cooldown),
                "retry_after": timestamp (Unix timestamp)
            }
        """
        try:
            # AUDIT LOG: Start of resend attempt
            logger.info(f"🔄 OTP_RESEND: User {user_id} | Email: {email} | IP: {ip_address or 'N/A'} | Agent: {user_agent or 'N/A'}")
            
            # Step 1: Retrieve active cashout session for validation
            with managed_session() as session:
                now = datetime.now(timezone.utc)
                
                # Get latest unverified cashout OTP session
                verification_session = session.query(EmailVerification).filter(
                    and_(
                        EmailVerification.user_id == user_id,
                        EmailVerification.purpose == 'cashout',
                        EmailVerification.verified == False,
                        EmailVerification.expires_at > now
                    )
                ).order_by(EmailVerification.created_at.desc()).first()
                
                # Validate session exists
                if not verification_session:
                    error_code = "session_not_found"
                    logger.warning(f"🔄 OTP_RESEND: User {user_id} | Email: {email} | Result: FAILED | Reason: {error_code}")
                    return {
                        'success': False,
                        'error': error_code,
                        'message': STANDARD_OTP_ERRORS[error_code]
                    }
                
                # Validate session hasn't expired
                if verification_session.expires_at <= now:
                    error_code = "session_expired"
                    logger.warning(f"🔄 OTP_RESEND: User {user_id} | Email: {email} | Result: FAILED | Reason: {error_code}")
                    return {
                        'success': False,
                        'error': error_code,
                        'message': STANDARD_OTP_ERRORS[error_code]
                    }
                
                # Validate email matches session
                if verification_session.email != email:
                    error_code = "email_mismatch"
                    logger.warning(f"🔄 OTP_RESEND: User {user_id} | Email: {email} | Session Email: {verification_session.email} | Result: FAILED | Reason: {error_code}")
                    return {
                        'success': False,
                        'error': error_code,
                        'message': STANDARD_OTP_ERRORS[error_code]
                    }
            
            # Step 2: Session validated, proceed with OTP resend
            result = EmailVerificationService.send_otp(
                user_id=user_id,
                email=email,
                purpose='cashout',  # Explicitly set purpose
                ip_address=ip_address,
                user_agent=user_agent
            )
            
            if result['success']:
                # AUDIT LOG: Success
                logger.info(f"🔄 OTP_RESEND: User {user_id} | Email: {email} | Result: SUCCESS")
                return {
                    'success': True,
                    'message': f"New security code sent to {email}",
                    'expires_at': result.get('expires_at')
                }
            else:
                # Handle rate limiting with unified format
                error_code = result.get('error', 'resend_failed')
                remaining_seconds = result.get('remaining_seconds', 0)
                
                # AUDIT LOG: Failure with reason
                logger.warning(f"🔄 OTP_RESEND: User {user_id} | Email: {email} | Result: FAILED | Reason: {error_code} | Cooldown: {remaining_seconds}s")
                
                # Unified rate limit response format
                response = {
                    'success': False,
                    'error': error_code,
                    'message': result.get('message', 'Failed to resend verification code')
                }
                
                # Add rate limit specific fields if cooldown is active
                if error_code == 'cooldown_active' and remaining_seconds > 0:
                    response['message'] = STANDARD_OTP_ERRORS['cooldown_active'].format(seconds=remaining_seconds)
                    response['remaining_seconds'] = remaining_seconds
                    response['retry_after'] = int((datetime.now(timezone.utc) + timedelta(seconds=remaining_seconds)).timestamp())
                
                return response
                
        except Exception as e:
            # AUDIT LOG: Exception
            logger.error(f"🔄 OTP_RESEND: User {user_id} | Email: {email} | Result: EXCEPTION | Error: {str(e)}")
            return {
                'success': False,
                'error': 'system_error',
                'message': f'System error: {str(e)}',
                'can_retry': False
            }
    
    @classmethod
    async def resend_otp_code_async(
        cls,
        user_id: int,
        email: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        ASYNC: Resend OTP code with session validation, cooldown checks, and audit logging
        
        Security Features:
        - Validates active cashout session exists
        - Checks session hasn't expired
        - Verifies email matches session
        - Comprehensive audit logging
        
        Args:
            user_id: User ID
            email: Email address
            ip_address: User's IP address for rate limiting
            user_agent: User's browser agent
            
        Returns:
            Dict with success status and resend details in unified format:
            {
                "success": bool,
                "error": str (if failed),
                "message": str,
                "remaining_seconds": int (if cooldown),
                "retry_after": timestamp (Unix timestamp)
            }
        """
        try:
            # AUDIT LOG: Start of resend attempt
            logger.info(f"🔄 OTP_RESEND: User {user_id} | Email: {email} | IP: {ip_address or 'N/A'} | Agent: {user_agent or 'N/A'}")
            
            from database import async_managed_session
            
            # Step 1: Retrieve active cashout session for validation
            async with async_managed_session() as session:
                now = datetime.now(timezone.utc)
                
                # Get latest unverified cashout OTP session
                result_query = await session.execute(
                    select(EmailVerification).where(
                        and_(
                            EmailVerification.user_id == user_id,
                            EmailVerification.purpose == 'cashout',
                            EmailVerification.verified == False,
                            EmailVerification.expires_at > now
                        )
                    ).order_by(EmailVerification.created_at.desc()).limit(1)
                )
                verification_session = result_query.scalar_one_or_none()
                
                # Validate session exists
                if not verification_session:
                    error_code = "session_not_found"
                    logger.warning(f"🔄 OTP_RESEND: User {user_id} | Email: {email} | Result: FAILED | Reason: {error_code}")
                    return {
                        'success': False,
                        'error': error_code,
                        'message': STANDARD_OTP_ERRORS[error_code]
                    }
                
                # Validate session hasn't expired
                if verification_session.expires_at <= now:
                    error_code = "session_expired"
                    logger.warning(f"🔄 OTP_RESEND: User {user_id} | Email: {email} | Result: FAILED | Reason: {error_code}")
                    return {
                        'success': False,
                        'error': error_code,
                        'message': STANDARD_OTP_ERRORS[error_code]
                    }
                
                # Validate email matches session
                if verification_session.email != email:
                    error_code = "email_mismatch"
                    logger.warning(f"🔄 OTP_RESEND: User {user_id} | Email: {email} | Session Email: {verification_session.email} | Result: FAILED | Reason: {error_code}")
                    return {
                        'success': False,
                        'error': error_code,
                        'message': STANDARD_OTP_ERRORS[error_code]
                    }
            
            # Step 2: Session validated, proceed with OTP resend
            async with async_managed_session() as session:
                result = await EmailVerificationService.send_otp_async(
                    session=session,
                    user_id=user_id,
                    email=email,
                    purpose='cashout',  # Explicitly set purpose
                    ip_address=ip_address,
                    user_agent=user_agent
                )
            
            if result['success']:
                # AUDIT LOG: Success
                logger.info(f"🔄 OTP_RESEND: User {user_id} | Email: {email} | Result: SUCCESS")
                return {
                    'success': True,
                    'message': f"New security code sent to {email}",
                    'expires_at': result.get('expires_at')
                }
            else:
                # Handle rate limiting with unified format
                error_code = result.get('error', 'resend_failed')
                remaining_seconds = result.get('remaining_seconds', 0)
                
                # AUDIT LOG: Failure with reason
                logger.warning(f"🔄 OTP_RESEND: User {user_id} | Email: {email} | Result: FAILED | Reason: {error_code} | Cooldown: {remaining_seconds}s")
                
                # Unified rate limit response format
                response = {
                    'success': False,
                    'error': error_code,
                    'message': result.get('message', 'Failed to resend verification code')
                }
                
                # Add rate limit specific fields if cooldown is active
                if error_code == 'cooldown_active' and remaining_seconds > 0:
                    response['message'] = STANDARD_OTP_ERRORS['cooldown_active'].format(seconds=remaining_seconds)
                    response['remaining_seconds'] = remaining_seconds
                    response['retry_after'] = int((datetime.now(timezone.utc) + timedelta(seconds=remaining_seconds)).timestamp())
                
                return response
                
        except Exception as e:
            # AUDIT LOG: Exception
            logger.error(f"🔄 OTP_RESEND: User {user_id} | Email: {email} | Result: EXCEPTION | Error: {str(e)}")
            return {
                'success': False,
                'error': 'system_error',
                'message': f'System error: {str(e)}',
                'can_retry': False
            }
    
    @classmethod
    async def get_user_email(cls, user_id: int) -> Optional[str]:
        """
        Get user's email address for OTP sending using async session
        
        Args:
            user_id: User ID
            
        Returns:
            User's email address or None if not found/not verified
        """
        try:
            # FIX: Use sync session context instead of async
            with managed_session() as session:
                # Use sync session query
                user = session.query(User).filter(User.telegram_id == int(user_id)).first()
                if user and user.email and user.email_verified:  # type: ignore[operator]
                    return user.email  # type: ignore[return-value]
                return None
        except Exception as e:
            logger.error(f"Error getting user email for user {user_id}: {e}")
            return None
    
    @classmethod
    def create_ngn_context(
        cls,
        amount: str,
        fee: str,
        bank_account: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Create standardized context for NGN cashout OTP
        
        Args:
            amount: NGN amount 
            fee: Processing fee
            bank_account: Bank account details
            
        Returns:
            Standardized NGN cashout context
        """
        return {
            'amount': amount,
            'fee': fee,
            'bank_account': {
                'bank_code': bank_account.get('bank_code', ''),
                'account_number': bank_account.get('account_number', ''),
                'account_name': bank_account.get('account_name', ''),
                'bank_name': bank_account.get('bank_name', '')
            }
        }
    
    @classmethod
    def create_crypto_context(
        cls,
        asset: str,
        network: str,
        address: str,
        gross_amount: str,
        net_amount: str,
        fee: str,
        platform_fee: Optional[str] = None,
        network_fee: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create standardized context for crypto cashout OTP
        
        Args:
            asset: Cryptocurrency asset (BTC, ETH, etc.)
            network: Network type (BTC, ERC20, TRC20, etc.)
            address: Withdrawal address
            gross_amount: Gross withdrawal amount
            net_amount: Net amount after fees
            fee: Total fee amount (for backward compatibility)
            platform_fee: Platform fee component (optional)
            network_fee: Network/Kraken fee component (optional)
            
        Returns:
            Standardized crypto cashout context
        """
        return {
            'asset': asset,
            'network': network,
            'address': address,
            'gross_amount': gross_amount,
            'net_amount': net_amount,
            'fee': fee,
            'platform_fee': platform_fee or fee,  # Default to total fee if not split
            'network_fee': network_fee or '0'
        }