"""
Universal Welcome Bonus Service
Processes $3 welcome bonus for ALL users 30 minutes after onboarding completion
"""

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Dict, Any

from sqlalchemy import and_
from database import SessionLocal
from models import User
from services.crypto import CryptoServiceAtomic

logger = logging.getLogger(__name__)


class UniversalWelcomeBonusService:
    """Handles $3 universal welcome bonus for all users after 30 minutes"""
    
    BONUS_AMOUNT_USD = Decimal("3.00")
    DELAY_MINUTES = 30
    
    @classmethod
    def process_eligible_bonuses(cls) -> Dict[str, Any]:
        """
        DISABLED: Universal welcome bonus removed per user request.
        
        Process welcome bonuses for all eligible users.
        
        Eligibility criteria:
        - Onboarding completed (onboarded_at is not null)
        - Onboarding completed more than 30 minutes ago
        - Universal welcome bonus not yet given
        - User did NOT use a referral code (referred_by_id is null)
          → Users with referral codes already got $3 from the referral system
        
        Uses per-user transactions with row-level locking to prevent
        concurrent scheduler instances from double-crediting users.
        
        Returns:
            Dict with processing statistics
        """
        # DISABLED: Return early without processing
        logger.info("✅ UNIVERSAL_WELCOME_BONUS: Disabled per configuration")
        return {
            "processed": 0,
            "successful": 0,
            "failed": 0,
            "errors": []
        }
        
        stats = {
            "processed": 0,
            "successful": 0,
            "failed": 0,
            "errors": []
        }
        
        # Calculate cutoff time (30 minutes ago)
        cutoff_time = datetime.now(timezone.utc) - timedelta(minutes=cls.DELAY_MINUTES)
        
        # Process users one at a time with individual transactions
        # This ensures locks are held until each user is fully processed
        while True:
            session = SessionLocal()
            try:
                # CRITICAL: Claim ONE user at a time with row-level locking
                # Each transaction claims, processes, and commits a single user
                # This prevents concurrent workers from processing the same user
                # IMPORTANT: Exclude users who used referral codes (they got $3 from referral system)
                user = (
                    session.query(User)
                    .filter(
                        and_(
                            User.onboarded_at.isnot(None),
                            User.onboarded_at <= cutoff_time,
                            User.universal_welcome_bonus_given == False,
                            User.referred_by_id.is_(None)  # Only users WITHOUT referral codes
                        )
                    )
                    .with_for_update(skip_locked=True)  # Lock this specific row
                    .limit(1)  # Process one user at a time
                    .first()
                )
                
                # No more eligible users
                if not user:
                    break
                
                stats["processed"] += 1
                
                try:
                    # CRITICAL: Mark flag FIRST to prevent double-processing
                    user.universal_welcome_bonus_given = True
                    user.universal_welcome_bonus_given_at = datetime.now(timezone.utc)
                    session.flush()  # Write flag to DB but don't commit yet
                    
                    # Give bonus using atomic credit (shares same transaction)
                    credit_result = CryptoServiceAtomic.credit_user_wallet_atomic(
                        user_id=user.id,
                        amount=cls.BONUS_AMOUNT_USD,
                        currency="USD",
                        transaction_type="admin_adjustment",
                        description=f"🎁 Welcome bonus: ${cls.BONUS_AMOUNT_USD} USD for joining LockBay!",
                        session=session,
                    )
                    
                    if credit_result:
                        # Commit this user's transaction (flag + credit atomically)
                        session.commit()
                        
                        stats["successful"] += 1
                        logger.info(
                            f"✅ UNIVERSAL_WELCOME_BONUS: Credited ${cls.BONUS_AMOUNT_USD} to user {user.id} "
                            f"({user.username or user.first_name}) - Onboarded: {user.onboarded_at}"
                        )
                        
                        # Send notification to user (non-blocking, async)
                        cls._send_bonus_notification(user.id, str(user.telegram_id), user.first_name or "User")
                        
                    else:
                        # Credit failed, rollback flag marking too
                        session.rollback()
                        stats["failed"] += 1
                        error_msg = f"Failed to credit bonus to user {user.id}"
                        stats["errors"].append(error_msg)
                        logger.error(f"❌ UNIVERSAL_WELCOME_BONUS: {error_msg}")
                        
                except Exception as e:
                    # Any error: rollback entire transaction (flag + credit)
                    session.rollback()
                    stats["failed"] += 1
                    error_msg = f"Error processing user {user.id}: {str(e)}"
                    stats["errors"].append(error_msg)
                    logger.error(f"❌ UNIVERSAL_WELCOME_BONUS: {error_msg}", exc_info=True)
                    
            finally:
                session.close()
        
        if stats["processed"] > 0:
            logger.info(
                f"✅ UNIVERSAL_WELCOME_BONUS_COMPLETE: Processed {stats['processed']}, "
                f"Successful {stats['successful']}, Failed {stats['failed']}"
            )
        else:
            logger.info("✅ UNIVERSAL_WELCOME_BONUS: No eligible users found")
        
        return stats
    
    @classmethod
    def _send_bonus_notification(cls, user_id: int, telegram_id: str, user_name: str):
        """Send notification to user about their welcome bonus"""
        try:
            from services.consolidated_notification_service import (
                ConsolidatedNotificationService,
                NotificationRequest,
                NotificationCategory,
                NotificationPriority,
                NotificationChannel
            )
            
            message = f"🎁 Welcome bonus: ${cls.BONUS_AMOUNT_USD} USD credited!\n\nStart trading or try Quick Exchange 🚀"
            
            notification_service = ConsolidatedNotificationService()
            request = NotificationRequest(
                user_id=user_id,
                title="🎁 Welcome Bonus",
                message=message,
                category=NotificationCategory.PAYMENTS,
                priority=NotificationPriority.NORMAL,
                channels=[NotificationChannel.TELEGRAM, NotificationChannel.EMAIL]
            )
            
            # Send notification (async, non-blocking)
            import asyncio
            try:
                asyncio.create_task(notification_service.send_notification(request))
            except RuntimeError:
                # No event loop, skip notification (will be sent by background worker)
                logger.info(f"Notification queued for background processing for user {user_id}")
            
            logger.info(f"✅ UNIVERSAL_WELCOME_BONUS: Notification queued for user {user_id}")
            
        except Exception as e:
            # Don't fail the bonus credit if notification fails
            logger.error(f"❌ Failed to send welcome bonus notification to user {user_id}: {e}")
    
    @classmethod
    def get_pending_count(cls) -> int:
        """Get count of users eligible for welcome bonus (excluding referred users)"""
        session = SessionLocal()
        try:
            cutoff_time = datetime.now(timezone.utc) - timedelta(minutes=cls.DELAY_MINUTES)
            
            count = (
                session.query(User)
                .filter(
                    and_(
                        User.onboarded_at.isnot(None),
                        User.onboarded_at <= cutoff_time,
                        User.universal_welcome_bonus_given == False,
                        User.referred_by_id.is_(None)  # Exclude users with referral codes
                    )
                )
                .count()
            )
            
            return count
            
        except Exception as e:
            logger.error(f"Error getting pending bonus count: {e}")
            return 0
        finally:
            session.close()
