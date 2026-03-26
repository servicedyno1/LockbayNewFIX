"""
Enhanced Reputation Service
Comprehensive rating and reputation management system addressing all rating system issues
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any
from decimal import Decimal
from dataclasses import dataclass
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy import desc, func, and_, or_

from database import SessionLocal
from models import Rating, User, Escrow, Dispute, EscrowStatus

logger = logging.getLogger(__name__)


@dataclass
class ReputationScore:
    """Comprehensive reputation score data structure"""
    overall_rating: float
    total_ratings: int
    rating_distribution: Dict[int, int]
    trust_level: str
    trust_score: float
    completion_rate: float
    dispute_rate: float
    total_volume: Decimal
    recent_activity: int
    verification_status: str
    reputation_trend: str
    badges: List[str]
    risk_level: str


@dataclass
class SellerProfile:
    """Complete seller profile for display during escrow creation"""
    user_id: int
    username: str
    display_name: str
    reputation_score: ReputationScore
    recent_reviews: List[Dict]
    stats_summary: Dict
    trust_indicators: List[str]
    warnings: List[str]
    recommendation_level: str


class EnhancedReputationService:
    """Comprehensive reputation and rating management service"""
    
    # Trust level thresholds
    TRUST_LEVELS = {
        'new': (0, 4.0, 0),      # (min_ratings, min_avg, min_volume)
        'bronze': (3, 4.0, 100),
        'silver': (10, 4.2, 500),
        'gold': (25, 4.5, 2000),
        'platinum': (50, 4.7, 10000),
        'diamond': (100, 4.8, 50000)
    }
    
    # Risk assessment thresholds
    RISK_THRESHOLDS = {
        'low': {'dispute_rate': 0.05, 'completion_rate': 0.95},
        'medium': {'dispute_rate': 0.15, 'completion_rate': 0.85},
        'high': {'dispute_rate': 0.25, 'completion_rate': 0.75}
    }
    
    def __init__(self):
        self.session_factory = sessionmaker(bind=SessionLocal().bind)
    
    @staticmethod
    def get_comprehensive_reputation(user_id: int, session: Optional[Session] = None) -> Optional[ReputationScore]:
        """
        Get comprehensive reputation score for a user
        Addresses Issues: #1, #2, #7, #10
        """
        if session is None:
            session = SessionLocal()
            close_session = True
        else:
            close_session = False
            
        try:
            service = EnhancedReputationService()
            
            # Get all ratings for user
            ratings = session.query(Rating).filter(Rating.rated_id == user_id).all()
            
            if not ratings:
                return ReputationScore(
                    overall_rating=0.0,
                    total_ratings=0,
                    rating_distribution={1: 0, 2: 0, 3: 0, 4: 0, 5: 0},
                    trust_level='new',
                    trust_score=0.0,
                    completion_rate=0.0,
                    dispute_rate=0.0,
                    total_volume=Decimal('0'),
                    recent_activity=0,
                    verification_status='unverified',
                    reputation_trend='stable',
                    badges=[],
                    risk_level='unknown'
                )
            
            # Calculate basic ratings
            total_ratings = len(ratings)
            overall_rating = sum(r.rating for r in ratings) / total_ratings
            
            # Rating distribution
            rating_distribution = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
            for rating in ratings:
                rating_distribution[rating.rating] += 1
            
            # Calculate weighted reputation (recent ratings matter more)
            weighted_score = service._calculate_weighted_reputation(ratings)
            
            # Get trading statistics
            user_stats = service._get_user_trading_stats(user_id, session)
            completion_rate = user_stats['completion_rate']
            dispute_rate = user_stats['dispute_rate']
            total_volume = user_stats['total_volume']
            
            # Calculate trust level and score
            trust_level = service._calculate_trust_level(
                total_ratings, overall_rating, total_volume
            )
            trust_score = service._calculate_trust_score(
                overall_rating, completion_rate, dispute_rate, total_ratings
            )
            
            # Recent activity (ratings in last 30 days)
            thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
            recent_activity = session.query(Rating).filter(
                Rating.rated_id == user_id,
                Rating.created_at >= thirty_days_ago
            ).count()
            
            # Verification status
            user = session.query(User).filter(User.id == user_id).first()
            verification_status = service._get_verification_status(user)
            
            # Reputation trend
            reputation_trend = service._calculate_reputation_trend(ratings)
            
            # Calculate badges
            badges = service._calculate_badges(
                total_ratings, overall_rating, completion_rate, 
                dispute_rate, total_volume, verification_status
            )
            
            # Risk assessment
            risk_level = service._assess_risk_level(completion_rate, dispute_rate, overall_rating)
            
            return ReputationScore(
                overall_rating=round(weighted_score, 2),
                total_ratings=total_ratings,
                rating_distribution=rating_distribution,
                trust_level=trust_level,
                trust_score=round(trust_score, 2),
                completion_rate=round(completion_rate, 3),
                dispute_rate=round(dispute_rate, 3),
                total_volume=total_volume,
                recent_activity=recent_activity,
                verification_status=verification_status,
                reputation_trend=reputation_trend,
                badges=badges,
                risk_level=risk_level
            )
            
        except Exception as e:
            logger.error(f"Error calculating reputation for user {user_id}: {e}")
            return None
        finally:
            if close_session:
                session.close()
    
    def _calculate_weighted_reputation(self, ratings: List[Rating]) -> float:
        """
        Calculate weighted reputation score with time decay
        Addresses Issue: #7 - Missing Reputation Algorithm
        """
        if not ratings:
            return 0.0
        
        now = datetime.now(timezone.utc)
        total_weight = 0
        weighted_sum = 0
        
        for rating in ratings:
            # Time decay: newer ratings have more weight
            days_old = (now - rating.created_at).days
            weight = max(1.0, 2.0 - (days_old / 365))  # Decay over 1 year
            
            # Volume weight: larger trades matter more
            if hasattr(rating, 'escrow') and rating.escrow:
                volume_weight = min(2.0, 1.0 + float(rating.escrow.amount) / 1000)
                weight *= volume_weight
            
            weighted_sum += rating.rating * weight
            total_weight += weight
        
        return weighted_sum / total_weight if total_weight > 0 else 0.0
    
    def _get_user_trading_stats(self, user_id: int, session: Session) -> Dict[str, Any]:
        """
        Get comprehensive trading statistics for user
        Addresses Issue: #13 - Incomplete User Stats Integration
        
        IMPORTANT: Counts both BUYER and SELLER escrows for accurate stats
        SUCCESS RATE: Only counts active trades (excludes cancelled/expired that never started)
        """
        # Get completed escrows as BOTH buyer and seller
        completed_escrows = session.query(Escrow).filter(
            or_(Escrow.buyer_id == user_id, Escrow.seller_id == user_id),
            Escrow.status == EscrowStatus.COMPLETED.value
        ).all()
        
        # Get "active" escrows that actually started (reached active/payment_confirmed status)
        # Excludes cancelled/expired trades that never truly began
        active_escrows = session.query(Escrow).filter(
            or_(Escrow.buyer_id == user_id, Escrow.seller_id == user_id),
            Escrow.status.in_([
                EscrowStatus.COMPLETED.value,
                EscrowStatus.DISPUTED.value,
                EscrowStatus.ACTIVE.value
            ])
        ).all()
        
        # Get disputes (user can be involved as either buyer or seller)
        disputes = session.query(Dispute).join(Escrow, Dispute.escrow_id == Escrow.id).filter(
            or_(Escrow.buyer_id == user_id, Escrow.seller_id == user_id)
        ).all()
        
        # Calculate metrics - Option 2: Only count active trades for success rate
        active_trade_count = len(active_escrows)
        completed_count = len(completed_escrows)
        
        # Success rate: completed / active trades (excludes cancelled before starting)
        completion_rate = completed_count / active_trade_count if active_trade_count > 0 else 0.0
        
        dispute_count = len(disputes)
        dispute_rate = dispute_count / active_trade_count if active_trade_count > 0 else 0.0
        
        total_volume = sum(Decimal(str(e.amount)) for e in completed_escrows)
        
        return {
            'completion_rate': completion_rate,
            'dispute_rate': dispute_rate,
            'total_volume': total_volume,
            'total_trades': completed_count,  # Show only completed trades count
            'completed_trades': completed_count,
            'disputes': dispute_count
        }
    
    def _calculate_trust_level(self, total_ratings: int, avg_rating: float, total_volume: Decimal) -> str:
        """
        Calculate trust level based on ratings, volume, and performance
        Addresses Issue: #4 - Missing Rating-Based Features
        """
        volume_float = float(total_volume)
        
        for level, (min_ratings, min_avg, min_volume) in reversed(list(self.TRUST_LEVELS.items())):
            if (total_ratings >= min_ratings and 
                avg_rating >= min_avg and 
                volume_float >= min_volume):
                return level
        
        return 'new'
    
    def _calculate_trust_score(self, avg_rating: float, completion_rate: float, 
                              dispute_rate: float, total_ratings: int) -> float:
        """
        Calculate numerical trust score (0-100)
        Addresses Issue: #7 - Missing Reputation Algorithm
        """
        # Base score from ratings (0-50 points)
        rating_score = (avg_rating / 5.0) * 50
        
        # Completion rate bonus (0-25 points)
        completion_score = completion_rate * 25
        
        # Dispute penalty (0-15 points deduction)
        dispute_penalty = min(15, dispute_rate * 100)
        
        # Volume bonus (0-10 points)
        volume_bonus = min(10, total_ratings / 10)
        
        total_score = rating_score + completion_score + volume_bonus - dispute_penalty
        return max(0, min(100, total_score))
    
    def _get_verification_status(self, user: User) -> str:
        """
        Get user verification status
        Addresses Issue: #17 - Privacy & Data Issues
        """
        if not user:
            return 'unverified'
        
        verified_items = []
        if getattr(user, 'email_verified', False):
            verified_items.append('email')
        if getattr(user, 'phone_verified', False):
            verified_items.append('phone')
        if getattr(user, 'kyc_verified', False):
            verified_items.append('kyc')
        
        if len(verified_items) >= 2:
            return 'fully_verified'
        elif len(verified_items) == 1:
            return 'partially_verified'
        else:
            return 'unverified'
    
    def _calculate_reputation_trend(self, ratings: List[Rating]) -> str:
        """
        Calculate reputation trend (improving/declining/stable)
        Addresses Issue: #10 - Insufficient Rating Analytics
        """
        if len(ratings) < 6:
            return 'stable'
        
        # Sort by date
        sorted_ratings = sorted(ratings, key=lambda r: r.created_at)
        
        # Compare recent vs older ratings
        recent_ratings = sorted_ratings[-3:]
        older_ratings = sorted_ratings[-6:-3]
        
        recent_avg = sum(r.rating for r in recent_ratings) / len(recent_ratings)
        older_avg = sum(r.rating for r in older_ratings) / len(older_ratings)
        
        diff = recent_avg - older_avg
        
        if diff > 0.3:
            return 'improving'
        elif diff < -0.3:
            return 'declining'
        else:
            return 'stable'
    
    def _calculate_badges(self, total_ratings: int, avg_rating: float, 
                         completion_rate: float, dispute_rate: float,
                         total_volume: Decimal, verification_status: str) -> List[str]:
        """
        Calculate user badges/achievements
        Addresses Issue: #4 - Missing Rating-Based Features
        """
        badges = []
        
        # Rating badges
        if avg_rating >= 4.8 and total_ratings >= 10:
            badges.append('🌟 Top Rated')
        elif avg_rating >= 4.5 and total_ratings >= 5:
            badges.append('⭐ Highly Rated')
        
        # Volume badges
        volume_float = float(total_volume)
        if volume_float >= 50000:
            badges.append('💎 High Volume Trader')
        elif volume_float >= 10000:
            badges.append('💰 Volume Trader')
        
        # Reliability badges
        if completion_rate >= 0.98 and total_ratings >= 10:
            badges.append('🎯 Ultra Reliable')
        elif completion_rate >= 0.95 and total_ratings >= 5:
            badges.append('✅ Reliable')
        
        # Dispute-free badge
        if dispute_rate == 0 and total_ratings >= 10:
            badges.append('🕊️ Dispute Free')
        
        # Verification badges
        if verification_status == 'fully_verified':
            badges.append('🔐 Verified')
        
        # Activity badges
        if total_ratings >= 100:
            badges.append('🏆 Veteran Trader')
        elif total_ratings >= 50:
            badges.append('🥉 Active Trader')
        
        return badges
    
    def _assess_risk_level(self, completion_rate: float, dispute_rate: float, avg_rating: float) -> str:
        """
        Assess risk level for trading with user
        Addresses Issue: #8 - Security Vulnerabilities
        """
        if (completion_rate >= self.RISK_THRESHOLDS['low']['completion_rate'] and
            dispute_rate <= self.RISK_THRESHOLDS['low']['dispute_rate'] and
            avg_rating >= 4.0):
            return 'low'
        elif (completion_rate >= self.RISK_THRESHOLDS['medium']['completion_rate'] and
              dispute_rate <= self.RISK_THRESHOLDS['medium']['dispute_rate'] and
              avg_rating >= 3.5):
            return 'medium'
        else:
            return 'high'
    
    @staticmethod
    def get_seller_profile_for_escrow(seller_identifier: str, seller_type: str, 
                                     session: Optional[Session] = None) -> Optional[SellerProfile]:
        """
        Get comprehensive seller profile for display during escrow creation
        Addresses Issues: #1, #2, #3 - Core Rating Display Issues
        """
        if session is None:
            session = SessionLocal()
            close_session = True
        else:
            close_session = False
            
        try:
            service = EnhancedReputationService()
            
            # Find seller user (case-insensitive)
            from sqlalchemy import func
            seller_user = None
            if seller_type == 'username':
                seller_user = session.query(User).filter(func.lower(User.username) == func.lower(seller_identifier)).first()
            elif seller_type == 'email':
                seller_user = session.query(User).filter(func.lower(User.email) == func.lower(seller_identifier)).first()
            
            if not seller_user:
                return None
            
            # Get reputation score
            reputation_score = service.get_comprehensive_reputation(seller_user.id, session)
            if not reputation_score:
                return None
            
            # Get recent reviews (last 5)
            recent_reviews = session.query(Rating).filter(
                Rating.rated_id == seller_user.id,
                Rating.category == 'seller'
            ).order_by(desc(Rating.created_at)).limit(5).all()
            
            recent_reviews_data = []
            for review in recent_reviews:
                rater = session.query(User).filter(User.id == review.rater_id).first()
                recent_reviews_data.append({
                    'rating': review.rating,
                    'comment': review.comment[:100] + '...' if review.comment and len(review.comment) > 100 else review.comment,
                    'rater_name': rater.first_name if rater else 'Anonymous',
                    'created_at': review.created_at,
                    'days_ago': (datetime.now(timezone.utc) - review.created_at).days
                })
            
            # Generate trust indicators
            trust_indicators = service._generate_trust_indicators(reputation_score, seller_user)
            
            # Generate warnings
            warnings = service._generate_warnings(reputation_score)
            
            # Calculate recommendation level
            recommendation_level = service._calculate_recommendation_level(reputation_score)
            
            # Stats summary
            stats_summary = {
                'total_trades': reputation_score.total_ratings,
                'completion_rate': f"{reputation_score.completion_rate * 100:.1f}%",
                'total_volume': f"${float(reputation_score.total_volume):,.2f}",
                'member_since': seller_user.created_at.strftime('%Y-%m-%d') if seller_user.created_at else 'Unknown',
                'last_active': 'Recently' if reputation_score.recent_activity > 0 else 'Not recently'
            }
            
            return SellerProfile(
                user_id=seller_user.id,
                username=seller_user.username or 'N/A',
                display_name=seller_user.first_name or seller_user.username or 'Unknown',
                reputation_score=reputation_score,
                recent_reviews=recent_reviews_data,
                stats_summary=stats_summary,
                trust_indicators=trust_indicators,
                warnings=warnings,
                recommendation_level=recommendation_level
            )
            
        except Exception as e:
            logger.error(f"Error getting seller profile for {seller_identifier}: {e}")
            return None
        finally:
            if close_session:
                session.close()
    
    def _generate_trust_indicators(self, reputation: ReputationScore, user: User) -> List[str]:
        """
        Generate trust indicators for display
        Addresses Issue: #4 - Missing Rating-Based Features
        """
        indicators = []
        
        # Rating indicators
        if reputation.overall_rating >= 4.5:
            indicators.append(f"⭐ {reputation.overall_rating}/5.0 rating")
        
        # Trust level
        if reputation.trust_level != 'new':
            indicators.append(f"🏅 {reputation.trust_level.title()} member")
        
        # Completion rate
        if reputation.completion_rate >= 0.95:
            indicators.append(f"✅ {reputation.completion_rate * 100:.1f}% completion rate")
        
        # Verification
        if reputation.verification_status == 'fully_verified':
            indicators.append("🔐 Fully verified")
        elif reputation.verification_status == 'partially_verified':
            indicators.append("🔒 Partially verified")
        
        # Volume
        if reputation.total_volume >= 1000:
            indicators.append(f"💰 ${float(reputation.total_volume):,.0f} total volume")
        
        # Low disputes
        if reputation.dispute_rate <= 0.05:
            indicators.append("🕊️ Low dispute rate")
        
        return indicators
    
    def _generate_warnings(self, reputation: ReputationScore) -> List[str]:
        """
        Generate warning messages for risky sellers
        Addresses Issue: #8 - Security Vulnerabilities
        """
        warnings = []
        
        if reputation.risk_level == 'high':
            warnings.append("⚠️ High risk seller - trade with caution")
        
        if reputation.overall_rating < 3.5 and reputation.total_ratings >= 5:
            warnings.append("📉 Below average ratings")
        
        if reputation.dispute_rate > 0.15:
            warnings.append("⚡ High dispute rate")
        
        if reputation.completion_rate < 0.85:
            warnings.append("📊 Low completion rate")
        
        if reputation.reputation_trend == 'declining':
            warnings.append("📉 Recent ratings declining")
        
        if reputation.verification_status == 'unverified':
            warnings.append("🔓 Unverified seller")
        
        return warnings
    
    def _calculate_recommendation_level(self, reputation: ReputationScore) -> str:
        """
        Calculate overall recommendation level
        Addresses Issue: #4 - Missing Rating-Based Features
        """
        score = reputation.trust_score
        
        if score >= 80:
            return 'highly_recommended'
        elif score >= 60:
            return 'recommended'
        elif score >= 40:
            return 'neutral'
        else:
            return 'not_recommended'
    
    @staticmethod
    def search_sellers_by_rating(min_rating: float = 4.0, min_ratings: int = 5,
                               trust_level: Optional[str] = None,
                               session: Optional[Session] = None) -> List[Dict]:
        """
        Search for sellers by rating criteria
        Addresses Issue: #4 - Missing Rating-Based Features
        """
        if session is None:
            session = SessionLocal()
            close_session = True
        else:
            close_session = False
            
        try:
            # Get users with good ratings
            seller_ratings = session.query(
                Rating.rated_id,
                func.avg(Rating.rating).label('avg_rating'),
                func.count(Rating.id).label('total_ratings')
            ).filter(
                Rating.category == 'seller'
            ).group_by(Rating.rated_id).having(
                and_(
                    func.avg(Rating.rating) >= min_rating,
                    func.count(Rating.id) >= min_ratings
                )
            ).order_by(desc('avg_rating')).all()
            
            results = []
            service = EnhancedReputationService()
            
            for rated_id, avg_rating, total_ratings in seller_ratings:
                user = session.query(User).filter(User.id == rated_id).first()
                if not user:
                    continue
                
                reputation = service.get_comprehensive_reputation(rated_id, session)
                if not reputation:
                    continue
                
                # Filter by trust level if specified
                if trust_level and reputation.trust_level != trust_level:
                    continue
                
                results.append({
                    'user_id': rated_id,
                    'username': user.username,
                    'display_name': user.first_name or user.username,
                    'rating': avg_rating,
                    'total_ratings': total_ratings,
                    'trust_level': reputation.trust_level,
                    'completion_rate': reputation.completion_rate,
                    'badges': reputation.badges
                })
            
            return results
            
        except Exception as e:
            logger.error(f"Error searching sellers by rating: {e}")
            return []
        finally:
            if close_session:
                session.close()
    
    @staticmethod
    def detect_rating_manipulation(user_id: int, session: Optional[Session] = None) -> Dict[str, Any]:
        """
        Detect potential rating manipulation
        Addresses Issue: #8 - Security Vulnerabilities
        """
        if session is None:
            session = SessionLocal()
            close_session = True
        else:
            close_session = False
            
        try:
            flags = []
            severity = 'low'
            
            # Get user's received ratings
            ratings = session.query(Rating).filter(Rating.rated_id == user_id).all()
            
            if len(ratings) < 3:
                return {'flags': [], 'severity': 'none', 'score': 0}
            
            # Check for suspicious patterns
            
            # 1. Too many 5-star ratings from new accounts
            five_star_count = sum(1 for r in ratings if r.rating == 5)
            if five_star_count / len(ratings) > 0.8 and len(ratings) >= 10:
                # Check if raters are new accounts
                rater_ids = [r.rater_id for r in ratings if r.rating == 5]
                new_raters = session.query(User).filter(
                    User.id.in_(rater_ids),
                    User.created_at >= datetime.now(timezone.utc) - timedelta(days=30)
                ).count()
                
                if new_raters / len(rater_ids) > 0.6:
                    flags.append("High percentage of 5-star ratings from new accounts")
                    severity = 'high'
            
            # 2. Rapid rating accumulation
            recent_ratings = [r for r in ratings if r.created_at >= datetime.now(timezone.utc) - timedelta(days=7)]
            if len(recent_ratings) > 10:
                flags.append("Unusually high number of ratings in short period")
                severity = max(severity, 'medium')
            
            # 3. Similar rating times (bot activity)
            rating_times = [r.created_at for r in ratings]
            rating_times.sort()
            
            close_time_pairs = 0
            for i in range(1, len(rating_times)):
                if (rating_times[i] - rating_times[i-1]).total_seconds() < 300:  # 5 minutes
                    close_time_pairs += 1
            
            if close_time_pairs / max(1, len(rating_times) - 1) > 0.3:
                flags.append("Multiple ratings submitted in close succession")
                severity = max(severity, 'medium')
            
            # 4. Check for reciprocal rating patterns
            rater_ids = [r.rater_id for r in ratings]
            reciprocal_ratings = session.query(Rating).filter(
                Rating.rater_id == user_id,
                Rating.rated_id.in_(rater_ids)
            ).count()
            
            if reciprocal_ratings / len(rater_ids) > 0.5:
                flags.append("High rate of reciprocal ratings")
                severity = max(severity, 'medium')
            
            # Calculate manipulation score
            score = len(flags) * 25
            if severity == 'high':
                score += 25
            
            return {
                'flags': flags,
                'severity': severity,
                'score': min(100, score),
                'total_ratings': len(ratings),
                'analysis_date': datetime.now(timezone.utc)
            }
            
        except Exception as e:
            logger.error(f"Error detecting rating manipulation for user {user_id}: {e}")
            return {'flags': [], 'severity': 'error', 'score': 0}
        finally:
            if close_session:
                session.close()
    
    @staticmethod
    def get_rating_analytics(days: int = 30, session: Optional[Session] = None) -> Dict[str, Any]:
        """
        Get comprehensive rating analytics for admin dashboard
        Addresses Issue: #10 - Insufficient Rating Analytics
        """
        if session is None:
            session = SessionLocal()
            close_session = True
        else:
            close_session = False
            
        try:
            start_date = datetime.now(timezone.utc) - timedelta(days=days)
            
            # Basic stats
            total_ratings = session.query(Rating).count()
            recent_ratings = session.query(Rating).filter(Rating.created_at >= start_date).count()
            
            # Average ratings
            overall_avg = session.query(func.avg(Rating.rating)).scalar() or 0
            recent_avg = session.query(func.avg(Rating.rating)).filter(
                Rating.created_at >= start_date
            ).scalar() or 0
            
            # Rating distribution
            distribution = {}
            for i in range(1, 6):
                count = session.query(Rating).filter(Rating.rating == i).count()
                distribution[i] = count
            
            # Top rated users
            top_users = session.query(
                Rating.rated_id,
                func.avg(Rating.rating).label('avg_rating'),
                func.count(Rating.id).label('rating_count')
            ).group_by(Rating.rated_id).having(
                func.count(Rating.id) >= 5
            ).order_by(desc('avg_rating')).limit(10).all()
            
            # Rating trends (daily counts for chart)
            daily_counts = {}
            for i in range(days):
                day = datetime.now(timezone.utc) - timedelta(days=i)
                day_start = day.replace(hour=0, minute=0, second=0, microsecond=0)
                day_end = day_start + timedelta(days=1)
                
                count = session.query(Rating).filter(
                    Rating.created_at >= day_start,
                    Rating.created_at < day_end
                ).count()
                
                daily_counts[day_start.strftime('%Y-%m-%d')] = count
            
            # Category breakdown
            seller_ratings = session.query(Rating).filter(Rating.category == 'seller').count()
            buyer_ratings = session.query(Rating).filter(Rating.category == 'buyer').count()
            
            return {
                'period_days': days,
                'total_ratings': total_ratings,
                'recent_ratings': recent_ratings,
                'overall_average': round(overall_avg, 2),
                'recent_average': round(recent_avg, 2),
                'rating_distribution': distribution,
                'top_users': [
                    {
                        'user_id': user_id,
                        'avg_rating': float(avg_rating),
                        'rating_count': rating_count
                    }
                    for user_id, avg_rating, rating_count in top_users
                ],
                'daily_trends': daily_counts,
                'category_breakdown': {
                    'seller_ratings': seller_ratings,
                    'buyer_ratings': buyer_ratings
                },
                'generated_at': datetime.now(timezone.utc)
            }
            
        except Exception as e:
            logger.error(f"Error generating rating analytics: {e}")
            return {}
        finally:
            if close_session:
                session.close()


# Convenience functions for easy integration
def get_seller_reputation(user_id: int) -> Optional[ReputationScore]:
    """Quick access to seller reputation"""
    return EnhancedReputationService.get_comprehensive_reputation(user_id)


def get_seller_profile_for_escrow_creation(seller_identifier: str, seller_type: str) -> Optional[SellerProfile]:
    """Quick access to seller profile for escrow creation"""
    return EnhancedReputationService.get_seller_profile_for_escrow(seller_identifier, seller_type)


def search_top_rated_sellers(min_rating: float = 4.5) -> List[Dict]:
    """Quick search for top rated sellers"""
    return EnhancedReputationService.search_sellers_by_rating(min_rating=min_rating)