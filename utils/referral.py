"""Referral system utilities"""

import os
import logging
from typing import Optional, Dict, Any, List

from models import User
from database import SessionLocal
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from config import Config
from utils.decimal_precision import MonetaryDecimal

logger = logging.getLogger(__name__)


class ReferralSystem:
    """Handles all referral-related operations"""

    # Referral rewards configuration (read from environment with fallbacks) - CRITICAL: Using Decimal for financial precision
    REFERRER_REWARD_USD = MonetaryDecimal.to_decimal(os.getenv("REFERRER_REWARD_USD", "5.0"), "referrer_reward")  # Reward for referrer when referee is active
    REFEREE_REWARD_USD = MonetaryDecimal.to_decimal(os.getenv("REFEREE_REWARD_USD", "5.0"), "referee_reward")  # Welcome bonus for new referred user
    MIN_ACTIVITY_FOR_REWARD = MonetaryDecimal.to_decimal(os.getenv("MIN_ACTIVITY_FOR_REWARD", "100.0"), "min_activity")  # Minimum trading volume to qualify

    @classmethod
    def generate_referral_code(cls, user_id: int, max_attempts: int = 5) -> str:
        """Generate a unique referral code for a user"""
        session = SessionLocal()
        try:
            from sqlalchemy import func
            
            for attempt in range(max_attempts):
                # Generate cryptographically secure 6-character alphanumeric code
                from utils.secure_crypto import SecureCrypto
                code = SecureCrypto.generate_random_id(length=12)[:6].upper()

                # Check if code already exists (case-insensitive)
                existing = (
                    session.query(User)
                    .filter(func.upper(User.referral_code) == code.upper())
                    .first()
                )
                if not existing:
                    return code

            # If all attempts failed, use longer code
            from utils.secure_crypto import SecureCrypto
            return SecureCrypto.generate_random_id(length=16)[:8].upper()

        except Exception as e:
            logger.error(f"Error generating referral code: {e}")
            from utils.secure_crypto import SecureCrypto
            return SecureCrypto.generate_random_id(length=12)[:6].upper()
        finally:
            session.close()

    @classmethod
    def ensure_user_has_referral_code(cls, user: User, session) -> str:
        """Ensure user has a referral code, generate if missing"""
        if not getattr(user, 'referral_code', None):
            setattr(user, 'referral_code', cls.generate_referral_code(getattr(user, 'id')))
            session.commit()
            logger.info(
                f"Generated referral code {user.referral_code} for user {user.id}"
            )
        return getattr(user, 'referral_code')

    @classmethod
    def get_referral_link(cls, referral_code: str) -> str:
        """Generate a Telegram bot deep link for referral sharing"""
        # Use Telegram bot deep link for direct invitation
        return f"https://t.me/{Config.BOT_USERNAME}?start=ref_{referral_code}"

    @classmethod
    async def _check_pending_escrows_from_buyer(
        cls, new_user: User, buyer_id: int, session: AsyncSession
    ) -> bool:
        """Check if new user has pending escrows where the given buyer_id is the buyer"""
        try:
            from models import Escrow
            from sqlalchemy import and_, or_, func
            
            # Build matching conditions for all contact types
            matching_conditions = []
            
            # Match by username (case-insensitive)
            if new_user.username:
                matching_conditions.append(
                    and_(
                        Escrow.seller_contact_type == 'username',
                        func.lower(Escrow.seller_contact_value) == func.lower(new_user.username)
                    )
                )
            
            # Match by phone number
            if new_user.phone_number:
                matching_conditions.append(
                    and_(
                        Escrow.seller_contact_type == 'phone',
                        Escrow.seller_contact_value == new_user.phone_number
                    )
                )
            
            # Match by email (case-insensitive)
            if new_user.email and new_user.email != f"temp_{new_user.telegram_id}@onboarding.temp":
                matching_conditions.append(
                    and_(
                        Escrow.seller_contact_type == 'email',
                        func.lower(Escrow.seller_contact_value) == func.lower(new_user.email)
                    )
                )
            
            # Match by telegram_id
            matching_conditions.append(
                and_(
                    Escrow.seller_contact_type == 'telegram_id',
                    Escrow.seller_contact_value == str(new_user.telegram_id)
                )
            )
            
            # Find escrows matching ANY contact type AND from the specific buyer
            if matching_conditions:
                from sqlalchemy import and_ as sql_and
                result = await session.execute(
                    select(Escrow).filter(
                        sql_and(
                            Escrow.seller_id.is_(None),
                            Escrow.buyer_id == buyer_id,
                            or_(*matching_conditions)
                        )
                    )
                )
                pending_escrows = result.scalars().all()
                return len(pending_escrows) > 0
            
            return False
            
        except Exception as e:
            logger.error(f"Error checking pending escrows for user {new_user.id}: {e}")
            return False
    
    @classmethod
    async def process_referral_signup(
        cls, new_user: User, referral_code: str, session: AsyncSession
    ) -> Dict[str, Any]:
        """Process a new user signup with referral code"""
        try:
            # Find referrer (case-insensitive lookup)
            result = await session.execute(
                select(User).filter(func.upper(User.referral_code) == referral_code.upper())
            )
            referrer = result.scalar_one_or_none()
            
            if not referrer:
                return {"success": False, "error": "Invalid referral code"}

            if referrer.id == new_user.id:
                return {"success": False, "error": "Cannot refer yourself"}

            # Set referral relationship
            new_user.referred_by_id = referrer.id

            # Give welcome bonus to referred user
            bonus_success = await cls._give_welcome_bonus(new_user, session)
            
            # CRITICAL: Fail the entire referral if bonus credit fails
            if not bonus_success:
                await session.rollback()
                logger.error(f"Failed to credit welcome bonus for user {new_user.id}, rolling back referral")
                return {
                    "success": False,
                    "error": "Failed to credit welcome bonus. Please try again or contact support."
                }

            await session.commit()

            logger.info(
                f"User {new_user.id} signed up with referral code {referral_code} from user {referrer.id}"
            )

            # Check if new user has pending escrows from referrer
            # If yes, skip "New Referral" notification (consolidated notification will be sent later)
            has_pending_escrows = await cls._check_pending_escrows_from_buyer(
                new_user, referrer.id, session
            )
            
            if has_pending_escrows:
                logger.info(f"🎯 User {new_user.id} has pending escrows from referrer {referrer.id} - skipping 'New Referral' notification (consolidated will be sent)")
            else:
                # PERFORMANCE OPTIMIZATION: Send notification to referrer in background to speed up TOS acceptance
                # This reduces TOS completion time from 3.8s to ~2.6s by not blocking on external API calls
                new_user_name = str(new_user.first_name or new_user.username or "A new user")
                from utils.background_task_runner import run_background_task
                await run_background_task(cls._send_new_referral_notification(referrer.id, new_user_name))
                logger.info(f"📤 BACKGROUND_NOTIFICATION: Queued referrer notification for user {referrer.id} (non-blocking)")

            return {
                "success": True,
                "referrer": referrer,
                "referrer_id": referrer.id,  # Added for consolidated notification
                "welcome_bonus": cls.REFEREE_REWARD_USD,
                "message": f"Welcome! You were referred by {referrer.first_name or referrer.username}!",
            }

        except Exception as e:
            logger.error(f"Error processing referral signup: {e}")
            await session.rollback()
            return {"success": False, "error": str(e)}

    @classmethod
    async def _give_welcome_bonus(cls, user: User, session: AsyncSession, referral_reference: Optional[str] = None):
        """Give welcome bonus to new referred user with idempotency"""
        try:
            from services.crypto import CryptoServiceAtomic
            
            # Generate unique referral reference if not provided
            if not referral_reference:
                from datetime import datetime
                referral_reference = f"REF_WELCOME_{user.id}_{int(datetime.utcnow().timestamp())}"
            
            # Check if welcome bonus already given
            if await cls._is_welcome_bonus_processed_async(user.id, referral_reference, session):
                logger.info(f"Welcome bonus already processed for user {user.id}, reference: {referral_reference}")
                return True

            # Credit user's wallet with welcome bonus as TRADING CREDIT (non-withdrawable)
            credit_result = await CryptoServiceAtomic.credit_trading_credit_atomic(
                user_id=getattr(user, 'id'),
                amount=cls.REFEREE_REWARD_USD,
                currency="USD",
                transaction_type="referral_welcome_bonus",
                description=f"🎁 Welcome Bonus: ${cls.REFEREE_REWARD_USD} Trading Credit (use for escrow/exchange, not withdrawable) - Ref: {referral_reference}",
                session=session,
            )

            if credit_result:
                # Record welcome bonus to prevent duplicates
                cls._record_welcome_bonus(user.id, referral_reference)
                logger.info(f"✅ Welcome bonus (trading credit) credited to user {user.id}: ${cls.REFEREE_REWARD_USD}")
                logger.info("📧 NOTIFICATION: Trading credit welcome will be sent via consolidated welcome notification in onboarding service")
                
                return True
            else:
                logger.error(f"Failed to credit welcome bonus to user {user.id}")
                return False

        except Exception as e:
            logger.error(f"Error giving welcome bonus: {e}")
            return False

    @classmethod
    def check_and_reward_referrer(cls, user: User, session, referrer_reward_reference: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Check if user activity qualifies referrer for reward with idempotency"""
        try:
            if not getattr(user, 'referred_by_id', None):
                return None

            # Generate unique referrer reward reference if not provided
            if not referrer_reward_reference:
                from datetime import datetime
                referrer_reward_reference = f"REF_REWARD_{user.referred_by_id}_{user.id}_{int(datetime.utcnow().timestamp())}"

            # Check total trading volume
            total_volume = cls._get_user_trading_volume(getattr(user, 'id'), session)

            if total_volume < cls.MIN_ACTIVITY_FOR_REWARD:
                return None

            # Check if referrer already received reward for this user (enhanced check)
            referrer_id = user.referred_by_id
            if referrer_id and cls._is_referrer_reward_processed(referrer_id, user.id, referrer_reward_reference):
                logger.info(f"Referrer reward already processed for referrer {referrer_id}, referee {user.id}")
                return None

            # Give reward to referrer
            referrer = session.query(User).filter(User.id == user.referred_by_id).first()
            if not referrer:
                return None

            from services.crypto import CryptoServiceAtomic

            credit_result = CryptoServiceAtomic.credit_user_wallet_atomic(
                user_id=referrer.id,
                amount=cls.REFERRER_REWARD_USD,
                currency="USD",
                transaction_type="admin_adjustment",
                description=f"Referral reward: ${cls.REFERRER_REWARD_USD} for referring active user {user.id} - Ref: {referrer_reward_reference}",
                session=session,
            )

            if credit_result:
                # Record referrer reward to prevent duplicates
                referrer_id = user.referred_by_id
                if referrer_id:
                    cls._record_referrer_reward(referrer_id, user.id, referrer_reward_reference)
                
                logger.info(
                    f"Rewarded referrer {referrer.id} ${cls.REFERRER_REWARD_USD} for user {user.id}, ref: {referrer_reward_reference}"
                )
                
                # Queue notification to referrer about earning the reward
                active_user_name = str(user.first_name or user.username or "Your referral")
                cls._send_reward_earned_notification(
                    referrer.id, 
                    active_user_name, 
                    float(cls.REFERRER_REWARD_USD)
                )
                
                return {
                    "referrer": referrer,
                    "reward_amount": cls.REFERRER_REWARD_USD,
                    "active_user": user,
                    "referrer_reward_reference": referrer_reward_reference,
                }

        except Exception as e:
            logger.error(f"Error checking referrer reward: {e}")

        return None

    @classmethod
    def _get_user_trading_volume(cls, user_id: int, session) -> float:
        """Get total trading volume for user"""
        try:
            from sqlalchemy import func
            from models import Escrow

            # Sum completed escrow amounts where user was buyer or seller
            result = (
                session.query(func.sum(Escrow.amount))
                .filter(
                    ((Escrow.buyer_id == user_id) | (Escrow.seller_id == user_id)),
                    Escrow.status.in_(["completed", "released"]),
                )
                .scalar()
            )

            return float(result) if result else 0.0

        except Exception as e:
            logger.error(f"Error getting trading volume: {e}")
            return 0.0

    @classmethod
    def get_referral_stats(cls, user: User, session) -> Dict[str, Any]:
        """Get comprehensive referral statistics for user"""
        try:
            # Ensure user has referral code
            referral_code = cls.ensure_user_has_referral_code(user, session)

            # Get referrals made by this user
            referrals = session.query(User).filter(User.referred_by_id == user.id).all()

            # Count active referrals (those who completed onboarding)
            active_referrals = []
            qualified_for_reward = []
            total_earned = 0.0

            for referral in referrals:
                # Check if onboarding completed
                is_onboarded = getattr(referral, 'onboarding_completed', False)
                
                # Check trading volume
                volume = cls._get_user_trading_volume(referral.id, session)
                
                # Active = completed onboarding
                if is_onboarded:
                    active_referrals.append(
                        {
                            "user": referral,
                            "volume": volume,
                            "joined_date": referral.created_at,
                        }
                    )
                
                # Qualified = traded enough to earn reward
                if volume >= cls.MIN_ACTIVITY_FOR_REWARD:
                    qualified_for_reward.append(referral)
                    total_earned += float(cls.REFERRER_REWARD_USD)

            # Get referral link
            referral_link = cls.get_referral_link(referral_code)

            return {
                "referral_code": referral_code,
                "referral_link": referral_link,
                "total_referrals": len(referrals),
                "active_referrals": len(active_referrals),
                "pending_referrals": len(referrals) - len(active_referrals),
                "qualified_for_reward": len(qualified_for_reward),
                "total_earned": total_earned,
                "referral_details": active_referrals,
                "reward_per_referral": cls.REFERRER_REWARD_USD,
                "min_activity_required": cls.MIN_ACTIVITY_FOR_REWARD,
            }

        except Exception as e:
            logger.error(f"Error getting referral stats: {e}")
            return {
                "referral_code": getattr(user, 'referral_code', None) or "ERROR",
                "total_referrals": 0,
                "active_referrals": 0,
                "total_earned": 0.0,
            }

    @classmethod
    def get_referral_leaderboard(
        cls, limit: int = 10, session=None
    ) -> List[Dict[str, Any]]:
        """Get top referrers leaderboard"""
        close_session = False
        if not session:
            session = SessionLocal()
            close_session = True

        try:

            # Find all unique referrer IDs (users who have referred others)
            referrer_ids_query = (
                session.query(User.referred_by_id)
                .filter(User.referred_by_id.isnot(None))
                .distinct()
            )
            
            referrer_ids = [row[0] for row in referrer_ids_query.all()]
            
            if not referrer_ids:
                return []
            
            # Get stats for each referrer and build leaderboard
            leaderboard_data = []
            for referrer_id in referrer_ids:
                referrer = session.query(User).filter(User.id == referrer_id).first()
                if referrer:
                    stats = cls.get_referral_stats(referrer, session)
                    
                    # Only include referrers with active referrals
                    if stats["active_referrals"] > 0:
                        leaderboard_data.append({
                            "user": referrer,
                            "display_name": getattr(referrer, 'first_name', None)
                            or getattr(referrer, 'username', None)
                            or f"User {getattr(referrer, 'id', 'unknown')}",
                            "total_referrals": stats["total_referrals"],
                            "active_referrals": stats["active_referrals"],
                            "total_earned": stats["total_earned"],
                        })
            
            # Sort by active referrals (descending), then by total earned
            leaderboard_data.sort(
                key=lambda x: (x["active_referrals"], x["total_earned"]),
                reverse=True
            )
            
            # Add ranks and limit results
            leaderboard = []
            for idx, entry in enumerate(leaderboard_data[:limit]):
                entry["rank"] = idx + 1
                leaderboard.append(entry)
            
            return leaderboard

        except Exception as e:
            logger.error(f"Error getting referral leaderboard: {e}")
            return []
        finally:
            if close_session:
                session.close()

    @classmethod
    async def _is_welcome_bonus_processed_async(cls, user_id: int, referral_reference: str, session: AsyncSession) -> bool:
        """Check if welcome bonus already processed for this reference (async version)"""
        try:
            from models import Transaction
            from sqlalchemy import select
            
            # Check for existing welcome bonus transaction with this reference
            result = await session.execute(
                select(Transaction).filter(
                    Transaction.user_id == user_id,
                    Transaction.transaction_type == "referral_welcome_bonus",
                    Transaction.description.contains(referral_reference)
                )
            )
            existing_transaction = result.scalar_one_or_none()
            
            # Additional check for recent identical welcome bonuses
            if not existing_transaction:
                from datetime import datetime, timedelta
                recent_cutoff = datetime.utcnow() - timedelta(minutes=30)
                
                result = await session.execute(
                    select(Transaction).filter(
                        Transaction.user_id == user_id,
                        Transaction.transaction_type == "referral_welcome_bonus",
                        Transaction.description.contains(f"Welcome Bonus: ${cls.REFEREE_REWARD_USD}"),
                        Transaction.created_at >= recent_cutoff
                    )
                )
                recent_identical = result.scalar_one_or_none()
                
                if recent_identical:
                    logger.warning(f"Found recent identical welcome bonus for user {user_id}")
                    return True
            
            return existing_transaction is not None
            
        except Exception as e:
            logger.error(f"Error checking welcome bonus status: {e}")
            return False

    @classmethod
    def _is_welcome_bonus_processed(cls, user_id: int, referral_reference: str) -> bool:
        """Check if welcome bonus already processed for this reference"""
        try:
            from database import SessionLocal
            from models import Transaction
            
            session = SessionLocal()
            
            # Check for existing welcome bonus transaction with this reference
            existing_transaction = session.query(Transaction).filter(
                Transaction.user_id == user_id,
                Transaction.transaction_type == "referral_welcome_bonus",
                Transaction.description.contains(referral_reference)
            ).first()
            
            # Additional check for recent identical welcome bonuses
            if not existing_transaction:
                from datetime import datetime, timedelta
                recent_cutoff = datetime.utcnow() - timedelta(minutes=30)
                
                recent_identical = session.query(Transaction).filter(
                    Transaction.user_id == user_id,
                    Transaction.transaction_type == "referral_welcome_bonus",
                    Transaction.description.contains(f"Welcome Bonus: ${cls.REFEREE_REWARD_USD}"),
                    Transaction.created_at >= recent_cutoff
                ).first()
                
                if recent_identical:
                    logger.warning(f"Found recent identical welcome bonus for user {user_id}")
                    return True
            
            session.close()
            return existing_transaction is not None
            
        except Exception as e:
            logger.error(f"Error checking welcome bonus status: {e}")
            return False

    @classmethod
    def _record_welcome_bonus(cls, user_id: int, referral_reference: str):
        """Record welcome bonus processing to prevent future duplicates"""
        try:
            logger.info(f"Welcome bonus recorded: User {user_id}, Ref: {referral_reference}")
        except Exception as e:
            logger.error(f"Error recording welcome bonus: {e}")

    @classmethod
    def _is_referrer_reward_processed(cls, referrer_id: int, referee_id: int, referrer_reward_reference: str) -> bool:
        """Check if referrer reward already processed"""
        try:
            from database import SessionLocal
            from models import Transaction
            
            session = SessionLocal()
            
            # Check for existing referrer reward transaction with this reference
            existing_transaction = session.query(Transaction).filter(
                Transaction.user_id == referrer_id,
                Transaction.transaction_type == "admin_adjustment",
                Transaction.description.contains(referrer_reward_reference)
            ).first()
            
            # Additional check for existing referral reward for this referee
            if not existing_transaction:
                existing_for_referee = session.query(Transaction).filter(
                    Transaction.user_id == referrer_id,
                    Transaction.transaction_type == "admin_adjustment",
                    Transaction.description.contains("Referral reward:")
                ).filter(
                    Transaction.description.contains(f"user {referee_id}")
                ).first()
                
                if existing_for_referee:
                    logger.warning(f"Found existing referrer reward for referrer {referrer_id}, referee {referee_id}")
                    return True
            
            session.close()
            return existing_transaction is not None
            
        except Exception as e:
            logger.error(f"Error checking referrer reward status: {e}")
            return False

    @classmethod
    def _record_referrer_reward(cls, referrer_id: int, referee_id: int, referrer_reward_reference: str):
        """Record referrer reward processing to prevent future duplicates"""
        try:
            logger.info(f"Referrer reward recorded: Referrer {referrer_id}, Referee {referee_id}, Ref: {referrer_reward_reference}")
        except Exception as e:
            logger.error(f"Error recording referrer reward: {e}")

    @classmethod
    def _queue_notification_for_background_processing(
        cls, 
        user_id: int, 
        title: str, 
        message: str,
        channels: Optional[list] = None,
        broadcast_mode: bool = False
    ):
        """Queue notification in database for background processing with broadcast support"""
        try:
            from database import SessionLocal
            from models import NotificationQueue, User
            from datetime import datetime
            
            if channels is None:
                channels = ["telegram", "email"]
            
            session = SessionLocal()
            try:
                # Fetch user to get actual contact information
                user = session.query(User).filter(User.id == user_id).first()
                if not user:
                    logger.error(f"User {user_id} not found, cannot queue notification")
                    return
                
                # If broadcast_mode=True, store broadcast_mode flag in template_data
                # This will be used when notification is reconstructed for processing
                template_data = {"broadcast_mode": broadcast_mode} if broadcast_mode else None
                
                # Queue notification for each available channel
                queued_count = 0
                for channel in channels:
                    recipient = None
                    
                    if channel == "telegram":
                        # Use actual telegram_id as recipient
                        if user.telegram_id:
                            recipient = str(user.telegram_id)
                        else:
                            logger.warning(f"User {user_id} has no telegram_id, skipping telegram notification")
                            continue
                            
                    elif channel == "email":
                        # Use actual email address as recipient
                        if user.email and user.email_verified:
                            recipient = user.email
                        else:
                            logger.warning(f"User {user_id} has no verified email, skipping email notification")
                            continue
                    
                    if recipient:
                        notification = NotificationQueue(
                            user_id=user_id,
                            channel=channel,
                            recipient=recipient,
                            subject=title,
                            content=message,
                            status='pending',
                            priority=2,  # Normal priority
                            scheduled_at=datetime.utcnow(),
                            template_data=template_data  # Store broadcast_mode for processing
                        )
                        session.add(notification)
                        queued_count += 1
                
                session.commit()
                mode_str = " (broadcast_mode=True)" if broadcast_mode else ""
                logger.info(f"✅ Queued {queued_count} notifications for user {user_id}{mode_str}")
                
            except Exception as e:
                session.rollback()
                logger.error(f"Failed to queue notification: {e}")
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"Error queuing notification: {e}")

    @classmethod
    async def _send_new_referral_notification(cls, referrer_id: int, new_user_name: str):
        """Send notification to referrer about new signup via Telegram + Email using consolidated service"""
        try:
            from services.consolidated_notification_service import (
                consolidated_notification_service,
                NotificationRequest,
                NotificationChannel,
                NotificationPriority,
                NotificationCategory
            )
            
            message = f"""{new_user_name} joined via your link!

💰 Earn ${cls.REFERRER_REWARD_USD:.2f} when they trade ${cls.MIN_ACTIVITY_FOR_REWARD:.0f}+
📊 /menu → Invite Friends"""
            
            notification = NotificationRequest(
                user_id=referrer_id,
                category=NotificationCategory.PAYMENTS,  # Changed from MARKETING to PAYMENTS (enabled by default, and referrals are financial notifications)
                priority=NotificationPriority.NORMAL,
                title="🎁 New Referral!",
                message=message,
                channels=[NotificationChannel.TELEGRAM, NotificationChannel.EMAIL],
                broadcast_mode=True
            )
            
            await consolidated_notification_service.send_notification(notification)
            logger.info(f"✅ Sent new referral notification to user {referrer_id} via consolidated service")
            
        except Exception as e:
            logger.error(f"❌ Failed to send new referral notification for user {referrer_id}: {e}", exc_info=True)

    @classmethod
    def _send_reward_earned_notification(cls, referrer_id: int, active_user_name: str, reward_amount: float):
        """Send notification to referrer about earning reward via Telegram + Email"""
        try:
            # Queue notification for background processing with dual-channel delivery
            cls._queue_notification_for_background_processing(
                user_id=referrer_id,
                title="🎉 Referral Reward Earned!",
                message=f"""🎉 Referral Reward Earned!

{active_user_name} has completed ${cls.MIN_ACTIVITY_FOR_REWARD:.0f}+ in trades!
💰 You earned ${reward_amount:.2f} USD

Your wallet has been credited. Keep inviting friends to earn more!""",
                channels=["telegram", "email"],
                broadcast_mode=True  # CRITICAL: Send to BOTH Telegram AND Email
            )
            
        except Exception as e:
            logger.error(f"❌ Failed to queue reward notification for user {referrer_id}: {e}")

    @classmethod
    def _send_welcome_bonus_notification(cls, user_id: int, bonus_amount: float):
        """OLD NOTIFICATION #3 DISABLED: Trading credit welcome now sent in consolidated welcome notification"""
        # This notification has been replaced by the consolidated welcome notification
        # in services/onboarding_service.py._send_consolidated_welcome_notification()
        logger.info(f"✅ TRADING_CREDIT: Welcome bonus ${bonus_amount:.2f} for user {user_id} (notification sent via consolidated welcome)")
