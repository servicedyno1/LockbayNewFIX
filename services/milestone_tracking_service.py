"""
Comprehensive Milestone Tracking Service for LockBay
Phase 3B implementation of user retention and achievement system
"""

import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Any
from sqlalchemy.orm import Session
from sqlalchemy import func, or_

from database import SessionLocal
from models import (
    User, UserAchievement, UserStreakTracking, AchievementType,
    Escrow, EscrowStatus
)
from utils.branding_utils import BrandingUtils
from utils.branding import SecurityIcons, UserRetentionElements
from utils.json_serialization import sanitize_for_json_column

logger = logging.getLogger(__name__)


class MilestoneTrackingService:
    """Comprehensive service for tracking user achievements and milestones"""
    
    # Achievement definitions with tiers
    ACHIEVEMENT_DEFINITIONS = {
        AchievementType.FIRST_TRADE: {
            1: {"name": "First Steps", "description": "Complete your first trade", "target": 1, "points": 50, "emoji": SecurityIcons.VERIFIED}
        },
        AchievementType.TRADE_VOLUME: {
            1: {"name": "Getting Started", "description": "Complete 5 successful trades", "target": 5, "points": 100, "emoji": SecurityIcons.STAR},
            2: {"name": "Active Trader", "description": "Complete 10 successful trades", "target": 10, "points": 200, "emoji": f"{SecurityIcons.STAR}{SecurityIcons.STAR}"},
            3: {"name": "Experienced Trader", "description": "Complete 25 successful trades", "target": 25, "points": 500, "emoji": f"{SecurityIcons.STAR * 3}"},
            4: {"name": "Elite Trader", "description": "Complete 50 successful trades", "target": 50, "points": 1000, "emoji": SecurityIcons.TRUSTED_USER}
        },
        AchievementType.DOLLAR_VOLUME: {
            1: {"name": "First Hundred", "description": "Process $100+ in trades", "target": 100, "points": 75, "emoji": "💰"},
            2: {"name": "Rising Trader", "description": "Process $500+ in trades", "target": 500, "points": 150, "emoji": "💎"},
            3: {"name": "High Volume", "description": "Process $1,000+ in trades", "target": 1000, "points": 300, "emoji": "🏆"},
            4: {"name": "Volume King", "description": "Process $5,000+ in trades", "target": 5000, "points": 750, "emoji": "👑"}
        },
        AchievementType.REPUTATION_MILESTONE: {
            1: {"name": "Good Reputation", "description": "Achieve 4.0+ rating", "target": 4.0, "points": 100, "emoji": SecurityIcons.STAR},
            2: {"name": "Great Reputation", "description": "Achieve 4.5+ rating", "target": 4.5, "points": 200, "emoji": f"{SecurityIcons.STAR * 2}"},
            3: {"name": "Perfect Reputation", "description": "Achieve 5.0 rating", "target": 5.0, "points": 500, "emoji": SecurityIcons.TRUSTED_USER}
        },
        AchievementType.STREAK_TRADES: {
            1: {"name": "Hot Streak", "description": "3 consecutive successful trades", "target": 3, "points": 100, "emoji": "🔥"},
            2: {"name": "On Fire", "description": "5 consecutive successful trades", "target": 5, "points": 200, "emoji": "🔥🔥"},
            3: {"name": "Unstoppable", "description": "10 consecutive successful trades", "target": 10, "points": 500, "emoji": "🔥🔥🔥"}
        },
        AchievementType.TRUSTED_TRADER: {
            1: {"name": "Trusted Trader", "description": "Achieved trusted status", "target": 1, "points": 1000, "emoji": SecurityIcons.TRUSTED_USER}
        }
    }
    
    @classmethod
    def check_user_milestones(cls, user_id: int, trigger_context: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """
        Comprehensive milestone check for a user
        
        Args:
            user_id: User ID to check milestones for
            trigger_context: Context about what triggered the check (transaction, escrow completion, etc.)
            
        Returns:
            List of newly achieved milestones
        """
        try:
            session = SessionLocal()
            try:
                # Get user with existing achievements
                user = session.query(User).filter(User.id == user_id).first()
                if not user:
                    logger.warning(f"User {user_id} not found for milestone check")
                    return []
                
                newly_achieved = []
                
                # Check all achievement types
                newly_achieved.extend(cls._check_first_trade_achievement(session, user, trigger_context))
                newly_achieved.extend(cls._check_trade_volume_achievements(session, user, trigger_context))
                newly_achieved.extend(cls._check_dollar_volume_achievements(session, user, trigger_context))
                newly_achieved.extend(cls._check_reputation_achievements(session, user, trigger_context))
                newly_achieved.extend(cls._check_streak_achievements(session, user, trigger_context))
                newly_achieved.extend(cls._check_trusted_trader_achievement(session, user, trigger_context))
                
                # Update streak tracking
                cls._update_streak_tracking(session, user, trigger_context)
                
                session.commit()
                
                logger.info(f"Milestone check for user {user_id}: {len(newly_achieved)} new achievements")
                return newly_achieved
                
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"Error checking milestones for user {user_id}: {e}")
            return []
    
    @classmethod
    def _check_first_trade_achievement(cls, session: Session, user: User, trigger_context: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Check first trade completion achievement"""
        try:
            # Check if user already has this achievement
            existing = session.query(UserAchievement).filter(
                UserAchievement.user_id == user.id,
                UserAchievement.achievement_type == AchievementType.FIRST_TRADE.value,
                UserAchievement.achieved == True
            ).first()
            
            if existing:
                return []
            
            # Check if user has completed first trade
            if user.completed_trades >= 1:
                achievement_def = cls.ACHIEVEMENT_DEFINITIONS[AchievementType.FIRST_TRADE][1]
                achievement = cls._create_achievement(
                    session, user, AchievementType.FIRST_TRADE, 1, achievement_def, trigger_context
                )
                return [achievement] if achievement else []
            
            return []
            
        except Exception as e:
            logger.error(f"Error checking first trade achievement for user {user.id}: {e}")
            return []
    
    @classmethod
    def _check_trade_volume_achievements(cls, session: Session, user: User, trigger_context: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Check trade volume milestones"""
        try:
            newly_achieved = []
            current_trades = user.completed_trades
            
            for tier, achievement_def in cls.ACHIEVEMENT_DEFINITIONS[AchievementType.TRADE_VOLUME].items():
                if current_trades >= achievement_def["target"]:
                    # Check if user already has this tier
                    existing = session.query(UserAchievement).filter(
                        UserAchievement.user_id == user.id,
                        UserAchievement.achievement_type == AchievementType.TRADE_VOLUME.value,
                        UserAchievement.achievement_tier == tier,
                        UserAchievement.achieved == True
                    ).first()
                    
                    if not existing:
                        achievement = cls._create_achievement(
                            session, user, AchievementType.TRADE_VOLUME, tier, achievement_def, trigger_context
                        )
                        if achievement:
                            newly_achieved.append(achievement)
            
            return newly_achieved
            
        except Exception as e:
            logger.error(f"Error checking trade volume achievements for user {user.id}: {e}")
            return []
    
    @classmethod
    def _check_dollar_volume_achievements(cls, session: Session, user: User, trigger_context: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Check dollar volume milestones"""
        try:
            # Calculate total USD volume from completed escrows
            total_volume = session.query(func.sum(Escrow.amount)).filter(
                or_(Escrow.buyer_id == user.id, Escrow.seller_id == user.id),
                Escrow.status == EscrowStatus.COMPLETED.value
            ).scalar() or Decimal('0')
            
            newly_achieved = []
            
            for tier, achievement_def in cls.ACHIEVEMENT_DEFINITIONS[AchievementType.DOLLAR_VOLUME].items():
                if total_volume >= achievement_def["target"]:
                    # Check if user already has this tier
                    existing = session.query(UserAchievement).filter(
                        UserAchievement.user_id == user.id,
                        UserAchievement.achievement_type == AchievementType.DOLLAR_VOLUME.value,
                        UserAchievement.achievement_tier == tier,
                        UserAchievement.achieved == True
                    ).first()
                    
                    if not existing:
                        achievement = cls._create_achievement(
                            session, user, AchievementType.DOLLAR_VOLUME, tier, achievement_def, trigger_context
                        )
                        if achievement:
                            newly_achieved.append(achievement)
            
            return newly_achieved
            
        except Exception as e:
            logger.error(f"Error checking dollar volume achievements for user {user.id}: {e}")
            return []
    
    @classmethod
    def _check_reputation_achievements(cls, session: Session, user: User, trigger_context: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Check reputation milestones"""
        try:
            current_reputation = float(getattr(user, 'reputation_score', 0.0))
            newly_achieved = []
            
            for tier, achievement_def in cls.ACHIEVEMENT_DEFINITIONS[AchievementType.REPUTATION_MILESTONE].items():
                if current_reputation >= achievement_def["target"]:
                    # Check if user already has this tier
                    existing = session.query(UserAchievement).filter(
                        UserAchievement.user_id == user.id,
                        UserAchievement.achievement_type == AchievementType.REPUTATION_MILESTONE.value,
                        UserAchievement.achievement_tier == tier,
                        UserAchievement.achieved == True
                    ).first()
                    
                    if not existing:
                        achievement = cls._create_achievement(
                            session, user, AchievementType.REPUTATION_MILESTONE, tier, achievement_def, trigger_context
                        )
                        if achievement:
                            newly_achieved.append(achievement)
            
            return newly_achieved
            
        except Exception as e:
            logger.error(f"Error checking reputation achievements for user {user.id}: {e}")
            return []
    
    @classmethod
    def _check_streak_achievements(cls, session: Session, user: User, trigger_context: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Check streak-based achievements"""
        try:
            # Get or create streak tracking
            streak_tracking = session.query(UserStreakTracking).filter(
                UserStreakTracking.user_id == user.id
            ).first()
            
            if not streak_tracking:
                return []
            
            current_streak = streak_tracking.successful_trades_streak
            newly_achieved = []
            
            for tier, achievement_def in cls.ACHIEVEMENT_DEFINITIONS[AchievementType.STREAK_TRADES].items():
                if current_streak >= achievement_def["target"]:
                    # Check if user already has this tier
                    existing = session.query(UserAchievement).filter(
                        UserAchievement.user_id == user.id,
                        UserAchievement.achievement_type == AchievementType.STREAK_TRADES.value,
                        UserAchievement.achievement_tier == tier,
                        UserAchievement.achieved == True
                    ).first()
                    
                    if not existing:
                        achievement = cls._create_achievement(
                            session, user, AchievementType.STREAK_TRADES, tier, achievement_def, trigger_context
                        )
                        if achievement:
                            newly_achieved.append(achievement)
            
            return newly_achieved
            
        except Exception as e:
            logger.error(f"Error checking streak achievements for user {user.id}: {e}")
            return []
    
    @classmethod
    def _check_trusted_trader_achievement(cls, session: Session, user: User, trigger_context: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Check trusted trader status achievement"""
        try:
            # Define criteria for trusted trader status
            # User needs: 10+ completed trades, 4.5+ reputation, minimal disputes
            is_trusted = (
                user.completed_trades >= 10 and 
                float(getattr(user, 'reputation_score', 0.0)) >= 4.5
            )
            
            if is_trusted:
                # Check if user already has this achievement
                existing = session.query(UserAchievement).filter(
                    UserAchievement.user_id == user.id,
                    UserAchievement.achievement_type == AchievementType.TRUSTED_TRADER.value,
                    UserAchievement.achieved == True
                ).first()
                
                if not existing:
                    achievement_def = cls.ACHIEVEMENT_DEFINITIONS[AchievementType.TRUSTED_TRADER][1]
                    achievement = cls._create_achievement(
                        session, user, AchievementType.TRUSTED_TRADER, 1, achievement_def, trigger_context
                    )
                    return [achievement] if achievement else []
            
            return []
            
        except Exception as e:
            logger.error(f"Error checking trusted trader achievement for user {user.id}: {e}")
            return []
    
    @classmethod
    def _create_achievement(cls, session: Session, user: User, achievement_type: AchievementType, 
                          tier: int, achievement_def: Dict[str, Any], 
                          trigger_context: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Create a new achievement record and return achievement data"""
        try:
            achievement = UserAchievement(
                user_id=user.id,
                achievement_type=achievement_type.value,
                achievement_name=achievement_def["name"],
                achievement_description=achievement_def["description"],
                achievement_tier=tier,
                target_value=Decimal(str(achievement_def["target"])),
                current_value=Decimal(str(achievement_def["target"])),  # Achieved, so current = target
                achieved=True,
                reward_message=f"🎉 {achievement_def['description']} - You've earned {achievement_def['points']} points!",
                badge_emoji=achievement_def["emoji"],
                points_awarded=achievement_def["points"],
                first_eligible_at=datetime.utcnow(),
                achieved_at=datetime.utcnow(),
                trigger_transaction_id=trigger_context.get("transaction_id") if trigger_context else None,
                trigger_escrow_id=trigger_context.get("escrow_id") if trigger_context else None,
                additional_context=sanitize_for_json_column(trigger_context) if trigger_context else None
            )
            
            session.add(achievement)
            session.flush()  # Get the ID
            
            # Generate milestone message
            user_data = {
                "first_name": user.first_name or "Trader",
                "user_id": user.id,
                "total_trades": user.completed_trades
            }
            
            milestone_message = BrandingUtils.make_milestone_message(user_data, achievement_type.value)
            
            return {
                "achievement_id": achievement.id,
                "achievement_type": achievement_type.value,
                "achievement_name": achievement_def["name"],
                "achievement_description": achievement_def["description"],
                "tier": tier,
                "points_awarded": achievement_def["points"],
                "badge_emoji": achievement_def["emoji"],
                "milestone_message": milestone_message,
                "user_id": user.id
            }
            
        except Exception as e:
            logger.error(f"Error creating achievement for user {user.id}: {e}")
            return None
    
    @classmethod
    def _update_streak_tracking(cls, session: Session, user: User, trigger_context: Optional[Dict[str, Any]] = None) -> None:
        """Update user streak tracking based on recent activity"""
        try:
            # Get or create streak tracking
            streak_tracking = session.query(UserStreakTracking).filter(
                UserStreakTracking.user_id == user.id
            ).first()
            
            if not streak_tracking:
                streak_tracking = UserStreakTracking(user_id=user.id)
                session.add(streak_tracking)
            
            # Update activity streak
            today = datetime.utcnow().date()
            if streak_tracking.last_activity_date:
                last_activity_date = streak_tracking.last_activity_date.date()
                if last_activity_date == today:
                    # Same day - no change
                    pass
                elif last_activity_date == today - timedelta(days=1):
                    # Consecutive day - increment streak
                    streak_tracking.daily_activity_streak += 1
                else:
                    # Streak broken - reset
                    streak_tracking.daily_activity_streak = 1
                    streak_tracking.activity_reset_count += 1
            else:
                # First activity
                streak_tracking.daily_activity_streak = 1
            
            streak_tracking.last_activity_date = datetime.utcnow()
            
            # Update best streaks
            if streak_tracking.daily_activity_streak > streak_tracking.best_daily_activity_streak:
                streak_tracking.best_daily_activity_streak = streak_tracking.daily_activity_streak
            
            # Update successful trades streak if this was a successful trade completion
            if trigger_context and trigger_context.get("event_type") == "escrow_completed":
                if streak_tracking.last_successful_trade_date:
                    # Check if this continues the streak (within reasonable time)
                    days_since_last = (datetime.utcnow() - streak_tracking.last_successful_trade_date).days
                    if days_since_last <= 7:  # Allow up to 7 days between trades for streak
                        streak_tracking.successful_trades_streak += 1
                    else:
                        # Streak broken
                        streak_tracking.successful_trades_streak = 1
                        streak_tracking.successful_trades_reset_count += 1
                else:
                    # First successful trade
                    streak_tracking.successful_trades_streak = 1
                
                streak_tracking.last_successful_trade_date = datetime.utcnow()
                
                # Update best trades streak
                if streak_tracking.successful_trades_streak > streak_tracking.best_successful_trades_streak:
                    streak_tracking.best_successful_trades_streak = streak_tracking.successful_trades_streak
            
            streak_tracking.updated_at = datetime.utcnow()
            
        except Exception as e:
            logger.error(f"Error updating streak tracking for user {user.id}: {e}")
    
    @classmethod
    def get_user_achievement_summary(cls, user_id: int) -> Dict[str, Any]:
        """Get comprehensive achievement summary for a user"""
        try:
            session = SessionLocal()
            try:
                user = session.query(User).filter(User.id == user_id).first()
                if not user:
                    return {"error": "User not found"}
                
                # Get all achievements
                achievements = session.query(UserAchievement).filter(
                    UserAchievement.user_id == user_id,
                    UserAchievement.achieved == True
                ).order_by(UserAchievement.achieved_at.desc()).all()
                
                # Get streak tracking
                streak_tracking = session.query(UserStreakTracking).filter(
                    UserStreakTracking.user_id == user_id
                ).first()
                
                # Calculate total points
                total_points = sum(achievement.points_awarded for achievement in achievements)
                
                # Get current badge level
                current_badge = UserRetentionElements.get_achievement_badge(user.completed_trades)
                
                return {
                    "user_id": user_id,
                    "total_achievements": len(achievements),
                    "total_points": total_points,
                    "current_badge": current_badge,
                    "reputation_score": float(getattr(user, 'reputation_score', 0.0)),
                    "total_trades": user.completed_trades,
                    "successful_trades": user.completed_trades,
                    "current_streaks": {
                        "successful_trades": streak_tracking.successful_trades_streak if streak_tracking else 0,
                        "daily_activity": streak_tracking.daily_activity_streak if streak_tracking else 0
                    } if streak_tracking else {},
                    "best_streaks": {
                        "successful_trades": streak_tracking.best_successful_trades_streak if streak_tracking else 0,
                        "daily_activity": streak_tracking.best_daily_activity_streak if streak_tracking else 0
                    } if streak_tracking else {},
                    "recent_achievements": [
                        {
                            "name": achievement.achievement_name,
                            "description": achievement.achievement_description,
                            "tier": achievement.achievement_tier,
                            "points": achievement.points_awarded,
                            "emoji": achievement.badge_emoji,
                            "achieved_at": achievement.achieved_at.isoformat()
                        }
                        for achievement in achievements[:5]  # Last 5 achievements
                    ]
                }
                
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"Error getting achievement summary for user {user_id}: {e}")
            return {"error": str(e)}


# Logging setup
logger.info("✅ MilestoneTrackingService loaded successfully")