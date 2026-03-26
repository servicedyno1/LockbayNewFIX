"""SMS Eligibility Service for Trade Invitations

This service manages SMS invitation restrictions based on user trading volume
and daily SMS limits to prevent abuse and ensure only established users
can send SMS invitations.
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any
from sqlalchemy import func
from sqlalchemy.orm import Session

from config import Config
from database import SessionLocal
from models import User, Transaction, UserSMSUsage

logger = logging.getLogger(__name__)


class SMSEligibilityService:
    """Service to check SMS invitation eligibility and track usage"""

    @staticmethod
    async def check_sms_eligibility(user_id: int) -> Dict[str, Any]:
        """
        Check if user is eligible to send SMS invitations
        
        Args:
            user_id: ID of the user requesting SMS invitation
            
        Returns:
            Dict with eligibility status and details:
            {
                "eligible": bool,
                "reason": str,  # If not eligible
                "remaining_sms": int,  # Daily SMS remaining
                "trading_volume": float,  # User's total trading volume
                "required_volume": float  # Minimum required volume
            }
        """
        session = SessionLocal()
        try:
            # Check if SMS invitations are globally enabled
            if not Config.SMS_INVITATIONS_ENABLED:
                return {
                    "eligible": False,
                    "reason": "SMS invitations are currently disabled",
                    "remaining_sms": 0,
                    "trading_volume": 0.0,
                    "required_volume": float(Config.SMS_MIN_TRADING_VOLUME_USD)
                }

            # Check if Twilio is properly configured
            if not Config.TWILIO_ENABLED:
                return {
                    "eligible": False,
                    "reason": "SMS service is not properly configured",
                    "remaining_sms": 0,
                    "trading_volume": 0.0,
                    "required_volume": float(Config.SMS_MIN_TRADING_VOLUME_USD)
                }

            # Get user
            user = session.query(User).filter(User.id == user_id).first()
            if not user:
                return {
                    "eligible": False,
                    "reason": "User not found",
                    "remaining_sms": 0,
                    "trading_volume": 0.0,
                    "required_volume": float(Config.SMS_MIN_TRADING_VOLUME_USD)
                }

            # Check user's total trading volume
            trading_volume = await SMSEligibilityService._get_user_trading_volume(session, user_id)
            min_required = float(Config.SMS_MIN_TRADING_VOLUME_USD)
            
            if trading_volume < min_required:
                return {
                    "eligible": False,
                    "reason": f"Minimum trading volume required: ${min_required:.0f}. Your volume: ${trading_volume:.0f}",
                    "remaining_sms": 0,
                    "trading_volume": trading_volume,
                    "required_volume": min_required
                }

            # Check daily SMS limit
            remaining_sms = await SMSEligibilityService._get_remaining_daily_sms(session, user_id)
            
            if remaining_sms <= 0:
                return {
                    "eligible": False,
                    "reason": f"Daily SMS limit reached ({Config.SMS_DAILY_LIMIT_PER_USER} per day). Try again tomorrow.",
                    "remaining_sms": 0,
                    "trading_volume": trading_volume,
                    "required_volume": min_required
                }

            # User is eligible
            return {
                "eligible": True,
                "reason": "",
                "remaining_sms": remaining_sms,
                "trading_volume": trading_volume,
                "required_volume": min_required
            }

        except Exception as e:
            logger.error(f"Error checking SMS eligibility for user {user_id}: {e}")
            return {
                "eligible": False,
                "reason": "Unable to verify SMS eligibility. Please try again.",
                "remaining_sms": 0,
                "trading_volume": 0.0,
                "required_volume": float(Config.SMS_MIN_TRADING_VOLUME_USD)
            }
        finally:
            session.close()

    @staticmethod
    async def _get_user_trading_volume(session: Session, user_id: int) -> float:
        """Calculate user's total trading volume from completed transactions"""
        try:
            # Sum all completed trade transactions
            total_volume = (
                session.query(func.sum(Transaction.amount))
                .filter(
                    Transaction.user_id == user_id,
                    Transaction.status == "completed",
                    Transaction.transaction_type.in_(["release", "deposit"])
                )
                .scalar()
            ) or 0.0
            
            return float(total_volume)

        except Exception as e:
            logger.error(f"Error calculating trading volume for user {user_id}: {e}")
            return 0.0

    @staticmethod
    async def _get_remaining_daily_sms(session: Session, user_id: int) -> int:
        """Get remaining SMS invitations for today"""
        try:
            today = datetime.now(timezone.utc).date()
            
            # Get today's SMS usage
            usage_record = (
                session.query(UserSMSUsage)
                .filter(
                    UserSMSUsage.user_id == user_id,
                    func.date(UserSMSUsage.date) == today
                )
                .first()
            )

            used_today = usage_record.sms_count if usage_record else 0
            remaining = Config.SMS_DAILY_LIMIT_PER_USER - used_today

            return max(0, remaining)  # type: ignore

        except Exception as e:
            logger.error(f"Error getting daily SMS usage for user {user_id}: {e}")
            return 0

    @staticmethod
    async def record_sms_usage(user_id: int, phone_number: str) -> bool:
        """
        Record SMS usage for rate limiting
        
        Args:
            user_id: ID of user who sent SMS
            phone_number: Phone number that received SMS
            
        Returns:
            bool: True if recorded successfully, False otherwise
        """
        session = SessionLocal()
        try:
            today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            
            # Get or create today's usage record
            usage_record = (
                session.query(UserSMSUsage)
                .filter(
                    UserSMSUsage.user_id == user_id,
                    UserSMSUsage.date == today
                )
                .first()
            )

            if usage_record:
                # Update existing record
                usage_record.sms_count = usage_record.sms_count + 1  # type: ignore
                usage_record.last_sms_sent_at = datetime.now(timezone.utc)  # type: ignore
                
                # Update phone numbers list
                try:
                    phone_list = json.loads(usage_record.phone_numbers_contacted) if usage_record.phone_numbers_contacted else []  # type: ignore
                    if phone_number not in phone_list:
                        phone_list.append(phone_number)
                        usage_record.phone_numbers_contacted = json.dumps(phone_list)  # type: ignore
                except (json.JSONDecodeError, TypeError):
                    usage_record.phone_numbers_contacted = json.dumps([phone_number])  # type: ignore
            else:
                # Create new record
                usage_record = UserSMSUsage(
                    user_id=user_id,
                    date=today,
                    sms_count=1,
                    last_sms_sent_at=datetime.now(timezone.utc),
                    phone_numbers_contacted=json.dumps([phone_number])
                )
                session.add(usage_record)

            session.commit()
            logger.info(f"Recorded SMS usage for user {user_id} to {phone_number}")
            return True

        except Exception as e:
            session.rollback()
            logger.error(f"Error recording SMS usage for user {user_id}: {e}")
            return False
        finally:
            session.close()

    @staticmethod
    async def get_user_sms_stats(user_id: int) -> Dict[str, Any]:
        """
        Get user's SMS usage statistics
        
        Args:
            user_id: ID of user
            
        Returns:
            Dict with SMS statistics
        """
        session = SessionLocal()
        try:
            # Get today's usage
            today = datetime.now(timezone.utc).date()
            today_usage = (
                session.query(UserSMSUsage)
                .filter(
                    UserSMSUsage.user_id == user_id,
                    func.date(UserSMSUsage.date) == today
                )
                .first()
            )

            # Get weekly usage (last 7 days)
            week_ago = datetime.now(timezone.utc) - timedelta(days=7)
            weekly_usage = (
                session.query(func.sum(UserSMSUsage.sms_count))
                .filter(
                    UserSMSUsage.user_id == user_id,
                    UserSMSUsage.date >= week_ago
                )
                .scalar()
            ) or 0

            # Get monthly usage (last 30 days)
            month_ago = datetime.now(timezone.utc) - timedelta(days=30)
            monthly_usage = (
                session.query(func.sum(UserSMSUsage.sms_count))
                .filter(
                    UserSMSUsage.user_id == user_id,
                    UserSMSUsage.date >= month_ago
                )
                .scalar()
            ) or 0

            today_used = today_usage.sms_count if today_usage else 0
            
            return {
                "today_used": today_used,
                "today_remaining": max(0, Config.SMS_DAILY_LIMIT_PER_USER - today_used),  # type: ignore
                "weekly_total": int(weekly_usage or 0),
                "monthly_total": int(monthly_usage or 0),
                "daily_limit": Config.SMS_DAILY_LIMIT_PER_USER,
                "last_sms_sent": getattr(today_usage, 'last_sms_sent_at', None) if today_usage else None
            }

        except Exception as e:
            logger.error(f"Error getting SMS stats for user {user_id}: {e}")
            return {
                "today_used": 0,
                "today_remaining": Config.SMS_DAILY_LIMIT_PER_USER,
                "weekly_total": 0,
                "monthly_total": 0,
                "daily_limit": Config.SMS_DAILY_LIMIT_PER_USER,
                "last_sms_sent": None
            }
        finally:
            session.close()