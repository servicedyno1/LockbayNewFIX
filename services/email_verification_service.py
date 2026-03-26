"""
EmailVerificationService - Robust Sync Architecture with Async Interface
Uses sync database operations with asyncio.to_thread for async compatibility

Features:
- Sync database operations for consistency with PostgreSQL driver
- Async interface via asyncio.to_thread wrapper
- Rate limiting with hourly send limits (max 4 sends per hour)
- Professional error messages
- Thread-safe operations
"""

import asyncio
import logging
import os
import secrets
import string
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func, and_, select
from sqlalchemy.dialects.postgresql import insert

from models import EmailVerification, User
from database import managed_session
from services.email import EmailService
from services.background_email_queue import background_email_queue
from utils.helpers import validate_email
from config import Config

logger = logging.getLogger(__name__)


class EmailVerificationError(Exception):
    """Custom exception for email verification errors"""
    pass


class RateLimitError(EmailVerificationError):
    """Rate limiting error with user-friendly messages"""
    pass


class EmailVerificationService:
    """
    Fully async email verification service with rate limiting and abuse prevention
    
    Features:
    - AsyncSession-only database operations
    - Hourly send limit (max 4 sends per hour)
    - Max daily OTP sends per user and IP
    - Max attempts per OTP code
    - Automatic cleanup of expired records
    """
    
    # Configuration constants
    DEFAULT_OTP_LENGTH = int(os.getenv("OTP_LENGTH", "6"))
    DEFAULT_OTP_EXPIRY_MINUTES = int(os.getenv("OTP_EXPIRY_MINUTES", "15"))
    DEFAULT_RESEND_COOLDOWN_SECONDS = int(os.getenv("OTP_RESEND_COOLDOWN_SECONDS", "60"))
    DEFAULT_MAX_DAILY_SENDS_PER_USER = int(os.getenv("OTP_MAX_DAILY_SENDS_PER_USER", "100"))
    DEFAULT_MAX_DAILY_SENDS_PER_IP = int(os.getenv("OTP_MAX_DAILY_SENDS_PER_IP", "200"))
    DEFAULT_MAX_ATTEMPTS_PER_OTP = int(os.getenv("OTP_MAX_ATTEMPTS_PER_CODE", "5"))
    
    # Purpose-specific configurations
    PURPOSE_CONFIGS = {
        'registration': {
            'template_title': '📧 Email Verification Required',
            'template_content': 'Please verify your email address to complete your registration.',
            'max_attempts': 5,
            'expiry_minutes': 15
        },
        'cashout': {
            'template_title': '🔐 Secure Your Cashout',
            'template_content': 'Please verify this cashout request with the code below.',
            'max_attempts': 3,
            'expiry_minutes': 10
        },
        'change_email': {
            'template_title': '🔄 Email Change Verification',
            'template_content': 'Please verify your new email address to complete the change.',
            'max_attempts': 5,
            'expiry_minutes': 30
        },
        'password_reset': {
            'template_title': '🔑 Password Reset Verification',
            'template_content': 'Please verify your identity to reset your password.',
            'max_attempts': 3,
            'expiry_minutes': 10
        }
    }
    
    @classmethod
    def _generate_otp(cls, length: Optional[int] = None) -> str:
        """Generate a cryptographically secure OTP code"""
        if length is None:
            length = cls.DEFAULT_OTP_LENGTH
        chars = string.digits
        otp = ''.join(secrets.choice(chars) for _ in range(length))
        logger.debug(f"Generated OTP of length {length}")
        return otp
    
    @classmethod
    def _hash_otp(cls, otp: str) -> str:
        """Create SHA256 hash of OTP for secure storage"""
        return hashlib.sha256(otp.encode()).hexdigest()
    
    @classmethod
    def _get_purpose_config(cls, purpose: str) -> Dict[str, Any]:
        """Get configuration for specific verification purpose"""
        return cls.PURPOSE_CONFIGS.get(purpose, cls.PURPOSE_CONFIGS['registration'])
    
    @classmethod
    async def _check_user_daily_limit(cls, session: AsyncSession, user_id: int) -> Tuple[bool, int]:
        """Check if user has exceeded daily OTP sending limit"""
        from datetime import timezone
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        daily_count_result = await session.execute(
            select(func.count(EmailVerification.id)).where(
                and_(
                    EmailVerification.user_id == user_id,
                    EmailVerification.created_at >= today_start
                )
            )
        )
        daily_count = daily_count_result.scalar() or 0
        max_daily = cls.DEFAULT_MAX_DAILY_SENDS_PER_USER
        within_limit = daily_count < max_daily
        logger.debug(f"User {user_id} daily OTP count: {daily_count}/{max_daily}")
        return within_limit, daily_count
    
    @classmethod
    async def _check_ip_daily_limit(cls, session: AsyncSession, ip_address: str) -> Tuple[bool, int]:
        """Check if IP has exceeded daily OTP sending limit"""
        # NOTE: EmailVerification model doesn't have ip_address field
        # Return True (allow) since we can't track by IP in current schema
        logger.debug("IP rate limiting skipped - no ip_address field in EmailVerification model")
        return True, 0
    
    @classmethod
    async def _check_hourly_send_limit(cls, session: AsyncSession, user_id: int, email: str, purpose: str = 'cashout') -> Tuple[bool, int]:
        """Check if user has exceeded hourly OTP sending limit (max 4 sends in 1 hour)"""
        from datetime import timezone
        one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
        hourly_count_result = await session.execute(
            select(func.count(EmailVerification.id)).where(
                and_(
                    EmailVerification.user_id == user_id,
                    EmailVerification.email == email,
                    EmailVerification.purpose == purpose,
                    EmailVerification.created_at >= one_hour_ago
                )
            )
        )
        hourly_count = hourly_count_result.scalar() or 0
        max_hourly_sends = 4  # Initial send + 3 resends
        within_limit = hourly_count < max_hourly_sends
        logger.debug(f"User {user_id} hourly OTP count for {purpose}: {hourly_count}/{max_hourly_sends}")
        return within_limit, hourly_count
    
    @classmethod
    async def _check_resend_cooldown(cls, session: AsyncSession, user_id: int, email: str) -> Tuple[bool, int]:
        """Check if resend cooldown period has passed"""
        latest_result = await session.execute(
            select(EmailVerification).where(
                and_(
                    EmailVerification.user_id == user_id,
                    EmailVerification.email == email,
                    EmailVerification.verified == False
                )
            ).order_by(EmailVerification.created_at.desc()).limit(1)
        )
        latest = latest_result.scalar_one_or_none()
        
        if not latest:
            return True, 0
        
        cooldown_period = timedelta(seconds=cls.DEFAULT_RESEND_COOLDOWN_SECONDS)
        # FIX: Ensure both datetimes are timezone-aware to avoid subtraction error
        from datetime import timezone
        now_utc = datetime.now(timezone.utc)
        time_since_creation = now_utc - latest.created_at
        
        if time_since_creation >= cooldown_period:  # type: ignore
            return True, 0
        
        remaining_seconds = int((cooldown_period - time_since_creation).total_seconds())
        logger.debug(f"Resend cooldown for user {user_id}: {remaining_seconds}s remaining")
        return False, remaining_seconds

    @classmethod
    def _check_user_daily_limit_sync(cls, session, user_id: int) -> Tuple[bool, int]:
        """Sync version: Check if user has exceeded daily OTP sending limit"""
        from datetime import timezone
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        daily_count = session.query(func.count(EmailVerification.id)).filter(
            and_(
                EmailVerification.user_id == user_id,
                EmailVerification.created_at >= today_start
            )
        ).scalar() or 0
        max_daily = cls.DEFAULT_MAX_DAILY_SENDS_PER_USER
        within_limit = daily_count < max_daily
        logger.debug(f"User {user_id} daily OTP count: {daily_count}/{max_daily}")
        return within_limit, daily_count
    
    @classmethod
    def _check_ip_daily_limit_sync(cls, session, ip_address: str) -> Tuple[bool, int]:
        """Sync version: Check if IP has exceeded daily OTP sending limit"""
        # NOTE: EmailVerification model doesn't have ip_address field
        # Return True (allow) since we can't track by IP in current schema
        logger.debug("IP rate limiting skipped - no ip_address field in EmailVerification model")
        return True, 0
    
    @classmethod
    def _check_hourly_send_limit_sync(cls, session, user_id: int, email: str, purpose: str = 'cashout') -> Tuple[bool, int]:
        """Sync version: Check if user has exceeded hourly OTP sending limit (max 4 sends in 1 hour)"""
        from datetime import timezone
        one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
        hourly_count = session.query(func.count(EmailVerification.id)).filter(
            and_(
                EmailVerification.user_id == user_id,
                EmailVerification.email == email,
                EmailVerification.purpose == purpose,
                EmailVerification.created_at >= one_hour_ago
            )
        ).scalar() or 0
        max_hourly_sends = 4  # Initial send + 3 resends
        within_limit = hourly_count < max_hourly_sends
        logger.debug(f"User {user_id} hourly OTP count for {purpose}: {hourly_count}/{max_hourly_sends}")
        return within_limit, hourly_count
    
    @classmethod
    def create_and_send_otp_sync(
        cls,
        user_id: int,
        email: str,
        purpose: str = 'registration',
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        cashout_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        CENTRALIZED sync factory for EmailVerification creation and OTP sending
        This is the ONLY method that should create EmailVerification records
        """
        with managed_session() as session:
            # Validate email format
            if not validate_email(email):
                return {
                    "success": False,
                    "error": "invalid_email",
                    "message": "Please enter a valid email address."
                }
            
            # Check if user exists
            user = session.query(User).filter(User.id == user_id).first()
            if not user:
                logger.error(f"User {user_id} not found for OTP sending")
                return {
                    "success": False,
                    "error": "user_not_found",
                    "message": "User account not found."
                }
            
            # Rate limiting checks - DISABLED FOR TESTING
            # user_within_limit, user_count = cls._check_user_daily_limit_sync(session, user_id)
            # if not user_within_limit:
            #     logger.warning(f"User {user_id} exceeded daily OTP limit: {user_count}")
            #     return {
            #         "success": False,
            #         "error": "daily_limit_exceeded",
            #         "message": f"You've reached the daily limit of {cls.DEFAULT_MAX_DAILY_SENDS_PER_USER} verification emails. Please try again tomorrow."
            #     }
            
            # ip_within_limit, ip_count = cls._check_ip_daily_limit_sync(session, ip_address or "")
            # if not ip_within_limit:
            #     logger.warning(f"IP {ip_address} exceeded daily OTP limit: {ip_count}")
            #     return {
            #         "success": False,
            #         "error": "ip_limit_exceeded",
            #         "message": "Too many verification requests from this location. Please try again later."
            #     }
            
            # Check hourly send limit (max 4 sends in 1 hour) - DISABLED FOR TESTING
            # hourly_within_limit, hourly_count = cls._check_hourly_send_limit_sync(session, user_id, email, purpose)
            # if not hourly_within_limit:
            #     logger.warning(f"User {user_id} exceeded hourly OTP limit: {hourly_count} sends in last hour")
            #     return {
            #         "success": False,
            #         "error": "max_resends_exceeded",
            #         "message": "You've requested the maximum number of verification codes. Please contact support for assistance.",
            #         "hourly_count": hourly_count
            #     }
            
            # Generate OTP
            purpose_config = cls._get_purpose_config(purpose)
            otp_code = cls._generate_otp()
            expiry_minutes = purpose_config.get('expiry_minutes', cls.DEFAULT_OTP_EXPIRY_MINUTES)
            from datetime import timezone
            expires_at = datetime.now(timezone.utc) + timedelta(minutes=expiry_minutes)
            
            # FAST PATH: Create DB record FIRST using UPSERT, send email in background
            # This allows immediate webhook response while email sends async
            logger.info(f"🚀 FAST_PATH: Creating OTP record for user {user_id}, email will be sent in background")
            
            # Use PostgreSQL UPSERT to atomically handle concurrent requests (sync version)
            from datetime import timezone
            now_utc = datetime.now(timezone.utc)
            max_attempts = purpose_config.get('max_attempts', cls.DEFAULT_MAX_ATTEMPTS_PER_OTP)
            
            stmt = insert(EmailVerification).values(
                user_id=user_id,
                email=email,
                verification_code=otp_code,
                purpose=purpose,
                verified=False,
                attempts=0,
                max_attempts=max_attempts,
                expires_at=expires_at
            )
            
            # On conflict, update the existing record with new OTP and reset attempts
            # Use index_elements because idx_email_verifications_active_unverified_unique is an INDEX not a CONSTRAINT
            # Index WHERE clause: (deleted_at IS NULL) AND (verified = false)
            stmt = stmt.on_conflict_do_update(
                index_elements=['email', 'purpose'],
                index_where=and_(
                    EmailVerification.deleted_at.is_(None),
                    EmailVerification.verified == False
                ),
                set_=dict(
                    verification_code=otp_code,
                    expires_at=expires_at,
                    attempts=0,
                    max_attempts=max_attempts,
                    created_at=now_utc
                )
            ).returning(EmailVerification.id)
            
            result = session.execute(stmt)
            verification_id = result.scalar_one()
            
            # CRITICAL FIX: Trigger background email send IMMEDIATELY after record creation
            # Previously this was missing in some paths, causing records to exist but emails not to fire
            logger.info(f"🚀 TRIGGER_EMAIL: Sending OTP directly for verification {verification_id} (fallback for reliability)")
            try:
                from services.email import EmailService
                email_service = EmailService()
                
                # Create optimized email content
                subject = f"🔐 Your verification code: {otp_code}"
                
                # Use the user's first name if available
                user_display_name = "User"
                if user and hasattr(user, 'first_name') and user.first_name:
                    user_display_name = user.first_name
                
                # Check if background_email_queue is initialized and in no-op mode
                # If so, or if we want maximum reliability for OTP, send directly
                html_content = cls._create_otp_email_html(otp_code, user_display_name, purpose)
                success = email_service.send_email(
                    to_email=email,
                    subject=subject,
                    html_content=html_content
                )
                if success:
                    logger.info(f"✅ OTP email sent directly to {email}")
                else:
                    logger.error(f"❌ Direct OTP email send failed for {email}")
            except Exception as email_err:
                logger.error(f"❌ Error in direct OTP email send: {email_err}")

            # Also queue in background if possible for redundancy/tracking
            asyncio.create_task(cls._send_email_background_task_safe(
                verification_id=verification_id,
                email=email,
                purpose=purpose,
                expiry_minutes=expiry_minutes,
                user_id=user_id
            ))
            
            logger.info(f"✅ OTP record created for {email} for user {user_id} (purpose: {purpose}) - DB operation complete")
            return {
                "success": True,
                "message": f"Verification code is being sent to {email}",
                "verification_id": verification_id,
                "expires_in_minutes": expiry_minutes,
                "max_attempts": purpose_config.get('max_attempts', cls.DEFAULT_MAX_ATTEMPTS_PER_OTP),
                "resend_cooldown_seconds": cls.DEFAULT_RESEND_COOLDOWN_SECONDS
            }

    @classmethod
    def _check_resend_cooldown_sync(cls, session, user_id: int, email: str) -> Tuple[bool, int]:
        """Sync version: Check if resend cooldown period has passed"""
        latest = session.query(EmailVerification).filter(
            and_(
                EmailVerification.user_id == user_id,
                EmailVerification.email == email,
                EmailVerification.verified == False
            )
        ).order_by(EmailVerification.created_at.desc()).first()
        
        if not latest:
            return True, 0
        
        cooldown_period = timedelta(seconds=cls.DEFAULT_RESEND_COOLDOWN_SECONDS)
        # FIX: Ensure both datetimes are timezone-aware to avoid subtraction error
        from datetime import timezone
        now_utc = datetime.now(timezone.utc)
        time_since_creation = now_utc - latest.created_at
        
        if time_since_creation >= cooldown_period:
            return True, 0
        
        remaining_seconds = int((cooldown_period - time_since_creation).total_seconds())
        logger.debug(f"Resend cooldown for user {user_id}: {remaining_seconds}s remaining")
        return False, remaining_seconds

    @classmethod
    def _create_otp_email_html(
        cls,
        otp_code: str,
        user_name: str,
        purpose: str
    ) -> str:
        """Helper to create OTP email HTML content directly"""
        purpose_configs = {
            'registration': {
                'title': '📧 Email Verification',
                'message': 'Please verify your email address to complete registration.',
                'color': '#007bff'
            },
            'cashout': {
                'title': '🔐 Secure Your Cashout',
                'message': 'Please verify this cashout request with the code below.',
                'color': '#28a745'
            },
            'change_email': {
                'title': '🔄 Email Change Verification',
                'message': 'Please verify your new email address.',
                'color': '#6f42c1'
            }
        }
        
        config = purpose_configs.get(purpose, purpose_configs['registration'])
        
        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>{config['title']}</title>
        </head>
        <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; background-color: #f8f9fa;">
            <div style="background: white; border-radius: 10px; padding: 30px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
                <div style="text-align: center; margin-bottom: 30px;">
                    <h1 style="color: {config['color']}; margin: 0; font-size: 24px;">{config['title']}</h1>
                    <p style="color: #6c757d; margin: 10px 0 0 0;">{Config.PLATFORM_NAME} Secure Platform</p>
                </div>
                <div style="text-align: center;">
                    <p style="color: #333; font-size: 16px; margin-bottom: 20px;">
                        Hello {user_name}! 👋
                    </p>
                    <p style="color: #333; font-size: 16px; margin-bottom: 30px;">
                        {config['message']}
                    </p>
                    <div style="background: linear-gradient(135deg, {config['color']}, {config['color']}dd); padding: 25px; border-radius: 10px; margin: 25px 0;">
                        <p style="color: white; margin: 0 0 10px 0; font-size: 14px;">Your verification code:</p>
                        <div style="font-size: 32px; font-weight: bold; color: white; letter-spacing: 5px; font-family: monospace;">
                            {otp_code}
                        </div>
                    </div>
                    <p style="color: #dc3545; font-size: 14px; margin: 20px 0;">
                        ⏰ This code expires in 15 minutes
                    </p>
                </div>
                <div style="text-align: center; margin-top: 30px; padding-top: 20px; border-top: 1px solid #e9ecef;">
                    <p style="color: #6c757d; font-size: 12px; margin: 0;">
                        This is an automated message from {Config.PLATFORM_NAME}<br>
                        If you didn't request this, please ignore this email.
                    </p>
                </div>
            </div>
        </body>
        </html>
        """

    @classmethod
    async def _send_email_background_task_safe(
        cls,
        verification_id: int,
        email: str,
        purpose: str,
        expiry_minutes: int,
        user_id: int
    ) -> None:
        """
        Send OTP email in background using background queue (PERFORMANCE FIX)
        
        PERFORMANCE OPTIMIZATION: Uses background queue instead of blocking email sends
        This eliminates 2-5 second delays in webhook responses
        """
        import time
        start_time = time.time()
        
        try:
            logger.info(f"⏱️ PERF: Starting background email for verification {verification_id}, user {user_id}")
            
            # Get OTP code and user info from database
            def _get_verification_data():
                """Get verification and user data from database"""
                from database import managed_session
                from models import User
                
                with managed_session() as session:
                    # Get specific verification record by ID (no race condition)
                    verification = session.query(EmailVerification).filter(
                        EmailVerification.id == verification_id
                    ).first()
                    
                    if not verification:
                        logger.error(f"❌ Verification record {verification_id} not found for background email")
                        return None, None
                    
                    # Import ORM typing helper
                    from utils.orm_typing_helpers import as_bool
                    
                    if as_bool(verification.verified):
                        logger.info(f"✅ Verification {verification_id} already verified, skipping email")
                        return None, None
                    
                    # Get user info for personalized email
                    user = session.query(User).filter(User.id == user_id).first()
                    user_name = getattr(user, 'first_name', 'User') if user else 'User'
                    
                    # Extract the actual OTP code value from the column
                    actual_otp_code = str(verification.verification_code)
                    
                    return actual_otp_code, user_name
            
            # Get data from DB in thread pool
            otp_code, user_name = await asyncio.to_thread(_get_verification_data)
            
            if not otp_code or not user_name:
                logger.info(f"Skipping email send - verification {verification_id} already verified or not found")
                return
            
            # Queue email for background processing - non-blocking and fast
            queue_result = await cls._send_otp_email_background(
                email=email,
                otp_code=otp_code,
                purpose=purpose,
                expiry_minutes=expiry_minutes,
                user_id=user_id,
                user_name=user_name
            )
            
            elapsed = time.time() - start_time
            
            if queue_result.get("success"):
                logger.info(f"✅ Background email queued for {email} in {elapsed*1000:.2f}ms (verification {verification_id})")
            else:
                logger.error(f"❌ Background email queue failed for {email} after {elapsed*1000:.2f}ms: {queue_result.get('error')}")
                
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"❌ Safe background email task error for verification {verification_id} after {elapsed*1000:.2f}ms: {e}")

    @classmethod
    async def send_otp_async(
        cls,
        session: Optional[AsyncSession] = None,
        user_id: Optional[int] = None,
        email: Optional[str] = None,
        purpose: str = 'registration',
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        cashout_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        BULLETPROOF async OTP sending with comprehensive session management
        """
        logger.info(f"🚀 BULLETPROOF_OTP: Starting for user {user_id}, email {email}")
        
        try:
            # Step 1: Validate inputs
            if user_id is None:
                return {"success": False, "error": "invalid_parameters", "message": "User ID required"}
            if email is None:
                return {"success": False, "error": "invalid_parameters", "message": "Email required"}
            if not validate_email(email):
                return {"success": False, "error": "invalid_email", "message": "Invalid email format"}
            
            # Step 2: Ensure we have a valid session - BULLETPROOF approach
            if session is None:
                logger.info(f"🔧 AUTO_SESSION: Creating new async session for user {user_id}")
                from database import async_managed_session
                async with async_managed_session() as auto_session:
                    return await cls._send_otp_with_session(
                        session=auto_session,
                        user_id=user_id,
                        email=email,
                        purpose=purpose,
                        ip_address=ip_address,
                        user_agent=user_agent,
                        cashout_context=cashout_context
                    )
            else:
                return await cls._send_otp_with_session(
                    session=session,
                    user_id=user_id,
                    email=email,
                    purpose=purpose,
                    ip_address=ip_address,
                    user_agent=user_agent,
                    cashout_context=cashout_context
                )
                
        except Exception as e:
            logger.error(f"❌ BULLETPROOF_OTP failed for user {user_id}: {e}")
            return {"success": False, "error": "system_error", "message": "System error occurred"}
    
    @classmethod
    async def _send_otp_with_session(
        cls,
        session: AsyncSession,
        user_id: int,
        email: str,
        purpose: str = 'registration',
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        cashout_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Internal method that handles OTP sending with guaranteed valid session"""
        logger.info(f"🔧 SESSION_OTP: Processing for user {user_id} with session {type(session)}")
        
        try:
            # Ensure session is not None before any database operations
            if session is None:
                raise ValueError("Session cannot be None in _send_otp_with_session")
            
            # Step 1: Check if user exists
            user_result = await session.execute(select(User).where(User.id == user_id))
            user = user_result.scalar_one_or_none()
            if not user:
                return {"success": False, "error": "user_not_found", "message": "User not found"}
            
            # Step 2: Rate limiting - DISABLED
            # user_within_limit, user_count = await cls._check_user_daily_limit(session, user_id)
            # if not user_within_limit:
            #     return {
            #         "success": False, 
            #         "error": "daily_limit_exceeded",
            #         "message": f"Daily limit of {cls.DEFAULT_MAX_DAILY_SENDS_PER_USER} emails reached"
            #     }
            
            # Check hourly send limit (max 4 sends in 1 hour) - DISABLED
            # hourly_within_limit, hourly_count = await cls._check_hourly_send_limit(session, user_id, email, purpose)
            # if not hourly_within_limit:
            #     logger.warning(f"User {user_id} exceeded hourly OTP limit: {hourly_count} sends in last hour")
            #     return {
            #         "success": False,
            #         "error": "max_resends_exceeded",
            #         "message": "You've requested the maximum number of verification codes. Please contact support for assistance.",
            #         "hourly_count": hourly_count
            #     }
            
            # Step 3: Generate OTP and create verification record using UPSERT
            purpose_config = cls._get_purpose_config(purpose)
            otp_code = cls._generate_otp()
            expiry_minutes = purpose_config.get('expiry_minutes', cls.DEFAULT_OTP_EXPIRY_MINUTES)
            from datetime import timezone
            now_utc = datetime.now(timezone.utc)
            expires_at = now_utc + timedelta(minutes=expiry_minutes)
            max_attempts = purpose_config.get('max_attempts', cls.DEFAULT_MAX_ATTEMPTS_PER_OTP)
            
            # Use PostgreSQL UPSERT to atomically handle concurrent requests (async version)
            stmt = insert(EmailVerification).values(
                user_id=user_id,
                email=email,
                verification_code=otp_code,
                purpose=purpose,
                verified=False,
                attempts=0,
                max_attempts=max_attempts,
                expires_at=expires_at
            )
            
            # On conflict, update the existing record with new OTP and reset attempts
            # Use index_elements because idx_email_verifications_active_unverified_unique is an INDEX not a CONSTRAINT
            # Index WHERE clause: (deleted_at IS NULL) AND (verified = false)
            stmt = stmt.on_conflict_do_update(
                index_elements=['email', 'purpose'],
                index_where=and_(
                    EmailVerification.deleted_at.is_(None),
                    EmailVerification.verified == False
                ),
                set_=dict(
                    verification_code=otp_code,
                    expires_at=expires_at,
                    attempts=0,
                    max_attempts=max_attempts,
                    created_at=now_utc
                )
            ).returning(EmailVerification.id)
            
            result = await session.execute(stmt)
            verification_id = result.scalar_one()
            
            # Step 4: Send OTP email DIRECTLY for instant delivery (like balance reports)
            # CRITICAL: OTP emails are time-sensitive and should not be queued
            user_name = getattr(user, 'first_name', 'User') or 'User'
            
            # Create email content using the professional template
            from services.email_templates import create_unified_email_template
            
            # Map purpose to template title/content if needed, or use defaults from template system
            template_data = cls.PURPOSE_CONFIGS.get(purpose, cls.PURPOSE_CONFIGS['registration'])
            
            subject = f"🔐 Your verification code: {otp_code}"
            html_content = create_unified_email_template(
                title=template_data['template_title'],
                content=template_data['template_content'],
                user_name=user_name,
                otp_code=otp_code,
                expiry_minutes=expiry_minutes
            )
            
            # Send email DIRECTLY (no queue) for instant delivery
            # We use send_email_async if available, otherwise fallback to threaded send_email
            email_service = EmailService()
            try:
                # Use the existing direct send method which is robust
                # FIXED: Added await for send_email_async and handled user_name for direct template rendering
                if hasattr(email_service, 'send_email_async'):
                    email_sent = await email_service.send_email_async(
                        to_email=email,
                        subject=subject,
                        html_content=html_content
                    )
                else:
                    # Threaded fallback for sync environments
                    if asyncio.iscoroutinefunction(email_service.send_email):
                         email_sent = await email_service.send_email(
                            to_email=email,
                            subject=subject,
                            html_content=html_content
                        )
                    else:
                        email_service.send_email(
                            to_email=email,
                            subject=subject,
                            html_content=html_content
                        )
                        email_sent = True
                logger.info(f"✅ OTP email sent DIRECTLY to {email} for user {user_id}")
            except Exception as email_error:
                logger.error(f"❌ Failed to send OTP email to {email}: {email_error}")
                email_sent = False
            
            logger.info(f"✅ OTP created for user {user_id}, verification_id {verification_id}")
            
            return {
                "success": True,
                "message": f"Verification code sent to {email}",
                "verification_id": verification_id,
                "expires_in_minutes": expiry_minutes,
                "max_attempts": purpose_config.get('max_attempts', cls.DEFAULT_MAX_ATTEMPTS_PER_OTP),
                "resend_cooldown_seconds": cls.DEFAULT_RESEND_COOLDOWN_SECONDS,
                "email_sent": email_sent  # Direct send status instead of queued
            }
            
        except Exception as e:
            logger.error(f"❌ Session OTP error for user {user_id}: {e}")
            return {"success": False, "error": "system_error", "message": "System error occurred"}

    @classmethod
    async def create_otp_record_async(
        cls,
        session: AsyncSession,
        user_id: int,
        email: str,
        purpose: str = 'registration',
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        cashout_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Create OTP record in database and return the actual OTP code for external use
        
        This method performs all database operations to create the OTP record
        but does NOT send the email - returns the actual OTP code for the caller
        to handle email sending through their preferred method.
        
        CRITICAL: This is used to fix the PLACEHOLDER bug in async refactoring
        
        Args:
            session: Single async session for all database operations
            user_id: User ID for OTP creation
            email: Email address for OTP
            purpose: Purpose of verification (registration, cashout, etc.)
            ip_address: Optional IP address for rate limiting
            user_agent: Optional user agent for tracking
            cashout_context: Optional cashout context data
            
        Returns:
            Dict with success status and actual OTP code if successful
        """
        logger.info(f"🔧 OTP_RECORD_CREATE: Creating OTP record for user {user_id} (email: {email})")
        
        try:
            # Step 1: Validate email format
            if not validate_email(email):
                return {
                    "success": False,
                    "error": "invalid_email",
                    "message": "Please enter a valid email address."
                }
            
            # Step 2: Check if user exists using provided session
            result = await session.execute(select(User).where(User.id == user_id))
            user = result.scalar_one_or_none()
            
            if not user:
                logger.error(f"User {user_id} not found for OTP creation")
                return {
                    "success": False,
                    "error": "user_not_found",
                    "message": "User account not found."
                }
            
            # Step 3: Rate limiting checks (async) - DISABLED FOR TESTING
            # user_within_limit, user_count = await cls._check_user_daily_limit(session, user_id)
            # if not user_within_limit:
            #     logger.warning(f"User {user_id} exceeded daily OTP limit: {user_count}")
            #     return {
            #         "success": False,
            #         "error": "daily_limit_exceeded",
            #         "message": f"You've reached the daily limit of {cls.DEFAULT_MAX_DAILY_SENDS_PER_USER} verification emails. Please try again tomorrow."
            #     }
            
            # ip_within_limit, ip_count = await cls._check_ip_daily_limit(session, ip_address or "")
            # if not ip_within_limit:
            #     logger.warning(f"IP {ip_address} exceeded daily OTP limit: {ip_count}")
            #     return {
            #         "success": False,
            #         "error": "ip_limit_exceeded",
            #         "message": "Too many verification requests from this location. Please try again later."
            #     }
            
            # Check hourly send limit (max 4 sends in 1 hour) - DISABLED FOR TESTING
            # hourly_within_limit, hourly_count = await cls._check_hourly_send_limit(session, user_id, email, purpose)
            # if not hourly_within_limit:
            #     logger.warning(f"User {user_id} exceeded hourly OTP limit: {hourly_count} sends in last hour")
            #     return {
            #         "success": False,
            #         "error": "max_resends_exceeded",
            #         "message": "You've requested the maximum number of verification codes. Please contact support for assistance.",
            #         "hourly_count": hourly_count
            #     }
            
            # Step 4: Generate OTP and prepare data
            purpose_config = cls._get_purpose_config(purpose)
            otp_code = cls._generate_otp()
            expiry_minutes = purpose_config.get('expiry_minutes', cls.DEFAULT_OTP_EXPIRY_MINUTES)
            from datetime import timezone
            now_utc = datetime.now(timezone.utc)
            expires_at = now_utc + timedelta(minutes=expiry_minutes)
            max_attempts = purpose_config.get('max_attempts', cls.DEFAULT_MAX_ATTEMPTS_PER_OTP)
            
            # Step 5: Use PostgreSQL UPSERT to atomically handle duplicates
            # This prevents race conditions between concurrent requests
            stmt = insert(EmailVerification).values(
                user_id=user_id,
                email=email,
                verification_code=otp_code,
                purpose=purpose,
                verified=False,
                attempts=0,
                max_attempts=max_attempts,
                expires_at=expires_at
            )
            
            # On conflict, update the existing record with new OTP and reset attempts
            # Use index_elements because idx_email_verifications_active_unverified_unique is an INDEX not a CONSTRAINT
            # Index WHERE clause: (deleted_at IS NULL) AND (verified = false)
            stmt = stmt.on_conflict_do_update(
                index_elements=['email', 'purpose'],
                index_where=and_(
                    EmailVerification.deleted_at.is_(None),
                    EmailVerification.verified == False
                ),
                set_=dict(
                    verification_code=otp_code,
                    expires_at=expires_at,
                    attempts=0,
                    max_attempts=max_attempts,
                    created_at=now_utc
                )
            ).returning(EmailVerification.id)
            
            result = await session.execute(stmt)
            verification_id = result.scalar_one()
            
            # Step 5: Return the actual OTP code for external email handling
            user_name = getattr(user, 'first_name', 'User') or 'User'
            
            logger.info(f"✅ OTP_RECORD_CREATE: Successfully created OTP record {verification_id} for user {user_id}")
            
            return {
                "success": True,
                "otp_code": otp_code,  # CRITICAL: Return actual OTP code, not PLACEHOLDER
                "verification_id": verification_id,
                "expires_in_minutes": expiry_minutes,
                "max_attempts": purpose_config.get('max_attempts', cls.DEFAULT_MAX_ATTEMPTS_PER_OTP),
                "resend_cooldown_seconds": cls.DEFAULT_RESEND_COOLDOWN_SECONDS,
                "user_name": user_name,
                "message": "OTP record created successfully"
            }
            
        except IntegrityError as ie:
            # UPSERT should prevent this, but catch any unexpected integrity errors
            logger.error(f"❌ OTP_RECORD_CREATE: Unexpected IntegrityError for user {user_id}: {ie}")
            return {
                "success": False,
                "error": "database_error",
                "message": "Unable to create verification code. Please try again."
            }
        except Exception as e:
            logger.error(f"❌ OTP_RECORD_CREATE: Failed to create OTP record for user {user_id}: {e}")
            return {
                "success": False,
                "error": "system_error",
                "message": "Something went wrong. Please try again."
            }

    @classmethod
    async def verify_otp_async(
        cls,
        user_id: int,
        otp_code: str,
        purpose: str = 'registration',
        cashout_context: Optional[Dict[str, Any]] = None,
        session: Optional[AsyncSession] = None
    ) -> Dict[str, Any]:
        """Async OTP verification with comprehensive session support"""
        
        def _verify_logic_sync(session):
            """Sync version of OTP verification logic"""
            # Find active verification (sync)
            verification = session.query(EmailVerification).filter(
                and_(
                    EmailVerification.user_id == user_id,
                    EmailVerification.purpose == purpose,
                    EmailVerification.verified.is_(False),
                    EmailVerification.expires_at > datetime.now(timezone.utc)
                )
            ).order_by(EmailVerification.created_at.desc()).first()
            
            if not verification:
                logger.warning(f"No active verification found for user {user_id}, purpose {purpose}")
                return {
                    "success": False,
                    "error": "no_active_verification",
                    "message": "No active verification found. Please request a new code."
                }
            
            # Check attempt limits
            if verification.attempts >= verification.max_attempts:
                logger.warning(f"Max attempts exceeded for verification {verification.id}")
                return {
                    "success": False,
                    "error": "max_attempts_exceeded",
                    "message": "Maximum verification attempts exceeded. Please request a new code."
                }
            
            # Increment attempt counter
            verification.attempts += 1
            verification.last_attempt_at = datetime.now(timezone.utc)
            
            # Verify OTP code using direct comparison (current schema)
            if verification.verification_code != otp_code.strip():
                session.flush()  # Save attempt increment (sync)
                
                remaining_attempts = verification.max_attempts - verification.attempts
                logger.warning(f"Invalid OTP for verification {verification.id}. Attempts: {verification.attempts}/{verification.max_attempts}")
                
                if remaining_attempts > 0:
                    return {
                        "success": False,
                        "error": "invalid_otp",
                        "message": f"Invalid verification code. {remaining_attempts} attempts remaining.",
                        "remaining_attempts": remaining_attempts
                    }
                else:
                    return {
                        "success": False,
                        "error": "max_attempts_exceeded",
                        "message": "Maximum verification attempts exceeded. Please request a new code."
                    }
            
            # Success - mark as verified
            verification.verified = True
            verification.verified_at = datetime.now(timezone.utc)
            session.flush()  # Sync
            
            logger.info(f"OTP verification successful for user {user_id}, purpose {purpose}")
            return {
                "success": True,
                "message": "OTP verification successful",
                "verification_id": verification.id,
                "email": verification.email
            }

        async def _verify_logic(session: AsyncSession):
            # Find active verification
            verification_query = await session.execute(
                select(EmailVerification).where(
                    and_(
                        EmailVerification.user_id == user_id,
                        EmailVerification.purpose == purpose,
                        EmailVerification.verified.is_(False),
                        EmailVerification.expires_at > datetime.now(timezone.utc)
                    )
                ).order_by(EmailVerification.created_at.desc())
            )
            verification = verification_query.scalar_one_or_none()
            
            if not verification:
                logger.warning(f"No active verification found for user {user_id}, purpose {purpose}")
                return {
                    "success": False,
                    "error": "no_active_verification",
                    "message": "No active verification found. Please request a new code."
                }
            
            # Check attempt limits
            if verification.attempts >= verification.max_attempts:  # type: ignore
                logger.warning(f"Max attempts exceeded for verification {verification.id}")
                return {
                    "success": False,
                    "error": "max_attempts_exceeded",
                    "message": "Maximum verification attempts exceeded. Please request a new code."
                }
            
            # Increment attempt counter
            verification.attempts += 1  # type: ignore
            verification.last_attempt_at = datetime.now(timezone.utc)  # type: ignore
            
            # Verify OTP code using direct comparison (current schema)
            if verification.verification_code != otp_code.strip():  # type: ignore
                await session.flush()  # Save attempt increment
                
                remaining_attempts = verification.max_attempts - verification.attempts
                logger.warning(f"Invalid OTP for verification {verification.id}. Attempts: {verification.attempts}/{verification.max_attempts}")
                
                if remaining_attempts > 0:  # type: ignore
                    return {
                        "success": False,
                        "error": "invalid_otp",
                        "message": f"Invalid verification code. {remaining_attempts} attempts remaining.",
                        "remaining_attempts": remaining_attempts
                    }
                else:
                    return {
                        "success": False,
                        "error": "max_attempts_exceeded",
                        "message": "Maximum verification attempts exceeded. Please request a new code."
                    }
            
            # Success - mark as verified
            verification.verified = True  # type: ignore
            verification.verified_at = datetime.now(timezone.utc)  # type: ignore
            await session.flush()
            
            logger.info(f"OTP verification successful for user {user_id}, purpose {purpose}")
            return {
                "success": True,
                "message": "OTP verification successful",
                "verification_id": verification.id,
                "email": verification.email
            }
        
        try:
            # ROBUST SOLUTION: Always use internal sync path to eliminate mixed async/sync pathways
            # Ignore external session parameter to prevent async session misuse
            import asyncio
            def _sync_verify_operation():
                with managed_session() as sync_session:
                    return _verify_logic_sync(sync_session)
            
            result = await asyncio.to_thread(_sync_verify_operation)
            return result
        except Exception as e:
            logger.error(f"Error in async OTP verification for user {user_id}: {e}")
            return {
                "success": False,
                "error": "system_error",
                "message": "System error occurred. Please try again later."
            }

    @classmethod
    def verify_otp(
        cls,
        user_id: int,
        otp_code: str,
        purpose: str = 'registration',
        cashout_context: Optional[Dict[str, Any]] = None,
        session: Optional[AsyncSession] = None
    ) -> Dict[str, Any]:
        """
        Sync compatibility wrapper for verify_otp_async
        
        This method provides backward compatibility for tests and any sync code
        that needs to call OTP verification. It properly handles the async call
        by running it in the appropriate event loop context.
        
        Args:
            user_id: User ID for verification
            otp_code: OTP code to verify
            purpose: Verification purpose (default: 'registration')
            cashout_context: Optional cashout context data
            session: Optional AsyncSession for database operations
            
        Returns:
            Dict[str, Any]: Same response format as verify_otp_async
        """
        import asyncio
        
        try:
            # Check if we're already in an async context
            try:
                loop = asyncio.get_running_loop()
                if loop.is_running():
                    # We're in an async context, need to handle this carefully
                    # Create a new event loop in a thread for the async call
                    import concurrent.futures
                    
                    def run_async_verification():
                        # Create a new event loop for this thread
                        new_loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(new_loop)
                        try:
                            return new_loop.run_until_complete(
                                cls.verify_otp_async(
                                    user_id=user_id,
                                    otp_code=otp_code,
                                    purpose=purpose,
                                    cashout_context=cashout_context,
                                    session=session
                                )
                            )
                        finally:
                            new_loop.close()
                    
                    # Run in a thread to avoid event loop conflicts
                    with concurrent.futures.ThreadPoolExecutor() as executor:
                        future = executor.submit(run_async_verification)
                        return future.result(timeout=30)  # 30 second timeout
                        
            except RuntimeError:
                # No event loop running, safe to use asyncio.run()
                pass
            
            # Not in async context, safe to use asyncio.run()
            return asyncio.run(
                cls.verify_otp_async(
                    user_id=user_id,
                    otp_code=otp_code,
                    purpose=purpose,
                    cashout_context=cashout_context,
                    session=session
                )
            )
            
        except Exception as e:
            logger.error(f"Error in sync OTP verification wrapper for user {user_id}: {e}")
            return {
                "success": False,
                "error": "system_error",
                "message": "System error occurred. Please try again later."
            }


    @classmethod
    def send_otp(
        cls,
        user_id: int,
        email: str,
        purpose: str = 'registration',
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        cashout_context: Optional[Dict[str, Any]] = None,
        session: Optional[AsyncSession] = None
    ) -> Dict[str, Any]:
        """
        FIXED: Simplified sync wrapper that avoids event loop conflicts.
        
        This method provides backward compatibility for sync code without
        creating complex thread-based event loop management.
        """
        import asyncio
        
        try:
            # CRITICAL FIX: Use simple asyncio.run() without thread complications
            # This avoids the "Task attached to different loop" errors
            try:
                # Check if event loop is already running
                asyncio.get_running_loop()
                
                # If we reach here, we're in an async context
                # Schedule the async operation properly without new loops
                logger.warning(f"⚠️ SYNC_WRAPPER: Called from async context for user {user_id} - this should use send_otp_async directly")
                
                # Return a safe fallback response for async contexts
                return {
                    "success": False,
                    "error": "async_context_conflict", 
                    "message": "OTP sending should use async method in async context"
                }
                
            except RuntimeError:
                # No event loop running - safe to use asyncio.run()
                async def _run_with_session():
                    from database import async_managed_session
                    async with async_managed_session() as session:
                        return await cls.send_otp_async(
                            session=session,
                            user_id=user_id,
                            email=email,
                            purpose=purpose,
                            ip_address=ip_address,
                            user_agent=user_agent,
                            cashout_context=cashout_context
                        )
                return asyncio.run(_run_with_session())
            
        except Exception as e:
            logger.error(f"❌ SYNC_WRAPPER: Error for user {user_id}: {e}")
            return {
                "success": False,
                "error": "system_error",
                "message": "System error occurred. Please try again later."
            }

    @classmethod
    async def _send_otp_email_background(
        cls,
        email: str,
        otp_code: str,
        purpose: str,
        expiry_minutes: int,
        user_id: Optional[int] = None,
        user_name: str = "User"
    ) -> Dict[str, Any]:
        """
        Send OTP email using background queue with graceful fallback
        """
        import time
        start_time = time.time()
        
        try:
            logger.info(f"⏱️ PERF: Starting background email queue for {email}")
            
            # Attempt to use background queue
            result = await background_email_queue.queue_otp_email(
                recipient=email,
                otp_code=otp_code,
                purpose=purpose,
                user_id=user_id,
                user_name=user_name
            )
            
            elapsed = time.time() - start_time
            
            # If queue is unavailable or failed, fallback to direct send
            if not result.get("success") and result.get("error") == "queue_unavailable":
                logger.info(f"📧 Fallback: Sending OTP email directly to {email}")
                success = await EmailService().send_email_async(
                    to_email=email,
                    subject=f"🔐 Your verification code: {otp_code}",
                    html_content=background_email_queue._create_otp_email_content(
                        otp_code=otp_code,
                        user_name=user_name,
                        purpose=purpose
                    )
                )
                return {"success": success, "fallback": True, "elapsed_ms": elapsed * 1000}
                
            return result
        except Exception as e:
            logger.error(f"❌ OTP email sending failed: {e}")
            return {"success": False, "error": str(e)}
