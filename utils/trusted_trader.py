"""
Trusted Trader System
Comprehensive achievement and badge system for users
"""

import logging
from models import Escrow, Rating
from utils.branding import SecurityIcons
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class TrustedTraderSystem:
    """Manages trusted trader levels, badges, and achievements"""

    # Trader Level Thresholds
    TRADER_LEVELS = {
        0: {
            "name": "New User",
            "badge": f"{SecurityIcons.STAR}",
            "color": "#9CA3AF",  # Gray
            "requirements": "Complete verification",
            "benefits": ["Access to basic trading"],
        },
        1: {
            "name": "New Trader",
            "badge": f"{SecurityIcons.STAR}",
            "color": "#10B981",  # Green
            "requirements": "1+ successful trade",
            "benefits": ["Basic trading access", "Community support"],
        },
        5: {
            "name": "Active Trader",
            "badge": f"{SecurityIcons.STAR}{SecurityIcons.STAR}",
            "color": "#3B82F6",  # Blue
            "requirements": "5+ successful trades",
            "benefits": ["Higher transaction limits", "Priority support"],
        },
        10: {
            "name": "Experienced Trader",
            "badge": f"{SecurityIcons.STAR}{SecurityIcons.STAR}{SecurityIcons.STAR}",
            "color": "#8B5CF6",  # Purple
            "requirements": "10+ successful trades",
            "benefits": ["Advanced trading features", "Reduced fees"],
        },
        25: {
            "name": "Trusted Trader",
            "badge": f"{SecurityIcons.TRUSTED_USER}",
            "color": "#F59E0B",  # Amber
            "requirements": "25+ successful trades, 4.5+ rating",
            "benefits": [
                "Trusted status badge",
                "Premium features",
                "Fast-track support",
            ],
        },
        50: {
            "name": "Elite Trader",
            "badge": f"{SecurityIcons.SHIELD}",
            "color": "#EF4444",  # Red
            "requirements": "50+ successful trades, 4.7+ rating",
            "benefits": [
                "Elite status",
                "Maximum limits",
                "VIP support",
                "Beta features",
            ],
        },
        100: {
            "name": "Master Trader",
            "badge": f"{SecurityIcons.SHIELD}👑",
            "color": "#DC2626",  # Dark Red
            "requirements": "100+ successful trades, 4.8+ rating",
            "benefits": ["Master status", "Unlimited trading", "Direct admin access"],
        },
    }

    # Achievement System
    ACHIEVEMENTS = {
        "first_trade": {
            "name": "First Steps",
            "description": "Complete your first trade",
            "icon": SecurityIcons.VERIFIED,
            "reward": "Trading confidence boost",
        },
        "perfect_rating": {
            "name": "Perfect Score",
            "description": "Maintain 5.0 rating with 10+ ratings",
            "icon": SecurityIcons.STAR,
            "reward": "Reputation highlight",
        },
        "volume_milestone": {
            "name": "High Volume",
            "description": "Trade over $10,000 total volume",
            "icon": SecurityIcons.PROGRESS,
            "reward": "Volume badge",
        },
        "dispute_free": {
            "name": "Dispute Free",
            "description": "50+ trades without disputes",
            "icon": SecurityIcons.SHIELD,
            "reward": "Trust indicator",
        },
        "quick_responder": {
            "name": "Quick Responder",
            "description": "Average response time under 1 hour",
            "icon": SecurityIcons.VERIFIED,
            "reward": "Speed badge",
        },
    }

    @staticmethod
    async def get_trader_level_async(user, session: AsyncSession):
        """Async version: Calculate user's trader level based on stats"""
        try:
            # Get completed trades for level calculation (only count successful trades)
            stmt = select(func.count(Escrow.id)).where(
                ((Escrow.buyer_id == user.id) | (Escrow.seller_id == user.id)),
                Escrow.status == "completed",
            )
            result = await session.execute(stmt)
            completed_trades = result.scalar() or 0

            # Use completed trades for trust level calculation
            trade_count = completed_trades

            # Find highest level user qualifies for
            current_level = None
            for threshold, level_info in sorted(
                TrustedTraderSystem.TRADER_LEVELS.items(), reverse=True
            ):
                if trade_count >= threshold:
                    # Additional checks for higher levels
                    reputation_score = getattr(user, 'reputation_score', 0.0) or 0.0
                    if threshold >= 25:  # Trusted Trader+
                        if reputation_score < 4.5:
                            continue
                    if threshold >= 50:  # Elite Trader+
                        if reputation_score < 4.7:
                            continue
                    if threshold >= 100:  # Master Trader
                        if reputation_score < 4.8:
                            continue

                    current_level = level_info.copy()
                    current_level["threshold"] = threshold
                    current_level["trade_count"] = trade_count
                    break

            return current_level or TrustedTraderSystem.TRADER_LEVELS[0]

        except Exception as e:
            logger.error(f"Error calculating trader level: {e}")
            return TrustedTraderSystem.TRADER_LEVELS[0]

    @staticmethod
    def get_trader_level(user, session):
        """Calculate user's trader level based on stats - works with both sync and async sessions.
        
        Note:
            For async sessions, returns safe default level to prevent errors
        """
        try:
            # For AsyncSession, safely return default level to prevent errors
            # Business logic preserved: async flows won't get trader discounts, but won't crash
            if isinstance(session, AsyncSession):
                logger.info(f"AsyncSession detected for user {user.id} trader level check - safely returning default level")
                return TrustedTraderSystem.TRADER_LEVELS[0]
            
            # Get completed trades for level calculation (only count successful trades)
            stmt = select(func.count(Escrow.id)).where(
                ((Escrow.buyer_id == user.id) | (Escrow.seller_id == user.id)),
                Escrow.status == "completed",
            )
            result = session.execute(stmt)
            completed_trades = result.scalar() or 0

            # Use completed trades for trust level calculation
            trade_count = completed_trades

            # Find highest level user qualifies for
            current_level = None
            for threshold, level_info in sorted(
                TrustedTraderSystem.TRADER_LEVELS.items(), reverse=True
            ):
                if trade_count >= threshold:
                    # Additional checks for higher levels
                    reputation_score = getattr(user, 'reputation_score', 0.0) or 0.0
                    if threshold >= 25:  # Trusted Trader+
                        if reputation_score < 4.5:
                            continue
                    if threshold >= 50:  # Elite Trader+
                        if reputation_score < 4.7:
                            continue
                    if threshold >= 100:  # Master Trader
                        if reputation_score < 4.8:
                            continue

                    current_level = level_info.copy()
                    current_level["threshold"] = threshold
                    current_level["trade_count"] = trade_count
                    break

            return current_level or TrustedTraderSystem.TRADER_LEVELS[0]

        except Exception as e:
            logger.error(f"Error calculating trader level: {e}")
            return TrustedTraderSystem.TRADER_LEVELS[0]

    @staticmethod
    def get_next_level(current_level_threshold):
        """Get information about the next achievable level"""
        for threshold, level_info in sorted(TrustedTraderSystem.TRADER_LEVELS.items()):
            if threshold > current_level_threshold:
                return threshold, level_info
        return None, None

    @staticmethod
    def format_trader_display(user, session, show_progress=True):
        """Format comprehensive trader status display"""
        level_info = TrustedTraderSystem.get_trader_level(user, session)

        # Base display
        display = f"{level_info['badge']} **{level_info['name']}**"

        if show_progress:
            next_threshold, next_level = TrustedTraderSystem.get_next_level(
                level_info.get("threshold", 0)
            )

            if next_threshold and next_level:
                current_count = level_info.get("trade_count", 0)
                progress = min(100, int((current_count / next_threshold) * 100))

                # Progress bar
                filled = int(progress / 10)
                empty = 10 - filled
                progress_bar = "█" * filled + "░" * empty

                display += f"\n\n**Progress to {next_level['name']}:**\n"
                display += f"`{progress_bar}` {progress}%\n"
                display += f"({current_count}/{next_threshold} trades)"

        return display

    @staticmethod
    async def get_achievement_status_async(user, session: AsyncSession):
        """Async version: Check which achievements user has earned"""
        earned_achievements = []

        try:
            # Get user stats (only count completed trades)
            total_stmt = select(func.count(Escrow.id)).where(
                ((Escrow.buyer_id == user.id) | (Escrow.seller_id == user.id)),
                Escrow.status == "completed"
            )
            total_result = await session.execute(total_stmt)
            total_trades = total_result.scalar() or 0

            # Get actual ratings count (FIX: query database instead of broken counter)
            ratings_stmt = select(func.count(Rating.id)).where(
                Rating.rated_id == user.id
            )
            ratings_result = await session.execute(ratings_stmt)
            actual_ratings_count = ratings_result.scalar() or 0

            # Calculate total volume from completed escrows
            volume_stmt = select(func.sum(Escrow.amount)).where(
                ((Escrow.buyer_id == user.id) | (Escrow.seller_id == user.id)),
                Escrow.status == "completed"
            )
            volume_result = await session.execute(volume_stmt)
            total_volume_usd = float(volume_result.scalar() or 0)

            # Check achievements
            if total_trades >= 1:
                earned_achievements.append("first_trade")

            # FIX: Use actual ratings count instead of broken counter
            if getattr(user, 'reputation_score', 0.0) >= 5.0 and actual_ratings_count >= 10:
                earned_achievements.append("perfect_rating")

            # FIX: Calculate volume from escrows
            if total_volume_usd >= 10000:
                earned_achievements.append("volume_milestone")

            if total_trades >= 50:
                # Check for disputes (use lowercase to match DB)
                disputed_stmt = select(func.count(Escrow.id)).where(
                    ((Escrow.buyer_id == user.id) | (Escrow.seller_id == user.id)),
                    Escrow.status == "disputed",
                )
                disputed_result = await session.execute(disputed_stmt)
                disputed_trades = disputed_result.scalar() or 0

                if disputed_trades == 0:
                    earned_achievements.append("dispute_free")

        except Exception as e:
            logger.error(f"Error checking achievements: {e}")

        return earned_achievements

    @staticmethod
    def get_achievement_status(user, session):
        """Check which achievements user has earned - works with both sync and async sessions.
        
        Note:
            For async sessions, returns safe empty list to prevent errors
        """
        earned_achievements = []

        try:
            # For AsyncSession, safely return empty achievements to prevent errors
            if isinstance(session, AsyncSession):
                logger.info(f"AsyncSession detected for user {user.id} achievement check - safely returning empty achievements")
                return earned_achievements

            
            # Get user stats (only count completed trades)
            total_stmt = select(func.count(Escrow.id)).where(
                ((Escrow.buyer_id == user.id) | (Escrow.seller_id == user.id)),
                Escrow.status == "completed"
            )
            total_result = session.execute(total_stmt)
            total_trades = total_result.scalar() or 0

            # Get actual ratings count (FIX: query database instead of broken counter)
            ratings_stmt = select(func.count(Rating.id)).where(
                Rating.rated_id == user.id
            )
            ratings_result = session.execute(ratings_stmt)
            actual_ratings_count = ratings_result.scalar() or 0

            # Calculate total volume from completed escrows (FIX: calculate instead of using missing column)
            volume_stmt = select(func.sum(Escrow.amount)).where(
                ((Escrow.buyer_id == user.id) | (Escrow.seller_id == user.id)),
                Escrow.status == "completed"
            )
            volume_result = session.execute(volume_stmt)
            total_volume_usd = float(volume_result.scalar() or 0)

            # Check achievements
            if total_trades >= 1:
                earned_achievements.append("first_trade")

            # FIX: Use actual ratings count instead of broken counter
            if getattr(user, 'reputation_score', 0.0) >= 5.0 and actual_ratings_count >= 10:
                earned_achievements.append("perfect_rating")

            # FIX: Calculate volume from escrows instead of using missing column
            if total_volume_usd >= 10000:
                earned_achievements.append("volume_milestone")

            if total_trades >= 50:
                # Check for disputes (use lowercase to match DB)
                disputed_stmt = select(func.count(Escrow.id)).where(
                    ((Escrow.buyer_id == user.id) | (Escrow.seller_id == user.id)),
                    Escrow.status == "disputed",
                )
                disputed_result = session.execute(disputed_stmt)
                disputed_trades = disputed_result.scalar() or 0

                if disputed_trades == 0:
                    earned_achievements.append("dispute_free")

        except Exception as e:
            logger.error(f"Error checking achievements: {e}")

        return earned_achievements

    @staticmethod
    def format_achievements_display(user, session):
        """Format achievements for display"""
        earned = TrustedTraderSystem.get_achievement_status(user, session)

        if not earned:
            return "🏆 **Achievements**\nNo achievements yet - start trading to unlock!"

        display = "🏆 **Your Achievements**\n\n"
        for achievement_key in earned:
            achievement = TrustedTraderSystem.ACHIEVEMENTS[achievement_key]
            display += f"{achievement['icon']} **{achievement['name']}**\n"
            display += f"   _{achievement['description']}_\n\n"

        return display.strip()

    @staticmethod
    async def get_trust_indicators_async(user, session: AsyncSession):
        """Async version: Get trust indicators for user profile"""
        level_info = await TrustedTraderSystem.get_trader_level_async(user, session)
        indicators = []

        # Level-based indicators
        if level_info.get("threshold", 0) >= 25:
            indicators.append("🏅 Trusted Trader")

        if level_info.get("threshold", 0) >= 50:
            indicators.append("👑 Elite Status")

        # Rating-based indicators - query actual ratings count
        try:
            ratings_stmt = select(func.count(Rating.id)).where(
                Rating.rated_id == user.id
            )
            ratings_result = await session.execute(ratings_stmt)
            actual_ratings_count = ratings_result.scalar() or 0
        except Exception:
            actual_ratings_count = 0

        if getattr(user, 'reputation_score', 0.0) >= 4.9 and actual_ratings_count >= 5:
            indicators.append("⭐ Perfect Rating")

        # Volume-based indicators - calculate from escrows
        try:
            volume_stmt = select(func.sum(Escrow.amount)).where(
                ((Escrow.buyer_id == user.id) | (Escrow.seller_id == user.id)),
                Escrow.status == "completed"
            )
            volume_result = await session.execute(volume_stmt)
            total_volume_usd = float(volume_result.scalar() or 0)
        except Exception:
            total_volume_usd = 0

        if total_volume_usd >= 50000:
            indicators.append("💎 High Volume")

        # Experience indicators - use completed trades for accuracy
        try:
            completed_stmt = select(func.count(Escrow.id)).where(
                ((Escrow.buyer_id == user.id) | (Escrow.seller_id == user.id)),
                Escrow.status == "completed",
            )
            completed_result = await session.execute(completed_stmt)
            completed_trades = completed_result.scalar() or 0
        except Exception:
            completed_trades = 0

        if completed_trades >= 100:
            indicators.append("🎯 Master Trader")

        return indicators

    @staticmethod
    def get_trust_indicators(user, session):
        """Get trust indicators for user profile - sync version only"""
        level_info = TrustedTraderSystem.get_trader_level(user, session)
        indicators = []

        # Level-based indicators
        if level_info.get("threshold", 0) >= 25:
            indicators.append("🏅 Trusted Trader")

        if level_info.get("threshold", 0) >= 50:
            indicators.append("👑 Elite Status")

        # Rating-based indicators - query actual ratings count
        try:
            ratings_stmt = select(func.count(Rating.id)).where(
                Rating.rated_id == user.id
            )
            ratings_result = session.execute(ratings_stmt)
            actual_ratings_count = ratings_result.scalar() or 0
        except Exception:
            actual_ratings_count = 0

        if getattr(user, 'reputation_score', 0.0) >= 4.9 and actual_ratings_count >= 5:
            indicators.append("⭐ Perfect Rating")

        # Volume-based indicators - calculate from escrows
        try:
            volume_stmt = select(func.sum(Escrow.amount)).where(
                ((Escrow.buyer_id == user.id) | (Escrow.seller_id == user.id)),
                Escrow.status == "completed"
            )
            volume_result = session.execute(volume_stmt)
            total_volume_usd = float(volume_result.scalar() or 0)
        except Exception:
            total_volume_usd = 0

        if total_volume_usd >= 50000:
            indicators.append("💎 High Volume")

        # Experience indicators - use completed trades for accuracy
        try:
            completed_stmt = select(func.count(Escrow.id)).where(
                ((Escrow.buyer_id == user.id) | (Escrow.seller_id == user.id)),
                Escrow.status == "completed",
            )
            completed_result = session.execute(completed_stmt)
            completed_trades = completed_result.scalar() or 0
        except Exception:
            completed_trades = 0

        if completed_trades >= 100:
            indicators.append("🎯 Master Trader")

        return indicators
