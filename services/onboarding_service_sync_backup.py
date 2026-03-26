"""
OnboardingService with 4-step state machine for user registration flow
Replaces complex ConversationHandler with stateless database-driven approach
"""

import logging
import hashlib
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session

from models import (
    User, OnboardingSession, OnboardingStep, EmailVerification, 
    UserStatus, Wallet
)
from database import sync_managed_session
from services.email import EmailService
from utils.helpers import validate_email
from caching.enhanced_cache import EnhancedCache
from services.onboarding_performance_monitor import track_onboarding_performance

# PERFORMANCE FIX: Initialize cache for onboarding operations
_onboarding_cache = EnhancedCache(default_ttl=600, max_size=1000)

logger = logging.getLogger(__name__)


class OnboardingService:
    """4-step onboarding state machine service for user registration"""
    
    # Step progression map for the state machine
    STEP_TRANSITIONS = {
        OnboardingStep.CAPTURE_EMAIL: OnboardingStep.VERIFY_OTP,
        OnboardingStep.VERIFY_OTP: OnboardingStep.ACCEPT_TOS,
        OnboardingStep.ACCEPT_TOS: OnboardingStep.DONE,
        OnboardingStep.DONE: None  # Terminal state
    }
    
    # Default session expiry time
    DEFAULT_SESSION_EXPIRY_HOURS = 24
    
    @classmethod
    @track_onboarding_performance("user_creation")
    def start(
        cls, 
        user_id: int, 
        invite_token: Optional[str] = None,
        user_agent: Optional[str] = None,
        ip_address: Optional[str] = None,
        referral_source: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Start or resume onboarding flow for a user
        
        Args:
            user_id: The user's ID
            invite_token: Optional invitation token
            user_agent: User's browser agent
            ip_address: User's IP address  
            referral_source: How user found the service
            
        Returns:
            Dict with session info and current step
        """
        try:
            with sync_managed_session() as session:
                # PERFORMANCE FIX: Check cache first for user onboarding status
                cache_key = f"onboarding_user_{user_id}"
                cached_result = _onboarding_cache.get(cache_key)
                if cached_result and cached_result.get('email_verified'):
                    logger.info(f"User {user_id} onboarding status from cache - already completed")
                    return {
                        "success": True,
                        "current_step": OnboardingStep.DONE.value,
                        "completed": True,
                        "session_id": None
                    }
                
                # PERFORMANCE OPTIMIZED: Combined query to get user info and onboarding session in one trip
                now = datetime.utcnow()
                
                # Single query to get both user status and active onboarding session
                from sqlalchemy import and_ as sql_and
                
                combined_query = session.query(User, OnboardingSession).outerjoin(
                    OnboardingSession,
                    sql_and(
                        OnboardingSession.user_id == User.id,
                        OnboardingSession.expires_at > now
                    )
                ).filter(User.id == user_id).first()
                
                if not combined_query or not combined_query[0]:
                    logger.error(f"User {user_id} not found for onboarding")
                    return {"success": False, "error": "User not found"}
                
                user, onboarding_session = combined_query
                email_verified = user.email_verified
                
                # PERFORMANCE: Enhanced cache with combined data
                cache_data = {
                    "email_verified": email_verified,
                    "has_session": onboarding_session is not None,
                    "current_step": onboarding_session.current_step if onboarding_session else None
                }
                _onboarding_cache.set(cache_key, cache_data, ttl=300)
                
                # Check if user has already completed onboarding
                if email_verified:
                    logger.info(f"User {user_id} already completed onboarding")
                    return {
                        "success": True,
                        "current_step": OnboardingStep.DONE.value,
                        "completed": True,
                        "session_id": None
                    }
                
                if onboarding_session:
                    # Check if session has expired
                    if onboarding_session.expires_at < now:
                        logger.info(f"Onboarding session expired for user {user_id}, creating new session")
                        session.delete(onboarding_session)
                        session.flush()
                        onboarding_session = None
                    else:
                        # Update session with new metadata if provided
                        if user_agent:
                            onboarding_session.user_agent = user_agent
                        if ip_address:
                            onboarding_session.ip_address = ip_address
                        if referral_source:
                            onboarding_session.referral_source = referral_source
                        
                        onboarding_session.updated_at = now
                        
                        logger.info(f"Resuming onboarding for user {user_id} at step {onboarding_session.current_step}")
                
                # Create new session if needed
                if not onboarding_session:
                    expires_at = now + timedelta(hours=cls.DEFAULT_SESSION_EXPIRY_HOURS)
                    
                    onboarding_session = OnboardingSession(
                        user_id=user_id,
                        current_step=OnboardingStep.CAPTURE_EMAIL.value,
                        invite_token=invite_token,
                        context_data={
                            "started_at": now.isoformat(),
                            "invite_token": invite_token
                        },
                        created_at=now,
                        updated_at=now,
                        expires_at=expires_at,
                        user_agent=user_agent,
                        ip_address=ip_address,
                        referral_source=referral_source
                    )
                    
                    session.add(onboarding_session)
                    logger.info(f"Created new onboarding session for user {user_id}")
                
                session.commit()
                
                result = {
                    "success": True,
                    "session_id": onboarding_session.id,
                    "current_step": onboarding_session.current_step,
                    "invite_token": onboarding_session.invite_token,
                    "email": onboarding_session.email,
                    "completed": False,
                    "expires_at": onboarding_session.expires_at.isoformat()
                }
                
                # PERFORMANCE FIX: Cache onboarding session for quick access
                _onboarding_cache.set(f"onboarding_session_{user_id}", result, ttl=600)
                return result
                
        except Exception as e:
            logger.error(f"Error starting onboarding for user {user_id}: {e}")
            return {"success": False, "error": str(e)}
    
    @classmethod
    def set_email(cls, user_id: int, email: str) -> Dict[str, Any]:
        """
        Handle email capture step - validates email and sends OTP
        
        Args:
            user_id: The user's ID
            email: Email address to verify
            
        Returns:
            Dict with operation result and next step info
        """
        try:
            # Validate email format
            if not validate_email(email):
                return {"success": False, "error": "Invalid email format"}
            
            with sync_managed_session() as session:
                # Get onboarding session
                onboarding_session = cls._get_active_session(session, user_id)
                if not onboarding_session:
                    return {"success": False, "error": "No active onboarding session"}
                
                # Verify we're in the correct step
                if onboarding_session.current_step != OnboardingStep.CAPTURE_EMAIL.value:
                    logger.warning(f"User {user_id} trying to set email from step {onboarding_session.current_step}")
                    return {"success": False, "error": "Invalid step for email capture"}
                
                # Check if email is already taken by another verified user
                existing_user = session.query(User).filter(
                    User.email == email,
                    User.email_verified == True,
                    User.id != user_id
                ).first()
                
                if existing_user:
                    return {"success": False, "error": "Email address is already registered"}
                
                # Update onboarding session with email
                onboarding_session.email = email
                onboarding_session.email_captured_at = datetime.utcnow()
                onboarding_session.updated_at = datetime.utcnow()
                
                # Update context data
                if not onboarding_session.context_data:
                    onboarding_session.context_data = {}
                onboarding_session.context_data["email_captured_at"] = datetime.utcnow().isoformat()
                
                session.commit()  # Commit the onboarding session updates
                
                # Use EmailVerificationService to send OTP
                from services.email_verification_service import EmailVerificationService
                
                # Extract IP and user agent if available
                ip_address = onboarding_session.ip_address
                user_agent = onboarding_session.user_agent
                
                otp_result = EmailVerificationService.send_otp(
                    user_id=user_id,
                    email=email,
                    purpose='registration',
                    ip_address=ip_address,
                    user_agent=user_agent
                )
                
                otp_sent = otp_result["success"]
                
                if otp_sent:
                    # Advance to next step
                    result = cls._advance_to_step(user_id, OnboardingStep.VERIFY_OTP)
                    # Add OTP details to the response
                    if result["success"]:
                        result["otp_expires_in_minutes"] = otp_result.get("expires_in_minutes", 15)
                        result["max_attempts"] = otp_result.get("max_attempts", 5)
                        result["resend_cooldown_seconds"] = otp_result.get("resend_cooldown_seconds", 60)
                    return result
                else:
                    return {
                        "success": False, 
                        "error": otp_result.get("error", "email_send_failed"),
                        "message": otp_result.get("message", "Failed to send verification email")
                    }
                    
        except Exception as e:
            logger.error(f"Error setting email for user {user_id}: {e}")
            return {"success": False, "error": str(e)}
    
    @classmethod
    def verify_otp(cls, user_id: int, otp_code: str) -> Dict[str, Any]:
        """
        Handle OTP verification step
        
        Args:
            user_id: The user's ID
            otp_code: OTP code to verify
            
        Returns:
            Dict with verification result and next step info
        """
        try:
            with sync_managed_session() as session:
                # Get onboarding session
                onboarding_session = cls._get_active_session(session, user_id)
                if not onboarding_session:
                    return {"success": False, "error": "No active onboarding session"}
                
                # Verify we're in the correct step
                if onboarding_session.current_step != OnboardingStep.VERIFY_OTP.value:
                    return {"success": False, "error": "Invalid step for OTP verification"}
                
                # Use EmailVerificationService to verify OTP
                from services.email_verification_service import EmailVerificationService
                
                otp_result = EmailVerificationService.verify_otp(
                    user_id=user_id,
                    otp_code=otp_code,
                    purpose='registration'
                )
                
                if not otp_result["success"]:
                    return {
                        "success": False,
                        "error": otp_result.get("error", "verification_failed"),
                        "message": otp_result.get("message", "OTP verification failed"),
                        "remaining_attempts": otp_result.get("remaining_attempts")
                    }
                
                # OTP verified successfully - update onboarding session
                onboarding_session.otp_verified_at = datetime.utcnow()
                onboarding_session.updated_at = datetime.utcnow()
                
                # Update context data
                if not onboarding_session.context_data:
                    onboarding_session.context_data = {}
                onboarding_session.context_data["otp_verified_at"] = datetime.utcnow().isoformat()
                onboarding_session.context_data["verified_email"] = otp_result.get("email")
                
                session.commit()
                
                logger.info(f"OTP verified successfully for user {user_id}")
                
                # Advance to next step
                result = cls._advance_to_step(user_id, OnboardingStep.ACCEPT_TOS)
                if result["success"]:
                    result["verified_email"] = otp_result.get("email")
                return result
                
        except Exception as e:
            logger.error(f"Error verifying OTP for user {user_id}: {e}")
            return {"success": False, "error": str(e)}
    
    @classmethod
    def accept_terms(cls, user_id: int) -> Dict[str, Any]:
        """
        Handle terms of service acceptance step
        
        Args:
            user_id: The user's ID
            
        Returns:
            Dict with acceptance result and completion info
        """
        try:
            with sync_managed_session() as session:
                # Get onboarding session
                onboarding_session = cls._get_active_session(session, user_id)
                if not onboarding_session:
                    return {"success": False, "error": "No active onboarding session"}
                
                # Verify we're in the correct step
                if onboarding_session.current_step != OnboardingStep.ACCEPT_TOS.value:
                    return {"success": False, "error": "Invalid step for terms acceptance"}
                
                # Get user record
                user = session.query(User).filter(User.id == user_id).first()
                if not user:
                    return {"success": False, "error": "User not found"}
                
                # Update onboarding session
                onboarding_session.terms_accepted_at = datetime.utcnow()
                onboarding_session.completed_at = datetime.utcnow()
                onboarding_session.updated_at = datetime.utcnow()
                
                # Update context data
                if not onboarding_session.context_data:
                    onboarding_session.context_data = {}
                onboarding_session.context_data["terms_accepted_at"] = datetime.utcnow().isoformat()
                onboarding_session.context_data["completed_at"] = datetime.utcnow().isoformat()
                
                # Complete user registration
                user.email = onboarding_session.email
                user.email_verified = True
                user.is_verified = True
                
                # SECURITY FIX: Validate user status transition before activation
                # Prevent BANNED/SUSPENDED users from being reactivated without admin authorization
                current_user_status = user.status
                if current_user_status in [UserStatus.BANNED.value, UserStatus.SUSPENDED.value]:
                    logger.error(
                        f"🚫 USER_STATUS_BLOCKED: Cannot transition {current_user_status}→ACTIVE for user {user_id} "
                        f"without admin authorization (onboarding bypass attempt)"
                    )
                    return {
                        "success": False, 
                        "error": f"Account is {current_user_status}. Please contact support for assistance."
                    }
                
                user.status = UserStatus.ACTIVE.value
                user.verified_at = datetime.utcnow()
                
                # Create default USD wallet if it doesn't exist
                wallet = session.query(Wallet).filter(
                    Wallet.user_id == user_id,
                    Wallet.currency == "USD"
                ).first()
                
                if not wallet:
                    wallet = Wallet(
                        user_id=user_id,
                        currency="USD",
                        balance=0.0,
                        frozen_balance=0.0,
                        created_at=datetime.utcnow()
                    )
                    session.add(wallet)
                    logger.info(f"Created default USD wallet for user {user_id}")
                
                # CRITICAL FIX: Set step to DONE in same session to avoid race condition
                onboarding_session.current_step = OnboardingStep.DONE.value
                
                session.commit()
                
                # CRITICAL FIX: Invalidate cache when completing onboarding
                cache_key = f"onboarding_step_{user_id}"
                _onboarding_cache.delete(cache_key)
                logger.info(f"Cache invalidated for user {user_id} completing onboarding")
                
                logger.info(f"Onboarding completed successfully for user {user_id}")
                
                return {
                    "success": True,
                    "current_step": OnboardingStep.DONE.value,
                    "completed": True,
                    "session_id": onboarding_session.id
                }
                
        except Exception as e:
            logger.error(f"Error accepting terms for user {user_id}: {e}")
            return {"success": False, "error": str(e)}
    
    @classmethod
    def get_current_step(cls, user_id: int) -> Optional[str]:
        """
        Get the current onboarding step for a user with caching optimization
        
        Args:
            user_id: The user's ID
            
        Returns:
            Current step name or None if no active session
        """
        try:
            # TEMPORARY DEBUG: Disable cache to troubleshoot test issue
            cache_key = f"onboarding_step_{user_id}"
            # cached_step = _onboarding_cache.get(cache_key)
            # if cached_step:
            #     return cached_step
            logger.info(f"DEBUGGING: Getting current step for user {user_id}")
            
            with sync_managed_session() as session:
                # CRITICAL FIX: Simplified query - get active onboarding session first
                onboarding_session = session.query(OnboardingSession).filter(
                    OnboardingSession.user_id == user_id,
                    OnboardingSession.expires_at > datetime.utcnow()
                ).first()
                
                if onboarding_session:
                    current_step = onboarding_session.current_step
                    # Cache for 5 minutes
                    _onboarding_cache.set(cache_key, current_step, ttl=300)
                    logger.debug(f"Found active onboarding session for user {user_id}: step={current_step}")
                    return current_step
                
                # Check if user completed onboarding (no active session but verified)
                user_verified = session.query(User.email_verified).filter(User.id == user_id).scalar()
                if user_verified:
                    step = OnboardingStep.DONE.value
                    # Cache completed status for longer (15 minutes)
                    _onboarding_cache.set(cache_key, step, ttl=900)
                    logger.debug(f"User {user_id} completed onboarding (email_verified=True)")
                    return step
                    
                # Cache null result for shorter time (1 minute) to avoid repeated queries
                _onboarding_cache.set(cache_key, None, ttl=60)
                logger.debug(f"No active onboarding session found for user {user_id}")
                return None
                
        except Exception as e:
            logger.error(f"Error getting current step for user {user_id}: {e}")
            return None
    
    @classmethod
    def reset_to_step(cls, user_id: int, step: str) -> Dict[str, Any]:
        """
        Reset user onboarding to a specific step for recovery
        
        Args:
            user_id: The user's ID
            step: Step to reset to (OnboardingStep value)
            
        Returns:
            Dict with reset result
        """
        try:
            # Validate step
            valid_steps = [s.value for s in OnboardingStep]
            if step not in valid_steps:
                return {"success": False, "error": "Invalid step"}
            
            with sync_managed_session() as session:
                onboarding_session = cls._get_active_session(session, user_id)
                if not onboarding_session:
                    return {"success": False, "error": "No active onboarding session"}
                
                # Update current step
                onboarding_session.current_step = step
                onboarding_session.updated_at = datetime.utcnow()
                onboarding_session.retry_count += 1
                
                # Update context data
                if not onboarding_session.context_data:
                    onboarding_session.context_data = {}
                onboarding_session.context_data["reset_to_step"] = step
                onboarding_session.context_data["reset_at"] = datetime.utcnow().isoformat()
                
                session.commit()
                
                logger.info(f"Reset user {user_id} onboarding to step {step}")
                
                return {
                    "success": True,
                    "current_step": step,
                    "session_id": onboarding_session.id
                }
                
        except Exception as e:
            logger.error(f"Error resetting step for user {user_id}: {e}")
            return {"success": False, "error": str(e)}
    
    @classmethod
    def cancel_session(cls, user_id: int) -> Dict[str, Any]:
        """
        Cancel onboarding session for a user
        
        Args:
            user_id: The user's ID
            
        Returns:
            Dict with cancellation result
        """
        try:
            with sync_managed_session() as session:
                onboarding_session = cls._get_active_session(session, user_id)
                if not onboarding_session:
                    logger.info(f"No active onboarding session found for user {user_id}")
                    return {"success": True, "message": "No active session to cancel"}
                
                # Delete the onboarding session
                session.delete(onboarding_session)
                session.commit()
                
                logger.info(f"Canceled onboarding session for user {user_id}")
                
                return {
                    "success": True,
                    "message": "Onboarding session canceled successfully"
                }
                
        except Exception as e:
            logger.error(f"Error canceling onboarding session for user {user_id}: {e}")
            return {"success": False, "error": str(e)}
    
    @classmethod
    def get_session_info(cls, user_id: int) -> Optional[Dict[str, Any]]:
        """
        Get complete onboarding session information
        
        Args:
            user_id: The user's ID
            
        Returns:
            Dict with session info or None if no session
        """
        try:
            with sync_managed_session() as session:
                onboarding_session = cls._get_active_session(session, user_id)
                if not onboarding_session:
                    return None
                
                return {
                    "session_id": onboarding_session.id,
                    "user_id": onboarding_session.user_id,
                    "current_step": onboarding_session.current_step,
                    "email": onboarding_session.email,
                    "invite_token": onboarding_session.invite_token,
                    "context_data": onboarding_session.context_data,
                    "email_captured_at": onboarding_session.email_captured_at.isoformat() if onboarding_session.email_captured_at else None,
                    "otp_verified_at": onboarding_session.otp_verified_at.isoformat() if onboarding_session.otp_verified_at else None,
                    "terms_accepted_at": onboarding_session.terms_accepted_at.isoformat() if onboarding_session.terms_accepted_at else None,
                    "completed_at": onboarding_session.completed_at.isoformat() if onboarding_session.completed_at else None,
                    "created_at": onboarding_session.created_at.isoformat(),
                    "updated_at": onboarding_session.updated_at.isoformat(),
                    "expires_at": onboarding_session.expires_at.isoformat(),
                    "retry_count": onboarding_session.retry_count
                }
                
        except Exception as e:
            logger.error(f"Error getting session info for user {user_id}: {e}")
            return None
    
    @classmethod
    def resend_otp(cls, user_id: int) -> Dict[str, Any]:
        """
        Resend OTP email for verification step
        
        Args:
            user_id: The user's ID
            
        Returns:
            Dict with resend result
        """
        try:
            with sync_managed_session() as session:
                # Get onboarding session
                onboarding_session = cls._get_active_session(session, user_id)
                if not onboarding_session:
                    return {"success": False, "error": "No active onboarding session"}
                
                # Must be in OTP verification step
                if onboarding_session.current_step != OnboardingStep.VERIFY_OTP.value:
                    return {"success": False, "error": "Can only resend OTP during verification step"}
                
                if not onboarding_session.email:
                    return {"success": False, "error": "No email address found in session"}
                
                # Get or create new email verification
                verification = session.query(EmailVerification).filter(
                    EmailVerification.user_id == user_id,
                    EmailVerification.purpose == "registration"
                ).first()
                
                # SECURITY FIX: Generate OTP and hash for secure storage
                otp_code = cls._generate_otp()
                otp_hash = cls._hash_otp(otp_code)
                
                if verification:
                    # Reset verification with new OTP - Store hash, not plain text
                    verification.verification_code = otp_hash  # Store hash for consistency
                    verification.otp_hash = otp_hash  # Store hash for verification
                    verification.verified = False
                    verification.attempts = 0
                    verification.created_at = datetime.utcnow()
                    verification.expires_at = datetime.utcnow() + timedelta(minutes=10)
                else:
                    # Create new verification - Store hash, not plain text
                    verification = EmailVerification(
                        user_id=user_id,
                        email=onboarding_session.email,
                        verification_code=otp_hash,  # Store hash for consistency
                        otp_hash=otp_hash,  # Store hash for verification
                        purpose="registration",
                        verified=False,
                        attempts=0,
                        max_attempts=5,
                        created_at=datetime.utcnow(),
                        expires_at=datetime.utcnow() + timedelta(minutes=10)
                    )
                    session.add(verification)
                
                session.commit()
                
                # Send new OTP email (use plain text OTP for display, hash is stored securely)
                otp_sent = cls._send_otp_email(onboarding_session.email, otp_code)
                
                if otp_sent:
                    logger.info(f"OTP resent successfully for user {user_id}")
                    return {"success": True, "message": "OTP sent successfully"}
                else:
                    return {"success": False, "error": "Failed to send verification email"}
                    
        except Exception as e:
            logger.error(f"Error resending OTP for user {user_id}: {e}")
            return {"success": False, "error": str(e)}
    
    # Private helper methods
    
    @classmethod
    def _get_active_session(cls, session: Session, user_id: int) -> Optional[OnboardingSession]:
        """Get active (non-expired) onboarding session for user"""
        return session.query(OnboardingSession).filter(
            OnboardingSession.user_id == user_id,
            OnboardingSession.expires_at > datetime.utcnow()
        ).first()
    
    @classmethod
    def _advance_to_step(cls, user_id: int, next_step: OnboardingStep) -> Dict[str, Any]:
        """Advance onboarding session to next step"""
        try:
            with sync_managed_session() as session:
                onboarding_session = cls._get_active_session(session, user_id)
                if not onboarding_session:
                    return {"success": False, "error": "No active onboarding session"}
                
                onboarding_session.current_step = next_step.value
                onboarding_session.updated_at = datetime.utcnow()
                
                session.commit()
                
                # CRITICAL FIX: Invalidate cache when step advances
                cache_key = f"onboarding_step_{user_id}"
                _onboarding_cache.delete(cache_key)
                logger.info(f"Cache invalidated for user {user_id} advancing to step {next_step.value}")
                
                return {
                    "success": True,
                    "current_step": next_step.value,
                    "session_id": onboarding_session.id,
                    "completed": next_step == OnboardingStep.DONE
                }
                
        except Exception as e:
            logger.error(f"Error advancing to step {next_step.value} for user {user_id}: {e}")
            return {"success": False, "error": str(e)}
    
    @classmethod
    def _generate_otp(cls) -> str:
        """Generate 6-digit OTP code"""
        import secrets
        return ''.join(secrets.choice('0123456789') for _ in range(6))
    
    @classmethod
    def _hash_otp(cls, otp: str) -> str:
        """Create SHA256 hash of OTP for secure storage (consistent with EmailVerificationService)"""
        return hashlib.sha256(otp.encode()).hexdigest()
    
    @classmethod
    def _send_otp_email(cls, email: str, otp_code: str) -> bool:
        """Send OTP verification email"""
        try:
            email_service = EmailService()
            
            subject = "Verify Your Email - LockBay"
            
            html_content = f"""
            <html>
            <body style="font-family: Arial, sans-serif; margin: 0; padding: 20px;">
                <div style="max-width: 600px; margin: 0 auto; background-color: #f9f9f9; padding: 30px; border-radius: 10px;">
                    <h2 style="color: #333; text-align: center;">Email Verification</h2>
                    <p style="color: #666; font-size: 16px;">
                        Welcome to LockBay! Please use the following verification code to complete your registration:
                    </p>
                    <div style="background-color: #fff; padding: 20px; border-radius: 5px; text-align: center; margin: 20px 0;">
                        <h1 style="color: #007bff; font-size: 32px; margin: 0; letter-spacing: 5px;">{otp_code}</h1>
                    </div>
                    <p style="color: #666; font-size: 14px;">
                        This code will expire in 10 minutes. If you didn't request this verification, please ignore this email.
                    </p>
                    <p style="color: #666; font-size: 14px;">
                        Best regards,<br>
                        The LockBay Team
                    </p>
                </div>
            </body>
            </html>
            """
            
            text_content = f"""
            Welcome to LockBay!
            
            Your email verification code is: {otp_code}
            
            This code will expire in 10 minutes.
            
            If you didn't request this verification, please ignore this email.
            
            Best regards,
            The LockBay Team
            """
            
            return email_service.send_email(
                to_email=email,
                subject=subject,
                html_content=html_content,
                text_content=text_content
            )
            
        except Exception as e:
            logger.error(f"Error sending OTP email to {email}: {e}")
            return False