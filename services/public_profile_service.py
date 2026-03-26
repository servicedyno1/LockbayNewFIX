"""
Public Profile Service
Aggregates user reputation data for public social proof pages
"""

import logging
from datetime import datetime
from typing import Dict, Optional, Any
from sqlalchemy import desc

from database import SessionLocal
from models import User, Rating, Escrow
from services.enhanced_reputation_service import EnhancedReputationService
from config import Config

logger = logging.getLogger(__name__)


class PublicProfileService:
    """Service for generating public profile data"""
    
    @staticmethod
    def get_trust_level_color(trust_level: str) -> str:
        """Get color code for trust level badge"""
        colors = {
            'new': '#9CA3AF',        # Gray
            'bronze': '#10B981',     # Green
            'silver': '#3B82F6',     # Blue
            'gold': '#8B5CF6',       # Purple
            'platinum': '#F59E0B',   # Amber
            'diamond': '#EC4899'     # Pink (premium)
        }
        return colors.get(trust_level.lower(), '#9CA3AF')
    
    @staticmethod
    def get_trust_level_icon(trust_level: str) -> str:
        """Get emoji icon for trust level"""
        icons = {
            'new': '🌱',
            'bronze': '🥉',
            'silver': '🥈',
            'gold': '🥇',
            'platinum': '💎',
            'diamond': '👑'
        }
        return icons.get(trust_level.lower(), '🌱')
    
    @staticmethod
    def format_rating_stars(rating: float) -> str:
        """Convert rating to star emoji string"""
        full_stars = int(rating)
        half_star = (rating - full_stars) >= 0.5
        empty_stars = 5 - full_stars - (1 if half_star else 0)
        
        return ('⭐' * full_stars) + ('⭐' if half_star else '') + ('☆' * empty_stars)
    
    @staticmethod
    def get_profile_data(profile_slug: str) -> Optional[Dict[str, Any]]:
        """
        Get comprehensive public profile data for a user
        
        Args:
            profile_slug: User's unique profile slug
            
        Returns:
            Dictionary with all profile data for template rendering, or None if not found
        """
        session = SessionLocal()
        try:
            # Find user by profile slug
            user = session.query(User).filter(User.profile_slug == profile_slug).first()
            
            if not user:
                logger.warning(f"User not found with profile slug: {profile_slug}")
                return None
            
            # Get comprehensive reputation
            reputation = EnhancedReputationService.get_comprehensive_reputation(user.id, session)
            
            if not reputation:
                logger.warning(f"Could not load reputation for user {user.id}")
                return None
            
            # Get recent reviews (last 5)
            recent_ratings = session.query(Rating).filter(
                Rating.rated_id == user.id,
                Rating.comment.isnot(None)
            ).order_by(desc(Rating.created_at)).limit(5).all()
            
            reviews = []
            for rating in recent_ratings:
                rater = session.query(User).filter(User.id == rating.rater_id).first()
                reviews.append({
                    'stars': PublicProfileService.format_rating_stars(rating.rating),
                    'rating': rating.rating,
                    'comment': rating.comment[:200] if rating.comment else "No comment",
                    'author': rater.username if rater and rater.username else f"user{rater.id}",
                    'date': PublicProfileService.format_date(rating.created_at),
                    'trade_id': rating.escrow_id
                })
            
            # Get trading statistics - ONLY COUNT COMPLETED ESCROWS
            buyer_escrows = session.query(Escrow).filter(
                Escrow.buyer_id == user.id,
                Escrow.status == 'completed'
            ).all()
            seller_escrows = session.query(Escrow).filter(
                Escrow.seller_id == user.id,
                Escrow.status == 'completed'
            ).all()
            
            # Calculate total completed trades
            total_completed_trades = len(buyer_escrows) + len(seller_escrows)
            
            buyer_ratings = session.query(Rating).filter(
                Rating.rated_id == user.id,
                Rating.category == 'buyer'
            ).all()
            seller_ratings = session.query(Rating).filter(
                Rating.rated_id == user.id,
                Rating.category == 'seller'
            ).all()
            
            buyer_avg = sum(r.rating for r in buyer_ratings) / len(buyer_ratings) if buyer_ratings else 0
            seller_avg = sum(r.rating for r in seller_ratings) / len(seller_ratings) if seller_ratings else 0
            
            # Calculate average response time from BOTH buyer and seller roles
            response_times = []
            
            # Buyer response time: created_at → payment_confirmed_at
            for escrow in buyer_escrows:
                if escrow.payment_confirmed_at is not None and escrow.created_at is not None:
                    delta = escrow.payment_confirmed_at - escrow.created_at
                    response_times.append(delta.total_seconds())
            
            # Seller response time: created_at → seller_accepted_at
            for escrow in seller_escrows:
                if escrow.seller_accepted_at is not None and escrow.created_at is not None:
                    delta = escrow.seller_accepted_at - escrow.created_at
                    response_times.append(delta.total_seconds())
            
            if response_times:
                avg_response_seconds = sum(response_times) / len(response_times)
                # Format response time in human-readable format
                if avg_response_seconds < 60:
                    response_time = "< 1 min"
                elif avg_response_seconds < 300:  # < 5 minutes
                    response_time = f"< {int(avg_response_seconds / 60)} min"
                elif avg_response_seconds < 3600:  # < 1 hour
                    response_time = f"~ {int(avg_response_seconds / 60)} min"
                elif avg_response_seconds < 86400:  # < 1 day
                    response_time = f"~ {int(avg_response_seconds / 3600)} hr"
                else:
                    response_time = f"~ {int(avg_response_seconds / 86400)} days"
            else:
                response_time = "No data"  # Only if no trades exist at all
            
            # Build trust indicators
            trust_indicators = []
            
            if user.email_verified:
                trust_indicators.append({'icon': '✅', 'text': 'Email Verified'})
            
            if reputation.total_ratings >= 10:
                trust_indicators.append({'icon': '🎯', 'text': f'{reputation.total_ratings} Verified Reviews'})
            
            if reputation.completion_rate >= 0.95:
                trust_indicators.append({'icon': '💯', 'text': f'{reputation.completion_rate * 100:.1f}% Completion Rate'})
            
            if reputation.total_volume > 1000:
                trust_indicators.append({'icon': '💰', 'text': f'${reputation.total_volume:,.0f}+ Trading Volume'})
            
            if reputation.dispute_rate <= 0.05:
                trust_indicators.append({'icon': '🛡️', 'text': 'Low Dispute Rate (<5%)'})
            
            # Check for long-term membership (>3 months)
            from datetime import timezone as tz
            # Handle both timezone-aware and timezone-naive datetimes
            now_utc = datetime.now(tz.utc)
            created_at = user.created_at.replace(tzinfo=tz.utc) if user.created_at.tzinfo is None else user.created_at
            member_duration = (now_utc - created_at).days
            if member_duration > 90:
                trust_indicators.append({'icon': '📅', 'text': f'Active for {member_duration // 30}+ months'})
            
            # Build badges/achievements
            badges = []
            for badge_name in reputation.badges:
                badge_icon = {
                    'Trusted Trader': '🏆',
                    'Fast Responder': '⚡',
                    'Perfect Month': '🎯',
                    'Top Seller': '🌟',
                    'Volume Leader': '💎',
                    '100+ Trades': '💯'
                }.get(badge_name, '🏅')
                
                badges.append({'icon': badge_icon, 'name': badge_name})
            
            # Generate profile URLs
            base_url = Config.PUBLIC_PROFILE_BASE_URL
            profile_username = user.username if user.username else str(user.id)
            profile_url = f"{base_url}/u/{profile_username}"
            bot_link = f"https://t.me/{getattr(Config, 'BOT_USERNAME', 'lockbay_bot')}"
            trade_link = f"{bot_link}?start=trade_{user.id}"
            contact_link = f"https://t.me/{user.username}" if user.username else bot_link
            og_image_url = f"{base_url}/og/{profile_username}.png"
            
            # Build complete profile data
            profile_data = {
                # User Identity
                'user_id': user.id,
                'username': user.username or str(user.id),
                'display_name': user.first_name or user.username or f"User{user.id}",
                'first_name': user.first_name or user.username or f"User{user.id}",
                'avatar_emoji': '👤',
                'member_since': user.created_at.strftime('%b %Y'),
                
                # Trust Level
                'trust_level': reputation.trust_level.title(),
                'trust_color': PublicProfileService.get_trust_level_color(reputation.trust_level),
                'trust_icon': PublicProfileService.get_trust_level_icon(reputation.trust_level),
                'trust_score': int(reputation.trust_score),
                
                # Reputation Scores - ALL DYNAMIC FROM DATABASE
                'overall_rating': f"{reputation.overall_rating:.1f}",
                'total_trades': total_completed_trades,  # ✅ Actual completed escrows count
                'total_reviews': reputation.total_ratings,  # ✅ Actual ratings count
                'success_rate': f"{reputation.completion_rate * 100:.1f}",  # ✅ From reputation system
                
                # Trading Stats - COMPLETED ESCROWS ONLY
                'buyer_trades': len(buyer_escrows),  # ✅ Completed buyer escrows
                'buyer_rating': f"{buyer_avg:.1f}",  # ✅ From ratings
                'seller_trades': len(seller_escrows),  # ✅ Completed seller escrows
                'seller_rating': f"{seller_avg:.1f}",  # ✅ From ratings
                'response_time': response_time,  # ✅ Dynamic: avg time from created_at to seller_accepted_at
                
                # Trust Indicators
                'trust_indicators': trust_indicators,
                
                # Reviews
                'reviews': reviews,
                
                # Badges
                'badges': badges,
                
                # URLs
                'profile_url': profile_url,
                'bot_link': bot_link,
                'trade_link': trade_link,
                'contact_link': contact_link,
                'og_image_url': og_image_url
            }
            
            logger.info(f"✅ Generated public profile for @{profile_username}")
            return profile_data
            
        except Exception as e:
            logger.error(f"❌ Error generating public profile: {e}", exc_info=True)
            return None
        finally:
            session.close()
    
    @staticmethod
    def format_date(dt: datetime) -> str:
        """Format datetime for display"""
        from datetime import timezone
        now = datetime.now(timezone.utc)
        # Handle both timezone-aware and timezone-naive datetimes
        dt_aware = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
        diff = now - dt_aware
        
        if diff.days == 0:
            if diff.seconds < 3600:
                return f"{diff.seconds // 60} min ago"
            else:
                return f"{diff.seconds // 3600} hours ago"
        elif diff.days == 1:
            return "Yesterday"
        elif diff.days < 7:
            return f"{diff.days} days ago"
        elif diff.days < 30:
            return f"{diff.days // 7} weeks ago"
        else:
            return dt.strftime('%b %d, %Y')
