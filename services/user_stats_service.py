"""
User Stats Service - Automatically update user reputation and trade statistics
"""

import logging
from typing import Optional, Union
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, and_, select

from database import get_session
from models import User, Rating, Escrow

logger = logging.getLogger(__name__)


class UserStatsService:
    """Service to automatically update user statistics and reputation"""

    @staticmethod
    async def calculate_user_reputation(user_id: int, session: Union[Session, AsyncSession]) -> tuple[float, int]:
        """
        Calculate user's reputation score and total completed trades
        
        Returns:
            tuple: (reputation_score, total_trades)
        """
        try:
            is_async = isinstance(session, AsyncSession)
            
            # Get all ratings for this user (both as buyer and seller) - using select() instead of query()
            ratings_stmt = select(Rating).where(Rating.rated_id == user_id)
            
            if is_async:
                ratings_result = await session.execute(ratings_stmt)
                ratings = ratings_result.scalars().all()
            else:
                ratings_result = session.execute(ratings_stmt)
                ratings = ratings_result.scalars().all()
            
            if not ratings:
                return 0.0, 0
            
            # Calculate average rating - ensure we have Python numeric types
            rating_values = [float(rating.rating) for rating in ratings]
            total_rating = sum(rating_values)
            average_rating = total_rating / len(ratings)
            
            # Count unique completed trades for this user - using select() instead of query()
            count_stmt = select(func.count()).select_from(Escrow).where(
                and_(
                    Escrow.status == 'completed',
                    (Escrow.buyer_id == user_id) | (Escrow.seller_id == user_id)
                )
            )
            
            if is_async:
                count_result = await session.execute(count_stmt)
                completed_trades = count_result.scalar()
            else:
                count_result = session.execute(count_stmt)
                completed_trades = count_result.scalar()
            
            # Convert to proper Python types before operations
            rating_value = float(average_rating)
            trades_value = int(completed_trades or 0)
            
            logger.info(f"User {user_id} stats: {rating_value:.1f} rating from {len(ratings)} reviews, {trades_value} completed trades")
            
            return round(rating_value, 1), trades_value
            
        except Exception as e:
            logger.error(f"Error calculating reputation for user {user_id}: {e}")
            return 0.0, 0

    @staticmethod
    async def update_user_stats(user_id: int, session: Optional[Union[Session, AsyncSession]] = None) -> bool:
        """
        Update user's reputation_score and total_trades fields in database
        
        Args:
            user_id: ID of user to update
            session: Optional existing session, will create new one if not provided
            
        Returns:
            bool: True if successful, False otherwise
        """
        close_session = False
        is_async = isinstance(session, AsyncSession) if session else False
        
        try:
            if session is None:
                session = get_session()
                close_session = True
                is_async = False
            
            # Get user - using select() instead of query()
            user_stmt = select(User).where(User.id == user_id)
            
            if is_async:
                user_result = await session.execute(user_stmt)  # type: ignore
                user = user_result.scalar_one_or_none()
            else:
                user_result = session.execute(user_stmt)  # type: ignore
                user = user_result.scalar_one_or_none()
            
            if not user:
                logger.warning(f"User {user_id} not found for stats update")
                return False
            
            # Calculate new stats
            reputation_score, total_trades = await UserStatsService.calculate_user_reputation(user_id, session)
            
            # Calculate total ratings count
            ratings_count_stmt = select(func.count(Rating.id)).where(Rating.rated_id == user_id)
            if is_async:
                ratings_count_result = await session.execute(ratings_count_stmt)  # type: ignore
                total_ratings_count = ratings_count_result.scalar() or 0
            else:
                ratings_count_result = session.execute(ratings_count_stmt)  # type: ignore
                total_ratings_count = ratings_count_result.scalar() or 0
            
            # Update user record using correct field names and data types
            setattr(user, 'reputation_score', reputation_score)  # Store as float with 1 decimal precision
            setattr(user, 'completed_trades', total_trades)  # Use completed_trades as per model
            setattr(user, 'total_ratings', int(total_ratings_count))  # Update total_ratings counter
            
            # Commit if we created the session
            if close_session:
                if is_async:
                    await session.commit()  # type: ignore
                else:
                    session.commit()  # type: ignore
                logger.info(f"Updated stats for user {user_id}: reputation={reputation_score}, trades={total_trades}, ratings={total_ratings_count}")
            
            return True
            
        except Exception as e:
            logger.error(f"Error updating stats for user {user_id}: {e}")
            if close_session and session:
                if is_async:
                    await session.rollback()  # type: ignore
                else:
                    session.rollback()  # type: ignore
            return False
            
        finally:
            if close_session and session:
                if is_async:
                    await session.close()  # type: ignore
                else:
                    session.close()  # type: ignore

    @staticmethod
    async def update_both_user_stats(buyer_id: int, seller_id: int, session: Optional[Union[Session, AsyncSession]] = None) -> bool:
        """
        Update stats for both buyer and seller (typically after trade completion)
        
        Args:
            buyer_id: ID of buyer
            seller_id: ID of seller
            session: Optional existing session
            
        Returns:
            bool: True if both updates successful
        """
        try:
            buyer_success = await UserStatsService.update_user_stats(buyer_id, session)
            seller_success = await UserStatsService.update_user_stats(seller_id, session)
            
            return buyer_success and seller_success
            
        except Exception as e:
            logger.error(f"Error updating stats for buyer {buyer_id} and seller {seller_id}: {e}")
            return False

    @staticmethod
    async def refresh_all_user_stats() -> int:
        """
        Refresh stats for all users with ratings or completed trades
        
        Returns:
            int: Number of users updated
        """
        session = get_session()
        updated_count = 0
        is_async = isinstance(session, AsyncSession)
        
        try:
            # Get all users who have either given/received ratings or completed trades
            # Using select() instead of query()
            rated_ids_stmt = select(Rating.rated_id).distinct()
            rater_ids_stmt = select(Rating.rater_id).distinct()
            buyer_ids_stmt = select(Escrow.buyer_id).where(Escrow.status == 'completed').distinct()
            seller_ids_stmt = select(Escrow.seller_id).where(Escrow.status == 'completed').distinct()
            
            users_with_activity_stmt = select(User.id).where(
                (User.id.in_(rated_ids_stmt)) |
                (User.id.in_(rater_ids_stmt)) |
                (User.id.in_(buyer_ids_stmt)) |
                (User.id.in_(seller_ids_stmt))
            ).distinct()
            
            if is_async:
                users_result = await session.execute(users_with_activity_stmt)  # type: ignore
                users_with_activity = users_result.all()
            else:
                users_result = session.execute(users_with_activity_stmt)  # type: ignore
                users_with_activity = users_result.all()
            
            for (user_id,) in users_with_activity:
                if await UserStatsService.update_user_stats(user_id, session):  # type: ignore
                    updated_count += 1
            
            if is_async:
                await session.commit()  # type: ignore
            else:
                session.commit()  # type: ignore
            logger.info(f"Refreshed stats for {updated_count} users")
            
        except Exception as e:
            logger.error(f"Error refreshing user stats: {e}")
            if is_async:
                await session.rollback()  # type: ignore
            else:
                session.rollback()  # type: ignore
            
        finally:
            if is_async:
                await session.close()  # type: ignore
            else:
                session.close()  # type: ignore
            
        return updated_count